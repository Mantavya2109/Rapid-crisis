import { useEffect, useMemo, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { sendBuildingData, getBuilding } from "../services/api";
import NodeCanvas from "../components/NodeCanvas";

function AdminPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const searchParams = new URLSearchParams(location.search);
  
  const [buildingId, setBuildingId] = useState(searchParams.get("id") || "BUILDING_01");
  const [totalFloors, setTotalFloors] = useState(Number(searchParams.get("floors")) || 1);
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [sensors, setSensors] = useState([]);
  const [selectedNodeType, setSelectedNodeType] = useState("room");
  const [selectedFloor, setSelectedFloor] = useState(0);
  const [images, setImages] = useState({}); // { [floorIndex]: base64 }
  const [selectedNodesForEdge, setSelectedNodesForEdge] = useState([]);
  const [toggleSensorMode, setToggleSensorMode] = useState(false);
  const [deleteMode, setDeleteMode] = useState(false);
  const [deployStatus, setDeployStatus] = useState(null); // null | 'loading' | 'success' | 'error'
  const [deployMessage, setDeployMessage] = useState("");

  const buildingData = useMemo(() => {
    // Generate clean edges (no duplicate direction)
    const cleanEdges = [];
    const edgeSet = new Set();
    
    edges.forEach((e) => {
      // sort to avoid a->b and b->a
      const sorted = [String(e.from), String(e.to)].sort();
      const key = `${sorted[0]}-${sorted[1]}`;
      if (!edgeSet.has(key)) {
        edgeSet.add(key);
        cleanEdges.push({ from: e.from, to: e.to, weight: 1 });
      }
    });

    return {
      buildingId,
      nodes: nodes.map((n) => ({
        id: n?.id,
        type: n?.type,
        floor: Math.max(0, Number(n?.floor) || 0),
        x: n?.x,
        y: n?.y,
      })),
      edges: cleanEdges,
      sensors,
      images,
    };
  }, [buildingId, nodes, edges, sensors, images]);

  // Floor-filtered views for canvas and directory
  const floorNodes = useMemo(() => nodes.filter(n => n.floor === selectedFloor), [nodes, selectedFloor]);
  const floorEdges = useMemo(() => {
    const floorNodeIds = new Set(floorNodes.map(n => String(n.id)));
    return edges.filter(e => floorNodeIds.has(String(e.from)) && floorNodeIds.has(String(e.to)));
  }, [edges, floorNodes]);

  useEffect(() => {
    const fetchBuilding = async () => {
      const id = searchParams.get("id");
      if (id) {
        try {
          const data = await getBuilding(id);
          if (data) {
            const loadedNodes = data.nodes || [];
            setNodes(loadedNodes);
            setEdges(data.edges || []);
            if (data.images && Object.keys(data.images).length > 0) {
              // Normalize keys to numbers for consistent floor lookup
              const normalizedImages = {};
              for (const [k, v] of Object.entries(data.images)) {
                normalizedImages[Number(k)] = v;
              }
              setImages(normalizedImages);
            }
            if (data.sensors) setSensors(data.sensors);
            // Auto-detect total floors from node data
            if (loadedNodes.length > 0) {
              const maxFloor = Math.max(...loadedNodes.map(n => n.floor || 0));
              setTotalFloors(maxFloor + 1);
            }
          }
        } catch (err) {
          console.error("Failed to load building:", err);
        }
      }
    };
    fetchBuilding();
  }, []);

  const handleFileChange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onloadend = () => {
      setImages(prev => ({ ...prev, [selectedFloor]: reader.result }));
    };
    reader.readAsDataURL(file);
  };

  const handleDeleteNode = (id) => {
    setNodes(prev => prev.filter(n => String(n.id) !== String(id)));
    setEdges(prev => prev.filter(e => String(e.from) !== String(id) && String(e.to) !== String(id)));
    setSensors(prev => prev.filter(s => String(s) !== String(id)));
  };

  const handleDeleteEdge = (edgeToDelete) => {
    setEdges(prev => prev.filter(e => e !== edgeToDelete));
  };

  const handleNodeClick = (nodeId) => {
    if (deleteMode) {
      handleDeleteNode(nodeId);
      return;
    }

    const id = String(nodeId);

    if (toggleSensorMode) {
      setSensors((prev) =>
        prev.includes(id) ? prev.filter((n) => n !== id) : [...prev, id],
      );
      return;
    }

    setSelectedNodesForEdge((prev) => {
      if (prev.includes(id)) return prev;

      const next = [...prev, id];
      if (next.length < 2) return next;

      const [from, to] = next;
      
      const fromNode = nodes.find(n => String(n.id) === from);
      const toNode = nodes.find(n => String(n.id) === to);

      if (fromNode && toNode) {
        if (fromNode.id === toNode.id) {
          window.alert("Cannot connect a node to itself.");
          return [];
        }

        // Normalize type for comparison (stair-up → stair, stair-down → stair)
        const normalizeType = (t) => {
          if (!t) return "";
          if (t.startsWith("stair")) return "stair";
          return t;
        };

        const isValidConnection = (type1, type2) => {
          const t1 = normalizeType(type1);
          const t2 = normalizeType(type2);
          const pair = [t1, t2].sort().join("-");
          if (pair === "corridor-room") return true;
          if (pair === "corridor-corridor") return true;
          if (pair === "corridor-exit") return true;
          if (pair === "corridor-stair") return true;
          if (pair === "stair-stair") return true;
          if (pair === "exit-stair") return true;
          return false;
        };

        if (!isValidConnection(fromNode.type, toNode.type)) {
          window.alert(`Invalid connection: ${fromNode.type} → ${toNode.type}.\nUse corridors to connect rooms, exits, and stairs.`);
          return [];
        }

        // Cross-floor: only stair nodes can bridge floors
        if (fromNode.floor !== toNode.floor) {
          const t1 = normalizeType(fromNode.type);
          const t2 = normalizeType(toNode.type);
          if (t1 !== "stair" || t2 !== "stair") {
            window.alert("Cross-floor connections must use stair nodes on both ends.");
            return [];
          }
        }


        setEdges((edgesPrev) => {
          const isDuplicate = edgesPrev.some(
            (e) => (e.from === fromNode.id && e.to === toNode.id) || 
                   (e.from === toNode.id && e.to === fromNode.id)
          );
          if (isDuplicate) {
            window.alert("Edge already exists.");
            return edgesPrev;
          }
          return [...edgesPrev, { from: fromNode.id, to: toNode.id }];
        });
      }
      
      return [];
    });
  };

  // Manual node/edge addition has been removed in favor of purely interactive canvas drawing

  const toggleSensor = () => {
    setToggleSensorMode((v) => !v);
    setDeleteMode(false);
    setSelectedNodesForEdge([]);
  };

  const toggleDeleteMode = () => {
    setDeleteMode((v) => !v);
    setToggleSensorMode(false);
    setSelectedNodesForEdge([]);
  };

  const validateGraph = () => {
    if (nodes.length === 0) {
      setDeployStatus("error");
      setDeployMessage("Add at least one node to the canvas before deploying.");
      return false;
    }
    return true;
  };

  const submitToBackend = async () => {
    if (!validateGraph()) return;
    setDeployStatus("loading");
    setDeployMessage("");
    try {
      await sendBuildingData(buildingData);
      setDeployStatus("success");
      setDeployMessage(`Building "${buildingId}" saved to Firebase!`);
      // Auto-clear after 4s
      setTimeout(() => setDeployStatus(null), 4000);
    } catch (err) {
      console.error("Deploy error:", err);
      const msg = err?.response?.data?.message || err?.message || "Deploy failed";
      setDeployStatus("error");
      setDeployMessage(msg);
    }
  };
  return (
    <div className="dashboard">
      <aside className="sidebar glass-panel animate-in">
        <div>
          <button type="button" className="btn btn-secondary" style={{ padding: "0.2rem 0.5rem", fontSize: "0.8rem", marginBottom: "1rem" }} onClick={() => navigate("/")}>
            ← Back to Dashboard
          </button>
          <h2 style={{ marginBottom: "0.2rem", color: "var(--accent-blue)" }}>Rapid Crisis</h2>
          <p style={{ margin: "0 0 1.5rem 0", fontSize: "0.85rem", color: "var(--text-secondary)" }}>
            Graph Editor UI
          </p>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <label className="label">
            Building ID
            <input
              className="input-field"
              value={buildingId}
              onChange={(e) => setBuildingId(e.target.value)}
              placeholder="e.g. BUILDING_01"
            />
          </label>

          <div style={{ display: "flex", gap: "1rem" }}>
            <label className="label" style={{ flex: 1 }}>
              Total Floors
              <input
                className="input-field"
                type="number"
                min="0"
                value={totalFloors}
                onChange={(e) => setTotalFloors(Math.max(0, parseInt(e.target.value) || 0))}
              />
            </label>
            <label className="label" style={{ flex: 1 }}>
              Current Floor
              <select
                className="input-field"
                value={selectedFloor}
                onChange={(e) => setSelectedFloor(Number(e.target.value))}
              >
                {Array.from({ length: totalFloors + 1 }).map((_, i) => (
                  <option key={i} value={i}>
                    {i === 0 ? "Ground (0)" : `Floor ${i}`}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="label">
            Node Type
            <select
              className="input-field"
              value={selectedNodeType}
              onChange={(e) => setSelectedNodeType(e.target.value)}
            >
              <option value="room">Room</option>
              <option value="corridor">Corridor</option>
              <option value="exit">Exit</option>
              <option value="stair-up">Stair Up</option>
              <option value="stair-down">Stair Down</option>
            </select>
          </label>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", marginTop: "0.5rem" }}>
          <button 
            type="button" 
            className={`btn ${toggleSensorMode ? 'btn-amber' : 'btn-secondary'}`} 
            onClick={toggleSensor}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.24a4.5 4.5 0 1 0 5 0z"/><path d="M11.5 6v6"/></svg>
            {toggleSensorMode ? "Sensor Mode: ON" : "Toggle Sensor Mode"}
          </button>
          <button 
            type="button" 
            className={`btn ${deleteMode ? 'btn-danger' : 'btn-secondary'}`} 
            onClick={toggleDeleteMode}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
            {deleteMode ? "Delete Mode: ON" : "Toggle Delete Mode"}
          </button>
          
          <div style={{ marginTop: "1rem", paddingTop: "1rem", borderTop: "1px solid var(--border-glass)", display: "flex", flexDirection: "column", gap: "0.6rem" }}>
            {/* Deploy status banner */}
            {deployStatus === "success" && (
              <div style={{
                background: "rgba(16,185,129,0.12)", border: "1px solid rgba(16,185,129,0.4)",
                borderRadius: 8, padding: "0.8rem", fontSize: "0.8rem",
                animation: "fadeIn 0.3s ease",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", color: "var(--accent-green)", marginBottom: "0.5rem" }}>
                  <span style={{ fontSize: 16 }}>✅</span> {deployMessage}
                </div>
                <button
                  className="btn btn-secondary"
                  style={{ width: "100%", fontSize: "0.78rem", padding: "0.4rem" }}
                  onClick={() => navigate("/")}
                >
                  ← View in Dashboard
                </button>
              </div>
            )}
            {deployStatus === "error" && (
              <div style={{
                display: "flex", alignItems: "center", gap: "0.5rem",
                background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.4)",
                borderRadius: 8, padding: "0.6rem 0.8rem", fontSize: "0.8rem", color: "var(--accent-red)",
                animation: "fadeIn 0.3s ease",
              }}>
                <span style={{ fontSize: 16 }}>❌</span> {deployMessage}
              </div>
            )}
            <button
              type="button"
              className="btn btn-primary"
              style={{
                width: "100%",
                opacity: deployStatus === "loading" ? 0.8 : 1,
                background: deployStatus === "success"
                  ? "linear-gradient(135deg, #059669, #10b981)"
                  : undefined,
                transition: "background 0.3s ease",
              }}
              onClick={submitToBackend}
              disabled={deployStatus === "loading"}
            >
              {deployStatus === "loading" ? (
                <>
                  <span style={{
                    display: "inline-block", width: 14, height: 14,
                    border: "2px solid rgba(255,255,255,0.3)",
                    borderTopColor: "white", borderRadius: "50%",
                    animation: "spin 0.7s linear infinite", marginRight: 8,
                  }} />
                  Saving to Firebase…
                </>
              ) : deployStatus === "success" ? (
                <><span style={{ marginRight: 6 }}>✅</span> Saved! Deploy Again</>
              ) : (
                <>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 6 }}>
                    <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
                  </svg>
                  Deploy Configuration
                </>
              )}
            </button>
            <style>{`
              @keyframes fadeIn { from { opacity:0; transform:translateY(-4px) } to { opacity:1; transform:translateY(0) } }
              @keyframes spin { to { transform: rotate(360deg) } }
            `}</style>
          </div>

        </div>

        <div style={{ marginTop: "auto" }}>
          <h4 style={{ marginBottom: "0.5rem", fontSize: "0.9rem" }}>Blueprint Map (Floor {selectedFloor})</h4>
          <label className="label">
            <input
              className="input-field"
              type="file"
              accept="image/*"
              onChange={handleFileChange}
              style={{ padding: "0.3rem" }}
            />
          </label>
        </div>
      </aside>

      <main className="main-content">
        <div className="glass-panel animate-in" style={{ display: "flex", flexDirection: "column", animationDelay: "0.1s", overflow: "hidden", minHeight: 0 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
            <h3 style={{ margin: 0 }}>Interactive Canvas</h3>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <span className="badge badge-blue">Nodes: {floorNodes.length}</span>
              <span className="badge badge-green">Edges: {floorEdges.length}</span>
              <span className="badge badge-amber">Sensors: {sensors.length}</span>
            </div>
          </div>
          
          <div className="canvas-container">
            {images[selectedFloor] ? (
              <NodeCanvas
                image={images[selectedFloor]}
                nodes={floorNodes}
                setNodes={setNodes}
                edges={floorEdges}
                setEdges={setEdges}
                selectedNodeType={selectedNodeType}
                selectedFloor={selectedFloor}
                totalFloors={totalFloors}
                deleteMode={deleteMode}
                onDeleteNode={handleDeleteNode}
                onDeleteEdge={handleDeleteEdge}
              />
            ) : (
              <div style={{ textAlign: "center", color: "var(--text-secondary)" }}>
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" style={{ marginBottom: "1rem", opacity: 0.5 }}><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>
                <p>Upload a blueprint image to start mapping nodes.</p>
              </div>
            )}
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem", overflow: "hidden" }}>
          <div className="glass-panel animate-in" style={{ animationDelay: "0.2s" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
              <h3 style={{ margin: 0, fontSize: "1.1rem" }}>Node Directory</h3>
              <span style={{ fontSize: "0.8rem", color: deleteMode ? "var(--accent-red)" : "var(--accent-blue)" }}>
                {deleteMode
                  ? "Click node to delete"
                  : toggleSensorMode
                    ? "Click node to toggle sensor"
                    : selectedNodesForEdge.length > 0
                      ? `Linking: ${selectedNodesForEdge.join(" → ")}`
                      : "Click 2 nodes to link"}
              </span>
            </div>
            
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", maxHeight: "200px", overflowY: "auto", paddingRight: "0.5rem" }}>
              {floorNodes.length === 0 && <span style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>No nodes on this floor yet.</span>}
              {floorNodes.map((n) => {
                const id = String(n.id);
                const isLinking = selectedNodesForEdge.includes(id);
                const isSensor = sensors.includes(id);
                
                let btnClass = "node-btn";
                if (isSensor) btnClass += " sensor";
                if (isLinking && !toggleSensorMode && !deleteMode) btnClass += " selected";

                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => handleNodeClick(id)}
                    className={btnClass}
                  >
                    {n.id}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="glass-panel animate-in" style={{ animationDelay: "0.3s", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
            <h3 style={{ margin: "0 0 1rem 0", fontSize: "1.1rem" }}>System Payload</h3>
            <pre
              style={{
                margin: 0,
                padding: "1rem",
                background: "rgba(0,0,0,0.3)",
                borderRadius: "8px",
                border: "1px solid var(--border-glass)",
                overflow: "auto",
                flex: 1,
                fontSize: "0.85rem",
                color: "#a78bfa",
                fontFamily: "monospace"
              }}
            >
              {JSON.stringify({
                ...buildingData,
                images: Object.fromEntries(
                  Object.entries(buildingData.images || {}).map(([k, v]) => [
                    k,
                    typeof v === "string" && v.length > 100
                      ? v.substring(0, 40) + "... [TRUNCATED]"
                      : v
                  ])
                )
              }, null, 2)}
            </pre>
          </div>
        </div>
      </main>
    </div>
  );
}

export default AdminPage;
