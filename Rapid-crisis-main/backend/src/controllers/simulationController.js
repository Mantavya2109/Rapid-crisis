/**
 * simulationController.js
 * ────────────────────────
 * Simulates fire spread and evacuation on the stored building graph.
 *
 * This is the GDG "wow" feature — judges can trigger a live simulation
 * and see projected evacuation times, blocked zones, and capacity analysis
 * without needing physical hardware.
 *
 * ── Simulation Algorithm ──────────────────────────────────────────────
 *   1. Load the building graph from Firestore.
 *   2. Start fire at `startNode`. Mark it blocked (fire tick 0).
 *   3. Each tick (= TICK_SECONDS simulated):
 *      - BFS-spread fire to immediate neighbours with probability SPREAD_PROB.
 *      - Run BFS evacuation from every non-fire node to the nearest exit,
 *        treating fire nodes as blocked.
 *   4. Continue until all exits are blocked OR max ticks reached.
 *   5. Return evacuation timeline, danger zones, safe corridors, and
 *      estimated total evacuation time.
 *
 * POST /simulate
 * {
 *   "buildingId":  "BUILDING_01",
 *   "startNode":   "ROOM_101",
 *   "tickSeconds": 5,        // optional, default 5
 *   "maxTicks":    20        // optional, default 20
 * }
 *
 * Response:
 * {
 *   "simulationId":    "uuid",
 *   "buildingId":      "BUILDING_01",
 *   "startNode":       "ROOM_101",
 *   "totalTimeSec":    35,
 *   "ticksRan":        7,
 *   "evacuatedCount":  12,
 *   "trappedNodes":    ["ROOM_102"],
 *   "fireSpread":      [["ROOM_101"], ["HALLWAY_A"], ...],
 *   "evacuationPaths": { "ROOM_103": ["ROOM_103", "HALLWAY_B", "EXIT_NORTH"] },
 *   "exitLoad":        { "EXIT_NORTH": 8, "EXIT_SOUTH": 4 },
 *   "safeExitsAtEnd":  ["EXIT_SOUTH"],
 *   "verdict":         "PARTIAL_EVACUATION"
 * }
 */

import { randomUUID } from "crypto";

const DEFAULT_TICK_SECONDS = 5;
const DEFAULT_MAX_TICKS    = 20;
const SPREAD_PROBABILITY   = 0.75; // 75% chance fire spreads to a neighbour per tick

// ─────────────────────────────────────────────────────────────────────
// Controller
// ─────────────────────────────────────────────────────────────────────

export const runSimulation = async (req, res) => {
  const {
    buildingId,
    startNode,
    tickSeconds = DEFAULT_TICK_SECONDS,
    maxTicks    = DEFAULT_MAX_TICKS,
  } = req.body || {};

  if (!buildingId || !startNode) {
    return res.status(400).json({ message: "buildingId and startNode are required." });
  }

  // ── 1. Load graph from Firestore ──────────────────────────────────
  let adjacency, exits;
  try {
    const { default: db } = await import("../../config/firebase.js");
    ({ adjacency, exits } = await _loadGraph(db, buildingId));
  } catch (err) {
    return res.status(500).json({ message: `Failed to load building graph: ${err.message}` });
  }

  if (!adjacency[startNode]) {
    return res.status(400).json({
      message: `startNode "${startNode}" not found in the building graph for "${buildingId}".`,
    });
  }
  if (exits.length === 0) {
    return res.status(400).json({
      message: `No EXIT nodes found in building "${buildingId}". Label exit nodes with "EXIT" in their ID.`,
    });
  }

  // ── 2. Run simulation ──────────────────────────────────────────────
  const result = _simulate({ adjacency, exits, startNode, tickSeconds, maxTicks, buildingId });

  // ── 3. Persist simulation result to Firestore (async, non-blocking) ─
  _persistSimulation(buildingId, result).catch((err) =>
    console.error("[Simulation] Failed to persist result:", err.message)
  );

  return res.json(result);
};

// ─────────────────────────────────────────────────────────────────────
// Core Simulation Engine
// ─────────────────────────────────────────────────────────────────────

/**
 * @param {{ adjacency, exits, startNode, tickSeconds, maxTicks, buildingId }} params
 */
function _simulate({ adjacency, exits, startNode, tickSeconds, maxTicks, buildingId }) {
  const simulationId = randomUUID();

  /** Set of currently burning node IDs */
  const fireNodes    = new Set([startNode]);
  /** History: fireSpread[tick] = list of nodes ignited this tick */
  const fireSpread   = [[startNode]];
  /** All nodes in the graph */
  const allNodes     = Object.keys(adjacency);
  const exitSet      = new Set(exits);

  let tick = 0;

  // ── Tick loop ───────────────────────────────────────────────────────
  while (tick < maxTicks) {
    tick++;

    // Spread fire to neighbours
    const newlyIgnited = [];
    for (const burning of [...fireNodes]) {
      const neighbors = adjacency[burning] ?? [];
      for (const neighbor of neighbors) {
        if (!fireNodes.has(neighbor) && Math.random() < SPREAD_PROBABILITY) {
          fireNodes.add(neighbor);
          newlyIgnited.push(neighbor);
        }
      }
    }
    fireSpread.push(newlyIgnited);

    // Check if all exits are blocked
    const safeExits = exits.filter((e) => !fireNodes.has(e));
    if (safeExits.length === 0) break;
  }

  // ── Compute final evacuation paths from all safe nodes ────────────
  const safeNodes        = allNodes.filter((n) => !fireNodes.has(n));
  const safeExitsAtEnd   = exits.filter((e) => !fireNodes.has(e));
  const evacuationPaths  = {};
  const exitLoad         = {};
  const trappedNodes     = [];

  for (const node of safeNodes) {
    if (exitSet.has(node)) continue; // exits don't need to evacuate
    const path = _bfsToExit(adjacency, node, safeExitsAtEnd, fireNodes);
    if (path && path.length > 0) {
      evacuationPaths[node] = path;
      const exit = path[path.length - 1];
      exitLoad[exit] = (exitLoad[exit] ?? 0) + 1;
    } else {
      trappedNodes.push(node);
    }
  }

  const evacuatedCount = Object.keys(evacuationPaths).length;
  const totalTimeSec   = tick * tickSeconds;

  let verdict;
  if (trappedNodes.length === 0 && safeExitsAtEnd.length > 0) {
    verdict = "FULL_EVACUATION";
  } else if (evacuatedCount > 0) {
    verdict = "PARTIAL_EVACUATION";
  } else {
    verdict = "EVACUATION_FAILED";
  }

  return {
    simulationId,
    buildingId,
    startNode,
    totalTimeSec,
    tickSeconds,
    ticksRan: tick,
    evacuatedCount,
    trappedNodes,
    fireSpread,
    evacuationPaths,
    exitLoad,
    safeExitsAtEnd,
    totalFireNodes: fireNodes.size,
    verdict,
    simulatedAt: new Date().toISOString(),
  };
}

// ─────────────────────────────────────────────────────────────────────
// BFS path finder (for simulation only)
// ─────────────────────────────────────────────────────────────────────

/**
 * BFS from startNode to the nearest safe exit, avoiding fire nodes.
 * @returns {string[] | null}
 */
function _bfsToExit(adjacency, startNode, safeExits, fireNodes) {
  const exitSet = new Set(safeExits);
  if (exitSet.has(startNode)) return [startNode];

  const visited = new Set([startNode]);
  const parent  = new Map();
  const queue   = [startNode];

  for (let i = 0; i < queue.length; i++) {
    const current   = queue[i];
    const neighbors = adjacency[current] ?? [];

    for (const next of neighbors) {
      if (fireNodes.has(next) || visited.has(next)) continue;
      visited.add(next);
      parent.set(next, current);

      if (exitSet.has(next)) {
        // Reconstruct path
        const path = [];
        let node = next;
        while (node !== undefined) {
          path.push(node);
          node = parent.get(node);
        }
        return path.reverse();
      }
      queue.push(next);
    }
  }
  return null;
}

// ─────────────────────────────────────────────────────────────────────
// Firestore helpers
// ─────────────────────────────────────────────────────────────────────

async function _loadGraph(db, buildingId) {
  const [nodeSnap, edgeSnap] = await Promise.all([
    db.collection(`buildings/${buildingId}/nodes`).get(),
    db.collection(`buildings/${buildingId}/edges`).get(),
  ]);

  const exits    = [];
  const adjacency = {};

  nodeSnap.docs.forEach((doc) => {
    const { id } = doc.data();
    adjacency[String(id)] = [];
    if (String(id).toUpperCase().includes("EXIT")) exits.push(String(id));
  });

  edgeSnap.docs.forEach((doc) => {
    const { from, to } = doc.data();
    if (!from || !to) return;
    if (adjacency[from]) adjacency[from].push(to);
    if (adjacency[to])   adjacency[to].push(from);
  });

  return { adjacency, exits };
}

async function _persistSimulation(buildingId, result) {
  const { default: db } = await import("../../config/firebase.js");
  await db
    .collection("buildings")
    .doc(buildingId)
    .collection("simulations")
    .doc(result.simulationId)
    .set(result);
}
