/**
 * GridEditorPage.jsx — Page 3
 * Step 2 of 3: Interactive grid editor per floor.
 * Auto-detects nodes from uploaded image, admin can then edit.
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import useBuildingStore from '../store/buildingStore';
import StepIndicator from '../components/StepIndicator';
import GridCanvas from '../components/GridCanvas';
import NodeInfoPanel from '../components/NodeInfoPanel';
import { autoDetectGrid } from '../utils/gridUtils';
import './GridEditorPage.css';

export default function GridEditorPage() {
  const navigate = useNavigate();
  const { name, floors, floorData, setFloorGrid } = useBuildingStore();

  // All hooks must come before any conditional returns (Rules of Hooks)
  const [activeFloor,     setActiveFloor]     = useState(1);
  const [selectedNodeId,  setSelectedNodeId]  = useState(null);
  const [detecting,       setDetecting]       = useState(false);
  const [detectedFloors,  setDetectedFloors]  = useState(new Set());

  // Guard redirect — use effect to avoid navigate-during-render
  useEffect(() => {
    if (!name || !floors) navigate('/');
  }, [name, floors]);

  // Auto-detect grid when switching to a floor that hasn't been processed
  useEffect(() => {
    if (!name || !floors) return;
    const run = async () => {
      const fd = useBuildingStore.getState().floorData[activeFloor];
      if (!fd?.imageUrl) return;
      if (detectedFloors.has(activeFloor)) return;
      if (fd.nodes?.length > 0) { setDetectedFloors((p) => new Set(p).add(activeFloor)); return; }

      setDetecting(true);
      try {
        const { nodes, edges } = await autoDetectGrid(fd.imageUrl);
        // Tag each node with the correct floor number
        const taggedNodes = nodes.map((n) => ({ ...n, floor: activeFloor }));
        setFloorGrid(activeFloor, taggedNodes, edges);
        setDetectedFloors((p) => new Set(p).add(activeFloor));
      } catch (err) {
        console.error('Auto-detect failed:', err);
        setFloorGrid(activeFloor, [], []);
      } finally {
        setDetecting(false);
      }
    };
    run();
  }, [activeFloor, name, floors]);

  // Guard: if no building yet, render nothing (effect above will redirect)
  if (!name || !floors) return null;

  const floorTabs = Array.from({ length: floors }, (_, i) => i + 1);
  const current   = floorData[activeFloor] || { nodes: [], edges: [], imageUrl: null };

  // Node mutations
  const handleNodesChange = useCallback((nodes) => {
    setFloorGrid(activeFloor, nodes, current.edges);
    // If selected node was removed, clear selection
    if (selectedNodeId && !nodes.find((n) => n.id === selectedNodeId)) {
      setSelectedNodeId(null);
    }
  }, [activeFloor, current.edges, selectedNodeId]);

  const handleEdgesChange = useCallback((edges) => {
    setFloorGrid(activeFloor, current.nodes, edges);
  }, [activeFloor, current.nodes]);

  const handleChangeType = (nodeId, type) => {
    const nodes = current.nodes.map((n) => n.id === nodeId ? { ...n, type } : n);
    setFloorGrid(activeFloor, nodes, current.edges);
  };

  const handleChangeLabel = (nodeId, label) => {
    const nodes = current.nodes.map((n) => n.id === nodeId ? { ...n, label } : n);
    setFloorGrid(activeFloor, nodes, current.edges);
  };

  const handleChangeEdgeDist = (edge, distance) => {
    const edges = current.edges.map((e) => {
      const match = (e.from === edge.from && e.to === edge.to) || (e.from === edge.to && e.to === edge.from);
      return match ? { ...e, distance } : e;
    });
    setFloorGrid(activeFloor, current.nodes, edges);
  };

  const selectedNode = current.nodes.find((n) => n.id === selectedNodeId) || null;

  const allFloorsHaveNodes = floorTabs.every((f) => floorData[f]?.nodes?.length > 0);
  const isLastFloor = activeFloor === floors;
  const floorsWithNodes = floorTabs.filter((f) => floorData[f]?.nodes?.length > 0).length;

  return (
    <div className="editor-layout">
      {/* Header */}
      <div className="editor-header">
        <div>
          <p className="page-breadcrumb">
            New Building / <strong>{name}</strong> / Floor {activeFloor} Grid Editor
          </p>
          <h1 className="page-title">{name} — Floor {activeFloor} Grid</h1>
        </div>
        <StepIndicator current={2} />
      </div>

      {/* Floor Tabs */}
      <div className="floor-tabs">
        {floorTabs.map((f) => {
          const hasNodes = floorData[f]?.nodes?.length > 0;
          return (
            <button
              key={f}
              className={`floor-tab ${activeFloor === f ? 'floor-tab-active' : ''} ${hasNodes ? 'floor-tab-done' : ''}`}
              onClick={() => { setActiveFloor(f); setSelectedNodeId(null); }}
            >
              <span className="floor-tab-num">Floor {f}</span>
              <span className={`floor-tab-status ${hasNodes ? 'status-ok' : 'status-pending'}`}>
                {hasNodes ? '✓' : '⚠'}
              </span>
            </button>
          );
        })}
        <div className="floor-tabs-progress">
          <span className="mono">{floorsWithNodes}/{floors} floors</span>
        </div>
      </div>

      {/* Main Editor */}
      <div className="editor-body">
        {/* Left Sidebar — Legend */}
        <div className="editor-legend">
          <p className="legend-title">NODE TYPES</p>
          {[
            { type: 'room',     color: 'var(--node-room)',     label: 'Room' },
            { type: 'corridor', color: 'var(--node-corridor)', label: 'Corridor' },
            { type: 'stairs',   color: 'var(--node-stairs)',   label: 'Stairs' },
            { type: 'exit',     color: 'var(--node-exit)',     label: 'Exit Point' },
          ].map(({ type, color, label }) => (
            <div key={type} className="legend-item">
              <span className="legend-dot" style={{ background: color, boxShadow: `0 0 6px ${color}` }} />
              <span className="legend-label">{label}</span>
            </div>
          ))}

          <div className="legend-divider" />
          <p className="legend-title">STATS</p>
          <div className="editor-stat">
            <span className="editor-stat-label">Nodes</span>
            <span className="editor-stat-value mono">{current.nodes.length}</span>
          </div>
          <div className="editor-stat">
            <span className="editor-stat-label">Edges</span>
            <span className="editor-stat-value mono">{current.edges.length}</span>
          </div>
          <div className="editor-stat">
            <span className="editor-stat-label">Exits</span>
            <span className="editor-stat-value mono">{current.nodes.filter((n) => n.type === 'exit').length}</span>
          </div>

          <div className="legend-divider" />
          <p className="legend-actions-title">ACTIONS</p>
          <button
            className="btn btn-secondary btn-sm w-full"
            style={{ justifyContent: 'center' }}
            onClick={async () => {
              setDetecting(true);
              try {
                const fd = useBuildingStore.getState().floorData[activeFloor];
                if (!fd?.imageUrl) return;
                const { nodes, edges } = await autoDetectGrid(fd.imageUrl);
                setFloorGrid(activeFloor, nodes.map((n) => ({ ...n, floor: activeFloor })), edges);
                setSelectedNodeId(null);
              } finally { setDetecting(false); }
            }}
            disabled={detecting}
          >
            {detecting ? '⏳ Detecting…' : '🔄 Re-detect'}
          </button>
          <button
            className="btn btn-secondary btn-sm w-full"
            style={{ justifyContent: 'center', marginTop: 6 }}
            onClick={() => { setFloorGrid(activeFloor, [], []); setSelectedNodeId(null); }}
          >
            🗑️ Clear Floor
          </button>
        </div>

        {/* Grid Canvas */}
        <div className="editor-canvas-wrap">
          {detecting ? (
            <div className="detecting-overlay">
              <div className="detecting-spinner" />
              <p className="detecting-text">Analysing floor plan…</p>
              <p className="detecting-sub">Auto-detecting rooms, corridors, exits</p>
            </div>
          ) : (
            <GridCanvas
              nodes={current.nodes}
              edges={current.edges}
              imageUrl={current.imageUrl}
              onNodesChange={handleNodesChange}
              onEdgesChange={handleEdgesChange}
              selectedNodeId={selectedNodeId}
              onSelectNode={setSelectedNodeId}
            />
          )}
        </div>

        {/* Right Sidebar — Node Info */}
        <NodeInfoPanel
          node={selectedNode}
          nodes={current.nodes}
          edges={current.edges}
          onChangeType={handleChangeType}
          onChangeLabel={handleChangeLabel}
          onChangeEdgeDist={handleChangeEdgeDist}
        />
      </div>

      {/* Bottom Bar */}
      <div className="page-bottom-bar">
        <button className="btn btn-secondary" onClick={() => navigate('/add-building/upload')}>
          ← Back
        </button>
        <div className="editor-bottom-center">
          {!isLastFloor && (
            <button
              className="btn btn-secondary"
              onClick={() => { setActiveFloor((f) => Math.min(f + 1, floors)); setSelectedNodeId(null); }}
            >
              Next Floor →
            </button>
          )}
        </div>
        <button
          className="btn btn-primary"
          disabled={!allFloorsHaveNodes}
          onClick={() => navigate('/add-building/review')}
          title={!allFloorsHaveNodes ? 'All floors must have nodes' : ''}
        >
          ✓ Done — Review
        </button>
      </div>
    </div>
  );
}
