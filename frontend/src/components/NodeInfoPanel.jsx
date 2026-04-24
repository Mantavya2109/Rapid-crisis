/**
 * NodeInfoPanel.jsx
 * Right sidebar for the grid editor — shows selected node info.
 */
import './NodeInfoPanel.css';

const TYPE_CONFIG = {
  room:     { color: '#3b82f6', label: 'Room' },
  corridor: { color: '#22c55e', label: 'Corridor' },
  stairs:   { color: '#eab308', label: 'Stairs' },
  exit:     { color: '#ef4444', label: 'Exit Point' },
};
const NODE_TYPES = ['room', 'corridor', 'stairs', 'exit'];

export default function NodeInfoPanel({ node, edges, nodes, onChangeType, onChangeLabel, onChangeEdgeDist }) {
  if (!node) {
    return (
      <div className="node-panel node-panel-empty">
        <div className="node-panel-empty-icon">🖱️</div>
        <p className="node-panel-empty-text">Click a node on the grid to inspect or edit it</p>
        <div className="node-legend">
          <p className="node-legend-title">Node Types</p>
          {NODE_TYPES.map((t) => (
            <div key={t} className="node-legend-item">
              <span className="node-legend-dot" style={{ background: TYPE_CONFIG[t].color }} />
              <span>{TYPE_CONFIG[t].label}</span>
            </div>
          ))}
        </div>
        <div className="node-panel-hint">
          <p>💡 <strong>Click</strong> empty cell to add</p>
          <p>💡 <strong>Click</strong> node to cycle type</p>
          <p>💡 <strong>Right-click</strong> to remove</p>
          <p>💡 <strong>Click badge</strong> to edit distance</p>
        </div>
      </div>
    );
  }

  const cfg = TYPE_CONFIG[node.type] || TYPE_CONFIG.room;
  const adjacentEdges = edges.filter((e) => e.from === node.id || e.to === node.id);

  return (
    <div className="node-panel">
      <div className="node-panel-header">
        <div className="node-panel-dot" style={{ background: cfg.color }} />
        <div>
          <p className="node-panel-id mono">{node.id}</p>
          <p className="node-panel-type">{cfg.label}</p>
        </div>
      </div>

      <div className="node-panel-section">
        <p className="node-panel-label">LABEL</p>
        <input
          className="form-input"
          value={node.label}
          onChange={(e) => onChangeLabel?.(node.id, e.target.value)}
          placeholder="Node label"
        />
      </div>

      <div className="node-panel-section">
        <p className="node-panel-label">TYPE</p>
        <div className="node-type-buttons">
          {NODE_TYPES.map((t) => (
            <button
              key={t}
              className={`node-type-btn ${node.type === t ? 'node-type-btn-active' : ''}`}
              style={node.type === t ? { borderColor: TYPE_CONFIG[t].color, color: TYPE_CONFIG[t].color, background: `${TYPE_CONFIG[t].color}22` } : {}}
              onClick={() => onChangeType?.(node.id, t)}
            >
              {TYPE_CONFIG[t].label}
            </button>
          ))}
        </div>
      </div>

      <div className="node-panel-section">
        <p className="node-panel-label">POSITION</p>
        <div className="node-coords mono">
          Row <strong>{node.row}</strong> · Col <strong>{node.col}</strong>
        </div>
      </div>

      {adjacentEdges.length > 0 && (
        <div className="node-panel-section">
          <p className="node-panel-label">CONNECTIONS ({adjacentEdges.length})</p>
          <div className="edge-list">
            {adjacentEdges.map((edge) => {
              const neighborId = edge.from === node.id ? edge.to : edge.from;
              const neighbor   = nodes.find((n) => n.id === neighborId);
              return (
                <div key={`${edge.from}--${edge.to}`} className="edge-item">
                  <span className="edge-neighbor mono">{neighborId}</span>
                  <div className="edge-dist-control">
                    <button className="edge-dist-btn" onClick={() => onChangeEdgeDist?.(edge, Math.max(1, edge.distance - 1))}>−</button>
                    <span className="edge-dist-value mono">{edge.distance}m</span>
                    <button className="edge-dist-btn" onClick={() => onChangeEdgeDist?.(edge, edge.distance + 1)}>+</button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
