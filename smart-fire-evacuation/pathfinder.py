"""
pathfinder.py
-------------
Pathfinding through the building graph.

Two algorithms available:
  dijkstra_to_exit()  — uses hazard weights, time decay, neighbour spread.
                         Finds the SAFEST path (lowest total hazard cost).
                         PREFERRED when sensor data is available.

  bfs_to_exit()       — unweighted BFS (fewest hops).
                         FALLBACK when no sensor data / weights unavailable.

Hazard weighting formula (exponential, threshold-aware):
  base_cost           — static traversal cost from edge_weights config
  smoke_penalty       — 0 → quadratic → 1000 (near-impassable above 2× threshold)
  temp_penalty        — exponential above TEMP_THRESHOLD (Kelvin-style growth)
  time_decay          — weight += age_of_alert * HAZARD_DECAY_FACTOR (fire spreads)
  neighbor_spread     — nodes adjacent to FIRE get partial penalty bleed-through
"""

import heapq
from collections import deque
from typing import Dict, List, Optional, Tuple

from config.settings import (
    TEMP_THRESHOLD,
    SMOKE_THRESHOLD,
    HAZARD_DECAY_FACTOR,
    NEIGHBOR_HAZARD_SPREAD,
)
from logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Hazard weight computation
# ─────────────────────────────────────────────────────────────────────

def compute_node_weights(
    graph: Dict[str, List[str]],
    sensor_data: Dict[str, Dict],
    alert_times: Dict[str, float],
    edge_base_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """
    Build a per-node hazard weight map for Dijkstra.

    Parameters
    ----------
    graph             : adjacency list
    sensor_data       : { nodeId → {temperature, smoke, ...} }
    alert_times       : { nodeId → unix timestamp of first alert }
    edge_base_weights : { nodeId → base traversal cost } from config

    Returns
    -------
    { nodeId → float hazard_weight }  (higher = more dangerous)
    """
    import time
    now = time.time()
    base  = edge_base_weights or {}
    weights: Dict[str, float] = {}

    for node in graph:
        w = base.get(node, 1.0)

        sensors = sensor_data.get(node, {})
        temp    = sensors.get("temperature", 0.0)
        smoke   = sensors.get("smoke",       0.0)

        # ── Smoke penalty (threshold-based, exponential) ──────────────
        if smoke >= SMOKE_THRESHOLD * 3:
            # 3× threshold = virtually impassable
            w += 1000.0
        elif smoke >= SMOKE_THRESHOLD * 2:
            w += 500.0
        elif smoke >= SMOKE_THRESHOLD:
            ratio = smoke / SMOKE_THRESHOLD
            w += ratio ** 2 * 50.0  # e.g. ratio=1.5 → +112.5

        # ── Temperature penalty (exponential above threshold) ─────────
        if temp >= TEMP_THRESHOLD:
            excess = temp - TEMP_THRESHOLD
            # e.g. excess=20 → +89.4,  excess=60 → +464.5
            w += excess ** 1.5

        # ── Time-based decay (fire spreads over time) ─────────────────
        alert_ts = alert_times.get(node)
        if alert_ts is not None:
            age = now - alert_ts
            w += age * HAZARD_DECAY_FACTOR

        weights[node] = w

    # ── Neighbour hazard bleed-through ────────────────────────────────
    # Nodes adjacent to a FIRE-alerted node inherit a fraction of its danger.
    # This prevents Dijkstra from routing through "hot corridors".
    for node, w in list(weights.items()):
        if node in alert_times:  # this node has an alert
            for neighbour in graph.get(node, []):
                if neighbour not in alert_times:
                    spread = w * NEIGHBOR_HAZARD_SPREAD
                    weights[neighbour] = weights.get(neighbour, 1.0) + spread

    log.debug("Hazard weights: %s", {k: round(v, 1) for k, v in weights.items()})
    return weights


# ─────────────────────────────────────────────────────────────────────
# Dijkstra (primary — safest path)
# ─────────────────────────────────────────────────────────────────────

def dijkstra_to_exit(
    graph: Dict[str, List[str]],
    start: str,
    exits: List[str],
    blocked_nodes: Optional[List[str]] = None,
    node_weights: Optional[Dict[str, float]] = None,
) -> Tuple[List[str], float]:
    """
    Find the path with the lowest total hazard cost to the nearest exit.

    Uses Dijkstra's algorithm on a node-weighted graph (hazard per node).

    Parameters
    ----------
    graph        : adjacency list
    start        : starting node
    exits        : valid exit nodes
    blocked_nodes: nodes to completely skip (fire / smoke > 3× threshold)
    node_weights : { nodeId → float } hazard cost

    Returns
    -------
    (path: List[str], total_cost: float)
    Path is empty list if no route found.
    """
    blocked  = set(blocked_nodes or [])
    weights  = node_weights or {}
    exit_set = set(exits)

    if start not in graph:
        log.warning("Dijkstra: start node '%s' not in graph.", start)
        return [], 0.0

    if start in blocked:
        log.warning("Dijkstra: start '%s' is blocked.", start)

    # Priority queue: (cumulative_cost, node_path_as_tuple)
    pq: list = [(0.0, (start,))]
    best_cost: Dict[str, float] = {}

    while pq:
        cost, path_tuple = heapq.heappop(pq)
        current = path_tuple[-1]

        if current in best_cost:
            continue
        best_cost[current] = cost

        if current in exit_set and current not in blocked:
            path = list(path_tuple)
            log.info(
                "✅ Dijkstra path [cost=%.1f]: %s",
                cost, " → ".join(path),
            )
            return path, cost

        for neighbour in graph.get(current, []):
            if neighbour in best_cost or neighbour in blocked:
                continue
            edge_cost  = weights.get(neighbour, 1.0)
            new_cost   = cost + edge_cost
            heapq.heappush(pq, (new_cost, path_tuple + (neighbour,)))

    log.error("Dijkstra: no safe path from '%s'. Blocked=%s", start, list(blocked))
    return [], 0.0


# ─────────────────────────────────────────────────────────────────────
# BFS (fallback — fewest hops)
# ─────────────────────────────────────────────────────────────────────

def bfs_to_exit(
    graph: Dict[str, List[str]],
    start: str,
    exits: List[str],
    blocked_nodes: Optional[List[str]] = None,
) -> List[str]:
    """
    Find the shortest path (fewest hops) to the nearest exit using BFS.
    Used as fallback when no sensor data / weights are available.

    Returns ordered path list, empty if no path found.
    """
    blocked = set(blocked_nodes or [])

    if start not in graph:
        log.warning("BFS: start node '%s' not in graph.", start)
        return []

    if start in exits and start not in blocked:
        return [start]

    queue   = deque([[start]])
    visited = {start}

    while queue:
        path    = queue.popleft()
        current = path[-1]

        for neighbour in graph.get(current, []):
            if neighbour in visited or neighbour in blocked:
                continue

            new_path = path + [neighbour]
            visited.add(neighbour)

            if neighbour in exits:
                log.info("BFS path found: %s (%d hops)", " → ".join(new_path), len(new_path) - 1)
                return new_path

            queue.append(new_path)

    log.error("BFS: no path from '%s'. Blocked=%s", start, list(blocked))
    return []


# ─────────────────────────────────────────────────────────────────────
# All-paths (for dashboard / alternative display)
# ─────────────────────────────────────────────────────────────────────

def find_all_paths(
    graph: Dict[str, List[str]],
    start: str,
    exits: List[str],
    blocked_nodes: Optional[List[str]] = None,
) -> List[List[str]]:
    """Return ALL safe paths to ALL reachable exits, sorted by hop count."""
    blocked = set(blocked_nodes or [])
    all_paths: List[List[str]] = []

    for exit_node in exits:
        if exit_node in blocked:
            continue
        path = bfs_to_exit(graph, start, [exit_node], blocked_nodes)
        if path:
            all_paths.append(path)

    all_paths.sort(key=len)
    return all_paths
