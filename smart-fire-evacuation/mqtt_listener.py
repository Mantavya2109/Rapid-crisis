"""
mqtt_listener.py
----------------
MQTT subscriber for the Raspberry Pi edge controller.

The Pi runs a local Mosquitto broker.  Each ESP32 publishes to:
  sensors/data/<nodeId>       — regular telemetry (temp + smoke)
  sensors/heartbeat/<nodeId>  — keepalive pings

This module:
  - Maintains a resilient paho-mqtt client with auto-reconnect
  - Dispatches decoded JSON messages to registered callback functions
  - Publishes a retained "rpi/status" WILL message if the Pi goes offline
  - Is started in a background daemon thread from main.py
"""

import json
import threading
import time
from typing import Callable, Optional

import paho.mqtt.client as mqtt

from config.settings import (
    MQTT_BROKER_HOST,
    MQTT_BROKER_PORT,
    MQTT_USERNAME,
    MQTT_PASSWORD,
    MQTT_TOPIC_SENSOR,
    MQTT_TOPIC_HEARTBEAT,
    MQTT_KEEPALIVE_SEC,
    MQTT_QOS,
    MQTT_CLIENT_ID,
    MQTT_TLS_ENABLED,
    MQTT_CA_CERTS,
    MQTT_CERTFILE,
    MQTT_KEYFILE,
)
from logger import get_logger
import metrics

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Callback registry
# ─────────────────────────────────────────────────────────────────────

_on_sensor_data:  Optional[Callable[[str, dict], None]] = None
_on_heartbeat:    Optional[Callable[[str, dict], None]] = None

_client:    Optional[mqtt.Client] = None
_connected  = threading.Event()
_started    = False
_lock       = threading.Lock()

_RECONNECT_DELAY_SEC = 5


def register_sensor_callback(fn: Callable[[str, dict], None]) -> None:
    """Register the function called for every sensors/data/+ message."""
    global _on_sensor_data
    _on_sensor_data = fn


def register_heartbeat_callback(fn: Callable[[str, dict], None]) -> None:
    """Register the function called for every sensors/heartbeat/+ message."""
    global _on_heartbeat
    _on_heartbeat = fn


# ─────────────────────────────────────────────────────────────────────
# paho-mqtt callbacks
# ─────────────────────────────────────────────────────────────────────

def _on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info(
            "✅ MQTT connected to broker %s:%d",
            MQTT_BROKER_HOST, MQTT_BROKER_PORT,
        )
        # Re-subscribe after every connect (handles re-connects too)
        client.subscribe(MQTT_TOPIC_SENSOR,     qos=MQTT_QOS)
        client.subscribe(MQTT_TOPIC_HEARTBEAT,  qos=MQTT_QOS)
        log.info("📡 Subscribed: %s | %s", MQTT_TOPIC_SENSOR, MQTT_TOPIC_HEARTBEAT)
        # Publish an ONLINE status (retained so dashboard always sees it)
        client.publish(
            "rpi/status",
            json.dumps({"status": "ONLINE", "ts": time.time()}),
            qos=1,
            retain=True,
        )
        _connected.set()
    else:
        _rc_errors = {
            1: "incorrect protocol version",
            2: "invalid client identifier",
            3: "server unavailable",
            4: "bad username or password",
            5: "not authorised",
        }
        log.error(
            "❌ MQTT connect failed (rc=%d): %s",
            rc, _rc_errors.get(rc, "unknown"),
        )
        _connected.clear()


def _on_disconnect(client, userdata, rc):
    _connected.clear()
    if rc != 0:
        log.warning(
            "⚠️  MQTT disconnected unexpectedly (rc=%d). "
            "paho will auto-reconnect.", rc,
        )


def _on_message(client, userdata, msg):
    topic   = msg.topic
    raw     = msg.payload

    # Parse JSON payload
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("⚠️  Non-JSON MQTT msg on %s: %s", topic, exc)
        return

    log.debug("📨 MQTT msg: topic=%s  payload=%s", topic, payload)
    metrics.mqtt_messages_received_total.labels(topic=topic).inc()

    # Route to the correct callback via the processing queue
    import processing_queue
    queue = processing_queue.get_instance()
    
    if queue:
        metrics.queue_size_gauge.set(queue.qsize())

    if topic.startswith("sensors/data/"):
        if _on_sensor_data:
            if queue:
                queue.enqueue_sensor(topic, payload)
            else:
                _on_sensor_data(topic, payload)

    elif topic.startswith("sensors/heartbeat/"):
        if _on_heartbeat:
            if queue:
                queue.enqueue_heartbeat(topic, payload)
            else:
                _on_heartbeat(topic, payload)

    else:
        log.debug("Unhandled topic: %s", topic)


# ─────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────

def start(block: bool = True, connect_timeout: float = 10.0) -> None:
    """
    Start the MQTT client in a background daemon thread.

    Parameters
    ----------
    block           : if True, wait up to connect_timeout seconds for the
                      initial MQTT connection before returning.
    connect_timeout : seconds to wait when block=True.
    """
    global _client, _started

    with _lock:
        if _started:
            log.warning("mqtt_listener.start() called twice — ignoring.")
            return
        _started = True

    _client = mqtt.Client(
        client_id   = MQTT_CLIENT_ID,
        clean_session = True,
        protocol    = mqtt.MQTTv311,
    )

    if MQTT_USERNAME:
        _client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD or "")

    if MQTT_TLS_ENABLED:
        if MQTT_CA_CERTS:
            log.info("🔒 Setting up MQTT TLS Configuration...")
            _client.tls_set(
                ca_certs=MQTT_CA_CERTS,
                certfile=MQTT_CERTFILE if MQTT_CERTFILE else None,
                keyfile=MQTT_KEYFILE if MQTT_KEYFILE else None,
            )
        else:
            log.warning("⚠️ MQTT_TLS_ENABLED is True but MQTT_CA_CERTS is empty, skipping TLS config.")

    # Last-will: Pi going offline (Mosquitto delivers this if TCP drops)
    _client.will_set(
        "rpi/status",
        json.dumps({"status": "OFFLINE", "ts": time.time()}),
        qos=1,
        retain=True,
    )

    _client.on_connect    = _on_connect
    _client.on_disconnect = _on_disconnect
    _client.on_message    = _on_message

    def _run():
        while True:
            try:
                log.info(
                    "🔌 Connecting to MQTT broker %s:%d …",
                    MQTT_BROKER_HOST, MQTT_BROKER_PORT,
                )
                _client.connect(
                    MQTT_BROKER_HOST,
                    MQTT_BROKER_PORT,
                    keepalive=MQTT_KEEPALIVE_SEC,
                )
                _client.loop_forever()   # blocks; auto-reconnects on network drop
            except OSError as exc:
                log.error(
                    "❌ MQTT connection error: %s. "
                    "Retrying in %ds…", exc, _RECONNECT_DELAY_SEC,
                )
                _connected.clear()
                time.sleep(_RECONNECT_DELAY_SEC)

    thread = threading.Thread(target=_run, name="mqtt-listener", daemon=True)
    thread.start()
    log.info("🚀 MQTT listener thread started.")

    if block:
        log.info("⏳ Waiting up to %.0fs for MQTT connection…", connect_timeout)
        connected = _connected.wait(timeout=connect_timeout)
        if not connected:
            log.warning(
                "⚠️  MQTT not connected within %.0fs — "
                "system will operate in HTTP-only mode until broker is reachable.",
                connect_timeout,
            )


def stop() -> None:
    """Gracefully disconnect (call on shutdown)."""
    if _client:
        _client.disconnect()
    _connected.clear()
    log.info("🛑 MQTT listener stopped.")


# ─────────────────────────────────────────────────────────────────────
# Status helpers (for REST API)
# ─────────────────────────────────────────────────────────────────────

def is_connected() -> bool:
    return _connected.is_set()


def publish(
    topic:   str,
    payload: dict,
    qos:     int  = 1,
    retain:  bool = False,
) -> bool:
    """
    Publish a message FROM the Pi (e.g. downstream LED commands, status).

    Returns True if published, False if not connected.
    """
    if _client and _connected.is_set():
        _client.publish(topic, json.dumps(payload), qos=qos, retain=retain)
        return True
    log.warning("⚠️  MQTT publish skipped — not connected. topic=%s", topic)
    return False
