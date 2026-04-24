/**
 * GridCanvas.jsx
 * The core interactive grid canvas for the floor plan editor.
 *
 * Props:
 *   nodes          - array of node objects
 *   edges          - array of edge objects { from, to, distance }
 *   imageUrl       - optional background floor plan image
 *   onNodesChange  - callback(nodes) when nodes are modified
 *   onEdgesChange  - callback(edges) when edges are modified
 *   readOnly       - disable editing
 *   fireAlert      - { startNodes, blockedNodes, safePath } for fire mode overlay
 */
import { useState, useRef } from 'react';
import './GridCanvas.css';

const CELL_SIZE = 68;
const GRID_COLS = 14;
const GRID_ROWS = 10;

const TYPE_CONFIG = {
  room:     { color: 'var(--node-room)',     label: 'Room',     symbol: '🟦' },
  corridor: { color: 'var(--node-corridor)', label: 'Corridor', symbol: '🟩' },
  stairs:   { color: 'var(--node-stairs)',   label: 'Stairs',   symbol: '🟨' },
  exit:     { color: 'var(--node-exit)',     label: 'Exit',     symbol: '🟥' },
};

const NODE_TYPES = ['room', 'corridor', 'stairs', 'exit'];

export default function GridCanvas({
  nodes = [],
  edges = [],
  imageUrl,
  onNodesChange,
  onEdgesChange,
  readOnly = false,
  fireAlert = null,
  selectedNodeId,
  onSelectNode,
}) {
  const [editingEdge, setEditingEdge] = useState(null); // { edgeKey, value }
  const inputRef = useRef();

  // Build lookup maps
  const nodeMap = {};
  nodes.forEach((n) => { nodeMap[`${n.row}_${n.col}`] = n; });

  const edgeMap = {}; // edgeKey → edge
  edges.forEach((e) => {
    const key = [e.from, e.to].sort().join('--');
    edgeMap[key] = e;
  });

  // Fire mode sets
  const blockedSet = new Set(fireAlert?.blockedNodes || []);
  const safeSet    = new Set(fireAlert?.safePath || []);
  const startSet   = new Set(fireAlert?.startNodes || []);

  const getNodeState = (node) => {
    if (!fireAlert) return 'normal';
    if (startSet.has(node.id))   return 'fire-start';
    if (blockedSet.has(node.id)) return 'blocked';
    if (safeSet.has(node.id))    return 'safe';
    return 'normal';
  };

  // Handle cell click — cycle through node types or place new node
  const handleCellClick = (row, col) => {
    if (readOnly) return;
    const key = `${row}_${col}`;
    const existing = nodeMap[key];
    let newNodes;
    if (existing) {
      // Cycle type
      const types = NODE_TYPES;
      const nextType = types[(types.indexOf(existing.type) + 1) % types.length];
      newNodes = nodes.map((n) =>
        n.id === existing.id ? { ...n, type: nextType } : n
      );
    } else {
      // Place new node
      const idx  = nodes.length + 1;
      const type = 'room';
      const id   = `ROOM_${String(idx).padStart(3, '0')}`;
      const newNode = {
        id,
        type,
        label: `Room ${idx}`,
        row,
        col,
        floor: nodes[0]?.floor || 1,
      };
      newNodes = [...nodes, newNode];
      // Add edges to adjacent existing nodes
      const dirs = [[0,1],[1,0],[0,-1],[-1,0]];
      const newEdges = [...edges];
      const edgeSet  = new Set(edges.map((e) => [e.from, e.to].sort().join('--')));
      for (const [dr, dc] of dirs) {
        const neighbor = nodeMap[`${row + dr}_${col + dc}`];
        if (!neighbor) continue;
        const ekey = [id, neighbor.id].sort().join('--');
        if (!edgeSet.has(ekey)) {
          newEdges.push({ from: id, to: neighbor.id, distance: 5 });
          edgeSet.add(ekey);
        }
      }
      onEdgesChange?.(newEdges);
    }
    onNodesChange?.(newNodes);
  };

  const handleNodeRightClick = (e, node) => {
    e.preventDefault();
    if (readOnly) return;
    onNodesChange?.(nodes.filter((n) => n.id !== node.id));
    onEdgesChange?.(edges.filter((ed) => ed.from !== node.id && ed.to !== node.id));
    if (selectedNodeId === node.id) onSelectNode?.(null);
  };

  // Edge distance editing
  const startEdgeEdit = (e, edgeKey, currentDist) => {
    e.stopPropagation();
    if (readOnly) return;
    setEditingEdge({ edgeKey, value: String(currentDist) });
    setTimeout(() => inputRef.current?.focus(), 50);
  };

  const commitEdgeEdit = () => {
    if (!editingEdge) return;
    const dist = parseFloat(editingEdge.value);
    if (!isNaN(dist) && dist > 0) {
      const newEdges = edges.map((ed) => {
        const key = [ed.from, ed.to].sort().join('--');
        return key === editingEdge.edgeKey ? { ...ed, distance: dist } : ed;
      });
      onEdgesChange?.(newEdges);
    }
    setEditingEdge(null);
  };

  // Compute wall badge positions (midpoint between adjacent nodes)
  const wallBadges = [];
  const rendered = new Set();
  edges.forEach((edge) => {
    const edgeKey = [edge.from, edge.to].sort().join('--');
    if (rendered.has(edgeKey)) return;
    rendered.add(edgeKey);
    const fromNode = nodes.find((n) => n.id === edge.from);
    const toNode   = nodes.find((n) => n.id === edge.to);
    if (!fromNode || !toNode) return;
    const midRow = (fromNode.row + toNode.row) / 2;
    const midCol = (fromNode.col + toNode.col) / 2;
    // Only show badge for adjacent cells (distance 1)
    const dr = Math.abs(fromNode.row - toNode.row);
    const dc = Math.abs(fromNode.col - toNode.col);
    if (dr + dc === 1) {
      wallBadges.push({ edgeKey, edge, midRow, midCol });
    }
  });

  const safePathArr = fireAlert?.safePath || [];

  return (
    <div className="grid-canvas-container">
      {/* Background image */}
      {imageUrl && (
        <img
          src={imageUrl}
          className="grid-bg-image"
          alt="Floor plan"
          draggable={false}
        />
      )}

      {/* Grid cells */}
      <div
        className="grid-cells"
        style={{ gridTemplateColumns: `repeat(${GRID_COLS}, ${CELL_SIZE}px)` }}
      >
        {Array.from({ length: GRID_ROWS * GRID_COLS }, (_, i) => {
          const row = Math.floor(i / GRID_COLS);
          const col = i % GRID_COLS;
          const node = nodeMap[`${row}_${col}`];
          const isSelected = node && selectedNodeId === node.id;
          const fireState  = node ? getNodeState(node) : 'normal';
          const safeOrder  = node ? safePathArr.indexOf(node.id) : -1;

          return (
            <div
              key={i}
              className={`grid-cell ${node ? 'grid-cell-filled' : 'grid-cell-empty'} ${isSelected ? 'grid-cell-selected' : ''} ${node ? `node-type-${node.type}` : ''} ${node ? `fire-state-${fireState}` : ''}`}
              style={safeOrder >= 0 ? { animationDelay: `${safeOrder * 0.15}s` } : {}}
              onClick={() => {
                if (node) onSelectNode?.(node.id === selectedNodeId ? null : node.id);
                else handleCellClick(row, col);
              }}
              onContextMenu={(e) => node && handleNodeRightClick(e, node)}
              title={node ? `${node.id} — ${node.type} (right-click to remove)` : `Add node at (${row},${col})`}
            >
              {node && (
                <div className="node-content">
                  {fireState === 'fire-start' && <span className="fire-icon">🔥</span>}
                  <span className="node-id mono">{node.id.split('_').pop()}</span>
                  <span className="node-type-label">{node.type}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Wall badges */}
      {wallBadges.map(({ edgeKey, edge, midRow, midCol }) => {
        const isEditing = editingEdge?.edgeKey === edgeKey;
        return (
          <div
            key={edgeKey}
            className="wall-badge"
            style={{
              top:  midRow * CELL_SIZE + CELL_SIZE / 2,
              left: midCol * CELL_SIZE + CELL_SIZE / 2,
            }}
            onClick={(e) => startEdgeEdit(e, edgeKey, edge.distance)}
          >
            {isEditing ? (
              <input
                ref={inputRef}
                className="wall-badge-input"
                value={editingEdge.value}
                onChange={(e) => setEditingEdge({ ...editingEdge, value: e.target.value })}
                onBlur={commitEdgeEdit}
                onKeyDown={(e) => { if (e.key === 'Enter') commitEdgeEdit(); if (e.key === 'Escape') setEditingEdge(null); }}
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <span className="wall-badge-value">{edge.distance}m</span>
            )}
          </div>
        );
      })}

      {/* Safe path connecting lines overlay */}
      {safePathArr.length > 1 && (
        <svg className="safe-path-svg" style={{ width: GRID_COLS * CELL_SIZE, height: GRID_ROWS * CELL_SIZE }}>
          {safePathArr.slice(0, -1).map((fromId, i) => {
            const toId    = safePathArr[i + 1];
            const fromNode = nodes.find((n) => n.id === fromId);
            const toNode   = nodes.find((n) => n.id === toId);
            if (!fromNode || !toNode) return null;
            const x1 = fromNode.col * CELL_SIZE + CELL_SIZE / 2;
            const y1 = fromNode.row * CELL_SIZE + CELL_SIZE / 2;
            const x2 = toNode.col   * CELL_SIZE + CELL_SIZE / 2;
            const y2 = toNode.row   * CELL_SIZE + CELL_SIZE / 2;
            return (
              <line
                key={i}
                x1={x1} y1={y1} x2={x2} y2={y2}
                stroke="var(--accent-green)"
                strokeWidth="3"
                strokeDasharray="8 4"
                style={{ animation: `glow-path 1.5s ease ${i * 0.2}s infinite` }}
              />
            );
          })}
        </svg>
      )}
    </div>
  );
}
