function fmtTime(isoStr) {
  if (!isoStr) return '—';
  try {
    return new Date(isoStr).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return isoStr;
  }
}

export default function StationScrubber({
  stationId,
  snapshots,
  timeIndex,
  onTimeChange,
  heightIndex,
  onHeightChange,
}) {
  if (!stationId || !snapshots?.length) {
    return (
      <div className="flight-scrubber">
        <div className="scrubber-info-row">
          <span className="scrubber-current-time">Select a station to load profiles</span>
        </div>
      </div>
    );
  }

  const timeMax = snapshots.length - 1;
  const safeTimeIndex = Math.max(0, Math.min(timeIndex, timeMax));
  const currentSnapshot = snapshots[safeTimeIndex];
  const levels = currentSnapshot?.levels || [];
  const levelMax = Math.max(0, levels.length - 1);
  const safeHeightIndex = Math.max(0, Math.min(heightIndex, levelMax));
  const currentLevel = levels[safeHeightIndex];

  const timePct = timeMax > 0 ? (safeTimeIndex / timeMax) * 100 : 100;
  const heightPct = levelMax > 0 ? (safeHeightIndex / levelMax) * 100 : 100;

  return (
    <div className="flight-scrubber station-scrubber">
      <div className="scrubber-info-row">
        <span className="scrubber-current-time">{fmtTime(currentSnapshot?.datetime)}</span>
        {currentLevel?.alt != null && (
          <span className="scrubber-current-alt">{(currentLevel.alt / 1000).toFixed(1)} km</span>
        )}
        {currentLevel?.temp != null && (
          <span className="scrubber-current-temp">{currentLevel.temp.toFixed(1)}°C</span>
        )}
        <span className="scrubber-live-badge">STATION</span>
      </div>

      <div className="station-scrubber-row">
        <span className="scrubber-label">
          Time
          <br />
          <small>{fmtTime(snapshots[0]?.datetime)}</small>
        </span>
        <div className="scrubber-range-wrap">
          <div className="scrubber-fill" style={{ width: `${timePct}%` }} />
          <input
            type="range"
            min={0}
            max={timeMax}
            value={safeTimeIndex}
            onChange={(e) => onTimeChange(Number(e.target.value))}
            className="scrubber-range"
          />
        </div>
        <span className="scrubber-label live-label">
          Latest
          <br />
          <small>{fmtTime(snapshots[timeMax]?.datetime)}</small>
        </span>
      </div>

      <div className="station-scrubber-row">
        <span className="scrubber-label">
          Height
          <br />
          <small>{levels[0]?.alt != null ? `${(levels[0].alt / 1000).toFixed(1)} km` : '—'}</small>
        </span>
        <div className="scrubber-range-wrap">
          <div className="scrubber-fill station-height-fill" style={{ width: `${heightPct}%` }} />
          <input
            type="range"
            min={0}
            max={levelMax}
            value={safeHeightIndex}
            onChange={(e) => onHeightChange(Number(e.target.value))}
            className="scrubber-range"
          />
        </div>
        <span className="scrubber-label live-label">
          Top
          <br />
          <small>{levels[levelMax]?.alt != null ? `${(levels[levelMax].alt / 1000).toFixed(1)} km` : '—'}</small>
        </span>
      </div>
    </div>
  );
}
