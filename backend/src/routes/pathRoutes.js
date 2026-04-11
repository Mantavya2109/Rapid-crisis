import express from "express";
import Edge from "../models/Edge.js";
import { getBlockedNodes } from "../controllers/fireController.js";
import { findShortestPathToNearestExit } from "../utils/pathFinder.js";

const router = express.Router();

// GET /api/path?start=NODE_ID
router.get("/", async (req, res) => {
  try {
    const startNodeId = req.query.start;

    if (!startNodeId) {
      return res.status(400).json({ message: "start query param is required" });
    }

    const edgesFromDb = await Edge.find({}, { from: 1, to: 1, _id: 0 }).lean();
    const edges = edgesFromDb.map((e) => ({ from: e.from, to: e.to }));

    const blockedNodes = getBlockedNodes();
    const path = findShortestPathToNearestExit(
      startNodeId,
      edges,
      blockedNodes,
    );

    if (!path || path.length === 0) {
      return res.status(404).json({
        message: "No safe path available",
        status: "BLOCKED",
        blocked: blockedNodes,
        start: startNodeId,
      });
    }

    return res.json({
      start: startNodeId,
      blocked: blockedNodes,
      safePath: path,
      status: "OK",
    });
  } catch (error) {
    return res.status(500).json({ message: error.message || "Server error" });
  }
});

export default router;
