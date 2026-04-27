"""
recovery_manager.py
--------------------
Post-fire recovery state machine.

States:
  IDLE         — normal operation; no recent fire
  RECOVERING   — all fire nodes cleared; cooling-down (RECOVER_COOLDOWN_SEC)
  RECOVERED    — cooldown complete; system back to NORMAL_MODE
  MANUAL_HOLD  — operator has pinned manual-hold; prevents auto-recovery

Recovery flow:
  1. Fire is detected → EVACUATION_MODE
  2. Operator clears all fire alerts via POST /alerts/clear-all
     OR all nodes return to NORMAL naturally
  3. recovery_manager detects zero active danger nodes → RECOVERING
  4. Waits RECOVER_COOLDOWN_SEC (default 30s) watching for re-ignition
  5. If no new danger → RECOVERED:
       - priority_resolver.reset_all()   — clears LED ownership
       - system_mode back to NORMAL_MODE — auto-compute resumes
       - LEDs pulsed GREEN briefly then set NORMAL
  6. If danger re-detected during cooldown → abort; back to EVACUATION_MODE

Operator shortcuts:
  POST /system/recover          — skip cooldown, force immediate recovery
  POST /alerts/clear-all        — mass-clear all alerts (audited)

Usage:
  Start in main.py: recovery_manager.start()
  Called from sensor_processor on every state update.
"""

import threading
import time
from typing import Callable, Dict, List, Optional

from config.settings import RECOVER_COOLDOWN_SEC
from logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Recovery states
# ─────────────────────────────────────────────────────────────────────

IDLE        = "IDLE"
RECOVERING  = "RECOVERING"
RECOVERED   = "RECOVERED"
MANUAL_HOLD = "MANUAL_HOLD"

_DANGER_STATES = frozenset({"DANGER", "CRITICAL_FIRE"})


# ─────────────────────────────────────────────────────────────────────
# Module state
# ─────────────────────────────────────────────────────────────────────

_recovery_state:   str   = IDLE
_recovery_start:   float = 0.0
_lock              = threading.Lock()

_on_recovered_callbacks: List[Callable] = []

_monitor_thread: Optional[threading.Thread] = None
_get_node_states_fn: Optional[Callable[[], Dict[str, str]]] = None


# ─────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────

def start(get_node_states: Callable[[], Dict[str, str]]) -> None:
    """
    Start the recovery monitor thread.

    Parameters
    ----------
    get_node_states : callable that returns { nodeId → state_str }
                      e.g. sensor_processor.get_all_node_states
    """
    global _get_node_states_fn, _monitor_thread
    _get_node_states_fn = get_node_states

    if _monitor_thread and _monitor_thread.is_alive():
        return

    _monitor_thread = threading.Thread(
        target=_monitor_loop, name="recovery-monitor", daemon=True
    )
    _monitor_thread.start()
    log.info(
        "♻️  Recovery monitor started (cooldown=%ds, check every 5s).",
        RECOVER_COOLDOWN_SEC,
    )


# ─────────────────────────────────────────────────────────────────────
# Monitor loop
# ─────────────────────────────────────────────────────────────────────

def _monitor_loop() -> None:
    while True:
        time.sleep(5)
        _tick()


def _tick() -> None:
    global _recovery_state, _recovery_start

    if _get_node_states_fn is None:
        return

    node_states = _get_node_states_fn()
    # Extract just the state strings (node_states may return dicts with "state" key)
    if node_states and isinstance(next(iter(node_states.values()), None), dict):
        states = {n: d.get("state", "NORMAL") for n, d in node_states.items()}
    else:
        states = node_states  # already flat { nodeId → state str }

    has_danger = any(s in _DANGER_STATES for s in states.values())

    with _lock:
        current = _recovery_state

    if current == MANUAL_HOLD:
        return  # Operator hold — do nothing

    if current == IDLE:
        # Nothing to do — fire will flip EVACUATION_MODE on its own
        pass

    elif current == RECOVERING:
        if has_danger:
            # Re-ignition detected during cooldown — abort recovery
            with _lock:
                _recovery_state = IDLE
            log.warning(
                "⚠️  Recovery ABORTED — danger re-detected during cooldown."
            )
        else:
            elapsed = time.time() - _recovery_start
            remaining = RECOVER_COOLDOWN_SEC - elapsed
            if remaining <= 0:
                _execute_recovery()
            else:
                log.debug(
                    "♻️  Recovering — %.0fs remaining in cooldown.", remaining
                )

    elif current in (IDLE, RECOVERED):
        # Check if evacuation mode just ended (no more danger nodes)
        # We rely on the caller (notify_all_clear) to trigger RECOVERING
        pass
def notify_all_clear() -> bool:
    """
    Called externally when all fire alerts have been cleared.
    Transitions IDLE → RECOVERING.
    Returns True if recovery cooldown started.
    """
    global _recovery_state, _recovery_start

    with _lock:
        if _recovery_state in (RECOVERING, MANUAL_HOLD):
            return False
        _recovery_state = RECOVERING
        _recovery_start = time.time()

    log.info(
        "♻️  All-clear received — starting %ds recovery cooldown.",
        RECOVER_COOLDOWN_SEC,
    )
    return True


def force_immediate_recovery(reason: str = "operator") -> bool:
    """
    Skip cooldown and execute recovery immediately.
    Called by POST /system/recover.
    Returns True if recovery was executed.
    """
    global _recovery_state

    with _lock:
        if _recovery_state == MANUAL_HOLD:
            log.warning("Cannot force recovery — manual hold is active.")
            return False

    log.warning("⚡ Immediate recovery forced by %s.", reason)
    _execute_recovery()
    return True


def set_manual_hold(reason: str = "") -> None:
    """Pin system in MANUAL_HOLD — prevents auto-recovery."""
    global _recovery_state
    with _lock:
        _recovery_state = MANUAL_HOLD
    log.warning("🔐 Recovery MANUAL_HOLD set. reason='%s'", reason)


def release_manual_hold() -> None:
    global _recovery_state
    with _lock:
        if _recovery_state == MANUAL_HOLD:
            _recovery_state = IDLE
    log.info("🔓 Recovery manual hold released.")


# ─────────────────────────────────────────────────────────────────────
# Recovery execution
# ─────────────────────────────────────────────────────────────────────

def _execute_recovery() -> None:
    global _recovery_state
    import priority_resolver
    import system_mode
    import state_manager

    log.info("✅ Recovery executing — resetting all zones to NORMAL.")

    # 1. Clear all priority ownership → LEDs reset to NORMAL
    priority_resolver.reset_all()

    # 2. Clear evacuation flag
    state_manager.set_evacuation_active(False)

    # 3. Release system mode manual override if set during evacuation
    system_mode.release_manual_override()

    with _lock:
        _recovery_state = RECOVERED

    log.info("✅ System RECOVERED — all zones NORMAL, evacuation mode cleared.")

    # 4. Fire callbacks
    for fn in _on_recovered_callbacks:
        try:
            fn()
        except Exception as exc:  # pylint: disable=broad-except
            log.error("Recovery callback raised: %s", exc, exc_info=True)


# ─────────────────────────────────────────────────────────────────────
# Queries
# ─────────────────────────────────────────────────────────────────────

def get_status() -> Dict:
    with _lock:
        return {
            "recovery_state":    _recovery_state,
            "cooldown_total_sec": RECOVER_COOLDOWN_SEC,
            "cooldown_elapsed":  round(time.time() - _recovery_start, 1)
                                 if _recovery_state == RECOVERING else 0,
            "cooldown_remaining": max(
                0, RECOVER_COOLDOWN_SEC - (time.time() - _recovery_start)
            ) if _recovery_state == RECOVERING else 0,
        }


def register_recovered_callback(fn: Callable) -> None:
    """fn() called when recovery completes successfully."""
    _on_recovered_callbacks.append(fn)


def get_state() -> str:
    with _lock:
        return _recovery_state
