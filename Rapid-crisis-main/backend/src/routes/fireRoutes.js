/**
 * fireRoutes.js
 * ─────────────
 * Routes for fire events sent by the Raspberry Pi.
 *
 * URL contract matches what the Pi's cloud_sync.py sends:
 *   CLOUD_API_URL = {CLOUD_BASE_URL}/fire-alert   → POST /fire-alert
 */

import express from "express";
import {
  handleFireAlert,
  getFireState,
  clearFireState,
} from "../controllers/fireController.js";

const router = express.Router();

/** Called by Pi's send_structured_fire_alert() when a fire is detected. */
router.post("/fire-alert", handleFireAlert);

/** Called by Pi's recovery flow when all-clear is declared. */
router.post("/fire-alert/clear", clearFireState);

/** Returns current fire state for the dashboard. */
router.get("/fire-alert/state/:buildingId", getFireState);

export default router;
