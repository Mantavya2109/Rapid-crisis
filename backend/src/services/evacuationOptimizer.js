/**
 * evacuationOptimizer.js
 * ──────────────────────
 * Tracks global evacuation route usage and detects exit overloading.
 * When too many nodes route toward the same exit, this service generates
 * "reroute hints" that the Pi can apply to its next Dijkstra run.
 *
 * ── How it works ──────────────────────────────────────────────────────
 * The Pi reports which exit each startNode is routing to (via fire-alert).
 * This service counts per-exit load and, above a configurable threshold,
 * recommends that the Pi add a penalty weight to the overloaded exit so
 * its Dijkstra naturally routes occupants to the next-best exit.
 *
 * ── Reroute hint shape (merged into fire-alert response) ─────────────
 * {
 *   "overloadedExits": ["EXIT_A"],
 *   "exitHazardWeights": { "EXIT_A": 80 }   ← extra cost added to exit node
 * }
 *
 * The Pi merges these into its hazard_weights and re-runs Dijkstra.
 */

// ── Config ─────────────────────────────────────────────────────────────
const MAX_NODES_PER_EXIT  = 8;   // capacity before overload declared
const OVERLOAD_WEIGHT_ADD = 80;  // extra weight penalty on overloaded exit
const DECAY_INTERVAL_MS   = 30_000; // every 30s, reduce counts (occupants evacuate)

// ── State ──────────────────────────────────────────────────────────────
/** @type {Map<string, number>}  exitId → count of nodes routing here */
const _exitLoad = new Map();

/** @type {Map<string, Set<string>>}  exitId → set of nodeIds using it */
const _exitRoutes = new Map();

// Gradually decay load counts as people evacuate
setInterval(() => {
  for (const [exit, count] of _exitLoad) {
    const reduced = Math.max(0, count - 2);
    if (reduced === 0) {
      _exitLoad.delete(exit);
      _exitRoutes.delete(exit);
    } else {
      _exitLoad.set(exit, reduced);
    }
  }
}, DECAY_INTERVAL_MS);

// ─────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────

/**
 * Record that a set of nodes are routing toward a particular exit.
 * Call this when a fire alert is received with known path info.
 *
 * @param {string[]} startNodes  - evacuating start nodes
 * @param {string}   exit        - the exit they are routing toward
 */
export function recordExitUsage(startNodes, exit) {
  if (!exit) return;

  if (!_exitRoutes.has(exit)) _exitRoutes.set(exit, new Set());
  const routes = _exitRoutes.get(exit);

  startNodes.forEach((n) => routes.add(n));
  _exitLoad.set(exit, routes.size);
}

/**
 * Compute evacuation optimization hints based on current exit loads.
 * Returns extra hazard weights to add to overloaded exit nodes so the
 * Pi's Dijkstra diverts traffic toward less-loaded exits.
 *
 * @returns {{
 *   overloadedExits:  string[],
 *   exitHazardWeights: Record<string, number>
 * }}
 */
export function getOptimizationHints() {
  const overloadedExits   = [];
  /** @type {Record<string, number>} */
  const exitHazardWeights = {};

  for (const [exit, count] of _exitLoad) {
    if (count > MAX_NODES_PER_EXIT) {
      overloadedExits.push(exit);
      // Proportional penalty: the more overloaded, the heavier the weight
      const overloadRatio = count / MAX_NODES_PER_EXIT;
      exitHazardWeights[exit] = parseFloat(
        Math.min(OVERLOAD_WEIGHT_ADD * overloadRatio, 150).toFixed(1)
      );
    }
  }

  return { overloadedExits, exitHazardWeights };
}

/**
 * Returns a snapshot of current per-exit load for the dashboard.
 * @returns {Record<string, number>}
 */
export function getExitLoadSnapshot() {
  const out = {};
  for (const [exit, count] of _exitLoad) out[exit] = count;
  return out;
}

/**
 * Reset all load tracking (called on fire-clear).
 */
export function resetAll() {
  _exitLoad.clear();
  _exitRoutes.clear();
}
