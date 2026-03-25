import { useEffect, useRef, useState } from 'react';
import { groupIntoBands, degreesToCardinal } from '../utils/wind';

const LIVE_POLL_MS = 2000;

/**
 * Render a single meteorological wind barb as SVG elements.
 *
 * Convention: staff points in the direction the wind comes FROM.
 * Pennant = 50 kt, long barb = 10 kt, short barb = 5 kt.
 * Calm (< 2.5 kt) = circle.
 */
function WindBarbSVG({ speedKnots, directionDeg, size = 56 }) {
  const cx = size / 2;
  const cy = size / 2;
  const staffLen = size * 0.42;
  const barbLen = size * 0.22;
  const shortBarbLen = barbLen * 0.55;
  const pennantWidth = size * 0.18;
  const barbSpacing = size * 0.075;
  const strokeW = 1.8;

  if (speedKnots < 2.5) {
    return (
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle
          cx={cx} cy={cy} r={size * 0.14}
          fill="none" stroke="#444" strokeWidth={strokeW}
        />
      </svg>
    );
  }

  let remaining = Math.round(speedKnots / 5) * 5;
  const pennants = Math.floor(remaining / 50);
  remaining -= pennants * 50;
  const longBarbs = Math.floor(remaining / 10);
  remaining -= longBarbs * 10;
  const shortBarbs = remaining >= 5 ? 1 : 0;

  const elements = [];
  let offset = 0;

  for (let i = 0; i < pennants; i++) {
    const y0 = -staffLen + offset;
    const y1 = y0 + pennantWidth * 0.8;
    elements.push(
      <polygon
        key={`p${i}`}
        points={`0,${y0} ${barbLen},${y0 + (y1 - y0) / 2} 0,${y1}`}
        fill="#444" stroke="none"
      />
    );
    offset += pennantWidth * 0.8 + 1;
  }

  for (let i = 0; i < longBarbs; i++) {
    const y = -staffLen + offset + barbSpacing;
    elements.push(
      <line
        key={`l${i}`}
        x1={0} y1={y} x2={barbLen} y2={y - barbLen * 0.45}
        stroke="#444" strokeWidth={strokeW} strokeLinecap="round"
      />
    );
    offset += barbSpacing;
  }

  for (let i = 0; i < shortBarbs; i++) {
    const y = -staffLen + offset + barbSpacing;
    elements.push(
      <line
        key={`s${i}`}
        x1={0} y1={y} x2={shortBarbLen} y2={y - shortBarbLen * 0.45}
        stroke="#444" strokeWidth={strokeW} strokeLinecap="round"
      />
    );
    offset += barbSpacing;
  }

  const rotation = directionDeg;

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <g transform={`translate(${cx},${cy}) rotate(${rotation})`}>
        <line
          x1={0} y1={staffLen} x2={0} y2={-staffLen}
          stroke="#444" strokeWidth={strokeW} strokeLinecap="round"
        />
        {elements}
        <polygon
          points={`0,${staffLen} -3,${staffLen - 6} 3,${staffLen - 6}`}
          fill="#444"
        />
      </g>
    </svg>
  );
}

export default function WindBarbs({ serial }) {
  const [bands, setBands] = useState(null);
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    if (!serial) {
      setBands(null);
      setStatus('idle');
      return;
    }

    let cancelled = false;

    async function fetchAndGroup() {
      try {
        const res = await fetch(`/balloon/${serial}/analysis`);
        if (!res.ok) throw new Error(`Analysis fetch failed: ${res.status}`);
        if (cancelled) return;

        const data = await res.json();
        const profile = data.wind_profile || [];

        if (profile.length === 0) {
          throw new Error('No wind profile data available');
        }

        const grouped = groupIntoBands(profile);
        if (!cancelled) {
          setBands(grouped);
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
    setBands(null);

    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }

    fetchAndGroup().then(() => {
      if (!cancelled) {
        pollRef.current = setInterval(() => {
          if (!cancelled) fetchAndGroup();
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
    <div className="wind-barbs-container">
      <div className="chart-header">
        <h2>Wind Profile</h2>
        {serial && <span className="serial-tag">{serial}</span>}
        {status === 'live' && <span className="live-badge">Live</span>}
      </div>

      {status === 'loading' && (
        <div className="chart-overlay">
          <div className="spinner" />
          <p>Fetching wind data...</p>
        </div>
      )}

      {status === 'error' && (
        <div className="chart-overlay error">
          <p>Error: {error}</p>
        </div>
      )}

      {status === 'idle' && !serial && (
        <div className="chart-overlay">
          <p>Select a balloon to view its wind profile</p>
        </div>
      )}

      {bands && (
        <div className="wind-bands">
          {bands.map((band) => (
            <div
              key={band.label}
              className={`wind-band-row ${band.count === 0 ? 'no-data' : ''}`}
            >
              <div className="band-label">
                <span className="band-name">{band.label}</span>
                <span className="band-range">{band.altRange}</span>
              </div>

              <div className="band-barb">
                {band.count > 0 ? (
                  <WindBarbSVG
                    speedKnots={band.avgSpeedKnots}
                    directionDeg={band.avgDirectionDeg}
                  />
                ) : (
                  <span className="no-data-text">—</span>
                )}
              </div>

              <div className="band-readout">
                {band.count > 0 ? (
                  <>
                    <span className="readout-speed">
                      {Math.round(band.avgSpeedKnots)} kt
                    </span>
                    <span className="readout-dir">
                      from {degreesToCardinal(band.avgDirectionDeg)} ({band.avgDirectionDeg}°)
                    </span>
                  </>
                ) : (
                  <span className="no-data-text">No data</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
