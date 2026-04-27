import { useEffect, useMemo, useRef, useState } from "react";

const TYPE_COLOR = {
  room: "var(--accent-green)",
  corridor: "var(--accent-blue)",
  exit: "var(--accent-amber)",
  "stair-up": "var(--accent-purple)",
  "stair-down": "var(--accent-purple)",
};

export default function NodeCanvas({
  image,
  nodes,
  setNodes,
  edges = [],
  setEdges,
  selectedNodeType,
  selectedFloor,
  totalFloors,
  deleteMode = false,
  onDeleteNode,
  onDeleteEdge,
}) {
  const [selectedNode, setSelectedNode] = useState(null);
  const [draggingNodeId, setDraggingNodeId] = useState(null);
  const dragStartPos = useRef({ x: 0, y: 0 });
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
    if (!image || deleteMode) return;

    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    setCanvasSize({ width: rect.width, height: rect.height });

    setNodes((prev) => {
      const prefixMap = {
        room: "ROOM",
        corridor: "CORRIDOR",
        exit: "EXIT",
        stair: "STAIR"
      };

      let id = "";
      let stairCount = 0;
      if (selectedNodeType.startsWith("stair")) {
        stairCount = prev.filter(n => n.type === selectedNodeType && n.floor === selectedFloor).length + 1;
        const floorStr = selectedFloor === 0 ? "G" : (selectedFloor === 1 ? "F" : selectedFloor);
        const dirStr = selectedNodeType === "stair-up" ? "UP" : "DOWN";
        id = `STAIR_${dirStr}_${floorStr}_${stairCount}`;
      } else {
        const count = prev.filter(n => n.type === selectedNodeType).length + 1;
        id = `${prefixMap[selectedNodeType]}_${count}`;
      }

      const newNode = {
        id,
        type: selectedNodeType,
        floor: selectedFloor,
        x,
        y,
      };

      if (selectedNodeType === "stair-up" && selectedFloor < totalFloors) {
        const nextFloor = selectedFloor + 1;
        const downFloorStr = nextFloor === 0 ? "G" : (nextFloor === 1 ? "F" : nextFloor);
        const downId = `STAIR_DOWN_${downFloorStr}_${stairCount}`;
        
        const downNode = {
          id: downId,
          type: "stair-down",
          floor: nextFloor,
          x,
          y,
        };

        if (typeof setEdges === "function") {
          // Set edge timeout to avoid React state batching issues with prev
          setTimeout(() => {
            setEdges(edgesPrev => [...edgesPrev, { from: id, to: downId }]);
          }, 0);
        }

        return [...prev, newNode, downNode];
      }

      return [...prev, newNode];
    });
  };

  const handleNodeClick = (nodeId) => {
    if (deleteMode) return;
    
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
      const fromNode = nodes.find(n => String(n.id) === selectedNode);
      const toNode = nodes.find(n => String(n.id) === id);

      if (fromNode && toNode) {
        if (fromNode.id === toNode.id) {
          window.alert("Cannot connect a node to itself.");
          setSelectedNode(null);
          return;
        }

        const isValidConnection = (type1, type2) => {
          const t1 = type1.startsWith("stair") ? "stair" : type1;
          const t2 = type2.startsWith("stair") ? "stair" : type2;
          const pair = [t1, t2].sort().join("-");
          if (pair === "corridor-room") return true;
          if (pair === "corridor-corridor") return true;
          if (pair === "corridor-exit") return true;
          if (pair === "corridor-stair") return true;
          if (pair === "stair-stair") return true;
          return false;
        };

        if (!isValidConnection(fromNode.type, toNode.type)) {
          window.alert("Invalid connection. Use corridor between rooms.");
          setSelectedNode(null);
          return;
        }

        if (fromNode.floor !== toNode.floor) {
          if (!fromNode.type.startsWith("stair") || !toNode.type.startsWith("stair")) {
            window.alert("Cross-floor connections must involve a stair node.");
            setSelectedNode(null);
            return;
          }
        }

        setEdges((prev) => {
          const isDuplicate = prev.some(
            (e) => (e.from === fromNode.id && e.to === toNode.id) || 
                   (e.from === toNode.id && e.to === fromNode.id)
          );
          if (isDuplicate) {
            window.alert("Edge already exists.");
            return prev;
          }
          return [...prev, { from: selectedNode, to: id }];
        });
      }
    }
    setSelectedNode(null);
  };

  const handlePointerDown = (e, nodeId) => {
    e.stopPropagation();
    if (deleteMode) {
      if (typeof onDeleteNode === "function") {
        onDeleteNode(nodeId);
      }
      return;
    }
    setDraggingNodeId(String(nodeId));
    dragStartPos.current = { x: e.clientX, y: e.clientY };
    e.target.setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e) => {
    if (deleteMode || !draggingNodeId) return;
    if (!imgRef.current) return;
    
    const rect = imgRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    setNodes((prev) => 
      prev.map((n) => String(n.id) === draggingNodeId ? { ...n, x, y } : n)
    );
  };

  const handlePointerUp = (e) => {
    if (deleteMode) return;
    if (draggingNodeId) {
      const dx = Math.abs(e.clientX - dragStartPos.current.x);
      const dy = Math.abs(e.clientY - dragStartPos.current.y);
      if (dx < 3 && dy < 3) {
        // It was a click
        handleNodeClick(draggingNodeId);
      }
      setDraggingNodeId(null);
      e.target.releasePointerCapture(e.pointerId);
    }
  };

  if (!image) return null;

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
        <img
          ref={imgRef}
          src={image}
          alt="Canvas"
          onClick={handleImageClick}
          onLoad={updateCanvasSize}
          style={{ display: "block", maxWidth: "100%", cursor: deleteMode ? "not-allowed" : "crosshair" }}
        />

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
            pointerEvents: deleteMode ? "auto" : "none",
            overflow: "visible",
            zIndex: 1,
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
                stroke={deleteMode ? "rgba(239, 68, 68, 0.8)" : "rgba(255, 255, 255, 0.4)"}
                strokeWidth={deleteMode ? "8" : "3"}
                strokeDasharray={deleteMode ? "none" : "6, 6"}
                strokeLinecap="round"
                style={{ cursor: deleteMode ? "crosshair" : "default", pointerEvents: deleteMode ? "stroke" : "none" }}
                onPointerDown={(evt) => {
                  if (deleteMode && typeof onDeleteEdge === "function") {
                    evt.stopPropagation();
                    onDeleteEdge(e);
                  }
                }}
              />
            );
          })}
        </svg>

        {nodes.map((n) => {
          const color = TYPE_COLOR[n.type] || "gray";
          const isSelected = selectedNode === String(n.id);
          const isDragging = draggingNodeId === String(n.id);

          return (
            <div
              key={n.id}
              title={n.id}
              onPointerDown={(e) => handlePointerDown(e, n.id)}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              className={!isDragging && !deleteMode ? "node-glow" : ""}
              style={{
                position: "absolute",
                width: 14,
                height: 14,
                borderRadius: "50%",
                background: deleteMode ? "var(--accent-red)" : color,
                border: isSelected ? "3px solid white" : "2px solid rgba(255,255,255,0.8)",
                left: n.x,
                top: n.y,
                transform: "translate(-50%, -50%)",
                boxSizing: "border-box",
                cursor: deleteMode ? "crosshair" : (isDragging ? "grabbing" : "grab"),
                zIndex: isDragging || deleteMode ? 10 : 2,
                color: deleteMode ? "var(--accent-red)" : color,
                boxShadow: (isSelected || isDragging) && !deleteMode ? `0 0 15px ${color}` : "none",
                transition: isDragging ? "none" : "box-shadow 0.2s ease, border 0.2s ease, background 0.2s ease",
                pointerEvents: "auto"
              }}
            />
          );
        })}
      </div>
  );
}
