/**
 * AlertBanner.jsx
 * Fire alert top banner shown when a fire event is active.
 */
import './AlertBanner.css';

export default function AlertBanner({ alertData, onClear }) {
  if (!alertData) return null;
  return (
    <div className="alert-banner" role="alert" aria-live="assertive">
      <div className="alert-banner-inner">
        <div className="alert-banner-left">
          <span className="alert-fire-icon" aria-hidden="true">🔥</span>
          <div>
            <p className="alert-banner-title">⚠ FIRE DETECTED — Safe path highlighted</p>
            <p className="alert-banner-sub">
              Building: <strong>{alertData.buildingId}</strong>
              {alertData.startNodes?.length > 0 && (
                <> · Origin: <strong>{alertData.startNodes.join(', ')}</strong></>
              )}
              {alertData.blockedNodes?.length > 0 && (
                <> · Blocked: <strong>{alertData.blockedNodes.length} nodes</strong></>
              )}
            </p>
          </div>
        </div>
        {onClear && (
          <button className="alert-clear-btn" onClick={onClear}>
            ✓ CLEAR ALERT
          </button>
        )}
      </div>
    </div>
  );
}
