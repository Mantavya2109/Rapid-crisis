/**
 * telemetryRoutes.js
 * ──────────────────
 * Routes for sensor telemetry ingested from the Raspberry Pi.
 *
 * URL contract matches what the Pi's cloud_sync.py sends:
 *   CLOUD_TELEMETRY_URL = {CLOUD_BASE_URL}/telemetry → POST /telemetry
 */

import express from "express";
import {
  ingestTelemetry,
  getNodeState,
  getBuildingState,
} from "../controllers/telemetryController.js";

const router = express.Router();

/** Called by Pi's send_sensor_telemetry() on filtered state-change events. */
router.post("/telemetry", ingestTelemetry);

/** Returns the latest snapshot for a specific node. */
router.get("/telemetry/node/:buildingId/:nodeId", getNodeState);

/** Returns live snapshots for all nodes in a building (dashboard initial load). */
router.get("/telemetry/building/:buildingId", getBuildingState);

export default router;
