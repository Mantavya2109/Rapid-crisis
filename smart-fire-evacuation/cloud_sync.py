"""
cloud_sync.py
-------------
Sends structured fire alert data to the cloud backend.

Endpoints used:
    POST /fire-alert   — notify cloud of a fire event + blocked nodes
    POST /led/batch    — (optional) cloud-side LED routing for remote buildings

Retry strategy: exponential back-off with jitter.
"""

import time
import random
import requests
from typing import Any, Dict, List, Optional

from config.settings import (
    CLOUD_API_URL,
    CLOUD_LED_BATCH_URL,
    CLOUD_API_KEY,
    CLOUD_TIMEOUT_SEC,
    CLOUD_RETRY_ATTEMPTS,
)
from logger import get_logger

log = get_logger(__name__)


def _build_headers() -> Dict[str, str]:
    """Return HTTP headers, including Bearer auth if a key is configured."""
    headers = {"Content-Type": "application/json"}
    if CLOUD_API_KEY:
        headers["Authorization"] = f"Bearer {CLOUD_API_KEY}"
    return headers


def _post_with_retry(
    url: str,
    payload: Dict[str, Any],
    label: str = "cloud",
) -> Optional[Dict]:
    """
    POST to a cloud endpoint with exponential back-off retry.

    Returns
    -------
    Parsed JSON response dict on success, None on failure.
    """
    headers = _build_headers()

    for attempt in range(1, CLOUD_RETRY_ATTEMPTS + 1):
        try:
            log.info(
                "☁️  %s attempt %d/%d → %s",
                label, attempt, CLOUD_RETRY_ATTEMPTS, url,
            )
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=CLOUD_TIMEOUT_SEC,
            )
            response.raise_for_status()
            log.info(
                "✅ %s successful (HTTP %d) on attempt %d.",
                label, response.status_code, attempt,
            )
            try:
                return response.json()
            except ValueError:
                return {"status": "OK"}

        except requests.exceptions.ConnectionError:
            log.warning(
                "⚠️  Cloud unreachable — %s (attempt %d/%d).",
                label, attempt, CLOUD_RETRY_ATTEMPTS,
            )
        except requests.exceptions.Timeout:
            log.warning(
                "⚠️  %s timed out after %ds (attempt %d/%d).",
                label, CLOUD_TIMEOUT_SEC, attempt, CLOUD_RETRY_ATTEMPTS,
            )
        except requests.exceptions.HTTPError as exc:
            log.error(
                "❌ %s HTTP error (attempt %d/%d): %s",
                label, attempt, CLOUD_RETRY_ATTEMPTS, exc,
            )
        except Exception as exc:  # pylint: disable=broad-except
            log.error(
                "❌ Unexpected %s error (attempt %d/%d): %s",
                label, attempt, CLOUD_RETRY_ATTEMPTS, exc,
            )

        # Exponential back-off with jitter
        if attempt < CLOUD_RETRY_ATTEMPTS:
            sleep_time = (2 ** attempt) + random.uniform(0, 0.5)
            log.debug("Retrying in %.1fs…", sleep_time)
            time.sleep(sleep_time)

    log.error(
        "🔴 %s FAILED after %d attempts. System running in LOCAL FAIL-SAFE mode.",
        label, CLOUD_RETRY_ATTEMPTS,
    )
    return None


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────

def send_fire_alert(payload: Dict[str, Any]) -> bool:
    """
    Legacy single-payload fire alert (backward-compatible).
    Returns True on success, False on all retries exhausted.
    """
    result = _post_with_retry(CLOUD_API_URL, payload, label="fire-alert")
    return result is not None


def send_structured_fire_alert(
    building_id: str,
    blocked_nodes: List[str],
    start_nodes: List[str],
    sensor_readings: Optional[Dict[str, Any]] = None,
) -> Optional[Dict]:
    """
    Send a structured fire alert to the cloud.

    Expected cloud response:
    {
        "commands": [
            { "node": "HALLWAY_A", "direction": "RIGHT", "color": "GREEN", "mode": "FLOW" },
            ...
        ]
    }
    """
    payload: Dict[str, Any] = {
        "buildingId":    building_id,
        "blocked_nodes": blocked_nodes,
        "startNodes":    start_nodes,
    }
    if sensor_readings:
        payload["sensor_data"] = sensor_readings

    return _post_with_retry(CLOUD_API_URL, payload, label="structured-fire-alert")


def send_led_batch_to_cloud(commands: List[Dict[str, Any]]) -> bool:
    """
    POST LED batch commands to the cloud's /led/batch endpoint.
    Returns True on success.
    """
    payload = {"commands": commands}
    result = _post_with_retry(CLOUD_LED_BATCH_URL, payload, label="led/batch")
    return result is not None


def send_sensor_telemetry(
    building_id:     str,
    node_id:         str,
    device_id:       str,
    temperature:     float,
    smoke:           float,
    status:          str,
    state_changed:   bool                    = False,
    smoke_rise_rate: float                   = 0.0,
    temp_rise_rate:  float                   = 0.0,
    raw:             Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Forward an event-filtered sensor reading to the cloud telemetry endpoint.

    This is only called when EventFilter.should_emit() returns True, so the
    cloud receives: state changes, critical events, and periodic summaries —
    NOT every raw reading.

    Cloud payload shape:
    {
        "buildingId":     "BUILDING_01",
        "nodeId":         "ROOM_101",
        "deviceId":       "ESP32_NODE_A",
        "temperature":    28.5,
        "smoke":          150.0,
        "status":         "WARNING",       ← Pi 6-state classification
        "stateChanged":   true,            ← NEW: was this a state transition?
        "smokeRiseRate":  12.3,            ← NEW: units/min
        "tempRiseRate":   2.1,             ← NEW: °C/min
        "raw":            { ... }          ← original ESP32 payload
    }

    Returns True on success, False if all retries exhausted.
    Never blocks the MQTT dispatch loop — always called from a daemon thread.
    """
    from config.settings import CLOUD_TELEMETRY_URL

    payload: Dict[str, Any] = {
        "buildingId":   building_id,
        "nodeId":       node_id,
        "deviceId":     device_id,
        "temperature":  round(temperature, 2),
        "smoke":        round(smoke, 2),
        "status":       status,
        "stateChanged": state_changed,
        "smokeRiseRate": round(smoke_rise_rate, 2),
        "tempRiseRate":  round(temp_rise_rate,  2),
    }
    if raw:
        payload["raw"] = raw

    result = _post_with_retry(CLOUD_TELEMETRY_URL, payload, label="telemetry")
    return result is not None


def send_system_telemetry(mode: str) -> bool:
    """
    Forward system mode transitions to the cloud backend.

    Called when system_mode changes (e.g., NORMAL → ALERT → EVACUATION).
    Uses the telemetry endpoint with a special `systemMode` field so the
    backend can distinguish it from regular sensor telemetry.

    Returns True on success, False if all retries exhausted.
    """
    from config.settings import CLOUD_TELEMETRY_URL, BUILDING_ID

    payload: Dict[str, Any] = {
        "buildingId":  BUILDING_ID,
        "nodeId":      "__SYSTEM__",
        "deviceId":    "RPI_CONTROLLER",
        "temperature": 0,
        "smoke":       0,
        "status":      mode,
        "systemMode":  mode,
        "stateChanged": True,
    }

    result = _post_with_retry(CLOUD_TELEMETRY_URL, payload, label="system-mode")
    return result is not None

