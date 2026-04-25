import axios from "axios";

const api = axios.create({
  baseURL: "http://localhost:5000",
});

export async function sendBuildingData(data) {
  const response = await api.post("/building/setup", data);
  return response;
}

export default api;
