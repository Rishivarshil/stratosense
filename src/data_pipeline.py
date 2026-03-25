import requests
import math
import json
from flask import Flask, jsonify
from flask_socketio import SocketIO
from datetime import datetime, timezone
import threading
import time

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# ─── CACHE ───────────────────────────────────────────────────────────────────
cache = {
    "balloons": {},
    "last_updated": None
}

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


@app.route("/status")
def status():
    return jsonify({
        "status": "running",
        "active_balloons": len(cache["balloons"]),
        "last_updated": cache["last_updated"]
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
    print("  GET /status                      — server health check")

    socketio.run(app, host="0.0.0.0", port=8080, allow_unsafe_werkzeug=True)
