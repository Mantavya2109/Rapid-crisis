# ESP32 Sensor Node — Quick-Start Guide

This directory contains the MicroPython firmware reference for an ESP32 sensor node.
Each board reads a **DHT11** (temperature + humidity) and **MQ-2** (smoke/gas) sensor
and publishes readings to the Raspberry Pi via MQTT.

---

## Hardware Wiring

```
ESP32 Dev Board
┌────────────────────────────────┐
│  GPIO 4  ──────────────────────┼── DHT11 DATA (+ 10 kΩ pull-up to 3.3 V)
│  3.3 V   ──────────────────────┼── DHT11 VCC
│  GND     ──────────────────────┼── DHT11 GND
│                                │
│  GPIO 34 (ADC1_CH6) ──────────┼── MQ-2 AOUT  (analogue, 0–3.3 V)
│  5 V     ──────────────────────┼── MQ-2 VCC   (needs 5 V for heater)
│  GND     ──────────────────────┼── MQ-2 GND
│                                │
│  GPIO 2  (onboard LED) ───────┼── Visual fail-safe indicator
└────────────────────────────────┘
```

> **Note:** GPIO 34 is input-only (no internal pull-up). ADC is 12-bit (0–4095).
> The MQ-2 heater draws ~180 mA — power from the board's 5 V pin or an external supply.
> **Warm-up time:** allow 60 seconds after power-on for accurate readings.

---

## Raspberry Pi Side (MQTT broker)

The Pi runs **Mosquitto** as a local MQTT broker (`localhost:1883`).  
After running `sudo bash setup.sh` on the Pi:

```bash
# Verify Mosquitto is running
sudo systemctl status mosquitto

# Monitor all incoming sensor messages live
mosquitto_sub -t "sensors/#" -v
```

---

## Flashing MicroPython

```bash
# 1. Install esptool
pip install esptool

# 2. Erase flash
esptool.py --port /dev/ttyUSB0 erase_flash

# 3. Flash MicroPython firmware (download from https://micropython.org/download/esp32/)
esptool.py --port /dev/ttyUSB0 --baud 460800 \
  write_flash -z 0x1000 esp32-generic-XXXXXXXXXX.bin

# 4. Copy umqtt library (required)
pip install mpremote
mpremote connect /dev/ttyUSB0 mip install umqtt.simple

# 5. Edit firmware.py — set your WiFi, MQTT broker IP, DEVICE_ID, NODE_ID

# 6. Upload as main.py (auto-runs on boot)
mpremote connect /dev/ttyUSB0 cp firmware.py :main.py

# 7. Open REPL to watch output
mpremote connect /dev/ttyUSB0 repl
```

---

## Device Registration

Before the ESP32's heartbeats are tracked, register it on the Pi:

```bash
curl -X POST http://raspberrypi.local:5000/devices/register \
  -H "Content-Type: application/json" \
  -d '{
    "deviceId":   "ESP32_NODE_A",
    "buildingId": "BUILDING_01",
    "nodeId":     "ROOM_101",
    "type":       "SENSOR",
    "ip":         "192.168.1.50"
  }'
```

---

## MQTT Topics

| Direction       | Topic                              | Publisher | Payload fields                                                   |
|-----------------|------------------------------------|-----------|------------------------------------------------------------------|
| ESP32 → Pi      | `sensors/data/<nodeId>`            | ESP32     | `deviceId`, `nodeId`, `buildingId`, `temperature`, `smoke`, `status` |
| ESP32 → Pi      | `sensors/heartbeat/<nodeId>`       | ESP32     | `deviceId`, `nodeId`                                             |
| Pi → all        | `rpi/status`                       | Pi        | `status` (`ONLINE`/`OFFLINE`), `ts`                              |

### Example payloads

**Sensor data** (published every 5 s):
```json
{
  "deviceId":    "ESP32_NODE_A",
  "nodeId":      "ROOM_101",
  "buildingId":  "BUILDING_01",
  "temperature": 28.5,
  "humidity":    62.0,
  "smoke":       150,
  "status":      "OK"
}
```

**Heartbeat** (published every 10 s):
```json
{
  "deviceId": "ESP32_NODE_A",
  "nodeId":   "ROOM_101"
}
```

---

## Status Classification (on ESP32)

The firmware performs a preliminary classification before publishing:

| Condition                              | `status` field |
|----------------------------------------|----------------|
| temp > 40 °C **and** smoke > 2000 ADC | `"FIRE"`       |
| temp > 40 °C **or** smoke > 2000 ADC  | `"FIRE"`       |
| smoke > 1500 ADC (warn level)          | `"WARNING"`    |
| All readings below thresholds          | `"OK"`         |

The Raspberry Pi re-classifies independently using its own thresholds and drives
the WS2812B LED strip accordingly — the ESP32 classification is advisory only.

---

## LED Zone Status on Pi

After the Pi processes a reading, the corresponding LED zone changes colour:

| Pi classification | LED colour    | Animation     |
|-------------------|---------------|---------------|
| `NORMAL`          | 🟢 Green      | Solid         |
| `WARNING`         | 🟡 Yellow     | Solid         |
| `DANGER`          | 🔴 Red        | Pulsing       |
| `OFFLINE`         | 🔵 Dim blue   | Solid         |

---

## Testing Without Hardware (MQTT simulator)

```bash
# Install mosquitto-clients on any machine on the same LAN
sudo apt install mosquitto-clients

# Simulate a normal reading
mosquitto_pub -h raspberrypi.local -t sensors/data/ROOM_101 \
  -m '{"deviceId":"SIM_01","temperature":25,"smoke":100,"status":"OK"}'

# Simulate a warning
mosquitto_pub -h raspberrypi.local -t sensors/data/ROOM_101 \
  -m '{"deviceId":"SIM_01","temperature":43,"smoke":1600,"status":"WARNING"}'

# Simulate a fire
mosquitto_pub -h raspberrypi.local -t sensors/data/ROOM_101 \
  -m '{"deviceId":"SIM_01","temperature":58,"smoke":2800,"status":"FIRE"}'

# Check the Pi's LED zone state via REST API
curl http://raspberrypi.local:5000/zones
curl http://raspberrypi.local:5000/sensors
curl http://raspberrypi.local:5000/mqtt/status
```
