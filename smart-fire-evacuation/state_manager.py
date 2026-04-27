"""
state_manager.py
----------------
In-memory state store for the evacuation system.

Tracks:
  - Per-node sensor snapshots
  - Unsafe node set
  - Active alerts: { node → {severity, alert_time} }
  - Alert severity levels: FIRE > WARNING > OK
  - Global evacuation flag

New in this version:
  - Alert severity-aware clear (FIRE alerts require force=True)
  - Hazard weight export for Dijkstra
  - Persistence integration (periodic flush via persistence.py)
"""

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import persistence
from config.settings import ALERT_DEBOUNCE_SEC
from logger import get_logger

log = get_logger(__name__)

_lock = threading.RLock()

_node_sensor_data: Dict[str, Dict[str, Any]] = {}
_unsafe_nodes: Set[str] = set()

# { nodeId → {"severity": "FIRE"|"WARNING", "alert_time": float} }
_active_alerts: Dict[str, Dict[str, Any]] = {}

_evacuation_active: bool = False

# Severity ordering
_SEVERITY_RANK = {"OK": 0, "WARNING": 1, "FIRE": 2}

_node_sequence: Dict[str, int] = {}
_hard_safety_mode_active: bool = False
_hard_safety_mode_time: float = 0.0


# ─────────────────────────────────────────────────────────────────────
# Startup restore
# ─────────────────────────────────────────────────────────────────────

def restore_from_db() -> None:
    """Reload active alerts from SQLite after Pi restart."""
    rows = persistence.load_alerts()
    with _lock:
        for row in rows:
            node = row["node_id"]
            _active_alerts[node] = {
                "severity":   row.get("severity", "FIRE"),
                "alert_time": row.get("alert_time", time.time()),
            }
            _unsafe_nodes.add(node)
    if rows:
        log.warning("🔄 Restored %d active alert(s) from SQLite.", len(rows))


# ─────────────────────────────────────────────────────────────────────
# Persistence snapshot (registered with persistence module)
# ─────────────────────────────────────────────────────────────────────

def _flush_alerts(conn, now: float) -> None:
    """Write current active_alerts to SQLite alerts table."""
    with _lock:
        alerts_copy = dict(_active_alerts)

    # Upsert all current alerts
    for node, alert in alerts_copy.items():
        conn.execute(
            """INSERT INTO alerts (node_id, severity, alert_time, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(node_id) DO UPDATE SET
                   severity=excluded.severity,
                   alert_time=excluded.alert_time,
                   updated_at=excluded.updated_at""",
            (node, alert["severity"], alert["alert_time"], now),
        )

    # Delete cleared alerts
    if alerts_copy:
        placeholders = ",".join("?" * len(alerts_copy))
        conn.execute(
            f"DELETE FROM alerts WHERE node_id NOT IN ({placeholders})",
            list(alerts_copy.keys()),
        )
    else:
        conn.execute("DELETE FROM alerts")


persistence.register_snapshot("alerts", _flush_alerts)


# ─────────────────────────────────────────────────────────────────────
# Sensor data
# ─────────────────────────────────────────────────────────────────────

def update_sensor_data(node: str, payload: Dict[str, Any]) -> None:
    with _lock:
        _node_sensor_data[node] = {
            **payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def get_all_sensor_data() -> Dict[str, Dict]:
    with _lock:
        return dict(_node_sensor_data)


def get_sensor_data(node: str) -> Optional[Dict]:
    with _lock:
        return _node_sensor_data.get(node)


# ─────────────────────────────────────────────────────────────────────
# Unsafe nodes
# ─────────────────────────────────────────────────────────────────────

def mark_unsafe(node: str) -> None:
    with _lock:
        if node not in _unsafe_nodes:
            _unsafe_nodes.add(node)
            log.warning("🔴 Node marked unsafe: %s", node)


def mark_safe(node: str) -> None:
    with _lock:
        _unsafe_nodes.discard(node)
        log.info("🟢 Node marked safe: %s", node)


def get_unsafe_nodes() -> List[str]:
    with _lock:
        return sorted(_unsafe_nodes)


def is_unsafe(node: str) -> bool:
    with _lock:
        return node in _unsafe_nodes


# ─────────────────────────────────────────────────────────────────────
# Alert tracking + severity + debounce
# ─────────────────────────────────────────────────────────────────────

def record_alert(node: str, severity: str = "FIRE") -> None:
    """
    Record a fire/warning alert for a node.
    If an existing alert has HIGHER severity, keeps the existing one.
    Always updates if new severity is higher.
    """
    with _lock:
        existing = _active_alerts.get(node)
        if existing:
            existing_rank = _SEVERITY_RANK.get(existing["severity"], 0)
            new_rank      = _SEVERITY_RANK.get(severity, 2)
            if new_rank > existing_rank:
                # Escalate severity but keep original alert_time
                existing["severity"] = severity
                log.warning("⚠️  Alert for '%s' escalated to %s", node, severity)
            # Don't reset alert_time — debounce uses original timestamp
        else:
            _active_alerts[node] = {
                "severity":   severity,
                "alert_time": time.time(),
            }
            _unsafe_nodes.add(node)
            log.warning("🚨 Alert recorded: node='%s' severity=%s", node, severity)

    persistence.mark_dirty()  # SD-friendly: deferred write within flush interval


def is_alert_debounced(node: str) -> bool:
    """Return True if an alert was triggered within the debounce window."""
    with _lock:
        alert = _active_alerts.get(node)
        if alert is None:
            return False
        age = time.time() - alert["alert_time"]
        if age < ALERT_DEBOUNCE_SEC:
            log.info("⏱️  Debounced: node='%s' last=%.1fs ago", node, age)
            return True
        return False


def clear_alert(node: str, force: bool = False) -> bool:
    """
    Clear the active alert for a node.

    Parameters
    ----------
    force : if True, clears even FIRE-level alerts.
            If False, refuses to clear FIRE alerts (safety lock).

    Returns True if cleared, False if blocked.
    """
    with _lock:
        alert = _active_alerts.get(node)
        if alert is None:
            log.info("clear_alert: no alert for '%s'.", node)
            _unsafe_nodes.discard(node)
            return True

        if alert["severity"] == "FIRE" and not force:
            log.warning(
                "🔒 Cannot clear FIRE alert for '%s' without force=True. "
                "Confirm physical clearance before forcing.", node,
            )
            return False

        del _active_alerts[node]
        _unsafe_nodes.discard(node)
        log.info("✅ Alert cleared for '%s' (force=%s).", node, force)

    persistence.mark_dirty()  # SD-friendly: deferred write within flush interval
    return True


def get_all_alerts() -> List[Dict[str, Any]]:
    with _lock:
        now = time.time()
        return [
            {
                "nodeId":        node,
                "severity":      a["severity"],
                "alert_time":    a["alert_time"],
                "alert_age_sec": round(now - a["alert_time"], 1),
            }
            for node, a in _active_alerts.items()
        ]


def get_alert_times() -> Dict[str, float]:
    """Return { nodeId → alert_time } for hazard weight computation."""
    with _lock:
        return {node: a["alert_time"] for node, a in _active_alerts.items()}


def get_last_alert_time(node: str) -> Optional[float]:
    with _lock:
        a = _active_alerts.get(node)
        return a["alert_time"] if a else None


def get_alert_severity(node: str) -> Optional[str]:
    with _lock:
        a = _active_alerts.get(node)
        return a["severity"] if a else None


# ─────────────────────────────────────────────────────────────────────
# Evacuation flag
# ─────────────────────────────────────────────────────────────────────

def set_evacuation_active(active: bool) -> None:
    global _evacuation_active
    with _lock:
        if active != _evacuation_active:
            _evacuation_active = active
            log.warning("🚨 EVACUATION %s", "ACTIVATED" if active else "DEACTIVATED")


def is_evacuation_active() -> bool:
    with _lock:
        return _evacuation_active

# ─────────────────────────────────────────────────────────────────────
# Sequence Validation & Hard Safety Mode Settings
# ─────────────────────────────────────────────────────────────────────

def get_node_sequence(node: str) -> int:
    with _lock:
        return _node_sequence.get(node, -1)

def update_node_sequence(node: str, seq: int) -> None:
    with _lock:
        _node_sequence[node] = seq

def set_hard_safety_mode(active: bool) -> None:
    global _hard_safety_mode_active, _hard_safety_mode_time
    with _lock:
        if active != _hard_safety_mode_active:
            _hard_safety_mode_active = active
            _hard_safety_mode_time = time.time()
            if active:
                log.error("🛑 HARD SAFETY MODE ACTIVATED 🛑")
            else:
                log.warning("✅ HARD SAFETY MODE DEACTIVATED")

def is_hard_safety_mode_active() -> bool:
    with _lock:
        return _hard_safety_mode_active

def get_hard_safety_mode_time() -> float:
    with _lock:
        return _hard_safety_mode_time


# ─────────────────────────────────────────────────────────────────────
# Full state snapshot
# ─────────────────────────────────────────────────────────────────────

def get_full_state() -> Dict:
    with _lock:
        now = time.time()
        return {
            "evacuation_active": _evacuation_active,
            "unsafe_nodes":      sorted(_unsafe_nodes),
            "active_alerts": [
                {
                    "nodeId":        node,
                    "severity":      a["severity"],
                    "alert_time":    a["alert_time"],
                    "alert_age_sec": round(now - a["alert_time"], 1),
                }
                for node, a in _active_alerts.items()
            ],
            "sensor_data": dict(_node_sensor_data),
        }


def reset_all() -> None:
    """Full system reset. Use with caution."""
    global _evacuation_active, _hard_safety_mode_active
    with _lock:
        _node_sensor_data.clear()
        _unsafe_nodes.clear()
        _active_alerts.clear()
        _node_sequence.clear()
        _evacuation_active = False
        _hard_safety_mode_active = False
    persistence.flush_now()
    log.warning("🔄 Full system state RESET.")
