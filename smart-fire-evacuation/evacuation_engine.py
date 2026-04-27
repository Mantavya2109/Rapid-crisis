"""
evacuation_engine.py
--------------------
Orchestrates the full evacuation pipeline.

New in this version:
  - Dijkstra with live hazard weights (falls back to BFS)
  - commandId per evacuation (dedup across LED retries)
  - LED coverage % metric
  - backup LED IP lookup
  - event_log integration throughout
"""

import uuid
import threading
from typing import Any, Dict, List, Optional

import device_registry
import event_log
import graph_manager
import state_manager
from cloud_sync import send_structured_fire_alert
from led_controller import (
    build_path_commands,
    broadcast_commands_to_devices,
    LED_OK,
    LED_FAILED_CRITICAL,
)
from logger import get_logger
from pathfinder import dijkstra_to_exit, bfs_to_exit
from config.settings import BUILDING_ID

log = get_logger(__name__)

HARD_SAFETY_MODE_COOLDOWN_SEC = 120

def execute_hard_safety_mode() -> None:
    """
    Globally lock the system into HARD SAFETY MODE.
    All nodes execute raw hazard warning regardless of dynamic routing or queue boundaries.
    """
    import metrics
    metrics.safety_mode_triggers_total.inc()
    
    log.error("💥 ENGAGING HARD SAFETY MODE ACROSS ENTIRE FACILITY.")
    
    graph = graph_manager.get_adjacency()
    directions = graph_manager.get_directions()
    
    # Broadcast DANGER / EXIT blindly everywhere to attempt maximum visibility since logic is failing
    merged_commands = {}
    for node in graph:
        merged_commands[node] = {"node": node, "color": "RED", "mode": "PULSE", "priority": 100}

    # Dispatch immediately (bypassing PriorityQueues or Cloud Syncs)
    device_ips = {}
    for node in merged_commands:
        led_dev = device_registry.get_led_device_for_node(node)
        if led_dev and led_dev.get("ip"):
            device_ips[node] = led_dev["ip"]
            
    if merged_commands:
        broadcast_commands_to_devices(
            device_ips=device_ips,
            node_commands=merged_commands,
            backup_ips={},
            command_id="HARD-SAFETY-BROADCAST",
        )
    
    event_log.system_event("HARD_SAFETY_MODE_TRIGGERED", "Total network blind spot threshold breached.")

def trigger_evacuation(
    sensor_payload: Dict[str, Any],
    start_nodes:    Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Full evacuation pipeline — multi-start, Dijkstra-first.

    Parameters
    ----------
    sensor_payload : validated sensor dict (with nodeId, buildingId, etc.)
    start_nodes    : explicit evacuation start nodes (multi-person scenario)
    """
    device_id:    str = sensor_payload.get("deviceId", "UNKNOWN")
    affected_node: str = (
        sensor_payload.get("nodeId")
        or sensor_payload.get("node")
        or "UNKNOWN"
    )
    building_id: str = sensor_payload.get("buildingId", BUILDING_ID)
    severity     = sensor_payload.get("status", "FIRE")

    log.warning(
        "🔥 EVACUATION — Device: %s | Node: %s | Building: %s | Temp: %s°C | Smoke: %s",
        device_id, affected_node, building_id,
        sensor_payload.get("temperature"), sensor_payload.get("smoke"),
    )

    # ── Step 1: Record alert ──────────────────────────────────────────
    state_manager.record_alert(affected_node, severity=severity)
    state_manager.set_evacuation_active(True)

    unsafe_nodes = state_manager.get_unsafe_nodes()

    event_log.fire_detected(
        node_id=affected_node,
        device_id=device_id,
        temp=sensor_payload.get("temperature", 0),
        smoke=sensor_payload.get("smoke", 0),
    )

    # ── Step 2: Determine start nodes ────────────────────────────────
    if not start_nodes:
        start_nodes = [affected_node]

    # ── Step 3: Load graph + compute live hazard weights ─────────────
    graph      = graph_manager.get_adjacency()
    exits      = graph_manager.get_exits()
    directions = graph_manager.get_directions()

    hazard_weights = graph_manager.get_hazard_weights()
    has_weights    = bool(hazard_weights)

    # ── Step 4: Pathfinding per start node ───────────────────────────
    local_paths: Dict[str, List[str]] = {}
    path_weights: Dict[str, float]    = {}
    algorithm_used                    = "dijkstra" if has_weights else "bfs"

    for start in start_nodes:
        if start not in graph:
            log.warning("Start node '%s' not in graph — skipping.", start)
            local_paths[start] = []
            continue

        if has_weights:
            path, cost = dijkstra_to_exit(
                graph, start, exits,
                blocked_nodes=unsafe_nodes,
                node_weights=hazard_weights,
            )
        else:
            path = bfs_to_exit(graph, start, exits, unsafe_nodes)
            cost = float(len(path))

        local_paths[start]  = path
        path_weights[start] = cost

        if path:
            event_log.path_computed(start, path, algorithm_used, cost)
        else:
            log.error("🚨 NO SAFE PATH from '%s'!", start)

    # ── Step 5: Build merged LED commands ────────────────────────────
    # commandId is shared across all LED sends for this evacuation event
    evacuation_cmd_id = str(uuid.uuid4())

    merged_commands: Dict[str, Dict[str, Any]] = {}
    for start, path in local_paths.items():
        if not path:
            continue
        cmds = build_path_commands(path, directions)
        for cmd in cmds:
            node = cmd["node"]
            if node not in merged_commands or cmd["priority"] > merged_commands[node]["priority"]:
                merged_commands[node] = cmd

    # ── Step 6: Cloud sync (Async AI Predictive Routing) ─────────────
    cloud_synced = False
    
    def _async_cloud_sync():
        try:
            cloud_response = send_structured_fire_alert(
                building_id=building_id,
                blocked_nodes=unsafe_nodes,
                start_nodes=start_nodes,
                sensor_readings={
                    "temperature": sensor_payload.get("temperature"),
                    "smoke":       sensor_payload.get("smoke"),
                    "status":      sensor_payload.get("status"),
                },
            )
            
            if cloud_response:
                event_log.cloud_sync_ok("/fire-alert", attempt=1)
                
                # Check for AI predictive hazard weights
                if "hazard_weights" in cloud_response:
                    weights = cloud_response["hazard_weights"]
                    log.info("🧠 Received AI predictive hazard weights from backend: %s", weights)
                    graph_manager.update_ai_hazard_weights(weights)
                    # Note: The next sensor reading (which happens frequently) will automatically
                    # trigger a re-evacuation using these new weights.
                
                # We could also apply dynamic commands from cloud here, but we prioritize local LEDs.
            else:
                event_log.cloud_sync_failed("/fire-alert", attempts=3)
        except Exception as exc:
            log.error("Cloud sync thread failed: %s", exc)

    # Fire and forget the cloud sync to avoid blocking local LED dispatch
    threading.Thread(target=_async_cloud_sync, name="cloud-sync-ai", daemon=True).start()
    log.info("🔁 Triggered local %s commands; backend AI prediction running async.", algorithm_used.upper())

    # ── Step 7: Build device IP maps ─────────────────────────────────
    device_ips:  Dict[str, str] = {}
    backup_ips:  Dict[str, str] = {}

    for node in merged_commands:
        # Try dynamic registry first, then static config
        led_dev = device_registry.get_led_device_for_node(node)
        if led_dev and led_dev.get("ip"):
            device_ips[node] = led_dev["ip"]
        else:
            static = graph_manager.get_led_device_for_node(node)
            if static and static.get("ip"):
                device_ips[node] = static["ip"]

        # Backup LED
        backup_dev = graph_manager.get_backup_led_for_node(node)
        if backup_dev and backup_dev.get("ip"):
            backup_ips[node] = backup_dev["ip"]

    # ── Step 8: Dispatch LED commands ────────────────────────────────
    led_results: Dict[str, str] = {}
    if merged_commands:
        led_results = broadcast_commands_to_devices(
            device_ips    = device_ips,
            node_commands = merged_commands,
            backup_ips    = backup_ips,
            command_id    = evacuation_cmd_id,
        )

    # ── Coverage metric + critical node detection ─────────────────────
    total_nodes    = len(merged_commands)
    ok_nodes       = sum(1 for s in led_results.values() if s == LED_OK)
    critical_nodes = [n for n, s in led_results.items() if s == LED_FAILED_CRITICAL]
    coverage_pct   = round((ok_nodes / total_nodes * 100) if total_nodes else 0.0, 1)

    # Determine overall evacuation status
    if critical_nodes:
        overall_status = "FAILED_CRITICAL"  # Manual intervention required on some nodes
    elif ok_nodes == total_nodes and total_nodes > 0:
        overall_status = "OK"
    elif ok_nodes > 0:
        overall_status = "PARTIAL"
    else:
        overall_status = "FAILED"

    event_log.evacuation_complete(cloud_synced, coverage_pct, led_results)
    event_log.evacuation_triggered(affected_node, start_nodes, local_paths)

    result = {
        "device_id":          device_id,
        "affected_node":      affected_node,
        "building_id":        building_id,
        "start_nodes":        start_nodes,
        "unsafe_nodes":       unsafe_nodes,
        "algorithm":          algorithm_used,
        "local_paths":        local_paths,
        "led_commands":       list(merged_commands.values()),
        "led_results":        led_results,
        "led_coverage_pct":   coverage_pct,
        "critical_led_nodes": critical_nodes,        # Nodes with NO LED guidance at all
        "critical_led_count": len(critical_nodes),
        "overall_status":     overall_status,        # OK | PARTIAL | FAILED | FAILED_CRITICAL
        "cloud_synced":       cloud_synced,
        "fail_safe_active":   not cloud_synced,
        "command_id":         evacuation_cmd_id,
    }
    log.info(
        "Evacuation: node=%s alg=%s coverage=%.0f%% cloud=%s status=%s critical_nodes=%s",
        affected_node, algorithm_used, coverage_pct, cloud_synced,
        overall_status, critical_nodes or "none",
    )
    return result
