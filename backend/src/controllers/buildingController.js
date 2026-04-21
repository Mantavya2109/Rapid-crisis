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

    if (sensors !== undefined && !Array.isArray(sensors)) {
      return res
        .status(400)
        .json({ message: "sensors must be an array of node IDs" });
    }

    const invalidNode = nodes.find(
      (n) => !n || n.id === undefined || n.id === null,
    );
    if (invalidNode) {
      return res.status(400).json({
        message:
          "Each node must include an 'id' field for Firestore document IDs",
      });
    }

    await Promise.all(
      nodes.map((node) =>
        firestore.collection("nodes").doc(String(node.id)).set(node),
      ),
    );

    await Promise.all(
      edges.map((edge) => firestore.collection("edges").add(edge)),
    );

    sensorNodes = sensors || [];

    const edgesForPath = edges
      .filter((e) => e && e.from && e.to)
      .map((e) => ({ from: e.from, to: e.to }));

    const blockedNodes = [];
    const path = findShortestPathToNearestExit(
      startId,
      edgesForPath,
      blockedNodes,
    );

    return res.json({
      savedNodesCount: nodes.length,
      savedEdgesCount: edges.length,
      path,
    });
  } catch (error) {
    return res.status(500).json({ message: error.message || "Server error" });
  }
};
