"""
zone_manager.py  (v2 — dynamic mapping)
-----------------------------------------
Maps building graph nodes to WS2812B LED strip zones.

UPGRADE:  Accepts dynamic mapping pushed from the frontend/backend at runtime.
          POST /zones/map now replaces the old static auto-partition for all
          production deployments while keeping auto-partition as the fallback.

Configuration sources (priority order):
  1. POST /zones/map (from frontend building-layout UI)       ← production
  2. config/zone_config.json (persisted from last runtime)    ← auto-reload
  3. auto_partition() from graph node list                     ← fallback
"""

import json
import os
from typing import Dict, Optional, Tuple

import led_driver
from config.settings import LED_COUNT, ZONE_CONFIG_PATH
from logger import get_logger

log = get_logger(__name__)

# { nodeId → (start_led, end_led) }   — both inclusive, 0-indexed
_zones: Dict[str, Tuple[int, int]] = {}

# { nodeId → "room" | "hallway" | "exit" }
# Exit nodes are used by multi-zone guidance to direct evacuation paths
_node_types: Dict[str, str] = {}


# ─────────────────────────────────────────────────────────────────────
# Zone configuration
# ─────────────────────────────────────────────────────────────────────

def auto_partition(node_ids: list) -> Dict[str, Tuple[int, int]]:
    """
    Divide LED_COUNT LEDs equally among the given node IDs.
    Order determines physical position on the strip (first = leftmost).
    """
    global _zones

    if not node_ids:
        log.warning("auto_partition: no node IDs — zones unchanged.")
        return {}

    per_zone  = LED_COUNT // len(node_ids)
    remainder = LED_COUNT  % len(node_ids)
    zones: Dict[str, Tuple[int, int]] = {}
    cursor = 0

    for idx, node in enumerate(node_ids):
        extra = 1 if idx < remainder else 0
        end   = cursor + per_zone + extra - 1
        zones[node] = (cursor, min(end, LED_COUNT - 1))
        cursor = end + 1

    _zones = zones
    log.info("🗺️  Auto-partitioned %d LEDs into %d zones:", LED_COUNT, len(zones))
    for n, (s, e) in zones.items():
        log.info("   %-20s  LEDs %d–%d  (%d LEDs)", n, s, e, e - s + 1)
    return zones


def set_zones(zones_dict: Dict) -> Dict[str, Tuple[int, int]]:
    """
    Set zones from a raw dict.  Accepts:
      { "NODE_A": [0, 9], ... }
      { "NODE_A": {"start": 0, "end": 9}, ... }
    Returns the parsed zones dict.
    """
    global _zones
    parsed: Dict[str, Tuple[int, int]] = {}
    for node, spec in zones_dict.items():
        if isinstance(spec, (list, tuple)) and len(spec) == 2:
            s, e = int(spec[0]), int(spec[1])
        elif isinstance(spec, dict):
            s = int(spec.get("start", spec.get("s", 0)))
            e = int(spec.get("end",   spec.get("e", 0)))
        else:
            log.warning("⚠️  Cannot parse zone spec for '%s': %s", node, spec)
            continue
        if s < 0 or e >= LED_COUNT or s > e:
            log.warning("⚠️  Zone '%s': LED %d–%d invalid (strip=%d)", node, s, e, LED_COUNT)
            continue
        parsed[node] = (s, e)

    _zones = parsed
    log.info("🗺️  Zones configured manually: %d nodes.", len(_zones))
    return parsed


def set_from_api_payload(payload: Dict) -> Dict[str, Tuple[int, int]]:
    """
    Accept a dynamic mapping from the frontend/backend  (Upgrade #7).

    Payload format (from POST /zones/map):
    {
      "ROOM_101":  {"start": 0,  "end": 9,  "type": "room"},
      "HALLWAY_A": {"start": 10, "end": 19, "type": "hallway"},
      "EXIT_MAIN": {"start": 20, "end": 24, "type": "exit"}
    }

    Or auto-partition from an ordered list:
    { "auto": ["ROOM_101", "HALLWAY_A", "EXIT_MAIN"] }

    Returns the parsed zones dict.
    """
    global _zones, _node_types

    if "auto" in payload:
        node_ids = payload["auto"]
        if not isinstance(node_ids, list) or not node_ids:
            raise ValueError("'auto' must be a non-empty list of node IDs.")
        return auto_partition(node_ids)

    parsed: Dict[str, Tuple[int, int]] = {}
    types:  Dict[str, str]             = {}

    for node, spec in payload.items():
        if not isinstance(spec, dict):
            log.warning("⚠️  Skipping non-dict spec for node '%s'.", node)
            continue
        s = int(spec.get("start", spec.get("s", 0)))
        e = int(spec.get("end",   spec.get("e", 0)))
        t = str(spec.get("type", "room")).lower()

        if s < 0 or e >= LED_COUNT or s > e:
            log.warning("⚠️  Node '%s': LED %d–%d out of range.", node, s, e)
            continue

        if t not in {"room", "hallway", "exit"}:
            t = "room"

        parsed[node] = (s, e)
        types[node]  = t

    _zones      = parsed
    _node_types = types

    log.info(
        "🗺️  Dynamic mapping applied: %d nodes (%d exits, %d hallways, %d rooms).",
        len(parsed),
        sum(1 for t in types.values() if t == "exit"),
        sum(1 for t in types.values() if t == "hallway"),
        sum(1 for t in types.values() if t == "room"),
    )
    return parsed


def load_from_config(path: Optional[str] = None) -> bool:
    target = path or ZONE_CONFIG_PATH
    if not target or not os.path.isfile(target):
        log.info("Zone config '%s' not found — will use auto-partition.", target)
        return False
    try:
        with open(target) as f:
            data = json.load(f)
        set_from_api_payload(data)
        log.info("✅ Zone config loaded from '%s'.", target)
        return True
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        log.error("❌ Failed to load zone config '%s': %s", target, exc)
        return False


def save_to_config(path: Optional[str] = None) -> bool:
    target = path or ZONE_CONFIG_PATH
    if not target:
        return False
    # Persist in API-payload format so it can be re-loaded via set_from_api_payload
    serialisable = {
        node: {
            "start": s,
            "end":   e,
            "type":  _node_types.get(node, "room"),
        }
        for node, (s, e) in _zones.items()
    }
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            json.dump(serialisable, f, indent=2)
        log.info("💾 Zone config saved to '%s'.", target)
        return True
    except OSError as exc:
        log.error("❌ Failed to save zone config: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Apply to LED driver
# ─────────────────────────────────────────────────────────────────────

def apply_to_driver() -> None:
    """Push zone layout + node types into led_driver (initial setup)."""
    if not _zones:
        log.warning("⚠️  apply_to_driver: no zones configured.")
        return
    led_driver.register_zones(_zones)


def update_led_driver() -> None:
    """
    Push zone layout + node types into led_driver at runtime
    (called after set_from_api_payload to apply dynamic remapping).
    """
    if not _zones:
        return
    led_driver.update_zone_layout(_zones, _node_types)
    log.info("✅ LED driver updated with new zone layout.")


# ─────────────────────────────────────────────────────────────────────
# Queries
# ─────────────────────────────────────────────────────────────────────

def get_zones() -> Dict[str, Tuple[int, int]]:
    return dict(_zones)


def get_node_types() -> Dict[str, str]:
    return dict(_node_types)


def get_zone_for_node(node: str) -> Optional[Tuple[int, int]]:
    return _zones.get(node)


def get_node_type(node: str) -> str:
    return _node_types.get(node, "room")


def node_count() -> int:
    return len(_zones)


