/**
 * buildingRoutes.js
 * ─────────────────
 * Routes for managing building topology (graph setup + digital twin reads).
 */

import express from "express";
import { setupBuilding, getBuilding, getAllBuildings, deleteBuilding } from "../controllers/buildingController.js";

const router = express.Router();

/** Returns all building graphs. */
router.get("/buildings", getAllBuildings);

/** Saves building graph (nodes, edges) to Firestore. Called once at setup. */
router.post("/building/setup", setupBuilding);

/** Returns the full building graph for the dashboard digital twin. */
router.get("/building/:buildingId", getBuilding);

/** Deletes a building and all its data. */
router.delete("/building/:buildingId", deleteBuilding);

export default router;
