#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# setup.sh — One-time setup for Raspberry Pi 4
#
# Run as root (or with sudo) for Mosquitto install and systemd service:
#   sudo bash setup.sh
#
# The script is safe to re-run (idempotent).
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="fire-evacuation"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PI_USER="${SUDO_USER:-pi}"

echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║  Smart Fire Evacuation — Raspberry Pi Setup       ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# ── 1. System dependencies ────────────────────────────────────────────
echo ">>> [1/6] Updating apt and installing system packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    mosquitto \
    mosquitto-clients \
    python3-dev \
    python3-pip \
    python3-venv \
    gcc \
    build-essential \
    libssl-dev

# ── 2. Configure Mosquitto ────────────────────────────────────────────
echo ">>> [2/6] Configuring Mosquitto MQTT broker..."
MOSQ_CONF="/etc/mosquitto/conf.d/fire-evac.conf"

if [ ! -f "${MOSQ_CONF}" ]; then
cat > "${MOSQ_CONF}" << 'EOF'
# Mosquitto config — Smart Fire Evacuation System
listener 1883
allow_anonymous true

# Increase max in-flight messages for reliability
max_inflight_messages 20

# Persistent sessions on broker restart
persistence true
persistence_location /var/lib/mosquitto/

# Log to syslog (viewable via: journalctl -u mosquitto)
log_dest syslog
log_type error
log_type warning
log_type notice
EOF
fi

systemctl enable  mosquitto
systemctl restart mosquitto
echo "    ✅ Mosquitto running on port 1883"

# ── 3. Python virtual environment ─────────────────────────────────────
echo ">>> [3/6] Creating Python virtual environment..."
cd "${SCRIPT_DIR}"
python3 -m venv venv
source venv/bin/activate

echo ">>> [4/6] Installing Python dependencies..."
pip install --upgrade pip --quiet
# rpi_ws281x needs --break-system-packages on newer Pi OS / use venv
pip install -r requirements.txt --quiet

# rpi_ws281x may need to be installed system-wide on the Pi for GPIO access
# Uncomment the next line if you get permission errors:
# sudo pip3 install rpi_ws281x

echo "    ✅ Python packages installed"

# ── 4. Directory structure ────────────────────────────────────────────
echo ">>> [5/6] Creating runtime directories..."
mkdir -p logs data config
touch logs/.gitkeep data/.gitkeep

# Copy env template if .env doesn't exist yet
if [ ! -f .env ]; then
    cp .env.example .env
    echo "    ⚠️  .env created from .env.example — update CLOUD_BASE_URL and other values!"
fi

# ── 5. systemd service ────────────────────────────────────────────────
echo ">>> [6/6] Installing systemd service (${SERVICE_NAME})..."
VENV_PYTHON="${SCRIPT_DIR}/venv/bin/python3"

cat > "${SERVICE_FILE}" << EOF
[Unit]
Description=Smart Fire Evacuation — Raspberry Pi Edge Controller
Documentation=https://github.com/your-repo/smart-fire-evacuation
After=network-online.target mosquitto.service
Wants=network-online.target
Requires=mosquitto.service

[Service]
Type=simple
User=root
WorkingDirectory=${SCRIPT_DIR}
EnvironmentFile=${SCRIPT_DIR}/.env
ExecStart=${VENV_PYTHON} main.py
Restart=on-failure
RestartSec=5
# Watchdog: systemd kills and restarts if no keep-alive within 90 s
WatchdogSec=90
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

# GPIO / PWM access requires elevated privileges
AmbientCapabilities=CAP_SYS_RAWIO CAP_SYS_NICE

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
echo "    ✅ systemd service installed"

# ── Done ──────────────────────────────────────────────────────────────
echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║  ✅  Setup complete!                                          ║"
echo "╠═══════════════════════════════════════════════════════════════╣"
echo "║  Edit .env (especially CLOUD_BASE_URL) before starting.      ║"
echo "║                                                               ║"
echo "║  Start service:                                               ║"
echo "║    sudo systemctl start fire-evacuation                      ║"
echo "║                                                               ║"
echo "║  View logs:                                                   ║"
echo "║    journalctl -u fire-evacuation -f                          ║"
echo "║                                                               ║"
echo "║  Test MQTT (from another terminal):                          ║"
echo "║    mosquitto_pub -t sensors/data/ROOM_101 -m '{             ║"
echo "║      \"deviceId\":\"TEST\",\"temperature\":25,\"smoke\":100}'       ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
