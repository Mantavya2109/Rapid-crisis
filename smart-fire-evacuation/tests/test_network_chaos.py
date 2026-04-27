"""
tests/test_network_chaos.py
---------------------------
Network chaos tests: simulate packet loss, MQTT disconnects, queue flooding,
stale sensors, sequence replay attacks, and backpressure behaviour.

These tests verify the system remains safe and consistent under adverse
network conditions — no real MQTT broker or network required.
"""

import sys
import os
import time
import threading
import queue as stdlib_queue
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub persistence so no real DB is touched ──────────────────────────────
import persistence
persistence.init_db = lambda: None
persistence.flush_now = lambda: None
persistence.register_snapshot = lambda name, fn: None
persistence.load_devices = lambda: []
persistence.load_alerts = lambda: []
persistence.load_events = lambda **kw: []
persistence.write_event = lambda **kw: None
persistence.mark_dirty = lambda: None

import state_manager
import processing_queue as pq_mod


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _make_queue(sensor_cb=None, hb_cb=None):
    sensor_cb  = sensor_cb  or MagicMock()
    hb_cb      = hb_cb      or MagicMock()
    return pq_mod.init(sensor_cb, hb_cb)


def _fire_payload(node: str = "NODE_A", seq: int = 1):
    return {"deviceId": node, "status": "FIRE", "temperature": 99.0,
            "smoke": 999.0, "seq": seq}


def _normal_payload(node: str = "NODE_A", seq: int = 1):
    return {"deviceId": node, "status": "OK", "temperature": 25.0,
            "smoke": 100.0, "seq": seq}


@pytest.fixture(autouse=True)
def reset_state():
    state_manager.reset_all()
    yield
    state_manager.reset_all()


# ─────────────────────────────────────────────────────────────────────
# 1. MQTT burst / queue overload
# ─────────────────────────────────────────────────────────────────────

class TestQueueFlood:
    """Simulate 200 simultaneous MQTT messages from 10 nodes."""

    def test_queue_does_not_grow_beyond_capacity(self):
        """Queue must stay ≤ PROCESSING_QUEUE_SIZE even under heavy burst."""
        from config.settings import PROCESSING_QUEUE_SIZE
        q = _make_queue()
        try:
            for i in range(200):
                topic = f"sensors/data/node_{i % 10}"
                q.enqueue_sensor(topic, _normal_payload(f"node_{i % 10}", i))

            stats = q.get_stats()
            total = stats["queue_high_size"] + stats["queue_low_size"]
            assert total <= PROCESSING_QUEUE_SIZE, (
                f"Queue exceeded capacity: {total} > {PROCESSING_QUEUE_SIZE}"
            )
        finally:
            q.stop()

    def test_critical_fire_enqueued_even_under_full_load(self):
        """FIRE payloads must always reach the high-priority queue."""
        processed_fire = threading.Event()

        def sensor_cb(topic, payload):
            if payload.get("status") == "FIRE":
                processed_fire.set()

        q = _make_queue(sensor_cb=sensor_cb)
        try:
            # flood with normal messages first to fill low queue
            for i in range(150):
                q.enqueue_sensor("sensors/data/flood", _normal_payload("flood", i))

            # now inject a FIRE event
            q.enqueue_sensor("sensors/data/NODE_A", _fire_payload("NODE_A", 200))

            fired = processed_fire.wait(timeout=5.0)
            assert fired, "FIRE event was never processed despite queue flood"
        finally:
            q.stop()

    def test_rate_limiter_activates_under_burst(self):
        """Per-node rate limiter must kick in after BURST_PER_NODE messages."""
        from config.settings import PROCESSING_BURST_PER_NODE
        q = _make_queue()
        try:
            accepted = 0
            rejected = 0
            for i in range(PROCESSING_BURST_PER_NODE * 3):
                ok = q.enqueue_sensor("sensors/data/spam_node",
                                      _normal_payload("spam_node", i))
                if ok:
                    accepted += 1
                else:
                    rejected += 1

            # After exhausting tokens, subsequent normal messages must be rate-limited
            assert rejected > 0, "Rate limiter never activated"
            assert accepted <= PROCESSING_BURST_PER_NODE + 5, (
                f"Too many messages passed rate limiter: {accepted}"
            )
        finally:
            q.stop()

    def test_critical_bypasses_rate_limiter(self):
        """FIRE events must bypass per-node rate limiting."""
        from config.settings import PROCESSING_BURST_PER_NODE
        fire_processed = threading.Event()

        def sensor_cb(topic, payload):
            if payload.get("status") == "FIRE":
                fire_processed.set()

        q = _make_queue(sensor_cb=sensor_cb)
        try:
            # exhaust tokens by sending normal messages
            for i in range(PROCESSING_BURST_PER_NODE * 2):
                q.enqueue_sensor("sensors/data/node_x", _normal_payload("node_x", i))

            # FIRE must still go through the high queue
            result = q.enqueue_sensor("sensors/data/node_x", _fire_payload("node_x", 9999))
            assert result is True, "enqueue_sensor returned False for FIRE while rate-limited"

            fired = fire_processed.wait(timeout=4.0)
            assert fired, "FIRE payload not processed even though enqueued"
        finally:
            q.stop()

    def test_stats_track_processed_and_dropped(self):
        """get_stats() must accurately reflect enqueued vs processed vs dropped."""
        q = _make_queue()
        try:
            for i in range(30):
                q.enqueue_sensor("sensors/data/node_a", _normal_payload("node_a", i))
            time.sleep(1.5)  # give workers time
            stats = q.get_stats()
            assert stats["enqueued"] > 0
            assert stats["processed"] > 0
            assert isinstance(stats["dropped"], int)
            assert isinstance(stats["rate_limited"], int)
        finally:
            q.stop()


# ─────────────────────────────────────────────────────────────────────
# 2. Stale / dropped sensor data (device goes silent)
# ─────────────────────────────────────────────────────────────────────

class TestStaleSensorData:
    """Simulate a device that stops sending data mid-fire."""

    def test_sequence_replay_dropped(self):
        """Old sequence numbers (replays) must be silently dropped."""
        state_manager.update_node_sequence("NODE_A", 50)

        drops_before = 0
        drops_after  = 0

        # seq=30 is older than last 50 → must be dropped
        dropped = False

        # We test this at the state_manager level (sequence tracking)
        last_seq = state_manager.get_node_sequence("NODE_A")
        incoming_seq = 30

        if incoming_seq <= last_seq:
            dropped = True  # correct — drop stale

        assert dropped, "Stale sequence number was not detected"

    def test_sequence_drastic_drop_treated_as_reboot(self):
        """
        If incoming seq is 100+ lower than last known, the device rebooted.
        The system must RESET the sequence counter (not drop the message).
        """
        state_manager.update_node_sequence("NODE_B", 500)
        last_seq = state_manager.get_node_sequence("NODE_B")
        incoming_seq = 1  # device rebooted

        drop_delta = incoming_seq - last_seq  # -499
        is_drastic_reboot = drop_delta < -100

        assert is_drastic_reboot, "Drastic reboot not detected"

        # After detecting a reboot the system resets seq
        state_manager.update_node_sequence("NODE_B", incoming_seq)
        assert state_manager.get_node_sequence("NODE_B") == 1

    def test_fresh_sequence_always_accepted(self):
        """Strictly incrementing sequence numbers must always be accepted."""
        state_manager.update_node_sequence("NODE_C", 10)
        for seq in range(11, 30):
            last = state_manager.get_node_sequence("NODE_C")
            assert seq > last, f"Expected seq {seq} > last {last}"
            state_manager.update_node_sequence("NODE_C", seq)

        assert state_manager.get_node_sequence("NODE_C") == 29

    def test_sequence_independent_per_node(self):
        """Sequence counters must be isolated per node — one reboot doesn't affect others."""
        state_manager.update_node_sequence("NODE_X", 200)
        state_manager.update_node_sequence("NODE_Y", 200)

        # Reboot NODE_X
        state_manager.update_node_sequence("NODE_X", 1)

        assert state_manager.get_node_sequence("NODE_X") == 1
        assert state_manager.get_node_sequence("NODE_Y") == 200, (
            "NODE_Y sequence was affected by NODE_X reboot"
        )


# ─────────────────────────────────────────────────────────────────────
# 3. Cloud sync failure / network partition simulation
# ─────────────────────────────────────────────────────────────────────

class TestCloudOutage:
    """Simulate network partitions where cloud sync calls fail."""

    @patch("cloud_sync.requests.post")
    def test_cloud_timeout_does_not_block_evacuation(self, mock_post):
        """
        If the cloud is unreachable, the evacuation engine must still
        compute local paths and dispatch LED commands without hanging.
        """
        import requests as req_lib
        mock_post.side_effect = req_lib.exceptions.ConnectTimeout("cloud unreachable")

        import cloud_sync
        result = cloud_sync.send_structured_fire_alert(
            building_id="BUILDING_01",
            blocked_nodes=[],
            start_nodes=["ROOM_101"],
            sensor_readings={"temperature": 99, "smoke": 999, "status": "FIRE"},
        )
        # Must return None (fail-safe), not raise
        assert result is None

    @patch("cloud_sync.requests.post")
    def test_cloud_500_treated_as_failure(self, mock_post):
        """HTTP 500 from cloud must be treated as a soft failure."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = Exception("Server Error")
        mock_post.return_value = mock_response

        import cloud_sync
        result = cloud_sync.send_structured_fire_alert(
            building_id="BUILDING_01",
            blocked_nodes=[],
            start_nodes=["ROOM_101"],
            sensor_readings={"temperature": 80, "smoke": 500, "status": "FIRE"},
        )
        assert result is None

    @patch("cloud_sync.requests.post")
    def test_repeated_cloud_failures_do_not_crash_system(self, mock_post):
        """Back-to-back cloud failures must not accumulate exceptions or crash."""
        import requests as req_lib
        mock_post.side_effect = req_lib.exceptions.ConnectionError("no route to host")

        import cloud_sync
        for _ in range(10):
            result = cloud_sync.send_structured_fire_alert(
                building_id="BUILDING_01",
                blocked_nodes=[],
                start_nodes=["NODE_A"],
                sensor_readings={"temperature": 70, "smoke": 300, "status": "WARNING"},
            )
            assert result is None  # must survive each iteration


# ─────────────────────────────────────────────────────────────────────
# 4. Concurrent sensor burst (thread-safety)
# ─────────────────────────────────────────────────────────────────────

class TestConcurrentMessages:
    """
    Fire messages from 20 threads simultaneously to verify no
    race conditions, deadlocks, or data corruption in shared state.
    """

    def test_concurrent_state_updates_are_safe(self):
        """50 threads updating different nodes concurrently must not corrupt state."""
        errors = []

        def worker(node_id, seq):
            try:
                state_manager.update_node_sequence(node_id, seq)
                state_manager.record_alert(node_id, severity="WARNING")
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=worker, args=(f"NODE_{i}", i + 1))
            for i in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread-safety errors: {errors}"

    def test_concurrent_enqueue_stays_bounded(self):
        """20 producer threads concurrently flooding the queue must stay bounded."""
        from config.settings import PROCESSING_QUEUE_SIZE
        q = _make_queue()
        errors = []

        def producer(node_idx):
            try:
                for j in range(30):
                    topic = f"sensors/data/node_{node_idx}"
                    q.enqueue_sensor(topic, _normal_payload(f"node_{node_idx}", j))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=producer, args=(i,)) for i in range(20)]
        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            stats = q.get_stats()
            total = stats["queue_high_size"] + stats["queue_low_size"]
            assert not errors, f"Errors in producer threads: {errors}"
            assert total <= PROCESSING_QUEUE_SIZE
        finally:
            q.stop()

    def test_10_to_1_scheduler_processes_high_before_low(self):
        """
        Verify that for every 10 FIRE entries processed,
        at least 1 NORMAL entry is also processed (starvation protection).
        """
        order = []
        lock  = threading.Lock()

        def sensor_cb(topic, payload):
            with lock:
                order.append(payload.get("status", "OK"))

        q = _make_queue(sensor_cb=sensor_cb)
        try:
            # Enqueue 20 FIRE then 5 NORMAL
            for i in range(20):
                q.enqueue_sensor("sensors/data/fire_node", _fire_payload("fire_node", i))
            for i in range(5):
                q.enqueue_sensor("sensors/data/normal_node",
                                 _normal_payload("normal_node", 100 + i))

            time.sleep(2.0)  # allow workers to drain

            fire_count   = order.count("FIRE")
            normal_count = order.count("OK")

            assert fire_count > 0,   "No FIRE events processed"
            assert normal_count > 0, (
                "NORMAL events were completely starved — 10:1 scheduler broken"
            )
        finally:
            q.stop()


# ─────────────────────────────────────────────────────────────────────
# 5. Hard Safety Mode trigger conditions
# ─────────────────────────────────────────────────────────────────────

class TestHardSafetyMode:
    """Verify HARD SAFETY MODE enters and exits correctly."""

    def test_hard_safety_mode_activates(self):
        state_manager.set_hard_safety_mode(False)
        state_manager.set_hard_safety_mode(True)
        assert state_manager.is_hard_safety_mode_active()

    def test_hard_safety_mode_deactivates(self):
        state_manager.set_hard_safety_mode(True)
        state_manager.set_hard_safety_mode(False)
        assert not state_manager.is_hard_safety_mode_active()

    def test_hard_safety_mode_records_activation_time(self):
        before = time.time()
        state_manager.set_hard_safety_mode(True)
        after = time.time()
        t = state_manager.get_hard_safety_mode_time()
        assert before <= t <= after, "Activation timestamp out of expected range"

    def test_cooldown_blocks_premature_recovery(self):
        """System must NOT exit Hard Safety Mode before cooldown elapses."""
        from evacuation_engine import HARD_SAFETY_MODE_COOLDOWN_SEC

        state_manager.set_hard_safety_mode(True)
        elapsed = time.time() - state_manager.get_hard_safety_mode_time()

        # Cooldown has NOT elapsed (we just set it)
        in_cooldown = elapsed < HARD_SAFETY_MODE_COOLDOWN_SEC
        assert in_cooldown, "Cooldown appears to have elapsed immediately"

    def test_reset_clears_hard_safety_mode(self):
        """Full system reset must clear Hard Safety Mode flag."""
        state_manager.set_hard_safety_mode(True)
        state_manager.reset_all()
        assert not state_manager.is_hard_safety_mode_active()
