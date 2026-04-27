"""
event_log.py
------------
Structured event logging for the Smart Fire Evacuation System.

Records the full timeline of:
  - Fire detections
  - Evacuation triggers and results
  - Device registration / heartbeat failures
  - LED command successes / failures
  - Cloud sync status
  - Alert clears / resets

Events are stored in:
  - SQLite `events` table (queryable, persistent)
  - Rotating JSONL file (logs/events.jsonl) for grep / Grafana import

Exposed via GET /events API endpoint.
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import persistence
from logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Event types (constants for consistent naming)
# ─────────────────────────────────────────────────────────────────────

FIRE_DETECTED        = "FIRE_DETECTED"
EVACUATION_TRIGGERED = "EVACUATION_TRIGGERED"
EVACUATION_COMPLETE  = "EVACUATION_COMPLETE"
ALERT_CLEARED        = "ALERT_CLEARED"
PATH_COMPUTED        = "PATH_COMPUTED"
LED_SENT             = "LED_SENT"
LED_FAILED           = "LED_FAILED"
LED_BACKUP_USED      = "LED_BACKUP_USED"
LED_FAILED_CRITICAL  = "LED_FAILED_CRITICAL"  # Both primary + backup exhausted
CLOUD_SYNC_OK        = "CLOUD_SYNC_OK"
CLOUD_SYNC_FAILED    = "CLOUD_SYNC_FAILED"
DEVICE_REGISTERED    = "DEVICE_REGISTERED"
DEVICE_OFFLINE       = "DEVICE_OFFLINE"
DEVICE_ONLINE        = "DEVICE_ONLINE"
HEARTBEAT_RECEIVED   = "HEARTBEAT_RECEIVED"
SYSTEM_RESET         = "SYSTEM_RESET"
AUTH_FAILURE         = "AUTH_FAILURE"
RATE_LIMITED         = "RATE_LIMITED"


# ─────────────────────────────────────────────────────────────────────
# Core logger
# ─────────────────────────────────────────────────────────────────────

def log_event(
    event_type: str,
    node_id:   Optional[str]  = None,
    device_id: Optional[str]  = None,
    severity:  Optional[str]  = None,
    metadata:  Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Record a structured event.

    Parameters
    ----------
    event_type : one of the EVENT_TYPE constants above
    node_id    : building node associated with the event
    device_id  : ESP32 device ID (if applicable)
    severity   : "INFO" | "WARNING" | "ERROR" | "CRITICAL"
    metadata   : any additional key-value context

    Returns
    -------
    The event dict that was stored.
    """
    ts  = time.time()
    iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    event: Dict[str, Any] = {
        "ts":         ts,
        "iso":        iso,
        "event_type": event_type,
        "node_id":    node_id,
        "device_id":  device_id,
        "severity":   severity or _default_severity(event_type),
        "metadata":   metadata or {},
    }

    # ── SQLite (persistent, queryable) ────────────────────────────────
    persistence.write_event(
        event_type=event_type,
        ts=ts,
        node_id=node_id,
        device_id=device_id,
        severity=event["severity"],
        metadata=metadata,
    )

    # ── Structured log line ───────────────────────────────────────────
    _log_line(event)

    return event


def _default_severity(event_type: str) -> str:
    """Map event type to a default severity level."""
    critical = {FIRE_DETECTED, EVACUATION_TRIGGERED, LED_FAILED_CRITICAL}
    warning  = {LED_FAILED, CLOUD_SYNC_FAILED, DEVICE_OFFLINE, AUTH_FAILURE}
    error    = set()
    if event_type in critical:
        return "CRITICAL"
    if event_type in warning:
        return "WARNING"
    return "INFO"


def _log_line(event: Dict[str, Any]) -> None:
    """Emit structured log at the appropriate level."""
    msg = (
        f"[EVENT] {event['event_type']} | "
        f"node={event['node_id']} | dev={event['device_id']} | "
        f"severity={event['severity']} | {event['metadata']}"
    )
    sev = event["severity"]
    if sev == "CRITICAL":
        log.critical(msg)
    elif sev == "WARNING":
        log.warning(msg)
    elif sev == "ERROR":
        log.error(msg)
    else:
        log.info(msg)


# ─────────────────────────────────────────────────────────────────────
# Convenience wrappers
# ─────────────────────────────────────────────────────────────────────

def fire_detected(node_id: str, device_id: str, temp: float, smoke: float) -> None:
    log_event(
        FIRE_DETECTED,
        node_id=node_id, device_id=device_id, severity="CRITICAL",
        metadata={"temperature": temp, "smoke": smoke},
    )


def evacuation_triggered(node_id: str, start_nodes: list, paths: dict) -> None:
    log_event(
        EVACUATION_TRIGGERED,
        node_id=node_id,
        metadata={"start_nodes": start_nodes, "paths_found": {k: bool(v) for k, v in paths.items()}},
    )


def evacuation_complete(cloud_synced: bool, led_coverage_pct: float, led_results: dict) -> None:
    log_event(
        EVACUATION_COMPLETE,
        metadata={
            "cloud_synced": cloud_synced,
            "led_coverage_pct": led_coverage_pct,
            "led_results": led_results,
        },
    )


def alert_cleared(node_id: str, forced: bool = False) -> None:
    log_event(ALERT_CLEARED, node_id=node_id, metadata={"forced": forced})


def path_computed(start: str, path: list, algorithm: str, total_weight: float) -> None:
    log_event(
        PATH_COMPUTED,
        node_id=start,
        metadata={"path": path, "hops": len(path) - 1, "algorithm": algorithm, "total_weight": total_weight},
    )


def led_sent(node_id: str, ip: str, command_id: str, backup: bool = False) -> None:
    evt = LED_BACKUP_USED if backup else LED_SENT
    log_event(evt, node_id=node_id, metadata={"ip": ip, "command_id": command_id})


def led_failed(node_id: str, ip: str, reason: str) -> None:
    log_event(LED_FAILED, node_id=node_id, severity="WARNING", metadata={"ip": ip, "reason": reason})


def led_failed_critical(node_id: str, primary_ip: str, backup_ip: str, command_id: str) -> None:
    """Both primary AND backup LED unreachable — evacuation guidance lost on this node."""
    log_event(
        LED_FAILED_CRITICAL,
        node_id=node_id,
        severity="CRITICAL",
        metadata={
            "primary_ip": primary_ip,
            "backup_ip":  backup_ip,
            "command_id": command_id,
            "action_required": "Manual evacuation guidance needed for this node.",
        },
    )


def cloud_sync_ok(endpoint: str, attempt: int) -> None:
    log_event(CLOUD_SYNC_OK, metadata={"endpoint": endpoint, "attempt": attempt})


def cloud_sync_failed(endpoint: str, attempts: int) -> None:
    log_event(CLOUD_SYNC_FAILED, severity="WARNING", metadata={"endpoint": endpoint, "attempts": attempts})


def device_registered(device_id: str, node_id: str, device_type: str) -> None:
    log_event(DEVICE_REGISTERED, node_id=node_id, device_id=device_id,
              metadata={"type": device_type})


def device_offline(device_id: str, node_id: str, age_sec: float) -> None:
    log_event(DEVICE_OFFLINE, node_id=node_id, device_id=device_id, severity="WARNING",
              metadata={"offline_for_sec": round(age_sec, 1)})


def auth_failure(path: str, reason: str, remote_addr: str) -> None:
    log_event(AUTH_FAILURE, severity="WARNING",
              metadata={"path": path, "reason": reason, "remote_addr": remote_addr})


# ─────────────────────────────────────────────────────────────────────
# Query API (used by GET /events)
# ─────────────────────────────────────────────────────────────────────

def get_events(limit: int = 100, event_type: Optional[str] = None) -> list:
    """Return recent events from SQLite, newest first."""
    return persistence.load_events(limit=limit, event_type=event_type)
