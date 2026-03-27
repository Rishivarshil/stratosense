# StratoSense

Real-time atmospheric analysis platform for weather balloon telemetry. Tracks active radiosondes worldwide via SondeHub, runs meteorological calculations on flight data, and visualizes results in an interactive dashboard.

## Features

- **Front Landing Page** — cinematic homepage at `/` with live status and direct dashboard access
- **Live Balloon Tracker** — interactive globe map showing active radiosondes worldwide
- **Atmospheric Sounding Chart** — temperature and dewpoint profiles vs altitude
- **Wind Barb Visualization** — standard meteorological wind profile by altitude band
- **Instability Score Card** — CAPE, CIN, lapse rate, tropopause altitude, precipitable water, and storm risk
- **Plain English Forecasts** — human-readable atmospheric summaries generated from the data
- **Flight Timeline Scrubber** — replay a balloon's ascent frame by frame
- **Real-time Updates** — backend polls SondeHub every 30 seconds; frontend refreshes every 2–30 seconds via REST + WebSocket

## Architecture

```text
SondeHub API  →  Flask + SocketIO backend (port 8080)  →  React + Vite frontend (port 5173)
```

| Layer | Technology |
| --- | --- |
| Data source | SondeHub API (global radiosonde network) |
| Backend | Python 3, Flask, Flask-SocketIO, Requests, python-dotenv |
| Frontend | React 19, Vite 8 |
| 3D / visualization | Three.js, Chart.js 4, react-chartjs-2 |

## Requirements

### Backend (Python)

- Python 3.9+
- `flask`
- `flask-socketio`
- `requests`
- `python-dotenv`

Optional for running backend tests:

- `pytest`

### Frontend (Node)

- Node.js 20+
- npm 9+

Dependencies are listed in [frontend/package.json](frontend/package.json). Key packages:

- `react` 19
- `react-dom` 19
- `react-router-dom` 7
- `three`
- `chart.js` 4
- `react-chartjs-2`
- `vite` 8
- `chartjs-plugin-annotation`
- `chartjs-plugin-zoom`

## Setup & Running

### 1. Clone the repo

```bash
git clone https://github.com/Bharathpillai06/stratosense.git
cd stratosense
```

### 2. Create and activate a Python virtual environment

#### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run this once in the same shell and try again:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

#### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install backend dependencies inside the venv

From the repo root:

```bash
pip install flask flask-socketio requests python-dotenv
```

If you also want to run the backend tests:

```bash
pip install pytest
```

### 4. Start the backend

```bash
cd src
python data_pipeline.py
```

The API starts on `http://localhost:8080`. On first run it fetches active balloons from SondeHub and begins a 30-second polling loop in the background.

### 5. Install frontend dependencies

Open a second terminal at the repo root:

```bash
cd frontend
npm install
```

This installs the frontend dependencies from `frontend/package.json`, including:

- `react`
- `react-dom`
- `react-router-dom`
- `three`
- `chart.js`
- `react-chartjs-2`
- `chartjs-plugin-annotation`
- `chartjs-plugin-zoom`

### 6. Start the frontend

```bash
npm run dev
```

The frontend starts at `http://localhost:5173`.

- Landing page: `http://localhost:5173/`
- Dashboard: `http://localhost:5173/dashboard`

### 7. Summary

You should have:

1. A backend terminal with the virtual environment activated and `python data_pipeline.py` running from `src`
2. A frontend terminal running `npm run dev` from `frontend`

### Optional: deactivate the virtual environment

When you are done working on the backend:

```bash
deactivate
```

## API Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/balloons` | List all cached balloons with metadata |
| GET | `/balloon/<serial>` | Full telemetry + atmospheric analysis for one balloon |
| GET | `/status` | Server health and balloon count |
| WS | — | SocketIO connection for real-time balloon updates |

## Atmospheric Calculations

The backend ([src/data_pipeline.py](src/data_pipeline.py)) computes the following from raw radiosonde telemetry:

- **Lapse rate** — environmental temperature gradient (K/km)
- **Tropopause detection** — altitude where lapse rate inverts
- **CAPE / CIN** — convective available potential energy and convective inhibition
- **Wind shear profile** — speed and direction at altitude bands
- **Precipitable water** — integrated moisture content estimate
- **Storm risk classification** — low / moderate / high / extreme based on CAPE thresholds

## Project Structure

```text
stratosense/
├── src/
│   ├── data_pipeline.py        # Flask backend + atmospheric analysis
│   └── test_data_pipeline.py   # Backend tests
└── frontend/
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── App.jsx                          # Router entrypoint
        ├── assets/
        │   └── hero-stratosphere.png        # Landing page hero background
        ├── components/
        │   ├── Globe.jsx                    # Leaflet balloon map
        │   ├── FlightScrubber.jsx           # Timeline slider
        │   ├── AltitudeColumn.jsx           # Altitude display
        │   ├── SoundingChart.jsx            # Temperature/dewpoint chart
        │   ├── WindBarbs.jsx                # SVG wind barb visualization
        │   └── ScoreCard.jsx                # Instability metrics dashboard
        ├── pages/
        │   ├── LandingPage.jsx              # Marketing landing page
        │   └── DashboardPage.jsx            # Main analysis dashboard
        ├── styles/
        │   ├── landing.css                  # Landing page styles
        │   └── dashboard.css                # Dashboard styles
        └── utils/
            ├── atmospheric.js               # Dewpoint (Magnus formula)
            └── wind.js                      # Wind data grouping by altitude band
```

## Data Source

Balloon telemetry is sourced from [SondeHub](https://sondehub.org), a community-driven global radiosonde tracking network. No API key is required for read access.
