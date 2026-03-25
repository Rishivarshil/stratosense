import { useEffect, useRef, useState } from 'react';

const LIVE_POLL_MS = 2000;

const RISK_COLORS = {
  low:      { bg: '#d4edda', text: '#155724' },
  moderate: { bg: '#fff3cd', text: '#856404' },
  high:     { bg: '#ffeeba', text: '#c8540a' },
  extreme:  { bg: '#f8d7da', text: '#721c24' },
};

function capeColor(v) {
  if (v == null) return null;
  if (v < 300) return 'low';
  if (v < 1000) return 'moderate';
  if (v < 2500) return 'high';
  return 'extreme';
}

function lapseColor(v) {
  if (v == null) return null;
  if (v < 6.5) return { bg: '#d6eaf8', text: '#1a5276' };
  if (v <= 9.8) return 'moderate';
  return 'extreme';
}

function precipColor(v) {
  if (v == null) return null;
  if (v > 40) return 'high';
  if (v > 25) return 'moderate';
  return null;
}

function riskPrefix(str) {
  if (!str) return null;
  const prefix = str.split(/\s*—\s*/)[0].toLowerCase().trim();
  if (prefix in RISK_COLORS) return prefix;
  return null;
}

function getBadge(colorKey) {
  if (!colorKey) return null;
  if (typeof colorKey === 'object') return colorKey;
  return RISK_COLORS[colorKey] || null;
}

function formatValue(val, unit) {
  if (val == null) return '—';
  const rounded = typeof val === 'number' ? (Number.isInteger(val) ? val : val.toFixed(1)) : val;
  return `${rounded} ${unit}`;
}

const TILES = [
  {
    label: 'CAPE',
    field: 'cape',
    unit: 'J/kg',
    color: (a) => capeColor(a.cape),
    badgeLabel: (a) => { const p = riskPrefix(a.storm_risk); return p ? p.charAt(0).toUpperCase() + p.slice(1) : null; },
  },
  {
    label: 'CIN',
    field: 'cin',
    unit: 'J/kg',
    color: () => ({ bg: '#eee', text: '#555' }),
    badgeLabel: () => 'Info',
  },
  {
    label: 'Lapse Rate',
    field: 'lapse_rate_c_per_km',
    unit: '°C/km',
    color: (a) => lapseColor(a.lapse_rate_c_per_km),
    badgeLabel: (a) => {
      const v = a.lapse_rate_c_per_km;
      if (v == null) return null;
      if (v < 6.5) return 'Stable';
      if (v <= 9.8) return 'Conditional';
      return 'Unstable';
    },
  },
  {
    label: 'Tropopause',
    field: 'tropopause_alt_km',
    unit: 'km',
    color: () => null,
    badgeLabel: () => null,
  },
  {
    label: 'Precip Water',
    field: 'precipitable_water_mm',
    unit: 'mm',
    color: (a) => precipColor(a.precipitable_water_mm),
    badgeLabel: (a) => {
      const v = a.precipitable_water_mm;
      if (v == null) return null;
      if (v > 40) return 'Very High';
      if (v > 25) return 'Moderate';
      return null;
    },
  },
  {
    label: 'Storm Risk',
    field: 'storm_risk',
    unit: '',
    value: (a) => a.storm_risk || '—',
    color: (a) => riskPrefix(a.storm_risk),
    badgeLabel: (a) => { const p = riskPrefix(a.storm_risk); return p ? p.charAt(0).toUpperCase() + p.slice(1) : null; },
  },
  {
    label: 'Surface Temp',
    field: 'surface_temp',
    unit: '°C',
    color: () => null,
    badgeLabel: () => null,
  },
  {
    label: 'Max Altitude',
    field: 'max_alt',
    unit: 'km',
    transform: (v) => v != null ? Math.round(v / 1000 * 10) / 10 : null,
    color: () => null,
    badgeLabel: () => null,
  },
];

export default function ScoreCard({ serial }) {
  const [analysis, setAnalysis] = useState(null);
  const [forecast, setForecast] = useState(null);
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    if (!serial) {
      setAnalysis(null);
      setForecast(null);
      setStatus('idle');
      return;
    }

    let cancelled = false;

    async function fetchData() {
      try {
        const [aRes, fRes] = await Promise.all([
          fetch(`/balloon/${serial}/analysis`),
          fetch(`/balloon/${serial}/forecast`),
        ]);

        if (!aRes.ok) throw new Error(`Analysis fetch failed: ${aRes.status}`);
        if (cancelled) return;

        const aData = await aRes.json();
        const fData = fRes.ok ? await fRes.json() : null;

        if (!cancelled) {
          setAnalysis(aData);
          setForecast(fData);
          setStatus('live');
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message);
          setStatus('error');
        }
      }
    }

    setStatus('loading');
    setError(null);
    setAnalysis(null);
    setForecast(null);

    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }

    fetchData().then(() => {
      if (!cancelled) {
        pollRef.current = setInterval(() => {
          if (!cancelled) fetchData();
        }, LIVE_POLL_MS);
      }
    });

    return () => {
      cancelled = true;
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [serial]);

  return (
    <div className="scorecard-container">
      <div className="chart-header">
        <h2>Instability Score Card</h2>
        {serial && <span className="serial-tag">{serial}</span>}
        {status === 'live' && <span className="live-badge">Live</span>}
      </div>

      {status === 'loading' && (
        <div className="chart-overlay">
          <div className="spinner" />
          <p>Fetching analysis...</p>
        </div>
      )}

      {status === 'error' && (
        <div className="chart-overlay error">
          <p>Error: {error}</p>
        </div>
      )}

      {status === 'idle' && !serial && (
        <div className="chart-overlay">
          <p>Select a balloon to view instability metrics</p>
        </div>
      )}

      {analysis && (
        <>
          <div className="metric-grid">
            {TILES.map((tile) => {
              let rawVal = analysis[tile.field];
              if (tile.transform) rawVal = tile.transform(rawVal);
              const display = tile.value ? tile.value(analysis) : formatValue(rawVal, tile.unit);
              const colorKey = tile.color(analysis);
              const badge = getBadge(colorKey);
              const badgeText = tile.badgeLabel(analysis);

              return (
                <div key={tile.field} className="metric-tile">
                  <span className="metric-label">{tile.label}</span>
                  <span className="metric-value">{display}</span>
                  {badge && badgeText && (
                    <span
                      className="metric-badge"
                      style={{ background: badge.bg, color: badge.text }}
                    >
                      {badgeText}
                    </span>
                  )}
                </div>
              );
            })}
          </div>

          {forecast && forecast.details && forecast.details.length > 0 && (
            <div className="forecast-section">
              <h3 className="forecast-heading">Forecast</h3>
              <ul className="forecast-list">
                {forecast.details.map((d, i) => (
                  <li key={i}>{d}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}
