import { BrowserRouter, Route, Routes } from "react-router-dom";
import AdminPage from "./pages/Admin";
import Buildings from "./pages/Buildings";
import ErrorBoundary from "./components/ErrorBoundary";
import { useSSE } from "./hooks/useSSE";

function GlobalEventListener() {
  useSSE({
    onFire: (data) => {
      console.error("🔥 FIRE DETECTED!", data);
      alert(`🔥 FIRE DETECTED at ${data.startNodes.join(", ")} in ${data.buildingId}!`);
    },
    onAnomaly: (data) => console.warn("⚠ ANOMALY:", data),
    onTelemetry: (data) => console.log("📡 Telemetry:", data),
    onSystemMode: (data) => console.info("⚙️ System Mode:", data)
  });
  return null;
}

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <GlobalEventListener />
        <Routes>
          <Route path="/" element={<Buildings />} />
          <Route path="/editor" element={<AdminPage />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
