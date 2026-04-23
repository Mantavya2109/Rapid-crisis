/**
 * fireController.js
 * ─────────────────
 * Handles fire events sent by the Raspberry Pi.
 *
 * ── What this controller does ─────────────────────────────────────────
 *  1. Receives structured fire alert from Pi (POST /fire-alert)
 *  2. Runs the AI hazard engine on the full building state
 *  3. Runs the evacuation optimizer for exit overload detection
 *  4. Persists fire event + live state to Firestore
 *  5. Broadcasts fire event on the internal event bus (→ SSE clients)
 *  6. Returns rich intelligence payload to Pi (hazard_weights + hints)
 *
 * ── Pi integration contract ───────────────────────────────────────────
 *  Called by: cloud_sync.send_structured_fire_alert()
 *  URL:       POST {CLOUD_BASE_URL}/fire-alert
 *
 *  Request:
 *  { buildingId, startNodes, blocked_nodes, sensor_data }
 *
 *  Response (Pi reads hazard_weights + evacuation_hints):
 *  {
 *    "status":         "ACK",
 *    "hazard_weights": { "ROOM_102": 72.5, "HALLWAY_A": 28.0 },
 *    "evacuation_hints": {
 *      "overloadedExits":   ["EXIT_A"],
 *      "exitHazardWeights": { "EXIT_A": 80 }
 *    },
 *    "global_blocked":  ["ROOM_101"],
 *    "timestamp":       "2026-04-23T..."
 *  }
 */

import { bus } from "../services/eventBus.js";
import {
  computeAllRiskScores,
  riskScoresToHazardWeights,
  registerFireNode,
  clearFireNode,
} from "../services/aiHazardEngine.js";
import {
  recordExitUsage,
  getOptimizationHints,
  resetAll as resetOptimizer,
} from "../services/evacuationOptimizer.js";

/** @type {Set<string>} Global blocked nodes (accumulates until clear) */
const _blockedNodes = new Set();

/** @type {Map<string, object>} In-memory node state cache for AI engine */
const _nodeStateCache = new Map();

// ─────────────────────────────────────────────────────────────────────
// POST /fire-alert
// ─────────────────────────────────────────────────────────────────────

export const handleFireAlert = async (req, res) => {
  const {
    buildingId,
    blocked_nodes = [],
    startNodes    = [],
    sensor_data   = {},
  } = req.body || {};

  if (!buildingId)                  return res.status(400).json({ message: "buildingId is required." });
  if (!startNodes.length)           return res.status(400).json({ message: "startNodes is required." });

  // ── 1. Update blocked list + AI fire registry ───────────────────────
  blocked_nodes.forEach((n) => { _blockedNodes.add(n); registerFireNode(n); });
  startNodes.forEach((n)    => { _blockedNodes.add(n); registerFireNode(n); });

  // ── 2. Update node state cache from incoming sensor data ────────────
  startNodes.forEach((nodeId) => {
    _nodeStateCache.set(nodeId, {
      nodeId,
      temperature:  sensor_data.temperature ?? 0,
      smoke:        sensor_data.smoke       ?? 0,
      smokeRiseRate: 0,
      tempRiseRate:  0,
      status:       sensor_data.status      ?? "FIRE",
    });
  });

  // ── 3. Run AI hazard engine on entire building ─────────────────────
  const nodeStates = Object.fromEntries(_nodeStateCache);
  const adjacency  = await _fetchAdjacency(buildingId);

  const riskScores    = computeAllRiskScores({ nodeStates, adjacency });
  const hazardWeights = riskScoresToHazardWeights(riskScores);

  // ── 4. Evacuation optimizer — detect overloaded exits ─────────────
  // We don't know specific exits per path yet, so record nodes against
  // all exits they might flow toward (optimizer picks up from here)
  recordExitUsage(startNodes, _nearestExitHint(adjacency, startNodes[0]));
  const evacuationHints = getOptimizationHints();

  // Merge exit penalties into hazard weights
  Object.assign(hazardWeights, evacuationHints.exitHazardWeights);

  // ── 5. Emit event for SSE dashboard ───────────────────────────────
  bus.fire("fire:detected", {
    buildingId,
    startNodes,
    blockedNodes: Array.from(_blockedNodes),
    riskScores,
    evacuationHints,
  });

  // ── 6. Persist to Firestore (non-blocking) ─────────────────────────
  _persistFireEvent(buildingId, {
    startNodes,
    blockedNodes: Array.from(_blockedNodes),
    sensorData: sensor_data,
    riskScores,
    evacuationHints,
  }).catch((e) => console.error("[Fire] Firestore write error:", e.message));

  // ── 7. Respond to Pi ───────────────────────────────────────────────
  return res.json({
    status:           "ACK",
    hazard_weights:   hazardWeights,   // Pi merges these into Dijkstra
    risk_scores:      riskScores,      // for dashboard display
    evacuation_hints: evacuationHints, // Pi adds exit penalties to graph
    global_blocked:   Array.from(_blockedNodes),
    timestamp:        new Date().toISOString(),
  });
};

// ─────────────────────────────────────────────────────────────────────
// POST /fire-alert/clear
// ─────────────────────────────────────────────────────────────────────

export const clearFireState = async (req, res) => {
  const { buildingId } = req.body || {};
  if (!buildingId) return res.status(400).json({ message: "buildingId is required." });

  // Clear all fire state
  [..._blockedNodes].forEach((n) => clearFireNode(n));
  _blockedNodes.clear();
  _nodeStateCache.clear();
  resetOptimizer();

  bus.fire("fire:cleared", { buildingId });

  _persistClear(buildingId).catch((e) =>
    console.error("[Fire] Failed to clear Firestore state:", e.message)
  );

  return res.json({ status: "CLEARED", buildingId });
};

// ─────────────────────────────────────────────────────────────────────
// GET /fire-alert/state/:buildingId
// ─────────────────────────────────────────────────────────────────────

export const getFireState = async (req, res) => {
  try {
    const { default: db } = await import("../../config/firebase.js");
    const { buildingId }  = req.params;
    const doc = await db.collection("buildings").doc(buildingId).get();
    return res.json(doc.exists ? doc.data() : { buildingId, activeEvacuation: false, blockedNodes: [] });
  } catch (e) {
    return res.status(500).json({ message: e.message });
  }
};

export const getBlockedNodes = () => Array.from(_blockedNodes);

/**
 * Export live node state cache so telemetryController can update it.
 * @param {string} nodeId
 * @param {object} state
 */
export function updateNodeStateCache(nodeId, state) {
  _nodeStateCache.set(nodeId, state);
}

// ─────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────

/** In-memory adjacency cache { buildingId → adjacency } */
const _adjCache = new Map();

async function _fetchAdjacency(buildingId) {
  if (_adjCache.has(buildingId)) return _adjCache.get(buildingId);

  try {
    const { default: db } = await import("../../config/firebase.js");
    const edgeSnap = await db.collection(`buildings/${buildingId}/edges`).get();
    const adj = {};
    edgeSnap.docs.forEach((doc) => {
      const { from, to } = doc.data();
      if (!from || !to) return;
      if (!adj[from]) adj[from] = [];
      if (!adj[to])   adj[to]   = [];
      adj[from].push(to);
      adj[to].push(from);
    });
    _adjCache.set(buildingId, adj);
    return adj;
  } catch {
    return {};
  }
}

/** Simple heuristic: pick a neighbor that looks like an exit */
function _nearestExitHint(adjacency, startNode) {
  if (!startNode) return null;
  const neighbors = adjacency[startNode] ?? [];
  return neighbors.find((n) => n.toUpperCase().includes("EXIT")) ?? null;
}

async function _persistFireEvent(buildingId, data) {
  const { default: db } = await import("../../config/firebase.js");
  const ref = db.collection("buildings").doc(buildingId).collection("fire_events").doc();
  await ref.set({ id: ref.id, ...data, timestamp: new Date().toISOString() });
  await db.collection("buildings").doc(buildingId).set(
    { activeEvacuation: true, blockedNodes: data.blockedNodes, updatedAt: new Date().toISOString() },
    { merge: true }
  );
}

async function _persistClear(buildingId) {
  const { default: db } = await import("../../config/firebase.js");
  await db.collection("buildings").doc(buildingId).set(
    { activeEvacuation: false, blockedNodes: [], updatedAt: new Date().toISOString() },
    { merge: true }
  );
}
