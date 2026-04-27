"""
sensor_processor.py  (v2 — upgraded)
--------------------------------------
Core intelligence of the Raspberry Pi edge controller.

UPGRADE SUMMARY vs v1
─────────────────────
1. Decision Logic   : 6 states (NORMAL / PREDICTIVE_FIRE / WARNING /
                      DANGER / CRITICAL_FIRE / OFFLINE)
2. Memory           : sliding-window history per node (smoke + temp)
3. Trend detection  : rate-of-rise [units/min] from deque; early fire warning
4. Anti-flicker     : maximum 1-severity-step downgrade per reading
5. Multi-zone       : DANGER/CRITICAL → BFS computes safe paths → EXIT LEDs
6. Event filtering  : EventFilter decides what reaches the cloud
7. Fail-safe        : stale-data watchdog marks nodes OFFLINE if no update
8. No alarm logic   : purely visual LED indication, no buzzer

State machine (ordered by severity):
  0 NORMAL          — all readings safe, no trend
  1 PREDICTIVE_FIRE — rising trend detected (early warning, orange)
  2 WARNING         — one value above threshold OR device says WARNING
  3 DANGER          — both values above threshold (slow-pulse red)
  4 CRITICAL_FIRE   — extreme values, sustained DANGER, or device says FIRE
  5 OFFLINE         — no data within STALE_DATA_TIMEOUT_SEC
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

import cloud_sync
import device_registry
import event_log
import graph_manager
import priority_resolver
import system_mode
import recovery_manager
import state_manager
from config.settings import (
    TEMP_THRESHOLD,
    SMOKE_THRESHOLD,
    BUILDING_ID,
    TREND_WINDOW_SIZE,
    TREND_RISE_RATE_WARNING,
    PREDICTIVE_FIRE_RISE_RATE,
    CRITICAL_SMOKE_MULTIPLIER,
    CRITICAL_TEMP_THRESHOLD,
    CONSECUTIVE_DANGER_CRITICAL,
    STALE_DATA_TIMEOUT_SEC,
)
from event_filter import EventFilter
from logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────
# State constants + severity ladder
# ─────────────────────────────────────────────────────────────────────

NORMAL          = "NORMAL"
PREDICTIVE_FIRE = "PREDICTIVE_FIRE"
WARNING         = "WARNING"
DANGER          = "DANGER"
CRITICAL_FIRE   = "CRITICAL_FIRE"
OFFLINE         = "OFFLINE"

# Ordered by severity index (used for anti-flicker logic)
_SEVERITY_ORDER = [NORMAL, PREDICTIVE_FIRE, WARNING, DANGER, CRITICAL_FIRE, OFFLINE]
_SEVERITY: Dict[str, int] = {s: i for i, s in enumerate(_SEVERITY_ORDER)}

_ALERT_STATES = frozenset({DANGER, CRITICAL_FIRE})


# ─────────────────────────────────────────────────────────────────────
# Per-node sliding-window memory
# ─────────────────────────────────────────────────────────────────────

@dataclass
class NodeMemory:
    """Sliding-window sensor history + state tracking for one node."""
    # (unix_ts, value) pairs — maxlen controlled by TREND_WINDOW_SIZE
    smoke_history:      Deque[Tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=TREND_WINDOW_SIZE)
    )
    temp_history:       Deque[Tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=TREND_WINDOW_SIZE)
    )
    last_state:         str   = OFFLINE
    last_update:        float = 0.0
    state_change_count: int   = 0
    # Counts consecutive readings at DANGER or above → triggers CRITICAL escalation
    consecutive_danger: int   = 0


# Module-level state
_node_memory:  Dict[str, NodeMemory] = {}
_memory_lock   = threading.RLock()
_event_filter  = EventFilter()

# Background watchdog
_watchdog_thread: Optional[threading.Thread] = None


# ─────────────────────────────────────────────────────────────────────
# Trend computation
# ─────────────────────────────────────────────────────────────────────

def _rise_rate(history: Deque[Tuple[float, float]]) -> float:
    """
    Linear rate of rise [units / minute] from a sliding window.
    Positive = rising, negative = falling.  Returns 0.0 if < 2 points.
    """
    if len(history) < 2:
        return 0.0
    t0, v0 = history[0]
    t1, v1 = history[-1]
    dt_min = (t1 - t0) / 60.0
    return (v1 - v0) / dt_min if dt_min > 0.001 else 0.0


# ─────────────────────────────────────────────────────────────────────
# Classification engine — 6-state FSM
# ─────────────────────────────────────────────────────────────────────

def _classify(
    node:     str,
    temp:     float,
    smoke:    float,
    declared: str,    # "OK" | "WARNING" | "FIRE"
    mem:      NodeMemory,
) -> str:
    """
    Classify into one of 6 states.  Priority chain (first match wins):
      1. CRITICAL_FIRE  — extreme readings, sustained danger, or device says FIRE
      2. DANGER         — both values exceed thresholds
      3. PREDICTIVE_FIRE— rapid rate-of-rise (high urgency)
      4. WARNING        — one value over threshold OR declared WARNING
      5. PREDICTIVE_FIRE— moderate rate-of-rise (low urgency)
      6. NORMAL
    """
    temp_high      = temp  > TEMP_THRESHOLD
    smoke_high     = smoke > SMOKE_THRESHOLD
    temp_critical  = temp  > CRITICAL_TEMP_THRESHOLD
    smoke_critical = smoke > SMOKE_THRESHOLD * CRITICAL_SMOKE_MULTIPLIER

    # ── 1. CRITICAL_FIRE ──────────────────────────────────────────────
    if declared == "FIRE":
        return CRITICAL_FIRE
    if temp_critical or smoke_critical:
        return CRITICAL_FIRE
    if mem.consecutive_danger >= CONSECUTIVE_DANGER_CRITICAL:
        return CRITICAL_FIRE

    # ── 2. DANGER ─────────────────────────────────────────────────────
    if temp_high and smoke_high:
        return DANGER

    # ── 3/5. Trend analysis ───────────────────────────────────────────
    smoke_rate = _rise_rate(mem.smoke_history)
    temp_rate  = _rise_rate(mem.temp_history)
    max_rate   = max(smoke_rate, temp_rate)

    if max_rate >= PREDICTIVE_FIRE_RISE_RATE:
        return PREDICTIVE_FIRE          # High urgency predictive

    # ── 4. WARNING ────────────────────────────────────────────────────
    if declared == "WARNING" or temp_high or smoke_high:
        return WARNING

    # ── 5. Moderate predictive ────────────────────────────────────────
    if max_rate >= TREND_RISE_RATE_WARNING:
        return PREDICTIVE_FIRE          # Low urgency predictive

    # ── 6. NORMAL ─────────────────────────────────────────────────────
    return NORMAL


def _anti_flicker(old_state: str, new_state: str) -> str:
    """
    Prevent rapid LED flickering by limiting downgrade to 1 severity step
    per reading.  Upgrades are always immediate.
    """
    old_sev = _SEVERITY.get(old_state, 0)
    new_sev = _SEVERITY.get(new_state, 0)

    if old_sev - new_sev > 1:          # Downgrade of > 1 step → only allow 1 step
        clamped = max(new_sev, old_sev - 1)
        return _SEVERITY_ORDER[clamped]

    return new_state


# ─────────────────────────────────────────────────────────────────────
# Multi-zone intelligence — evacuation LED guidance
# ─────────────────────────────────────────────────────────────────────

def _apply_multi_zone_guidance(danger_nodes: List[str]) -> None:
    """
    Upgrade #3 — Directional Evacuation via Dijkstra (safest path).

    When DANGER/CRITICAL nodes exist, uses Dijkstra with live hazard weights
    to find the genuinely safest path (not just shortest hop count).

    All LED writes go through priority_resolver so sensor-owned critical zones
    are never overridden by guidance (Upgrade #1 — priority conflict resolution).

    Actions:
    - Blocked nodes (fire)  → CRITICAL/DANGER colours (sensor already set these)
    - Safe-path corridor    → EXIT (white) via priority_resolver tier GUIDANCE_EXIT
    - Exit nodes reachable  → EXIT
    - Fire-adjacent safe    → WARNING via tier GUIDANCE_WARN (lowest, easily overridden)
    """
    graph = graph_manager.get_adjacency()
    exits = graph_manager.get_exits()
    if not graph or not exits or not danger_nodes:
        return

    try:
        from pathfinder import dijkstra_to_exit, compute_node_weights
        import priority_resolver
    except ImportError:
        return

    danger_set = set(danger_nodes)

    # ── Build live hazard weights ──────────────────────────────────────
    try:
        hazard_weights = graph_manager.get_hazard_weights()
    except Exception:
        hazard_weights = {}

    safe_path_nodes: set = set()
    fire_neighbours:  set = set()
    computed_paths:   Dict[str, List[str]] = {}

    non_danger = [n for n in graph if n not in danger_set]

    for start in non_danger:
        if start in exits:
            continue
        # Dijkstra — safest path (lowest total hazard cost), not shortest hops
        path, cost = dijkstra_to_exit(
            graph        = graph,
            start        = start,
            exits        = exits,
            blocked_nodes = list(danger_set),
            node_weights  = hazard_weights,
        )
        if path and cost < 1e9:
            computed_paths[start] = path
            for n in path[1:-1]:
                if n not in danger_set:
                    safe_path_nodes.add(n)

    # Fire-adjacent safe nodes (hazard awareness)
    for dn in danger_set:
        for nb in graph.get(dn, []):
            if nb not in danger_set:
                fire_neighbours.add(nb)

    # ── Push guidance through priority_resolver ────────────────────────
    with _memory_lock:
        current_states = {n: m.last_state for n, m in _node_memory.items()}

    for node in graph:
        cs = current_states.get(node, "NORMAL")

        # Never touch sensor-owned critical zones
        if cs in ("DANGER", "CRITICAL_FIRE", "OFFLINE"):
            continue

        if node in exits or node in safe_path_nodes:
            priority_resolver.try_set(
                node, "EXIT", source="GUIDANCE", tier=priority_resolver.TIER_GUIDANCE_EXIT
            )
        elif node in fire_neighbours and cs == "NORMAL":
            priority_resolver.try_set(
                node, "WARNING", source="GUIDANCE", tier=priority_resolver.TIER_GUIDANCE_WARN
            )

    # Persist computed evacuation paths for REST API
    _store_evacuation_paths(computed_paths, list(danger_set))

    log.info(
        "🗺️  Dijkstra guidance: %d danger, %d corridor EXIT, %d hazard neighbours",
        len(danger_set), len(safe_path_nodes), len(fire_neighbours),
    )


# Evacuation path cache for GET /evacuation/paths
_evacuation_paths: Dict = {}
_paths_lock = threading.Lock()


def _store_evacuation_paths(paths: Dict[str, List[str]], danger_nodes: List[str]) -> None:
    with _paths_lock:
        _evacuation_paths.clear()
        _evacuation_paths.update({
            "paths":        paths,
            "danger_nodes": danger_nodes,
            "computed_at":  time.time(),
        })


def get_evacuation_paths() -> Dict:
    with _paths_lock:
        return dict(_evacuation_paths)


# ─────────────────────────────────────────────────────────────────────
# MQTT callbacks  (registered in main.py)
# ─────────────────────────────────────────────────────────────────────

def on_sensor_data(topic: str, payload: Dict[str, Any]) -> None:
    """
    Handle decoded JSON from  sensors/data/<nodeId>.

    Expected fields
    ---------------
    deviceId    : str   (required)
    nodeId      : str   (optional — inferred from registry)
    buildingId  : str   (optional)
    temperature : float (required)
    smoke       : float (required)
    status      : "OK" | "WARNING" | "FIRE"  (optional, device self-assessment)
    ip          : str   (optional)
    """
    # Anti-Spoofing: Extract Device ID strictly from the Topic
    # Expected Topic: sensors/data/<device_id>
    parts = topic.split("/")
    if len(parts) >= 3:
        topic_device_id = parts[2]
    else:
        topic_device_id = None
        
    device_id = topic_device_id or payload.get("deviceId") or payload.get("device_id")
    if not device_id:
        log.warning("⚠️  Sensor payload missing 'deviceId' globally — dropping: %s", payload)
        return

    # Enforce strict device_id to node resolution
    resolved_node = device_registry.get_node_for_device(device_id)
    if not resolved_node:
        log.warning("⚠️  Unregistered device '%s' attempting publish. Dropping.", device_id)
        return
        
    node = resolved_node 

    # ── Strict Sequence Validation ──────────────────────────────────────
    seq = payload.get("seq", payload.get("sequence_number"))
    if seq is not None:
        try:
            seq = int(seq)
            last_seq = state_manager.get_node_sequence(node)
            
            if seq <= last_seq:
                drop_delta = seq - last_seq
                if drop_delta < -100:
                    log.warning("🔄 Sequence drastically fell (%d) for '%s'. Assuming Device Reboot. Resetting seq.", drop_delta, node)
                else:
                    log.debug("🗑️  Dropping stale sequence payload from '%s' (seq: %d <= last_seq: %d)", node, seq, last_seq)
                    import metrics
                    metrics.duplicate_events_dropped_total.labels(node=node).inc()
                    return
                    
            state_manager.update_node_sequence(node, seq)
        except ValueError:
            pass # Ignore malformed seq, though prod strictly should drop it

    building_id = payload.get("buildingId") or payload.get("building_id") or BUILDING_ID

    # Validate readings
    try:
        temp  = float(payload.get("temperature", 0))
        smoke = float(payload.get("smoke", 0))
    except (TypeError, ValueError) as exc:
        log.warning("⚠️  Non-numeric readings from '%s': %s — dropping.", device_id, exc)
        return

    declared = str(payload.get("status", "OK")).upper()
    if declared not in {"OK", "WARNING", "FIRE"}:
        declared = "OK"

    now = time.time()

    # ── Heartbeat + state persist ──────────────────────────────────────
    device_registry.record_heartbeat(device_id, ip=payload.get("ip"))
    enriched: Dict[str, Any] = {
        **payload,
        "nodeId":     node,
        "buildingId": building_id,
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }
    state_manager.update_sensor_data(node, enriched)

    # ── Update sliding-window memory + classify ────────────────────────
    with _memory_lock:
        if node not in _node_memory:
            _node_memory[node] = NodeMemory()
        mem = _node_memory[node]

        mem.smoke_history.append((now, smoke))
        mem.temp_history.append((now, temp))
        mem.last_update = now
        old_state = mem.last_state

        # Classify with full context
        raw_new = _classify(node, temp, smoke, declared, mem)

        # Apply anti-flicker (max 1-step downgrade)
        new_state = _anti_flicker(old_state, raw_new)

        # Track consecutive danger for CRITICAL escalation
        if new_state in _ALERT_STATES:
            mem.consecutive_danger += 1
        else:
            mem.consecutive_danger = 0

        state_changed = (new_state != old_state)
        if state_changed:
            mem.last_state = new_state
            mem.state_change_count += 1

        smoke_rate = _rise_rate(mem.smoke_history)
        temp_rate  = _rise_rate(mem.temp_history)

    log.info(
        "📡 %-14s  node=%-12s  T=%5.1f°C  S=%5.0f  "
        "dS/dt=%+5.1f/min  declared=%-8s  → %-16s%s",
        device_id, node, temp, smoke, smoke_rate, declared, new_state,
        "  🔄" if state_changed else "",
    )

    # ── Drive LED zone ─────────────────────────────────────────────────
    priority_resolver.try_set(node, new_state, source="SENSOR")

    # ── Alert tracking & Recovery ───────────────────────────────────────
    if new_state in _ALERT_STATES:
        if not state_manager.is_alert_debounced(node):
            state_manager.record_alert(node, severity="FIRE")
            if state_changed:
                event_log.fire_detected(
                    node_id=node, device_id=device_id,
                    temp=temp, smoke=smoke,
                )
        state_manager.set_evacuation_active(True)
    elif new_state == WARNING:
        if not state_manager.is_alert_debounced(node):
            state_manager.record_alert(node, severity="WARNING")
    elif new_state == NORMAL and old_state != NORMAL:
        priority_resolver.release(node, source="SENSOR")

    # Update global system mode
    with _memory_lock:
        all_states = {n: m.last_state for n, m in _node_memory.items()}
    system_mode.update_from_node_states(all_states)
    
    if not any(s in _ALERT_STATES for s in all_states.values()):
        # Check Hard Safety Mode Cooldown Exits
        if state_manager.is_hard_safety_mode_active():
            import evacuation_engine
            if time.time() - state_manager.get_hard_safety_mode_time() < evacuation_engine.HARD_SAFETY_MODE_COOLDOWN_SEC:
                log.warning("⏱️ Cannot recover yet. HARD SAFETY MODE Cooldown is actively blocking regression.")
                return
            else:
                log.info("✅ HARD SAFETY MODE Cooldown elapsed. Zero fires remaining. Allowing system recovery.")
                state_manager.set_hard_safety_mode(False)
                
        recovery_manager.notify_all_clear()

    # ── Multi-zone guidance ────────────────────────────────────────────
    with _memory_lock:
        danger_nodes = [
            n for n, m in _node_memory.items()
            if m.last_state in _ALERT_STATES
        ]
    if danger_nodes:
        # Run in background so MQTT loop is never blocked
        threading.Thread(
            target=_apply_multi_zone_guidance,
            args=(danger_nodes,),
            daemon=True,
        ).start()

    # ── Event-filtered cloud forward ───────────────────────────────────
    if _event_filter.should_emit(node, new_state, old_state):
        _async_cloud_forward(
            building_id   = building_id,
            node_id       = node,
            device_id     = device_id,
            temperature   = temp,
            smoke         = smoke,
            status        = new_state,
            state_changed = state_changed,
            smoke_rise_rate = round(smoke_rate, 2),
            temp_rise_rate  = round(temp_rate,  2),
            raw           = enriched,
        )


def on_heartbeat(topic: str, payload: Dict[str, Any]) -> None:
    """Handle decoded JSON from  sensors/heartbeat/<nodeId>."""
    parts = topic.split("/")
    if len(parts) >= 3:
        topic_device_id = parts[2]
    else:
        topic_device_id = None
        
    device_id = topic_device_id or payload.get("deviceId") or payload.get("device_id")
    if not device_id:
        return

    found = device_registry.record_heartbeat(
        device_id, ip=payload.get("ip") or ""
    )
    if found:
        log.debug("💓 HB MQTT: %s", device_id)
    else:
        log.warning(
            "💔 HB from UNKNOWN device '%s' — "
            "register via POST /devices/register first.", device_id,
        )


# ─────────────────────────────────────────────────────────────────────
# Stale-data watchdog  (Upgrade #6 — fail-safe)
# ─────────────────────────────────────────────────────────────────────

def start_watchdog() -> None:
    """
    Start the stale-data watchdog daemon thread.
    Every 10 s it checks all known nodes:
    - If last_update > STALE_DATA_TIMEOUT_SEC ago → mark OFFLINE
    - Recovery: next live reading clears OFFLINE automatically
    """
    global _watchdog_thread
    if _watchdog_thread and _watchdog_thread.is_alive():
        return

    def _run() -> None:
        log.info(
            "🐕 Stale-data watchdog started (offline_threshold=%ds, check every 10s).",
            STALE_DATA_TIMEOUT_SEC,
        )
        while True:
            time.sleep(10)
            _check_stale()

    _watchdog_thread = threading.Thread(
        target=_run, name="stale-watchdog", daemon=True
    )
    _watchdog_thread.start()


def _check_stale() -> None:
    now = time.time()
    with _memory_lock:
        nodes_snapshot = [(n, m.last_state, m.last_update)
                          for n, m in _node_memory.items()]

    for node, state, last_up in nodes_snapshot:
        if state == OFFLINE:
            continue
        age = now - last_up
        if age > STALE_DATA_TIMEOUT_SEC:
            log.warning(
                "📵 Node '%s' stale (%.0fs with no data) → OFFLINE", node, age
            )
            with _memory_lock:
                if node in _node_memory:
                    _node_memory[node].last_state = OFFLINE
                    _node_memory[node].consecutive_danger = 0
            priority_resolver.try_set(node, OFFLINE, source="SENSOR")
            state_manager.clear_alert(node, force=True)
            # Emit offline event to cloud
            _event_filter.reset(node)
            
    # Hard Safety Mode Trigger Evaluation
    # If >30% Sensors drop offline, Enter HARD SAFETY MODE
    if len(nodes_snapshot) > 0:
        offline_count = sum(1 for n, s, u in nodes_snapshot if s == OFFLINE)
        if (offline_count / len(nodes_snapshot)) > 0.3:
            log.error("💥 Greater than 30% sensors offline! Triggering HARD SAFETY MODE.")
            import evacuation_engine
            state_manager.set_hard_safety_mode(True)
            evacuation_engine.execute_hard_safety_mode()


# ─────────────────────────────────────────────────────────────────────
# Cloud forwarding (non-blocking)
# ─────────────────────────────────────────────────────────────────────

def _async_cloud_forward(**kwargs) -> None:
    threading.Thread(
        target=cloud_sync.send_sensor_telemetry,
        kwargs=kwargs,
        name="cloud-fwd",
        daemon=True,
    ).start()


# ─────────────────────────────────────────────────────────────────────
# Public state queries  (used by REST API  GET /system/state)
# ─────────────────────────────────────────────────────────────────────

def get_all_node_states() -> Dict[str, Dict]:
    """
    Return current state + trend metrics for every known node.
    Used by GET /system/state.
    """
    with _memory_lock:
        result = {}
        now = time.time()
        for node, mem in _node_memory.items():
            sr = _rise_rate(mem.smoke_history)
            tr = _rise_rate(mem.temp_history)
            result[node] = {
                "state":              mem.last_state,
                "last_update_ts":     mem.last_update,
                "stale_sec":          round(now - mem.last_update, 1),
                "smoke_rise_rate":    round(sr, 2),
                "temp_rise_rate":     round(tr, 2),
                "consecutive_danger": mem.consecutive_danger,
                "state_changes":      mem.state_change_count,
            }
        return result


def get_node_state(node: str) -> Optional[Dict]:
    with _memory_lock:
        mem = _node_memory.get(node)
        if not mem:
            return None
        now = time.time()
        return {
            "state":              mem.last_state,
            "stale_sec":          round(now - mem.last_update, 1),
            "smoke_rise_rate":    round(_rise_rate(mem.smoke_history), 2),
            "temp_rise_rate":     round(_rise_rate(mem.temp_history),  2),
            "consecutive_danger": mem.consecutive_danger,
            "state_changes":      mem.state_change_count,
        }
