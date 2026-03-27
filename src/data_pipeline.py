import requests
import math
import json
from flask import Flask, jsonify, request
from flask_socketio import SocketIO
from datetime import datetime, timezone
import threading
import time
import os
from dotenv import load_dotenv
from interpolation import generate_full_profile

# Auto-load environment variables from .env in repository root
load_dotenv()
SYNOPTIC_TOKEN = os.getenv("SYNOPTIC_TOKEN")

# Person 4: atmospheric model + SDR blueprints
from atmosphere import atmosphere_bp
from sdr_integration import sdr_bp

app = Flask(__name__)
app.register_blueprint(atmosphere_bp)
app.register_blueprint(sdr_bp)
socketio = SocketIO(app, cors_allowed_origins="*")

# ─── CACHE ───────────────────────────────────────────────────────────────────
cache = {
    "balloons": {},
    "last_updated": None
}

# --- SYNOPTIC FETCH ----------------------------------------------------------
def search_stations(lat: float = None, long: float = None, radius: float = None, network: int = None):
    try:
        params = {'token': SYNOPTIC_TOKEN, 'status': 'active'}
        
        # Add radius search parameters if all are provided
        if lat is not None and long is not None and radius is not None and radius > 0:
            print(f"Searching for stations at ({lat}, {long}) within radius {radius}")
            params['radius'] = f'{lat},{long},{radius}'
        else:
            print("Searching for stations (no radius specified)")
        
        # Add network filter if provided
        if network is not None and network > 0:
            params['mnet'] = network
        
        response = requests.get(
            f"https://api.synopticdata.com/v2/stations/metadata",
            params=params,
            timeout=30
        )
        data = response.json()
        print(f"Got {len(data)} active stations")
        return data
    except Exception as e:
        print(f"Fetch error: {e}")
        return {}
    
def fetch_station_data(serial: str):
    try:
        print(f"Fetching data for station {serial}")
        response = requests.get(
            f"https://api.synopticdata.com/v2/stations/latest",
            params={'token': SYNOPTIC_TOKEN,
                'stid': serial
            }
        )
        data = response.json()
        print(f"Got {len(data)} active stations")
        return data
    except Exception as e:
        print(f"Fetch error: {e}")
        return {}
    
ASSIMILATION_VARS = [
    'air_temp', 'dew_point_temperature',
    'sea_level_pressure', 'wind_speed', 'wind_direction',
]


def fetch_station_timeseries(serial: str, recent_minutes: int = 120, variables: list = None):
    """
    Fetch time-series observations from the Synoptic v2 API.

    Defaults to the five variables the assimilation pipeline needs.
    Validates the response structure and returns {} on any failure.
    """
    if variables is None:
        variables = ASSIMILATION_VARS

    try:
        params = {
            'token': SYNOPTIC_TOKEN,
            'stid': serial,
            'recent': recent_minutes,
            'vars': ','.join(variables),
        }

        response = requests.get(
            'https://api.synopticdata.com/v2/stations/timeseries',
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        summary = data.get('SUMMARY', {})
        if summary.get('RESPONSE_CODE') != 1:
            print(f"Synoptic API error for {serial}: "
                  f"{summary.get('RESPONSE_MESSAGE', 'unknown')}")
            return {}

        if not data.get('STATION'):
            print(f"No station records returned for {serial}")
            return {}

        return data
    except requests.exceptions.Timeout:
        print(f"Timeout fetching timeseries for {serial}")
        return {}
    except requests.exceptions.RequestException as e:
        print(f"Request error fetching timeseries for {serial}: {e}")
        return {}
    except (ValueError, KeyError) as e:
        print(f"Parse error for {serial} timeseries: {e}")
        return {}


def parse_timeseries_for_assimilation(raw):
    """
    Turn a Synoptic v2 timeseries response into a flat list of dicts
    suitable for the assimilation nudging pipeline.

    Each entry: {
        'datetime_utc': str,  'temp_c': float|None,
        'dewpoint_c': float|None, 'pressure_hpa': float|None,
        'wind_speed_ms': float|None, 'wind_dir_deg': float|None,
        'elev_m': float
    }
    """
    try:
        station = raw['STATION'][0]
    except (KeyError, IndexError, TypeError):
        return []

    obs = station.get('OBSERVATIONS', {})
    timestamps = obs.get('date_time', [])
    if not timestamps:
        return []

    temps = obs.get('air_temp_set_1', [])
    dewpoints = obs.get('dew_point_temperature_set_1', [])
    pressures = obs.get('sea_level_pressure_set_1', [])
    winds = obs.get('wind_speed_set_1', [])
    wind_dirs = obs.get('wind_direction_set_1', [])
    elev = float(station.get('ELEVATION', 247))

    def safe_float(lst, idx):
        try:
            v = lst[idx]
            return float(v) if v is not None else None
        except (IndexError, TypeError, ValueError):
            return None

    records = []
    for i, ts in enumerate(timestamps):
        temp = safe_float(temps, i)
        if temp is None:
            continue
        records.append({
            'datetime_utc': ts,
            'temp_c': temp,
            'dewpoint_c': safe_float(dewpoints, i),
            'pressure_hpa': safe_float(pressures, i),
            'wind_speed_ms': safe_float(winds, i),
            'wind_dir_deg': safe_float(wind_dirs, i),
            'elev_m': elev,
        })

    return records


def estimate_relative_humidity(temp_c, dewpoint_c):
    if temp_c is None or dewpoint_c is None:
        return None
    try:
        # Convert T/Td to RH (%) using Magnus approximation.
        expo = ((17.625 * dewpoint_c) / (243.04 + dewpoint_c)) - ((17.625 * temp_c) / (243.04 + temp_c))
        rh = 100.0 * math.exp(expo)
        return max(0.0, min(100.0, round(rh, 2)))
    except Exception:
        return None


def _build_station_hybrid_dataset(stid: str, recent_minutes: int = 180):
    raw = fetch_station_timeseries(stid, recent_minutes=recent_minutes)
    if not raw:
        return None, "No weather data found for station"

    parsed = parse_timeseries_for_assimilation(raw)
    if not parsed:
        return None, "No parsed observations found for station"

    station = (raw.get('STATION') or [{}])[0]
    lat = station.get('LATITUDE')
    lon = station.get('LONGITUDE')

    snapshots = []
    for obs in parsed:
        temp_c = obs.get('temp_c')
        elev_m = obs.get('elev_m')
        if temp_c is None or elev_m is None:
            continue

        dewpoint_c = obs.get('dewpoint_c')
        pressure_hpa = obs.get('pressure_hpa')
        wind_speed_ms = obs.get('wind_speed_ms')
        wind_dir_deg = obs.get('wind_dir_deg')

        surface = {
            'temp_c': temp_c,
            'dewpoint_c': dewpoint_c if dewpoint_c is not None else temp_c - 4.0,
            'pressure_hpa': pressure_hpa if pressure_hpa is not None else 1013.25,
            'elev_m': elev_m,
            'wind_speed_ms': wind_speed_ms if wind_speed_ms is not None else 2.0,
            'wind_dir_deg': wind_dir_deg if wind_dir_deg is not None else 180.0,
            'elr': 6.5,
            'dewpoint_lapse': 2.0,
        }
        profile = generate_full_profile(surface, max_alt=20000, step=500)
        levels = []
        for p in profile:
            levels.append({
                'lat': lat,
                'lon': lon,
                'alt': p.get('altitude_m'),
                'temp': p.get('temp_c'),
                'pressure_hpa': p.get('pressure_hpa'),
                'humidity': p.get('humidity_pct'),
                'dewpoint': p.get('dewpoint_c'),
                'datetime': obs.get('datetime_utc'),
                'wind_speed_ms': (p.get('wind') or {}).get('speed_ms'),
                'wind_dir_deg': (p.get('wind') or {}).get('direction_deg'),
                'source': p.get('source', 'interpolated'),
            })

        if not levels:
            continue

        frame_like = [
            {
                "alt": lv["alt"],
                "temp": lv["temp"],
                "humidity": lv["humidity"],
                "datetime": lv["datetime"],
            }
            for lv in levels
            if lv["alt"] is not None and lv["temp"] is not None
        ]

        lapse = calc_lapse_rate(frame_like)
        tropo = find_tropopause(frame_like)
        cape, cin, risk = calc_cape_cin(frame_like)
        pw = calc_precipitable_water(frame_like)

        wind_profile = []
        for lv in levels:
            ws = lv.get("wind_speed_ms")
            wd = lv.get("wind_dir_deg")
            alt = lv.get("alt")
            if ws is None or wd is None or alt is None:
                continue
            wind_profile.append({
                "alt": alt,
                "speed_ms": round(float(ws), 2),
                "speed_knots": round(float(ws) * 1.94384, 1),
                "direction_deg": round(float(wd), 1),
            })

        analysis = {
            "serial": stid,
            "frame_count": len(frame_like),
            "lapse_rate_c_per_km": lapse,
            "tropopause_alt_m": tropo,
            "tropopause_alt_km": round(tropo / 1000, 1) if tropo else None,
            "cape": cape,
            "cin": cin,
            "storm_risk": risk,
            "precipitable_water_mm": pw,
            "wind_profile": wind_profile,
            "surface_temp": temp_c,
            "max_alt": max((lv.get("alt") or 0) for lv in levels),
            "sonde_type": "synoptic_station_hybrid",
            "snapshot_time": obs.get('datetime_utc'),
        }

        snapshots.append({
            "datetime": obs.get('datetime_utc'),
            "observed_surface": obs,
            "levels": levels,
            "analysis": analysis,
        })

    if not snapshots:
        return None, "No valid station snapshots generated"

    return {
        "serial": stid,
        "lat": lat,
        "lon": lon,
        "times": [s["datetime"] for s in snapshots],
        "snapshots": snapshots,
        "snapshot_count": len(snapshots),
        "source": "synoptic_station_hybrid",
    }, None


def _select_snapshot(hybrid_data, time_index):
    snapshots = hybrid_data.get("snapshots") or []
    if not snapshots:
        return None, -1
    idx = time_index
    if idx is None:
        idx = len(snapshots) - 1
    idx = max(0, min(int(idx), len(snapshots) - 1))
    return snapshots[idx], idx

# ─── SONDEHUB FETCH ──────────────────────────────────────────────────────────
def fetch_all_balloons():
    try:
        print("Fetching all active balloons from SondeHub...")
        response = requests.get(
            "https://api.v2.sondehub.org/sondes/telemetry",
            params={"duration": "1h"},
            timeout=30
        )
        data = response.json()
        print(f"Got {len(data)} active balloons")
        return data
    except Exception as e:
        print(f"Fetch error: {e}")
        return {}


def fetch_balloon_path(serial, duration="6h"):
    try:
        response = requests.get(
            "https://api.v2.sondehub.org/sondes/telemetry",
            params={"serial": serial, "duration": duration},
            timeout=30
        )
        data = response.json()
        if serial in data:
            frames = sorted(data[serial].values(), key=lambda x: x["datetime"])
            return frames
        return []
    except Exception as e:
        print(f"Path fetch error: {e}")
        return []


# ─── ATMOSPHERIC CALCULATIONS ────────────────────────────────────────────────

def calc_lapse_rate(frames):
    """
    Calculate environmental lapse rate (deg C per km)
    Uses linear regression across all frames with temp + altitude
    """
    points = [(f["alt"], f["temp"]) for f in frames
              if f.get("temp") is not None and f.get("alt") is not None]
    if len(points) < 5:
        return None

    points.sort(key=lambda x: x[0])
    n = len(points)
    alts = [p[0] / 1000 for p in points]  # convert to km
    temps = [p[1] for p in points]

    mean_alt = sum(alts) / n
    mean_temp = sum(temps) / n
    numerator = sum((alts[i] - mean_alt) * (temps[i] - mean_temp) for i in range(n))
    denominator = sum((alts[i] - mean_alt) ** 2 for i in range(n))

    if denominator == 0:
        return None

    slope = numerator / denominator  # deg C per km
    return round(-slope, 3)  # positive = normal cooling with altitude


def find_tropopause(frames):
    """
    Detect tropopause by finding where temperature stops decreasing
    Tropopause = sustained temp increase after a period of cooling
    Returns altitude in meters
    """
    points = [(f["alt"], f["temp"]) for f in frames
              if f.get("temp") is not None and f.get("alt") is not None]
    if len(points) < 10:
        return None

    points.sort(key=lambda x: x[0])

    # Look for where lapse rate reverses above 8km
    for i in range(len(points) - 3):
        alt = points[i][0]
        if alt < 8000:
            continue
        # Check if temp starts increasing over next few points
        t0 = points[i][1]
        t1 = points[i + 1][1]
        t2 = points[i + 2][1]
        if t1 > t0 and t2 > t1:
            return round(alt)

    return None


def calc_wind_profile(frames):
    """
    Calculate wind speed and direction from GPS drift between frames
    Returns list of {alt, speed_ms, direction_deg}
    """
    winds = []
    sorted_frames = sorted(frames, key=lambda x: x["datetime"])

    for i in range(1, len(sorted_frames)):
        f0 = sorted_frames[i - 1]
        f1 = sorted_frames[i]

        if not all(k in f0 and k in f1 for k in ["lat", "lon", "alt", "datetime"]):
            continue

        # Time difference in seconds
        try:
            t0 = datetime.fromisoformat(f0["datetime"].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(f1["datetime"].replace("Z", "+00:00"))
            dt = (t1 - t0).total_seconds()
        except:
            continue

        if dt <= 0 or dt > 120:
            continue

        # Distance in meters using haversine
        lat0, lon0 = math.radians(f0["lat"]), math.radians(f0["lon"])
        lat1, lon1 = math.radians(f1["lat"]), math.radians(f1["lon"])
        dlat = lat1 - lat0
        dlon = lon1 - lon0
        a = math.sin(dlat/2)**2 + math.cos(lat0) * math.cos(lat1) * math.sin(dlon/2)**2
        dist = 6371000 * 2 * math.asin(math.sqrt(a))

        speed = dist / dt  # m/s

        # Direction
        y = math.sin(dlon) * math.cos(lat1)
        x = math.cos(lat0) * math.sin(lat1) - math.sin(lat0) * math.cos(lat1) * math.cos(dlon)
        bearing = (math.degrees(math.atan2(y, x)) + 360) % 360

        winds.append({
            "alt": round((f0["alt"] + f1["alt"]) / 2),
            "speed_ms": round(speed, 2),
            "speed_knots": round(speed * 1.94384, 1),
            "direction_deg": round(bearing, 1)
        })

    return winds


def calc_cape_cin(frames):
    """
    Simplified CAPE/CIN estimation from temperature profile.
    Uses parcel theory - compares environmental temp to lifted parcel temp.
    Returns CAPE (J/kg) and CIN (J/kg) estimates and a risk label.
    """
    points = [(f["alt"], f["temp"]) for f in frames
              if f.get("temp") is not None and f.get("alt") is not None]
    if len(points) < 5:
        return None, None, "insufficient data"

    points.sort(key=lambda x: x[0])

    # Surface conditions
    surface_temp = points[0][1]
    surface_alt = points[0][0]

    # Dry adiabatic lapse rate = 9.8 C/km
    # Moist adiabatic lapse rate = ~6 C/km
    DALR = 9.8
    MALR = 6.0

    cape = 0.0
    cin = 0.0
    g = 9.81

    # Assume LCL (lifting condensation level) at ~500m above surface
    lcl_alt = surface_alt + 500

    for i in range(len(points) - 1):
        alt0, temp_env0 = points[i]
        alt1, temp_env1 = points[i + 1]
        dz = (alt1 - alt0)
        mid_alt = (alt0 + alt1) / 2
        mid_env_temp = (temp_env0 + temp_env1) / 2

        # Parcel temperature at this altitude
        if mid_alt < lcl_alt:
            parcel_temp = surface_temp - DALR * (mid_alt - surface_alt) / 1000
        else:
            parcel_temp = surface_temp - DALR * (lcl_alt - surface_alt) / 1000
            parcel_temp -= MALR * (mid_alt - lcl_alt) / 1000

        temp_diff = parcel_temp - mid_env_temp

        if temp_diff > 0:
            cape += g * (temp_diff / (mid_env_temp + 273.15)) * dz
        else:
            if cape == 0:
                cin += g * (temp_diff / (mid_env_temp + 273.15)) * dz

    cape = round(max(cape, 0), 1)
    cin = round(cin, 1)

    if cape < 300:
        risk = "low — stable atmosphere, storms unlikely"
    elif cape < 1000:
        risk = "moderate — isolated storm possible"
    elif cape < 2500:
        risk = "high — scattered storms likely"
    else:
        risk = "extreme — severe storm threat"

    return cape, cin, risk


def calc_precipitable_water(frames):
    """
    Estimate total precipitable water from humidity profile.
    Returns mm of precipitable water.
    """
    points = [(f["alt"], f.get("humidity"), f.get("temp")) for f in frames
              if f.get("humidity") is not None and f.get("temp") is not None]
    if len(points) < 3:
        return None

    points.sort(key=lambda x: x[0])
    pw = 0.0

    for i in range(len(points) - 1):
        alt0, rh0, t0 = points[i]
        alt1, rh1, t1 = points[i + 1]
        dz = (alt1 - alt0) / 1000  # km

        # Saturation vapor pressure (Tetens formula)
        es0 = 6.112 * math.exp(17.67 * t0 / (t0 + 243.5))
        es1 = 6.112 * math.exp(17.67 * t1 / (t1 + 243.5))

        # Actual vapor pressure
        e0 = (rh0 / 100) * es0
        e1 = (rh1 / 100) * es1

        pw += ((e0 + e1) / 2) * dz * 1.5  # rough scaling factor

    return round(pw, 1)


def generate_forecast(serial, analysis):
    """
    Generate a plain English forecast card from analysis data.
    """
    cape = analysis.get("cape")
    cin = analysis.get("cin")
    lapse = analysis.get("lapse_rate_c_per_km")
    tropo = analysis.get("tropopause_alt_m")
    pw = analysis.get("precipitable_water_mm")
    risk = analysis.get("storm_risk", "unknown")
    winds = analysis.get("wind_profile", [])

    forecast = {
        "serial": serial,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "storm_risk": risk,
        "summary": "",
        "details": []
    }

    details = []

    # Storm risk
    if cape is not None:
        details.append(f"CAPE is {cape} J/kg — {risk}.")

    # Atmospheric stability
    if lapse is not None:
        if lapse > 9:
            details.append(f"Lapse rate of {lapse} C/km indicates an unstable atmosphere — convection likely this afternoon.")
        elif lapse > 6:
            details.append(f"Lapse rate of {lapse} C/km is near neutral — atmosphere is conditionally unstable.")
        else:
            details.append(f"Lapse rate of {lapse} C/km indicates a stable, capped atmosphere.")

    # Tropopause
    if tropo is not None:
        tropo_km = round(tropo / 1000, 1)
        if tropo_km > 14:
            details.append(f"Tropopause detected at {tropo_km} km — deep atmosphere, consistent with warm season convection.")
        elif tropo_km > 10:
            details.append(f"Tropopause at {tropo_km} km — typical for mid-latitudes.")
        else:
            details.append(f"Low tropopause at {tropo_km} km — cold air mass in place.")

    # Precipitable water
    if pw is not None:
        if pw > 40:
            details.append(f"Precipitable water of {pw} mm is very high — heavy rainfall possible if storms develop.")
        elif pw > 25:
            details.append(f"Precipitable water of {pw} mm is moderate — decent moisture available.")
        else:
            details.append(f"Precipitable water of {pw} mm is low — limited rainfall potential.")

    # Jet stream / upper winds
    upper_winds = [w for w in winds if w["alt"] > 9000]
    if upper_winds:
        avg_jet = round(sum(w["speed_ms"] for w in upper_winds) / len(upper_winds), 1)
        avg_dir = round(sum(w["direction_deg"] for w in upper_winds) / len(upper_winds), 1)
        details.append(f"Upper level winds averaging {avg_jet} m/s from {avg_dir} degrees.")

    forecast["details"] = details
    forecast["summary"] = details[0] if details else "Insufficient data for forecast."

    return forecast


# ─── BACKGROUND POLLER ───────────────────────────────────────────────────────
def poll_sondehub():
    while True:
        data = fetch_all_balloons()
        processed = {}

        for serial, frames in data.items():
            sorted_frames = sorted(frames.values(), key=lambda x: x["datetime"])
            latest = sorted_frames[-1]
            processed[serial] = {
                "serial": serial,
                "lat": latest.get("lat"),
                "lon": latest.get("lon"),
                "alt": latest.get("alt"),
                "temp": latest.get("temp"),
                "humidity": latest.get("humidity"),
                "vel_v": latest.get("vel_v"),
                "freq": latest.get("frequency"),
                "type": latest.get("type"),
                "datetime": latest.get("datetime"),
                "frame_count": len(sorted_frames)
            }

        cache["balloons"] = processed
        cache["last_updated"] = datetime.now(timezone.utc).isoformat()
        print(f"Cache updated — {len(processed)} balloons at {cache['last_updated']}")

        socketio.emit("balloons_update", list(processed.values()))
        time.sleep(30)


# ─── FLASK ROUTES ────────────────────────────────────────────────────────────

@app.route("/balloons")
def get_balloons():
    return jsonify({
        "count": len(cache["balloons"]),
        "last_updated": cache["last_updated"],
        "balloons": list(cache["balloons"].values())
    })


@app.route("/balloon/<serial>")
def get_balloon_path(serial):
    frames = fetch_balloon_path(serial)
    if not frames:
        return jsonify({"error": "No data found"}), 404

    path = [{
        "lat": f.get("lat"),
        "lon": f.get("lon"),
        "alt": f.get("alt"),
        "temp": f.get("temp"),
        "humidity": f.get("humidity"),
        "datetime": f.get("datetime"),
        "vel_v": f.get("vel_v")
    } for f in frames]

    return jsonify({
        "serial": serial,
        "point_count": len(path),
        "path": path
    })


@app.route("/balloon/<serial>/analysis")
def get_balloon_analysis(serial):
    frames = fetch_balloon_path(serial, duration="6h")
    if not frames:
        return jsonify({"error": "No data found"}), 404

    lapse = calc_lapse_rate(frames)
    tropo = find_tropopause(frames)
    winds = calc_wind_profile(frames)
    cape, cin, risk = calc_cape_cin(frames)
    pw = calc_precipitable_water(frames)

    analysis = {
        "serial": serial,
        "frame_count": len(frames),
        "lapse_rate_c_per_km": lapse,
        "tropopause_alt_m": tropo,
        "tropopause_alt_km": round(tropo / 1000, 1) if tropo else None,
        "cape": cape,
        "cin": cin,
        "storm_risk": risk,
        "precipitable_water_mm": pw,
        "wind_profile": winds,
        "surface_temp": frames[0].get("temp") if frames else None,
        "max_alt": max(f.get("alt", 0) for f in frames),
        "sonde_type": frames[0].get("type") if frames else None
    }

    return jsonify(analysis)


@app.route("/balloon/<serial>/forecast")
def get_balloon_forecast(serial):
    frames = fetch_balloon_path(serial, duration="6h")
    if not frames:
        return jsonify({"error": "No data found"}), 404

    lapse = calc_lapse_rate(frames)
    tropo = find_tropopause(frames)
    winds = calc_wind_profile(frames)
    cape, cin, risk = calc_cape_cin(frames)
    pw = calc_precipitable_water(frames)

    analysis = {
        "cape": cape,
        "cin": cin,
        "storm_risk": risk,
        "lapse_rate_c_per_km": lapse,
        "tropopause_alt_m": tropo,
        "precipitable_water_mm": pw,
        "wind_profile": winds
    }

    forecast = generate_forecast(serial, analysis)
    return jsonify(forecast)


@app.route("/balloon/<serial>/telemetry")
def get_balloon_telemetry(serial):
    frames = fetch_balloon_path(serial, duration="6h")
    if not frames:
        return jsonify({"error": "No data found"}), 404

    return jsonify({
        "serial": serial,
        "frame_count": len(frames),
        "first_seen": frames[0].get("datetime"),
        "last_seen": frames[-1].get("datetime"),
        "max_altitude_m": max(f.get("alt", 0) for f in frames),
        "min_temp_c": min((f.get("temp") for f in frames if f.get("temp") is not None), default=None),
        "sonde_type": frames[0].get("type"),
        "frequency_mhz": frames[0].get("frequency"),
        "frames": frames
    })


@app.route("/weather/<stid>")
def get_weather(stid):
    if not SYNOPTIC_TOKEN:
        return jsonify({"error": "Synoptic Data not configured. Set SYNOPTIC_TOKEN environment variable."}), 503

    data = fetch_station_data(stid)  # Latest observation
    if not data:
        return jsonify({"error": "No weather data found for station"}), 404

    return jsonify(data)


@app.route("/weather/<stid>/timeseries")
def get_weather_timeseries(stid):
    if not SYNOPTIC_TOKEN:
        return jsonify({"error": "Synoptic Data not configured. Set SYNOPTIC_TOKEN environment variable."}), 503

    recent = request.args.get('recent', default=120, type=int)
    variables = request.args.get('vars')
    if variables:
        variables = variables.split(',')

    data = fetch_station_timeseries(stid, recent_minutes=recent, variables=variables)
    if not data:
        return jsonify({"error": "No weather data found for station"}), 404

    parsed = parse_timeseries_for_assimilation(data)

    return jsonify({
        'raw': data,
        'parsed': parsed,
        'observation_count': len(parsed),
    })


@app.route("/weather/stations/search")
def search_weather_stations():
    if not SYNOPTIC_TOKEN:
        return jsonify({"error": "Synoptic Data not configured. Set SYNOPTIC_TOKEN environment variable."}), 503

    # Get optional parameters
    lat = request.args.get('lat', type=float)
    long = request.args.get('long', type=float) 
    radius = request.args.get('radius', type=float)
    network = request.args.get('network', type=int)
    query = request.args.get('q')
    limit = request.args.get('limit', default=40, type=int)
    if limit is None or limit <= 0:
        limit = 40

    # Call search with provided parameters
    data = search_stations(lat=lat, long=long, radius=radius, network=network)
    
    stations = data.get('STATION', [])

    # If state query provided, filter results.
    if query:
        stations = [s for s in stations if s.get('STATE') == query.upper()]

    return jsonify({"stations": stations[:limit]})


@app.route("/station/<stid>/profile")
def get_station_profile(stid):
    if not SYNOPTIC_TOKEN:
        return jsonify({"error": "Synoptic Data not configured. Set SYNOPTIC_TOKEN environment variable."}), 503

    recent = request.args.get('recent', default=180, type=int)
    time_index = request.args.get('time_index', default=None, type=int)
    hybrid, err = _build_station_hybrid_dataset(stid, recent_minutes=recent)
    if not hybrid:
        return jsonify({"error": err}), 404
    snapshot, idx = _select_snapshot(hybrid, time_index)
    if snapshot is None:
        return jsonify({"error": "No station snapshots available"}), 404

    return jsonify({
        "serial": stid,
        "point_count": len(snapshot["levels"]),
        "path": snapshot["levels"],
        "source": "synoptic_station_hybrid",
        "selected_time_index": idx,
        "selected_time": snapshot["datetime"],
        "times": hybrid["times"],
    })


@app.route("/station/<stid>/analysis")
def get_station_analysis(stid):
    if not SYNOPTIC_TOKEN:
        return jsonify({"error": "Synoptic Data not configured. Set SYNOPTIC_TOKEN environment variable."}), 503

    recent = request.args.get('recent', default=180, type=int)
    time_index = request.args.get('time_index', default=None, type=int)
    hybrid, err = _build_station_hybrid_dataset(stid, recent_minutes=recent)
    if not hybrid:
        return jsonify({"error": err}), 404
    snapshot, idx = _select_snapshot(hybrid, time_index)
    if snapshot is None:
        return jsonify({"error": "No station snapshots available"}), 404

    analysis = dict(snapshot["analysis"])
    analysis["selected_time_index"] = idx
    analysis["selected_time"] = snapshot["datetime"]
    analysis["times"] = hybrid["times"]
    return jsonify(analysis)


@app.route("/station/<stid>/hybrid")
def get_station_hybrid(stid):
    if not SYNOPTIC_TOKEN:
        return jsonify({"error": "Synoptic Data not configured. Set SYNOPTIC_TOKEN environment variable."}), 503

    recent = request.args.get('recent', default=180, type=int)
    hybrid, err = _build_station_hybrid_dataset(stid, recent_minutes=recent)
    if not hybrid:
        return jsonify({"error": err}), 404

    return jsonify(hybrid)


@app.route("/status")
def status():
    return jsonify({
        "status": "running",
        "active_balloons": len(cache["balloons"]),
        "last_updated": cache["last_updated"],
        "synoptic_available": SYNOPTIC_TOKEN is not None
    })


# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    poll_thread = threading.Thread(target=poll_sondehub, daemon=True)
    poll_thread.start()

    print("Starting pipeline server on port 8080...")
    print("Endpoints:")
    print("  GET /balloons                    — all active balloons")
    print("  GET /balloon/<serial>            — full flight path")
    print("  GET /balloon/<serial>/analysis   — CAPE, lapse rate, winds, tropopause")
    print("  GET /balloon/<serial>/forecast   — plain English forecast card")
    print("  GET /balloon/<serial>/telemetry  — raw telemetry frames")
    # Synoptic commands
    print("  GET /weather/<stid>              — latest weather for station")
    print("  GET /weather/<stid>/timeseries   — weather time series (?recent=120&vars=temp,wind)")
    print("  GET /weather/stations/search?q=UT&lat=40.7&long=-111.9&radius=50&network=1 — search weather stations")

    print("  GET /status                      — server health check")

    socketio.run(app, host="0.0.0.0", port=8080, allow_unsafe_werkzeug=True)
