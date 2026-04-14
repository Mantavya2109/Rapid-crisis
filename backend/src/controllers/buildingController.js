import Node from "../models/Node.js";
import Edge from "../models/Edge.js";
import { findShortestPathToNearestExit } from "../utils/pathFinder.js";

let sensorNodes = [];

export function getSensorNodes() {
  return sensorNodes;
}

export const saveBuildingAndFindPath = async (req, res) => {
  try {
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

    await Node.deleteMany({});
    await Edge.deleteMany({});
    const savedNodes = await Node.insertMany(nodes);
    const savedEdges = await Edge.insertMany(edges);

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
      savedNodesCount: savedNodes.length,
      savedEdgesCount: savedEdges.length,
      path,
    });
  } catch (error) {
    return res.status(500).json({ message: error.message || "Server error" });
  }
};
