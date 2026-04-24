/**
 * App.jsx
 * Root router for the Rapid Crisis Admin Panel.
 */
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import TopBar from './components/TopBar';
import HomePage         from './pages/HomePage';
import BuildingListPage from './pages/BuildingListPage';
import FloorUploadPage  from './pages/FloorUploadPage';
import GridEditorPage   from './pages/GridEditorPage';
import ReviewSubmitPage from './pages/ReviewSubmitPage';
import BuildingViewPage from './pages/BuildingViewPage';
import './App.css';

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <TopBar />
        <main className="app-main">
          <Routes>
            <Route path="/"                      element={<HomePage />} />
            <Route path="/buildings"             element={<BuildingListPage />} />
            <Route path="/add-building/upload"   element={<FloorUploadPage />} />
            <Route path="/add-building/grid"     element={<GridEditorPage />} />
            <Route path="/add-building/review"   element={<ReviewSubmitPage />} />
            <Route path="/building/:buildingId"  element={<BuildingViewPage />} />
            {/* Fallback */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
