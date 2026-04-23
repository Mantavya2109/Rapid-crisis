/**
 * buildingController.js
 * ─────────────────────
 * Stores the building graph (nodes + edges) to Firestore.
 *
 * Responsibilities of THIS controller (backend):
 *   ✅ Persist building topology to Firestore so the dashboard can render it
 *   ✅ Expose graph data to the frontend digital-twin visualization
 *
 * Responsibilities deliberately NOT here (belongs to Raspberry Pi edge):
 *   ❌ Pathfinding — the Pi runs Dijkstra locally in real-time
 *   ❌ Fire state management — the Pi owns live sensor state
 */

/** @type {string[]} */
let sensorNodes = [];

/**
 * Returns the current list of node IDs that have sensors attached.
 * Used by fireController to validate incoming alerts.
 * @returns {string[]}
 */
export function getSensorNodes() {
  return sensorNodes;
}

/**
 * POST /api/building/setup
 * Saves the building graph (nodes, edges, sensor list) to Firestore.
 * Called once when the building is configured, not on every sensor tick.
 *
 * Expected body:
 * {
 *   "buildingId": "BUILDING_01",
 *   "nodes":   [{ "id": "ROOM_101", "label": "Room 101", "type": "room", "floor": 1 }],
 *   "edges":   [{ "from": "ROOM_101", "to": "HALLWAY_A" }],
 *   "sensors": ["ROOM_101", "HALLWAY_A"]
 * }
 */
export const setupBuilding = async (req, res) => {
  try {
    const { default: db } = await import("../../config/firebase.js");

    const { buildingId, nodes, edges, sensors } = req.body || {};

    if (!buildingId || !Array.isArray(nodes) || !Array.isArray(edges)) {
      return res.status(400).json({
        message: "Request body must include buildingId, nodes (array), and edges (array).",
      });
    }

    const invalidNode = nodes.find((n) => n?.id === undefined || n?.id === null);
    if (invalidNode) {
      return res.status(400).json({
        message: "Each node must have an 'id' field.",
      });
    }

    // ── Wipe previous graph for this building ─────────────────────────
    const deleteCollection = async (collectionName) => {
      const snap = await db.collection(collectionName).get();
      await Promise.all(snap.docs.map((doc) => doc.ref.delete()));
    };

    await Promise.all([
      deleteCollection(`buildings/${buildingId}/nodes`),
      deleteCollection(`buildings/${buildingId}/edges`),
    ]);

    // ── Persist nodes ─────────────────────────────────────────────────
    await Promise.all(
      nodes.map((node) =>
        db
          .collection(`buildings/${buildingId}/nodes`)
          .doc(String(node.id))
          .set({ ...node, buildingId })
      )
    );

    // ── Persist edges ─────────────────────────────────────────────────
    await Promise.all(
      edges.map((edge) =>
        db.collection(`buildings/${buildingId}/edges`).add({ ...edge, buildingId })
      )
    );

    // ── Update building meta document ─────────────────────────────────
    await db.collection("buildings").doc(buildingId).set(
      {
        buildingId,
        nodeCount: nodes.length,
        edgeCount: edges.length,
        updatedAt: new Date().toISOString(),
      },
      { merge: true }
    );

    // ── Update in-memory sensor node list ─────────────────────────────
    if (Array.isArray(sensors)) {
      sensorNodes = sensors;
    }

    console.log(
      `[Building] Saved ${nodes.length} nodes + ${edges.length} edges for building "${buildingId}"`
    );

    return res.status(201).json({
      message: "Building graph saved successfully.",
      buildingId,
      nodesSaved: nodes.length,
      edgesSaved: edges.length,
      status: "OK",
    });
  } catch (error) {
    console.error("[Building] Error saving building:", error);
    return res.status(500).json({ message: error.message || "Internal server error." });
  }
};

/**
 * GET /api/building/:buildingId
 * Returns the full graph for a building — used by the digital-twin dashboard.
 */
export const getBuilding = async (req, res) => {
  try {
    const { default: db } = await import("../../config/firebase.js");
    const { buildingId } = req.params;

    if (!buildingId) {
      return res.status(400).json({ message: "buildingId is required." });
    }

    const [nodeSnap, edgeSnap] = await Promise.all([
      db.collection(`buildings/${buildingId}/nodes`).get(),
      db.collection(`buildings/${buildingId}/edges`).get(),
    ]);

    const nodes = nodeSnap.docs.map((d) => d.data());
    const edges = edgeSnap.docs.map((d) => d.data());

    return res.json({ buildingId, nodes, edges });
  } catch (error) {
    console.error("[Building] Error fetching building:", error);
    return res.status(500).json({ message: error.message || "Internal server error." });
  }
};
