/**
 * HomePage.jsx — Page 1
 * 2×2 dashboard tile grid + Add Building modal.
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import useBuildingStore from '../store/buildingStore';
import './HomePage.css';

const TILES = [
  {
    id: 'devices',
    icon: '📡',
    label: 'Devices',
    sub: 'Sensor management',
    color: 'tile-blue',
    badge: null,
    action: 'future',
  },
  {
    id: 'add-building',
    icon: '🏗️',
    label: 'Add New Building',
    sub: 'Create floor map',
    color: 'tile-amber',
    badge: null,
    action: 'add',
  },
  {
    id: 'all-buildings',
    icon: '🏢',
    label: 'All Buildings',
    sub: 'View & manage',
    color: 'tile-green',
    badge: null,
    action: 'buildings',
  },
  {
    id: 'alerts',
    icon: '🚨',
    label: 'Alerts',
    sub: 'Fire & evacuation events',
    color: 'tile-red',
    badge: 0,
    action: 'future',
  },
];

export default function HomePage() {
  const navigate = useNavigate();
  const setBuilding = useBuildingStore((s) => s.setBuilding);
  const [showModal, setShowModal] = useState(false);
  const [buildingName, setBuildingName] = useState('');
  const [floorCount, setFloorCount] = useState(1);
  const [error, setError] = useState('');

  const handleTileClick = (action) => {
    if (action === 'add')       setShowModal(true);
    if (action === 'buildings') navigate('/buildings');
  };

  const handleCreate = () => {
    if (!buildingName.trim()) { setError('Building name is required'); return; }
    if (floorCount < 1 || floorCount > 20) { setError('Floors must be between 1 and 20'); return; }
    setBuilding(buildingName.trim(), Number(floorCount));
    setShowModal(false);
    setBuildingName('');
    setFloorCount(1);
    setError('');
    navigate('/add-building/upload');
  };

  const handleCancel = () => {
    setShowModal(false);
    setBuildingName('');
    setFloorCount(1);
    setError('');
  };

  return (
    <div className="home-page">
      <div className="home-hero">
        <div className="home-hero-badge mono">ADMIN PANEL v3.0</div>
        <h1 className="home-title">Smart Fire Evacuation<br />Control Centre</h1>
        <p className="home-sub">Manage buildings, configure evacuation routes, and monitor real-time sensor data.</p>
      </div>

      <div className="tile-grid">
        {TILES.map((tile) => (
          <button
            key={tile.id}
            id={`tile-${tile.id}`}
            className={`dashboard-tile ${tile.color} ${tile.action === 'future' ? 'tile-future' : ''}`}
            onClick={() => handleTileClick(tile.action)}
            disabled={tile.action === 'future'}
            aria-label={tile.label}
          >
            <div className="tile-icon-wrap">
              <span className="tile-icon">{tile.icon}</span>
            </div>
            <div className="tile-body">
              <span className="tile-label">{tile.label}</span>
              <span className="tile-sub">{tile.sub}</span>
            </div>
            {tile.badge !== null && tile.badge > 0 && (
              <span className="tile-badge badge badge-red">{tile.badge}</span>
            )}
            {tile.action === 'future' && (
              <span className="tile-future-tag">SOON</span>
            )}
            <div className="tile-arrow">→</div>
          </button>
        ))}
      </div>

      <div className="home-footer">
        <p className="mono home-footer-text">RAPID CRISIS SYSTEM · SMART EVACUATION · v3.0.0</p>
      </div>

      {/* Add Building Modal */}
      {showModal && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="modal-title" onClick={handleCancel}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <span className="modal-icon">🏗️</span>
              <div>
                <h2 id="modal-title" className="modal-title">New Building</h2>
                <p className="modal-sub">Configure building details to get started</p>
              </div>
            </div>

            <div className="modal-body">
              <div className="form-group">
                <label className="form-label" htmlFor="building-name">Building Name</label>
                <input
                  id="building-name"
                  className="form-input"
                  type="text"
                  placeholder="e.g. Block A, Engineering Block"
                  value={buildingName}
                  onChange={(e) => { setBuildingName(e.target.value); setError(''); }}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor="floor-count">Number of Floors</label>
                <input
                  id="floor-count"
                  className="form-input"
                  type="number"
                  min={1}
                  max={20}
                  value={floorCount}
                  onChange={(e) => { setFloorCount(e.target.value); setError(''); }}
                />
              </div>
              {error && <p className="modal-error">{error}</p>}
            </div>

            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={handleCancel}>Cancel</button>
              <button className="btn btn-primary" id="modal-create-btn" onClick={handleCreate}>
                Create Building →
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
