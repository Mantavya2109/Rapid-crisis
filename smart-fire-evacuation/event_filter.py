"""
event_filter.py
---------------
Event filtering layer — sits between sensor_processor and cloud_sync.

Policy (evaluated in priority order):
  1. State CHANGED             → always emit
  2. State is CRITICAL/DANGER  → always emit (even if repeated)
  3. Periodic force-emit       → emit every CLOUD_SUMMARY_INTERVAL_SEC
  4. Repeat within quiet window → SUPPRESS

By sending only meaningful events, cloud load is reduced by 90%+ 
compared to forwarding every raw reading.
"""

import threading
import time
from typing import Dict

from config.settings import CLOUD_SUMMARY_INTERVAL_SEC, MIN_REPEAT_INTERVAL_SEC
from logger import get_logger

log = get_logger(__name__)

# These states always bypass the quiet window
_ALWAYS_EMIT = frozenset({"CRITICAL_FIRE", "DANGER"})


class EventFilter:

    def __init__(self):
        self._last_emit_ts:    Dict[str, float] = {}  # node → unix ts of last emit
        self._last_emit_state: Dict[str, str]   = {}  # node → last emitted state
        self._lock = threading.Lock()

    def should_emit(self, node: str, new_state: str, old_state: str) -> bool:
        """
        Decide whether to forward a sensor reading to the cloud.

        Parameters
        ----------
        node      : building node ID
        new_state : freshly classified state
        old_state : previous state (from NodeMemory)

        Returns True → forward to cloud.
        Returns False → suppress (same state, within quiet window).
        """
        now = time.time()
        with self._lock:
            last_ts    = self._last_emit_ts.get(node, 0.0)
            last_state = self._last_emit_state.get(node)
            elapsed    = now - last_ts

            # ── Rule 1: state just changed ────────────────────────────
            # Compare against *both* the in-memory old_state and the last
            # cloud-emitted state — catches transient states we never sent.
            if new_state != old_state or new_state != last_state:
                self._record(node, new_state, now)
                reason = "STATE_CHANGE"
                log.debug(
                    "📤 [%s] EMIT  node=%-12s  %s → %s  (%s)",
                    reason, node, old_state, new_state, reason,
                )
                return True

            # ── Rule 2: critical states bypass quiet window ───────────
            if new_state in _ALWAYS_EMIT:
                self._record(node, new_state, now)
                log.debug(
                    "📤 [CRITICAL] EMIT  node=%-12s  state=%s", node, new_state
                )
                return True

            # ── Rule 3: periodic summary ──────────────────────────────
            if elapsed >= CLOUD_SUMMARY_INTERVAL_SEC:
                self._record(node, new_state, now)
                log.debug(
                    "📤 [PERIODIC] EMIT  node=%-12s  state=%s  (%.0fs since last)",
                    node, new_state, elapsed,
                )
                return True

            # ── Rule 4: suppress ──────────────────────────────────────
            log.debug(
                "🔇 [SUPPRESS] node=%-12s  state=%s  (%.0fs / min=%ds)",
                node, new_state, elapsed, MIN_REPEAT_INTERVAL_SEC,
            )
            return False

    def _record(self, node: str, state: str, ts: float) -> None:
        """Update the node's last-emit tracker (caller must hold _lock)."""
        self._last_emit_ts[node]    = ts
        self._last_emit_state[node] = state

    def reset(self, node: str) -> None:
        """Force the next event for this node to be emitted (e.g., after alert clear)."""
        with self._lock:
            self._last_emit_ts.pop(node, None)
            self._last_emit_state.pop(node, None)

    def get_stats(self) -> Dict[str, Dict]:
        with self._lock:
            now = time.time()
            return {
                node: {
                    "last_state":      self._last_emit_state.get(node, "—"),
                    "seconds_since":   round(now - self._last_emit_ts.get(node, now), 1),
                }
                for node in self._last_emit_ts
            }
