/**
 * gridUtils.js
 * Utilities for auto-generating a grid layout from an uploaded floor image.
 * 
 * Strategy: We analyse the image on a canvas, sample a coarse grid of pixels,
 * and classify cells as:
 *   - dark/opaque = WALL (no node)
 *   - lighter/open = ROOM / CORRIDOR
 * Cells near edges are flagged as EXIT candidates.
 * Cells with intermediate brightness as CORRIDOR.
 * Then we infer STAIRS from isolated clusters far from exits.
 *
 * This is a heuristic — the admin can edit the result manually.
 */

const GRID_COLS = 14;
const GRID_ROWS = 10;

/**
 * Analyses an image File/Blob and returns a grid of nodes + edges.
 * @param {string} imageUrl - object URL of the uploaded image
 * @returns {Promise<{ nodes: Node[], edges: Edge[] }>}
 */
export async function autoDetectGrid(imageUrl) {
  const img = await loadImage(imageUrl);
  const canvas = document.createElement('canvas');
  canvas.width  = GRID_COLS;
  canvas.height = GRID_ROWS;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0, GRID_COLS, GRID_ROWS);
  const pixel = ctx.getImageData(0, 0, GRID_COLS, GRID_ROWS).data;

  const grid = []; // grid[row][col] = { type, brightness }
  for (let r = 0; r < GRID_ROWS; r++) {
    grid[r] = [];
    for (let c = 0; c < GRID_COLS; c++) {
      const idx = (r * GRID_COLS + c) * 4;
      const R = pixel[idx], G = pixel[idx + 1], B = pixel[idx + 2], A = pixel[idx + 3];
      // If alpha is very low treat as empty
      const brightness = A < 50 ? 255 : (R * 0.299 + G * 0.587 + B * 0.114);
      grid[r][c] = { brightness };
    }
  }

  // Classify cells
  const nodes = [];
  const nodeMap = {}; // `${r}_${c}` → nodeId
  let nodeIdx = 0;

  for (let r = 0; r < GRID_ROWS; r++) {
    for (let c = 0; c < GRID_COLS; c++) {
      const b = grid[r][c].brightness;
      // Dark cells = wall, skip
      if (b < 60) continue;

      const isEdge = r === 0 || r === GRID_ROWS - 1 || c === 0 || c === GRID_COLS - 1;
      let type;
      if (isEdge && b > 150) {
        type = 'exit';
      } else if (b > 180) {
        type = 'room';
      } else if (b > 110) {
        type = 'corridor';
      } else {
        type = 'stairs';
      }

      const id = `${type.toUpperCase()}_${String(++nodeIdx).padStart(3, '0')}`;
      nodes.push({
        id,
        type,
        label: `${type.charAt(0).toUpperCase() + type.slice(1)} ${nodeIdx}`,
        row: r,
        col: c,
        floor: 1, // caller will override
      });
      nodeMap[`${r}_${c}`] = id;
    }
  }

  // Build edges between adjacent nodes (4-directional)
  const edges = [];
  const edgeSet = new Set();
  const dirs = [[0,1],[1,0],[0,-1],[-1,0]];
  for (const node of nodes) {
    for (const [dr, dc] of dirs) {
      const key = `${node.row + dr}_${node.col + dc}`;
      const neighborId = nodeMap[key];
      if (!neighborId) continue;
      const edgeKey = [node.id, neighborId].sort().join('--');
      if (edgeSet.has(edgeKey)) continue;
      edgeSet.add(edgeKey);
      edges.push({ from: node.id, to: neighborId, distance: 5 });
    }
  }

  return { nodes, edges };
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload  = () => resolve(img);
    img.onerror = reject;
    img.src = url;
  });
}

export const GRID_W = GRID_COLS;
export const GRID_H = GRID_ROWS;
