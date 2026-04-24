# Rapid Crisis — Frontend UI/UX Plan

## Overview

**Project**: Rapid Crisis (Emergency Evacuation System)  
**Role**: Admin-facing frontend for building/floor map management & fire evacuation visualization  
**Stack**: React + Tailwind CSS (or styled-components)  
**Aesthetic Direction**: Industrial/Utilitarian — dark theme, monospace accents, amber/red alert palette. Feels like mission-critical software. Clean, high-contrast, trustworthy.

---

## App Architecture (Pages & Flow)

```
Home (Page 1)
├── [All Buildings] tile → Building List Page
│   └── [Building Name] → Floor Map Viewer/Editor (Page 4)
├── [Add a New Building] tile → Popup: Enter name + no. of floors → Page 2
├── [Devices] tile → (future scope)
└── [Alerts] tile → (future scope)

Add Building Flow:
Page 2 → Upload floor architecture images (one per floor)
Page 3 → Grid diagram editor per floor (node detection, wall editing)
Page 4 → Review all floor grids → Submit to backend

Fire Mode (triggered by backend push):
Page 4 / Map View → Safe path highlighted live
```

---

## Page 1 — Home Dashboard

### Layout
- Top bar: `RAPID CRISIS` logo + system status indicator (green dot = normal, red = alert)
- 2×2 tile grid in center:
  - **Devices** — icon + label
  - **Add a New Building** — icon + label (primary CTA)
  - **All Buildings** — icon + label
  - **Alerts** — icon + label + badge (unread count)

### Interactions
- Hover: tile lifts with subtle shadow + border accent
- `Add a New Building` → triggers Modal Popup (see below)
- `All Buildings` → navigates to Building List page
- `Alerts` → shows alert feed (future scope)

### Add New Building Modal (Popup)
- Fields:
  - Building Name (text input)
  - Number of Floors (number input, min 1)
- Buttons: `Cancel` | `Create`
- On `Create` → navigate to **Page 2** with building context

---

## Page 2 — Floor Architecture Upload

### Layout
- Header: `New Building: [Building Name]` + step indicator (Step 1 of 3)
- Horizontal tab row: `Floor 1 | Floor 2 | Floor 3 | ...` (dynamic, based on floor count)
- Active floor panel:
  - Large dashed upload zone: "Drop architecture image here or click to browse"
  - Preview thumbnail once uploaded
  - Status: ✓ Uploaded / ⚠ Pending
- Bottom bar: `Back` | `Next →` (Next enabled only when all floors have images)

### Notes
- Each floor tab independently holds its image
- Image stored in local state, passed to Page 3 for processing

---

## Page 3 — Grid Diagram Editor (per floor)

### Layout
- Header: `[Building Name] — Floor [N] Grid Editor` + step indicator (Step 2 of 3)
- Left sidebar: node type legend
  - 🟦 Room
  - 🟩 Corridor
  - 🟨 Stairs
  - 🟥 Exit Point
- Center: **Interactive Grid Canvas**
  - Background: uploaded floor architecture image (semi-transparent overlay)
  - Grid lines over it
  - Auto-detected nodes shown as colored tiles
  - Each wall (grid line segment) has a circular badge with wall length in metres
- Right sidebar (contextual): selected node info panel
  - Node type, ID, coordinates
  - Edit length of adjacent walls

### Grid Canvas Interactions
- **Click a node** → selects it (turns blue highlight), shows info in right panel
- **Click a wall length badge** → inline edit → confirm → updates edge distance
- **Drag a node** (optional enhancement) → reposition
- Node types auto-colored:
  - Room → blue
  - Corridor → green
  - Stairs → yellow
  - Exit → red
- Selected node → bright blue border + glow

### Bottom Bar
- `← Back` | `Next Floor →` (cycle through floors) | `✓ Done (All Floors)`
- Progress: `Floor 2 / 4 complete` pill indicator

### Data collected per floor
```json
{
  "floor": 1,
  "nodes": [...],
  "edges": [{ "from": "NODE_A", "to": "NODE_B", "distance": 5 }]
}
```

---

## Page 4 — Review & Submit / Live Map View

### Layout
- Header: `[Building Name]` + Back button
- Horizontal tabs: `Floor 1 Grid | Floor 2 Grid | Floor 3 Grid | ...`
- Active floor tab shows the finalized grid (read-only or editable)
  - Click on floor tab → opens that floor's grid editor again (edit mode)
- Bottom bar: `← Back` | `✅ Submit to Backend`

### Submit Behavior
- Collects all floor data, assembles full payload:
```json
{
  "nodes": [...all floors combined...],
  "edges": [...],
  "sensors": [...],
  "start": "ROOM_X"
}
```
- POST to backend API endpoint
- On success → toast notification "Building saved successfully"

### Fire Alert Mode (backend push → frontend display)
When backend sends:
```json
{
  "start": "ROOM_1",
  "blocked": ["CORRIDOR_1"],
  "safePath": ["ROOM_1", "CORRIDOR_2", "EXIT_1"],
  "status": "OK"
}
```

Grid canvas updates:
- 🔴 Blocked nodes → red pulsing highlight
- 🟢 Safe path nodes → green sequential glow (animated, showing direction)
- 🔥 Start node → fire icon overlay
- Banner at top: `⚠ FIRE DETECTED — Safe path highlighted`
- Optionally: animated arrow/path line connecting safe nodes in order

---

## Data Flow Summary

```
[Admin uploads images]
        ↓
[Page 3: Grid editor — nodes & walls defined]
        ↓
[Page 4: Review → Submit]
        ↓
[POST /api/building → Backend stores graph]
        ↓
[Fire sensor triggers → Backend runs pathfinding]
        ↓
[WebSocket/SSE push → Frontend receives safePath]
        ↓
[Grid highlights safe path in real-time]
```

---

## State Management

Recommended: React Context or Zustand

```
buildingStore {
  name: string
  floors: number
  floorData: {
    [floorNum]: {
      image: File | null
      nodes: Node[]
      edges: Edge[]
    }
  }
}
```

---

## Component Breakdown

| Component | Description |
|---|---|
| `<HomePage />` | 2×2 tile dashboard |
| `<AddBuildingModal />` | Popup form |
| `<BuildingList />` | All buildings listing |
| `<FloorUploader />` | Per-floor image upload tabs |
| `<GridEditor />` | Core interactive grid canvas |
| `<NodeTile />` | Individual node on grid |
| `<WallBadge />` | Circular length label on wall |
| `<FloorReview />` | Read-only grid tabs + submit |
| `<SafePathOverlay />` | Fire mode path highlight layer |
| `<AlertBanner />` | Top banner for fire alert state |

---

## API Contracts

### POST /api/buildings
```json
Request:
{
  "buildingName": "Block A",
  "nodes": [...],
  "edges": [...],
  "sensors": [...],
  "start": "ROOM_1"
}

Response: { "status": "created", "buildingId": "abc123" }
```

### GET /api/buildings
```json
Response: [{ "id": "abc123", "name": "Block A", "floors": 3 }]
```

### WebSocket / SSE: fire alert
```json
Incoming: {
  "start": "ROOM_1",
  "blocked": ["CORRIDOR_1"],
  "safePath": ["ROOM_1", "CORRIDOR_2", "EXIT_1"],
  "status": "OK"
}
```

---

## Visual Design Tokens

```css
--bg-primary: #0d0f14;
--bg-card: #161b22;
--border: #2a2f3a;
--accent-amber: #f59e0b;
--accent-red: #ef4444;
--accent-green: #22c55e;
--accent-blue: #3b82f6;
--text-primary: #f1f5f9;
--text-muted: #64748b;
--font-display: 'Rajdhani', sans-serif;  /* sharp, military-ish */
--font-mono: 'JetBrains Mono', monospace; /* for IDs and measurements */
```

---

## Implementation Order

1. **Page 1** — Home dashboard with tiles + Add Building modal
2. **Page 2** — Floor image upload with tabs
3. **Page 3** — Grid editor (core complexity — start simple, enhance iteratively)
4. **Page 4** — Review + submit + building list view
5. **Fire mode** — Safe path overlay animation (WebSocket/SSE integration)

---

## Notes from Sketches

- Page 3 grid: walls shown as lines, nodes as boxes; wall length in circular badges
- "Nodes" = rooms, corridors, stairs, gates, exit points
- After Page 3 → click Next → updates same for all floors → final Create/Submit
- Page 4 building list: clicking a building opens its floor grids for editing (back button returns to list)