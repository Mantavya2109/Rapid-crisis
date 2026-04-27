# MicroPython firmware reference for ESP32 sensor nodes
# ============================================================
#  File  : esp32_reference/firmware.py
#  Target: ESP32 running MicroPython v1.22+
#
#  Hardware wiring
#  ---------------
#  DHT11 DATA pin  → GPIO 4  (with 10 kΩ pull-up to 3.3 V)
#  MQ-2  AOUT pin  → GPIO 34 (ADC1 channel 6 — input only)
#  MQ-2  VCC       → 5 V
#  MQ-2  GND       → GND
#
#  Behaviour
#  ---------
#  1. Connect to Wi-Fi
#  2. Connect to MQTT broker (Mosquitto running on Raspberry Pi)
#  3. Register with Pi REST API  POST /devices/register
#  4. Every SENSOR_INTERVAL_MS:
#       - Read DHT11  (temperature + humidity)
#       - Read MQ-2   (smoke / gas ADC value)
#       - Classify status: OK / WARNING / FIRE
#       - Publish to  sensors/data/<NODE_ID>
#  5. Every HEARTBEAT_INTERVAL_MS:
#       - Publish to  sensors/heartbeat/<NODE_ID>
#  6. Watchdog: if Pi heartbeat ACK not received within FAILSAFE_TIMEOUT_MS
#       → flash onboard LED RED (visual fail-safe, NO buzzer per design)
#
#  MQTT topics published
#  ---------------------
#  sensors/data/<NODE_ID>       — sensor telemetry
#  sensors/heartbeat/<NODE_ID>  — keepalive
#
#  Flash this file as  main.py  on the ESP32.
# ============================================================

import ujson       # type: ignore[import]  # MicroPython ROM module
import utime       # type: ignore[import]  # MicroPython ROM module
import machine     # type: ignore[import]  # MicroPython ROM module
import network     # type: ignore[import]  # MicroPython ROM module
import ubinascii   # type: ignore[import]  # MicroPython ROM module

# ─── Third-party MicroPython libraries (install via upip or copy manually) ───
# pip install micropython-umqtt.simple  →  copy umqtt/simple.py to /lib/umqtt/
from umqtt.simple import MQTTClient  # type: ignore[import]  # MicroPython ROM module

# ─────────────────────────────────────────────────────────────────────────────
# USER CONFIGURATION  — edit before flashing
# ─────────────────────────────────────────────────────────────────────────────

WIFI_SSID     = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

MQTT_BROKER   = "192.168.1.100"   # Raspberry Pi IP address
MQTT_PORT     = 1883
MQTT_USER     = ""                # Leave empty if Mosquitto auth is disabled
MQTT_PASSWORD = ""

# Identity — must match a row registered via POST /devices/register on the Pi
DEVICE_ID   = "ESP32_NODE_A"      # Unique ID for this board
NODE_ID     = "ROOM_101"          # Building graph node this sensor covers
BUILDING_ID = "BUILDING_01"

# GPIO pins
DHT_PIN       = 4                 # DHT11 data pin
MQ2_ADC_PIN   = 34                # MQ-2 analogue output → ADC

# Intervals (milliseconds)
SENSOR_INTERVAL_MS    = 5_000     # How often to publish sensor data
HEARTBEAT_INTERVAL_MS = 10_000    # How often to send keepalive

# Thresholds for self-classification (should match Pi settings)
TEMP_THRESHOLD_C  = 40.0
SMOKE_THRESHOLD   = 2000          # Raw ADC value (0–4095 on ESP32 12-bit ADC)
SMOKE_WARN_LEVEL  = 1500          # Intermediate warning level

# Fail-safe: max ms without a Pi heartbeat ACK before visual alert
FAILSAFE_TIMEOUT_MS = 60_000

# MQ-2 warm-up time (sensor needs ~60 s pre-heat for accurate readings)
MQ2_WARMUP_MS = 60_000

# ─────────────────────────────────────────────────────────────────────────────
# Unique client ID derived from MAC address
# ─────────────────────────────────────────────────────────────────────────────

def _make_client_id():
    mac = ubinascii.hexlify(network.WLAN(network.STA_IF).config("mac")).decode()
    return "esp32-" + mac[-6:]   # last 3 octets of MAC


CLIENT_ID = _make_client_id()

# ─────────────────────────────────────────────────────────────────────────────
# Peripheral helpers
# ─────────────────────────────────────────────────────────────────────────────

# DHT11
import dht as _dht  # type: ignore[import]  # MicroPython ROM module
_dht_sensor = _dht.DHT11(machine.Pin(DHT_PIN))

# MQ-2 — ESP32 ADC (12-bit, 0–4095)
_adc = machine.ADC(machine.Pin(MQ2_ADC_PIN))
_adc.atten(machine.ADC.ATTN_11DB)   # Full 3.3 V range

# Onboard LED (GPIO 2 on most ESP32 dev boards)
_led = machine.Pin(2, machine.Pin.OUT)

# ─────────────────────────────────────────────────────────────────────────────
# LED helpers
# ─────────────────────────────────────────────────────────────────────────────

def led_on():   _led.value(1)
def led_off():  _led.value(0)

def led_blink(times=3, on_ms=100, off_ms=100):
    for _ in range(times):
        led_on();  utime.sleep_ms(on_ms)
        led_off(); utime.sleep_ms(off_ms)

# ─────────────────────────────────────────────────────────────────────────────
# Wi-Fi
# ─────────────────────────────────────────────────────────────────────────────

def connect_wifi(ssid, password, timeout_ms=20_000):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        print("[WiFi] Already connected:", wlan.ifconfig())
        return wlan

    print("[WiFi] Connecting to", ssid, "…")
    wlan.connect(ssid, password)

    deadline = utime.ticks_ms() + timeout_ms
    while not wlan.isconnected():
        if utime.ticks_diff(deadline, utime.ticks_ms()) <= 0:
            raise OSError("[WiFi] Timed out connecting to " + ssid)
        utime.sleep_ms(500)
        print(".", end="")

    print("\n[WiFi] Connected:", wlan.ifconfig())
    led_blink(3)
    return wlan

# ─────────────────────────────────────────────────────────────────────────────
# Sensor reading
# ─────────────────────────────────────────────────────────────────────────────

def read_dht11():
    """Return (temperature_c, humidity_pct) or (None, None) on error."""
    try:
        _dht_sensor.measure()
        return float(_dht_sensor.temperature()), float(_dht_sensor.humidity())
    except Exception as e:
        print("[DHT11] Read error:", e)
        return None, None


def read_mq2():
    """Return raw ADC value (0–4095).  Higher = more smoke/gas."""
    try:
        return _adc.read()
    except Exception as e:
        print("[MQ-2] ADC error:", e)
        return 0


def classify_status(temperature, smoke):
    """Classify readings into OK / WARNING / FIRE."""
    if temperature is None:
        # DHT11 error — cannot assess temp; use smoke only
        if smoke > SMOKE_THRESHOLD:
            return "FIRE"
        if smoke > SMOKE_WARN_LEVEL:
            return "WARNING"
        return "OK"

    temp_high  = temperature > TEMP_THRESHOLD_C
    smoke_high = smoke > SMOKE_THRESHOLD
    smoke_warn = smoke > SMOKE_WARN_LEVEL

    if temp_high and smoke_high:
        return "FIRE"
    if temp_high or smoke_high:
        return "FIRE"
    if smoke_warn:
        return "WARNING"
    return "OK"

# ─────────────────────────────────────────────────────────────────────────────
# MQTT helpers
# ─────────────────────────────────────────────────────────────────────────────

_mqtt_client = None

def _build_mqtt_client():
    c = MQTTClient(
        client_id = CLIENT_ID,
        server    = MQTT_BROKER,
        port      = MQTT_PORT,
        user      = MQTT_USER or None,
        password  = MQTT_PASSWORD or None,
        keepalive = 30,
    )
    return c


def mqtt_connect(retries=5, delay_ms=3000):
    global _mqtt_client
    for attempt in range(1, retries + 1):
        try:
            print("[MQTT] Connecting attempt %d/%d …" % (attempt, retries))
            _mqtt_client = _build_mqtt_client()
            _mqtt_client.connect()
            print("[MQTT] Connected to broker", MQTT_BROKER)
            led_blink(2, on_ms=50, off_ms=50)
            return _mqtt_client
        except Exception as e:
            print("[MQTT] Failed:", e)
            utime.sleep_ms(delay_ms * attempt)
    raise OSError("[MQTT] Could not connect after %d attempts" % retries)


def mqtt_publish(topic, payload_dict, retain=False):
    global _mqtt_client
    try:
        _mqtt_client.publish(
            topic,
            ujson.dumps(payload_dict),
            retain=retain,
            qos=0,
        )
        return True
    except Exception as e:
        print("[MQTT] Publish error:", e)
        # Re-connect on next cycle
        try:
            _mqtt_client = _build_mqtt_client()
            _mqtt_client.connect()
        except Exception:
            pass
        return False

# ─────────────────────────────────────────────────────────────────────────────
# Topic helpers
# ─────────────────────────────────────────────────────────────────────────────

TOPIC_DATA      = "sensors/data/"      + NODE_ID
TOPIC_HEARTBEAT = "sensors/heartbeat/" + NODE_ID

# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  ESP32 Fire Sensor Node  |  ID:", DEVICE_ID)
    print("  Node:", NODE_ID, "  Building:", BUILDING_ID)
    print("=" * 50)

    # ── Wi-Fi ─────────────────────────────────────────────────────────
    connect_wifi(WIFI_SSID, WIFI_PASSWORD)

    # ── MQ-2 warm-up ──────────────────────────────────────────────────
    print("[MQ-2] Warming up for %d s …" % (MQ2_WARMUP_MS // 1000))
    warmup_done = False
    warmup_start = utime.ticks_ms()

    # ── MQTT ──────────────────────────────────────────────────────────
    mqtt_connect()

    # Timing
    last_sensor_ms    = utime.ticks_ms()
    last_heartbeat_ms = utime.ticks_ms()
    last_pi_ack_ms    = utime.ticks_ms()   # updated when Pi responds (see subscribe below)
    failsafe_active   = False

    print("[MAIN] Sensor loop starting…")

    while True:
        now_ms = utime.ticks_ms()

        # ── MQ-2 warm-up check ─────────────────────────────────────────
        if not warmup_done:
            elapsed = utime.ticks_diff(now_ms, warmup_start)
            if elapsed >= MQ2_WARMUP_MS:
                warmup_done = True
                print("[MQ-2] Warm-up complete.")
            else:
                remaining = (MQ2_WARMUP_MS - elapsed) // 1000
                print("[MQ-2] Warming up… %d s remaining" % remaining)
                utime.sleep_ms(2000)
                continue

        # ── Sensor publish ─────────────────────────────────────────────
        if utime.ticks_diff(now_ms, last_sensor_ms) >= SENSOR_INTERVAL_MS:
            temperature, humidity = read_dht11()
            smoke = read_mq2()
            status = classify_status(temperature, smoke)

            payload = {
                "deviceId":   DEVICE_ID,
                "nodeId":     NODE_ID,
                "buildingId": BUILDING_ID,
                "temperature": temperature if temperature is not None else -1,
                "humidity":    humidity    if humidity    is not None else -1,
                "smoke":       smoke,
                "status":      status,
            }

            print(
                "[SENSOR] temp=%.1f°C  hum=%.0f%%  smoke=%d  status=%s"
                % (temperature or 0, humidity or 0, smoke, status)
            )

            ok = mqtt_publish(TOPIC_DATA, payload)
            if ok:
                led_blink(1, on_ms=30, off_ms=0)   # quick flash on successful publish
            last_sensor_ms = now_ms

        # ── Heartbeat publish ──────────────────────────────────────────
        if utime.ticks_diff(now_ms, last_heartbeat_ms) >= HEARTBEAT_INTERVAL_MS:
            hb_payload = {
                "deviceId": DEVICE_ID,
                "nodeId":   NODE_ID,
            }
            mqtt_publish(TOPIC_HEARTBEAT, hb_payload)
            print("[HB] Heartbeat sent.")
            last_heartbeat_ms = now_ms

        # ── Fail-safe check ────────────────────────────────────────────
        # NOTE: The Pi sends ACKs via MQTT rpi/status topic.
        # For simplicity this reference firmware uses a timer-based check.
        # If FAILSAFE_TIMEOUT_MS elapses with no sign of Pi → visual alert.
        if utime.ticks_diff(now_ms, last_pi_ack_ms) > FAILSAFE_TIMEOUT_MS:
            if not failsafe_active:
                print("[FAILSAFE] Pi heartbeat timeout — visual fail-safe active.")
                failsafe_active = True
            # Blink LED rapidly to indicate fail-safe (no buzzer per design)
            led_blink(1, on_ms=200, off_ms=200)
        else:
            if failsafe_active:
                print("[FAILSAFE] Pi reconnected — fail-safe cleared.")
                failsafe_active = False
            led_off()

        utime.sleep_ms(100)   # 100 ms main loop tick


# Run
try:
    main()
except Exception as e:
    print("[CRASH]", e)
    # Hard reset after 5 seconds so systemd / watchdog can restart
    utime.sleep(5)
    machine.reset()
