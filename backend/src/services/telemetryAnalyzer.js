/**
 * telemetryAnalyzer.js
 * ────────────────────
 * Maintains a per-node sliding window of recent sensor readings and
 * detects anomalies BEFORE hard thresholds are breached.
 *
 * This is the "early warning" layer. The Pi sends only meaningful state
 * changes, so each received reading is a significant data point.
 *
 * ── Detects ───────────────────────────────────────────────────────────
 *   1. SLOW_SMOKE_RISE    – smoke increasing steadily below threshold
 *   2. SLOW_TEMP_RISE     – temperature creeping upward below threshold
 *   3. SUSTAINED_ELEVATED – readings persistently above a soft limit
 *   4. SUDDEN_SPIKE       – single jump > SPIKE_THRESHOLD vs last reading
 *
 * ── Output (returned to Pi in telemetry response) ────────────────────
 *   {
 *     anomalyDetected: true,
 *     anomalyType:     "SLOW_SMOKE_RISE",
 *     severity:        "WARNING",           // "INFO" | "WARNING" | "CRITICAL"
 *     nodeId:          "ROOM_101",
 *     details:         { smokeRiseRate: 8.5, trend: "RISING" }
 *   }
 */

// ── Configuration ──────────────────────────────────────────────────────
const WINDOW_SIZE             = 6;    // readings to keep per node
const SOFT_TEMP_LIMIT_C       = 32.0; // °C — sustained elevation below hard threshold
const SOFT_SMOKE_LIMIT_PPM    = 120.0;// ppm — sustained elevation below hard threshold
const SLOW_SMOKE_RATE_WARNING = 4.0;  // ppm/reading — creeping smoke
const SLOW_TEMP_RATE_WARNING  = 1.5;  // °C/reading  — creeping temp
const SPIKE_SMOKE_THRESHOLD   = 80.0; // ppm jump in one reading = sudden spike
const SPIKE_TEMP_THRESHOLD    = 8.0;  // °C  jump in one reading = sudden spike
const SUSTAINED_READINGS      = 4;    // how many readings above soft limit = "sustained"

// ── In-memory sliding windows ──────────────────────────────────────────
// { nodeId → Array<{ temperature, smoke, ts }> }
const _windows = new Map();

// ─────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────

/**
 * Feed a new reading into the analyzer for a given node.
 * Returns an anomaly descriptor if something suspicious is found, or null.
 *
 * @param {{
 *   nodeId:       string,
 *   temperature:  number,
 *   smoke:        number,
 *   smokeRiseRate: number,
 *   tempRiseRate:  number
 * }} reading
 * @returns {{ anomalyDetected: boolean, anomalyType: string, severity: string, nodeId: string, details: object } | null}
 */
export function analyze(reading) {
  const { nodeId, temperature, smoke, smokeRiseRate, tempRiseRate } = reading;

  // ── Update sliding window ──────────────────────────────────────────
  if (!_windows.has(nodeId)) {
    _windows.set(nodeId, []);
  }
  const window = _windows.get(nodeId);
  window.push({ temperature, smoke, ts: Date.now() });
  if (window.length > WINDOW_SIZE) window.shift();

  // Need at least 2 readings to calculate trends
  if (window.length < 2) return null;

  // ── Check 1: Sudden spike ──────────────────────────────────────────
  const prev  = window[window.length - 2];
  const delta_smoke = smoke - prev.smoke;
  const delta_temp  = temperature - prev.temperature;

  if (delta_smoke > SPIKE_SMOKE_THRESHOLD) {
    return _anomaly(nodeId, "SUDDEN_SPIKE", "CRITICAL",
      { delta_smoke, smoke, prevSmoke: prev.smoke });
  }
  if (delta_temp > SPIKE_TEMP_THRESHOLD) {
    return _anomaly(nodeId, "SUDDEN_SPIKE", "CRITICAL",
      { delta_temp, temperature, prevTemp: prev.temperature });
  }

  // ── Check 2: Slow smoke rise ───────────────────────────────────────
  if (smokeRiseRate > SLOW_SMOKE_RATE_WARNING && smoke < 200) {
    return _anomaly(nodeId, "SLOW_SMOKE_RISE", "WARNING",
      { smokeRiseRate, smoke, trend: "RISING" });
  }

  // ── Check 3: Slow temp rise ────────────────────────────────────────
  if (tempRiseRate > SLOW_TEMP_RATE_WARNING && temperature < 40) {
    return _anomaly(nodeId, "SLOW_TEMP_RISE", "WARNING",
      { tempRiseRate, temperature, trend: "RISING" });
  }

  // ── Check 4: Sustained elevated readings ──────────────────────────
  if (window.length >= SUSTAINED_READINGS) {
    const recent    = window.slice(-SUSTAINED_READINGS);
    const allHighSmoke = recent.every((r) => r.smoke > SOFT_SMOKE_LIMIT_PPM);
    const allHighTemp  = recent.every((r) => r.temperature > SOFT_TEMP_LIMIT_C);

    if (allHighSmoke) {
      return _anomaly(nodeId, "SUSTAINED_ELEVATED", "WARNING",
        { readings: SUSTAINED_READINGS, averageSmoke: _avg(recent, "smoke") });
    }
    if (allHighTemp) {
      return _anomaly(nodeId, "SUSTAINED_ELEVATED", "WARNING",
        { readings: SUSTAINED_READINGS, averageTemp: _avg(recent, "temperature") });
    }
  }

  return null;
}

/**
 * Clear a node's window (called on fire clear / node recovery).
 * @param {string} nodeId
 */
export function clearNode(nodeId) {
  _windows.delete(nodeId);
}

/**
 * Return a snapshot of all current windows for diagnostics.
 */
export function getWindows() {
  const out = {};
  for (const [k, v] of _windows) out[k] = v;
  return out;
}

// ─────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────

function _anomaly(nodeId, type, severity, details) {
  return { anomalyDetected: true, anomalyType: type, severity, nodeId, details };
}

function _avg(arr, key) {
  return parseFloat((arr.reduce((s, r) => s + r[key], 0) / arr.length).toFixed(2));
}
