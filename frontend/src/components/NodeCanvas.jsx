import { useEffect, useMemo, useRef, useState } from "react";

const TYPE_COLOR = {
  room: "blue",
  hall: "yellow",
  exit: "green",
};

export default function NodeCanvas({
  image,
  nodes,
  setNodes,
  edges = [],
  setEdges,
  selectedNodeType,
  selectedFloor,
}) {
  const [selectedNode, setSelectedNode] = useState(null);
  const imgRef = useRef(null);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });

  const nodePosById = useMemo(() => {
    const map = new Map();
    nodes.forEach((n) => {
      if (!n?.id) return;
      if (typeof n.x !== "number" || typeof n.y !== "number") return;
      map.set(String(n.id), { x: n.x, y: n.y });
    });
    return map;
  }, [nodes]);

  const updateCanvasSize = () => {
    if (!imgRef.current) return;
    const rect = imgRef.current.getBoundingClientRect();
    setCanvasSize({ width: rect.width, height: rect.height });
  };

  useEffect(() => {
    updateCanvasSize();
    window.addEventListener("resize", updateCanvasSize);
    return () => window.removeEventListener("resize", updateCanvasSize);
  }, [image]);

  const handleImageClick = (e) => {
    if (!image) return;

    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    setCanvasSize({ width: rect.width, height: rect.height });

    setNodes((prev) => {
      const number = prev.length + 1;
      const newNode = {
        id: `NODE_${number}`,
        label: `Node ${number}`,
        type: selectedNodeType,
        floor: selectedFloor,
        x,
        y,
      };
      return [...prev, newNode];
    });
  };

  const handleNodeClick = (nodeId) => {
    const id = String(nodeId);

    if (selectedNode === null) {
      setSelectedNode(id);
      return;
    }

    if (selectedNode === id) {
      setSelectedNode(null);
      return;
    }

    if (typeof setEdges === "function") {
      setEdges((prev) => [...prev, { from: selectedNode, to: id }]);
    }
    setSelectedNode(null);
  };

  if (!image) return null;

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <svg
        width={canvasSize.width || "100%"}
        height={canvasSize.height || "100%"}
        viewBox={
          canvasSize.width && canvasSize.height
            ? `0 0 ${canvasSize.width} ${canvasSize.height}`
            : undefined
        }
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          pointerEvents: "none",
          overflow: "visible",
        }}
      >
        {edges.map((e, idx) => {
          const fromId = String(e?.from ?? "");
          const toId = String(e?.to ?? "");
          const from = nodePosById.get(fromId);
          const to = nodePosById.get(toId);
          if (!from || !to) return null;

          return (
            <line
              key={`${fromId}-${toId}-${idx}`}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              stroke="black"
              strokeWidth="2"
            />
          );
        })}
      </svg>

      <img
        ref={imgRef}
        src={image}
        alt="Canvas"
        onClick={handleImageClick}
        onLoad={updateCanvasSize}
        style={{ display: "block", maxWidth: "100%", height: "auto" }}
      />

      {nodes.map((n) => {
        const color = TYPE_COLOR[n.type] || "gray";
        const isSelected = selectedNode === String(n.id);

        return (
          <div
            key={n.id}
            title={n.label || n.id}
            onClick={() => handleNodeClick(n.id)}
            style={{
              position: "absolute",
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: color,
              border: isSelected ? "2px solid red" : "none",
              left: n.x,
              top: n.y,
              transform: "translate(-50%, -50%)",
              boxSizing: "border-box",
              cursor: "pointer",
            }}
          />
        );
      })}
    </div>
  );
}
