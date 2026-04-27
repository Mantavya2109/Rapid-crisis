"""
system_mode.py
--------------
Global building-level state machine.

Three modes (ordered by severity):

  NORMAL_MODE     — all zones safe; no active alerts
  ALERT_MODE      — at least one WARNING or PREDICTIVE_FIRE node exists
  EVACUATION_MODE — at least one DANGER or CRITICAL_FIRE node exists

Transitions are computed from live node states and broadcast to:
  - LED driver (affects un-assigned zones in future)
  - Event filter (EVACUATION always emits immediately)
  - REST API (GET /system/mode)
  - Cloud sync (mode changes are always forwarded)

Mode can also be manually overridden by an operator (POST /system/mode)
and will stay pinned until manually released or cleared by recovery.
"""

import threading
import time
from typing import Callable, Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Mode constants (ordered by severity)
# ─────────────────────────────────────────────────────────────────────

NORMAL_MODE     = "NORMAL_MODE"
ALERT_MODE      = "ALERT_MODE"
EVACUATION_MODE = "EVACUATION_MODE"

_MODE_SEVERITY = {
    NORMAL_MODE:     0,
    ALERT_MODE:      1,
    EVACUATION_MODE: 2,
}

# Node states that trigger each mode
_EVACUATION_TRIGGERS = frozenset({"DANGER", "CRITICAL_FIRE"})
_ALERT_TRIGGERS      = frozenset({"WARNING", "PREDICTIVE_FIRE"})


# ─────────────────────────────────────────────────────────────────────
# Module state
# ─────────────────────────────────────────────────────────────────────

_current_mode:    str             = NORMAL_MODE
_mode_since:      float           = time.time()
_manual_override: bool            = False      # True → only operator can clear
_lock             = threading.Lock()

# Callbacks: called whenever mode changes
_change_callbacks: List[Callable[[str, str], None]] = []


# ─────────────────────────────────────────────────────────────────────
# Mode computation
# ─────────────────────────────────────────────────────────────────────

def compute_mode(node_states: Dict[str, str]) -> str:
    """
    Compute the appropriate global mode from a dict of { nodeId → state }.
    Returns one of: NORMAL_MODE | ALERT_MODE | EVACUATION_MODE.

    Priority: if ANY node is DANGER/CRITICAL → EVACUATION_MODE
              if ANY node is WARNING/PREDICTIVE → ALERT_MODE
              otherwise → NORMAL_MODE
    """
    if any(s in _EVACUATION_TRIGGERS for s in node_states.values()):
        return EVACUATION_MODE
    if any(s in _ALERT_TRIGGERS for s in node_states.values()):
        return ALERT_MODE
    return NORMAL_MODE


def update_from_node_states(node_states: Dict[str, str]) -> bool:
    """
    Recompute global mode from current node states.
    Returns True if mode changed.

    Skips the update if the mode is manually overridden by an operator.
    """
    global _current_mode, _mode_since, _manual_override

    with _lock:
        if _manual_override:
            return False

        new_mode = compute_mode(node_states)
        if new_mode == _current_mode:
            return False

        old_mode      = _current_mode
        _current_mode = new_mode
        _mode_since   = time.time()

    log.info(
        "🌐 System mode: %s → %s  (nodes: %s)",
        old_mode, new_mode,
        {s: sum(1 for v in node_states.values() if v == s) for s in set(node_states.values())}
    )
    _fire_callbacks(old_mode, new_mode)
    return True


# ─────────────────────────────────────────────────────────────────────
# Manual override (operator)
# ─────────────────────────────────────────────────────────────────────

def set_manual_mode(mode: str, reason: str = "") -> bool:
    """
    Pin the system mode manually (operator override).
    Auto-computation is suspended until release_manual_override() is called.
    Returns False if mode is not a valid mode string.
    """
    global _current_mode, _mode_since, _manual_override

    if mode not in _MODE_SEVERITY:
        return False

    with _lock:
        old = _current_mode
        _current_mode    = mode
        _mode_since      = time.time()
        _manual_override = True

    log.warning(
        "🔐 System mode MANUALLY set: %s → %s  reason='%s'", old, mode, reason
    )
    _fire_callbacks(old, mode)
    return True


def release_manual_override() -> None:
    """Release operator override; auto-computation resumes on next sensor reading."""
    global _manual_override
    with _lock:
        _manual_override = False
    log.info("🔓 System mode manual override released — auto-compute resumed.")


# ─────────────────────────────────────────────────────────────────────
# Queries
# ─────────────────────────────────────────────────────────────────────

def get_mode() -> str:
    with _lock:
        return _current_mode


def get_status() -> Dict:
    with _lock:
        return {
            "mode":            _current_mode,
            "since_ts":        _mode_since,
            "since_sec":       round(time.time() - _mode_since, 1),
            "manual_override": _manual_override,
        }


def is_evacuation() -> bool:
    with _lock:
        return _current_mode == EVACUATION_MODE


def is_alert_or_above() -> bool:
    with _lock:
        return _MODE_SEVERITY.get(_current_mode, 0) >= 1


# ─────────────────────────────────────────────────────────────────────
# Change callbacks
# ─────────────────────────────────────────────────────────────────────

def register_change_callback(fn: Callable[[str, str], None]) -> None:
    """
    Register a callback fn(old_mode, new_mode) called whenever mode changes.
    Used by cloud_sync to forward mode changes immediately.
    """
    _change_callbacks.append(fn)


def _fire_callbacks(old: str, new: str) -> None:
    for fn in _change_callbacks:
        try:
            fn(old, new)
        except Exception as exc:  # pylint: disable=broad-except
            log.error("Mode callback raised: %s", exc, exc_info=True)
