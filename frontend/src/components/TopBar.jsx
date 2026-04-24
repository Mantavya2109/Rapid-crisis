/**
 * TopBar.jsx
 * Application-wide top navigation bar.
 */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { healthApi } from '../services/api';
import './TopBar.css';

export default function TopBar() {
  const navigate = useNavigate();
  const [status, setStatus] = useState('checking'); // 'ok' | 'alert' | 'checking'

  useEffect(() => {
    const check = async () => {
      try {
        await healthApi.check();
        setStatus('ok');
      } catch {
        setStatus('offline');
      }
    };
    check();
    const id = setInterval(check, 15000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <button className="topbar-logo" onClick={() => navigate('/')}>
          <span className="topbar-logo-icon">🔥</span>
          <span className="topbar-logo-text">RAPID CRISIS</span>
          <span className="topbar-logo-sub">EVACUATION SYSTEM</span>
        </button>

        <div className="topbar-right">
          <div className={`status-pill status-${status}`}>
            <span className="status-dot" />
            <span className="status-label">
              {status === 'ok' ? 'SYSTEM NOMINAL' : status === 'offline' ? 'BACKEND OFFLINE' : 'CHECKING…'}
            </span>
          </div>
        </div>
      </div>
    </header>
  );
}
