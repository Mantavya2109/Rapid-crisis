import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:3000";

const api = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
  headers: {
    "x-api-key": import.meta.env.VITE_API_KEY || "",
  },
});

export async function sendBuildingData(data) {
  const response = await api.post("/building/setup", data);
  return response.data;
}

export async function getBuildings() {
  const response = await api.get("/buildings");
  return response.data;
}

export async function getBuilding(id) {
  const response = await api.get(`/building/${id}`);
  return response.data;
}

export async function deleteBuilding(id) {
  const response = await api.delete(`/building/${id}`);
  return response.data;
}

export default api;
