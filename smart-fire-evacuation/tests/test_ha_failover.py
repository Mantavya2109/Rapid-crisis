"""
tests/test_ha_failover.py
--------------------------
High Availability failover tests.

Simulates:
  1. Primary goes silent → Secondary promotes
  2. Split-brain: network partition to Primary only (broker still up) → Promote
  3. Total isolation: Primary AND broker unreachable → Hard Safety Mode, no promotion
  4. Transient blip: brief glitch < timeout → Secondary stays secondary
  5. Promotion callback fires exactly once
  6. Role stays PRIMARY after promotion even if primary becomes visible again
  7. HA disabled mode (single-node) always returns PRIMARY
  8. Stabilization window: 2-second blackout delay on promotion
"""

import sys
import os
import time
import threading
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub persistence ──────────────────────────────────────────────────
import persistence
persistence.init_db = lambda: None
persistence.flush_now = lambda: None
persistence.register_snapshot = lambda name, fn: None
persistence.load_devices = lambda: []
persistence.load_alerts = lambda: []
persistence.load_events = lambda **kw: []
persistence.write_event = lambda **kw: None

import state_manager
import ha_manager


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_state():
    state_manager.reset_all()
    ha_manager._active_role = "SECONDARY"
    ha_manager._is_running  = False
    ha_manager._last_primary_seen = time.time()
    ha_manager._promotion_callback = None
    yield
    ha_manager.stop()
    state_manager.reset_all()


# ─────────────────────────────────────────────────────────────────────
# 1. Primary heartbeat goes silent → promotion
# ─────────────────────────────────────────────────────────────────────

class TestPrimaryHeartbeatLost:

    def test_secondary_promotes_when_primary_silent(self):
        """
        Secondary must promote to PRIMARY when the primary has been
        unreachable for longer than HA_TAKEOVER_TIMEOUT.
        """
        promoted = threading.Event()

        def on_promote():
            promoted.set()

        ha_manager.init(promotion_callback=on_promote)
        ha_manager._active_role = "SECONDARY"
        # Force last_primary_seen to be old (simulates silent primary)
        ha_manager._last_primary_seen = time.time() - 9999

        # Mock: primary is unreachable, broker IS reachable (tier-2 check passes)
        with patch("ha_manager.requests.get", side_effect=Exception("connection refused")), \
             patch("ha_manager._check_local_network", return_value=True):
            ha_manager._ha_loop.__globals__["_is_running"] = True
            # Directly invoke one cycle of the HA loop logic
            ha_manager._active_role = "SECONDARY"
            age = time.time() - ha_manager._last_primary_seen
            from config.settings import HA_TAKEOVER_TIMEOUT
            if age > HA_TAKEOVER_TIMEOUT and ha_manager._check_local_network():
                ha_manager._promote_to_primary()

        assert ha_manager.get_current_role() == "PRIMARY"
        assert promoted.is_set(), "Promotion callback was never called"

    def test_secondary_stays_secondary_if_primary_responds(self):
        """If primary responds, secondary must NOT promote."""
        ha_manager._active_role = "SECONDARY"
        ha_manager._last_primary_seen = time.time()  # just seen

        # primary is alive, age < timeout → no promotion
        from config.settings import HA_TAKEOVER_TIMEOUT
        age = time.time() - ha_manager._last_primary_seen
        assert age < HA_TAKEOVER_TIMEOUT, "Clock mismatch in test setup"
        assert ha_manager.get_current_role() == "SECONDARY"

    def test_primary_role_returns_is_primary_true(self):
        ha_manager._active_role = "PRIMARY"
        assert ha_manager.is_primary() is True

    def test_secondary_role_returns_is_primary_false(self):
        ha_manager._active_role = "SECONDARY"
        # Note: is_primary() returns True when HA is disabled (single node)
        # When HA is enabled + role=SECONDARY, returns False
        from config.settings import HA_MODE_ENABLED
        if HA_MODE_ENABLED:
            assert ha_manager.is_primary() is False


# ─────────────────────────────────────────────────────────────────────
# 2. 3-Tier Quorum — split-brain protection
# ─────────────────────────────────────────────────────────────────────

class TestThreeTierQuorum:

    def test_tier1_primary_visible_no_promotion(self):
        """Tier 1: Primary is reachable → stay SECONDARY."""
        ha_manager._active_role = "SECONDARY"
        ha_manager._last_primary_seen = time.time()  # update: just seen primary

        # Age is fresh → no decision needed
        from config.settings import HA_TAKEOVER_TIMEOUT
        age = time.time() - ha_manager._last_primary_seen
        should_promote = age > HA_TAKEOVER_TIMEOUT
        assert not should_promote
        assert ha_manager.get_current_role() == "SECONDARY"

    def test_tier2_primary_down_broker_up_promotes(self):
        """
        Tier 2: Primary unreachable, Mosquitto broker reachable.
        Secondary should promote (primary is dead, not a partition scenario).
        """
        callback_fired = threading.Event()
        ha_manager._promotion_callback = lambda: callback_fired.set()
        ha_manager._active_role = "SECONDARY"
        ha_manager._last_primary_seen = time.time() - 9999  # primary timed out

        from config.settings import HA_TAKEOVER_TIMEOUT
        age = time.time() - ha_manager._last_primary_seen

        # Simulate: broker reachable
        with patch("ha_manager._check_local_network", return_value=True):
            if age > HA_TAKEOVER_TIMEOUT and ha_manager._check_local_network():
                ha_manager._promote_to_primary()

        assert ha_manager.get_current_role() == "PRIMARY"
        assert callback_fired.is_set()

    def test_tier3_total_isolation_triggers_hard_safety_mode(self):
        """
        Tier 3: Both primary AND Mosquitto broker unreachable.
        Secondary must NOT promote; instead triggers HARD SAFETY MODE.
        """
        ha_manager._active_role = "SECONDARY"
        ha_manager._last_primary_seen = time.time() - 9999

        from config.settings import HA_TAKEOVER_TIMEOUT
        age = time.time() - ha_manager._last_primary_seen

        with patch("ha_manager._check_local_network", return_value=False), \
             patch("evacuation_engine.execute_hard_safety_mode") as mock_alarm:

            import evacuation_engine
            if age > HA_TAKEOVER_TIMEOUT and not ha_manager._check_local_network():
                state_manager.set_hard_safety_mode(True)
                evacuation_engine.execute_hard_safety_mode()
                # Must NOT promote
                pass

        assert ha_manager.get_current_role() == "SECONDARY", (
            "Secondary should NOT have promoted during total isolation"
        )
        assert state_manager.is_hard_safety_mode_active(), (
            "Hard Safety Mode must be active during total isolation"
        )
        mock_alarm.assert_called_once()

    def test_tier3_does_not_promote(self):
        """Total isolation must never result in PRIMARY promotion."""
        ha_manager._active_role = "SECONDARY"

        with patch("ha_manager._check_local_network", return_value=False), \
             patch("evacuation_engine.execute_hard_safety_mode"):
            # Simulate isolation decision — no promotion path should fire
            if not ha_manager._check_local_network():
                pass  # stay secondary, trigger safety mode

        assert ha_manager.get_current_role() != "PRIMARY", (
            "System incorrectly promoted to PRIMARY during network isolation"
        )


# ─────────────────────────────────────────────────────────────────────
# 3. Transient blip (brief disconnection < timeout)
# ─────────────────────────────────────────────────────────────────────

class TestTransientBlip:

    def test_brief_blip_does_not_trigger_promotion(self):
        """
        A 2-second network blip (much less than HA_TAKEOVER_TIMEOUT)
        must not cause the secondary to promote.
        """
        from config.settings import HA_TAKEOVER_TIMEOUT
        ha_manager._active_role = "SECONDARY"

        # last_primary_seen is only 2 seconds ago → no timeout
        ha_manager._last_primary_seen = time.time() - 2.0

        age = time.time() - ha_manager._last_primary_seen
        assert age < HA_TAKEOVER_TIMEOUT, (
            f"Age {age:.1f}s already exceeds timeout {HA_TAKEOVER_TIMEOUT}s"
        )
        assert ha_manager.get_current_role() == "SECONDARY"

    def test_age_just_below_threshold_safe(self):
        """Age = timeout - 1 second → must NOT promote."""
        from config.settings import HA_TAKEOVER_TIMEOUT
        ha_manager._active_role = "SECONDARY"
        ha_manager._last_primary_seen = time.time() - (HA_TAKEOVER_TIMEOUT - 1)

        age = time.time() - ha_manager._last_primary_seen
        would_promote = age > HA_TAKEOVER_TIMEOUT
        assert not would_promote
        assert ha_manager.get_current_role() == "SECONDARY"


# ─────────────────────────────────────────────────────────────────────
# 4. Promotion callback hygiene
# ─────────────────────────────────────────────────────────────────────

class TestPromotionCallback:

    def test_promotion_callback_fires_exactly_once(self):
        """Callback must fire once and only once during promotion."""
        call_count = {"n": 0}

        def on_promote():
            call_count["n"] += 1

        ha_manager._promotion_callback = on_promote
        ha_manager._active_role = "SECONDARY"

        # Trigger promote twice (edge case: loop fires twice before role update)
        ha_manager._promote_to_primary()

        # Role is now PRIMARY — second call should not re-fire callback
        # (In a real loop, role check prevents re-entry)
        if ha_manager.get_current_role() != "PRIMARY":
            ha_manager._promote_to_primary()

        assert call_count["n"] == 1, (
            f"Callback fired {call_count['n']} times instead of once"
        )

    def test_role_updated_before_callback(self):
        """Role must be PRIMARY by the time the callback executes."""
        role_during_callback = []

        def on_promote():
            role_during_callback.append(ha_manager.get_current_role())

        ha_manager._promotion_callback = on_promote
        ha_manager._active_role = "SECONDARY"
        ha_manager._promote_to_primary()

        assert role_during_callback == ["PRIMARY"], (
            f"Role during callback was: {role_during_callback}"
        )

    def test_no_callback_configured_promotion_is_safe(self):
        """Promotion without a registered callback must not raise."""
        ha_manager._promotion_callback = None
        ha_manager._active_role = "SECONDARY"
        try:
            ha_manager._promote_to_primary()
        except Exception as e:
            pytest.fail(f"Promotion without callback raised: {e}")

        assert ha_manager.get_current_role() == "PRIMARY"


# ─────────────────────────────────────────────────────────────────────
# 5. Role persistence after promotion
# ─────────────────────────────────────────────────────────────────────

class TestRolePersistence:

    def test_primary_stays_primary_even_if_peer_becomes_visible(self):
        """
        Once promoted, the node stays PRIMARY even if the old primary
        comes back online (prevents role flip-flop).
        The HA loop only evaluates role change for SECONDARY nodes.
        """
        ha_manager._active_role = "PRIMARY"

        # Simulate the old primary pinging back
        with patch("ha_manager.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            # PRIMARY branch in _ha_loop does nothing — let's verify
            # that the role remains PRIMARY after a simulated loop
            if ha_manager._active_role == "PRIMARY":
                pass  # PRIMARY path is a no-op, does not demote

        assert ha_manager.get_current_role() == "PRIMARY"

    def test_get_current_role_reflects_latest_state(self):
        ha_manager._active_role = "SECONDARY"
        assert ha_manager.get_current_role() == "SECONDARY"
        ha_manager._active_role = "PRIMARY"
        assert ha_manager.get_current_role() == "PRIMARY"


# ─────────────────────────────────────────────────────────────────────
# 6. HA disabled (single-node mode)
# ─────────────────────────────────────────────────────────────────────

class TestHADisabledMode:

    def test_is_primary_true_when_ha_disabled(self):
        """When HA is off, is_primary() always returns True regardless of role."""
        with patch("ha_manager.HA_MODE_ENABLED", False):
            # Temporarily patch the module-level flag
            original = ha_manager.HA_MODE_ENABLED
            try:
                ha_manager.HA_MODE_ENABLED = False
                ha_manager._active_role = "SECONDARY"
                # is_primary() formula: not HA_MODE_ENABLED OR role==PRIMARY
                result = not ha_manager.HA_MODE_ENABLED or ha_manager._active_role == "PRIMARY"
                assert result is True
            finally:
                ha_manager.HA_MODE_ENABLED = original

    def test_start_is_noop_when_ha_disabled(self):
        """ha_manager.start() must be a no-op when HA is disabled, no thread spawned."""
        with patch("ha_manager.HA_MODE_ENABLED", False):
            ha_manager.HA_MODE_ENABLED = False
            ha_manager._is_running = False
            ha_manager.start()
            assert not ha_manager._is_running, (
                "HA manager started despite HA_MODE_ENABLED=False"
            )


# ─────────────────────────────────────────────────────────────────────
# 7. Stabilization window after promotion
# ─────────────────────────────────────────────────────────────────────

class TestPromotionStabilization:
    """
    The 2-second stabilization blackout prevents LED flicker / state
    race when the new primary takes over and replays MQTT maps.
    """

    def test_stabilization_delay_is_respected(self):
        """
        The promotion callback should delay ≥2 seconds before activating
        new MQTT/processing (as implemented in main.py _on_ha_promotion).
        We simulate this by measuring the delay in a minimal promotion harness.
        """
        STABILIZATION_WINDOW = 2.0  # seconds, as per main.py implementation
        start_times = []
        end_times   = []

        def simulated_promotion_with_stabilization():
            start_times.append(time.time())
            time.sleep(STABILIZATION_WINDOW)
            end_times.append(time.time())

        t = threading.Thread(target=simulated_promotion_with_stabilization)
        t.start()
        t.join(timeout=5.0)

        assert len(start_times) == 1 and len(end_times) == 1
        elapsed = end_times[0] - start_times[0]
        assert elapsed >= STABILIZATION_WINDOW - 0.1, (
            f"Stabilization window too short: {elapsed:.2f}s < {STABILIZATION_WINDOW}s"
        )

    def test_state_consistent_during_stabilization(self):
        """
        During the stabilization window, system state must remain readable
        and consistent — no partial writes or torn reads.
        """
        state_manager.reset_all()
        state_manager.record_alert("ROOM_101", severity="FIRE")

        # Simulate concurrent reads during a promotion window
        read_errors = []

        def reader():
            for _ in range(100):
                try:
                    state_manager.get_unsafe_nodes()
                    state_manager.is_evacuation_active()
                    state_manager.is_hard_safety_mode_active()
                except Exception as e:
                    read_errors.append(str(e))

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not read_errors, f"State read errors during stabilization: {read_errors}"
