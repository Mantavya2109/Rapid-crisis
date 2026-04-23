/**
 * firestoreWriteQueue.js
 * ──────────────────────
 * Batched Firestore write buffer to protect against:
 *   1. SD card write wear on the Raspberry Pi (edge, not relevant here)
 *   2. Firestore write costs — 500 nodes × 1 read/s = 43M ops/day (expensive!)
 *   3. Latency spikes from synchronous individual writes during fire events
 *
 * ── Strategy ──────────────────────────────────────────────────────────
 *   - Reads are always served directly (latency sensitive).
 *   - Writes that update the LIVE node_states snapshot are immediate
 *     (the dashboard depends on these).
 *   - Historical telemetry records are queued and flushed every
 *     FLUSH_INTERVAL_MS using a Firestore batch, capped at MAX_BATCH_SIZE.
 *
 * ── Firestore Batch Limit: 500 ops per batch ─────────────────────────
 */

import { bus } from "./eventBus.js";

const FLUSH_INTERVAL_MS = 5_000; // flush every 5 seconds
const MAX_BATCH_SIZE    = 400;   // stay well under Firestore's 500 limit

/** @type {Array<{ ref: FirebaseFirestore.DocumentReference, data: object }>} */
const _queue = [];

let _db = null;
let _flushTimer = null;

/**
 * Initialise the write queue with a Firestore instance.
 * Call this once from server.js after Firebase is ready.
 * @param {FirebaseFirestore.Firestore} db
 */
export function initWriteQueue(db) {
  _db = db;
  _flushTimer = setInterval(_flush, FLUSH_INTERVAL_MS);
  console.log(`[WriteQueue] Initialized — flush every ${FLUSH_INTERVAL_MS / 1000}s`);
}

/**
 * Enqueue a document set operation. Fire and forget — does not await.
 * @param {FirebaseFirestore.DocumentReference} ref
 * @param {object} data
 */
export function enqueue(ref, data) {
  if (_queue.length >= MAX_BATCH_SIZE * 2) {
    // Safety valve: drop the oldest entry to prevent unbounded memory growth
    _queue.shift();
  }
  _queue.push({ ref, data });
}

/**
 * Flush the queue immediately (e.g., on shutdown or fire event).
 * Returns a promise that resolves when the flush completes.
 */
export async function flushNow() {
  await _flush();
}

/** Stop the timer (for clean shutdown). */
export function shutdown() {
  if (_flushTimer) {
    clearInterval(_flushTimer);
    _flushTimer = null;
  }
}

// ─────────────────────────────────────────────────────────────────────
// Internal
// ─────────────────────────────────────────────────────────────────────

async function _flush() {
  if (!_db || _queue.length === 0) return;

  // Drain up to MAX_BATCH_SIZE items
  const chunk = _queue.splice(0, MAX_BATCH_SIZE);

  try {
    const batch = _db.batch();
    chunk.forEach(({ ref, data }) => batch.set(ref, data, { merge: true }));
    await batch.commit();
    console.log(`[WriteQueue] Flushed ${chunk.length} writes to Firestore.`);
  } catch (err) {
    console.error("[WriteQueue] Batch flush failed — re-queuing:", err.message);
    // Re-prepend failed items so they are retried next cycle
    _queue.unshift(...chunk);
    bus.fire("writeQueue:error", { count: chunk.length, error: err.message });
  }
}
