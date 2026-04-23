/**
 * Find the shortest path from a start node to the nearest exit node.
 * Exit nodes are identified by having "EXIT" in their id.
 *
 * @param {string} startNodeId
 * @param {Array<{from: string, to: string}>} edges
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

  // Build adjacency list (treat edges as bidirectional for BFS)
  const adjacency = new Map();
  const addNeighbor = (a, b) => {
    if (blocked.has(a) || blocked.has(b)) return;
    if (!adjacency.has(a)) adjacency.set(a, []);
    adjacency.get(a).push(b);
  };

  for (const edge of edges) {
    if (!edge) continue;
    const from = edge.from;
    const to = edge.to;
    if (!from || !to) continue;
    addNeighbor(from, to);
    addNeighbor(to, from);
  }

  // BFS
  const queue = [effectiveStartNodeId];
  const visited = new Set([effectiveStartNodeId]);
  const parent = new Map();

  for (let i = 0; i < queue.length; i++) {
    const current = queue[i];
    const neighbors = adjacency.get(current) || [];

    for (const next of neighbors) {
      if (blocked.has(next) || visited.has(next)) continue;

      visited.add(next);
      parent.set(next, current);

      if (isExit(next)) {
        // Reconstruct path: next -> ... -> startNodeId
        const path = [];
        let node = next;
        while (node !== undefined) {
          path.push(node);
          if (node === effectiveStartNodeId) break;
          node = parent.get(node);
        }
        return path.reverse();
      }

      queue.push(next);
    }
  }

  return [];
};
