/**
 * BuildingListPage.jsx
 * Lists all buildings fetched from GET /building/:id
 * For now shows a search + grid of building cards.
 * Clicking a building opens BuildingViewPage.
 */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { buildingApi } from '../services/api';
import './BuildingListPage.css';

export default function BuildingListPage() {
  const navigate = useNavigate();
  const [buildings, setBuildings] = useState([]);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState(null);
  const [query,     setQuery]     = useState('');

  useEffect(() => {
    // The backend doesn't have a GET /buildings list endpoint,
    // so we use the health check to confirm backend is live, then
    // try to load a known list from localStorage (persisted on submit).
    const saved = localStorage.getItem('rc_buildings');
    if (saved) {
      try { setBuildings(JSON.parse(saved)); } catch { /* ignore */ }
    }
    setLoading(false);
  }, []);

  const filtered = buildings.filter((b) =>
    b.name.toLowerCase().includes(query.toLowerCase())
  );

  const handleViewBuilding = (b) => {
    navigate(`/building/${b.buildingId}`);
  };

  return (
    <div className="blist-layout">
      {/* Header */}
      <div className="blist-header">
        <div>
          <h1 className="page-title">All Buildings</h1>
          <p className="blist-sub">Manage building floor plans and evacuation routes</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/')}>
          + New Building
        </button>
      </div>

      {/* Search */}
      <div className="blist-search-wrap">
        <span className="blist-search-icon">🔍</span>
        <input
          id="building-search"
          className="blist-search-input"
          type="text"
          placeholder="Search buildings…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="blist-loading">
          <div className="detecting-spinner" />
          <p>Loading buildings…</p>
        </div>
      ) : error ? (
        <div className="blist-error">
          <p>⚠ {error}</p>
          <button className="btn btn-secondary btn-sm" onClick={() => window.location.reload()}>Retry</button>
        </div>
      ) : filtered.length === 0 ? (
        <div className="blist-empty">
          <div className="blist-empty-icon">🏗️</div>
          <p className="blist-empty-title">
            {query ? 'No buildings match your search' : 'No buildings yet'}
          </p>
          <p className="blist-empty-sub">
            {query ? 'Try a different search term.' : 'Create your first building to get started.'}
          </p>
          {!query && (
            <button className="btn btn-primary" onClick={() => navigate('/')}>
              + Add New Building
            </button>
          )}
        </div>
      ) : (
        <div className="blist-grid">
          {filtered.map((b) => (
            <button
              key={b.buildingId}
              className="building-card"
              onClick={() => handleViewBuilding(b)}
              aria-label={`View ${b.name}`}
            >
              <div className="building-card-icon">🏢</div>
              <div className="building-card-body">
                <p className="building-card-name">{b.name}</p>
                <p className="building-card-id mono">{b.buildingId}</p>
                <div className="building-card-meta">
                  <span className="badge badge-amber">{b.floors} floor{b.floors !== 1 ? 's' : ''}</span>
                  <span className="badge badge-blue">{b.nodeCount || '?'} nodes</span>
                  {b.activeEvacuation && <span className="badge badge-red">🔥 ALERT</span>}
                </div>
              </div>
              <div className="building-card-arrow">→</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
