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

import fs from "fs";
import path from "path";
import { v2 as cloudinary } from "cloudinary";
import { invalidateBuildingCache } from "./fireController.js";


cloudinary.config({
  cloud_name: process.env.CLOUDINARY_CLOUD_NAME || "difkjrclt",
  api_key:    process.env.CLOUDINARY_API_KEY     || "",
  api_secret: process.env.CLOUDINARY_API_SECRET  || "",
});

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
 * Load sensor nodes from Firestore on server startup to prevent memory loss (H2).
 */
export async function initSensorNodes(db) {
  try {
    const snap = await db.collection("buildings").get();
    const allSensors = snap.docs.flatMap((d) => d.data().sensors || []);
    sensorNodes = [...new Set(allSensors)];
    console.log(`[Building] Initialized ${sensorNodes.length} sensor nodes from DB`);
  } catch (err) {
    console.warn(`[Building] Failed to load sensor nodes from DB: ${err.message}`);
  }
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

    const { buildingId, nodes, edges, sensors, start, images } = req.body || {};

    if (!buildingId || !Array.isArray(nodes) || !Array.isArray(edges)) {
      return res.status(400).json({
        message:
          "Request body must include buildingId, nodes (array), and edges (array).",
      });
    }

    const invalidNode = nodes.find(
      (n) => n?.id === undefined || n?.id === null || typeof n?.floor !== "number" || n?.floor < 0,
    );
    if (invalidNode) {
      return res.status(400).json({
        message: "Each node must have an 'id' and a valid 'floor' (>= 0).",
      });
    }

    const invalidEdge = edges.find(
      (e) =>
        e?.from === undefined ||
        e?.from === null ||
        e?.to === undefined ||
        e?.to === null,
    );
    if (invalidEdge) {
      return res.status(400).json({
        message: "Each edge must have 'from' and 'to' fields.",
      });
    }

    // ── Read existing images BEFORE any delete ───────────────────────
    // Must happen first so Cloudinary URLs are preserved on redeploy
    const existingMeta = await db.collection("buildings").doc(buildingId).get();
    const existingImages = existingMeta.exists ? (existingMeta.data().images || {}) : {};

    // ── REPLACE semantics — wipe old graph ───────────────────────────
    const deleteCollection = async (collectionName) => {
      const batchSize = 400;
      let snap = await db.collection(collectionName).limit(batchSize).get();
      while (!snap.empty) {
        const batch = db.batch();
        snap.docs.forEach((doc) => batch.delete(doc.ref));
        await batch.commit();
        snap = await db.collection(collectionName).limit(batchSize).get();
      }
    };

    await Promise.all([
      db.collection("buildings").doc(buildingId).delete(),
      deleteCollection(`buildings/${buildingId}/nodes`),
      deleteCollection(`buildings/${buildingId}/edges`),
    ]);

    // ── Persist nodes ─────────────────────────────────────────────────
    await Promise.all(
      nodes.map((node) =>
        db
          .collection(`buildings/${buildingId}/nodes`)
          .doc(String(node.id))
          .set({ ...node, buildingId }),
      ),
    );

    // ── Persist edges ─────────────────────────────────────────────────
    await Promise.all(
      edges.map((edge) =>
        db.collection(`buildings/${buildingId}/edges`).add({
          ...edge,
          distance: edge?.distance ?? 1,
          buildingId,
        }),
      ),
    );

    // ── Handle Cloudinary image processing ─────────────────────────────

    
    const finalImages = { ...existingImages };
    if (images && typeof images === "object") {
      const uploadPromises = Object.entries(images).map(async ([floor, base64Str]) => {
        if (!base64Str || typeof base64Str !== "string") return;
        
        // Skip if it's already a Cloudinary URL (not base64)
        if (base64Str.startsWith("http")) {
          finalImages[floor] = base64Str;
          return;
        }
        
        if (!base64Str.startsWith("data:image")) return;
        
        try {
          const uploadResponse = await cloudinary.uploader.upload(base64Str, {
            folder: `rapid_crisis/${buildingId}`,
            public_id: `floor_${floor}`,
            overwrite: true,
            resource_type: "image",
          });
          
          finalImages[floor] = uploadResponse.secure_url;
          console.log(`[Building] Uploaded floor ${floor} image to Cloudinary:`, uploadResponse.secure_url);
        } catch (uploadError) {
          console.error(`[Building] Failed to upload floor ${floor} image to Cloudinary:`, uploadError);
        }
      });
      
      await Promise.all(uploadPromises);
    }

    // ── Update building meta document ─────────────────────────────────
    await db
      .collection("buildings")
      .doc(buildingId)
      .set({
        buildingId,
        nodeCount: nodes.length,
        edgeCount: edges.length,
        ...(start !== undefined ? { start } : {}),
        images: finalImages,
        sensors: Array.isArray(sensors) ? sensors : [],
        updatedAt: new Date().toISOString(),
      });

    // ── Update in-memory sensor node list ─────────────────────────────
    if (Array.isArray(sensors)) {
      sensorNodes = sensors;
    }

    console.log(
      `[Building] Saved ${nodes.length} nodes + ${edges.length} edges for building "${buildingId}"`,
    );

    // ── Invalidate fire controller caches (H1 fix) ────────────────────
    invalidateBuildingCache(buildingId);

    // ── Push graph to Pi (C6 fix — Dashboard→Pi sync) ─────────────────
    _pushGraphToPi(buildingId, nodes, edges).catch((err) =>
      console.warn(`[Building] Pi graph push failed (non-blocking): ${err.message}`)
    );

    return res.status(201).json({
      message: "Building graph replaced successfully.",
      buildingId,
      status: "OK",
    });
  } catch (error) {
    console.error("[Building] Error saving building:", error);
    return res
      .status(500)
      .json({ message: error.message || "Internal server error." });
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

    const [nodeSnap, edgeSnap, metaSnap] = await Promise.all([
      db.collection(`buildings/${buildingId}/nodes`).get(),
      db.collection(`buildings/${buildingId}/edges`).get(),
      db.collection("buildings").doc(buildingId).get(),
    ]);

    const nodes = nodeSnap.docs.map((d) => d.data());
    const edges = edgeSnap.docs.map((d) => d.data());
    const meta = metaSnap.exists ? metaSnap.data() : {};

    return res.json({ 
      buildingId, 
      nodes, 
      edges,
      images: meta.images || {},
      sensors: meta.sensors || [],
    });
  } catch (error) {
    console.error("[Building] Error fetching building:", error);
    return res
      .status(500)
      .json({ message: error.message || "Internal server error." });
  }
};

/**
 * GET /api/buildings
 * Returns all building meta documents from the 'buildings' collection.
 */
export const getAllBuildings = async (req, res) => {
  try {
    const { default: db } = await import("../../config/firebase.js");
    const snap = await db.collection("buildings").get();
    const buildings = snap.docs.map((d) => {
      const data = d.data();
      // Remove volatile fire state fields (M2 fix)
      delete data.activeEvacuation;
      delete data.blockedNodes;
      return data;
    });
    return res.json(buildings);
  } catch (error) {
    console.error("[Building] Error fetching all buildings:", error);
    return res
      .status(500)
      .json({ message: error.message || "Internal server error." });
  }
};

/**
 * DELETE /building/:buildingId
 * Permanently deletes a building and all its Firestore data.
 */
export const deleteBuilding = async (req, res) => {
  try {
    const { default: db } = await import("../../config/firebase.js");
    const { buildingId } = req.params;

    if (!buildingId) {
      return res.status(400).json({ message: "buildingId is required." });
    }

    const deleteCollection = async (collectionPath) => {
      const batchSize = 400;
      let snap = await db.collection(collectionPath).limit(batchSize).get();
      while (!snap.empty) {
        const batch = db.batch();
        snap.docs.forEach((doc) => batch.delete(doc.ref));
        await batch.commit();
        snap = await db.collection(collectionPath).limit(batchSize).get();
      }
    };

    await Promise.all([
      deleteCollection(`buildings/${buildingId}/nodes`),
      deleteCollection(`buildings/${buildingId}/edges`),
    ]);

    await db.collection("buildings").doc(buildingId).delete();

    console.log(`[Building] Deleted building "${buildingId}"`);
    return res.json({ message: `Building "${buildingId}" deleted.`, buildingId });
  } catch (error) {
    console.error("[Building] Error deleting building:", error);
    return res.status(500).json({ message: error.message || "Internal server error." });
  }
};

// ─────────────────────────────────────────────────────────────────────
// Pi Graph Sync — push building topology to Raspberry Pi
// ─────────────────────────────────────────────────────────────────────

/**
 * Convert Firestore nodes+edges into the Pi's adjacency format and POST
 * to the Pi's /graph/update endpoint (app.py:915).
 *
 * Pi format:
 * {
 *   "graph":  { "ROOM_101": ["HALLWAY_A"], "HALLWAY_A": ["ROOM_101", "EXIT"] },
 *   "exits":  ["EXIT_1", "EXIT_2"],
 *   "buildingId": "1"
 * }
 *
 * Non-blocking — failure is logged but doesn't block the HTTP response.
 */
async function _pushGraphToPi(buildingId, nodes, edges) {
  const PI_URL = process.env.PI_BASE_URL;
  if (!PI_URL) {
    console.log("[Building] PI_BASE_URL not configured — skipping Pi graph push.");
    return;
  }

  // Build adjacency map from edges
  const adjacency = {};
  nodes.forEach((n) => {
    adjacency[String(n.id)] = [];
  });
  edges.forEach((e) => {
    const from = String(e.from);
    const to = String(e.to);
    if (!adjacency[from]) adjacency[from] = [];
    if (!adjacency[to]) adjacency[to] = [];
    if (!adjacency[from].includes(to)) adjacency[from].push(to);
    if (!adjacency[to].includes(from)) adjacency[to].push(from);
  });

  // Identify exit nodes
  const exits = nodes
    .filter((n) => String(n.type).toLowerCase() === "exit")
    .map((n) => String(n.id));

  const piPayload = {
    buildingId,
    graph: adjacency,
    exits,
  };

  try {
    const resp = await fetch(`${PI_URL}/graph/update`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": process.env.API_KEY || "",
      },
      body: JSON.stringify(piPayload),
      signal: AbortSignal.timeout(5000),
    });

    if (resp.ok) {
      console.log(`[Building] ✅ Graph pushed to Pi at ${PI_URL} for "${buildingId}"`);
    } else {
      console.warn(`[Building] Pi returned ${resp.status}: ${await resp.text()}`);
    }
  } catch (err) {
    console.warn(`[Building] Pi unreachable at ${PI_URL}: ${err.message}`);
  }
}
