import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import Globe from '../components/Globe';
import AltitudeColumn from '../components/AltitudeColumn';
import FlightScrubber from '../components/FlightScrubber';
import StationScrubber from '../components/StationScrubber';
import SoundingChart from '../components/SoundingChart';
import WindBarbs from '../components/WindBarbs';
import ScoreCard from '../components/ScoreCard';
import '../styles/dashboard.css';

const TABS = [
  { id: '3d', label: '3D Profile', icon: '◈' },
  { id: 'sounding', label: 'Sounding', icon: '〰' },
  { id: 'wind', label: 'Wind', icon: '⊹' },
  { id: 'score', label: 'Score Card', icon: '◎' },
];

export default function DashboardPage() {
  const [serialInput, setSerialInput] = useState('');
  const [activeSource, setActiveSource] = useState('balloon');
  const [activeId, setActiveId] = useState(null);
  const [serverStatus, setServerStatus] = useState(null);
  const [activeTab, setActiveTab] = useState('3d');
  const [flightFrames, setFlightFrames] = useState(null);
  const [scrubIndex, setScrubIndex] = useState(0);
  const [analysis, setAnalysis] = useState(null);
  const [stationHybrid, setStationHybrid] = useState(null);
  const [stationTimeIndex, setStationTimeIndex] = useState(0);
  const [stationHeightIndex, setStationHeightIndex] = useState(0);
  const activeSerial = activeSource === 'balloon' ? activeId : null;

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
        setActiveSource('balloon');
        setActiveId(serial);
      }
    }
    function onStationSelected(e) {
      const stid = e.detail?.stid;
      if (stid) {
        setSerialInput(stid);
        setActiveSource('station');
        setActiveId(stid);
      }
    }
    document.addEventListener('balloonSelected', onBalloonSelected);
    document.addEventListener('stationSelected', onStationSelected);
    return () => {
      document.removeEventListener('balloonSelected', onBalloonSelected);
      document.removeEventListener('stationSelected', onStationSelected);
    };
  }, []);

  const loadFlightData = useCallback(async (serial) => {
    setFlightFrames(null);
    setAnalysis(null);
    setScrubIndex(0);
    if (!serial) return;
    try {
      const [pathRes, analysisRes] = await Promise.all([
        fetch(`/balloon/${serial}`),
        fetch(`/balloon/${serial}/analysis`),
      ]);
      if (pathRes.ok) {
        const d = await pathRes.json();
        const frames = (d.path || []).filter((f) => f.lat != null && f.lon != null);
        setFlightFrames(frames);
        setScrubIndex(Math.max(0, frames.length - 1));
      }
      if (analysisRes.ok) {
        setAnalysis(await analysisRes.json());
      }
    } catch {
      // Charts handle their own error state.
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      if (!cancelled) await loadFlightData(activeSerial);
    }
    run();
    return () => {
      cancelled = true;
    };
  }, [activeSerial, loadFlightData]);

  useEffect(() => {
    let cancelled = false;
    async function loadStationHybrid(stid) {
      if (!stid) {
        setStationHybrid(null);
        setStationTimeIndex(0);
        setStationHeightIndex(0);
        return;
      }
      try {
        const res = await fetch(`/station/${stid}/hybrid`);
        if (!res.ok) throw new Error(`Station hybrid fetch failed: ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        setStationHybrid(data);
        const latest = Math.max(0, (data.snapshots?.length ?? 1) - 1);
        setStationTimeIndex(latest);
        setStationHeightIndex(0);
      } catch {
        if (!cancelled) {
          setStationHybrid(null);
          setStationTimeIndex(0);
          setStationHeightIndex(0);
        }
      }
    }

    if (activeSource === 'station' && activeId) {
      loadStationHybrid(activeId);
    } else {
      setStationHybrid(null);
      setStationTimeIndex(0);
      setStationHeightIndex(0);
    }
    return () => {
      cancelled = true;
    };
  }, [activeSource, activeId]);

  useEffect(() => {
    const levels = stationHybrid?.snapshots?.[stationTimeIndex]?.levels ?? [];
    if (stationHeightIndex > Math.max(0, levels.length - 1)) {
      setStationHeightIndex(Math.max(0, levels.length - 1));
    }
  }, [stationHybrid, stationTimeIndex, stationHeightIndex]);

  function handleLoad(e) {
    e.preventDefault();
    const trimmed = serialInput.trim();
    if (trimmed) {
      setActiveSource('balloon');
      setActiveId(trimmed);
    }
  }

  const online = serverStatus?.status === 'running';
  const scrubFrame = flightFrames?.[scrubIndex] ?? null;
  const stationSnapshot = stationHybrid?.snapshots?.[stationTimeIndex] ?? null;
  const stationFrames3d = (stationSnapshot?.levels || []).map((lv) => ({
    lat: lv.lat,
    lon: lv.lon,
    alt: lv.alt,
    temp: lv.temp,
    humidity: lv.humidity,
    datetime: lv.datetime,
    vel_v: null,
  }));

  return (
    <div className="app dashboard-page">
      <header className="app-header">
        <div className="brand">
          <div className="brand-row">
            <Link className="dashboard-wordmark" to="/" aria-label="Go to landing page">
              <span className="dashboard-nav-cloud-outline" aria-hidden="true">
                <span className="dashboard-nav-cloud-bump dashboard-nav-cloud-bump-left" />
                <span className="dashboard-nav-cloud-bump dashboard-nav-cloud-bump-center" />
                <span className="dashboard-nav-cloud-bump dashboard-nav-cloud-bump-right" />
                <span className="dashboard-nav-cloud-base" />
              </span>
              <span className="dashboard-wordmark-text">StratoSense</span>
            </Link>
            <span className="subtitle">Atmospheric Analysis</span>
          </div>
        </div>

        <div className="dashboard-status">
          <span className="dashboard-status-text">
            {online ? 'Pipeline online' : 'Pipeline offline'}
          </span>
          <span
            className={`dashboard-status-pill ${online ? 'online' : 'offline'}`}
            aria-hidden="true"
          />
        </div>
      </header>

      <div className="app-body">
        <div className="left-panel">
          {activeSource === 'balloon' ? (
            <>
              <Globe selectedSerial={activeSerial} scrubFrame={scrubFrame} />
              <FlightScrubber
                frames={flightFrames}
                scrubIndex={scrubIndex}
                onChange={setScrubIndex}
              />
            </>
          ) : (
            <>
              <Globe selectedSerial={null} scrubFrame={null} />
              <StationScrubber
                stationId={activeId}
                snapshots={stationHybrid?.snapshots || []}
                timeIndex={stationTimeIndex}
                onTimeChange={setStationTimeIndex}
                heightIndex={stationHeightIndex}
                onHeightChange={setStationHeightIndex}
              />
            </>
          )}
        </div>

        <div className="right-panel">
          <div className="controls">
            <form onSubmit={handleLoad} className="serial-form">
              <input
                type="text"
                value={serialInput}
                onChange={(e) => setSerialInput(e.target.value)}
                placeholder="Serial (e.g. T1234567) — or click globe"
                className="serial-input"
              />
              <button
                type="submit"
                className="btn btn-primary"
                disabled={!serialInput.trim()}
              >
                Load
              </button>
            </form>
            {activeId && (
              <span className="active-serial-display">
                <strong>{activeSource === 'station' ? `${activeId} (station)` : activeSerial}</strong>
              </span>
            )}
          </div>

          <div className="tab-bar">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                className={`tab-btn${activeTab === tab.id ? ' active' : ''}`}
                onClick={() => setActiveTab(tab.id)}
              >
                <span className="tab-icon">{tab.icon}</span>
                <span className="tab-label">{tab.label}</span>
              </button>
            ))}
          </div>

          <div className="tab-panels">
            <div
              className="tab-panel tab-panel-3d"
              style={{ display: activeTab === '3d' ? 'flex' : 'none' }}
            >
              <AltitudeColumn
                frames={activeSource === 'station' ? stationFrames3d : flightFrames}
                scrubIndex={activeSource === 'station' ? stationHeightIndex : scrubIndex}
                analysis={activeSource === 'station' ? stationSnapshot?.analysis ?? null : analysis}
                serial={activeSource === 'station' ? activeId : activeSerial}
              />
            </div>

            <div
              className="tab-panel"
              style={{ display: activeTab === 'sounding' ? 'block' : 'none' }}
            >
              <SoundingChart
                source={activeSource}
                id={activeId}
                stationTimeIndex={stationTimeIndex}
                stationHeightIndex={stationHeightIndex}
              />
            </div>

            <div
              className="tab-panel"
              style={{ display: activeTab === 'wind' ? 'block' : 'none' }}
            >
              <WindBarbs
                source={activeSource}
                id={activeId}
                serial={activeSerial}
                stationTimeIndex={stationTimeIndex}
              />
            </div>

            <div
              className="tab-panel"
              style={{ display: activeTab === 'score' ? 'block' : 'none' }}
            >
              <ScoreCard
                source={activeSource}
                id={activeId}
                serial={activeSerial}
                stationTimeIndex={stationTimeIndex}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
