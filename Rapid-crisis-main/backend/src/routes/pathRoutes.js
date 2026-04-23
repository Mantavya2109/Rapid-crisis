import express from "express";
import { getBlockedNodes } from "../controllers/fireController.js";
import { findShortestPathToNearestExit } from "../utils/pathFinder.js";

const router = express.Router();

const fetchEdgesFromFirestore = async () => {
  const { default: firestore } = await import("../../config/firebase.js");
  const snapshot = await firestore.collection("edges").get();

  return snapshot.docs
    .map((doc) => doc.data())
    .filter((e) => e && e.from !== undefined && e.to !== undefined)
    .map((e) => ({ from: e.from, to: e.to }));
};

// Returns: [{ from, to }, ...]
router.get("/edges", async (req, res) => {
  try {
    const edges = await fetchEdgesFromFirestore();
    return res.json(edges);
  } catch (error) {
    return res.status(500).json({ message: error.message || "Server error" });
  }
});

router.get("/", async (req, res) => {
  try {
    const startNodeId = req.query.start;

    if (!startNodeId) {
      return res.status(400).json({ message: "start query param is required" });
    }

    const edges = await fetchEdgesFromFirestore();

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
