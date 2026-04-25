import { useEffect, useMemo, useState } from "react";
import { sendBuildingData } from "../services/api";
import NodeCanvas from "../components/NodeCanvas";

function AdminPage() {
  const [buildingId, setBuildingId] = useState("BUILDING_01");
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [sensors, setSensors] = useState([]);
  const [selectedNodeType, setSelectedNodeType] = useState("room");
  const [selectedFloor, setSelectedFloor] = useState(1);
  const [image, setImage] = useState("");
  const [selectedNodesForEdge, setSelectedNodesForEdge] = useState([]);
  const [toggleSensorMode, setToggleSensorMode] = useState(false);

  const buildingData = useMemo(() => {
    return {
      buildingId,
      nodes: nodes.map((n) => ({
        id: n?.id,
        label: n?.label,
        type: n?.type,
        floor: n?.floor,
      })),
      edges: edges.map((e) => ({
        from: e?.from,
        to: e?.to,
      })),
      sensors,
    };
  }, [buildingId, nodes, edges, sensors]);

  useEffect(() => {
    return () => {
      if (image) URL.revokeObjectURL(image);
    };
  }, [image]);

  const handleFileChange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (image) URL.revokeObjectURL(image);
    const url = URL.createObjectURL(file);
    setImage(url);
  };

  const handleNodeClick = (nodeId) => {
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
      setEdges((edgesPrev) => [...edgesPrev, { from, to }]);
      return [];
    });
  };

  const addNode = () => {
    const id = window.prompt(
      "Node ID (e.g., ROOM_101, HALLWAY_A, EXIT_NORTH):",
    );
    if (!id) return;

    setNodes((prev) => {
      if (prev.some((n) => String(n.id) === String(id))) return prev;
      return [
        ...prev,
        {
          id: String(id),
          type: selectedNodeType,
          floor: Number(selectedFloor),
        },
      ];
    });
  };

  const addEdge = () => {
    const from = window.prompt("Edge FROM node ID:");
    if (!from) return;
    const to = window.prompt("Edge TO node ID:");
    if (!to) return;

    setEdges((prev) => [...prev, { from: String(from), to: String(to) }]);
  };

  const toggleSensor = () => {
    setToggleSensorMode((v) => !v);
    setSelectedNodesForEdge([]);
  };

  const submitToBackend = async () => {
    try {
      const response = await sendBuildingData(buildingData);
      console.log("Building setup response:", response);
      window.alert("Submitted successfully.");
    } catch (err) {
      const message =
        err?.response?.data?.message || err?.message || "Request failed";
      window.alert(message);
    }
  };

  return (
    <div style={{ padding: 16 }}>
      <h2>Admin</h2>

      <div
        style={{
          display: "flex",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        <label>
          Building ID:
          <input
            style={{ marginLeft: 8 }}
            value={buildingId}
            onChange={(e) => setBuildingId(e.target.value)}
          />
        </label>

        <label>
          Node Type:
          <select
            style={{ marginLeft: 8 }}
            value={selectedNodeType}
            onChange={(e) => setSelectedNodeType(e.target.value)}
          >
            <option value="room">room</option>
            <option value="hall">hall</option>
            <option value="exit">exit</option>
          </select>
        </label>

        <label>
          Floor:
          <select
            style={{ marginLeft: 8 }}
            value={selectedFloor}
            onChange={(e) => setSelectedFloor(Number(e.target.value))}
          >
            <option value={0}>0</option>
            <option value={1}>1</option>
          </select>
        </label>
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
        <button type="button" onClick={addNode}>
          Add Node
        </button>
        <button type="button" onClick={addEdge}>
          Add Edge
        </button>
        <button type="button" onClick={toggleSensor}>
          Toggle Sensor
        </button>
        <button type="button" onClick={submitToBackend}>
          Submit to Backend
        </button>
      </div>

      <div style={{ marginTop: 12 }}>
        <label>
          Upload Image:
          <input
            style={{ marginLeft: 8 }}
            type="file"
            accept="image/*"
            onChange={handleFileChange}
          />
        </label>

        <div style={{ marginTop: 12 }}>
          <NodeCanvas
            image={image}
            nodes={nodes}
            setNodes={setNodes}
            edges={edges}
            setEdges={setEdges}
            selectedNodeType={selectedNodeType}
            selectedFloor={selectedFloor}
          />
        </div>
      </div>

      <div style={{ marginTop: 16 }}>
        <h3>Nodes</h3>
        <div style={{ fontSize: 12, marginBottom: 8 }}>
          {toggleSensorMode
            ? "Toggle Sensor mode: click nodes to add/remove sensors."
            : "Click two nodes to create an edge."}
          {!toggleSensorMode && selectedNodesForEdge.length > 0
            ? ` Selected: ${selectedNodesForEdge.join(" → ")}`
            : ""}
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {nodes.map((n) => {
            const id = String(n.id);
            const selected = selectedNodesForEdge.includes(id);
            const isSensor = sensors.includes(id);
            return (
              <button
                key={id}
                type="button"
                onClick={() => handleNodeClick(id)}
                style={{
                  border: "1px solid #ccc",
                  padding: "6px 10px",
                  background: toggleSensorMode
                    ? isSensor
                      ? "#eaeaea"
                      : "white"
                    : selected
                      ? "#eaeaea"
                      : "white",
                }}
              >
                {n.label || id}
              </button>
            );
          })}
        </div>
      </div>

      <h3 style={{ marginTop: 16 }}>JSON Preview</h3>
      <pre
        style={{
          padding: 12,
          background: "#f5f5f5",
          borderRadius: 6,
          overflow: "auto",
          maxHeight: 360,
        }}
      >
        {JSON.stringify(buildingData, null, 2)}
      </pre>
    </div>
  );
}

export default AdminPage;
