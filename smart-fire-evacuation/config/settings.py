"""
config/settings.py
------------------
Centralized configuration for the Smart Fire Evacuation System.
"""

import os

# ─────────────────────────────────────────────
# Server
# ─────────────────────────────────────────────
HOST = os.getenv("SERVER_HOST", "0.0.0.0")
PORT = int(os.getenv("SERVER_PORT", 5000))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# TLS Configuration for API (if running standalone)
API_TLS_ENABLED = os.getenv("API_TLS_ENABLED", "false").lower() == "true"
API_CERTFILE    = os.getenv("API_CERTFILE", "")
API_KEYFILE     = os.getenv("API_KEYFILE", "")

# ─────────────────────────────────────────────
# High Availability (HA) Failover
# ─────────────────────────────────────────────
HA_MODE_ENABLED = os.getenv("HA_MODE_ENABLED", "false").lower() == "true"
HA_ROLE         = os.getenv("HA_ROLE", "PRIMARY").upper() # PRIMARY or SECONDARY
HA_PEER_URL     = os.getenv("HA_PEER_URL", "http://192.168.1.100:5000")
HA_HEARTBEAT_INTERVAL = int(os.getenv("HA_HEARTBEAT_INTERVAL", 10))
HA_TAKEOVER_TIMEOUT   = int(os.getenv("HA_TAKEOVER_TIMEOUT", 30))

# ─────────────────────────────────────────────
# Building identity
# ─────────────────────────────────────────────
BUILDING_ID = os.getenv("BUILDING_ID", "BUILDING_01")

# ─────────────────────────────────────────────
# Thresholds
# ─────────────────────────────────────────────
TEMP_THRESHOLD  = float(os.getenv("TEMP_THRESHOLD",  40.0))   # °C
SMOKE_THRESHOLD = float(os.getenv("SMOKE_THRESHOLD", 200.0))  # PPM

# ─────────────────────────────────────────────
# Alert debounce
# ─────────────────────────────────────────────
ALERT_DEBOUNCE_SEC = int(os.getenv("ALERT_DEBOUNCE_SEC", 5))

# ─────────────────────────────────────────────
# Device heartbeat
# ─────────────────────────────────────────────
HEARTBEAT_TIMEOUT_SEC = int(os.getenv("HEARTBEAT_TIMEOUT_SEC", 30))

# ─────────────────────────────────────────────
# ESP32 Fail-Safe Behaviour
# ─────────────────────────────────────────────
# If an ESP32 does not receive a heartbeat ACK from the Pi within this window
# (milliseconds) it should enter fail-safe mode: blink ALL onboard LEDs RED.
ESP32_FAILSAFE_BLINK_MS = int(os.getenv("ESP32_FAILSAFE_BLINK_MS", 60_000))  # 60 s

# ─────────────────────────────────────────────
# Hazard model
# ─────────────────────────────────────────────
HAZARD_DECAY_FACTOR    = float(os.getenv("HAZARD_DECAY_FACTOR",    0.5))
NEIGHBOR_HAZARD_SPREAD = float(os.getenv("NEIGHBOR_HAZARD_SPREAD", 0.3))

# ─────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────
BASE_DIR          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH_CONFIG_PATH = os.path.join(BASE_DIR, "config", "building_graph.json")
LOG_DIR           = os.path.join(BASE_DIR, "logs")
DATA_DIR          = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))

# Zone layout persisted here so it survives Pi reboots
ZONE_CONFIG_PATH  = os.getenv(
    "ZONE_CONFIG_PATH",
    os.path.join(BASE_DIR, "config", "zone_config.json"),
)

# ─────────────────────────────────────────────
# SQLite persistence
# ─────────────────────────────────────────────
DB_FLUSH_INTERVAL_SEC = int(os.getenv("DB_FLUSH_INTERVAL_SEC", 3))

# ─────────────────────────────────────────────────────────────────────
# Trend / predictive analysis
# ─────────────────────────────────────────────────────────────────────
# Number of readings kept per node for rate-of-rise calculation
TREND_WINDOW_SIZE          = int(os.getenv("TREND_WINDOW_SIZE",          5))
# Rise rate [units/min] that triggers PREDICTIVE_FIRE (low urgency)
TREND_RISE_RATE_WARNING    = float(os.getenv("TREND_RISE_RATE_WARNING",  10.0))
# Rise rate [units/min] that triggers PREDICTIVE_FIRE (high urgency)
PREDICTIVE_FIRE_RISE_RATE  = float(os.getenv("PREDICTIVE_FIRE_RISE_RATE", 25.0))

# ─────────────────────────────────────────────────────────────────────
# Critical-fire thresholds
# ─────────────────────────────────────────────────────────────────────
# Temperature above which node is instantly CRITICAL (never just DANGER)
CRITICAL_TEMP_THRESHOLD    = float(os.getenv("CRITICAL_TEMP_THRESHOLD",  65.0))
# Smoke multiple above SMOKE_THRESHOLD that triggers CRITICAL
CRITICAL_SMOKE_MULTIPLIER  = float(os.getenv("CRITICAL_SMOKE_MULTIPLIER", 3.0))
# How many consecutive DANGER readings before auto-escalating to CRITICAL
CONSECUTIVE_DANGER_CRITICAL = int(os.getenv("CONSECUTIVE_DANGER_CRITICAL",  3))

# ─────────────────────────────────────────────────────────────────────
# Stale-data watchdog
# ─────────────────────────────────────────────────────────────────────
# Seconds of silence before a node is promoted to OFFLINE
STALE_DATA_TIMEOUT_SEC = int(os.getenv("STALE_DATA_TIMEOUT_SEC", 45))

# ─────────────────────────────────────────────────────────────────────
# Cloud event filtering
# ─────────────────────────────────────────────────────────────────────
# How often to force-send a periodic summary even if state hasn't changed
CLOUD_SUMMARY_INTERVAL_SEC = int(os.getenv("CLOUD_SUMMARY_INTERVAL_SEC", 300))  # 5 min
# Minimum seconds between repeated sends of the same state (quiet window)
MIN_REPEAT_INTERVAL_SEC    = int(os.getenv("MIN_REPEAT_INTERVAL_SEC",     30))

# ─────────────────────────────────────────────
# MQTT Broker  (Mosquitto running locally on the Pi)
# ─────────────────────────────────────────────
MQTT_BROKER_HOST   = os.getenv("MQTT_BROKER_HOST",   "localhost")
MQTT_BROKER_PORT   = int(os.getenv("MQTT_BROKER_PORT",   1883))
MQTT_USERNAME      = os.getenv("MQTT_USERNAME",       "")
MQTT_PASSWORD      = os.getenv("MQTT_PASSWORD",       "")
MQTT_CLIENT_ID     = os.getenv("MQTT_CLIENT_ID",      "rpi-edge-controller")
MQTT_QOS           = int(os.getenv("MQTT_QOS",         1))
MQTT_KEEPALIVE_SEC = int(os.getenv("MQTT_KEEPALIVE_SEC", 60))

# MQTT TLS
MQTT_TLS_ENABLED = os.getenv("MQTT_TLS_ENABLED", "false").lower() == "true"
MQTT_CA_CERTS    = os.getenv("MQTT_CA_CERTS", "")
MQTT_CERTFILE    = os.getenv("MQTT_CERTFILE", "")
MQTT_KEYFILE     = os.getenv("MQTT_KEYFILE", "")

# Wildcard subscriptions the Pi listens on:
#   sensors/data/+       → telemetry from all ESP32 nodes
#   sensors/heartbeat/+  → keepalive pings from all ESP32 nodes
MQTT_TOPIC_SENSOR    = os.getenv("MQTT_TOPIC_SENSOR",    "sensors/data/+")
MQTT_TOPIC_HEARTBEAT = os.getenv("MQTT_TOPIC_HEARTBEAT", "sensors/heartbeat/+")

# ─────────────────────────────────────────────────────────────────────
# Processing queue (load protection)
# ─────────────────────────────────────────────────────────────────────
# Max pending MQTT messages before oldest is evicted/dropped
PROCESSING_QUEUE_SIZE    = int(os.getenv("PROCESSING_QUEUE_SIZE",   200))
# Worker threads draining the queue (2 is ideal for a Pi 4)
PROCESSING_WORKER_COUNT  = int(os.getenv("PROCESSING_WORKER_COUNT",   2))
# Per-node burst: max messages accepted per PROCESSING_BUCKET_SEC window
PROCESSING_BURST_PER_NODE = int(os.getenv("PROCESSING_BURST_PER_NODE", 10))
# Token-bucket refill window in seconds
PROCESSING_BUCKET_SEC    = float(os.getenv("PROCESSING_BUCKET_SEC",  60.0))

# ─────────────────────────────────────────────────────────────────────
# Recovery cooldown
# ─────────────────────────────────────────────────────────────────────
# Seconds to wait after all fire nodes clear before declaring RECOVERED
RECOVER_COOLDOWN_SEC = int(os.getenv("RECOVER_COOLDOWN_SEC", 30))

# ─────────────────────────────────────────────
# WS2812B LED Strip — rpi_ws281x (direct GPIO)
# ─────────────────────────────────────────────
# GPIO 18 is the hardware-PWM pin used by rpi_ws281x.
# Must run as root or with SYS_RAWIO capability on the Pi.
LED_GPIO_PIN   = int(os.getenv("LED_GPIO_PIN",    18))
LED_COUNT      = int(os.getenv("LED_COUNT",        60))   # total LEDs on the strip
LED_FREQ_HZ    = int(os.getenv("LED_FREQ_HZ",     800_000))
LED_DMA        = int(os.getenv("LED_DMA",           5))
LED_BRIGHTNESS = int(os.getenv("LED_BRIGHTNESS",  128))   # 0 (off) – 255 (full)
LED_INVERT     = os.getenv("LED_INVERT", "false").lower() == "true"
LED_CHANNEL    = int(os.getenv("LED_CHANNEL",       0))   # 0 → GPIO18; 1 → GPIO13

# ─────────────────────────────────────────────
# Cloud API
# ─────────────────────────────────────────────
CLOUD_BASE_URL       = os.getenv("CLOUD_BASE_URL",       "https://your-cloud-backend.com")
CLOUD_API_KEY        = os.getenv("CLOUD_API_KEY",        "")
CLOUD_TIMEOUT_SEC    = int(os.getenv("CLOUD_TIMEOUT_SEC",    5))
CLOUD_RETRY_ATTEMPTS = int(os.getenv("CLOUD_RETRY_ATTEMPTS", 3))

CLOUD_API_URL       = os.getenv("CLOUD_FIRE_ALERT_URL",  f"{CLOUD_BASE_URL}/fire-alert")
CLOUD_LED_BATCH_URL = os.getenv("CLOUD_LED_BATCH_URL",   f"{CLOUD_BASE_URL}/led/batch")
CLOUD_REGISTER_URL  = os.getenv("CLOUD_REGISTER_URL",    f"{CLOUD_BASE_URL}/devices/register")
CLOUD_TELEMETRY_URL = os.getenv("CLOUD_TELEMETRY_URL",   f"{CLOUD_BASE_URL}/telemetry")

# ─────────────────────────────────────────────
# API Security & Validation
# ─────────────────────────────────────────────
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")

# Reject requests whose X-Timestamp is older than this (anti-replay).
# 30 seconds is a good balance: tolerates clock drift, blocks replays.
REPLAY_WINDOW_SEC = int(os.getenv("REPLAY_WINDOW_SEC", 30))
# Require strict timestamp presence and validation
REQUIRE_TIMESTAMP = os.getenv("REQUIRE_TIMESTAMP", "true").lower() == "true"

# JSON Web Token Authentication for Devices
JWT_ALGORITHM  = os.getenv("JWT_ALGORITHM", "RS256")
# RS256 Keys 
JWT_PRIVATE_KEY = os.getenv("JWT_PRIVATE_KEY", "")
JWT_PUBLIC_KEY  = os.getenv("JWT_PUBLIC_KEY", "")

DEVICE_TOKEN_EXPIRE_SEC = int(os.getenv("DEVICE_TOKEN_EXPIRE_SEC", 86400 * 30)) # 30 days
OTA_PUBLIC_KEY  = os.getenv("OTA_PUBLIC_KEY", JWT_PUBLIC_KEY)

# ─────────────────────────────────────────────
# Rate Limiting
# ─────────────────────────────────────────────
# In-memory limiter resets on Pi restart (limitation acknowledged).
# To persist across restarts, set RATELIMIT_STORAGE_URI:
#   Redis (if available):  RATELIMIT_STORAGE_URI=redis://localhost:6379
#   SQLite (Pi-native):    RATELIMIT_STORAGE_URI=sqlite:////path/to/data/rate_limits.db
#   Memory (default):      RATELIMIT_STORAGE_URI=memory://
RATE_LIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
RATE_LIMIT_SENSOR      = os.getenv("RATE_LIMIT_SENSOR",   "60 per minute")
RATE_LIMIT_EVACUATE    = os.getenv("RATE_LIMIT_EVACUATE",  "10 per minute")
RATE_LIMIT_DEFAULT     = os.getenv("RATE_LIMIT_DEFAULT",  "30 per minute")

# ─────────────────────────────────────────────
# ESP32 LED Controller
# ─────────────────────────────────────────────
LED_ENDPOINT       = "/led"
LED_BATCH_ENDPOINT = "/led/batch"
LED_TIMEOUT_SEC    = int(os.getenv("LED_TIMEOUT_SEC",    3))
LED_RETRY_ATTEMPTS = int(os.getenv("LED_RETRY_ATTEMPTS", 2))
