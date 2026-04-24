/**
 * api.js
 * Centralised API client for Rapid Crisis backend.
 * Base URL: http://localhost:3000
 */

const BASE_URL = 'http://localhost:3000';

async function request(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE_URL}${path}`, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`);
  return data;
}

// ── Building ──────────────────────────────────────────────────
export const buildingApi = {
  /** POST /building/setup */
  setup: (payload) => request('POST', '/building/setup', payload),

  /** GET /building/:id */
  get: (buildingId) => request('GET', `/building/${buildingId}`),
};

// ── Fire Alert ────────────────────────────────────────────────
export const fireApi = {
  /** GET /fire-alert/state/:buildingId */
  getState: (buildingId) => request('GET', `/fire-alert/state/${buildingId}`),

  /** POST /fire-alert/clear */
  clear: (buildingId) => request('POST', '/fire-alert/clear', { buildingId }),
};

// ── Telemetry ─────────────────────────────────────────────────
export const telemetryApi = {
  /** GET /telemetry/building/:buildingId */
  getBuilding: (buildingId) => request('GET', `/telemetry/building/${buildingId}`),
};

// ── Health ────────────────────────────────────────────────────
export const healthApi = {
  check: () => request('GET', '/health'),
};

// ── SSE ───────────────────────────────────────────────────────
export function createSSEConnection(onEvent) {
  const es = new EventSource(`${BASE_URL}/events`);
  const events = [
    'fire:detected',
    'fire:cleared',
    'telemetry:received',
    'anomaly:detected',
    'evacuation:reroute',
    'intelligence:ready',
    'connected',
  ];
  events.forEach((evt) => {
    es.addEventListener(evt, (e) => {
      try {
        const data = JSON.parse(e.data);
        onEvent(evt, data);
      } catch {
        onEvent(evt, e.data);
      }
    });
  });
  es.onerror = () => console.warn('[SSE] Connection error — will retry');
  return es;
}
