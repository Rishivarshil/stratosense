# StratoSense — Person 3: Atmospheric Charts & Analysis Panel

**Comprehensive Developer Guide | Buckeye Black Box Hackathon 2026**

---

## 1. What Is Person 3?

Person 3 owns the **atmospheric charts and analysis panel** — the right-hand side of the app that appears when a user clicks any balloon on the 3D globe. Person 2 handles the map, Person 4 handles the ML forecast card. Person 3 is responsible for everything in between: the **visual science layer** that turns raw telemetry into charts and scores a user can actually understand.

**Person 2 gives the user a dot on a globe. Person 4 gives them a forecast card. Person 3 gives them everything that explains WHY the forecast says what it says.**

### 1.1 The Three Main Deliverables

| Deliverable | What It Is | Priority |
|---|---|---|
| **Sounding Chart** | Altitude vs temperature/humidity plot that builds upward as balloon climbs | HIGH |
| **Wind Barb Diagram** | Visual showing wind speed + direction at every altitude level | HIGH |
| **Instability Score Card** | CAPE, lapse rate, tropopause, storm risk in a clean dashboard card | HIGH |

> All three must be visible simultaneously in a side panel — not tabs. All visible at once so judges see everything in one glance.

---

## 2. Project Overview & Architecture

### 2.1 What StratoSense Does

StratoSense is a real-time atmospheric analysis platform that ingests live weather balloon (radiosonde) telemetry from the **SondeHub API**, performs atmospheric science calculations, and presents the results through a 3D globe with detailed analysis panels.

### 2.2 Data Sources

| Source | Description |
|---|---|
| **SondeHub API** | Real-time and historical data for every weather balloon on Earth. Free. Updates every few seconds. |
| **SondeHub S3 Archive** | Every flight ever recorded. Used by Person 4 for ML training. |
| **Local SDR (Raspberry Pi)** | Team member's Pi receiving live balloon telemetry off the air, overlaid on the SondeHub global feed. |

### 2.3 The Three SondeHub API Calls

```
All active balloons right now:
GET https://api.v2.sondehub.org/sondes/telemetry?duration=1h

Full flight path for one balloon:
GET https://api.v2.sondehub.org/sondes/telemetry?serial=SERIAL&duration=24h

Historical flights near Columbus:
GET https://api.v2.sondehub.org/sondes/telemetry?lat=39.99&lon=-83.01&distance=300&duration=24h
```

### 2.4 Team Roles

| Person | Responsibility |
|---|---|
| **Person 1** | Data pipeline — Python/Flask backend that pulls SondeHub API, cleans data, calculates CAPE/lapse rate/tropopause. **Already built.** |
| **Person 2** | 3D globe frontend — map showing active balloons, flight paths, timeline scrubber, click to select. |
| **Person 3 (You)** | Atmospheric charts — sounding chart, wind barb diagram, instability score card. |
| **Person 4** | ML predictor — historical S3 data, fingerprint matcher, forecast cards, SDR integration. |

---

## 3. Existing Codebase

### 3.1 Repository Structure

```
stratosense/
├── README.md
└── src/
    └── data_pipeline.py    ← Person 1's Flask backend (490 lines, complete)
```

### 3.2 The Data Pipeline (What Person 1 Already Built)

`src/data_pipeline.py` is a Flask + SocketIO server that:

- **Polls SondeHub every 30 seconds** via a background thread, caching all active balloons
- **Emits WebSocket updates** (`balloons_update` event) to connected clients
- **Serves REST endpoints** on port 8080 with CORS enabled (`cors_allowed_origins="*"`)
- **Calculates all atmospheric analysis** — lapse rate, tropopause detection, wind profile from GPS drift, CAPE/CIN, precipitable water, and generates plain English forecast text

Key implementation details in the pipeline:

- **Lapse rate**: Linear regression across all altitude/temp points, returned as °C/km (positive = normal cooling)
- **Tropopause detection**: Finds where temperature stops decreasing above 8km — looks for sustained temp increase
- **Wind profile**: Calculated from GPS drift between consecutive frames using haversine distance and bearing
- **CAPE/CIN**: Simplified parcel theory — dry adiabatic (9.8°C/km) below LCL (assumed 500m AGL), moist adiabatic (6.0°C/km) above
- **Precipitable water**: Integrated from humidity profile using Tetens saturation vapor pressure formula
- **Storm risk labels**: `low` (<300 CAPE), `moderate` (300–1000), `high` (1000–2500), `extreme` (2500+)

---

## 4. What Data You Get

**You do not call the SondeHub API yourself.** Person 1's pipeline serves everything you need over Flask on port 8080.

### 4.1 The Endpoints You Use

| Endpoint | What It Returns |
|---|---|
| `GET /balloons` | All active balloons with latest position, count, and last_updated timestamp |
| `GET /balloon/<serial>` | Full flight path — every GPS frame in chronological order |
| `GET /balloon/<serial>/analysis` | Pre-calculated CAPE, CIN, lapse rate, tropopause, wind profile, precipitable water, storm risk |
| `GET /balloon/<serial>/forecast` | Plain English forecast — storm_risk, summary, and details array of sentences |
| `GET /balloon/<serial>/telemetry` | Raw telemetry frames with metadata (first/last seen, max altitude, min temp, sonde type, frequency) |
| `GET /status` | Server health check — status, active balloon count, last update time |

### 4.2 What a Flight Path Frame Looks Like

Each frame in the `/balloon/<serial>` response `path` array:

```json
{
  "lat": 39.992,
  "lon": -83.011,
  "alt": 15234.5,
  "temp": -45.2,
  "humidity": 12.4,
  "vel_v": 5.2,
  "datetime": "2026-03-25T21:30:00Z"
}
```

| Field | Type | Description |
|---|---|---|
| `lat` | float | Latitude |
| `lon` | float | Longitude |
| `alt` | float | Meters above sea level |
| `temp` | float or null | Degrees Celsius |
| `humidity` | float or null | Percent relative humidity |
| `vel_v` | float or null | Vertical velocity m/s (positive = ascending) |
| `datetime` | string | ISO 8601 UTC timestamp |

### 4.3 What the Analysis Response Looks Like

```json
{
  "serial": "X3833334",
  "frame_count": 482,
  "lapse_rate_c_per_km": 7.3,
  "tropopause_alt_m": 11400,
  "tropopause_alt_km": 11.4,
  "cape": 840.2,
  "cin": -45.1,
  "storm_risk": "moderate — isolated storm possible",
  "precipitable_water_mm": 28.4,
  "surface_temp": 18.3,
  "max_alt": 28450.0,
  "sonde_type": "RS41",
  "wind_profile": [
    { "alt": 500, "speed_ms": 4.2, "speed_knots": 8.2, "direction_deg": 245.0 },
    { "alt": 1500, "speed_ms": 8.1, "speed_knots": 15.7, "direction_deg": 260.0 }
  ]
}
```

### 4.4 What the Forecast Response Looks Like

```json
{
  "serial": "X3833334",
  "generated_at": "2026-03-25T22:00:00+00:00",
  "storm_risk": "moderate — isolated storm possible",
  "summary": "CAPE is 840.2 J/kg — moderate — isolated storm possible.",
  "details": [
    "CAPE is 840.2 J/kg — moderate — isolated storm possible.",
    "Lapse rate of 7.3 C/km is near neutral — atmosphere is conditionally unstable.",
    "Tropopause at 11.4 km — typical for mid-latitudes.",
    "Precipitable water of 28.4 mm is moderate — decent moisture available.",
    "Upper level winds averaging 22.5 m/s from 270.3 degrees."
  ]
}
```

---

## 5. Chart 1 — The Sounding Chart

The sounding chart is the most important atmospheric visualization in meteorology. It shows temperature (and optionally humidity/dewpoint) on the horizontal axis vs altitude on the vertical axis. As the balloon climbs, the line builds upward in real time.

### 5.1 What It Shows

- **Temperature line** — main red/orange line showing how temperature changes with altitude
- **Dewpoint line** — blue line showing humidity as dewpoint temperature. Where temp and dewpoint converge = saturated air = clouds
- **Tropopause marker** — horizontal dashed line at the altitude where temp stops dropping (from analysis endpoint)
- **Color fill between lines** — optional shading between temp and dewpoint to show cloud/moisture depth

### 5.2 How to Build It

Use **Chart.js** with altitude on the Y axis, temperature on the X axis.

**Step 1 — Fetch the data:**

```javascript
const res = await fetch(`http://localhost:8080/balloon/${serial}`);
const data = await res.json();
const frames = data.path.filter(f => f.temp !== null && f.alt !== null);
```

**Step 2 — Set up Chart.js:**

```javascript
const chart = new Chart(ctx, {
  type: 'line',
  data: {
    datasets: [{
      label: 'Temperature (°C)',
      data: frames.map(f => ({ x: f.temp, y: f.alt / 1000 })),
      borderColor: '#e8593c',
      borderWidth: 2,
      pointRadius: 0,
    }, {
      label: 'Dewpoint (°C)',
      data: frames.map(f => ({ x: calcDewpoint(f.temp, f.humidity), y: f.alt / 1000 })),
      borderColor: '#3b8bd4',
      borderWidth: 2,
      pointRadius: 0,
    }]
  },
  options: {
    scales: {
      x: { title: { display: true, text: 'Temperature (°C)' } },
      y: { title: { display: true, text: 'Altitude (km)' }, min: 0 }
    }
  }
});
```

**Dewpoint calculation (Magnus formula):**

```javascript
function calcDewpoint(temp, humidity) {
  if (!humidity) return null;
  const a = 17.67, b = 243.5;
  const alpha = (a * temp) / (b + temp) + Math.log(humidity / 100);
  return (b * alpha) / (a - alpha);
}
```

### 5.3 The Animation

The line building upward as the balloon climbs is what makes this visually compelling.

**Replay mode (recommended for demo):** Use `setInterval` to progressively add frames one by one. The `chart.data.datasets[0].data` array grows each tick and `chart.update()` is called. Start at the lowest altitude frame and add one frame every ~100ms so the full profile draws in ~30 seconds.

**Live mode:** If the balloon is still in the air, poll `/balloon/<serial>` every 10 seconds and append new frames.

### 5.4 The Tropopause Line

Draw a horizontal dashed line at the tropopause altitude using `chartjs-plugin-annotation`:

```javascript
annotations: {
  tropopause: {
    type: 'line',
    yMin: analysis.tropopause_alt_km,
    yMax: analysis.tropopause_alt_km,
    borderColor: '#aa44aa',
    borderWidth: 1,
    borderDash: [6, 3],
    label: { content: 'Tropopause', enabled: true }
  }
}
```

---

## 6. Chart 2 — The Wind Barb Diagram

Wind barbs are the standard meteorological way to show wind speed and direction. A barb is an arrow pointing in the wind direction with lines on the tail indicating speed.

### 6.1 Wind Barb Symbol Reference

| Symbol | Meaning |
|---|---|
| Short line on tail | 5 knots |
| Long line on tail | 10 knots |
| Filled triangle on tail | 50 knots |
| Arrow direction | Direction wind is coming FROM |

Example: 2 long lines + 1 short line = 25 knots. Arrow pointing right = westerly wind.

### 6.2 Simpler Alternative — Wind Profile Bar Chart

If SVG wind barbs are too complex for hackathon time, a **wind profile bar chart** is acceptable. Two charts side by side: speed vs altitude and direction vs altitude.

```javascript
// Speed chart
data: winds.map(w => ({ x: w.speed_knots, y: w.alt / 1000 }))

// Direction chart — scatter plot
data: winds.map(w => ({ x: w.direction_deg, y: w.alt / 1000 }))
```

### 6.3 Recommended Altitude Bands

The raw wind profile has a data point per frame — too many to display. Group into bands:

| Altitude Band | Label | Meteorological Significance |
|---|---|---|
| 0–1 km | Surface | Low-level inflow for storms |
| 1–3 km | Low levels | Storm-relative flow |
| 3–6 km | Mid levels | Storm steering layer |
| 6–9 km | Upper levels | Divergence layer |
| 9+ km | Jet stream | Storm track and development |

Average speed and direction within each band. **For direction averaging, convert to u/v components first, average those, then convert back** — otherwise you get wrong results near the 0/360 degree wrap:

```javascript
function averageWindDirection(directions) {
  let u = 0, v = 0;
  directions.forEach(d => {
    u += Math.sin(d * Math.PI / 180);
    v += Math.cos(d * Math.PI / 180);
  });
  return (Math.atan2(u, v) * 180 / Math.PI + 360) % 360;
}
```

---

## 7. Chart 3 — The Instability Score Card

A dashboard-style panel showing all calculated atmospheric indices at a glance. Bold numbers, color-coded risk levels, short plain English labels.

### 7.1 Fields to Display

| Field | Source Field | Unit | Color Coding |
|---|---|---|---|
| CAPE | `analysis.cape` | J/kg | Green < 300, Yellow 300–1000, Orange 1000–2500, Red > 2500 |
| CIN | `analysis.cin` | J/kg | Show as negative number, grey — informational only |
| Lapse Rate | `analysis.lapse_rate_c_per_km` | °C/km | Blue < 6.5, Yellow 6.5–9.8, Red > 9.8 |
| Tropopause | `analysis.tropopause_alt_km` | km | No color — factual |
| Precip Water | `analysis.precipitable_water_mm` | mm | Yellow > 25, Orange > 40 |
| Storm Risk | `analysis.storm_risk` | label | Green/Yellow/Orange/Red by risk level |
| Surface Temp | `analysis.surface_temp` | °C | No color — factual |
| Max Altitude | `analysis.max_alt / 1000` | km | No color — factual |

### 7.2 Risk Color Reference

| CAPE Value | Risk Label | Background | Text Color |
|---|---|---|---|
| 0–300 J/kg | Low | `#d4edda` | `#155724` |
| 300–1000 J/kg | Moderate | `#fff3cd` | `#856404` |
| 1000–2500 J/kg | High | `#ffeeba` | `#c8540a` |
| 2500+ J/kg | Extreme | `#f8d7da` | `#721c24` |

### 7.3 Layout

Lay out as a **2×4 grid of metric tiles**. Each tile:
- Metric name in small text at top
- Value in large bold text in the middle
- Colored badge showing risk level at bottom

Below the grid: plain English forecast details from `/forecast` endpoint as a bulleted list.

```html
<div class="metric-tile" style="background: #fff3cd; border-radius: 8px; padding: 16px; text-align: center;">
  <div class="metric-label">CAPE</div>
  <div class="metric-value">840 J/kg</div>
  <div class="metric-badge" style="background: #856404; color: white;">Moderate</div>
</div>
```

---

## 8. How Person 3 Connects to the Rest of the Team

### 8.1 Person 2 → Person 3 (Balloon Selection)

When a user clicks a balloon on the 3D globe, Person 2 fires an event with the sonde serial number. Your code listens for it and populates all three charts.

```javascript
// Person 2 fires this when a balloon is clicked:
document.dispatchEvent(new CustomEvent("balloonSelected", {
  detail: { serial: "X3833334" }
}));

// Person 3 listens for it:
document.addEventListener("balloonSelected", async (e) => {
  const serial = e.detail.serial;
  await loadSoundingChart(serial);
  await loadWindProfile(serial);
  await loadScoreCard(serial);
});
```

> **Note:** If the team uses React, replace this with Zustand or React Context so the selected serial flows cleanly between Person 2, 3, and 4.

### 8.2 Person 3 → Person 4 (Panel Space)

Person 4's ML forecast card sits below your score card in the analysis panel. Leave space at the bottom for it. You don't need to integrate anything — Person 4 appends their card into the same panel.

### 8.3 Person 1 → Person 3 (Data)

Person 1's Flask server on port 8080 is your sole data source. CORS is already enabled. The pipeline also emits WebSocket events via SocketIO (`balloons_update`) if you want real-time push updates for the active balloon list.

---

## 9. Tech Stack & Libraries

| Library | Purpose | Install |
|---|---|---|
| **Chart.js** | Sounding chart, wind profile charts | `npm install chart.js` |
| **chartjs-plugin-annotation** | Tropopause line on sounding chart | `npm install chartjs-plugin-annotation` |
| **chartjs-plugin-zoom** | User zoom into altitude ranges | `npm install chartjs-plugin-zoom` |
| **D3.js** (optional) | Custom SVG wind barbs if you go that route | `npm install d3` |

Chart.js is the right call — faster to set up than D3 for standard charts, annotation plugin handles the tropopause line cleanly, and the zoom plugin lets users drill into altitude ranges (impressive in a demo).

---

## 10. Demo Tips

The charts are what judges will spend the most time looking at.

1. **Pre-select a balloon with high CAPE** for the demo. Pick one from `/balloons` that shows storm risk as `high` or `extreme` — the score card looks more impressive with dramatic numbers.
2. **Let the sounding chart animate during the demo.** Don't show the final state — show it building upward. The climbing motion is what makes people lean forward.
3. **Have the tropopause line appear automatically** once the chart finishes drawing. Add a fade transition.
4. **Load the score card before the charts finish.** Show numbers immediately while the sounding chart is still animating — fills the screen and makes everything feel live.
5. **Point to the CAPE number and explain it out loud.** Most judges won't know what 840 J/kg means — tell them it means isolated storm potential. The forecast card helps, but reinforce verbally.

---

## 11. Suggested Build Order

| Step | Task | Est. Time |
|---|---|---|
| 1 | Set up the side panel HTML layout — three sections with placeholders | 30 min |
| 2 | Listen for `balloonSelected` event and log serial to confirm it works | 15 min |
| 3 | Fetch `/balloon/<serial>` and `/balloon/<serial>/analysis`, log the data | 15 min |
| 4 | Build the score card tiles — hardcode one balloon first, then dynamic | 45 min |
| 5 | Build the sounding chart — static first, then add animation | 60 min |
| 6 | Add the tropopause annotation line | 20 min |
| 7 | Build the wind profile bar chart | 45 min |
| 8 | Connect forecast text from `/balloon/<serial>/forecast` to the panel | 20 min |
| 9 | Polish — color coding, transitions, loading states | 45 min |

**Total: ~5–6 hours.** Get something working first through Steps 1–8, then polish.

---

## 12. Questions for the Team

These need to be resolved before or during integration:

| Question | For |
|---|---|
| What framework are you using for the globe? (Three.js, Cesium, Deck.gl?) — affects whether we share a bundler | Person 2 |
| How exactly are you firing the `balloonSelected` event? Custom DOM event, React state, or shared store? | Person 2 |
| Where in the panel does your ML forecast card go? Below the score card? I'll leave space. | Person 4 |
| Is the Flask server running on port 8080? Is CORS enabled? (Confirmed: yes, `cors_allowed_origins="*"`) | Person 1 ✅ |

> If anyone is using React, agree on shared state management (Zustand or React Context) so the selected balloon serial flows cleanly between Persons 2, 3, and 4 without custom DOM events.

---

## 13. Reference: Pipeline Internals

For context on what the pipeline is doing behind the scenes (you don't need to modify this, but understanding it helps explain the data to judges):

### 13.1 Atmospheric Calculation Methods

**Lapse Rate** — Linear regression of temperature vs altitude across all frames. Returns °C/km. A normal tropospheric lapse rate is ~6.5°C/km. Values >9.8°C/km (dry adiabatic rate) indicate absolute instability.

**Tropopause Detection** — Scans upward from 8km looking for where temperature reverses from decreasing to increasing over 3+ consecutive points. The tropopause is the boundary between troposphere and stratosphere.

**Wind Profile** — GPS drift between consecutive frames: haversine distance divided by time delta gives speed; bearing calculation gives direction. Each wind data point uses the midpoint altitude between two frames. Frames >120 seconds apart are discarded to avoid interpolation errors.

**CAPE/CIN** — Parcel theory: a surface air parcel is lifted using the dry adiabatic lapse rate (9.8°C/km) up to the LCL (assumed 500m AGL), then the moist adiabatic rate (6.0°C/km) above. CAPE accumulates where parcel temp > environmental temp. CIN accumulates where parcel temp < environmental temp, but only before any CAPE has accumulated.

**Precipitable Water** — Integrates water vapor from the humidity profile using Tetens formula for saturation vapor pressure, summed through the column depth.

### 13.2 WebSocket Events

The pipeline emits `balloons_update` via SocketIO every 30 seconds with an array of all active balloon summaries. You can optionally use this to refresh your panel if the user has a balloon selected and it's still transmitting.

---

**StratoSense | Person 3 Guide | Buckeye Black Box Hackathon 2026**
