/**
 * telemetryController.js
 * ──────────────────────
 * Ingests filtered sensor telemetry from the Raspberry Pi.
 *
 * ── What this controller does ─────────────────────────────────────────
 *  1. Validates incoming payload
 *  2. Runs TelemetryAnalyzer for pre-threshold anomaly detection
 *  3. Updates AI engine's node state cache (for next fire-alert scoring)
 *  4. Immediately writes live node_states snapshot to Firestore (dashboard)
 *  5. Queues historical time-series write for batched commit (write-efficient)
 *  6. Emits "telemetry:received" event on bus (→ SSE clients)
 *  7. Returns OK + any detected anomaly back to Pi (Pi-backend feedback loop)
 *
 * ── Pi integration contract ───────────────────────────────────────────
 *  Called by: cloud_sync.send_sensor_telemetry()
 *  URL:       POST {CLOUD_BASE_URL}/telemetry
 *
 *  The Pi reads the response — if anomalyDetected is true,
 *  it can pre-emptively log a WARNING before threshold is breached.
 */

import { bus }         from "../services/eventBus.js";
import { analyze }     from "../services/telemetryAnalyzer.js";
import { enqueue }     from "../services/firestoreWriteQueue.js";
import { updateNodeStateCache } from "./fireController.js";

export const ingestTelemetry = async (req, res) => {
  const {
    buildingId,
    nodeId,
    deviceId,
    temperature,
    smoke,
    status,
    stateChanged  = false,
    smokeRiseRate = 0,
    tempRiseRate  = 0,
    raw,
  } = req.body || {};

  // ── Validate ─────────────────────────────────────────────────────────
  if (!buildingId || !nodeId || !deviceId) {
    return res.status(400).json({ message: "buildingId, nodeId, and deviceId are required." });
  }
  if (temperature === undefined || smoke === undefined) {
    return res.status(400).json({ message: "temperature and smoke are required." });
  }

  const timestamp = new Date().toISOString();

  // ── 1. Anomaly detection ─────────────────────────────────────────────
  const anomaly = analyze({ nodeId, temperature, smoke, smokeRiseRate, tempRiseRate });

  if (anomaly) {
    console.warn(
      `[Telemetry] ⚠ Anomaly at ${buildingId}/${nodeId}: ${anomaly.anomalyType} (${anomaly.severity})`
    );
    bus.fire("anomaly:detected", { buildingId, ...anomaly });
  }

  // ── 2. Update AI engine node state cache ─────────────────────────────
  updateNodeStateCache(nodeId, { nodeId, temperature, smoke, smokeRiseRate, tempRiseRate, status });

  // ── 3. Live Firestore snapshot (immediate write — dashboard depends on it) ─
  try {
    const { default: db } = await import("../../config/firebase.js");

    // This is a single SET — cheap, fast, overwrites previous snapshot
    await db
      .collection("buildings")
      .doc(buildingId)
      .collection("node_states")
      .doc(nodeId)
      .set({ nodeId, deviceId, temperature, smoke, status, smokeRiseRate, tempRiseRate, updatedAt: timestamp },
           { merge: true });

    // ── 4. Batch the historical telemetry record ─────────────────────
    const histRef = db
      .collection("buildings")
      .doc(buildingId)
      .collection("telemetry")
      .doc(); // auto-ID

    enqueue(histRef, {
      id: histRef.id,
      buildingId, nodeId, deviceId,
      temperature, smoke, status,
      stateChanged, smokeRiseRate, tempRiseRate,
      timestamp,
      ...(raw ? { raw } : {}),
    });
  } catch (err) {
    console.error("[Telemetry] Firestore write error:", err.message);
    // Don't fail the Pi response on DB error
  }

  // ── 5. Event bus ─────────────────────────────────────────────────────
  bus.fire("telemetry:received", {
    buildingId, nodeId, temperature, smoke, status, stateChanged,
    ...(anomaly ?? {}),
  });

  console.log(
    `[Telemetry] ${buildingId}/${nodeId} — ${status} | ` +
    `temp=${temperature}°C smoke=${smoke}ppm | anomaly=${anomaly ? anomaly.anomalyType : "none"}`
  );

  // ── 6. Respond to Pi with anomaly info (Pi-backend feedback loop) ─────
  return res.status(201).json({
    status:    "OK",
    nodeId,
    timestamp,
    ...(anomaly ? { anomaly } : {}),
  });
};

// ─────────────────────────────────────────────────────────────────────
// GET endpoints (dashboard reads)
// ─────────────────────────────────────────────────────────────────────

export const getNodeState = async (req, res) => {
  try {
    const { default: db } = await import("../../config/firebase.js");
    const { buildingId, nodeId } = req.params;
    const doc = await db
      .collection("buildings").doc(buildingId)
      .collection("node_states").doc(nodeId).get();
    if (!doc.exists) return res.status(404).json({ message: `No state for node "${nodeId}".` });
    return res.json(doc.data());
  } catch (e) {
    return res.status(500).json({ message: e.message });
  }
};

export const getBuildingState = async (req, res) => {
  try {
    const { default: db } = await import("../../config/firebase.js");
    const { buildingId } = req.params;
    const snap = await db
      .collection("buildings").doc(buildingId)
      .collection("node_states").get();
    const nodes = snap.docs.map((d) => d.data());
    return res.json({ buildingId, nodes, count: nodes.length });
  } catch (e) {
    return res.status(500).json({ message: e.message });
  }
};
