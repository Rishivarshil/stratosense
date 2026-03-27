import { useEffect, useRef, useState } from 'react';

const LIVE_POLL_MS = 5000;

const RISK_COLORS = {
  low:      { bg: '#1a3a1a', text: '#5ecf9a' },
  moderate: { bg: '#3a2e00', text: '#ffe066' },
  high:     { bg: '#3a1a00', text: '#ff8844' },
  extreme:  { bg: '#3a0a0a', text: '#ff4444' },
};

const MODE_COLORS = {
  assimilated:           { bg: '#1a3a2a', text: '#5dcaa5' },
  'surface-assimilated': { bg: '#1a2a3a', text: '#7eb3e8' },
  interpolated:          { bg: '#2a2a1a', text: '#ef9f27' },
};

const CONF_COLORS = {
  high:           '#5dcaa5',
  medium:         '#7eb3e8',
  'surface-only': '#ef9f27',
  baseline:       '#666',
};

function capeColor(v) {
  if (v == null) return null;
  if (v < 300)  return 'low';
  if (v < 1000) return 'moderate';
  if (v < 2500) return 'high';
  return 'extreme';
}

function lapseLabel(v) {
  if (v == null) return null;
  if (v < 6.5)  return 'Stable';
  if (v <= 9.8) return 'Conditional';
  return 'Unstable';
}

function precipLabel(v) {
  if (v == null) return null;
  if (v > 40) return 'Very High';
  if (v > 25) return 'Moderate';
  return null;
}

function riskKey(str) {
  if (!str) return null;
  const k = str.split(/\s*—\s*/)[0].toLowerCase().trim();
  return k in RISK_COLORS ? k : null;
}

function getBadge(key) {
  if (!key) return null;
  if (typeof key === 'object') return key;
  return RISK_COLORS[key] ?? null;
}

function fmt(val, unit) {
  if (val == null) return '—';
  const v = typeof val === 'number' ? (Number.isInteger(val) ? val : val.toFixed(1)) : val;
  return unit ? `${v} ${unit}` : String(v);
}

const PROFILE_ALTS = [300, 1000, 2000, 3000, 5000, 7000, 10000, 15000, 20000, 25000];

function sampleProfile(profile) {
  if (!profile?.length) return [];
  return PROFILE_ALTS.map(target =>
    profile.reduce((best, p) =>
      Math.abs(p.altitude_m - target) < Math.abs(best.altitude_m - target) ? p : best
    )
  );
}

export default function ScoreCard({ source = 'balloon', serial = null, id = null, stationTimeIndex = 0 }) {
  const selectedId = id ?? serial;
  const isStation = source === 'station';
  const [analysis,   setAnalysis]   = useState(null);
  const [forecast,   setForecast]   = useState(null);
  const [telemetry,  setTelemetry]  = useState(null);
  const [atmStatus,  setAtmStatus]  = useState(null);
  const [atmProfile, setAtmProfile] = useState(null);
  const [status,     setStatus]     = useState('idle');
  const [error,      setError]      = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    if (!selectedId) {
      setAnalysis(null); setForecast(null); setTelemetry(null);
      setAtmStatus(null); setAtmProfile(null); setStatus('idle');
      return;
    }
    let cancelled = false;

    async function fetchAll() {
      try {
        const [aRes, fRes, tRes, sRes, pRes] = await Promise.all([
          fetch(
            isStation
              ? `/station/${selectedId}/analysis?time_index=${stationTimeIndex}`
              : `/balloon/${selectedId}/analysis`
          ),
          isStation ? Promise.resolve({ ok: false }) : fetch(`/balloon/${selectedId}/forecast`),
          isStation ? Promise.resolve({ ok: false }) : fetch(`/balloon/${selectedId}/telemetry`),
          isStation ? Promise.resolve({ ok: false }) : fetch(`/atmosphere/status`),
          isStation
            ? fetch(`/station/${selectedId}/profile?time_index=${stationTimeIndex}`)
            : fetch(`/atmosphere/profile`),
        ]);
        if (cancelled) return;
        if (!aRes.ok) throw new Error(`Analysis ${aRes.status}`);
        const [aData, fData, tData, sData, pData] = await Promise.all([
          aRes.json(),
          fRes.ok ? fRes.json() : null,
          tRes.ok ? tRes.json() : null,
          sRes.ok ? sRes.json() : null,
          pRes.ok ? pRes.json() : null,
        ]);
        if (!cancelled) {
          setAnalysis(aData); setForecast(fData); setTelemetry(tData);
          setAtmStatus(sData);
          if (isStation && pData?.path) {
            const converted = pData.path.map((row) => ({
              altitude_m: row.alt,
              temp_c: row.temp,
              pressure_hpa: row.pressure_hpa,
              humidity_pct: row.humidity,
              wind: {
                speed_ms: row.wind_speed_ms,
                direction_deg: row.wind_dir_deg,
              },
              source: row.source || 'interpolated',
            }));
            setAtmProfile({ profile: converted });
          } else {
            setAtmProfile(pData);
          }
          setStatus(isStation ? 'done' : 'live');
        }
      } catch (err) {
        if (!cancelled) { setError(err.message); setStatus('error'); }
      }
    }

    setStatus('loading'); setError(null);
    clearInterval(pollRef.current);
    fetchAll().then(() => {
      if (!cancelled && !isStation)
        pollRef.current = setInterval(() => { if (!cancelled) fetchAll(); }, LIVE_POLL_MS);
    });
    return () => { cancelled = true; clearInterval(pollRef.current); };
  }, [selectedId, isStation, stationTimeIndex]);

  const a = analysis;
  const rk = riskKey(a?.storm_risk);
  const profileRows = sampleProfile(atmProfile?.profile);
  const modeColors = MODE_COLORS[atmStatus?.mode] ?? { bg: '#1e1e2e', text: '#666' };
  const confPct = { high: 90, medium: 60, 'surface-only': 35, baseline: 15 }[atmStatus?.confidence] ?? 15;
  const confColor = CONF_COLORS[atmStatus?.confidence] ?? '#666';

  return (
    <div className="scorecard-container">
      <div className="chart-header">
        <h2>Instability Score Card</h2>
        {selectedId && <span className="serial-tag">{selectedId}{isStation ? ' (station)' : ''}</span>}
        {status === 'live' && <span className="live-badge">Live</span>}
      </div>

      {status === 'loading' && (
        <div className="chart-overlay"><div className="spinner" /><p>Fetching analysis…</p></div>
      )}
      {status === 'error' && (
        <div className="chart-overlay error"><p>Error: {error}</p></div>
      )}
      {status === 'idle' && (
        <div className="chart-overlay"><p>Select a balloon or station to view instability metrics</p></div>
      )}

      {a && (
        <>
          {/* ── Instability grid ──────────────────────────────────── */}
          <div className="metric-grid">
            <MetricTile label="CAPE" value={fmt(a.cape, 'J/kg')}
              badgeBg={getBadge(capeColor(a.cape))?.bg} badgeFg={getBadge(capeColor(a.cape))?.text}
              badgeText={rk ? rk[0].toUpperCase() + rk.slice(1) : null} />

            <MetricTile label="CIN" value={fmt(a.cin, 'J/kg')}
              badgeBg="#1e1e2e" badgeFg="#666" badgeText="Info" />

            <MetricTile label="Lapse Rate" value={fmt(a.lapse_rate_c_per_km, '°C/km')}
              badgeBg={a.lapse_rate_c_per_km > 9.8 ? RISK_COLORS.extreme.bg : '#1a2a3a'}
              badgeFg={a.lapse_rate_c_per_km > 9.8 ? RISK_COLORS.extreme.text : '#7eb3e8'}
              badgeText={lapseLabel(a.lapse_rate_c_per_km)} />

            <MetricTile label="Tropopause" value={fmt(a.tropopause_alt_km, 'km')} />

            <MetricTile label="Precip Water" value={fmt(a.precipitable_water_mm, 'mm')}
              badgeBg={a.precipitable_water_mm > 25 ? RISK_COLORS[a.precipitable_water_mm > 40 ? 'high' : 'moderate'].bg : null}
              badgeFg={a.precipitable_water_mm > 25 ? RISK_COLORS[a.precipitable_water_mm > 40 ? 'high' : 'moderate'].text : null}
              badgeText={precipLabel(a.precipitable_water_mm)} />

            <MetricTile label="Storm Risk" value={a.storm_risk || '—'} small
              badgeBg={rk ? RISK_COLORS[rk].bg : null} badgeFg={rk ? RISK_COLORS[rk].text : null}
              badgeText={rk ? rk[0].toUpperCase() + rk.slice(1) : null} />

            <MetricTile label="Surface Temp" value={fmt(a.surface_temp, '°C')} />

            <MetricTile label="Max Altitude"
              value={a.max_alt != null ? `${(a.max_alt / 1000).toFixed(1)} km` : '—'} />

            {/* Radio frequency from telemetry */}
            <MetricTile label="Frequency"
              value={telemetry?.frequency_mhz != null ? `${telemetry.frequency_mhz} MHz` : '—'}
              badgeBg="#1a1a3a" badgeFg="#cc88ff" badgeText={telemetry?.frequency_mhz ? 'RF' : null} />

            <MetricTile label="Sonde Type"
              value={telemetry?.sonde_type ?? a.sonde_type ?? '—'} small />
          </div>

          {/* ── Data Assimilation Status ──────────────────────────── */}
          {atmStatus && !isStation && (
            <div className="sc-section">
              <div className="sc-section-title">
                Data Assimilation
                <span className="sc-mode-badge" style={{ background: modeColors.bg, color: modeColors.text }}>
                  {atmStatus.mode ?? 'unknown'}
                </span>
              </div>
              <div className="sc-assim-grid">
                <div className="sc-assim-item sc-assim-wide">
                  <span className="sc-assim-key">Confidence</span>
                  <div className="sc-conf-track">
                    <div className="sc-conf-fill" style={{ width: `${confPct}%`, background: confColor }} />
                  </div>
                  <span className="sc-assim-val" style={{ color: confColor }}>{atmStatus.confidence ?? '—'}</span>
                </div>
                <div className="sc-assim-item">
                  <span className="sc-assim-key">ELR</span>
                  <span className="sc-assim-val">
                    {atmStatus.lapse_rate_c_per_km != null ? `${atmStatus.lapse_rate_c_per_km.toFixed(2)} °C/km` : '—'}
                  </span>
                </div>
                <div className="sc-assim-item">
                  <span className="sc-assim-key">ELR Source</span>
                  <span className="sc-assim-val">{atmStatus.lapse_rate_source ?? '—'}</span>
                </div>
                <div className="sc-assim-item">
                  <span className="sc-assim-key">Balloon Age</span>
                  <span className="sc-assim-val">
                    {atmStatus.balloon_age_hours != null ? `${atmStatus.balloon_age_hours.toFixed(1)} h` : '—'}
                  </span>
                </div>
                <div className="sc-assim-item">
                  <span className="sc-assim-key">Surface Obs</span>
                  <span className="sc-assim-val">{atmStatus.surface_obs_count ?? '—'}</span>
                </div>
                <div className="sc-assim-item">
                  <span className="sc-assim-key">Station</span>
                  <span className="sc-assim-val">{atmStatus.surface_station ?? '—'}</span>
                </div>
              </div>
            </div>
          )}

          {/* ── Vertical Interpolation Profile ───────────────────── */}
          {profileRows.length > 0 && (
            <div className="sc-section">
              <div className="sc-section-title">Vertical Interpolation Profile</div>
              <div className="sc-profile-scroll">
                <table className="sc-profile-table">
                  <thead>
                    <tr><th>Alt</th><th>Temp</th><th>Press</th><th>RH</th><th>Wind</th><th>Src</th></tr>
                  </thead>
                  <tbody>
                    {profileRows.map(row => (
                      <tr key={row.altitude_m} className={row.source === 'assimilated' ? 'sc-row-assim' : ''}>
                        <td>{(row.altitude_m / 1000).toFixed(1)} km</td>
                        <td style={{ color: row.temp_c < -30 ? '#7eb3e8' : row.temp_c > 10 ? '#ff8844' : '#ccc' }}>
                          {row.temp_c?.toFixed(1)}°C
                        </td>
                        <td>{row.pressure_hpa?.toFixed(0)} hPa</td>
                        <td>{row.humidity_pct?.toFixed(0)}%</td>
                        <td>{row.wind?.speed_ms?.toFixed(1)} m/s</td>
                        <td className={row.source === 'assimilated' ? 'sc-src-assim' : 'sc-src-interp'}>
                          {row.source === 'assimilated' ? '✓' : '~'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── Forecast ─────────────────────────────────────────── */}
          {forecast?.details?.length > 0 && !isStation && (
            <div className="forecast-section">
              <h3 className="forecast-heading">Forecast</h3>
              <ul className="forecast-list">
                {forecast.details.map((d, i) => <li key={i}>{d}</li>)}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function MetricTile({ label, value, badgeBg, badgeFg, badgeText, small }) {
  return (
    <div className="metric-tile">
      <span className="metric-label">{label}</span>
      <span className="metric-value" style={small ? { fontSize: '0.78rem' } : undefined}>{value}</span>
      {badgeText && badgeBg && (
        <span className="metric-badge" style={{ background: badgeBg, color: badgeFg }}>{badgeText}</span>
      )}
    </div>
  );
}
