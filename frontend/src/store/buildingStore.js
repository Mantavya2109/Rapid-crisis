/**
 * buildingStore.js
 * Zustand global store for building setup flow state.
 */
import { create } from 'zustand';

const useBuildingStore = create((set, get) => ({
  // ── New Building Flow ──────────────────────────────────────
  name: '',
  floors: 0,
  floorData: {}, // { [floorNum]: { image: File|null, imageUrl: string|null, nodes: [], edges: [] } }

  setBuilding: (name, floors) =>
    set(() => {
      const floorData = {};
      for (let i = 1; i <= floors; i++) {
        floorData[i] = { image: null, imageUrl: null, nodes: [], edges: [] };
      }
      return { name, floors, floorData };
    }),

  setFloorImage: (floorNum, file, imageUrl) =>
    set((state) => ({
      floorData: {
        ...state.floorData,
        [floorNum]: { ...state.floorData[floorNum], image: file, imageUrl },
      },
    })),

  setFloorGrid: (floorNum, nodes, edges) =>
    set((state) => ({
      floorData: {
        ...state.floorData,
        [floorNum]: { ...state.floorData[floorNum], nodes, edges },
      },
    })),

  reset: () => set({ name: '', floors: 0, floorData: {} }),

  // ── Helpers ────────────────────────────────────────────────
  allFloorsUploaded: () => {
    const { floors, floorData } = get();
    if (!floors) return false;
    for (let i = 1; i <= floors; i++) {
      if (!floorData[i]?.image) return false;
    }
    return true;
  },

  allFloorsGridded: () => {
    const { floors, floorData } = get();
    if (!floors) return false;
    for (let i = 1; i <= floors; i++) {
      if (!floorData[i]?.nodes?.length) return false;
    }
    return true;
  },
}));

export default useBuildingStore;
