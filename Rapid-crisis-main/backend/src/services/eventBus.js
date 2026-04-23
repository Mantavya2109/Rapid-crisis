/**
 * eventBus.js
 * ───────────
 * Application-wide event bus that decouples producers from consumers.
 *
 * This is the backbone of the event-driven architecture. Instead of:
 *   controller A → calls controller B → waits → responds
 *
 * We do:
 *   controller A → emits event → responds immediately
 *   service B, C, D → react independently, in parallel
 *
 * For the prototype this uses Node.js EventEmitter (zero dependencies).
 * In production, replace the emit/on calls with Google Cloud Pub/Sub topics —
 * the rest of the codebase stays identical.
 *
 * ── Event Catalogue ──────────────────────────────────────────────────
 *
 *  "telemetry:received"   – Pi sent a filtered sensor reading
 *  "fire:detected"        – Pi reported a confirmed fire event
 *  "fire:cleared"         – Pi declared all-clear
 *  "anomaly:detected"     – Analyzer found a pre-threshold trend
 *  "evacuation:reroute"   – Optimizer suggests redirecting to alternate exit
 *  "intelligence:ready"   – AI engine finished scoring; push result to SSE clients
 */

import { EventEmitter } from "events";

class FireEvacEventBus extends EventEmitter {
  constructor() {
    super();
    // Prevent Node.js MaxListenersExceededWarning in dev
    this.setMaxListeners(30);
  }

  /**
   * Emit with structured logging so every event is traceable.
   * @param {string} event
   * @param {object} payload
   */
  fire(event, payload) {
    const envelope = { event, payload, ts: new Date().toISOString() };
    console.log(`[EventBus] ▶ ${event}`, JSON.stringify(payload).slice(0, 120));
    this.emit(event, envelope);
    return this;
  }
}

// Singleton — every module imports the same instance
export const bus = new FireEvacEventBus();
