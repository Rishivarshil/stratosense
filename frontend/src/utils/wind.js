const BANDS = [
  { label: 'Jet',     altRange: '9+ km',  minAlt: 9000, maxAlt: Infinity },
  { label: 'Upper',   altRange: '6–9 km', minAlt: 6000, maxAlt: 9000 },
  { label: 'Mid',     altRange: '3–6 km', minAlt: 3000, maxAlt: 6000 },
  { label: 'Low',     altRange: '1–3 km', minAlt: 1000, maxAlt: 3000 },
  { label: 'Surface', altRange: '0–1 km', minAlt: 0,    maxAlt: 1000 },
];

/**
 * Average wind directions using u/v component decomposition.
 * Handles the 0/360 wraparound correctly.
 */
export function averageWindDirection(directions) {
  if (directions.length === 0) return 0;
  let u = 0, v = 0;
  directions.forEach((d) => {
    u += Math.sin((d * Math.PI) / 180);
    v += Math.cos((d * Math.PI) / 180);
  });
  return ((Math.atan2(u, v) * 180) / Math.PI + 360) % 360;
}

/**
 * Group a raw wind_profile array into 5 meteorological altitude bands.
 * Returns one entry per band (top to bottom: Jet → Surface),
 * with averaged speed/direction or null if no data in that band.
 */
export function groupIntoBands(windProfile) {
  const buckets = BANDS.map((b) => ({ ...b, entries: [] }));

  windProfile.forEach((w) => {
    for (const bucket of buckets) {
      if (w.alt >= bucket.minAlt && w.alt < bucket.maxAlt) {
        bucket.entries.push(w);
        break;
      }
    }
  });

  return buckets.map((b) => {
    if (b.entries.length === 0) {
      return {
        label: b.label,
        altRange: b.altRange,
        avgSpeedKnots: null,
        avgSpeedMs: null,
        avgDirectionDeg: null,
        count: 0,
      };
    }

    const avgKnots =
      b.entries.reduce((sum, e) => sum + e.speed_knots, 0) / b.entries.length;
    const avgMs =
      b.entries.reduce((sum, e) => sum + e.speed_ms, 0) / b.entries.length;
    const avgDir = averageWindDirection(b.entries.map((e) => e.direction_deg));

    return {
      label: b.label,
      altRange: b.altRange,
      avgSpeedKnots: Math.round(avgKnots * 10) / 10,
      avgSpeedMs: Math.round(avgMs * 10) / 10,
      avgDirectionDeg: Math.round(avgDir),
      count: b.entries.length,
    };
  });
}

/**
 * Convert degrees to a cardinal/intercardinal label.
 */
export function degreesToCardinal(deg) {
  const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  return dirs[Math.round(deg / 22.5) % 16];
}
