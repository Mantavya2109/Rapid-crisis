import { findShortestPathToNearestExit } from "../utils/pathFinder.js";

let sensorNodes = [];

export function getSensorNodes() {
  return sensorNodes;
}

export const saveBuildingAndFindPath = async (req, res) => {
  try {
    const { default: firestore } = await import("../../config/firebase.js");

    const { nodes, edges, sensors, start, startNode, startNodeId } =
      req.body || {};
    const startId = startNodeId || startNode || start;

    if (!Array.isArray(nodes) || !Array.isArray(edges) || !startId) {
      return res.status(400).json({
        message:
          "Request body must include nodes (array), edges (array), and start node",
      });
    }

    const deleteAllDocumentsInCollection = async (collectionName) => {
      const snapshot = await firestore.collection(collectionName).get();
      const deletePromises = snapshot.docs.map((doc) => doc.ref.delete());
      await Promise.all(deletePromises);
    };

    await Promise.all([
      deleteAllDocumentsInCollection("nodes"),
      deleteAllDocumentsInCollection("edges"),
    ]);

    const invalidNode = nodes.find(
      (n) => n?.id === undefined || n?.id === null,
    );
    if (invalidNode) {
      return res.status(400).json({
        message:
          "Each node must include an 'id' field for Firestore document IDs",
      });
    }

    const nodeIds = nodes.map((n) => String(n.id));
    const normalizedStartId = String(startId);
    if (!nodeIds.includes(normalizedStartId)) {
      return res.status(400).json({ message: "Start node not found in nodes" });
    }

    await Promise.all(
      nodes.map((node) =>
        firestore.collection("nodes").doc(String(node.id)).set(node),
      ),
    );

    await Promise.all(
      edges.map((edge) => firestore.collection("edges").add(edge)),
    );

    if (Array.isArray(sensors)) {
      sensorNodes = sensors;
    }

    const edgesForPath = edges
      .filter((e) => e && e.from && e.to)
      .map((e) => ({ from: e.from, to: e.to }));

    const blockedNodes = [];
    const path = findShortestPathToNearestExit(
      normalizedStartId,
      edgesForPath,
      blockedNodes,
    );

    return res.json({
      message: "Building data saved",
      nodesSaved: nodes.length,
      edgesSaved: edges.length,
      safePath: path,
      status: "OK",
    });
  } catch (error) {
    return res.status(500).json({ message: error.message || "Server error" });
  }
};
