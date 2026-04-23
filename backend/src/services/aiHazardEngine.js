/**
 * aiHazardEngine.js
 * ─────────────────
 * Computes a probabilistic risk score (0.0 – 1.0) for each node based on
 * real sensor data, rate-of-change trends, neighbour states, and elapsed time.
 *
 * This replaces the naive "if temp > threshold → danger" with a weighted
 * composite model that predicts fire spread BEFORE it physically arrives.
 *
 * ── Risk Score Formula ────────────────────────────────────────────────
 *
 *   risk = clamp(
 *     w_temp      × temp_factor(temp)           +
 *     w_smoke     × smoke_factor(smoke)          +
 *     w_rise      × rise_factor(maxRiseRate)     +
 *     w_neighbor  × neighbor_factor(neighbors)   +
 *     w_time      × time_factor(elapsedMin)
 *   , 0.0, 1.0)
 *
 * ── Factors ──────────────────────────────────────────────────────────
 *
 *   temp_factor   = sigmoid curve peaking at 1.0 near CRITICAL_TEMP
 *   smoke_factor  = linear: smoke / CRITICAL_SMOKE_PPM, capped at 1.0
 *   rise_factor   = linear: max(smokeRiseRate, tempRiseRate) / MAX_RISE
 *   neighbor_fact = max(risk_scores of adjacent nodes) × SPREAD_COEFF
 *   time_factor   = 1 − exp(−λ × elapsedMinutes)  → grows with fire age
 *
 * ── Weights ──────────────────────────────────────────────────────────
 *   temp:     0.30   (direct evidence)
 *   smoke:    0.30   (direct evidence)
 *   rise:     0.20   (predictive — rate of escalation)
 *   neighbor: 0.15   (spatial spread)
 *   time:     0.05   (temporal growth)
 */

// ── Tunable constants ─────────────────────────────────────────────────
const CRITICAL_TEMP_C      = 65.0;   // °C — maps to risk ≈ 1.0
const WARN_TEMP_C          = 40.0;   // °C — sigmoid mid-point
const CRITICAL_SMOKE_PPM   = 600.0;  // ppm — linear ceiling
const MAX_RISE_RATE        = 30.0;   // °C or ppm / min to risk 1.0
const SPREAD_COEFF         = 0.55;   // how strongly fire propagates to neighbors
const TIME_LAMBDA          = 0.15;   // controls how fast time_factor grows

// ── Weights (must sum to 1.0) ─────────────────────────────────────────
const W = { temp: 0.30, smoke: 0.30, rise: 0.20, neighbor: 0.15, time: 0.05 };

// ── Per-node alert registry (used for time_factor) ────────────────────
// { nodeId → firstAlertTs (ms) }
const _alertRegistry = new Map();

/**
 * Mark a node as actively on fire (starts the time clock).
 * @param {string} nodeId
 */
export function registerFireNode(nodeId) {
  if (!_alertRegistry.has(nodeId)) {
    _alertRegistry.set(nodeId, Date.now());
  }
}

/**
 * Clear a fire node from the registry (all-clear).
 * @param {string} nodeId
 */
export function clearFireNode(nodeId) {
  _alertRegistry.delete(nodeId);
}

// ─────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────

/**
 * Compute risk scores for every known node given the current system state.
 *
 * @param {{
 *   nodeStates: Record<string, {
 *     nodeId: string,
 *     temperature: number,
 *     smoke: number,
 *     smokeRiseRate: number,
 *     tempRiseRate: number,
 *     status: string
 *   }>,
 *   adjacency: Record<string, string[]>   // { nodeId → [neighbourId, ...] }
 * }} context
 *
 * @returns {Record<string, number>}  { nodeId → risk 0.0–1.0 }
 */
export function computeAllRiskScores({ nodeStates, adjacency }) {
  // ── First pass: score every node ignoring neighbours ─────────────
  /** @type {Record<string, number>} */
  const rawScores = {};

  for (const [nodeId, state] of Object.entries(nodeStates)) {
    rawScores[nodeId] = _scoreNode(nodeId, state, 0 /* no neighbor influence yet */);
  }

  // ── Second pass: blend in neighbour influence ─────────────────────
  /** @type {Record<string, number>} */
  const finalScores = {};

  for (const [nodeId, state] of Object.entries(nodeStates)) {
    const neighbors      = adjacency[nodeId] ?? [];
    const maxNeighborRisk = neighbors.reduce(
      (max, nid) => Math.max(max, rawScores[nid] ?? 0),
      0
    );
    finalScores[nodeId] = _scoreNode(nodeId, state, maxNeighborRisk);
  }

  return finalScores;
}

/**
 * Compute hazard weights suitable for the Pi's Dijkstra algorithm.
 * Transforms risk scores (0–1) into additive path costs (0–100).
 *
 * @param {Record<string, number>} riskScores
 * @returns {Record<string, number>}  { nodeId → extra path cost }
 */
export function riskScoresToHazardWeights(riskScores) {
  /** @type {Record<string, number>} */
  const weights = {};
  for (const [nodeId, risk] of Object.entries(riskScores)) {
    if (risk > 0.05) {
      // Quadratic amplification: higher risks get disproportionately large penalties
      weights[nodeId] = parseFloat((risk * risk * 100).toFixed(2));
    }
  }
  return weights;
}

// ─────────────────────────────────────────────────────────────────────
// Internal helpers
// ─────────────────────────────────────────────────────────────────────

/** @param {number} x @param {number} mid @param {number} scale */
function _sigmoid(x, mid, scale) {
  return 1 / (1 + Math.exp(-(x - mid) / scale));
}

/** @param {number} v @param {number} lo @param {number} hi */
function _clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

/**
 * Score a single node.
 * @param {string}  nodeId
 * @param {object}  state
 * @param {number}  maxNeighborRisk
 * @returns {number}  risk 0.0–1.0
 */
function _scoreNode(nodeId, state, maxNeighborRisk) {
  const { temperature = 0, smoke = 0, smokeRiseRate = 0, tempRiseRate = 0 } = state;

  // ── Individual factors ──────────────────────────────────────────────
  const temp_factor   = _sigmoid(temperature, WARN_TEMP_C, (CRITICAL_TEMP_C - WARN_TEMP_C) / 4);
  const smoke_factor  = _clamp(smoke / CRITICAL_SMOKE_PPM, 0, 1);
  const rise_factor   = _clamp(Math.max(smokeRiseRate, tempRiseRate) / MAX_RISE_RATE, 0, 1);
  const neighbor_factor = maxNeighborRisk * SPREAD_COEFF;

  // ── Time factor (grows as fire ages at this node) ──────────────────
  let time_factor = 0;
  const firstAlert = _alertRegistry.get(nodeId);
  if (firstAlert) {
    const elapsedMin = (Date.now() - firstAlert) / 60_000;
    time_factor = 1 - Math.exp(-TIME_LAMBDA * elapsedMin);
  }

  const raw = (
    W.temp     * temp_factor    +
    W.smoke    * smoke_factor   +
    W.rise     * rise_factor    +
    W.neighbor * neighbor_factor +
    W.time     * time_factor
  );

  return parseFloat(_clamp(raw, 0, 1).toFixed(4));
}
