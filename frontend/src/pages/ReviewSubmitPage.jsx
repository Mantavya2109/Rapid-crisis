/**
 * ReviewSubmitPage.jsx — Page 4
 * Step 3 of 3: Review all floor grids and submit to backend.
 * Also handles live fire alert display via SSE.
 */
import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import toast, { Toaster } from 'react-hot-toast';
import useBuildingStore from '../store/buildingStore';
import StepIndicator from '../components/StepIndicator';
import GridCanvas from '../components/GridCanvas';
import AlertBanner from '../components/AlertBanner';
import { buildingApi, fireApi, createSSEConnection } from '../services/api';
import './ReviewSubmitPage.css';

export default function ReviewSubmitPage() {
  const navigate  = useNavigate();
  const { name, floors, floorData, reset } = useBuildingStore();

  const [activeFloor,  setActiveFloor]  = useState(1);
  const [submitting,   setSubmitting]   = useState(false);
  const [submitted,    setSubmitted]    = useState(false);
  const [buildingId,   setBuildingId]   = useState(null);
  const [fireAlert,    setFireAlert]    = useState(null);
  const sseRef = useRef(null);

  // Guard redirect via effect (avoids navigate-during-render)
  useEffect(() => {
    if (!name || !floors) navigate('/');
  }, [name, floors]);

  // ── SSE connection for fire alerts ──────────────────────────
  useEffect(() => {
    if (!submitted || !buildingId) return;
    sseRef.current = createSSEConnection((evt, data) => {
      if (evt === 'fire:detected' && data.buildingId === buildingId) {
        setFireAlert(data);
      }
      if (evt === 'fire:cleared' && data.buildingId === buildingId) {
        setFireAlert(null);
        toast.success('🟢 Fire alert cleared — system nominal');
      }
    });
    return () => sseRef.current?.close();
  }, [submitted, buildingId]);

  // Guard: render nothing while redirecting
  if (!name || !floors) return null;

  const floorTabs = Array.from({ length: floors }, (_, i) => i + 1);
  const current   = floorData[activeFloor] || { nodes: [], edges: [], imageUrl: null };

  // ── Build and submit payload ─────────────────────────────────
  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      // Collect all nodes and edges across floors
      const allNodes = [];
      const allEdges = [];
      for (let f = 1; f <= floors; f++) {
        const fd = floorData[f] || {};
        allNodes.push(...(fd.nodes || []));
        allEdges.push(...(fd.edges || []));
      }

      const sensors = allNodes
        .filter((n) => n.type === 'room' || n.type === 'corridor')
        .map((n) => n.id);

      const bid = name.trim().replace(/\s+/g, '_').toUpperCase();
      const payload = {
        buildingId: bid,
        nodes:      allNodes,
        edges:      allEdges,
        sensors,
      };

      await buildingApi.setup(payload);
      setBuildingId(bid);
      setSubmitted(true);

      // Persist to localStorage so BuildingListPage can display it
      const existing = JSON.parse(localStorage.getItem('rc_buildings') || '[]');
      const updated  = existing.filter((b) => b.buildingId !== bid);
      updated.push({ buildingId: bid, name, floors, nodeCount: allNodes.length, edgeCount: allEdges.length });
      localStorage.setItem('rc_buildings', JSON.stringify(updated));

      toast.success(`✅ Building "${name}" saved successfully!`, { duration: 5000 });
    } catch (err) {
      toast.error(`❌ Failed to save: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleClearAlert = async () => {
    try {
      await fireApi.clear(buildingId);
      setFireAlert(null);
    } catch { /* ignore */ }
  };

  const totalNodes = floorTabs.reduce((s, f) => s + (floorData[f]?.nodes?.length || 0), 0);
  const totalEdges = floorTabs.reduce((s, f) => s + (floorData[f]?.edges?.length || 0), 0);

  return (
    <>
      <Toaster
        position="top-right"
        toastOptions={{
          style: { background: '#1c2130', color: '#f1f5f9', border: '1px solid #2a2f3a', fontFamily: 'Inter, sans-serif' },
        }}
      />

      {fireAlert && <AlertBanner alertData={fireAlert} onClear={handleClearAlert} />}

      <div className="review-layout">
        {/* Header */}
        <div className="page-header">
          <div>
            <p className="page-breadcrumb">New Building / <strong>{name}</strong> / Review</p>
            <h1 className="page-title">Review & Submit</h1>
          </div>
          <StepIndicator current={3} />
        </div>

        {/* Summary Cards */}
        <div className="review-summary">
          <div className="summary-card">
            <span className="summary-icon">🏢</span>
            <div>
              <p className="summary-value mono">{name}</p>
              <p className="summary-label">Building Name</p>
            </div>
          </div>
          <div className="summary-card">
            <span className="summary-icon">🏠</span>
            <div>
              <p className="summary-value mono">{floors}</p>
              <p className="summary-label">Total Floors</p>
            </div>
          </div>
          <div className="summary-card">
            <span className="summary-icon">📍</span>
            <div>
              <p className="summary-value mono">{totalNodes}</p>
              <p className="summary-label">Total Nodes</p>
            </div>
          </div>
          <div className="summary-card">
            <span className="summary-icon">🔗</span>
            <div>
              <p className="summary-value mono">{totalEdges}</p>
              <p className="summary-label">Total Edges</p>
            </div>
          </div>
        </div>

        {/* Floor Tabs */}
        <div className="floor-tabs">
          {floorTabs.map((f) => (
            <button
              key={f}
              className={`floor-tab ${activeFloor === f ? 'floor-tab-active' : ''} floor-tab-done`}
              onClick={() => setActiveFloor(f)}
            >
              <span className="floor-tab-num">Floor {f}</span>
              <span className="floor-tab-status status-ok">
                ✓ {floorData[f]?.nodes?.length || 0} nodes
              </span>
            </button>
          ))}
          {!submitted && (
            <button
              className="floor-tab"
              onClick={() => navigate('/add-building/grid')}
              style={{ marginLeft: 'auto', color: 'var(--accent-amber)' }}
            >
              ✏️ Edit Grids
            </button>
          )}
        </div>

        {/* Grid Preview */}
        <div className="review-canvas-wrap">
          {fireAlert && (
            <div className="review-fire-mode-badge">
              <span>🔥 FIRE MODE ACTIVE</span>
            </div>
          )}
          <GridCanvas
            nodes={current.nodes}
            edges={current.edges}
            imageUrl={current.imageUrl}
            readOnly={true}
            fireAlert={
              fireAlert
                ? {
                    startNodes:   fireAlert.startNodes   || [],
                    blockedNodes: fireAlert.blockedNodes  || [],
                    safePath:     fireAlert.safePath      || [],
                  }
                : null
            }
          />
        </div>

        {/* Submit / Success */}
        <div className="page-bottom-bar">
          <button className="btn btn-secondary" onClick={() => navigate('/add-building/grid')}>
            ← Back to Editor
          </button>

          {submitted ? (
            <div className="submit-success">
              <span className="submit-success-icon">✅</span>
              <span>Building saved! Now listening for fire alerts via SSE…</span>
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => { reset(); navigate('/buildings'); }}
              >
                View All Buildings
              </button>
            </div>
          ) : (
            <button
              className="btn btn-primary btn-lg"
              onClick={handleSubmit}
              disabled={submitting}
              id="submit-building-btn"
            >
              {submitting
                ? <><span className="btn-spinner" /> Saving…</>
                : '✅ Submit to Backend'}
            </button>
          )}
        </div>
      </div>
    </>
  );
}
