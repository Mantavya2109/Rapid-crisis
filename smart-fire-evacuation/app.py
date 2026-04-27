"""
app.py
------
Smart Fire Evacuation System — Flask REST API Server (Raspberry Pi Edge)

Security:
  - API key authentication via X-API-Key header (write routes)
  - Timestamp-based replay protection via X-Timestamp header
  - Per-IP rate limiting via flask-limiter

New endpoints:
  GET  /events              Structured event log query
  (plus all previous endpoints)
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import device_registry
import event_log
import graph_manager
import state_manager
import persistence
from config.settings import (
    HOST, PORT, DEBUG,
    TEMP_THRESHOLD, SMOKE_THRESHOLD,
    BUILDING_ID, API_SECRET_KEY, REPLAY_WINDOW_SEC,
    RATE_LIMIT_SENSOR, RATE_LIMIT_EVACUATE, RATE_LIMIT_DEFAULT,
    RATE_LIMIT_STORAGE_URI, HEARTBEAT_TIMEOUT_SEC, ESP32_FAILSAFE_BLINK_MS,
)
from evacuation_engine import trigger_evacuation
from logger import get_logger
from pathfinder import bfs_to_exit
from prometheus_client import make_wsgi_app
from werkzeug.middleware.dispatcher import DispatcherMiddleware
import auth_manager
import metrics
from pydantic import BaseModel, Field, ValidationError

# Pydantic Schemas for validation
class SensorPayload(BaseModel):
    deviceId: str
    buildingId: Optional[str] = None
    nodeId: Optional[str] = None
    temperature: float
    smoke: float
    status: Optional[str] = None

class MapZonesPayload(BaseModel):
    auto: Optional[List[str]] = None
    # For dictionary parsing we will fall back to manual validation mapping
    
class EvacuatePayload(BaseModel):
    startNodes: List[str] = Field(min_length=1)
    blockedNodes: Optional[List[str]] = []
    buildingId: Optional[str] = None

# ─────────────────────────────────────────────────────────────────────
# App init
# ─────────────────────────────────────────────────────────────────────

log = get_logger("fire_evacuation")
app = Flask(__name__)
# Add Prometheus WSGI middleware to route /metrics
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
    '/metrics': make_wsgi_app()
})

app.config["JSON_SORT_KEYS"] = False
_SERVER_START = time.time()

# Rate limiter
# Storage URI is configurable:
#   memory://                            → default (resets on restart)
#   sqlite:////path/data/rate_limits.db  → Pi-native persistence (no Redis needed)
#   redis://localhost:6379               → production persistence
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[RATE_LIMIT_DEFAULT],
    storage_uri=RATE_LIMIT_STORAGE_URI,  # From env: RATELIMIT_STORAGE_URI
)


# ─────────────────────────────────────────────────────────────────────
# Middleware (Auth & Metrics)
# ─────────────────────────────────────────────────────────────────────

# Routes that do NOT require authentication (monitoring / device keepalive)
_AUTH_EXEMPT = {"/health", "/status", "/events", "/devices", "/metrics"}

@app.before_request
def track_request_start():
    request._start_time = time.time()

@app.after_request
def track_request_latency(response):
    if hasattr(request, '_start_time'):
        latency = time.time() - request._start_time
        metrics.http_request_latency_seconds.labels(
            method=request.method, endpoint=request.endpoint or request.path
        ).observe(latency)
    
    metrics.http_requests_total.labels(
        method=request.method, endpoint=request.endpoint or request.path
    ).inc()
    return response


@app.before_request
def check_auth():
    """
    JWT + timestamp replay protection for write routes.
    """
    # Exempt read-only and monitoring routes
    if request.method == "GET":
        return
    if request.path in _AUTH_EXEMPT or request.path.startswith("/devices/") and request.method == "GET":
        return
    if request.path == "/devices/register":
        return # Skip auth for register allowing generation of JWT token

    # ── JWT Check ─────────────────────────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        event_log.auth_failure(request.path, "missing_bearer_token", request.remote_addr)
        return err("Unauthorized: missing or invalid Authorization Bearer token.", status=401)
        
    token = auth_header.split(" ")[1]
    decoded = auth_manager.decode_token(token)
    
    if not decoded:
        event_log.auth_failure(request.path, "invalid_jwt", request.remote_addr)
        return err("Unauthorized: invalid or expired token.", status=401)

    # ── Replay protection (timestamp window) ──────────────────────────
    from config.settings import REQUIRE_TIMESTAMP
    ts_header = request.headers.get("X-Timestamp", "")
    
    if not ts_header:
        if REQUIRE_TIMESTAMP:
            return err("Unauthorized: X-Timestamp header is required.", status=401)
        return # allowed if disable strict timestamp verification

    try:
        req_ts = float(ts_header)
        age    = abs(time.time() - req_ts)
        if age > REPLAY_WINDOW_SEC:
            event_log.auth_failure(
                request.path,
                f"timestamp too old ({age:.0f}s > {REPLAY_WINDOW_SEC}s)",
                request.remote_addr,
            )
            return err(
                f"Unauthorized: request timestamp too old ({age:.0f}s). "
                f"Max window: {REPLAY_WINDOW_SEC}s.",
                status=401,
            )
    except ValueError:
        return err("Unauthorized: X-Timestamp must be a numeric unix timestamp.", status=401)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def ok(data: Any, status: int = 200) -> Response:
    return jsonify({"success": True, **data}), status


def err(message: str, status: int = 400) -> Response:
    return jsonify({"success": False, "error": message}), status


def is_dangerous(data: Dict) -> bool:
    """Check temp AND smoke thresholds (both pathways matter)."""
    return (
        data.get("temperature", 0) > TEMP_THRESHOLD
        or data.get("smoke", 0) > SMOKE_THRESHOLD
        or data.get("status") == "FIRE"
    )


def validate_sensor_payload(data: Optional[Dict]) -> Optional[str]:
    if not data:
        return "Request body must be a valid JSON object."
    for field, ftype in {"deviceId": str, "temperature": (int, float), "smoke": (int, float)}.items():
        if field not in data:
            return f"Missing required field: '{field}'"
        if not isinstance(data[field], ftype):
            return f"Field '{field}' must be numeric." if ftype != str else f"Field '{field}' must be a string."
    if "status" in data and data["status"] not in {"OK", "WARNING", "FIRE"}:
        return f"'status' must be OK | WARNING | FIRE. Got: '{data['status']}'"
    return None


# ─────────────────────────────────────────────────────────────────────
# Routes — System
# ─────────────────────────────────────────────────────────────────────

@app.get("/health")
@limiter.exempt
def health_check() -> Response:
    uptime = int(time.time() - _SERVER_START)
    return ok({
        "status":             "healthy",
        "uptime_seconds":     uptime,
        "timestamp":          datetime.now(timezone.utc).isoformat(),
        "evacuation_active":  state_manager.is_evacuation_active(),
        "registered_devices": device_registry.count(),
        "building_id":        BUILDING_ID,
    })


@app.get("/status")
@limiter.exempt
def system_status() -> Response:
    device_registry.refresh_online_status()
    state = state_manager.get_full_state()
    state["devices"] = device_registry.get_all_devices()
    return ok(state)


@app.get("/events")
@limiter.exempt
def get_events() -> Response:
    """
    GET /events?limit=100&type=FIRE_DETECTED
    Return structured event log from SQLite.
    """
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except ValueError:
        return err("'limit' must be an integer.", status=400)

    event_type = request.args.get("type")
    events     = event_log.get_events(limit=limit, event_type=event_type or None)
    return ok({"events": events, "count": len(events)})


# ─────────────────────────────────────────────────────────────────────
# Routes — Device Management
# ─────────────────────────────────────────────────────────────────────

@app.post("/devices/register")
@limiter.limit(RATE_LIMIT_DEFAULT)
def register_device() -> Response:
    data = request.get_json(silent=True)
    error = device_registry.validate_registration_payload(data)
    if error:
        return err(error, status=400)

    ip = data.get("ip") or request.remote_addr or ""
    try:
        entry = device_registry.register_device(
            device_id   = data["deviceId"],
            building_id = data["buildingId"],
            node_id     = data["nodeId"],
            device_type = data["type"],
            ip          = ip,
        )
        
        # Issue JWT Access Token for this device
        token = auth_manager.generate_device_token(data["deviceId"], role="device")
        entry["token"] = token
        
    except ValueError as ve:
        return err(str(ve), status=400)

    event_log.device_registered(entry["deviceId"], entry["nodeId"], entry["type"])
    return ok({"message": "Device registered.", "device": entry}, status=201)


@app.get("/devices")
@limiter.exempt
def list_devices() -> Response:
    device_registry.refresh_online_status()
    devices = device_registry.get_all_devices()
    return ok({"devices": devices, "count": len(devices)})


@app.get("/devices/<string:device_id>")
@limiter.exempt
def get_device(device_id: str) -> Response:
    device_registry.refresh_online_status()
    device = device_registry.get_device(device_id)
    if device is None:
        return err(f"Device '{device_id}' not found.", status=404)
    return ok({"device": device})


# ─────────────────────────────────────────────────────────────────────
# Routes — Heartbeat
# ─────────────────────────────────────────────────────────────────────

@app.post("/heartbeat")
@limiter.limit(RATE_LIMIT_DEFAULT)
def heartbeat() -> Response:
    """
    POST /heartbeat
    Body: { "deviceId": "ESP32_A1", "ip": "192.168.1.50" }

    Response includes:
      - failsafe_blink_ms : ESP32 must enter RED-blink fail-safe if no heartbeat
                            received within this many milliseconds. Firmware should
                            use this value from the last ACK to set its watchdog timer.
      - heartbeat_interval_ms : recommended interval for the next heartbeat
      - pi_alive          : always true (the ESP32 infers false on timeout/no response)
    """
    data = request.get_json(silent=True)
    if not data or "deviceId" not in data:
        return err("Body must contain 'deviceId'.", status=400)

    device_id = data["deviceId"]
    ip = data.get("ip") or request.remote_addr or ""

    found = device_registry.record_heartbeat(device_id, ip=ip)
    if not found:
        return err(f"Unknown device '{device_id}'. Register first via POST /devices/register.", status=404)

    return ok({
        "message":              "Heartbeat received.",
        "deviceId":             device_id,
        "timestamp":            datetime.now(timezone.utc).isoformat(),
        "pi_alive":             True,
        "failsafe_blink_ms":    ESP32_FAILSAFE_BLINK_MS,
        "heartbeat_interval_ms": max(1000, (HEARTBEAT_TIMEOUT_SEC * 1000) // 3),
        "evacuation_active":    state_manager.is_evacuation_active(),
    })


# ─────────────────────────────────────────────────────────────────────
# Routes — Sensor Data
# ─────────────────────────────────────────────────────────────────────

@app.post("/sensor")
@limiter.limit(RATE_LIMIT_SENSOR)
def receive_sensor_data() -> Response:
    raw_data = request.get_json(silent=True)
    
    try:
        sensor_data = SensorPayload(**raw_data)
        data = sensor_data.model_dump()
    except ValidationError as e:
        return err(f"Invalid sensor payload format: {e.errors()}", status=400)
    except Exception:
        return err("Request body must be a valid JSON object.", status=400)

    device_id   = data["deviceId"]
    building_id = data.get("buildingId", BUILDING_ID)
    node        = (
        data.get("nodeId")
        or device_registry.get_node_for_device(device_id)
        or device_id
    )
    data["nodeId"]     = node
    data["buildingId"] = building_id

    log.info("📡 Sensor: dev=%s node=%s temp=%.1f smoke=%s status=%s",
             device_id, node, data["temperature"], data["smoke"], data.get("status", "OK"))

    state_manager.update_sensor_data(node, data)

    if not is_dangerous(data):
        return ok({"message": "Data received. No action required.", "node": node})

    if state_manager.is_alert_debounced(node):
        return ok({
            "message":   f"Alert debounced for '{node}' — evacuation already active.",
            "node":      node,
            "debounced": True,
        })

    log.warning("🔥 DANGER: node=%s temp=%.1f smoke=%s", node, data["temperature"], data["smoke"])
    result = trigger_evacuation(data)

    return ok({
        "message":   "Fire detected. Evacuation triggered.",
        "node":      node,
        "evacuation": result,
    })


# ─────────────────────────────────────────────────────────────────────
# Routes — Evacuation Control
# ─────────────────────────────────────────────────────────────────────

@app.post("/evacuate")
@limiter.limit(RATE_LIMIT_EVACUATE)
def manual_evacuate() -> Response:
    raw_data = request.get_json(silent=True)
    try:
        payload = EvacuatePayload(**raw_data)
        data = payload.model_dump()
    except ValidationError as e:
        return err(f"Invalid evacuation payload: {e.errors()}", status=400)
    except Exception:
        return err("Request body must be a valid JSON object.", status=400)

    start_nodes = data["startNodes"]

    for n in data.get("blockedNodes", []):
        state_manager.mark_unsafe(n)

    building_id = data.get("buildingId", BUILDING_ID)
    state_manager.set_evacuation_active(True)

    synthetic = {
        "deviceId":    "MANUAL_TRIGGER",
        "buildingId":  building_id,
        "nodeId":      start_nodes[0],
        "temperature": 0,
        "smoke":       0,
        "status":      "FIRE",
    }
    result = trigger_evacuation(synthetic, start_nodes=start_nodes)
    return ok({"message": "Manual evacuation triggered.", "start_nodes": start_nodes, "evacuation": result})


@app.post("/clear-alert")
@limiter.limit(RATE_LIMIT_DEFAULT)
def clear_alert() -> Response:
    """
    POST /clear-alert
    Body: { "nodeId": "ROOM_102", "force": false }

    FIRE-level alerts require "force": true as safety interlock.
    """
    data = request.get_json(silent=True)
    if not data or "nodeId" not in data:
        return err("Body must contain 'nodeId'.", status=400)

    node  = data["nodeId"]
    force = data.get("force", False)

    severity = state_manager.get_alert_severity(node)
    cleared  = state_manager.clear_alert(node, force=force)

    if not cleared:
        return err(
            f"Cannot clear FIRE-level alert for '{node}' without 'force': true. "
            f"Confirm physical clearance first.",
            status=409,
        )

    event_log.alert_cleared(node, forced=force)

    remaining = state_manager.get_unsafe_nodes()
    if not remaining:
        state_manager.set_evacuation_active(False)

    return ok({
        "message":           f"Alert cleared for '{node}'.",
        "node":              node,
        "was_severity":      severity,
        "forced":            force,
        "remaining_unsafe":  remaining,
        "evacuation_active": state_manager.is_evacuation_active(),
    })


@app.post("/alerts/clear-all")
@limiter.limit(RATE_LIMIT_DEFAULT)
def clear_all_alerts() -> Response:
    """
    Mass-clear all alerts across all nodes.
    Used by operator to confirm physical clearance.
    Automatically triggers the recovery manager cooldown.
    """
    import recovery_manager
    nodes = state_manager.get_unsafe_nodes()
    
    for n in nodes:
        state_manager.clear_alert(n, force=True)
        event_log.alert_cleared(n, forced=True)
        
    state_manager.set_evacuation_active(False)
    recovery_manager.notify_all_clear()
    
    return ok({
        "message": "All alerts cleared.",
        "nodes_cleared": len(nodes),
    })


# ─────────────────────────────────────────────────────────────────────
# Route — System State Dashboard  (Upgrade #1/#2 — 6 states + memory)
# ─────────────────────────────────────────────────────────────────────

@app.get("/system/state")
@limiter.exempt
def get_system_state() -> Response:
    """
    GET /system/state
    Full snapshot of every node's current 6-state classification plus
    rate-of-rise trend metrics.  The primary dashboard endpoint.

    Response:
    {
      "nodes": {
        "ROOM_101": {
          "state":              "DANGER",
          "stale_sec":          2.1,
          "smoke_rise_rate":    18.5,    ← units/min (positive = rising)
          "temp_rise_rate":     3.2,     ← °C/min
          "consecutive_danger": 1,
          "state_changes":      3
        }, ...
      },
      "danger_nodes":    ["ROOM_101"],
      "evacuation_active": true,
      "mqtt_connected":    true,
      "event_filter_stats": { "ROOM_101": {"last_state":"DANGER","seconds_since":5.0} }
    }
    """
    import sensor_processor
    import mqtt_listener
    from event_filter import EventFilter

    node_states = sensor_processor.get_all_node_states()
    danger_nodes = [n for n, d in node_states.items()
                    if d.get("state") in ("DANGER", "CRITICAL_FIRE")]

    return ok({
        "nodes":              node_states,
        "danger_nodes":       danger_nodes,
        "evacuation_active":  state_manager.is_evacuation_active(),
        "mqtt_connected":     mqtt_listener.is_connected(),
    })


# ─────────────────────────────────────────────────────────────────────
# Routes — System Mode & Recovery (Upgrades #2, #5)
# ─────────────────────────────────────────────────────────────────────

@app.get("/system/mode")
@limiter.exempt
def get_system_mode() -> Response:
    import system_mode
    return ok(system_mode.get_status())


@app.post("/system/mode")
@limiter.limit(RATE_LIMIT_DEFAULT)
def set_system_mode() -> Response:
    import system_mode
    data = request.get_json(silent=True) or {}
    
    if "mode" in data:
        mode = data["mode"]
        if system_mode.set_manual_mode(mode, reason=data.get("reason", "API")):
            return ok(system_mode.get_status())
        return err(f"Invalid mode '{mode}'", status=400)
    elif "release" in data and data["release"]:
        system_mode.release_manual_override()
        return ok(system_mode.get_status())
    
    return err("Body must contain 'mode' (string) or 'release' (true).", status=400)


@app.get("/system/recovery")
@limiter.exempt
def get_recovery_status() -> Response:
    import recovery_manager
    return ok(recovery_manager.get_status())


@app.post("/system/recover")
@limiter.limit(RATE_LIMIT_DEFAULT)
def force_recovery() -> Response:
    """Force an immediate recovery bypass of the cooldown."""
    import recovery_manager
    data = request.get_json(silent=True) or {}
    
    if "hold" in data:
        if data["hold"]:
            recovery_manager.set_manual_hold()
        else:
            recovery_manager.release_manual_hold()
        return ok(recovery_manager.get_status())
    
    success = recovery_manager.force_immediate_recovery(reason="API forced")
    if not success:
        return err("System is on manual hold — cannot force recovery.", status=409)
    
    return ok(recovery_manager.get_status())


@app.get("/system/queue")
@limiter.exempt
def get_queue_stats() -> Response:
    import processing_queue
    queue = processing_queue.get_instance()
    if queue:
        return ok(queue.get_stats())
    return err("Queue not initialized.", status=503)


# ─────────────────────────────────────────────────────────────────────
# Route — Dynamic Zone Mapping  (Upgrade #7 — frontend-driven mapping)
# ─────────────────────────────────────────────────────────────────────

@app.post("/zones/map")
@limiter.limit(RATE_LIMIT_DEFAULT)
def map_zones_from_frontend() -> Response:
    """
    POST /zones/map
    Accept a building-layout-driven zone mapping from the frontend UI.
    Replaces the previously computed zone layout at runtime.

    Body format (explicit):
    {
      "ROOM_101":  {"start": 0,  "end": 9,  "type": "room"},
      "HALLWAY_A": {"start": 10, "end": 19, "type": "hallway"},
      "EXIT_MAIN": {"start": 20, "end": 24, "type": "exit"}
    }

    Body format (auto-partition):
    { "auto": ["ROOM_101", "HALLWAY_A", "EXIT_MAIN"] }

    The new layout is:
      - Applied immediately to the LED driver (live effect)
      - Persisted to zone_config.json for next Pi reboot
    """
    import zone_manager
    from config.settings import LED_COUNT

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return err("Body must be a JSON object.", status=400)

    try:
        zones = zone_manager.set_from_api_payload(data)
    except ValueError as exc:
        return err(str(exc), status=400)

    if not zones:
        return err("No valid zone entries found in request body.", status=400)

    zone_manager.update_led_driver()   # push to LED driver (hot-reload)
    zone_manager.save_to_config()      # persist for next reboot

    return ok({
        "message":    "Zone mapping updated from frontend.",
        "zones":      {k: {"start": s, "end": e, "type": zone_manager.get_node_type(k)}
                       for k, (s, e) in zones.items()},
        "zone_count": len(zones),
        "led_total":  LED_COUNT,
    })


# ─────────────────────────────────────────────────────────────────────
# Routes — LED Zones  (WS2812B strip, controlled directly by the Pi)
# ─────────────────────────────────────────────────────────────────────

@app.get("/zones")
@limiter.exempt
def get_zones() -> Response:
    """
    GET /zones
    Return the current LED zone layout and status for every building node.

    Response:
    {
      "zones": {
        "ROOM_101": {
          "start_led": 0, "end_led": 9, "led_count": 10,
          "status": "NORMAL",
          "color_rgb": [0, 200, 0]
        }, ...
      },
      "led_count_total": 60,
      "hw_available": true
    }
    """
    import led_driver
    from config.settings import LED_COUNT
    zones = led_driver.get_zones()
    return ok({
        "zones":           zones,
        "zone_count":      len(zones),
        "led_count_total": LED_COUNT,
        "hw_available":    led_driver.is_hw_available(),
    })


@app.post("/zones/configure")
@limiter.limit(RATE_LIMIT_DEFAULT)
def configure_zones() -> Response:
    """
    POST /zones/configure
    Reconfigure the LED zone layout at runtime (no restart needed).

    Body (two accepted formats):
      { "ROOM_101": [0, 9], "HALLWAY_A": [10, 19] }
      { "ROOM_101": {"start": 0, "end": 9}, ... }

    Or auto-partition from an ordered list of node IDs:
      { "auto": ["ROOM_101", "HALLWAY_A", "ROOM_102"] }
    """
    import led_driver
    import zone_manager
    from config.settings import LED_COUNT

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return err("Body must be a JSON object.", status=400)

    if "auto" in data:
        node_ids = data["auto"]
        if not isinstance(node_ids, list) or not node_ids:
            return err("'auto' must be a non-empty list of node IDs.", status=400)
        zones = zone_manager.auto_partition(node_ids)
    else:
        zones = zone_manager.set_zones(data)
        if not zones:
            return err("No valid zone entries found in request body.", status=400)

    zone_manager.apply_to_driver()
    zone_manager.save_to_config()   # Persist for next Pi reboot

    return ok({
        "message":    "LED zones updated.",
        "zones":      {k: {"start": s, "end": e} for k, (s, e) in zones.items()},
        "zone_count": len(zones),
        "led_total":  LED_COUNT,
    })


@app.get("/zones/<string:node>")
@limiter.exempt
def get_zone(node: str) -> Response:
    """GET /zones/<nodeId> — status and LED range for a single node."""
    import led_driver
    zones = led_driver.get_zones()
    if node not in zones:
        return err(f"Zone for node '{node}' not found.", status=404)
    return ok({"node": node, **zones[node]})


@app.post("/zones/<string:node>/clear")
@limiter.limit(RATE_LIMIT_DEFAULT)
def clear_zone(node: str) -> Response:
    """
    POST /zones/<nodeId>/clear
    Reset a node's zone to NORMAL and clear its active alert.
    Use after physical inspection confirms the area is safe.
    Body: { "force": false }  — force=true required to clear FIRE-level alerts.
    """
    import led_driver

    data  = request.get_json(silent=True) or {}
    force = data.get("force", False)

    severity = state_manager.get_alert_severity(node)
    cleared  = state_manager.clear_alert(node, force=force)

    if not cleared:
        return err(
            f"Cannot clear FIRE-level alert on '{node}' without 'force': true.",
            status=409,
        )

    led_driver.set_zone_status(node, "NORMAL")
    event_log.alert_cleared(node, forced=force)

    remaining = state_manager.get_unsafe_nodes()
    if not remaining:
        state_manager.set_evacuation_active(False)

    return ok({
        "message":           f"Zone '{node}' cleared → NORMAL.",
        "node":              node,
        "was_severity":      severity,
        "forced":            force,
        "remaining_unsafe":  remaining,
        "evacuation_active": state_manager.is_evacuation_active(),
    })


# ─────────────────────────────────────────────────────────────────────
# Routes — MQTT Status
# ─────────────────────────────────────────────────────────────────────

@app.get("/mqtt/status")
@limiter.exempt
def mqtt_status() -> Response:
    """
    GET /mqtt/status
    Report the Pi's MQTT broker connection state and subscriptions.
    """
    import mqtt_listener
    from config.settings import MQTT_BROKER_HOST, MQTT_BROKER_PORT, MQTT_TOPIC_SENSOR, MQTT_TOPIC_HEARTBEAT

    return ok({
        "connected":       mqtt_listener.is_connected(),
        "broker_host":     MQTT_BROKER_HOST,
        "broker_port":     MQTT_BROKER_PORT,
        "subscriptions":   [MQTT_TOPIC_SENSOR, MQTT_TOPIC_HEARTBEAT],
        "client_id":       "rpi-edge-controller",
    })


# ─────────────────────────────────────────────────────────────────────
# Routes — Per-node Sensor Data
# ─────────────────────────────────────────────────────────────────────

@app.get("/sensor/<string:node_id>")
@limiter.exempt
def get_node_sensor_data(node_id: str) -> Response:
    """
    GET /sensor/<nodeId>
    Return the latest sensor snapshot for a specific node.
    """
    data = state_manager.get_sensor_data(node_id)
    if data is None:
        return err(f"No sensor data found for node '{node_id}'.", status=404)
    return ok({"node": node_id, "data": data})


@app.get("/sensors")
@limiter.exempt
def get_all_sensor_data() -> Response:
    """
    GET /sensors
    Return the latest sensor snapshot for every node the Pi has received data from.
    """
    all_data = state_manager.get_all_sensor_data()
    return ok({"sensor_data": all_data, "node_count": len(all_data)})


# ─────────────────────────────────────────────────────────────────────
# Routes — Path Query
# ─────────────────────────────────────────────────────────────────────

@app.get("/path/<string:node>")
@limiter.exempt
def get_evacuation_path(node: str) -> Response:
    graph = graph_manager.get_adjacency()
    exits = graph_manager.get_exits()

    if node not in graph:
        return err(f"Node '{node}' not in graph.", status=404)

    extra = request.args.get("blocked", "")
    manual_blocked = [n.strip() for n in extra.split(",") if n.strip()]
    blocked = list(set(state_manager.get_unsafe_nodes() + manual_blocked))

    path = bfs_to_exit(graph, node, exits, blocked)
    if not path:
        return ok({"message": "No safe path — all routes blocked.", "start": node, "path": [], "blocked": blocked})

    return ok({"start": node, "path": path, "hops": len(path) - 1, "blocked": blocked})


@app.post("/system/ota")
@limiter.limit(RATE_LIMIT_DEFAULT)
def trigger_ota_update() -> Response:
    """
    POST /system/ota
    Cryptographically verified firmware OTA updates with downward-rollback protection.
    """
    import jwt
    import os
    from config.settings import OTA_PUBLIC_KEY
    CURRENT_VERSION = int(os.getenv("FIRMWARE_VERSION", 1))

    signature = request.headers.get("X-Signature")
    if not signature:
        return err("Missing 'X-Signature' Header required for OTA payload validation.", status=401)
        
    request_data = request.get_json(silent=True) or {}
    
    # ── Authenticate signature over payload ────────────────────────
    try:
        # Assuming the signature is a JWT encoding the exact firmware context
        verified_payload = jwt.decode(signature, OTA_PUBLIC_KEY, algorithms=["RS256"])
        if verified_payload.get("url") != request_data.get("firmware_url"):
            return err("Signature does not match requested Firmware URL payload.", status=401)
    except Exception as e:
        log.warning(f"OTA Signature validation failed: {str(e)}")
        return err("Invalid cryptographic signature.", status=401)

    target = request_data.get("target", "all") # 'pi', 'esp32', or 'all'
    new_version = int(request_data.get("version", 0))

    if new_version < CURRENT_VERSION:
        log.error(f"🛑 REJECTING OTA DOWNGRADE. Current: {CURRENT_VERSION}, Requested: {new_version}")
        return err("Downgrade attack detected. Firmware versions must be incremental.", status=403)
    
    if not request_data.get("firmware_url"):
        return err("'firmware_url' is required.", status=400)
    
    log.info(f"Received Validated OTA update command. Target: {target}, URL: {request_data.get('firmware_url')}, V: {new_version}")
    event_log.system_event("OTA_UPDATE_STARTED", f"Target: {target}, Version: {new_version}")
    
    # Send MQTT broadcast to ESP32s if target is esp32 or all
    if target in ["esp32", "all"]:
        import mqtt_listener
        ota_payload = f'{{"url":"{request_data.get("firmware_url")}","version":{new_version},"signature":"{signature}"}}'
        mqtt_listener._client.publish("system/ota/update", ota_payload, qos=1)
        
    return ok({"message": "OTA update initiated securely", "target": target, "version_started": new_version})


# ─────────────────────────────────────────────────────────────────────
# Graph
# ─────────────────────────────────────────────────────────────────────

@app.post("/graph/update")
@limiter.limit(RATE_LIMIT_DEFAULT)
def update_graph() -> Response:
    data = request.get_json(silent=True)
    if not data or "graph" not in data or not isinstance(data["graph"], dict):
        return err("Body must contain a 'graph' adjacency object.")
    graph_manager.update_graph(data["graph"])
    return ok({"message": "Graph updated.", "nodes": list(data["graph"].keys())})


# ─────────────────────────────────────────────────────────────────────
# Routes — Legacy
# ─────────────────────────────────────────────────────────────────────

@app.post("/node/clear/<string:node>")
@limiter.limit(RATE_LIMIT_DEFAULT)
def clear_node_legacy(node: str) -> Response:
    """DEPRECATED — use POST /clear-alert"""
    state_manager.clear_alert(node, force=False)
    remaining = state_manager.get_unsafe_nodes()
    if not remaining:
        state_manager.set_evacuation_active(False)
    return ok({"message": f"Node '{node}' marked safe.", "remaining_unsafe": remaining})


# ─────────────────────────────────────────────────────────────────────
# Error handlers
# ─────────────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e) -> Response:
    return err(f"Endpoint not found: {request.path}", status=404)


@app.errorhandler(405)
def method_not_allowed(e) -> Response:
    return err(f"Method '{request.method}' not allowed on {request.path}.", status=405)


@app.errorhandler(429)
def rate_limited(e) -> Response:
    event_log.log_event(event_log.RATE_LIMITED, metadata={"path": request.path, "ip": request.remote_addr})
    return err("Too many requests. Please slow down.", status=429)


@app.errorhandler(500)
def internal_error(e) -> Response:
    log.error("Internal server error: %s", e, exc_info=True)
    return err("Internal server error. Check server logs.", status=500)


# ─────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 60)
    log.info("  Smart Fire Evacuation System — Edge Server")
    log.info("  Building: %s | Auth: %s", BUILDING_ID, "ENABLED" if API_SECRET_KEY else "DISABLED")
    log.info("  Starting on http://%s:%d", HOST, PORT)
    log.info("  Thresholds — Temp: %.1f°C | Smoke: %.0f ppm", TEMP_THRESHOLD, SMOKE_THRESHOLD)
    log.info("=" * 60)

    graph_manager.load_graph()
    state_manager.restore_from_db()
    device_registry.restore_from_db()

    app.run(host=HOST, port=PORT, debug=DEBUG, use_reloader=False)
