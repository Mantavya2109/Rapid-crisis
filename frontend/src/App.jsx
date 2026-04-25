import { BrowserRouter, Route, Routes } from "react-router-dom";
import AdminPage from "./pages/Admin";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AdminPage />} />
      </Routes>
    </BrowserRouter>
  );
}
