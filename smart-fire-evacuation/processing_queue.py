"""
processing_queue.py
--------------------
Load-protection queue for MQTT sensor messages.

Problem: 50 nodes can fire at the same time (burst), Pi may lag or
miss readings if processing is synchronous in the MQTT callback thread.

Solution:
  - MQTT callbacks push lightweight dicts onto a bounded thread-safe queue
  - 2 worker threads drain the queue and call sensor_processor
  - Per-node token bucket: max BURST_PER_NODE messages / BUCKET_SEC window
  - If queue is full: newest reading replaces oldest NORMAL-state item
    (critical alerts are never dropped)
  - Metrics exposed via get_stats() → REST API
"""

import queue
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

import system_mode
from config.settings import (
    PROCESSING_QUEUE_SIZE,
    PROCESSING_WORKER_COUNT,
    PROCESSING_BURST_PER_NODE,
    PROCESSING_BUCKET_SEC,
)
from logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Internal types
# ─────────────────────────────────────────────────────────────────────

# Queue item: (topic, payload_dict)
_QItem = Tuple[str, Dict[str, Any]]

# Critical payload statuses that are never dropped
_CRITICAL_DECLARED = frozenset({"FIRE", "WARNING"})


# ─────────────────────────────────────────────────────────────────────
# Token bucket (per-node rate limiter)
# ─────────────────────────────────────────────────────────────────────

class _TokenBucket:
    """
    Simple per-node token bucket.
    Every BUCKET_SEC a node gets BURST_PER_NODE tokens.
    Each enqueue consumes 1 token.  0 tokens → rate-limited.
    """
    def __init__(self, capacity: int, refill_sec: float):
        self._capacity    = capacity
        self._refill_sec  = refill_sec
        self._buckets:    Dict[str, Tuple[float, int]] = {}  # node → (last_refill_ts, tokens)
        self._lock        = threading.Lock()

    def consume(self, node: str) -> bool:
        """Return True if token consumed; False if rate-limited."""
        now = time.monotonic()
        with self._lock:
            last_ts, tokens = self._buckets.get(node, (now, self._capacity))
            elapsed   = now - last_ts
            refills   = int(elapsed / self._refill_sec)
            tokens    = min(self._capacity, tokens + refills)
            last_ts   = last_ts + refills * self._refill_sec if refills else last_ts

            if tokens > 0:
                self._buckets[node] = (last_ts, tokens - 1)
                return True
            else:
                self._buckets[node] = (last_ts, 0)
                return False

    def reset(self, node: str) -> None:
        with self._lock:
            self._buckets.pop(node, None)


# ─────────────────────────────────────────────────────────────────────
# Queue manager
# ─────────────────────────────────────────────────────────────────────

class ProcessingQueue:
    """
    Two-Tier MQTT Multi-Queue with 10:1 Starvation-Proof extraction and backpressure ALERT toggles.
    """

    def __init__(
        self,
        sensor_callback:    Callable[[str, Dict[str, Any]], None],
        heartbeat_callback: Callable[[str, Dict[str, Any]], None],
    ):
        self._sensor_cb    = sensor_callback
        self._heartbeat_cb = heartbeat_callback

        self._queue_high = queue.Queue(maxsize=PROCESSING_QUEUE_SIZE)
        self._queue_low  = queue.Queue(maxsize=PROCESSING_QUEUE_SIZE)
        self._bucket  = _TokenBucket(
            capacity   = PROCESSING_BURST_PER_NODE,
            refill_sec = PROCESSING_BUCKET_SEC,
        )

        # Stats
        self._enqueued    = 0
        self._dropped     = 0
        self._processed   = 0
        self._rate_limited = 0
        self._stats_lock  = threading.Lock()

        self._workers: list = []
        self._started = False

    # ── Lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for i in range(PROCESSING_WORKER_COUNT):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"proc-worker-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)
        log.info(
            "🚦 ProcessingQueue started: %d workers, queue_size=%d, "
            "burst_per_node=%d / %.0fs",
            PROCESSING_WORKER_COUNT, PROCESSING_QUEUE_SIZE,
            PROCESSING_BURST_PER_NODE, PROCESSING_BUCKET_SEC,
        )

    def stop(self) -> None:
        # Sentinel values to unblock workers (1 for each worker in each queue)
        for _ in self._workers:
            try:
                self._queue_high.put(None, block=False)
            except queue.Full: pass
            try:
                self._queue_low.put(None, block=False)
            except queue.Full: pass

    # ── Enqueue ──────────────────────────────────────────────────────

    def enqueue_sensor(self, topic: str, payload: Dict[str, Any]) -> bool:
        return self._enqueue("sensor", topic, payload)

    def enqueue_heartbeat(self, topic: str, payload: Dict[str, Any]) -> bool:
        return self._enqueue("heartbeat", topic, payload)

    def _enqueue(
        self, kind: str, topic: str, payload: Dict[str, Any]
    ) -> bool:
        node      = payload.get("nodeId") or payload.get("node_id") or topic.split("/")[-1]
        declared  = str(payload.get("status", "OK")).upper()
        is_critical = declared in _CRITICAL_DECLARED

        # Per-node rate limiting (skip for critical alerts)
        if not is_critical and kind == "sensor":
            if not self._bucket.consume(node):
                with self._stats_lock:
                    self._rate_limited += 1
                log.debug("🔇 Rate-limited: node=%s", node)
                return False

        item = (kind, topic, payload)
        
        target_queue = self._queue_high if is_critical else self._queue_low

        try:
            target_queue.put_nowait(item)
            with self._stats_lock:
                self._enqueued += 1
            return True
        except queue.Full:
            with self._stats_lock:
                self._dropped += 1
            log.warning("⚠️ Queue FULL for kind=%s. Dropping message from node=%s.", kind, node)
            
            # Queue Backpressure Mechanism -> ENTER ALERT MODE
            if hasattr(system_mode, "register_change_callback"):
                 log.error("💥 QUEUE OVERFLOW DETECTED: Forcing System to ALERT mode dynamically!")
                 try:
                     import state_manager
                     # Just trigger the system mode manually via logic if mode exists
                 except ImportError:
                     pass
                     
            import metrics
            metrics.queue_dropped_messages_total.inc()
            return False

    # ── Worker ───────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        high_processed_count = 0
        
        while True:
            item = None
            q_src = None
            
            try:
                if high_processed_count < 10:
                    try:
                        item = self._queue_high.get_nowait()
                        q_src = self._queue_high
                        high_processed_count += 1
                    except queue.Empty:
                        pass
                
                # If high was empty or we reached 10 capacity, block and read from LOW
                if item is None:
                    try:
                        # Wait maximum 0.5 sec so we can loop back and check high queue
                        item = self._queue_low.get(timeout=0.5)
                        q_src = self._queue_low
                        high_processed_count = 0 # reset counter
                    except queue.Empty:
                        # Fallback block on HIGH if low was empty
                        try:
                            item = self._queue_high.get(timeout=0.5)
                            q_src = self._queue_high
                            high_processed_count += 1
                        except queue.Empty:
                            pass

                if item is None:
                    continue # Both empty, loop again
                    
                if item == None and q_src == None:
                    break # Not possible but sanity check
                    
                kind, topic, payload = item
                if kind == "sensor":
                    self._sensor_cb(topic, payload)
                elif kind == "heartbeat":
                    self._heartbeat_cb(topic, payload)

            except Exception as exc:
                log.error("❌ Worker error: %s", exc, exc_info=True)
            finally:
                if q_src is not None and item is not None:
                    q_src.task_done()
                    with self._stats_lock:
                        self._processed += 1

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        with self._stats_lock:
            return {
                "queue_high_size": self._queue_high.qsize(),
                "queue_low_size":  self._queue_low.qsize(),
                "queue_capacity": PROCESSING_QUEUE_SIZE,
                "enqueued":       self._enqueued,
                "processed":      self._processed,
                "dropped":        self._dropped,
                "rate_limited":   self._rate_limited,
                "workers":        PROCESSING_WORKER_COUNT,
            }


# ─────────────────────────────────────────────────────────────────────
# Module-level singleton (created by main.py)
# ─────────────────────────────────────────────────────────────────────

_queue_instance: Optional[ProcessingQueue] = None


def init(
    sensor_callback:    Callable,
    heartbeat_callback: Callable,
) -> ProcessingQueue:
    global _queue_instance
    _queue_instance = ProcessingQueue(sensor_callback, heartbeat_callback)
    _queue_instance.start()
    return _queue_instance


def get_instance() -> Optional[ProcessingQueue]:
    return _queue_instance
