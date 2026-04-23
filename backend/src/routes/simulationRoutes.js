import express from "express";
import { runSimulation } from "../controllers/simulationController.js";

const router = express.Router();

/**
 * POST /simulate
 * Body: { buildingId, startNode, tickSeconds?, maxTicks? }
 */
router.post("/simulate", runSimulation);

export default router;
