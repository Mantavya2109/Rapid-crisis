"""
priority_resolver.py
---------------------
LED state conflict resolution for the multi-zone evacuation system.

Problem: a node's LED zone can receive state updates from multiple sources:
  - Its own sensor reading (primary)
  - Multi-zone guidance (SAFE_PATH → EXIT, neighbour WARNING)
  - System mode override (EVACUATION_MODE → restrict state changes)
  - Operator manual set

Without a resolver these writes can conflict and create confusing LEDs.

Resolution rule
---------------
Every zone has a "write owner" and a priority tier.  A lower-priority
source cannot override a higher-priority source.

Priority tiers (highest first):

  Tier 5  OPERATOR          — manual operator override (always wins)
  Tier 4  SENSOR_CRITICAL   — CRITICAL_FIRE or DANGER from own sensor
  Tier 3  GUIDANCE_EXIT     — evacuation path EXIT assignment
  Tier 2  SENSOR_WARNING    — WARNING or PREDICTIVE from own sensor
  Tier 1  GUIDANCE_WARN     — hazard-spread WARNING from neighbour
  Tier 0  NORMAL            — default; any source can override

The resolver is the single gate for all zone writes.  led_driver is
never called directly from multi-zone logic — only through this module.
"""

import threading
from typing import Dict, Optional, Tuple

import led_driver
from logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Priority tiers
# ─────────────────────────────────────────────────────────────────────

TIER_OPERATOR         = 5
TIER_SENSOR_CRITICAL  = 4
TIER_GUIDANCE_EXIT    = 3
TIER_SENSOR_WARNING   = 2
TIER_GUIDANCE_WARN    = 1
TIER_NORMAL           = 0

# Maps LED zone status → default tier
_STATUS_TIER: Dict[str, int] = {
    "CRITICAL_FIRE":   TIER_SENSOR_CRITICAL,
    "DANGER":          TIER_SENSOR_CRITICAL,
    "EXIT":            TIER_GUIDANCE_EXIT,
    "WARNING":         TIER_SENSOR_WARNING,
    "PREDICTIVE_FIRE": TIER_SENSOR_WARNING,
    "OFFLINE":         TIER_SENSOR_WARNING,  # OFFLINE from own sensor = high priority
    "NORMAL":          TIER_NORMAL,
    "OFF":             TIER_NORMAL,
}

# Sources that bypass the resolver entirely (always win)
_BYPASS_SOURCES = frozenset({"OPERATOR"})


# ─────────────────────────────────────────────────────────────────────
# Module state
# ─────────────────────────────────────────────────────────────────────

# { nodeId → (current_tier, current_status, source) }
_zone_ownership: Dict[str, Tuple[int, str, str]] = {}
_lock = threading.RLock()


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────

def try_set(
    node:   str,
    status: str,
    source: str = "SENSOR",
    tier:   Optional[int] = None,
) -> bool:
    """
    Attempt to set a zone's LED status.

    Parameters
    ----------
    node   : building node ID
    status : LED state string (must be in VALID_STATUSES)
    source : "SENSOR" | "GUIDANCE" | "OPERATOR" | custom label (for logging)
    tier   : override the auto-computed priority tier (optional)

    Returns True if the write was accepted; False if blocked by higher priority.
    """
    # Resolve tier: explicit > status-derived
    if tier is None:
        tier = _STATUS_TIER.get(status, TIER_NORMAL)

    # Operator always wins
    if source in _BYPASS_SOURCES:
        _apply(node, status, tier, source)
        return True

    with _lock:
        existing = _zone_ownership.get(node)

        if existing is None:
            # No owner yet — accept unconditionally
            _apply(node, status, tier, source)
            return True

        existing_tier, existing_status, existing_source = existing

        if tier >= existing_tier:
            # New write has equal or higher priority — accept
            _apply(node, status, tier, source)
            return True
        else:
            log.debug(
                "🛑 Priority block: node='%s' source='%s' status=%s (tier=%d) "
                "blocked by '%s' status=%s (tier=%d)",
                node, source, status, tier,
                existing_source, existing_status, existing_tier,
            )
            return False


def release(node: str, source: str) -> bool:
    """
    Release a zone so lower-priority sources can write again.
    Should be called when:
    - Node sensor data returns to NORMAL (sensor releases ownership)
    - Alert is cleared
    - Evacuation mode ends (guidance releases EXIT ownership)

    Returns True if the zone was owned by `source`.
    """
    with _lock:
        existing = _zone_ownership.get(node)
        if existing and existing[2] == source:
            del _zone_ownership[node]
            log.debug("🔓 Zone '%s' ownership released by '%s'.", node, source)
            return True
    return False


def release_all(source: str) -> int:
    """Release all zones owned by `source`. Returns count released."""
    with _lock:
        to_release = [n for n, (_, _, s) in _zone_ownership.items() if s == source]
        for n in to_release:
            del _zone_ownership[n]
    if to_release:
        log.info("🔓 Released %d zones owned by '%s'.", len(to_release), source)
    return len(to_release)


def force_set(node: str, status: str, source: str = "OPERATOR") -> None:
    """
    Unconditionally set a zone status (operator override).
    Bypasses all priority checks and sets OPERATOR tier.
    """
    _apply(node, status, TIER_OPERATOR, source)
    log.warning("🔐 OPERATOR forced: node='%s' → %s", node, status)


def reset_node(node: str) -> None:
    """
    Clear all priority ownership for a node and reset LED to NORMAL.
    Use during recovery.
    """
    with _lock:
        _zone_ownership.pop(node, None)
    led_driver.set_zone_status(node, "NORMAL")
    log.info("♻️  Zone '%s' reset to NORMAL (priority cleared).", node)


def reset_all() -> None:
    """Clear all ownership and reset every zone to NORMAL. Use on full recovery."""
    with _lock:
        nodes = list(_zone_ownership.keys())
        _zone_ownership.clear()
    for node in nodes:
        led_driver.set_zone_status(node, "NORMAL")
    log.info("♻️  ALL zones reset to NORMAL (full priority clear).")


def get_ownership_snapshot() -> Dict[str, Dict]:
    """Return current zone ownership for debugging/REST API."""
    with _lock:
        return {
            node: {"tier": t, "status": s, "source": src}
            for node, (t, s, src) in _zone_ownership.items()
        }


# ─────────────────────────────────────────────────────────────────────
# Internal
# ─────────────────────────────────────────────────────────────────────

def _apply(node: str, status: str, tier: int, source: str) -> None:
    with _lock:
        _zone_ownership[node] = (tier, status, source)
    led_driver.set_zone_status(node, status)
