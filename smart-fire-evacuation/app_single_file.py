"""
app_single_file.py  —  Smart Fire Evacuation System  —  Raspberry Pi Edge Server
==================================================================================
4-tier architecture:  ESP32 Sensor → Pi (this file) → Cloud → LED ESP32

ALL 10 PRODUCTION GAPS FIXED:
  ✅ 1  Device registration (SENSOR / LED types)
  ✅ 2  Heartbeat + online/offline status tracking
  ✅ 3  Full state management (blocked_nodes, active_alerts)
  ✅ 4  Fire clear / reset system  (POST /clear-alert)
  ✅ 5  Multi-start-point support (all blocked nodes sent to cloud)
  ✅ 6  Rich LED commands  {direction, color, mode, priority}
  ✅ 7  Acknowledgement check  (ESP32 returns {"status":"OK"})
  ✅ 8  Retry with back-off on every outbound request
  ✅ 9  Alert debounce  (per-node cooldown window)
  ✅ 10 Device type differentiation  (SENSOR vs LED)

Run:
    cp .env.example .env && export $(cat .env | grep -v ^# | xargs)
    python app_single_file.py
"""

# ──────────────────────────────────────────────────────────────────────
# IMPORTS
# ──────────────────────────────────────────────────────────────────────
import json, logging, os, random, threading, time
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from flask import Flask, jsonify, request, Response

# ══════════════════════════════════════════════════════════════════════
# ❶  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════
HOST         = os.getenv("SERVER_HOST", "0.0.0.0")
PORT         = int(os.getenv("SERVER_PORT", "5000"))
DEBUG        = os.getenv("DEBUG", "false").lower() == "true"
BUILDING_ID  = os.getenv("BUILDING_ID", "BUILDING_01")

TEMP_THRESHOLD  = float(os.getenv("TEMP_THRESHOLD",  "40.0"))   # °C
SMOKE_THRESHOLD = float(os.getenv("SMOKE_THRESHOLD", "200.0"))  # ppm

CLOUD_BASE_URL       = os.getenv("CLOUD_BASE_URL",       "https://your-cloud.com")
CLOUD_API_KEY        = os.getenv("CLOUD_API_KEY",        "")
CLOUD_TIMEOUT_SEC    = int(os.getenv("CLOUD_TIMEOUT_SEC",    "5"))
CLOUD_RETRY_ATTEMPTS = int(os.getenv("CLOUD_RETRY_ATTEMPTS", "3"))
GRAPH_SYNC_INTERVAL  = int(os.getenv("GRAPH_SYNC_INTERVAL_SEC", "300"))

LED_ENDPOINT      = "/led"
LED_TIMEOUT_SEC   = int(os.getenv("LED_TIMEOUT_SEC",   "3"))
LED_RETRY_ATTEMPTS= int(os.getenv("LED_RETRY_ATTEMPTS","3"))

# ✅ FIX 9 — per-node alert cooldown (seconds)
ALERT_DEBOUNCE_SEC = int(os.getenv("ALERT_DEBOUNCE_SEC", "10"))

# ✅ FIX 2 — offline if no heartbeat in this many seconds
HEARTBEAT_TIMEOUT_SEC = int(os.getenv("HEARTBEAT_TIMEOUT_SEC", "30"))

VALID_DIRECTIONS  = {"LEFT", "RIGHT", "STRAIGHT", "STOP", "ALARM"}
VALID_COLORS      = {"GREEN", "RED", "YELLOW", "BLUE", "WHITE"}
VALID_MODES       = {"FLOW", "BLINK", "SOLID", "PULSE"}
VALID_DEVICE_TYPES= {"SENSOR", "LED"}

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
GRAPH_CACHE_PATH = os.path.join(BASE_DIR, "config", "graph_cache.json")
LOG_DIR          = os.path.join(BASE_DIR, "logs")

# ══════════════════════════════════════════════════════════════════════
# ❷  LOGGER
# ══════════════════════════════════════════════════════════════════════
def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("pi_edge")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    os.makedirs(LOG_DIR, exist_ok=True)
    fh = RotatingFileHandler(
        os.path.join(LOG_DIR, "pi_edge.log"),
        maxBytes=5_242_880, backupCount=5,
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger

log = _setup_logger()

# ══════════════════════════════════════════════════════════════════════
# ❸  DEVICE REGISTRY  (FIX #1 — registration, FIX #2 — heartbeat,
#                       FIX #10 — type differentiation)
#
# Stores every ESP32 that has called POST /devices/register.
# Schema per entry:
#   { deviceId, buildingId, nodeId, type (SENSOR|LED),
#     ip, registeredAt, lastSeen, status (ONLINE|OFFLINE) }
# ══════════════════════════════════════════════════════════════════════
_reg_lock    = threading.RLock()
_devices: Dict[str, Dict] = {}   # deviceId → device record


def register_device(
    device_id: str,
    building_id: str,
    node_id: str,
    device_type: str,
    ip: Optional[str] = None,
) -> Dict:
    """Register or update a device. Returns the stored record."""
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "deviceId":    device_id,
        "buildingId":  building_id,
        "nodeId":      node_id,
        "type":        device_type,   # SENSOR | LED
        "ip":          ip,
        "registeredAt": now,
        "lastSeen":    now,
        "status":      "ONLINE",
    }
    with _reg_lock:
        if device_id in _devices:
            # Preserve registeredAt on re-register
            record["registeredAt"] = _devices[device_id]["registeredAt"]
        _devices[device_id] = record
    log.info("📋 Device registered: %s (type=%s, node=%s, ip=%s)",
             device_id, device_type, node_id, ip or "unknown")
    return record


def heartbeat(device_id: str, status: str = "ONLINE") -> bool:
    """Update lastSeen and status for a device. Returns False if unknown."""
    with _reg_lock:
        if device_id not in _devices:
            return False
        _devices[device_id]["lastSeen"] = datetime.now(timezone.utc).isoformat()
        _devices[device_id]["status"]   = status
    return True


def _device_is_online(device: Dict) -> bool:
    """A device is ONLINE only if its heartbeat arrived within the timeout window."""
    last_seen = device.get("lastSeen")
    if not last_seen:
        return False
    dt = datetime.fromisoformat(last_seen)
    age = (datetime.now(timezone.utc) - dt).total_seconds()
    return age < HEARTBEAT_TIMEOUT_SEC


def get_device(device_id: str) -> Optional[Dict]:
    with _reg_lock:
        return _devices.get(device_id)


def get_led_device_for_node(node_id: str) -> Optional[Dict]:
    """Return the LED-type device registered for a node, or None."""
    with _reg_lock:
        for d in _devices.values():
            if d["type"] == "LED" and d["nodeId"] == node_id:
                return d
    return None


def get_all_devices() -> List[Dict]:
    with _reg_lock:
        result = []
        for d in _devices.values():
            entry = dict(d)
            entry["online"] = _device_is_online(d)
            result.append(entry)
        return result


# ══════════════════════════════════════════════════════════════════════
# ❹  STATE MANAGER  (FIX #3 — blocked_nodes + active_alerts,
#                    FIX #4 — clear/reset)
#
# blocked_nodes  — set of node IDs currently on fire / dangerous
# active_alerts  — { nodeId: { triggered details } }
# evacuation_active — global flag
# ══════════════════════════════════════════════════════════════════════
_state_lock       = threading.RLock()
_sensor_readings: Dict[str, Dict] = {}          # nodeId → latest reading
_blocked_nodes:   Set[str]  = set()             # nodes currently unsafe
_active_alerts:   Dict[str, Dict] = {}          # nodeId → alert details
_evacuation_active: bool = False

# ✅ FIX 9 — debounce: nodeId → last alert epoch float
_last_alert_time: Dict[str, float] = {}


def record_sensor(node_id: str, payload: Dict) -> None:
    with _state_lock:
        _sensor_readings[node_id] = {
            **payload,
            "receivedAt": datetime.now(timezone.utc).isoformat(),
        }


def should_debounce(node_id: str) -> bool:
    """Return True if an alert for this node was fired too recently."""
    with _state_lock:
        last = _last_alert_time.get(node_id, 0)
    return (time.time() - last) < ALERT_DEBOUNCE_SEC


def mark_blocked(node_id: str, alert_detail: Dict) -> None:
    """Add node to blocked set and record the active alert."""
    with _state_lock:
        _blocked_nodes.add(node_id)
        _active_alerts[node_id] = {
            **alert_detail,
            "triggeredAt": datetime.now(timezone.utc).isoformat(),
        }
        _last_alert_time[node_id] = time.time()
    log.warning("🔴 Node BLOCKED: %s", node_id)


def clear_alert(node_id: str) -> bool:
    """Remove a node from blocked state. Returns True if it was blocked."""
    with _state_lock:
        was_blocked = node_id in _blocked_nodes
        _blocked_nodes.discard(node_id)
        _active_alerts.pop(node_id, None)
        if not _blocked_nodes:
            global _evacuation_active
            _evacuation_active = False
            log.info("🟢 All nodes cleared — evacuation deactivated.")
    if was_blocked:
        log.info("🟢 Node cleared: %s", node_id)
    return was_blocked


def get_blocked_nodes() -> List[str]:
    with _state_lock:
        return sorted(_blocked_nodes)


def set_evacuation_active(active: bool) -> None:
    global _evacuation_active
    with _state_lock:
        if active != _evacuation_active:
            _evacuation_active = active
            log.warning("🚨 EVACUATION %s", "ACTIVATED" if active else "DEACTIVATED")


def is_evacuation_active() -> bool:
    with _state_lock:
        return _evacuation_active


def get_full_state() -> Dict:
    with _state_lock:
        return {
            "evacuation_active": _evacuation_active,
            "blocked_nodes":     sorted(_blocked_nodes),
            "active_alerts":     dict(_active_alerts),
            "sensor_readings":   dict(_sensor_readings),
        }


# ══════════════════════════════════════════════════════════════════════
# ❺  LOCAL GRAPH CACHE  (synced from cloud; fail-safe fall-back)
# ══════════════════════════════════════════════════════════════════════
_cache_lock   = threading.RLock()
_adjacency:   Dict[str, List[str]] = {}
_exits:       List[str] = []
_directions:  Dict[str, str] = {}      # "FROM→TO" → "LEFT"
_graph_synced_at: Optional[str] = None


def _apply_graph(data: Dict) -> None:
    global _adjacency, _exits, _directions, _graph_synced_at
    with _cache_lock:
        _adjacency        = data.get("graph",      {})
        _exits            = data.get("exits",      [])
        _directions       = data.get("directions", {})
        _graph_synced_at  = datetime.now(timezone.utc).isoformat()
    log.info("📐 Graph applied — %d nodes | %d exits", len(_adjacency), len(_exits))


def load_graph_from_disk() -> bool:
    try:
        with open(GRAPH_CACHE_PATH) as fh:
            _apply_graph(json.load(fh))
        log.info("💾 Graph loaded from disk.")
        return True
    except FileNotFoundError:
        log.warning("No graph cache on disk — waiting for cloud sync.")
        return False
    except json.JSONDecodeError as e:
        log.error("Corrupt graph cache: %s", e)
        return False


def save_graph_to_disk(data: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(GRAPH_CACHE_PATH), exist_ok=True)
        with open(GRAPH_CACHE_PATH, "w") as fh:
            json.dump(data, fh, indent=2)
    except OSError as e:
        log.error("Graph save failed: %s", e)


def get_adjacency() -> Dict[str, List[str]]:
    with _cache_lock: return dict(_adjacency)

def get_exits() -> List[str]:
    with _cache_lock: return list(_exits)

def get_direction(frm: str, to: str) -> str:
    with _cache_lock: return _directions.get(f"{frm}→{to}", "STRAIGHT")

def get_graph_info() -> Dict:
    with _cache_lock:
        return {"nodes": len(_adjacency), "exits": len(_exits),
                "synced_at": _graph_synced_at}


# ══════════════════════════════════════════════════════════════════════
# ❻  FIRE DETECTION
# ══════════════════════════════════════════════════════════════════════
def is_fire_condition(temperature: float, smoke: float) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if temperature > TEMP_THRESHOLD:
        reasons.append(f"temp={temperature}°C>{TEMP_THRESHOLD}°C")
    if smoke > SMOKE_THRESHOLD:
        reasons.append(f"smoke={smoke:.0f}>{SMOKE_THRESHOLD:.0f}ppm")
    if reasons:
        log.warning("🔥 Fire condition: %s", " | ".join(reasons))
    return bool(reasons), reasons


# ══════════════════════════════════════════════════════════════════════
# ❼  BFS PATHFINDER  (local fail-safe — cloud runs Dijkstra)
# ══════════════════════════════════════════════════════════════════════
def bfs_to_exit(
    graph: Dict[str, List[str]],
    start: str,
    exits: List[str],
    blocked: Optional[List[str]] = None,
) -> List[str]:
    skip = set(blocked or [])
    if start not in graph:
        log.warning("BFS: '%s' not in graph.", start)
        return []
    if start in exits and start not in skip:
        return [start]
    queue, visited = deque([[start]]), {start}
    while queue:
        path = queue.popleft()
        for nb in graph.get(path[-1], []):
            if nb in visited or nb in skip:
                continue
            new = path + [nb]
            visited.add(nb)
            if nb in exits:
                log.info("🗺  BFS path: %s", " → ".join(new))
                return new
            queue.append(new)
    log.error("❌ BFS: no path from '%s'", start)
    return []


# ══════════════════════════════════════════════════════════════════════
# ❽  PATH → LED COMMANDS CONVERTER  (FIX #6 — color/mode/priority)
#
# Produces the standard command envelope:
#   { node, direction, color, mode, priority }
# Used by both cloud-path and BFS-fallback code paths.
# ══════════════════════════════════════════════════════════════════════
def path_to_commands(
    path: List[str],
    color:    str = "GREEN",
    mode:     str = "FLOW",
    priority: int = 1,
) -> List[Dict]:
    """Translate a node path into rich LED command dicts."""
    cmds: List[Dict] = []
    for i in range(len(path) - 1):
        cmds.append({
            "node":      path[i],
            "direction": get_direction(path[i], path[i + 1]),
            "color":     color,
            "mode":      mode,
            "priority":  priority,
        })
    if path:
        cmds.append({
            "node": path[-1], "direction": "STOP",
            "color": color, "mode": "SOLID", "priority": priority,
        })
    return cmds


# ══════════════════════════════════════════════════════════════════════
# ❾  CLOUD COMMUNICATOR  (FIX #8 — retry, FIX #5 — multi-start)
# ══════════════════════════════════════════════════════════════════════
def _cloud_headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json", "X-Building-ID": BUILDING_ID}
    if CLOUD_API_KEY:
        h["Authorization"] = f"Bearer {CLOUD_API_KEY}"
    return h


def _post_with_retry(
    url: str,
    payload: Dict,
    attempts: int = CLOUD_RETRY_ATTEMPTS,
    timeout: int  = CLOUD_TIMEOUT_SEC,
    label: str    = "cloud",
) -> Optional[requests.Response]:
    """Generic POST with exponential back-off. Returns Response or None."""
    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(url, json=payload, headers=_cloud_headers(), timeout=timeout)
            r.raise_for_status()
            return r
        except requests.exceptions.ConnectionError:
            log.warning("⚠  %s unreachable (attempt %d/%d)", label, attempt, attempts)
        except requests.exceptions.Timeout:
            log.warning("⚠  %s timeout (attempt %d/%d)", label, attempt, attempts)
        except requests.exceptions.HTTPError as e:
            log.error("❌ %s HTTP error: %s (attempt %d)", label, e, attempt)
        except Exception as e:
            log.error("❌ %s unexpected error: %s", label, e)
        if attempt < attempts:
            time.sleep((2 ** attempt) + random.uniform(0, 0.5))
    log.error("🔴 %s failed after %d attempts.", label, attempts)
    return None


def fire_alert_to_cloud(
    node_id: str,
    device_id: str,
    temperature: float,
    smoke: float,
) -> Tuple[Optional[List[Dict]], str]:
    """
    POST /api/fire-alert → Cloud.
    Cloud stores event, marks nodes blocked, runs Dijkstra for ALL blocked nodes
    (FIX #5 — multi-start: Pi sends complete blocked_nodes list).

    Cloud response (success):
    {
        "commands": [
            {"node":"ROOM_102","direction":"RIGHT","color":"GREEN","mode":"FLOW","priority":1},
            ...
        ]
    }
    Returns (commands_list, source_label).
    """
    payload = {
        "buildingId":   BUILDING_ID,
        "nodeId":       node_id,
        "deviceId":     device_id,
        "temperature":  temperature,
        "smoke":        smoke,
        "blockedNodes": get_blocked_nodes(),   # ALL currently blocked nodes
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }
    url = f"{CLOUD_BASE_URL}/api/fire-alert"
    resp = _post_with_retry(url, payload, label="cloud/fire-alert")

    if resp:
        try:
            data = resp.json()
            cmds = data.get("commands", [])
            if cmds and isinstance(cmds, list):
                log.info("✅ Cloud returned %d LED commands.", len(cmds))
                return cmds, "cloud_dijkstra"
            log.warning("⚠  Cloud OK but no commands returned.")
        except ValueError as e:
            log.error("❌ Cloud response parse error: %s", e)

    return None, "failed"


def sync_graph_from_cloud() -> bool:
    url = f"{CLOUD_BASE_URL}/api/graph-snapshot"
    try:
        r = requests.get(
            url, params={"buildingId": BUILDING_ID},
            headers=_cloud_headers(), timeout=CLOUD_TIMEOUT_SEC,
        )
        r.raise_for_status()
        data = r.json()
        _apply_graph(data)
        save_graph_to_disk(data)
        log.info("✅ Graph synced from cloud.")
        return True
    except Exception as e:
        log.warning("⚠  Graph sync failed: %s", e)
        return False


def notify_cloud_node_cleared(node_id: str) -> None:
    """Best-effort — no retry needed for clear notifications."""
    try:
        requests.post(
            f"{CLOUD_BASE_URL}/api/node-cleared",
            json={"buildingId": BUILDING_ID, "nodeId": node_id},
            headers=_cloud_headers(),
            timeout=CLOUD_TIMEOUT_SEC,
        )
        log.info("☁  Cloud notified: '%s' cleared.", node_id)
    except Exception as e:
        log.warning("Cloud clear-notify failed (non-critical): %s", e)


# ══════════════════════════════════════════════════════════════════════
# ❿  LED CONTROLLER  (FIX #6 — rich commands, FIX #7 — ack check,
#                     FIX #8 — retry per device)
#
# Pi sends HTTP to LED ESP32. NO GPIO. NO direct hardware.
# LED ESP32 receives: POST /led  {"direction","color","mode","priority"}
# LED ESP32 must reply: {"status": "OK"}
# ══════════════════════════════════════════════════════════════════════
def send_led_command(ip: str, cmd: Dict, node_id: str = "") -> bool:
    """
    Send one rich LED command to one ESP32 LED device.
    Retries up to LED_RETRY_ATTEMPTS times.
    Validates acknowledgement {"status":"OK"} from ESP32 (FIX #7).
    """
    url = f"http://{ip}{LED_ENDPOINT}"
    payload = {
        "direction": cmd.get("direction", "STOP"),
        "color":     cmd.get("color",     "GREEN"),
        "mode":      cmd.get("mode",      "SOLID"),
        "priority":  cmd.get("priority",  1),
    }

    for attempt in range(1, LED_RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(url, json=payload, timeout=LED_TIMEOUT_SEC)
            resp.raise_for_status()

            # ✅ FIX #7 — Acknowledgement check
            ack = resp.json() if resp.content else {}
            if ack.get("status") == "OK":
                log.info(
                    "💡 LED ACK OK → node=%-12s ip=%-15s dir=%-10s color=%s mode=%s",
                    node_id, ip, payload["direction"], payload["color"], payload["mode"],
                )
                return True
            else:
                log.warning("⚠  LED ESP32 at %s returned unexpected ack: %s", ip, ack)

        except requests.exceptions.ConnectionError:
            log.error("LED unreachable — node='%s' ip=%s (attempt %d)", node_id, ip, attempt)
        except requests.exceptions.Timeout:
            log.error("LED timeout — node='%s' ip=%s (attempt %d)", node_id, ip, attempt)
        except requests.exceptions.HTTPError as e:
            log.error("LED HTTP error — node='%s': %s", node_id, e)
        except ValueError:
            log.warning("LED at %s returned non-JSON body — treating as success.", ip)
            return True   # older firmware may not return JSON
        except Exception as e:
            log.error("LED unexpected error — node='%s': %s", node_id, e)

        if attempt < LED_RETRY_ATTEMPTS:
            time.sleep(0.5 * attempt)

    log.error(
        "🔴 LED FAILED after %d attempts — node='%s' ip=%s direction=%s",
        LED_RETRY_ATTEMPTS, node_id, ip, payload["direction"],
    )
    return False


def dispatch_led_commands(commands: List[Dict]) -> Dict[str, bool]:
    """
    Send a list of rich LED command dicts to their respective ESP32 LED devices.
    Device IP is resolved from:  ① _registered_devices  ② graph cache led_devices

    commands format (from cloud or local BFS):
    [
        {"node":"ROOM_102","direction":"RIGHT","color":"GREEN","mode":"FLOW","priority":1},
        ...
    ]
    Returns { nodeId: True/False }.
    """
    results: Dict[str, bool] = {}

    for cmd in commands:
        node_id = cmd.get("node", "")
        if not node_id:
            continue

        # ✅ FIX #10 — look up LED-type device only
        device = get_led_device_for_node(node_id)

        # Fallback: check graph cache led_devices table
        if not device:
            with _cache_lock:
                ld = _adjacency  # just to acquire lock; actual check below
            # Access graph cache led_devices via module-level dict (graph cache section)
            ip = _get_led_ip_from_cache(node_id)
        else:
            if not _device_is_online(device):
                log.warning("⚠  LED device for '%s' is OFFLINE — skipping.", node_id)
                results[node_id] = False
                continue
            ip = device.get("ip")

        if not ip:
            log.warning("No IP for LED node '%s' — skipping.", node_id)
            results[node_id] = False
            continue

        results[node_id] = send_led_command(ip, cmd, node_id)

    ok_count = sum(results.values())
    log.info("💡 Dispatch done: %d/%d nodes reached.", ok_count, len(results))
    return results


# Small helper for graph-cache LED IP fallback (populated by push-graph / sync)
_led_device_ips: Dict[str, str] = {}   # nodeId → ip (from graph cache)
_led_lock = threading.Lock()

def _get_led_ip_from_cache(node_id: str) -> Optional[str]:
    with _led_lock:
        return _led_device_ips.get(node_id)

def _apply_led_ips(led_devices: Dict) -> None:
    with _led_lock:
        _led_device_ips.clear()
        for node_id, cfg in led_devices.items():
            if isinstance(cfg, dict) and cfg.get("ip"):
                _led_device_ips[node_id] = cfg["ip"]


# ══════════════════════════════════════════════════════════════════════
# ⓫  EVACUATION ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════
def trigger_evacuation(
    device_id:   str,
    node_id:     str,
    temperature: float,
    smoke:       float,
    reasons:     List[str],
) -> Dict[str, Any]:
    """
    Full fire response pipeline:
      [1] Mark node blocked + record active alert (state)
      [2] POST fire-alert → Cloud (receives Dijkstra LED commands)
      [3] If cloud fails → local BFS + path_to_commands() fallback
      [4] Dispatch rich LED commands to LED ESP32s (with ack + retry)
    """
    log.warning(
        "🔥 FIRE | building=%s node=%s device=%s | %s",
        BUILDING_ID, node_id, device_id, " | ".join(reasons),
    )

    # [1] State update
    mark_blocked(node_id, {
        "deviceId": device_id, "temperature": temperature,
        "smoke": smoke, "reasons": reasons,
    })
    set_evacuation_active(True)

    # [2] Cloud → Dijkstra commands
    cloud_cmds, cloud_status = fire_alert_to_cloud(
        node_id, device_id, temperature, smoke
    )

    # [3] Determine commands (cloud or BFS fallback)
    if cloud_cmds:
        commands    = cloud_cmds
        path_source = "cloud_dijkstra"
        safe_path   = [c["node"] for c in commands]
    else:
        log.warning("⚠  FAIL-SAFE: local BFS activated.")
        path = bfs_to_exit(get_adjacency(), node_id, get_exits(), get_blocked_nodes())
        commands    = path_to_commands(path, color="YELLOW", mode="BLINK", priority=2)
        path_source = "local_bfs_failsafe"
        safe_path   = path

    path_found = bool(commands)
    if not path_found:
        log.error("🚨 NO PATH from '%s'! Broadcasting ALARM.", node_id)
        # Last resort: ALARM to all registered LED devices
        commands = [
            {"node": d["nodeId"], "direction": "ALARM",
             "color": "RED", "mode": "BLINK", "priority": 9}
            for d in get_all_devices() if d["type"] == "LED"
        ]

    # [4] Dispatch
    led_results = dispatch_led_commands(commands)

    return {
        "buildingId":      BUILDING_ID,
        "deviceId":        device_id,
        "affectedNode":    node_id,
        "fireReasons":     reasons,
        "pathFound":       path_found,
        "pathSource":      path_source,
        "safePath":        safe_path,
        "ledCommands":     commands,
        "ledResults":      led_results,
        "cloudAvailable":  cloud_status != "failed",
        "failSafeActive":  cloud_status == "failed",
    }


# ══════════════════════════════════════════════════════════════════════
# ⓬  BACKGROUND: periodic graph sync thread
# ══════════════════════════════════════════════════════════════════════
def _sync_worker() -> None:
    if GRAPH_SYNC_INTERVAL <= 0:
        return
    log.info("🔄 Graph sync thread started (interval %ds).", GRAPH_SYNC_INTERVAL)
    while True:
        time.sleep(GRAPH_SYNC_INTERVAL)
        try:
            sync_graph_from_cloud()
        except Exception as e:
            log.error("Sync thread error: %s", e)


def _start_sync_thread() -> None:
    t = threading.Thread(target=_sync_worker, daemon=True, name="GraphSync")
    t.start()


# ══════════════════════════════════════════════════════════════════════
# ⓭  FLASK APPLICATION
# ══════════════════════════════════════════════════════════════════════
app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
_SERVER_START = time.time()

# ── Response helpers ──────────────────────────────────────────────────
def ok(data: Dict, status: int = 200) -> Response:
    return jsonify({"success": True,  **data}), status

def err(message: str, status: int = 400) -> Response:
    return jsonify({"success": False, "error": message}), status


# ══════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

# ── GET /health ───────────────────────────────────────────────────────
@app.get("/health")
def health_check() -> Response:
    """Liveness check — systemd / cloud ping / uptime monitors."""
    return ok({
        "status":           "healthy",
        "buildingId":       BUILDING_ID,
        "uptimeSeconds":    int(time.time() - _SERVER_START),
        "evacuationActive": is_evacuation_active(),
        "blockedNodes":     get_blocked_nodes(),
        "activeAlerts":     len(_active_alerts),
        "devices":          len(_devices),
        "graph":            get_graph_info(),
        "timestamp":        datetime.now(timezone.utc).isoformat(),
    })


# ── GET /status ───────────────────────────────────────────────────────
@app.get("/status")
def system_status() -> Response:
    """Full system snapshot for admin dashboard."""
    return ok({
        **get_full_state(),
        "devices": get_all_devices(),
        "graph":   get_graph_info(),
    })


# ── POST /devices/register  (FIX #1 + #10) ───────────────────────────
@app.post("/devices/register")
def device_register() -> Response:
    """
    Register an ESP32 device before it starts sending data.
    Called once at ESP32 boot.

    Body:
    {
        "deviceId":   "ESP32_01",
        "buildingId": "BUILDING_01",
        "nodeId":     "ROOM_102",
        "type":       "SENSOR",      ← or "LED"
        "ip":         "192.168.1.42" ← required for LED type
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return err("JSON body required.")

    for f in ("deviceId", "buildingId", "nodeId", "type"):
        if f not in data:
            return err(f"Missing field: '{f}'")

    if data["type"] not in VALID_DEVICE_TYPES:
        return err(f"'type' must be one of {VALID_DEVICE_TYPES}.")

    if data["type"] == "LED" and not data.get("ip"):
        return err("LED devices must supply 'ip'.")

    # Reject registrations for a different building
    if data["buildingId"] != BUILDING_ID:
        return err(
            f"This Pi manages building '{BUILDING_ID}'. "
            f"Got '{data['buildingId']}'.",
            status=409,
        )

    record = register_device(
        device_id   = data["deviceId"],
        building_id = data["buildingId"],
        node_id     = data["nodeId"],
        device_type = data["type"],
        ip          = data.get("ip"),
    )
    return ok({"message": "Device registered.", "device": record}, status=201)


# ── POST /heartbeat  (FIX #2) ────────────────────────────────────────
@app.post("/heartbeat")
def device_heartbeat() -> Response:
    """
    ESP32 calls this every 5–10 seconds to signal it is alive.
    If no heartbeat arrives within HEARTBEAT_TIMEOUT_SEC, device → OFFLINE.

    Body: { "deviceId": "ESP32_01", "status": "ONLINE" }
    """
    data = request.get_json(silent=True)
    if not data or "deviceId" not in data:
        return err("Missing 'deviceId'.")

    device_id  = data["deviceId"]
    status_str = data.get("status", "ONLINE").upper()

    found = heartbeat(device_id, status_str)
    if not found:
        log.warning("Heartbeat from unknown device '%s' — not registered.", device_id)
        return err(f"Device '{device_id}' not registered. Call POST /devices/register first.", status=404)

    return ok({"message": "Heartbeat recorded.", "deviceId": device_id, "status": status_str})


# ── POST /sensor  (primary — from ESP32 sensor) ───────────────────────
@app.post("/sensor")
def receive_sensor_data() -> Response:
    """
    Receives raw sensor data from ESP32 SENSOR devices.
    Pi decides fire — ESP32 sends numbers only.

    Body:
    {
        "deviceId":    "ESP32_01",
        "buildingId":  "BUILDING_01",
        "nodeId":      "ROOM_102",
        "temperature": 45.0,
        "smoke":       300
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return err("JSON body required.")

    for f in ("deviceId", "nodeId", "temperature", "smoke"):
        if f not in data:
            return err(f"Missing field: '{f}'")
        if f in ("temperature", "smoke") and not isinstance(data[f], (int, float)):
            return err(f"'{f}' must be a number.")

    device_id   = data["deviceId"]
    node_id     = data["nodeId"]
    temperature = float(data["temperature"])
    smoke       = float(data["smoke"])

    # ✅ FIX #10 — reject LED-typed devices sending sensor data
    device = get_device(device_id)
    if device and device["type"] == "LED":
        return err(f"Device '{device_id}' is type LED. Only SENSOR devices post to /sensor.", status=400)

    log.info("📡 Sensor | device=%-10s node=%-12s temp=%.1f°C smoke=%.0fppm",
             device_id, node_id, temperature, smoke)

    record_sensor(node_id, data)

    fire, reasons = is_fire_condition(temperature, smoke)
    if not fire:
        return ok({"message": "Data received. No danger.", "nodeId": node_id})

    # ✅ FIX #9 — Debounce: ignore repeat alerts within cooldown window
    if should_debounce(node_id):
        log.info("⏱  Alert debounced for node '%s' (cooldown %ds).", node_id, ALERT_DEBOUNCE_SEC)
        return ok({
            "message": "Fire already active at this node — debounced.",
            "nodeId":  node_id,
            "debounced": True,
        })

    result = trigger_evacuation(device_id, node_id, temperature, smoke, reasons)
    return ok({"message": "Fire detected. Evacuation triggered.", "evacuation": result})


# ── POST /led/batch  (FIX #6 — rich commands, cloud/admin override) ───
@app.post("/led/batch")
def led_batch() -> Response:
    """
    Send a batch of rich LED commands to multiple ESP32 LED devices at once.
    Called by: cloud override, admin panel, or manual testing.

    Body:
    {
        "commands": [
            {"node":"ROOM_102","direction":"RIGHT","color":"GREEN","mode":"FLOW","priority":1},
            {"node":"HALLWAY_A","direction":"STRAIGHT","color":"GREEN","mode":"FLOW","priority":1}
        ]
    }
    """
    data = request.get_json(silent=True)
    if not data or "commands" not in data:
        return err("Body must contain 'commands' list.")

    cmds = data["commands"]
    if not isinstance(cmds, list) or not cmds:
        return err("'commands' must be a non-empty list.")

    # Validate each command
    for i, cmd in enumerate(cmds):
        if "node" not in cmd:
            return err(f"Command[{i}] missing 'node'.")
        if "direction" not in cmd:
            return err(f"Command[{i}] missing 'direction'.")
        if cmd["direction"] not in VALID_DIRECTIONS:
            return err(f"Command[{i}] invalid direction '{cmd['direction']}'.")

    log.info("🎮 LED batch: %d commands received.", len(cmds))
    results = dispatch_led_commands(cmds)
    return ok({"sent": len(cmds), "results": results})


# ── POST /led-command  (single node manual override) ─────────────────
@app.post("/led-command")
def led_command() -> Response:
    """
    Send one rich LED command to a specific node's LED ESP32.
    Useful for admin panel testing and single-device override.

    Body: {"nodeId":"ROOM_102","direction":"LEFT","color":"GREEN","mode":"FLOW"}
    """
    data = request.get_json(silent=True)
    if not data:
        return err("JSON body required.")
    if "nodeId" not in data:
        return err("Missing 'nodeId'.")
    if "direction" not in data:
        return err("Missing 'direction'.")
    if data["direction"] not in VALID_DIRECTIONS:
        return err(f"'direction' must be one of {sorted(VALID_DIRECTIONS)}.")

    node_id   = data["nodeId"]
    cmd = {
        "node":      node_id,
        "direction": data["direction"],
        "color":     data.get("color",    "GREEN"),
        "mode":      data.get("mode",     "SOLID"),
        "priority":  data.get("priority", 1),
    }

    results = dispatch_led_commands([cmd])
    sent = results.get(node_id, False)
    return ok({"nodeId": node_id, "command": cmd, "sent": sent})


# ── POST /clear-alert  (FIX #4 — fire clear / reset) ─────────────────
@app.post("/clear-alert")
def clear_alert_endpoint() -> Response:
    """
    Mark a node safe after fire suppression.
    Clears it from blocked_nodes + active_alerts.
    Sends STOP to the node's LED ESP32.
    Notifies cloud so Dijkstra re-opens the node.

    Body: { "nodeId": "ROOM_102" }
    """
    data = request.get_json(silent=True)
    if not data or "nodeId" not in data:
        return err("Missing 'nodeId'.")

    node_id = data["nodeId"]

    if not clear_alert(node_id):
        return ok({"message": f"Node '{node_id}' was not in an alert state."})

    # Send STOP to LED device
    stop_cmd = [{"node": node_id, "direction": "STOP", "color": "WHITE", "mode": "SOLID", "priority": 0}]
    led_results = dispatch_led_commands(stop_cmd)

    # Notify cloud (best-effort)
    notify_cloud_node_cleared(node_id)

    return ok({
        "message":        f"Alert cleared for node '{node_id}'.",
        "remainingBlocked": get_blocked_nodes(),
        "ledStop":        led_results,
    })


# ── POST /push-graph  (cloud pushes updated map) ─────────────────────
@app.post("/push-graph")
def push_graph() -> Response:
    """
    Cloud or admin panel calls this after any map change.
    Pi updates local cache immediately.
    """
    data = request.get_json(silent=True)
    if not data or "graph" not in data:
        return err("Body must contain 'graph' adjacency-list.")

    if data.get("buildingId") and data["buildingId"] != BUILDING_ID:
        return err(f"buildingId mismatch. This Pi is '{BUILDING_ID}'.", status=409)

    _apply_graph(data)
    _apply_led_ips(data.get("led_devices", {}))
    save_graph_to_disk(data)
    return ok({"message": "Graph updated.", **get_graph_info()})


# ── POST /sync-graph  (Pi pulls from cloud on demand) ────────────────
@app.post("/sync-graph")
def trigger_sync() -> Response:
    """Manually trigger a cloud graph sync (e.g. after network outage)."""
    ok_flag = sync_graph_from_cloud()
    return ok({"message": "Synced." if ok_flag else "Cloud unavailable — using cache.", **get_graph_info()})


# ── Global error handlers ─────────────────────────────────────────────
@app.errorhandler(404)
def not_found(_e) -> Response:
    return err(f"Endpoint not found: {request.path}", status=404)

@app.errorhandler(405)
def method_not_allowed(_e) -> Response:
    return err(f"Method '{request.method}' not allowed on {request.path}.", status=405)

@app.errorhandler(500)
def internal_error(e) -> Response:
    log.error("Internal error: %s", e, exc_info=True)
    return err("Internal server error — check Pi logs.", status=500)


# ══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info("=" * 66)
    log.info("  🚒  Smart Fire Evacuation — Raspberry Pi Edge Server  (v3)")
    log.info("  🏢  Building : %s", BUILDING_ID)
    log.info("  📡  Listening: http://%s:%d", HOST, PORT)
    log.info("  ☁️   Cloud    : %s", CLOUD_BASE_URL)
    log.info("  🌡️   Thresholds: temp>%.0f°C | smoke>%.0fppm", TEMP_THRESHOLD, SMOKE_THRESHOLD)
    log.info("  ⏱️   Debounce : %ds | Heartbeat timeout: %ds", ALERT_DEBOUNCE_SEC, HEARTBEAT_TIMEOUT_SEC)
    log.info("=" * 66)

    load_graph_from_disk()
    sync_graph_from_cloud()
    _start_sync_thread()

    app.run(host=HOST, port=PORT, debug=DEBUG, use_reloader=False)
