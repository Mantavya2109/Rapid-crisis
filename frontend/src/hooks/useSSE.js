import { useEffect, useRef, useCallback } from "react";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:3000";

/**
 * useSSE — connects to the backend's Server-Sent Events stream.
 *
 * Automatically reconnects on disconnect. Provides real-time events
 * from the backend event bus (fire alerts, telemetry, anomalies).
 *
 * Usage:
 *   useSSE({
 *     onFire:      (data) => console.log("Fire!", data),
 *     onTelemetry: (data) => updateNodeState(data),
 *     onCleared:   (data) => resetEvacuation(data),
 *   });
 *
 * @param {object} handlers — event-specific callbacks
 */
export function useSSE(handlers = {}) {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    let es;
    let reconnectTimer;

    function connect() {
      es = new EventSource(`${API_BASE}/events`);

      es.addEventListener("connected", () => {
        console.log("[SSE] ✅ Connected to backend event stream");
      });

      es.addEventListener("fire:detected", (e) => {
        try {
          const data = JSON.parse(e.data);
          handlersRef.current.onFire?.(data);
        } catch {}
      });

      es.addEventListener("fire:cleared", (e) => {
        try {
          const data = JSON.parse(e.data);
          handlersRef.current.onCleared?.(data);
        } catch {}
      });

      es.addEventListener("telemetry:received", (e) => {
        try {
          const data = JSON.parse(e.data);
          handlersRef.current.onTelemetry?.(data);
        } catch {}
      });

      es.addEventListener("anomaly:detected", (e) => {
        try {
          const data = JSON.parse(e.data);
          handlersRef.current.onAnomaly?.(data);
        } catch {}
      });

      es.addEventListener("evacuation:reroute", (e) => {
        try {
          const data = JSON.parse(e.data);
          handlersRef.current.onReroute?.(data);
        } catch {}
      });

      es.addEventListener("intelligence:ready", (e) => {
        try {
          const data = JSON.parse(e.data);
          handlersRef.current.onIntelligence?.(data);
        } catch {}
      });

      es.onerror = () => {
        console.warn("[SSE] Connection lost — reconnecting in 3s…");
        es.close();
        reconnectTimer = setTimeout(connect, 3000);
      };
    }

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      if (es) es.close();
    };
  }, []);
}

export default useSSE;
