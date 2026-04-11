import { findShortestPathToNearestExit } from "../utils/pathFinder.js";

const blockedNodes = [];

/*
  Fire alert handler
  - Expects: { nodeId, edges }
  - Adds nodeId to the blocked list
  - Returns the shortest safe path to the nearest EXIT
 */
export const handleFireAlert = (req, res) => {
  const { nodeId, edges = [] } = req.body || {};

  if (!nodeId) {
    return res.status(400).json({ message: "nodeId is required" });
  }

  if (!blockedNodes.includes(nodeId)) {
    blockedNodes.push(nodeId);
  }

  // Allow starting from the alert node, but treat it as blocked for all other traversals.
  const blockedForSearch = blockedNodes.filter((id) => id !== nodeId);
  const path = findShortestPathToNearestExit(nodeId, edges, blockedForSearch);

  return res.json({
    status: "FIRE",
    blockedNodes,
    path,
  });
};

// Optional helper if other parts of the app need to read/reset blocked nodes
export const getBlockedNodes = () => blockedNodes;
