import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getBuildings, deleteBuilding } from "../services/api";

export default function Buildings() {
  const [buildings, setBuildings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [newId, setNewId] = useState("");
  const [newFloors, setNewFloors] = useState("2");
  const [createError, setCreateError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const navigate = useNavigate();

  const fetchBuildings = async () => {
    try {
      const data = await getBuildings();
      setBuildings(data || []);
      setError(null);
    } catch (err) {
      console.error(err);
      setError("Failed to connect to backend. Ensure the server is running.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchBuildings();
  }, []);

  const handleCreate = () => {
    const idTrim = newId.trim().toUpperCase().replace(/\s+/g, "_");
    if (!idTrim) { setCreateError("Building ID is required."); return; }
    if (!/^[A-Z0-9_-]+$/.test(idTrim)) { setCreateError("ID can only contain letters, numbers, _ and -."); return; }
    const floors = parseInt(newFloors, 10);
    if (!floors || floors < 1 || floors > 20) { setCreateError("Floors must be between 1 and 20."); return; }
    setShowCreateModal(false);
    setNewId("");
    setNewFloors("2");
    setCreateError("");
    navigate(`/editor?id=${idTrim}&floors=${floors}`);
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteBuilding(deleteTarget.buildingId);
      setBuildings(prev => prev.filter(b => b.buildingId !== deleteTarget.buildingId));
      setDeleteTarget(null);
    } catch (err) {
      alert("Delete failed: " + (err?.response?.data?.message || err.message));
    } finally {
      setDeleting(false);
    }
  };

  const filtered = buildings.filter(b =>
    b.buildingId?.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const totalNodes = buildings.reduce((s, b) => s + (b.nodeCount || 0), 0);
  const totalEdges = buildings.reduce((s, b) => s + (b.edgeCount || 0), 0);
  const withImages = buildings.filter(b => b.images && Object.keys(b.images).length > 0).length;

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg-main)", fontFamily: "var(--font-family)" }}>
      {/* ── Top header bar ────────────────────────────────────── */}
      <header style={{
        borderBottom: "1px solid var(--border-glass)",
        background: "rgba(11,15,25,0.85)",
        backdropFilter: "blur(20px)",
        position: "sticky", top: 0, zIndex: 100,
        padding: "0 2rem",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        height: "64px",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          {/* Fire icon */}
          <div style={{
            width: 40, height: 40, borderRadius: 10,
            background: "linear-gradient(135deg,#ef4444,#f97316)",
            display: "flex", alignItems: "center", justifyContent: "center",
            boxShadow: "0 0 20px rgba(239,68,68,0.4)",
          }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="white"><path d="M12 2C12 2 9 6 9 10c0 1.7.7 3.2 1.8 4.2C10.3 13 10 11.5 10 11c0 0 2 2 2 4a2 2 0 0 1-2 2 4 4 0 0 1-4-4c0-4 6-11 6-11zm0 0c0 0 3 4 3 8 0 1.7-.7 3.2-1.8 4.2C13.7 13 14 11.5 14 11c0 0-2 2-2 4a2 2 0 0 0 2 2 4 4 0 0 0 4-4c0-4-6-11-6-11z"/></svg>
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: "1.2rem", fontWeight: 700, color: "white", letterSpacing: "-0.3px" }}>
              Rapid Crisis
            </h1>
            <p style={{ margin: 0, fontSize: "0.72rem", color: "var(--text-secondary)", letterSpacing: "1.5px", textTransform: "uppercase" }}>
              Smart Fire Evacuation System
            </p>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <div style={{
            display: "flex", alignItems: "center", gap: "0.5rem",
            padding: "0.4rem 0.9rem", borderRadius: 8,
            background: "rgba(16,185,129,0.1)", border: "1px solid rgba(16,185,129,0.3)",
            fontSize: "0.8rem", color: "var(--accent-green)",
          }}>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--accent-green)", display: "inline-block", boxShadow: "0 0 6px var(--accent-green)", animation: "pulse 2s infinite" }} />
            System Online
          </div>
          <button
            id="create-building-btn"
            className="btn btn-primary"
            style={{ padding: "0.5rem 1.2rem", fontSize: "0.88rem" }}
            onClick={() => { setShowCreateModal(true); setCreateError(""); }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 6 }}><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            New Building
          </button>
        </div>
      </header>

      <main style={{ maxWidth: 1280, margin: "0 auto", padding: "2rem 2rem 4rem" }}>

        {/* ── Hero stat bar ──────────────────────────────────── */}
        <div style={{
          display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: "1rem",
          marginBottom: "2.5rem",
        }}>
          {[
            { label: "Total Buildings", value: buildings.length, color: "var(--accent-blue)", icon: "🏢" },
            { label: "Total Nodes", value: totalNodes, color: "var(--accent-green)", icon: "⬡" },
            { label: "Total Edges", value: totalEdges, color: "var(--accent-purple)", icon: "↔" },
            { label: "Blueprints Mapped", value: withImages, color: "var(--accent-amber)", icon: "🗺" },
          ].map((stat, i) => (
            <div key={i} className="glass-panel animate-in" style={{
              animationDelay: `${i * 0.07}s`, padding: "1.2rem 1.5rem",
              display: "flex", alignItems: "center", gap: "1rem",
              borderRadius: 14, position: "relative", overflow: "hidden",
            }}>
              <div style={{
                position: "absolute", right: -10, top: -10,
                fontSize: 56, opacity: 0.06, userSelect: "none",
              }}>{stat.icon}</div>
              <div>
                <p style={{ margin: 0, fontSize: "0.72rem", color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "1px", fontWeight: 600 }}>{stat.label}</p>
                <p style={{ margin: "0.3rem 0 0", fontSize: "2rem", fontWeight: 700, color: stat.color, lineHeight: 1 }}>{stat.value}</p>
              </div>
            </div>
          ))}
        </div>

        {/* ── Toolbar ────────────────────────────────────────── */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.5rem", gap: "1rem" }}>
          <div>
            <h2 style={{ margin: 0, fontSize: "1.1rem", fontWeight: 600, color: "white" }}>
              Building Inventory
            </h2>
            <p style={{ margin: "0.2rem 0 0", fontSize: "0.82rem", color: "var(--text-secondary)" }}>
              {filtered.length} building{filtered.length !== 1 ? "s" : ""} {searchQuery && "matching search"}
            </p>
          </div>
          <div style={{
            display: "flex", alignItems: "center", gap: "0.5rem",
            background: "rgba(255,255,255,0.04)", border: "1px solid var(--border-glass)",
            borderRadius: 10, padding: "0.5rem 1rem", width: 260,
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-secondary)" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Search buildings…"
              style={{ background: "transparent", border: "none", outline: "none", color: "white", fontSize: "0.88rem", fontFamily: "var(--font-family)", width: "100%" }}
            />
          </div>
        </div>

        {/* ── Content ───────────────────────────────────────── */}
        {loading ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: "1.25rem" }}>
            {[1, 2, 3].map((i) => (
              <div key={i} className="glass-panel" style={{ padding: 0, borderRadius: 16, overflow: "hidden", animation: "pulse 1.5s infinite" }}>
                <div style={{ height: 4, background: "var(--border-glass)" }} />
                <div style={{ padding: "1.25rem 1.5rem" }}>
                  <div style={{ display: "flex", gap: "0.6rem", marginBottom: "1rem" }}>
                    <div style={{ width: 32, height: 32, borderRadius: 8, background: "var(--border-glass)" }} />
                    <div>
                      <div style={{ width: 120, height: 16, borderRadius: 4, background: "var(--border-glass)", marginBottom: "0.4rem" }} />
                      <div style={{ width: 80, height: 12, borderRadius: 4, background: "var(--border-glass)" }} />
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.75rem", marginBottom: "1.2rem" }}>
                    {[1, 2, 3].map((j) => (
                      <div key={j} style={{ background: "rgba(0,0,0,0.1)", borderRadius: 10, padding: "0.7rem", height: 60, border: "1px solid var(--border-glass)" }} />
                    ))}
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <div style={{ width: 100, height: 12, borderRadius: 4, background: "var(--border-glass)" }} />
                    <div style={{ width: 60, height: 12, borderRadius: 4, background: "var(--border-glass)" }} />
                  </div>
                </div>
                <div style={{ borderTop: "1px solid var(--border-glass)", padding: "0.6rem 1.5rem", height: 44 }} />
              </div>
            ))}
          </div>
        ) : error ? (
          <div className="glass-panel" style={{ textAlign: "center", padding: "3rem", borderColor: "rgba(239,68,68,0.3)" }}>
            <div style={{ fontSize: 40, marginBottom: "1rem" }}>⚠️</div>
            <h3 style={{ margin: "0 0 0.5rem", color: "var(--accent-red)" }}>Connection Error</h3>
            <p style={{ color: "var(--text-secondary)", margin: "0 0 1.5rem" }}>{error}</p>
            <button className="btn btn-secondary" onClick={fetchBuildings}>Retry</button>
          </div>
        ) : filtered.length === 0 ? (
          <div className="glass-panel animate-in" style={{ textAlign: "center", padding: "5rem 2rem" }}>
            <div style={{
              width: 80, height: 80, borderRadius: 20,
              background: "rgba(59,130,246,0.1)", border: "1px solid rgba(59,130,246,0.2)",
              display: "flex", alignItems: "center", justifyContent: "center",
              margin: "0 auto 1.5rem", fontSize: 36,
            }}>🏗️</div>
            <h3 style={{ margin: "0 0 0.5rem" }}>{searchQuery ? "No buildings match your search" : "No Buildings Yet"}</h3>
            <p style={{ color: "var(--text-secondary)", margin: "0 0 2rem" }}>
              {searchQuery ? "Try a different search term." : "Create your first building to start mapping evacuation routes."}
            </p>
            {!searchQuery && (
              <button className="btn btn-primary" onClick={() => setShowCreateModal(true)}>
                Create First Building
              </button>
            )}
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: "1.25rem" }}>
            {filtered.map((b, i) => {
              const hasImages = b.images && Object.keys(b.images).length > 0;
              const floorCount = b.images ? Object.keys(b.images).length : 0;
              const updatedDate = b.updatedAt ? new Date(b.updatedAt) : null;
              return (
                <div
                  key={b.buildingId || i}
                  className="glass-panel animate-in"
                  style={{
                    animationDelay: `${i * 0.05}s`, padding: 0, overflow: "hidden",
                    borderRadius: 16, cursor: "pointer", position: "relative",
                    transition: "transform 0.2s ease, box-shadow 0.2s ease",
                  }}
                  onMouseEnter={e => { e.currentTarget.style.transform = "translateY(-4px)"; e.currentTarget.style.boxShadow = "0 16px 40px rgba(0,0,0,0.4)"; }}
                  onMouseLeave={e => { e.currentTarget.style.transform = "translateY(0)"; e.currentTarget.style.boxShadow = ""; }}
                >
                  {/* Card header accent */}
                  <div style={{
                    height: 4, background: hasImages
                      ? "linear-gradient(90deg,var(--accent-blue),var(--accent-purple))"
                      : "linear-gradient(90deg,rgba(255,255,255,0.1),rgba(255,255,255,0.05))",
                  }} />

                  <div style={{ padding: "1.25rem 1.5rem" }} onClick={() => navigate(`/editor?id=${b.buildingId}`)}>
                    {/* Title row */}
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1rem" }}>
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "0.3rem" }}>
                          <div style={{
                            width: 32, height: 32, borderRadius: 8,
                            background: "linear-gradient(135deg,rgba(59,130,246,0.2),rgba(168,85,247,0.2))",
                            border: "1px solid rgba(59,130,246,0.3)",
                            display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16,
                          }}>🏢</div>
                          <h3 style={{ margin: 0, fontSize: "1rem", fontWeight: 700, color: "white", letterSpacing: "-0.2px" }}>
                            {b.buildingId}
                          </h3>
                        </div>
                        <span className={`badge ${hasImages ? "badge-blue" : "badge-amber"}`} style={{ fontSize: "0.65rem" }}>
                          {hasImages ? `${floorCount} floor${floorCount !== 1 ? "s" : ""} mapped` : "Blueprints pending"}
                        </span>
                      </div>
                    </div>

                    {/* Stats grid */}
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.75rem", marginBottom: "1.2rem" }}>
                      {[
                        { label: "Nodes", value: b.nodeCount || 0, color: "var(--accent-blue)" },
                        { label: "Edges", value: b.edgeCount || 0, color: "var(--accent-green)" },
                        { label: "Floors", value: floorCount || "—", color: "var(--accent-purple)" },
                      ].map(stat => (
                        <div key={stat.label} style={{
                          background: "rgba(0,0,0,0.2)", borderRadius: 10, padding: "0.7rem",
                          border: "1px solid var(--border-glass)", textAlign: "center",
                        }}>
                          <p style={{ margin: 0, fontSize: "0.68rem", color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.8px", fontWeight: 600 }}>{stat.label}</p>
                          <p style={{ margin: "0.2rem 0 0", fontSize: "1.4rem", fontWeight: 700, color: stat.color, lineHeight: 1 }}>{stat.value}</p>
                        </div>
                      ))}
                    </div>

                    {/* Footer */}
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <p style={{ margin: 0, fontSize: "0.72rem", color: "var(--text-secondary)" }}>
                        {updatedDate ? `Updated ${updatedDate.toLocaleDateString()} ${updatedDate.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Never deployed"}
                      </p>
                      <div style={{
                        fontSize: "0.72rem", color: "var(--accent-blue)",
                        display: "flex", alignItems: "center", gap: "0.3rem",
                        fontWeight: 600,
                      }}>
                        Open Editor
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
                      </div>
                    </div>
                  </div>

                  {/* Delete button */}
                  <div style={{ borderTop: "1px solid var(--border-glass)", padding: "0.6rem 1.5rem", display: "flex", justifyContent: "flex-end" }}>
                    <button
                      className="btn btn-danger"
                      style={{ fontSize: "0.75rem", padding: "0.35rem 0.9rem" }}
                      onClick={e => { e.stopPropagation(); setDeleteTarget(b); }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 4 }}><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
                      Delete
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </main>

      {/* ── Create Building Modal ──────────────────────────── */}
      {showCreateModal && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 1000,
          background: "rgba(0,0,0,0.7)", backdropFilter: "blur(8px)",
          display: "flex", alignItems: "center", justifyContent: "center",
          animation: "fadeIn 0.15s ease",
        }} onClick={() => setShowCreateModal(false)}>
          <div
            className="glass-panel"
            style={{ width: "100%", maxWidth: 480, borderRadius: 20, padding: "2rem", animation: "slideUp 0.2s ease" }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "1.5rem" }}>
              <div style={{
                width: 44, height: 44, borderRadius: 12,
                background: "linear-gradient(135deg,rgba(59,130,246,0.2),rgba(168,85,247,0.2))",
                border: "1px solid rgba(59,130,246,0.3)",
                display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22,
              }}>🏗️</div>
              <div>
                <h2 style={{ margin: 0, fontSize: "1.2rem" }}>Create New Building</h2>
                <p style={{ margin: 0, fontSize: "0.8rem", color: "var(--text-secondary)" }}>Configure your building's identity and structure</p>
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "1.2rem" }}>
              <label className="label">
                Building ID
                <input
                  id="new-building-id"
                  className="input-field"
                  value={newId}
                  onChange={e => { setNewId(e.target.value); setCreateError(""); }}
                  placeholder="e.g. OFFICE_BLOCK_A"
                  onKeyDown={e => e.key === "Enter" && handleCreate()}
                  autoFocus
                />
                <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)", fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>
                  Letters, numbers, underscores only. Will be uppercased.
                </span>
              </label>

              <label className="label">
                Number of Floors
                <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: "0.5rem", marginTop: "0.2rem" }}>
                  {["1","2","3","4","5+"].map(f => (
                    <button
                      key={f}
                      type="button"
                      onClick={() => { if (f === "5+") { setNewFloors(""); } else setNewFloors(f); setCreateError(""); }}
                      style={{
                        padding: "0.6rem", borderRadius: 8, border: "1px solid",
                        borderColor: newFloors === f ? "var(--accent-blue)" : "var(--border-glass)",
                        background: newFloors === f ? "rgba(59,130,246,0.15)" : "rgba(0,0,0,0.2)",
                        color: newFloors === f ? "var(--accent-blue)" : "var(--text-secondary)",
                        cursor: "pointer", fontSize: "0.9rem", fontWeight: 600,
                        transition: "all 0.15s", fontFamily: "var(--font-family)",
                      }}
                    >{f}</button>
                  ))}
                </div>
                {(newFloors === "" || parseInt(newFloors) > 4) && (
                  <input
                    className="input-field"
                    style={{ marginTop: "0.5rem" }}
                    type="number"
                    min="1" max="20"
                    value={newFloors}
                    onChange={e => { setNewFloors(e.target.value); setCreateError(""); }}
                    placeholder="Enter number of floors (1-20)"
                  />
                )}
              </label>

              {createError && (
                <div style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: 8, padding: "0.7rem 1rem", fontSize: "0.83rem", color: "var(--accent-red)", display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <span>⚠️</span> {createError}
                </div>
              )}

              <div style={{ display: "flex", gap: "0.75rem", marginTop: "0.5rem" }}>
                <button className="btn btn-secondary" style={{ flex: 1 }} onClick={() => setShowCreateModal(false)}>
                  Cancel
                </button>
                <button id="confirm-create-btn" className="btn btn-primary" style={{ flex: 2 }} onClick={handleCreate}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" style={{ marginRight: 6 }}><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                  Create & Open Editor
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Delete Confirm Modal ───────────────────────────── */}
      {deleteTarget && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 1000,
          background: "rgba(0,0,0,0.75)", backdropFilter: "blur(8px)",
          display: "flex", alignItems: "center", justifyContent: "center",
          animation: "fadeIn 0.15s ease",
        }} onClick={() => !deleting && setDeleteTarget(null)}>
          <div
            className="glass-panel"
            style={{ width: "100%", maxWidth: 420, borderRadius: 20, padding: "2rem", borderColor: "rgba(239,68,68,0.3)", animation: "slideUp 0.2s ease" }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ textAlign: "center", marginBottom: "1.5rem" }}>
              <div style={{
                width: 64, height: 64, borderRadius: "50%",
                background: "rgba(239,68,68,0.1)", border: "2px solid rgba(239,68,68,0.3)",
                display: "flex", alignItems: "center", justifyContent: "center",
                margin: "0 auto 1rem", fontSize: 28,
              }}>🗑️</div>
              <h2 style={{ margin: "0 0 0.5rem", color: "white" }}>Delete Building?</h2>
              <p style={{ color: "var(--text-secondary)", margin: 0, lineHeight: 1.6 }}>
                You are about to permanently delete{" "}
                <strong style={{ color: "var(--accent-red)" }}>{deleteTarget.buildingId}</strong>.
                <br />This removes all nodes, edges, and blueprints. This cannot be undone.
              </p>
            </div>
            <div style={{ display: "flex", gap: "0.75rem" }}>
              <button className="btn btn-secondary" style={{ flex: 1 }} onClick={() => setDeleteTarget(null)} disabled={deleting}>
                Keep Building
              </button>
              <button id="confirm-delete-btn" className="btn btn-danger" style={{ flex: 1 }} onClick={handleDelete} disabled={deleting}>
                {deleting ? "Deleting…" : "Delete Forever"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Global styles ─────────────────────────────────── */}
      <style>{`
        @keyframes fadeIn { from { opacity: 0 } to { opacity: 1 } }
        @keyframes slideUp { from { opacity: 0; transform: translateY(24px) } to { opacity: 1; transform: translateY(0) } }
        @keyframes spin { to { transform: rotate(360deg) } }
        @keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.4 } }
      `}</style>
    </div>
  );
}
