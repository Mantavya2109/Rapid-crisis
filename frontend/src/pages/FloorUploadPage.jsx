/**
 * FloorUploadPage.jsx — Page 2
 * Step 1 of 3: Upload floor architecture images per floor.
 */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import useBuildingStore from '../store/buildingStore';
import StepIndicator from '../components/StepIndicator';
import './FloorUploadPage.css';

export default function FloorUploadPage() {
  const navigate    = useNavigate();
  const { name, floors, floorData, setFloorImage, allFloorsUploaded } = useBuildingStore();
  const [activeFloor, setActiveFloor] = useState(1);
  const [dragging,    setDragging]    = useState(false);

  // Guard redirect via effect (avoids navigate-during-render)
  useEffect(() => {
    if (!name || !floors) navigate('/');
  }, [name, floors]);

  // Guard: render nothing while redirecting
  if (!name || !floors) return null;

  const currentFloorData = floorData[activeFloor] || {};

  const processFile = (file) => {
    if (!file || !file.type.startsWith('image/')) return;
    const url = URL.createObjectURL(file);
    setFloorImage(activeFloor, file, url);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    processFile(e.dataTransfer.files[0]);
  };

  const handleFileInput = (e) => processFile(e.target.files[0]);

  const floorTabs = Array.from({ length: floors }, (_, i) => i + 1);
  const allUploaded = allFloorsUploaded();

  return (
    <div className="page-layout">
      {/* Page Header */}
      <div className="page-header">
        <div>
          <p className="page-breadcrumb">New Building / <strong>{name}</strong></p>
          <h1 className="page-title">Floor Architecture Upload</h1>
        </div>
        <StepIndicator current={1} />
      </div>

      {/* Floor Tabs */}
      <div className="floor-tabs">
        {floorTabs.map((f) => {
          const uploaded = !!floorData[f]?.image;
          return (
            <button
              key={f}
              className={`floor-tab ${activeFloor === f ? 'floor-tab-active' : ''} ${uploaded ? 'floor-tab-done' : ''}`}
              onClick={() => setActiveFloor(f)}
            >
              <span className="floor-tab-num">Floor {f}</span>
              <span className={`floor-tab-status ${uploaded ? 'status-ok' : 'status-pending'}`}>
                {uploaded ? '✓' : '⚠'}
              </span>
            </button>
          );
        })}
      </div>

      {/* Upload Zone */}
      <div className="upload-zone-wrap">
        {currentFloorData.imageUrl ? (
          <div className="upload-preview">
            <img
              src={currentFloorData.imageUrl}
              alt={`Floor ${activeFloor} plan`}
              className="upload-preview-img"
            />
            <div className="upload-preview-overlay">
              <p className="upload-preview-name mono">
                {currentFloorData.image?.name || 'Floor plan uploaded'}
              </p>
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => setFloorImage(activeFloor, null, null)}
              >
                Replace Image
              </button>
            </div>
            <div className="upload-preview-badge">
              <span className="badge badge-green">✓ UPLOADED</span>
            </div>
          </div>
        ) : (
          <label
            className={`drop-zone ${dragging ? 'drop-zone-dragging' : ''}`}
            htmlFor={`floor-upload-${activeFloor}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
          >
            <div className="drop-zone-icon">🏠</div>
            <p className="drop-zone-title">
              Drop Floor {activeFloor} Architecture Image Here
            </p>
            <p className="drop-zone-sub">or click to browse — PNG, JPG, SVG accepted</p>
            <span className="btn btn-secondary btn-sm" style={{ marginTop: 12, pointerEvents: 'none' }}>
              Browse Files
            </span>
            <input
              id={`floor-upload-${activeFloor}`}
              type="file"
              accept="image/*"
              className="drop-zone-input"
              onChange={handleFileInput}
            />
          </label>
        )}
      </div>

      {/* Progress Summary */}
      <div className="upload-progress-bar">
        {floorTabs.map((f) => (
          <div
            key={f}
            className={`upload-progress-seg ${floorData[f]?.image ? 'seg-done' : 'seg-pending'}`}
            title={`Floor ${f}: ${floorData[f]?.image ? 'Uploaded' : 'Pending'}`}
          />
        ))}
      </div>
      <p className="upload-progress-label">
        {floorTabs.filter((f) => floorData[f]?.image).length} / {floors} floors uploaded
      </p>

      {/* Bottom Nav */}
      <div className="page-bottom-bar">
        <button className="btn btn-secondary" onClick={() => navigate('/')}>
          ← Back
        </button>
        <button
          className="btn btn-primary"
          disabled={!allUploaded}
          onClick={() => navigate('/add-building/grid')}
          title={!allUploaded ? 'Upload images for all floors first' : ''}
        >
          Next: Grid Editor →
        </button>
      </div>
    </div>
  );
}
