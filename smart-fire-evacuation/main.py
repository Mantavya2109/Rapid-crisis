"""
main.py
-------
Raspberry Pi Edge Controller — entry point.

Start order
-----------
1. Load .env + settings
2. Restore SQLite state (devices, active alerts)
3. Initialise LED strip hardware
4. Configure LED zones (from zone_config.json or auto-partition)
5. Register MQTT callbacks (sensor data + heartbeat)
6. Start MQTT listener (blocking until first broker connection)
7. Start Flask REST API in a background thread
8. Block on MQTT loop (main thread handles reconnect)

Usage
-----
    # Activate venv then:
    python main.py

    # Or via systemd (see setup.sh):
    sudo systemctl start fire-evacuation
"""

import os
import signal
import sys
import threading
import time

# Load .env before importing settings
from dotenv import load_dotenv
load_dotenv()

import device_registry
import event_log
import graph_manager
import led_driver
import mqtt_listener
import sensor_processor
import state_manager
import zone_manager
import persistence
import processing_queue
import recovery_manager
import system_mode
import cloud_sync
import ha_manager
from config.settings import (
    HOST, PORT, DEBUG,
    API_TLS_ENABLED, API_CERTFILE, API_KEYFILE,
    BUILDING_ID,
    TEMP_THRESHOLD, SMOKE_THRESHOLD,
    MQTT_BROKER_HOST, MQTT_BROKER_PORT,
    LED_GPIO_PIN, LED_COUNT,
    GRAPH_CONFIG_PATH,
)
from logger import get_logger

log = get_logger("main")


# ─────────────────────────────────────────────────────────────────────
# Graceful shutdown
# ─────────────────────────────────────────────────────────────────────

_shutdown_event = threading.Event()


def _handle_signal(signum, frame):
    log.warning("🛑 Received signal %d — shutting down gracefully…", signum)
    _shutdown_event.set()


signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ─────────────────────────────────────────────────────────────────────
# Flask API  (imported here to avoid circular imports)
# ─────────────────────────────────────────────────────────────────────

def _start_flask() -> threading.Thread:
    """Start the Flask REST API in a daemon thread."""
    from app import app  # noqa: import here to delay until after setup

    def _run():
        log.info("🌐 Flask API starting on http://%s:%d", HOST, PORT)
        
        ssl_ctx = None
        if API_TLS_ENABLED and API_CERTFILE and API_KEYFILE:
            ssl_ctx = (API_CERTFILE, API_KEYFILE)
            log.info("🔒 Flask API TLS is ENABLED")
            
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False, ssl_context=ssl_ctx, threaded=True)

    t = threading.Thread(target=_run, name="flask-api", daemon=True)
    t.start()
    return t


# ─────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────

def main() -> None:
    _banner()

    # ── 1. Restore persisted state ────────────────────────────────────
    log.info("🔄 Restoring state from SQLite…")
    state_manager.restore_from_db()
    device_registry.restore_from_db()

    # ── 2. Load building graph (cloud-first, local fallback) ─────────
    cloud_loaded = False
    try:
        cloud_loaded = graph_manager.load_from_cloud()
    except Exception as exc:
        log.warning("☁️  Cloud graph sync failed: %s", exc)

    if not cloud_loaded:
        log.info("📂 Loading graph from local JSON fallback…")
        graph_manager.load_graph()

    # ── 3. Initialise LED strip ───────────────────────────────────────
    log.info("💡 Initialising LED strip (%d LEDs on GPIO %d)…", LED_COUNT, LED_GPIO_PIN)
    led_driver.init()

    # ── 4. Configure LED zones ────────────────────────────────────────
    #  Try to load saved zone config first; fall back to auto-partition
    #  using all nodes from the building graph.
    loaded = zone_manager.load_from_config()
    if not loaded:
        all_nodes = list(graph_manager.get_adjacency().keys())
        if all_nodes:
            log.info(
                "🗺️  No zone config found — auto-partitioning %d nodes across %d LEDs.",
                len(all_nodes), LED_COUNT,
            )
            zone_manager.auto_partition(all_nodes)
        else:
            log.warning(
                "⚠️  No graph nodes loaded and no zone config — "
                "LED zones will not be registered until POST /zones/configure is called."
            )

    zone_manager.apply_to_driver()

    # — 4.5. Strict Mosquitto ACL Check —
    log.warning("⚠️ Skipping MQTT ACL verification (DEV MODE)")

    # — 5. HA Manager —
    def _on_promoted_to_primary():
        log.warning("Initiating PRIMARY logic...")
        time.sleep(2)
        _start_primary_logic()

    ha_manager.init(promotion_callback=_on_promoted_to_primary)
    # ── 6. Setup logic layers if Primary ──────────────────────────────
    if ha_manager.is_primary():
        _start_primary_logic()
    else:
        log.info("⏸️ Operating as SECONDARY. Node processing logic and MQTT paused.")

    # ── 7. Start HA loop ──────────────────────────────────────────────
    ha_manager.start()

    # ── 8. Start Flask API (Both Primary and Secondary runs it) ───────
    flask_thread = _start_flask()
    # Give Flask a moment to bind the port
    time.sleep(0.5)

    # ── 9. Ready ──────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("  ✅  Raspberry Pi Edge Controller — READY")
    log.info("  Building   : %s", BUILDING_ID)
    log.info("  API        : http://%s:%d", HOST, PORT)
    log.info("  MQTT broker: %s:%d", MQTT_BROKER_HOST, MQTT_BROKER_PORT)
    log.info("  HA Role    : %s", ha_manager.get_current_role())
    log.info("=" * 60)

    # Wait for shutdown signal
    while not _shutdown_event.is_set():
        if not flask_thread.is_alive():
            log.critical("🚨 Flask API thread DIED — triggering system restart.")
            sys.exit(1)
        time.sleep(5)

    # ── 10. Teardown ──────────────────────────────────────────────────
    log.info("🛑 Shutting down…")
    ha_manager.stop()
    queue = processing_queue.get_instance()
    if queue:
        queue.stop()
    mqtt_listener.stop()
    led_driver.set_all_zones("OFF")
    persistence.flush_now()
    log.info("👋 Goodbye.")

_primary_started = False

def _start_primary_logic():
    global _primary_started
    if _primary_started:
        log.warning("⚠️ _start_primary_logic() called again — ignoring.")
        return
    _primary_started = True

    # Init processing queue (Load protection)
    queue = processing_queue.init(
        sensor_callback=sensor_processor.on_sensor_data,
        heartbeat_callback=sensor_processor.on_heartbeat,
    )

    # Recovery manager (Post-fire recovery)
    recovery_manager.start(get_node_states=sensor_processor.get_all_node_states)
    
    # Mode forwarder to cloud
    system_mode.register_change_callback(
        lambda o, n: cloud_sync.send_system_telemetry(mode=n)
    )

    # ── Register MQTT callbacks ────────────────────────────────────
    mqtt_listener.register_sensor_callback(sensor_processor.on_sensor_data)
    mqtt_listener.register_heartbeat_callback(sensor_processor.on_heartbeat)

    # ── Start MQTT listener ────────────────────────────────────────
    log.info(
        "📡 Connecting to MQTT broker at %s:%d…",
        MQTT_BROKER_HOST, MQTT_BROKER_PORT,
    )
    mqtt_listener.start(block=False, connect_timeout=10.0)

    # ── Start stale-data watchdog ────────
    sensor_processor.start_watchdog()

def _verify_strict_mqtt_acl() -> bool:
    """Explicitly publish an unauthorized packet. If Mosquitto allows it, ACLs are misconfigured."""
    import paho.mqtt.client as mqtt_client
    
    test_client = mqtt_client.Client(client_id="ghost-tester-check")
    success_flag = {"allowed": False}
    
    def on_publish(client, userdata, mid):
        success_flag["allowed"] = True # It successfully published
        
    test_client.on_publish = on_publish
    
    try:
        test_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT)
        test_client.loop_start()
        # Attempt to publish to ghost device
        msg = test_client.publish("sensors/data/GHOST_DEVICE_TESTER", "{}", qos=1)
        msg.wait_for_publish(timeout=2.0)
        
        test_client.loop_stop()
        test_client.disconnect()
        
        # If it reached here and success_flag is True, it bypassed ACL! We expect it to FAIL or TIMEOUT publishing.
        return not success_flag["allowed"]
    except Exception as e:
        log.error(f"MQTT ACL Test setup failed (Broker offline?): {str(e)}")
        return False


def _banner() -> None:
    log.info("=" * 60)
    log.info("  Smart Fire Detection — Raspberry Pi Edge Controller")
    log.info("  Building: %-20s", BUILDING_ID)
    log.info("  PID     : %d", os.getpid())
    log.info("=" * 60)


if __name__ == "__main__":
    main()
