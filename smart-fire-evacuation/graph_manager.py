"""
graph_manager.py
----------------
Loads the building graph from JSON and exposes it for runtime use.

New in this version:
  - edge_weights section (base traversal costs per node)
  - backup_led field in led_devices (fallback LED node on failure)
  - get_hazard_weights() dynamically computes live weights from state_manager
"""

import json
import threading
from typing import Dict, List, Optional

from config.settings import GRAPH_CONFIG_PATH
from logger import get_logger

log = get_logger(__name__)

_lock          = threading.Lock()
_graph_data:   Dict = {}
_adjacency:    Dict[str, List[str]] = {}
_exits:        List[str] = []
_esp32_devices: Dict[str, Dict] = {}
_led_devices:  Dict[str, Dict] = {}          # { nodeId → {ip, backup, type} }
_directions:   Dict[str, str]  = {}          # { "FROM→TO" → direction }
_edge_weights: Dict[str, float] = {}         # { nodeId → base traversal cost }
_ai_hazard_weights: Dict[str, float] = {}    # { nodeId → AI predicted hazard weight }


# ─────────────────────────────────────────────────────────────────────
# Load / Save
# ─────────────────────────────────────────────────────────────────────

def load_graph(path: str = GRAPH_CONFIG_PATH) -> None:
    global _graph_data, _adjacency, _exits, _esp32_devices
    global _led_devices, _directions, _edge_weights

    try:
        with open(path, "r") as fh:
            data = json.load(fh)

        with _lock:
            _graph_data    = data
            _adjacency     = data.get("graph", {})
            _exits         = data.get("exits", [])
            _esp32_devices = data.get("esp32_devices", {})
            _led_devices   = data.get("led_devices", {})
            _directions    = data.get("directions", {})
            _edge_weights  = data.get("edge_weights", {})

        log.info(
            "Graph loaded — %d nodes, %d exits, %d LED devices, %d weighted edges",
            len(_adjacency), len(_exits), len(_led_devices), len(_edge_weights),
        )
    except FileNotFoundError:
        log.error("Graph config not found: %s", path)
        raise
    except json.JSONDecodeError as exc:
        log.error("Malformed graph JSON: %s", exc)
        raise


def save_graph(path: str = GRAPH_CONFIG_PATH) -> None:
    with _lock:
        data = dict(_graph_data)
        data["graph"] = dict(_adjacency)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
    log.info("Graph saved to %s", path)


# ─────────────────────────────────────────────────────────────────────
# Graph accessors
# ─────────────────────────────────────────────────────────────────────

def get_adjacency() -> Dict[str, List[str]]:
    with _lock:
        return dict(_adjacency)


def get_exits() -> List[str]:
    with _lock:
        return list(_exits)


def get_directions() -> Dict[str, str]:
    with _lock:
        return dict(_directions)


def get_direction(from_node: str, to_node: str) -> str:
    with _lock:
        return _directions.get(f"{from_node}→{to_node}", "STRAIGHT")


def get_edge_weights() -> Dict[str, float]:
    """Return static base traversal costs per node."""
    with _lock:
        return dict(_edge_weights)


# ─────────────────────────────────────────────────────────────────────
# Dynamic hazard weights (live, from state_manager)
# ─────────────────────────────────────────────────────────────────────

def get_hazard_weights() -> Dict[str, float]:
    """
    Compute live hazard weights by combining static edge costs with
    real-time sensor data and alert age (time decay).

    Imports state_manager at call time to avoid circular imports.
    """
    import state_manager
    from pathfinder import compute_node_weights

    with _lock:
        graph_snapshot = dict(_adjacency)
        base_weights   = dict(_edge_weights)

    sensor_data  = state_manager.get_all_sensor_data()
    alert_times  = state_manager.get_alert_times()

    weights = compute_node_weights(
        graph=graph_snapshot,
        sensor_data=sensor_data,
        alert_times=alert_times,
        edge_base_weights=base_weights,
    )
    
    # Merge AI predictive hazard weights
    with _lock:
        for node, ai_weight in _ai_hazard_weights.items():
            weights[node] = weights.get(node, 0.0) + ai_weight
            
    return weights


def update_ai_hazard_weights(weights: Dict[str, float]) -> None:
    """Update dynamic AI hazard weights received from the backend."""
    global _ai_hazard_weights
    with _lock:
        _ai_hazard_weights.clear()
        _ai_hazard_weights.update(weights)
    log.info("Updated AI predictive hazard weights for %d nodes.", len(weights))


# ─────────────────────────────────────────────────────────────────────
# LED device lookups
# ─────────────────────────────────────────────────────────────────────

def get_led_device_for_node(node: str) -> Optional[Dict]:
    """Return static LED device config for a node (includes backup field)."""
    with _lock:
        return _led_devices.get(node)


def get_backup_led_for_node(node: str) -> Optional[Dict]:
    """
    Return the backup LED device config for a node.
    Backup is identified by the 'backup' key in led_devices config,
    which stores the backup nodeId whose LED should be used as fallback.
    """
    with _lock:
        dev = _led_devices.get(node)
        if not dev:
            return None
        backup_node = dev.get("backup")
        if not backup_node:
            return None
        return _led_devices.get(backup_node)


# ─────────────────────────────────────────────────────────────────────
# ESP32 device lookups (legacy static config)
# ─────────────────────────────────────────────────────────────────────

def get_esp32_for_node(node: str) -> Optional[Dict]:
    with _lock:
        for meta in _esp32_devices.values():
            if meta.get("node") == node or meta.get("nodeId") == node:
                return meta
    return None


def get_esp32_by_id(device_id: str) -> Optional[Dict]:
    with _lock:
        return _esp32_devices.get(device_id)


# ─────────────────────────────────────────────────────────────────────
# Runtime graph updates
# ─────────────────────────────────────────────────────────────────────

def update_graph(new_adjacency: Dict[str, List[str]], new_exits: Optional[List[str]] = None) -> None:
    global _adjacency, _exits
    with _lock:
        _adjacency = new_adjacency
        if new_exits is not None:
            _exits = new_exits
    log.info("Graph updated in-memory (%d nodes, %d exits).", len(new_adjacency), len(_exits))


def load_from_cloud() -> bool:
    """
    Pull the building graph from the cloud backend.

    Converts the Firestore format (nodes + edges arrays) into the Pi's
    adjacency-list format.  Falls back to local JSON if the cloud is
    unreachable.

    Returns True if successfully loaded from cloud, False otherwise.
    """
    import requests
    from config.settings import CLOUD_BASE_URL, CLOUD_API_KEY, BUILDING_ID

    url = f"{CLOUD_BASE_URL}/building/{BUILDING_ID}"
    headers = {"Content-Type": "application/json"}
    if CLOUD_API_KEY:
        headers["Authorization"] = f"Bearer {CLOUD_API_KEY}"

    try:
        log.info("☁️  Fetching graph from cloud: %s", url)
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        data = resp.json()

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        if not nodes:
            log.warning("☁️  Cloud returned 0 nodes for '%s' — skipping.", BUILDING_ID)
            return False

        # Convert to adjacency format
        adjacency: Dict[str, List[str]] = {}
        for n in nodes:
            nid = str(n.get("id", ""))
            if nid:
                adjacency[nid] = []

        for e in edges:
            fr = str(e.get("from", ""))
            to = str(e.get("to", ""))
            if not fr or not to:
                continue
            if fr not in adjacency:
                adjacency[fr] = []
            if to not in adjacency:
                adjacency[to] = []
            if to not in adjacency[fr]:
                adjacency[fr].append(to)
            if fr not in adjacency[to]:
                adjacency[to].append(fr)

        # Identify exits
        exits = [
            str(n["id"]) for n in nodes
            if str(n.get("type", "")).lower() == "exit"
        ]

        global _adjacency, _exits, _graph_data
        with _lock:
            _adjacency = adjacency
            _exits = exits
            _graph_data = {"graph": adjacency, "exits": exits, "buildingId": BUILDING_ID}

        # Save to local JSON as cache for offline boots
        try:
            save_graph()
        except Exception:
            pass

        log.info(
            "✅ Cloud graph loaded — %d nodes, %d exits for building '%s'",
            len(adjacency), len(exits), BUILDING_ID,
        )
        return True

    except requests.exceptions.ConnectionError:
        log.warning("☁️  Cloud unreachable — will use local graph.")
        return False
    except Exception as exc:
        log.warning("☁️  Cloud graph fetch failed: %s — will use local graph.", exc)
        return False
