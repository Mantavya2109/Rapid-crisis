/**
 * Find the shortest path from a start node to the nearest exit node.
 * Exit nodes are identified by having "EXIT" in their id.
 *
 * @param {string} startNodeId
 * @param {Array<{from: string, to: string, distance?: number}>} edges
 * @param {string[]|Set<string>} blockedNodes
 * @returns {string[]} Path as an array of node ids (empty if no path found)
 */
export const findShortestPathToNearestExit = (
  startNodeId,
  edges = [],
  blockedNodes = [],
) => {
  const blocked =
    blockedNodes instanceof Set ? blockedNodes : new Set(blockedNodes);

  if (!startNodeId) return [];

  let effectiveStartNodeId = startNodeId;
  if (blocked.has(startNodeId)) {
    const neighborSet = new Set();

    for (const edge of edges) {
      if (!edge) continue;
      const from = edge.from;
      const to = edge.to;
      if (!from || !to) continue;

      if (from === startNodeId) neighborSet.add(to);
      else if (to === startNodeId) neighborSet.add(from);
    }

    const safeNeighbors = Array.from(neighborSet).filter(
      (id) => id && !blocked.has(id),
    );

    if (safeNeighbors.length === 0) return [];
    effectiveStartNodeId = safeNeighbors[0];
  }

  const isExit = (nodeId) => String(nodeId).toUpperCase().includes("EXIT");
  if (isExit(effectiveStartNodeId)) return [effectiveStartNodeId];

  // Build weighted adjacency list (treat edges as bidirectional)
  // distance defaults to 1 when missing.
  const adjacency = new Map();
  const addNeighbor = (a, b, w) => {
    if (blocked.has(a) || blocked.has(b)) return;
    if (!adjacency.has(a)) adjacency.set(a, []);
    adjacency.get(a).push({ to: b, w });
  };

  for (const edge of edges) {
    if (!edge) continue;
    const from = edge.from;
    const to = edge.to;
    if (!from || !to) continue;

    const rawW = edge.distance ?? 1;
    const w =
      Number.isFinite(Number(rawW)) && Number(rawW) > 0 ? Number(rawW) : 1;

    addNeighbor(from, to, w);
    addNeighbor(to, from, w);
  }

  // Dijkstra (simple array-based priority queue for small graphs)
  const dist = new Map();
  const prev = new Map();
  const visited = new Set();

  dist.set(effectiveStartNodeId, 0);

  /** @type {Array<{ id: string, d: number }>} */
  const pq = [{ id: effectiveStartNodeId, d: 0 }];

  while (pq.length > 0) {
    // Extract min
    let minIdx = 0;
    for (let i = 1; i < pq.length; i++) {
      if (pq[i].d < pq[minIdx].d) minIdx = i;
    }
    const [{ id: current, d: currentDist }] = pq.splice(minIdx, 1);

    if (visited.has(current)) continue;
    visited.add(current);

    // First exit popped is guaranteed shortest by total distance
    if (current !== effectiveStartNodeId && isExit(current)) {
      const path = [];
      let node = current;
      while (node !== undefined) {
        path.push(node);
        if (node === effectiveStartNodeId) break;
        node = prev.get(node);
      }
      return path.reverse();
    }

    const neighbors = adjacency.get(current) || [];
    for (const { to: next, w } of neighbors) {
      if (!next || blocked.has(next) || visited.has(next)) continue;

      const nextDist = currentDist + w;
      const best = dist.get(next);
      if (best === undefined || nextDist < best) {
        dist.set(next, nextDist);
        prev.set(next, current);
        pq.push({ id: next, d: nextDist });
      }
    }
  }

  return [];
};
