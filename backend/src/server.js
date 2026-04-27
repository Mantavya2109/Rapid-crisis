/**
 * server.js
 * ─────────
 * Smart Fire Evacuation System — Cloud Backend
 *
 * ── Architecture ──────────────────────────────────────────────────────
 *
 *  ESP32 → Pi → POST /fire-alert  → AI Engine → hazard_weights → Pi → Dijkstra → LED
 *                POST /telemetry  → Analyzer  → anomaly        → Pi → early warning
 *
 *  Backend → SSE /events → Dashboard (real-time digital twin)
 *
 *  POST /simulate → Simulation Engine → evacuation report
 *
 * ── Routes ────────────────────────────────────────────────────────────
 *  POST /fire-alert              Pi fire event      → hazard_weights + hints
 *  POST /fire-alert/clear        Pi all-clear
 *  GET  /fire-alert/state/:id    Dashboard
 *  POST /telemetry               Pi sensor reading  → anomaly feedback
 *  GET  /telemetry/node/:b/:n    Dashboard
 *  GET  /telemetry/building/:b   Dashboard initial load
 *  POST /building/setup          Building graph setup
 *  GET  /building/:id            Dashboard graph
 *  POST /simulate                GDG demo: fire simulation
 *  GET  /events                  SSE stream → real-time dashboard
 *  GET  /health                  Health check
 */

import express from "express";
import cors    from "cors";
import dotenv  from "dotenv";

dotenv.config();

import fireRoutes        from "./routes/fireRoutes.js";
import telemetryRoutes   from "./routes/telemetryRoutes.js";
import buildingRoutes    from "./routes/buildingRoutes.js";
import simulationRoutes  from "./routes/simulationRoutes.js";
import { bus }           from "./services/eventBus.js";
import { initWriteQueue, flushNow } from "./services/firestoreWriteQueue.js";
import { requireApiKey } from "./middleware/auth.js";

const app  = express();
const PORT = process.env.PORT || 3000;

// ── Middleware ─────────────────────────────────────────────────────────
const ALLOWED_ORIGINS = [
  "http://localhost:5173",
  "http://localhost:3000",
  process.env.FRONTEND_URL,         // production frontend
].filter(Boolean);

app.use(cors({
  origin: (origin, cb) => {
    // Allow requests with no origin (Pi, Postman, curl)
    if (!origin || ALLOWED_ORIGINS.includes(origin)) return cb(null, true);
    cb(null, true); // Allow all for now — restrict in production
  },
  methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
}));
app.use(express.json({ limit: "50mb" }));
app.use(express.urlencoded({ extended: true, limit: "50mb" }));
app.use("/uploads", express.static("uploads"));

// ── Initialise Firestore write queue ──────────────────────────────────
// Import db lazily to avoid crashing on missing serviceAccountKey in dev
let _dbReady = false;
import("../config/firebase.js")
  .then(({ default: db }) => { 
    initWriteQueue(db); 
    import("./controllers/buildingController.js").then((m) => m.initSensorNodes(db));
    _dbReady = true; 
  })
  .catch((err) => console.warn("[Server] Firebase not ready:", err.message));

// ─────────────────────────────────────────────────────────────────────
// SSE (Server-Sent Events) — real-time dashboard push
// ─────────────────────────────────────────────────────────────────────

/** @type {Set<express.Response>} Active SSE connections */
const _sseClients = new Set();

/**
 * GET /events
 * Dashboard subscribes here and receives live JSON events pushed by the server.
 * Uses standard SSE (text/event-stream) — no extra libraries needed.
 */
app.get("/events", (req, res) => {
  res.setHeader("Content-Type",  "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection",    "keep-alive");
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.flushHeaders();

  // Send a connection confirmation
  _sendSse(res, "connected", { message: "SSE stream established." });

  _sseClients.add(res);
  console.log(`[SSE] Client connected. Total: ${_sseClients.size}`);

  req.on("close", () => {
    _sseClients.delete(res);
    console.log(`[SSE] Client disconnected. Total: ${_sseClients.size}`);
  });
});

// ── Wire event bus → SSE broadcast ────────────────────────────────────
const SSE_EVENTS = [
  "fire:detected",
  "fire:cleared",
  "telemetry:received",
  "anomaly:detected",
  "evacuation:reroute",
  "intelligence:ready",
  "system:mode",
];

SSE_EVENTS.forEach((eventName) => {
  bus.on(eventName, (envelope) => _broadcastSse(eventName, envelope.payload));
});

// ── Helpers ───────────────────────────────────────────────────────────
function _sendSse(res, event, data) {
  res.write(`event: ${event}\n`);
  res.write(`data: ${JSON.stringify(data)}\n\n`);
}

function _broadcastSse(event, data) {
  if (_sseClients.size === 0) return;
  _sseClients.forEach((client) => {
    try { _sendSse(client, event, data); }
    catch { _sseClients.delete(client); }
  });
}

// ─────────────────────────────────────────────────────────────────────
// Routes
// ─────────────────────────────────────────────────────────────────────
app.get("/health", (_req, res) =>
  res.json({
    service:    "Smart Fire Evacuation — Cloud Backend",
    status:     "operational",
    version:    "3.0.0",
    dbReady:    _dbReady,
    sseClients: _sseClients.size,
    ts:         new Date().toISOString(),
  })
);

// Pi endpoints (URLs must match CLOUD_BASE_URL/* in Pi settings.py)
// All write operations require API key; reads are public for dashboard
app.use("/", requireApiKey, fireRoutes);
app.use("/", requireApiKey, telemetryRoutes);
app.use("/", requireApiKey, buildingRoutes);
app.use("/", requireApiKey, simulationRoutes);

// ── Pi-expected stub routes ────────────────────────────────────────────
// The Pi's cloud_sync.py sends to these endpoints; provide basic handlers

/** POST /led/batch — Pi sends LED commands for cloud-side routing */
app.post("/led/batch", requireApiKey, (req, res) => {
  const { commands } = req.body || {};
  console.log(`[LED] Received ${(commands || []).length} LED commands from Pi`);
  bus.fire("evacuation:reroute", { commands });
  res.json({ status: "ACK", received: (commands || []).length });
});

/** POST /devices/register — Pi registers ESP32 devices */
app.post("/devices/register", requireApiKey, (req, res) => {
  const { deviceId, nodeId, buildingId } = req.body || {};
  console.log(`[Devices] Registered ${deviceId} → ${nodeId} (${buildingId})`);
  res.status(201).json({ status: "REGISTERED", deviceId, nodeId });
});

// ── 404 + Error handlers ──────────────────────────────────────────────
app.use((_req, res) => res.status(404).json({ message: "Endpoint not found." }));
app.use((err, _req, res, _next) => {
  console.error("[Server] Unhandled error:", err);
  res.status(500).json({ message: "Internal server error." });
});

// ── Start ──────────────────────────────────────────────────────────────
const server = app.listen(PORT, () => {
  console.log("═".repeat(60));
  console.log("  🔥 Smart Fire Evacuation — Cloud Backend v3.0.0");
  console.log(`  Listening  : http://localhost:${PORT}`);
  console.log(`  Pi alerts  : POST http://localhost:${PORT}/fire-alert`);
  console.log(`  Pi telemetry: POST http://localhost:${PORT}/telemetry`);
  console.log(`  Simulation : POST http://localhost:${PORT}/simulate`);
  console.log(`  Dashboard  : GET  http://localhost:${PORT}/events (SSE)`);
  console.log("═".repeat(60));
});

// ── Graceful shutdown ──────────────────────────────────────────────────
async function _shutdown(signal) {
  console.log(`\n[Server] ${signal} received — flushing writes and shutting down…`);
  await flushNow();
  server.close(() => { console.log("[Server] Goodbye."); process.exit(0); });
}
process.on("SIGTERM", () => _shutdown("SIGTERM"));
process.on("SIGINT",  () => _shutdown("SIGINT"));
