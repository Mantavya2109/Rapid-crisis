/**
 * BuildingViewPage.jsx
 * Shows an existing building's floor grid (read-only), with live fire alert overlay.
 * Route: /building/:buildingId
 */
import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import toast, { Toaster } from 'react-hot-toast';
import GridCanvas from '../components/GridCanvas';
import AlertBanner from '../components/AlertBanner';
import { buildingApi, fireApi, createSSEConnection } from '../services/api';
import './BuildingViewPage.css';

export default function BuildingViewPage() {
  const { buildingId } = useParams();
  const navigate = useNavigate();

  const [building,    setBuilding]    = useState(null);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState(null);
  const [activeFloor, setActiveFloor] = useState(1);
  const [fireAlert,   setFireAlert]   = useState(null);
  const sseRef = useRef(null);

  // Load building data
  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true);
        const data = await buildingApi.get(buildingId);
        // Organise nodes by floor
        const floorMap = {};
        for (const node of data.nodes || []) {
          const f = node.floor || 1;
          if (!floorMap[f]) floorMap[f] = { nodes: [], edges: [] };
          floorMap[f].nodes.push(node);
        }
        for (const edge of data.edges || []) {
          // Assign edge to the floor of its 'from' node
          const fromNode = (data.nodes || []).find((n) => n.id === edge.from);
          const f = fromNode?.floor || 1;
          if (!floorMap[f]) floorMap[f] = { nodes: [], edges: [] };
          floorMap[f].edges.push(edge);
        }
        const floors = Math.max(...Object.keys(floorMap).map(Number), 1);
        setBuilding({ buildingId: data.buildingId, floors, floorMap });
        setActiveFloor(1);
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [buildingId]);

  // SSE fire alerts
  useEffect(() => {
    sseRef.current = createSSEConnection((evt, data) => {
      if (evt === 'fire:detected' && data.buildingId === buildingId) setFireAlert(data);
      if (evt === 'fire:cleared'  && data.buildingId === buildingId) {
        setFireAlert(null);
        toast.success('🟢 Fire alert cleared');
      }
    });
    return () => sseRef.current?.close();
  }, [buildingId]);

  const handleClearAlert = async () => {
    try { await fireApi.clear(buildingId); setFireAlert(null); } catch { /* ignore */ }
  };

  const floorTabs  = building ? Array.from({ length: building.floors }, (_, i) => i + 1) : [];
  const currentFD  = building?.floorMap[activeFloor] || { nodes: [], edges: [] };

  return (
    <>
      <Toaster
        position="top-right"
        toastOptions={{
          style: { background: '#1c2130', color: '#f1f5f9', border: '1px solid #2a2f3a' },
        }}
      />
      {fireAlert && <AlertBanner alertData={fireAlert} onClear={handleClearAlert} />}

      <div className="bview-layout">
        {/* Header */}
        <div className="bview-header">
          <div className="bview-header-left">
            <button className="btn btn-ghost btn-sm" onClick={() => navigate('/buildings')}>
              ← All Buildings
            </button>
            <div>
              <p className="bview-building-id mono">{buildingId}</p>
              <h1 className="page-title">{building?.buildingId || buildingId}</h1>
            </div>
          </div>
          <div className="bview-header-right">
            {fireAlert ? (
              <span className="badge badge-red" style={{ padding: '6px 14px', fontSize: '0.8rem' }}>
                🔥 FIRE ACTIVE
              </span>
            ) : (
              <span className="badge badge-green" style={{ padding: '6px 14px', fontSize: '0.8rem' }}>
                ✓ NOMINAL
              </span>
            )}
          </div>
        </div>

        {loading ? (
          <div className="bview-loading">
            <div className="detecting-spinner" />
            <p>Loading building data…</p>
          </div>
        ) : error ? (
          <div className="bview-error">
            <p>⚠ {error}</p>
            <button className="btn btn-secondary btn-sm" onClick={() => navigate('/buildings')}>← Back</button>
          </div>
        ) : (
          <>
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
                    {currentFD.nodes?.length || 0} nodes
                  </span>
                </button>
              ))}
            </div>

            {/* Stats Row */}
            <div className="bview-stats">
              <div className="bview-stat">
                <span className="bview-stat-value mono">{currentFD.nodes?.length || 0}</span>
                <span className="bview-stat-label">Nodes</span>
              </div>
              <div className="bview-stat">
                <span className="bview-stat-value mono">{currentFD.edges?.length || 0}</span>
                <span className="bview-stat-label">Edges</span>
              </div>
              <div className="bview-stat">
                <span className="bview-stat-value mono">
                  {currentFD.nodes?.filter((n) => n.type === 'exit').length || 0}
                </span>
                <span className="bview-stat-label">Exits</span>
              </div>
              <div className="bview-stat">
                <span className="bview-stat-value mono">
                  {currentFD.nodes?.filter((n) => n.type === 'room').length || 0}
                </span>
                <span className="bview-stat-label">Rooms</span>
              </div>
            </div>

            {/* Grid Canvas — read-only with fire overlay */}
            <div className="bview-canvas-wrap">
              {fireAlert && (
                <div className="review-fire-mode-badge">🔥 FIRE MODE ACTIVE</div>
              )}
              <GridCanvas
                nodes={currentFD.nodes || []}
                edges={currentFD.edges || []}
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
          </>
        )}
      </div>
    </>
  );
}
