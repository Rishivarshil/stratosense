import { useState, useEffect } from 'react';
import SoundingChart from './components/SoundingChart';
import WindBarbs from './components/WindBarbs';
import ScoreCard from './components/ScoreCard';
import './App.css';

export default function App() {
  const [serialInput, setSerialInput] = useState('');
  const [activeSerial, setActiveSerial] = useState(null);
  const [balloons, setBalloons] = useState([]);
  const [loadingBalloons, setLoadingBalloons] = useState(false);
  const [serverStatus, setServerStatus] = useState(null);

  useEffect(() => {
    fetch('/status')
      .then((r) => r.json())
      .then(setServerStatus)
      .catch(() => setServerStatus({ status: 'offline' }));
  }, []);

  useEffect(() => {
    function onBalloonSelected(e) {
      const serial = e.detail?.serial;
      if (serial) {
        setSerialInput(serial);
        setActiveSerial(serial);
      }
    }
    document.addEventListener('balloonSelected', onBalloonSelected);
    return () => document.removeEventListener('balloonSelected', onBalloonSelected);
  }, []);

  async function fetchActiveBalloons() {
    setLoadingBalloons(true);
    try {
      const res = await fetch('/balloons');
      const data = await res.json();
      setBalloons(data.balloons || []);
    } catch {
      setBalloons([]);
    }
    setLoadingBalloons(false);
  }

  function handleLoad(e) {
    e.preventDefault();
    const trimmed = serialInput.trim();
    if (trimmed) setActiveSerial(trimmed);
  }

  function handleSelectBalloon(serial) {
    setSerialInput(serial);
    setActiveSerial(serial);
  }

  const online = serverStatus?.status === 'running';

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <h1>StratoSense</h1>
          <span className="subtitle">Atmospheric Analysis</span>
        </div>
        <div className={`status-dot ${online ? 'online' : 'offline'}`}>
          {online
            ? `Pipeline online — ${serverStatus.active_balloons} active`
            : 'Pipeline offline — start data_pipeline.py on port 8080'}
        </div>
      </header>

      <div className="controls">
        <form onSubmit={handleLoad} className="serial-form">
          <input
            type="text"
            value={serialInput}
            onChange={(e) => setSerialInput(e.target.value)}
            placeholder="Enter balloon serial (e.g. T1234567)"
            className="serial-input"
          />
          <button type="submit" className="btn btn-primary" disabled={!serialInput.trim()}>
            Load
          </button>
        </form>

        <div className="divider-text">or</div>

        <button
          className="btn btn-secondary"
          onClick={fetchActiveBalloons}
          disabled={loadingBalloons}
        >
          {loadingBalloons ? 'Fetching...' : 'Fetch Active Balloons'}
        </button>

        {balloons.length > 0 && (
          <div className="balloon-list">
            <span className="balloon-list-label">{balloons.length} active:</span>
            <div className="balloon-chips">
              {balloons.slice(0, 30).map((b) => (
                <button
                  key={b.serial}
                  className={`chip ${b.serial === activeSerial ? 'active' : ''}`}
                  onClick={() => handleSelectBalloon(b.serial)}
                  title={`Alt: ${b.alt ? Math.round(b.alt) + 'm' : '?'} | Temp: ${b.temp ?? '?'}°C | Frames: ${b.frame_count}`}
                >
                  {b.serial}
                </button>
              ))}
              {balloons.length > 30 && (
                <span className="chip-overflow">+{balloons.length - 30} more</span>
              )}
            </div>
          </div>
        )}
      </div>

      <main className="chart-area">
        <SoundingChart serial={activeSerial} />
        <WindBarbs serial={activeSerial} />
        <ScoreCard serial={activeSerial} />
      </main>
    </div>
  );
}
