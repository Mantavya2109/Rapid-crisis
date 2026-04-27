"""
device_registry.py
------------------
Central in-memory registry for all ESP32 devices in the building.

New in this version:
  - SQLite persistence via periodic flush (not write-through)
  - Offline detection emits event_log entries
  - restore_from_db() called at startup
"""

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import persistence
from config.settings import HEARTBEAT_TIMEOUT_SEC
from logger import get_logger

log = get_logger(__name__)

_lock = threading.RLock()
_registry: Dict[str, Dict[str, Any]] = {}

VALID_TYPES = {"SENSOR", "LED"}


# ─────────────────────────────────────────────────────────────────────
# Startup restore
# ─────────────────────────────────────────────────────────────────────

def restore_from_db() -> None:
    """Reload registered devices from SQLite after Pi restart."""
    rows = persistence.load_devices()
    with _lock:
        for row in rows:
            _registry[row["device_id"]] = {
                "deviceId":      row["device_id"],
                "buildingId":    row["building_id"],
                "nodeId":        row["node_id"],
                "type":          row["type"],
                "ip":            row.get("ip", ""),
                "status":        "OFFLINE",  # conservative on restore
                "last_seen":     row.get("last_seen", 0.0),
                "registered_at": row.get("registered_at", ""),
            }
    if rows:
        log.info("🔄 Restored %d device(s) from SQLite.", len(rows))


# ─────────────────────────────────────────────────────────────────────
# Persistence snapshot
# ─────────────────────────────────────────────────────────────────────

def _flush_devices(conn, now: float) -> None:
    """Write current registry to SQLite devices table."""
    with _lock:
        devices_copy = {k: dict(v) for k, v in _registry.items()}

    for dev in devices_copy.values():
        conn.execute(
            """INSERT INTO devices
               (device_id, building_id, node_id, type, ip, status, last_seen, registered_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(device_id) DO UPDATE SET
                   building_id=excluded.building_id,
                   node_id=excluded.node_id,
                   type=excluded.type,
                   ip=excluded.ip,
                   status=excluded.status,
                   last_seen=excluded.last_seen,
                   updated_at=excluded.updated_at""",
            (
                dev["deviceId"], dev["buildingId"], dev["nodeId"],
                dev["type"], dev.get("ip", ""), dev["status"],
                dev.get("last_seen", 0), dev.get("registered_at", ""), now,
            ),
        )


persistence.register_snapshot("devices", _flush_devices)


# ─────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────

def register_device(
    device_id:   str,
    building_id: str,
    node_id:     str,
    device_type: str,
    ip:          Optional[str] = None,
) -> Dict[str, Any]:
    if device_type not in VALID_TYPES:
        raise ValueError(f"Invalid device type '{device_type}'. Must be one of {VALID_TYPES}.")

    now     = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    with _lock:
        existing = _registry.get(device_id)
        if existing:
            existing.update({
                "buildingId": building_id,
                "nodeId":     node_id,
                "type":       device_type,
                "status":     "ONLINE",
                "last_seen":  now,
            })
            if ip:
                existing["ip"] = ip
            log.info("♻️  Device re-registered: %s (%s @ %s)", device_id, device_type, node_id)
            result = dict(existing)
        else:
            entry: Dict[str, Any] = {
                "deviceId":      device_id,
                "buildingId":    building_id,
                "nodeId":        node_id,
                "type":          device_type,
                "ip":            ip or "",
                "status":        "ONLINE",
                "last_seen":     now,
                "registered_at": now_iso,
            }
            _registry[device_id] = entry
            log.info("✅ Device registered: %s | type=%s | node=%s", device_id, device_type, node_id)
            result = dict(entry)

    persistence.flush_now()
    return result


# ─────────────────────────────────────────────────────────────────────
# Heartbeat
# ─────────────────────────────────────────────────────────────────────

def record_heartbeat(device_id: str, ip: Optional[str] = None) -> bool:
    with _lock:
        device = _registry.get(device_id)
        if device is None:
            log.warning("💔 Heartbeat from UNKNOWN device: %s", device_id)
            return False
        device["last_seen"] = time.time()
        device["status"]    = "ONLINE"
        if ip:
            device["ip"] = ip
        log.debug("💓 Heartbeat OK: %s", device_id)
        return True


def refresh_online_status() -> None:
    """Mark devices OFFLINE if no heartbeat within HEARTBEAT_TIMEOUT_SEC."""
    import event_log
    now = time.time()
    with _lock:
        for dev in _registry.values():
            age = now - dev.get("last_seen", 0)
            if age > HEARTBEAT_TIMEOUT_SEC and dev["status"] == "ONLINE":
                dev["status"] = "OFFLINE"
                log.warning("📵 Device OFFLINE (no heartbeat %.0fs): %s", age, dev["deviceId"])
                event_log.device_offline(dev["deviceId"], dev["nodeId"], age)


# ─────────────────────────────────────────────────────────────────────
# Lookups
# ─────────────────────────────────────────────────────────────────────

def get_device(device_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        d = _registry.get(device_id)
        return dict(d) if d else None


def get_all_devices() -> List[Dict[str, Any]]:
    with _lock:
        return [dict(d) for d in _registry.values()]


def get_devices_for_node(node_id: str) -> List[Dict[str, Any]]:
    with _lock:
        return [dict(d) for d in _registry.values() if d["nodeId"] == node_id]


def get_led_device_for_node(node_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        candidates = [d for d in _registry.values()
                      if d["nodeId"] == node_id and d["type"] == "LED"]
    if not candidates:
        return None
    online = [d for d in candidates if d["status"] == "ONLINE"]
    return dict(online[0]) if online else dict(candidates[0])


def get_sensor_device_for_node(node_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        for d in _registry.values():
            if d["nodeId"] == node_id and d["type"] == "SENSOR":
                return dict(d)
    return None


def get_node_for_device(device_id: str) -> Optional[str]:
    with _lock:
        d = _registry.get(device_id)
        return d["nodeId"] if d else None


def count() -> int:
    with _lock:
        return len(_registry)


def validate_registration_payload(data: Optional[Dict]) -> Optional[str]:
    if not data:
        return "Request body must be a valid JSON object."
    required = {"deviceId": str, "buildingId": str, "nodeId": str, "type": str}
    for field, ftype in required.items():
        if field not in data:
            return f"Missing required field: '{field}'"
        if not isinstance(data[field], ftype):
            return f"Field '{field}' must be a string."
    if data["type"] not in VALID_TYPES:
        return f"'type' must be one of {VALID_TYPES}. Got: '{data['type']}'"
    return None
