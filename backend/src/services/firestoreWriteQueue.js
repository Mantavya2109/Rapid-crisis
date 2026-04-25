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
const MAX_BATCH_SIZE = 400; // stay well under Firestore's 500 limit

/**
 * @typedef {"set"|"delete"|"replace"} QueueOp
 */

/** @type {Array<{ op: QueueOp, ref: FirebaseFirestore.DocumentReference, data?: object, setOptions?: import("firebase-admin/firestore").SetOptions }>} */
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
  console.log(
    `[WriteQueue] Initialized — flush every ${FLUSH_INTERVAL_MS / 1000}s`,
  );
}

/**
 * Enqueue a document set operation. Fire and forget — does not await.
 * @param {FirebaseFirestore.DocumentReference} ref
 * @param {object} data
 * @param {import("firebase-admin/firestore").SetOptions=} setOptions
 */
export function enqueue(ref, data, setOptions) {
  if (_queue.length >= MAX_BATCH_SIZE * 2) {
    // Safety valve: drop the oldest entry to prevent unbounded memory growth
    _queue.shift();
  }
  _queue.push({ op: "set", ref, data, setOptions });
}

/**
 * Enqueue a document delete operation.
 * @param {FirebaseFirestore.DocumentReference} ref
 */
export function enqueueDelete(ref) {
  if (_queue.length >= MAX_BATCH_SIZE * 2) {
    _queue.shift();
  }
  _queue.push({ op: "delete", ref });
}

/**
 * Enqueue a document replace operation.
 *
 * This will delete the document and then insert the new data (merge:false)
 * so that no old nested fields remain.
 *
 * Note: This affects the document only. Firestore subcollections (if any)
 * are not deleted by deleting a document.
 *
 * @param {FirebaseFirestore.DocumentReference} ref
 * @param {object} data
 */
export function enqueueReplace(ref, data) {
  if (_queue.length >= MAX_BATCH_SIZE * 2) {
    _queue.shift();
  }
  _queue.push({ op: "replace", ref, data });
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

  const batchOps = chunk.filter(({ op }) => op === "set" || op === "delete");
  const replaceOps = chunk.filter(({ op }) => op === "replace");

  try {
    if (batchOps.length > 0) {
      const batch = _db.batch();
      batchOps.forEach(({ op, ref, data, setOptions }) => {
        if (op === "delete") {
          batch.delete(ref);
          return;
        }

        const sanitized = _stripUndefinedDeep(data ?? {});
        batch.set(ref, sanitized, setOptions ?? { merge: true });
      });
      await batch.commit();
    }

    for (const { ref, data } of replaceOps) {
      const sanitized = _stripUndefinedDeep(data ?? {});
      await _db.runTransaction(async (tx) => {
        tx.delete(ref);
        tx.set(ref, sanitized, { merge: false });
      });
    }

    console.log(
      `[WriteQueue] Flushed ${chunk.length} ops to Firestore (batch:${batchOps.length}, replace:${replaceOps.length}).`,
    );
  } catch (err) {
    console.error("[WriteQueue] Batch flush failed — re-queuing:", err.message);
    // Re-prepend failed items so they are retried next cycle
    _queue.unshift(...chunk);
    bus.fire("writeQueue:error", { count: chunk.length, error: err.message });
  }
}

/**
 * Firestore rejects `undefined` values. If any are present, the whole batch fails,
 * causing repeated retries and the appearance that fields are "not being saved".
 *
 * This strips ONLY `undefined` values (recursively) while preserving all other
 * fields (including x/y, distance, sensors, start, nulls, 0, false).
 */
function _stripUndefinedDeep(value) {
  if (value === undefined) return undefined;
  if (value === null) return null;

  if (Array.isArray(value)) {
    return value.map(_stripUndefinedDeep).filter((v) => v !== undefined);
  }

  if (typeof value === "object") {
    const proto = Object.getPrototypeOf(value);
    const isPlain = proto === Object.prototype || proto === null;
    if (!isPlain) return value;

    const out = {};
    Object.keys(value).forEach((k) => {
      const v = _stripUndefinedDeep(value[k]);
      if (v !== undefined) out[k] = v;
    });
    return out;
  }

  return value;
}
