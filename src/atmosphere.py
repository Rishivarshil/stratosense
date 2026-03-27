"""
Combined atmospheric profile generator and Flask routes.

Ties together interpolation + assimilation and exposes four endpoints:
  /atmosphere/profile          — full vertical profile, surface to 30 km
  /atmosphere/at/<altitude_m>  — single-altitude query for drone planning
  /atmosphere/density_altitude — current density altitude at ground level
  /atmosphere/status           — model mode, confidence, lapse rate source

Can run standalone (port 8081) for testing, or be registered as a Blueprint
on Person 1's Flask app via:
    from atmosphere import atmosphere_bp
    app.register_blueprint(atmosphere_bp)
"""

from flask import Blueprint, Flask, jsonify
from datetime import datetime, timezone
import os
import math

from interpolation import (
    baseline_profile,
    generate_full_profile,
    calc_density_altitude,
)
from assimilation import update_lapse_rates, apply_observation_nudging

# ─── BLUEPRINT ───────────────────────────────────────────────────────────────

atmosphere_bp = Blueprint('atmosphere', __name__)

# ─── DATA ACCESS ─────────────────────────────────────────────────────────────
# These pull from Person 1's existing Synoptic + SondeHub integrations.
# When the data-gathering layer isn't available yet they fall back to
# hardcoded Columbus KCMH defaults so the model still runs.

_surface_cache = {
    'data': None,
    'fetched_at': None,
}

TARGET_LAT = float(os.getenv('MODEL_TARGET_LAT', '39.99'))
TARGET_LON = float(os.getenv('MODEL_TARGET_LON', '-83.01'))
SURFACE_SEARCH_RADIUS_KM = float(os.getenv('SURFACE_SEARCH_RADIUS_KM', '150'))
MAX_SURFACE_STATIONS = int(os.getenv('MAX_SURFACE_STATIONS', '8'))
MAX_BALLOON_DISTANCE_KM = float(os.getenv('MAX_BALLOON_DISTANCE_KM', '250'))
MAX_BALLOONS_FOR_ASSIM = int(os.getenv('MAX_BALLOONS_FOR_ASSIM', '3'))

COLUMBUS_DEFAULTS = {
    'station_id': 'KCMH',
    'temp_c': 15.0,
    'dewpoint_c': 8.0,
    'pressure_hpa': 1013.25,
    'elev_m': 247,
    'wind_speed_ms': 3.0,
    'wind_dir_deg': 225,
    'lat': TARGET_LAT,
    'lon': TARGET_LON,
}


def _haversine_km(lat0, lon0, lat1, lon1):
    """Great-circle distance between two lat/lon points in km."""
    r = 6371.0
    lat0_r = math.radians(lat0)
    lon0_r = math.radians(lon0)
    lat1_r = math.radians(lat1)
    lon1_r = math.radians(lon1)
    dlat = lat1_r - lat0_r
    dlon = lon1_r - lon0_r
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat0_r) * math.cos(lat1_r) * math.sin(dlon / 2) ** 2)
    return r * 2 * math.asin(math.sqrt(a))


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _solve_linear_system(a, b):
    """Dense Gaussian elimination with partial pivoting."""
    n = len(a)
    m = [row[:] + [b_i] for row, b_i in zip(a, b)]

    for i in range(n):
        pivot = max(range(i, n), key=lambda r: abs(m[r][i]))
        if abs(m[pivot][i]) < 1e-12:
            return None
        if pivot != i:
            m[i], m[pivot] = m[pivot], m[i]

        for r in range(i + 1, n):
            factor = m[r][i] / m[i][i]
            if factor == 0:
                continue
            for c in range(i, n + 1):
                m[r][c] -= factor * m[i][c]

    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        rhs = m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))
        if abs(m[i][i]) < 1e-12:
            return None
        x[i] = rhs / m[i][i]
    return x


def _empirical_covariance(d_km, sill, range_km, nugget):
    # Exponential variogram -> covariance.
    gamma = nugget + sill * (1.0 - math.exp(-max(d_km, 0.0) / max(range_km, 1.0)))
    return (sill + nugget) - gamma


def _ordinary_kriging_value(samples, target_lat, target_lon):
    """
    Ordinary Kriging estimate at target point.
    samples: list of {'lat', 'lon', 'value'}
    """
    if not samples:
        return None
    if len(samples) == 1:
        return samples[0]['value']

    values = [s['value'] for s in samples]
    v_min = min(values)
    v_max = max(values)
    variance_span = max(v_max - v_min, 1e-3)

    distances = []
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            distances.append(_haversine_km(
                samples[i]['lat'], samples[i]['lon'],
                samples[j]['lat'], samples[j]['lon'],
            ))
    mean_dist = (sum(distances) / len(distances)) if distances else 25.0
    range_km = max(mean_dist * 1.5, 10.0)
    sill = variance_span ** 2
    nugget = 0.05 * sill

    n = len(samples)
    a = [[0.0] * (n + 1) for _ in range(n + 1)]
    b = [0.0] * (n + 1)

    for i in range(n):
        for j in range(n):
            if i == j:
                a[i][j] = sill + nugget
            else:
                d_ij = _haversine_km(
                    samples[i]['lat'], samples[i]['lon'],
                    samples[j]['lat'], samples[j]['lon'],
                )
                a[i][j] = _empirical_covariance(d_ij, sill, range_km, nugget)
        a[i][n] = 1.0
        a[n][i] = 1.0
        d_i0 = _haversine_km(samples[i]['lat'], samples[i]['lon'], target_lat, target_lon)
        b[i] = _empirical_covariance(d_i0, sill, range_km, nugget)

    a[n][n] = 0.0
    b[n] = 1.0

    solution = _solve_linear_system(a, b)
    if not solution:
        weighted = []
        for s in samples:
            d = max(_haversine_km(s['lat'], s['lon'], target_lat, target_lon), 0.5)
            weighted.append((1.0 / (d ** 2), s['value']))
        w_sum = sum(w for w, _ in weighted)
        return sum(w * v for w, v in weighted) / w_sum if w_sum > 0 else None

    lambdas = solution[:n]
    return sum(lambdas[i] * samples[i]['value'] for i in range(n))


def _parse_synoptic_obs(raw):
    """
    Extract surface dict from the Synoptic /v2/stations/latest response
    that Person 1's fetch_station_data() returns.
    """
    try:
        station = raw['STATION'][0]
        obs = station.get('OBSERVATIONS', {})

        temp = obs.get('air_temp_value_1', {}).get('value')
        dewpoint = obs.get('dew_point_temperature_value_1', {}).get('value')
        pressure = obs.get('sea_level_pressure_value_1', {}).get('value')
        wind_speed = obs.get('wind_speed_value_1', {}).get('value')
        wind_dir = obs.get('wind_direction_value_1', {}).get('value')
        elev = station.get('ELEVATION')
        lat = _to_float(station.get('LATITUDE'))
        lon = _to_float(station.get('LONGITUDE'))

        if temp is None:
            return None

        return {
            'station_id': station.get('STID', 'KCMH'),
            'temp_c': float(temp),
            'dewpoint_c': float(dewpoint) if dewpoint is not None else float(temp) - 7,
            'pressure_hpa': float(pressure) if pressure is not None else 1013.25,
            'elev_m': float(elev) if elev is not None else 247,
            'wind_speed_ms': float(wind_speed) if wind_speed is not None else 3.0,
            'wind_dir_deg': float(wind_dir) if wind_dir is not None else 225,
            'lat': lat if lat is not None else TARGET_LAT,
            'lon': lon if lon is not None else TARGET_LON,
        }
    except (KeyError, IndexError, TypeError):
        return None


def _kriging_surface_from_stations(stations):
    if not stations:
        return None

    first = stations[0]
    base = {
        'station_id': first.get('station_id', 'MULTI'),
        'temp_c': first.get('temp_c', COLUMBUS_DEFAULTS['temp_c']),
        'dewpoint_c': first.get('dewpoint_c', COLUMBUS_DEFAULTS['dewpoint_c']),
        'pressure_hpa': first.get('pressure_hpa', COLUMBUS_DEFAULTS['pressure_hpa']),
        'elev_m': first.get('elev_m', COLUMBUS_DEFAULTS['elev_m']),
        'wind_speed_ms': first.get('wind_speed_ms', COLUMBUS_DEFAULTS['wind_speed_ms']),
        'wind_dir_deg': first.get('wind_dir_deg', COLUMBUS_DEFAULTS['wind_dir_deg']),
        'lat': TARGET_LAT,
        'lon': TARGET_LON,
    }

    scalar_keys = ['temp_c', 'dewpoint_c', 'pressure_hpa', 'elev_m', 'wind_speed_ms']
    for key in scalar_keys:
        samples = []
        for s in stations:
            val = s.get(key)
            lat = s.get('lat')
            lon = s.get('lon')
            if val is None or lat is None or lon is None:
                continue
            samples.append({'lat': lat, 'lon': lon, 'value': float(val)})
        if samples:
            estimate = _ordinary_kriging_value(samples, TARGET_LAT, TARGET_LON)
            if estimate is not None:
                base[key] = estimate

    dir_samples = []
    for s in stations:
        wd = s.get('wind_dir_deg')
        ws = s.get('wind_speed_ms')
        lat = s.get('lat')
        lon = s.get('lon')
        if wd is None or ws is None or lat is None or lon is None:
            continue
        wd_rad = math.radians(float(wd))
        dir_samples.append({
            'lat': lat,
            'lon': lon,
            'u': -float(ws) * math.sin(wd_rad),
            'v': -float(ws) * math.cos(wd_rad),
        })
    if dir_samples:
        u_est = _ordinary_kriging_value(
            [{'lat': d['lat'], 'lon': d['lon'], 'value': d['u']} for d in dir_samples],
            TARGET_LAT, TARGET_LON)
        v_est = _ordinary_kriging_value(
            [{'lat': d['lat'], 'lon': d['lon'], 'value': d['v']} for d in dir_samples],
            TARGET_LAT, TARGET_LON)
        if u_est is not None and v_est is not None:
            speed = math.sqrt(u_est ** 2 + v_est ** 2)
            direction = (math.degrees(math.atan2(-u_est, -v_est)) + 360) % 360
            base['wind_speed_ms'] = speed
            base['wind_dir_deg'] = direction

    base['station_id'] = f"KRIGING_{len(stations)}"
    return base


def get_latest_surface_obs():
    """
    Try to fetch live surface data from Person 1's Synoptic endpoint.
    Falls back to hardcoded Columbus defaults.
    """
    import requests

    now = datetime.now(timezone.utc)

    if (_surface_cache['data'] is not None
            and _surface_cache['fetched_at'] is not None
            and (now - _surface_cache['fetched_at']).total_seconds() < 300):
        return dict(_surface_cache['data'])

    try:
        station_resp = requests.get(
            'http://localhost:8080/weather/stations/search',
            params={
                'lat': TARGET_LAT,
                'long': TARGET_LON,
                'radius': SURFACE_SEARCH_RADIUS_KM,
                'limit': MAX_SURFACE_STATIONS,
            },
            timeout=8,
        )
        if station_resp.ok:
            candidates = station_resp.json().get('stations', [])
            stations = []
            for s in candidates:
                stid = s.get('STID')
                if not stid:
                    continue
                obs_resp = requests.get(f'http://localhost:8080/weather/{stid}', timeout=6)
                if not obs_resp.ok:
                    continue
                parsed = _parse_synoptic_obs(obs_resp.json())
                if not parsed:
                    continue
                parsed['lat'] = _to_float(parsed.get('lat') or s.get('LATITUDE'))
                parsed['lon'] = _to_float(parsed.get('lon') or s.get('LONGITUDE'))
                if parsed['lat'] is None or parsed['lon'] is None:
                    continue
                stations.append(parsed)
                if len(stations) >= MAX_SURFACE_STATIONS:
                    break

            kriged = _kriging_surface_from_stations(stations)
            if kriged:
                _surface_cache['data'] = kriged
                _surface_cache['fetched_at'] = now
                return dict(kriged)
    except Exception:
        pass

    return dict(COLUMBUS_DEFAULTS)


def get_latest_balloon_data(target_lat=TARGET_LAT, target_lon=TARGET_LON):
    """
    Grab nearby balloon frames from Person 1's cache.
    Returns merged frames from nearby balloons, or None.
    """
    import requests

    try:
        resp = requests.get(
            'http://localhost:8080/balloons', timeout=5)
        if not resp.ok:
            return None
        balloons = resp.json().get('balloons', [])
        if not balloons:
            return None

        nearby = []
        for b in balloons:
            lat = b.get('lat')
            lon = b.get('lon')
            serial = b.get('serial')
            if lat is None or lon is None or not serial:
                continue
            dist_km = _haversine_km(float(lat), float(lon), target_lat, target_lon)
            if dist_km <= MAX_BALLOON_DISTANCE_KM:
                nearby.append((dist_km, serial))

        nearby.sort(key=lambda x: x[0])
        selected = nearby[:MAX_BALLOONS_FOR_ASSIM] if nearby else []
        if not selected:
            return None

        merged_frames = []
        for _, serial in selected:
            path_resp = requests.get(f'http://localhost:8080/balloon/{serial}', timeout=10)
            if not path_resp.ok:
                continue
            frames = path_resp.json().get('path', [])
            for f in frames:
                f['serial'] = serial
            merged_frames.extend(frames)

        return merged_frames or None
    except Exception:
        return None


def calc_balloon_age(balloon):
    """Hours since the last frame in the balloon data."""
    if not balloon:
        return None
    try:
        valid_times = [f.get('datetime') for f in balloon if f.get('datetime')]
        if not valid_times:
            return None
        last_dt = max(valid_times)
        if not last_dt:
            return None
        dt = datetime.fromisoformat(last_dt.replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return round(age, 2)
    except (ValueError, TypeError, IndexError):
        return None


# ─── SURFACE TIMESERIES FOR ASSIMILATION ─────────────────────────────────────

_ts_cache = {
    'data': None,
    'fetched_at': None,
}


def get_surface_timeseries(station='KCMH', recent_minutes=120):
    """
    Fetch the last 2 hours of surface observations from Person 1's
    timeseries endpoint and convert them into observation dicts the
    nudging pipeline can use as ground-level truth.

    Returns a list of {alt, temp, humidity, datetime} frame-like dicts
    at station elevation, so apply_observation_nudging can blend them
    with balloon data.
    """
    import requests

    now = datetime.now(timezone.utc)

    if (_ts_cache['data'] is not None
            and _ts_cache['fetched_at'] is not None
            and (now - _ts_cache['fetched_at']).total_seconds() < 300):
        return list(_ts_cache['data'])

    try:
        resp = requests.get(
            f'http://localhost:8080/weather/{station}/timeseries',
            params={'recent': recent_minutes},
            timeout=5,
        )
        if not resp.ok:
            return []

        parsed = resp.json().get('parsed', [])
        if not parsed:
            return []

        frames = []
        for obs in parsed:
            if obs.get('temp_c') is None:
                continue
            rh = None
            if obs.get('dewpoint_c') is not None and obs['temp_c'] is not None:
                from interpolation import calc_relative_humidity
                rh = calc_relative_humidity(obs['temp_c'], obs['dewpoint_c'])
                rh = max(0.0, min(100.0, rh))

            frames.append({
                'alt': obs['elev_m'],
                'temp': obs['temp_c'],
                'humidity': rh,
                'datetime': obs['datetime_utc'],
            })

        _ts_cache['data'] = frames
        _ts_cache['fetched_at'] = now
        return list(frames)
    except Exception:
        return []


# ─── ROUTES ──────────────────────────────────────────────────────────────────

@atmosphere_bp.route('/atmosphere/profile')
def atmosphere_profile():
    """Full atmospheric profile, surface to 30 km."""
    surface = get_latest_surface_obs()
    balloon = get_latest_balloon_data()
    surface_ts = get_surface_timeseries(
        station=surface.get('station_id', 'KCMH'))

    if balloon:
        surface = update_lapse_rates(surface, balloon)

    profile = generate_full_profile(surface)

    nudging_frames = (balloon or []) + surface_ts
    if nudging_frames:
        apply_observation_nudging(profile, nudging_frames)

    age = calc_balloon_age(balloon)
    serials = sorted({f.get('serial') for f in (balloon or []) if f.get('serial')})

    return jsonify({
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'surface_station': surface.get('station_id', 'KCMH'),
        'balloon_serial': serials[0] if len(serials) == 1 else None,
        'balloon_serials': serials,
        'balloon_age_hours': age,
        'assimilation_active': (
            (balloon is not None and age is not None and age < 6)
            or len(surface_ts) > 0),
        'surface_obs_count': len(surface_ts),
        'lapse_rate_source': (
            'observed' if surface.get('elr') is not None
            and surface.get('elr') != 6.5 else 'standard'),
        'elr_c_per_km': surface.get('elr', 6.5),
        'profile': profile,
    })


@atmosphere_bp.route('/atmosphere/at/<int:altitude_m>')
def atmosphere_at(altitude_m):
    """Single-altitude query for drone flight planning."""
    surface = get_latest_surface_obs()
    balloon = get_latest_balloon_data()
    if balloon:
        surface = update_lapse_rates(surface, balloon)
    level = baseline_profile(altitude_m, surface)
    return jsonify(level)


@atmosphere_bp.route('/atmosphere/density_altitude')
def density_altitude():
    """Current density altitude at ground level."""
    surface = get_latest_surface_obs()
    da = calc_density_altitude(
        surface['pressure_hpa'],
        surface['temp_c'],
        surface['dewpoint_c'],
    )
    return jsonify({
        'density_altitude_m': da,
        'density_altitude_ft': round(da * 3.281, 0),
        'conditions': {
            'temp_c': surface['temp_c'],
            'dewpoint_c': surface['dewpoint_c'],
            'pressure_hpa': surface['pressure_hpa'],
        },
    })


@atmosphere_bp.route('/atmosphere/status')
def atmosphere_status():
    """Model status for the dashboard header."""
    balloon = get_latest_balloon_data()
    age = calc_balloon_age(balloon)
    surface = get_latest_surface_obs()
    surface_ts = get_surface_timeseries(
        station=surface.get('station_id', 'KCMH'))

    if balloon:
        surface = update_lapse_rates(surface, balloon)

    has_balloon = age is not None and age < 6
    has_surface_ts = len(surface_ts) > 0

    if has_balloon:
        mode = 'assimilated'
    elif has_surface_ts:
        mode = 'surface-assimilated'
    else:
        mode = 'interpolated'

    if has_balloon and age < 1:
        confidence = 'high'
    elif has_balloon and age < 3:
        confidence = 'medium'
    elif has_surface_ts:
        confidence = 'surface-only'
    else:
        confidence = 'baseline'

    return jsonify({
        'mode': mode,
        'balloon_age_hours': age,
        'surface_obs_count': len(surface_ts),
        'lapse_rate_c_per_km': surface.get('elr', 6.5),
        'lapse_rate_source': 'observed' if surface.get('elr') else 'standard',
        'surface_station': surface.get('station_id', 'KCMH'),
        'confidence': confidence,
    })


# ─── STANDALONE SERVER ───────────────────────────────────────────────────────

if __name__ == '__main__':
    app = Flask(__name__)
    app.register_blueprint(atmosphere_bp)

    print('Starting atmospheric model server on port 8081...')
    print('Endpoints:')
    print('  GET /atmosphere/profile')
    print('  GET /atmosphere/at/<altitude_m>')
    print('  GET /atmosphere/density_altitude')
    print('  GET /atmosphere/status')
    app.run(host='0.0.0.0', port=8081, debug=True)
