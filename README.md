# Rapid Crisis — Smart Fire Evacuation (Full Repo)

Rapid Crisis is an end-to-end smart fire evacuation prototype:

- **Frontend (React + Vite)**: a digital-twin dashboard + building graph editor.
- **Cloud backend (Node.js + Express)**: ingests telemetry/fire alerts, stores building graphs in Firestore, and pushes real-time events to the dashboard via **SSE**.
- **Edge controller (Python / Raspberry Pi)**: listens to ESP32 sensor events over MQTT, runs local pathfinding + LED routing, and syncs key events to the cloud.

This README documents the whole repository and gives setup instructions for **Windows development**, plus a **Raspberry Pi** guide for the edge controller.

---

## Repository layout

```
backend/                Node/Express cloud backend (Firestore + SSE)
frontend/               React/Vite dashboard + building editor
smart-fire-evacuation/  Raspberry Pi edge controller (Flask API + MQTT + LED)
	esp32_reference/       Reference ESP32 firmware + notes (prototype)
```

---

## System architecture (high level)

1. **Building setup** (dashboard → cloud)

   - You design a building graph in the frontend editor and deploy it.
   - The backend stores nodes/edges in **Firestore** (and can optionally push the graph to a Pi).
2. **Live operation** (ESP32 → Pi → cloud → dashboard)

   - ESP32 devices publish telemetry/heartbeat via **MQTT (Mosquitto)**.
   - The Raspberry Pi edge controller:
     - validates devices,
     - tracks hazards,
     - computes evacuation routes,
     - drives LEDs (WS2812B strip on Pi),
     - and syncs alerts/telemetry upstream to the cloud.
   - The backend broadcasts events to the frontend via **SSE** at `GET /events`.
3. **Demo mode** (no hardware)

   - You can run backend + frontend and use `POST /simulate` to demo fire spread + evacuation on a stored building graph.

---

## Ports (defaults)

- Frontend (Vite dev server): `http://localhost:5173`
- Backend (Express): `http://localhost:3000`
- Raspberry Pi edge API (Flask): `http://<pi-ip>:5000`
- MQTT broker (Mosquitto on Pi): `tcp://<pi-ip>:1883`

---

## Quick start (Windows): run dashboard + cloud backend

### Prerequisites

- Node.js 18+ (recommended) and npm
- A Firebase project with Firestore enabled

### 1) Backend: configure environment

Copy the template and edit it:

```bash
cd backend
cp .env.example .env
```

Key variables (see `backend/.env.example` for the full list):

```dotenv
# Server
PORT=3000

# Optional: protects write routes (POST/PUT/DELETE). If empty, auth is disabled.
API_KEY=

# Firebase Admin (required for building graph + telemetry persistence)
FIREBASE_PROJECT_ID=your-project-id
FIREBASE_CLIENT_EMAIL=your-service-account@your-project-id.iam.gserviceaccount.com

# IMPORTANT: keep the \n escaping (Firebase private keys are multi-line)
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"

# Optional (used when deploying floorplan images from the editor)
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=

# Optional (Gemini). If missing, backend uses a fallback text analysis.
GEMINI_API_KEY=

# Optional: if set, backend will POST the latest graph to the Pi after /building/setup
PI_BASE_URL=http://192.168.1.50:5000

# Optional CORS allowlist for production
FRONTEND_URL=http://localhost:5173
```

Notes:

- The backend expects Firebase credentials via env vars (see `backend/config/firebase.js`).
- If `API_KEY` is empty, the backend runs in dev mode with auth disabled.

### 2) Backend: install & run

From the repo root:

```bash
cd backend
npm install
npm run dev
```

Health check:

- `GET http://localhost:3000/health`

### 3) Frontend: configure environment

Copy the template and edit it:

```bash
cd frontend
cp .env.example .env
```

Key variables (see `frontend/.env.example`):

```dotenv
VITE_API_URL=http://localhost:3000

# Must match backend API_KEY if you enabled it
VITE_API_KEY=
```

### 4) Frontend: install & run

```bash
cd frontend
npm install
npm run dev
```

Open the UI:

- `http://localhost:5173`

---

## Using the dashboard

- `GET /` shows the building list.
- Create a new building, then go to the editor at `/editor`.
- In the editor you can:
  - draw nodes and edges,
  - mark sensor nodes,
  - optionally upload a floorplan image per floor,
  - and deploy the graph to Firestore.

Real-time events:

- The frontend connects to `GET http://localhost:3000/events` via Server-Sent Events.

---

## Cloud backend API (Express)

Base URL: `http://localhost:3000`

### Health + realtime

- `GET /health` — service status
- `GET /events` — SSE stream (dashboard subscribes here)

### Buildings (Firestore)

- `GET /buildings` — list building meta docs
- `POST /building/setup` — replace building graph (nodes/edges/sensors/images)
- `GET /building/:buildingId` — get full graph (nodes/edges/images/sensors)
- `DELETE /building/:buildingId` — delete a building

### Fire alerts (from Pi)

- `POST /fire-alert` — ingest fire alert, compute hazard weights + hints
- `POST /fire-alert/clear` — all-clear
- `GET /fire-alert/state/:buildingId` — current fire/evacuation state

### Telemetry (from Pi)

- `POST /telemetry` — ingest sensor telemetry; may return anomaly feedback
- `GET /telemetry/node/:buildingId/:nodeId` — latest snapshot for node
- `GET /telemetry/building/:buildingId` — snapshots for all nodes

### Demo simulation (no hardware)

- `POST /simulate` — run a fire spread + evacuation simulation on the stored graph
  - Body: `{ "buildingId": "BUILDING_01", "startNode": "ROOM_101", "tickSeconds"?: 5, "maxTicks"?: 20 }`

### Pi “expected” endpoints (stubs)

These exist to match what the Pi can call in some flows:

- `POST /led/batch` — receives LED commands from Pi and re-broadcasts as SSE
- `POST /devices/register` — registers devices (cloud-side)

---

## Raspberry Pi edge controller (Python)

Location: `smart-fire-evacuation/`

What it does:

- Runs a Flask API (health/status/device registration/event log)
- Listens to MQTT telemetry from ESP32 nodes (Mosquitto)
- Computes evacuation paths locally and drives LED zones
- Syncs selected events to the cloud backend (fire alerts + telemetry)
- Supports HA primary/secondary role switching

ESP32 reference:

- `smart-fire-evacuation/esp32_reference/` contains a reference firmware implementation and documentation for ESP32 nodes (sensors/LEDs) used in the prototype.

### Run on Raspberry Pi (recommended)

On a Pi, the project includes an idempotent setup script:

```bash
cd smart-fire-evacuation
sudo bash setup.sh
```

Then configure your environment:

```bash
cd smart-fire-evacuation
cp .env.example .env
```

Edit `.env` and set at least:

- `CLOUD_BASE_URL` to your backend URL (for local dev this is usually a tunnel like ngrok)
- `CLOUD_API_KEY` to match the backend `API_KEY` (if enabled)

Start:

```bash
sudo systemctl start fire-evacuation
```

View logs:

```bash
journalctl -u fire-evacuation -f
```

### Run on Windows (dev-only, limited)

The edge controller is designed for Raspberry Pi hardware (GPIO LED driving and Mosquitto), but you can still run the Flask API and core logic for development.

From PowerShell:

```powershell
cd smart-fire-evacuation
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

If `rpi_ws281x` fails to install on Windows, you have two options:

1. Run the edge controller on a Raspberry Pi (recommended), or
2. Remove/comment the `rpi_ws281x` line in `smart-fire-evacuation/requirements.txt` for Windows-only development.

---

## End-to-end demo (no hardware)

1. Start backend and frontend.
2. In the frontend, create a building and deploy a graph (include at least one node with type `exit`).
3. Trigger a simulation:

```bash
curl -X POST http://localhost:3000/simulate \
	-H "Content-Type: application/json" \
	-d "{\"buildingId\":\"BUILDING_01\",\"startNode\":\"ROOM_101\"}"
```

PowerShell equivalent:

```powershell
$body = @{ buildingId = "BUILDING_01"; startNode = "ROOM_101" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://localhost:3000/simulate" -ContentType "application/json" -Body $body

# Or if you prefer curl on Windows:
curl.exe -X POST "http://localhost:3000/simulate" -H "Content-Type: application/json" -d "{\"buildingId\":\"BUILDING_01\",\"startNode\":\"ROOM_101\"}"
```

If you set `API_KEY` in the backend, add one of:

- `-H "X-API-Key: <your key>"` (preferred)
- or `-H "Authorization: Bearer <your key>"`

---

## Tests

The Python edge controller has an automated test suite:

```bash
cd smart-fire-evacuation
pip install -r requirements.txt
pip install pytest
pytest -q
```
