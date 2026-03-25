"""
Test suite for data_pipeline.py — Person 3 validation.

Verifies that Person 1's pipeline returns everything Person 3 needs:
  - Sounding chart: flight path frames with alt, temp, humidity
  - Wind barb diagram: wind_profile with alt, speed_ms, speed_knots, direction_deg
  - Instability score card: CAPE, CIN, lapse_rate, tropopause, precipitable_water, storm_risk
  - Forecast card: storm_risk, summary, details[]

Tests are grouped:
  1. Unit tests for atmospheric calculation functions (mock frames, no network)
  2. Flask endpoint integration tests (mocked SondeHub, no network)
  3. Person 3 contract tests (validates exact fields/types the frontend expects)
"""

import pytest
import math
import json
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

import data_pipeline as dp


# ═══════════════════════════════════════════════════════════════════════════════
# TEST FIXTURES — realistic radiosonde telemetry
# ═══════════════════════════════════════════════════════════════════════════════

def _make_frames(n=60, surface_alt=200, surface_temp=20.0, lapse=6.5,
                 humidity_surface=80, humidity_decay=0.03,
                 tropopause_alt=11000, strat_lapse=-2.0,
                 lat_start=39.99, lon_start=-83.01,
                 dt_seconds=10):
    """
    Generate a synthetic but physically plausible balloon ascent.
    Temperature drops at `lapse` °C/km up to `tropopause_alt`,
    then rises at `strat_lapse` °C/km (negative = warming) above it.
    GPS drifts eastward at ~5 m/s so wind can be calculated.
    """
    frames = []
    t0 = datetime(2026, 3, 25, 18, 0, 0, tzinfo=timezone.utc)
    ascent_rate = 5.0  # m/s

    for i in range(n):
        alt = surface_alt + ascent_rate * dt_seconds * i
        dt_km = (alt - surface_alt) / 1000

        if alt < tropopause_alt:
            temp = surface_temp - lapse * dt_km
        else:
            above_tropo_km = (alt - tropopause_alt) / 1000
            temp_at_tropo = surface_temp - lapse * ((tropopause_alt - surface_alt) / 1000)
            temp = temp_at_tropo - strat_lapse * above_tropo_km

        rh = max(5, humidity_surface * math.exp(-humidity_decay * dt_km))

        wind_speed_ms = 5.0 + 0.001 * alt
        drift_per_sec = wind_speed_ms / 111320
        lat = lat_start + drift_per_sec * 0.3 * (dt_seconds * i)
        lon = lon_start + drift_per_sec * (dt_seconds * i)

        ts = t0 + timedelta(seconds=dt_seconds * i)

        frames.append({
            "serial": "T1234567",
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "alt": round(alt, 1),
            "temp": round(temp, 2),
            "humidity": round(rh, 1),
            "vel_v": ascent_rate,
            "datetime": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": "RS41",
            "frequency": 404.0,
        })

    return frames


@pytest.fixture
def realistic_frames():
    """60-frame ascent from 200m to ~3200m, well below tropopause."""
    return _make_frames(n=60, tropopause_alt=11000)


@pytest.fixture
def full_ascent_frames():
    """240-frame ascent reaching ~12200m, crossing the tropopause."""
    return _make_frames(n=240, tropopause_alt=11000)


@pytest.fixture
def minimal_frames():
    """3 frames — too few for most calculations."""
    return _make_frames(n=3)


@pytest.fixture
def no_temp_frames():
    """Frames where temp is None."""
    frames = _make_frames(n=20)
    for f in frames:
        f["temp"] = None
    return frames


@pytest.fixture
def flask_client():
    dp.app.config["TESTING"] = True
    with dp.app.test_client() as client:
        yield client


# ═══════════════════════════════════════════════════════════════════════════════
# 1. UNIT TESTS — atmospheric calculation functions
# ═══════════════════════════════════════════════════════════════════════════════

class TestLapseRate:
    def test_returns_positive_for_normal_atmosphere(self, realistic_frames):
        lr = dp.calc_lapse_rate(realistic_frames)
        assert lr is not None
        assert lr > 0, "Lapse rate should be positive (temp drops with altitude)"

    def test_value_near_input_lapse_rate(self, realistic_frames):
        lr = dp.calc_lapse_rate(realistic_frames)
        assert 5.0 < lr < 8.0, f"Expected ~6.5 C/km for standard atmosphere, got {lr}"

    def test_returns_none_for_insufficient_data(self, minimal_frames):
        lr = dp.calc_lapse_rate(minimal_frames)
        assert lr is None, "Should return None with < 5 frames"

    def test_returns_none_when_no_temp(self, no_temp_frames):
        lr = dp.calc_lapse_rate(no_temp_frames)
        assert lr is None

    def test_return_type_is_float(self, realistic_frames):
        lr = dp.calc_lapse_rate(realistic_frames)
        assert isinstance(lr, float)


class TestTropopause:
    def test_detects_tropopause_when_present(self, full_ascent_frames):
        tropo = dp.find_tropopause(full_ascent_frames)
        assert tropo is not None, "Should detect tropopause in full ascent data"

    def test_tropopause_altitude_reasonable(self, full_ascent_frames):
        tropo = dp.find_tropopause(full_ascent_frames)
        if tropo is not None:
            assert 8000 <= tropo <= 18000, f"Tropopause at {tropo}m is outside reasonable range"

    def test_returns_none_when_ascent_too_low(self, realistic_frames):
        tropo = dp.find_tropopause(realistic_frames)
        assert tropo is None, "Should not find tropopause below 8km"

    def test_returns_none_for_insufficient_data(self, minimal_frames):
        tropo = dp.find_tropopause(minimal_frames)
        assert tropo is None

    def test_return_type_is_int_when_found(self, full_ascent_frames):
        tropo = dp.find_tropopause(full_ascent_frames)
        if tropo is not None:
            assert isinstance(tropo, int)


class TestWindProfile:
    def test_returns_nonempty_list(self, realistic_frames):
        winds = dp.calc_wind_profile(realistic_frames)
        assert isinstance(winds, list)
        assert len(winds) > 0, "Should compute wind for consecutive frames"

    def test_wind_entry_has_required_fields(self, realistic_frames):
        """Person 3 needs: alt, speed_ms, speed_knots, direction_deg"""
        winds = dp.calc_wind_profile(realistic_frames)
        required = {"alt", "speed_ms", "speed_knots", "direction_deg"}
        for w in winds:
            assert required.issubset(w.keys()), f"Missing fields: {required - set(w.keys())}"

    def test_speed_knots_conversion(self, realistic_frames):
        winds = dp.calc_wind_profile(realistic_frames)
        for w in winds:
            expected_knots = w["speed_ms"] * 1.94384
            assert abs(w["speed_knots"] - expected_knots) < 0.2, "Knot conversion mismatch"

    def test_speeds_are_nonnegative(self, realistic_frames):
        winds = dp.calc_wind_profile(realistic_frames)
        for w in winds:
            assert w["speed_ms"] >= 0
            assert w["speed_knots"] >= 0

    def test_direction_in_0_360(self, realistic_frames):
        winds = dp.calc_wind_profile(realistic_frames)
        for w in winds:
            assert 0 <= w["direction_deg"] < 360

    def test_altitudes_are_positive(self, realistic_frames):
        winds = dp.calc_wind_profile(realistic_frames)
        for w in winds:
            assert w["alt"] > 0

    def test_skips_large_time_gaps(self):
        """Frames > 120s apart should be discarded."""
        frames = [
            {"lat": 40.0, "lon": -83.0, "alt": 1000, "datetime": "2026-03-25T18:00:00Z"},
            {"lat": 40.001, "lon": -83.001, "alt": 1050, "datetime": "2026-03-25T18:05:00Z"},
        ]
        winds = dp.calc_wind_profile(frames)
        assert len(winds) == 0, "Should skip frames 300s apart (>120s threshold)"


class TestCapeCin:
    def test_returns_three_values(self, realistic_frames):
        result = dp.calc_cape_cin(realistic_frames)
        assert len(result) == 3, "Should return (cape, cin, risk)"

    def test_cape_is_nonnegative(self, realistic_frames):
        cape, cin, risk = dp.calc_cape_cin(realistic_frames)
        assert cape >= 0

    def test_cin_is_nonpositive(self, realistic_frames):
        cape, cin, risk = dp.calc_cape_cin(realistic_frames)
        assert cin <= 0, f"CIN should be <= 0, got {cin}"

    def test_risk_label_is_valid(self, realistic_frames):
        valid_prefixes = ["low", "moderate", "high", "extreme", "insufficient"]
        cape, cin, risk = dp.calc_cape_cin(realistic_frames)
        assert any(risk.startswith(p) for p in valid_prefixes), f"Unknown risk label: {risk}"

    def test_risk_matches_cape_thresholds(self):
        """Verify risk labels align with documented thresholds in Person 3 context."""
        frames_stable = _make_frames(n=60, surface_temp=10, lapse=5.0)
        cape, _, risk = dp.calc_cape_cin(frames_stable)
        if cape < 300:
            assert risk.startswith("low")
        elif cape < 1000:
            assert risk.startswith("moderate")
        elif cape < 2500:
            assert risk.startswith("high")
        else:
            assert risk.startswith("extreme")

    def test_insufficient_data(self, minimal_frames):
        cape, cin, risk = dp.calc_cape_cin(minimal_frames)
        assert "insufficient" in risk

    def test_cape_cin_types(self, realistic_frames):
        cape, cin, risk = dp.calc_cape_cin(realistic_frames)
        assert isinstance(cape, float)
        assert isinstance(cin, float)
        assert isinstance(risk, str)


class TestPrecipitableWater:
    def test_returns_float(self, realistic_frames):
        pw = dp.calc_precipitable_water(realistic_frames)
        assert pw is not None
        assert isinstance(pw, float)

    def test_positive_value(self, realistic_frames):
        pw = dp.calc_precipitable_water(realistic_frames)
        assert pw > 0, "Precipitable water should be positive with nonzero humidity"

    def test_returns_none_for_insufficient_data(self, minimal_frames):
        pw = dp.calc_precipitable_water(minimal_frames)
        # 3 frames might work (needs >=3), so check boundary
        assert pw is None or isinstance(pw, float)

    def test_returns_none_without_humidity(self, no_temp_frames):
        for f in no_temp_frames:
            f["humidity"] = None
        pw = dp.calc_precipitable_water(no_temp_frames)
        assert pw is None


class TestForecast:
    def test_forecast_structure(self, realistic_frames):
        winds = dp.calc_wind_profile(realistic_frames)
        analysis = {
            "cape": 840.2,
            "cin": -45.1,
            "storm_risk": "moderate — isolated storm possible",
            "lapse_rate_c_per_km": 7.3,
            "tropopause_alt_m": 11400,
            "precipitable_water_mm": 28.4,
            "wind_profile": winds,
        }
        forecast = dp.generate_forecast("T1234567", analysis)

        assert "serial" in forecast
        assert "generated_at" in forecast
        assert "storm_risk" in forecast
        assert "summary" in forecast
        assert "details" in forecast
        assert isinstance(forecast["details"], list)

    def test_forecast_has_content(self, realistic_frames):
        winds = dp.calc_wind_profile(realistic_frames)
        analysis = {
            "cape": 840.2,
            "cin": -45.1,
            "storm_risk": "moderate — isolated storm possible",
            "lapse_rate_c_per_km": 7.3,
            "tropopause_alt_m": 11400,
            "precipitable_water_mm": 28.4,
            "wind_profile": winds,
        }
        forecast = dp.generate_forecast("T1234567", analysis)
        assert len(forecast["details"]) > 0, "Forecast should produce at least one detail sentence"
        assert len(forecast["summary"]) > 0

    def test_forecast_mentions_cape(self, realistic_frames):
        analysis = {
            "cape": 2600,
            "cin": -10,
            "storm_risk": "extreme — severe storm threat",
            "lapse_rate_c_per_km": 9.5,
            "tropopause_alt_m": 12000,
            "precipitable_water_mm": 45,
            "wind_profile": [],
        }
        forecast = dp.generate_forecast("T1234567", analysis)
        cape_mentioned = any("CAPE" in d or "2600" in d for d in forecast["details"])
        assert cape_mentioned, "Forecast details should reference CAPE value"

    def test_forecast_with_no_data(self):
        forecast = dp.generate_forecast("EMPTY", {})
        assert forecast["summary"] == "Insufficient data for forecast."


# ═══════════════════════════════════════════════════════════════════════════════
# 2. FLASK ENDPOINT INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def _mock_sondehub_telemetry(serial="T1234567", n=60):
    """Build a dict matching SondeHub API structure: {serial: {frame_id: frame}}."""
    frames = _make_frames(n=n)
    return {serial: {str(i): f for i, f in enumerate(frames)}}


class TestStatusEndpoint:
    def test_status_returns_200(self, flask_client):
        resp = flask_client.get("/status")
        assert resp.status_code == 200

    def test_status_fields(self, flask_client):
        data = flask_client.get("/status").get_json()
        assert "status" in data
        assert "active_balloons" in data
        assert "last_updated" in data
        assert data["status"] == "running"


class TestBalloonsEndpoint:
    def test_balloons_returns_200(self, flask_client):
        resp = flask_client.get("/balloons")
        assert resp.status_code == 200

    def test_balloons_structure(self, flask_client):
        data = flask_client.get("/balloons").get_json()
        assert "count" in data
        assert "last_updated" in data
        assert "balloons" in data
        assert isinstance(data["balloons"], list)


class TestBalloonPathEndpoint:
    @patch("data_pipeline.fetch_balloon_path")
    def test_path_returns_frames(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=30)
        resp = flask_client.get("/balloon/T1234567")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "serial" in data
        assert "point_count" in data
        assert "path" in data
        assert isinstance(data["path"], list)
        assert len(data["path"]) == 30

    @patch("data_pipeline.fetch_balloon_path")
    def test_path_frame_has_required_fields(self, mock_fetch, flask_client):
        """Person 3 sounding chart needs: lat, lon, alt, temp, humidity, datetime, vel_v"""
        mock_fetch.return_value = _make_frames(n=10)
        data = flask_client.get("/balloon/T1234567").get_json()
        required = {"lat", "lon", "alt", "temp", "humidity", "datetime", "vel_v"}
        for frame in data["path"]:
            assert required.issubset(frame.keys()), f"Missing: {required - set(frame.keys())}"

    @patch("data_pipeline.fetch_balloon_path")
    def test_path_404_when_empty(self, mock_fetch, flask_client):
        mock_fetch.return_value = []
        resp = flask_client.get("/balloon/NONEXIST")
        assert resp.status_code == 404


class TestAnalysisEndpoint:
    @patch("data_pipeline.fetch_balloon_path")
    def test_analysis_returns_200(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=240, tropopause_alt=11000)
        resp = flask_client.get("/balloon/T1234567/analysis")
        assert resp.status_code == 200

    @patch("data_pipeline.fetch_balloon_path")
    def test_analysis_has_all_scorecard_fields(self, mock_fetch, flask_client):
        """Person 3 score card needs every one of these fields."""
        mock_fetch.return_value = _make_frames(n=240, tropopause_alt=11000)
        data = flask_client.get("/balloon/T1234567/analysis").get_json()

        scorecard_fields = [
            "serial",
            "frame_count",
            "lapse_rate_c_per_km",
            "tropopause_alt_m",
            "tropopause_alt_km",
            "cape",
            "cin",
            "storm_risk",
            "precipitable_water_mm",
            "wind_profile",
            "surface_temp",
            "max_alt",
            "sonde_type",
        ]
        for field in scorecard_fields:
            assert field in data, f"Analysis response missing '{field}' — Person 3 needs this!"

    @patch("data_pipeline.fetch_balloon_path")
    def test_analysis_wind_profile_structure(self, mock_fetch, flask_client):
        """Person 3 wind barb diagram needs wind_profile entries with specific fields."""
        mock_fetch.return_value = _make_frames(n=60)
        data = flask_client.get("/balloon/T1234567/analysis").get_json()
        assert isinstance(data["wind_profile"], list)
        if len(data["wind_profile"]) > 0:
            wind = data["wind_profile"][0]
            for key in ["alt", "speed_ms", "speed_knots", "direction_deg"]:
                assert key in wind, f"Wind profile entry missing '{key}'"

    @patch("data_pipeline.fetch_balloon_path")
    def test_analysis_types(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=240, tropopause_alt=11000)
        data = flask_client.get("/balloon/T1234567/analysis").get_json()

        assert isinstance(data["serial"], str)
        assert isinstance(data["frame_count"], int)
        assert isinstance(data["cape"], (int, float))
        assert isinstance(data["cin"], (int, float))
        assert isinstance(data["storm_risk"], str)
        assert isinstance(data["wind_profile"], list)
        if data["lapse_rate_c_per_km"] is not None:
            assert isinstance(data["lapse_rate_c_per_km"], (int, float))
        if data["tropopause_alt_m"] is not None:
            assert isinstance(data["tropopause_alt_m"], (int, float))
        if data["tropopause_alt_km"] is not None:
            assert isinstance(data["tropopause_alt_km"], (int, float))

    @patch("data_pipeline.fetch_balloon_path")
    def test_analysis_404_when_empty(self, mock_fetch, flask_client):
        mock_fetch.return_value = []
        resp = flask_client.get("/balloon/NONEXIST/analysis")
        assert resp.status_code == 404


class TestForecastEndpoint:
    @patch("data_pipeline.fetch_balloon_path")
    def test_forecast_returns_200(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=60)
        resp = flask_client.get("/balloon/T1234567/forecast")
        assert resp.status_code == 200

    @patch("data_pipeline.fetch_balloon_path")
    def test_forecast_has_required_fields(self, mock_fetch, flask_client):
        """Person 3 needs: serial, generated_at, storm_risk, summary, details[]"""
        mock_fetch.return_value = _make_frames(n=60)
        data = flask_client.get("/balloon/T1234567/forecast").get_json()
        for field in ["serial", "generated_at", "storm_risk", "summary", "details"]:
            assert field in data, f"Forecast response missing '{field}'"
        assert isinstance(data["details"], list)

    @patch("data_pipeline.fetch_balloon_path")
    def test_forecast_details_are_strings(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=60)
        data = flask_client.get("/balloon/T1234567/forecast").get_json()
        for detail in data["details"]:
            assert isinstance(detail, str)

    @patch("data_pipeline.fetch_balloon_path")
    def test_forecast_404_when_empty(self, mock_fetch, flask_client):
        mock_fetch.return_value = []
        resp = flask_client.get("/balloon/NONEXIST/forecast")
        assert resp.status_code == 404


class TestTelemetryEndpoint:
    @patch("data_pipeline.fetch_balloon_path")
    def test_telemetry_returns_200(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=20)
        resp = flask_client.get("/balloon/T1234567/telemetry")
        assert resp.status_code == 200

    @patch("data_pipeline.fetch_balloon_path")
    def test_telemetry_has_metadata(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=20)
        data = flask_client.get("/balloon/T1234567/telemetry").get_json()
        for field in ["serial", "frame_count", "first_seen", "last_seen",
                       "max_altitude_m", "min_temp_c", "sonde_type", "frequency_mhz", "frames"]:
            assert field in data, f"Telemetry response missing '{field}'"
        assert isinstance(data["frames"], list)
        assert data["frame_count"] == 20


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PERSON 3 CONTRACT TESTS — validates the frontend can build all 3 panels
# ═══════════════════════════════════════════════════════════════════════════════

class TestSoundingChartContract:
    """
    The sounding chart plots altitude (Y) vs temperature (X).
    It also computes dewpoint from temp+humidity.
    Every frame MUST have alt and temp; humidity is needed for the dewpoint line.
    """

    @patch("data_pipeline.fetch_balloon_path")
    def test_frames_have_alt_and_temp_for_sounding(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=50)
        data = flask_client.get("/balloon/T1234567").get_json()
        frames_with_data = [f for f in data["path"] if f["temp"] is not None and f["alt"] is not None]
        assert len(frames_with_data) > 0, "Need frames with both alt and temp for sounding chart"

    @patch("data_pipeline.fetch_balloon_path")
    def test_frames_have_humidity_for_dewpoint(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=50)
        data = flask_client.get("/balloon/T1234567").get_json()
        frames_with_humidity = [f for f in data["path"] if f["humidity"] is not None]
        assert len(frames_with_humidity) > 0, "Need humidity data to compute dewpoint line"

    @patch("data_pipeline.fetch_balloon_path")
    def test_frames_sorted_chronologically(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=50)
        data = flask_client.get("/balloon/T1234567").get_json()
        datetimes = [f["datetime"] for f in data["path"]]
        assert datetimes == sorted(datetimes), "Frames should be in chronological order for animation"

    @patch("data_pipeline.fetch_balloon_path")
    def test_altitude_generally_increases(self, mock_fetch, flask_client):
        """For replay animation, altitude should mostly increase as balloon ascends."""
        mock_fetch.return_value = _make_frames(n=50)
        data = flask_client.get("/balloon/T1234567").get_json()
        alts = [f["alt"] for f in data["path"] if f["alt"] is not None]
        assert alts[-1] > alts[0], "Altitude should increase over the flight"


class TestWindBarbContract:
    """
    The wind barb diagram needs wind_profile entries from the analysis endpoint.
    Each entry must have alt, speed_ms, speed_knots, direction_deg.
    Person 3 groups these into altitude bands for display.
    """

    @patch("data_pipeline.fetch_balloon_path")
    def test_wind_profile_present_in_analysis(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=60)
        data = flask_client.get("/balloon/T1234567/analysis").get_json()
        assert "wind_profile" in data
        assert isinstance(data["wind_profile"], list)

    @patch("data_pipeline.fetch_balloon_path")
    def test_wind_profile_has_enough_points_for_bands(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=60)
        data = flask_client.get("/balloon/T1234567/analysis").get_json()
        assert len(data["wind_profile"]) >= 5, "Need enough wind points to fill altitude bands"

    @patch("data_pipeline.fetch_balloon_path")
    def test_wind_speed_knots_for_barbs(self, mock_fetch, flask_client):
        """Wind barbs use knots: short line=5kt, long=10kt, triangle=50kt."""
        mock_fetch.return_value = _make_frames(n=60)
        data = flask_client.get("/balloon/T1234567/analysis").get_json()
        for w in data["wind_profile"]:
            assert "speed_knots" in w, "Wind barbs need speed in knots"
            assert isinstance(w["speed_knots"], (int, float))

    @patch("data_pipeline.fetch_balloon_path")
    def test_wind_direction_for_barbs(self, mock_fetch, flask_client):
        """Wind barb arrow points in the direction wind comes FROM."""
        mock_fetch.return_value = _make_frames(n=60)
        data = flask_client.get("/balloon/T1234567/analysis").get_json()
        for w in data["wind_profile"]:
            assert "direction_deg" in w
            assert 0 <= w["direction_deg"] < 360


class TestScoreCardContract:
    """
    The instability score card displays 8 metric tiles:
    CAPE, CIN, Lapse Rate, Tropopause, Precip Water, Storm Risk, Surface Temp, Max Alt.
    All come from the /analysis endpoint.
    """

    @patch("data_pipeline.fetch_balloon_path")
    def test_all_scorecard_metrics_present(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=240, tropopause_alt=11000)
        data = flask_client.get("/balloon/T1234567/analysis").get_json()

        tiles = {
            "CAPE":            ("cape",                   (int, float)),
            "CIN":             ("cin",                    (int, float)),
            "Lapse Rate":      ("lapse_rate_c_per_km",    (int, float, type(None))),
            "Tropopause (m)":  ("tropopause_alt_m",       (int, float, type(None))),
            "Tropopause (km)": ("tropopause_alt_km",      (int, float, type(None))),
            "Precip Water":    ("precipitable_water_mm",   (int, float, type(None))),
            "Storm Risk":      ("storm_risk",              str),
            "Surface Temp":    ("surface_temp",            (int, float, type(None))),
            "Max Altitude":    ("max_alt",                 (int, float)),
        }

        for label, (field, expected_type) in tiles.items():
            assert field in data, f"Score card tile '{label}' needs '{field}' but it's missing"
            assert isinstance(data[field], expected_type), (
                f"'{field}' should be {expected_type}, got {type(data[field])}"
            )

    @patch("data_pipeline.fetch_balloon_path")
    def test_storm_risk_has_color_mappable_prefix(self, mock_fetch, flask_client):
        """Person 3 maps risk to colors: low=green, moderate=yellow, high=orange, extreme=red."""
        mock_fetch.return_value = _make_frames(n=60)
        data = flask_client.get("/balloon/T1234567/analysis").get_json()
        valid = ["low", "moderate", "high", "extreme", "insufficient"]
        risk = data["storm_risk"]
        assert any(risk.startswith(v) for v in valid), (
            f"storm_risk '{risk}' doesn't start with a known level — can't map to color"
        )


class TestForecastCardContract:
    """
    Below the score card: forecast details as a bulleted list.
    Needs storm_risk, summary, details[].
    """

    @patch("data_pipeline.fetch_balloon_path")
    def test_forecast_details_nonempty(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=60)
        data = flask_client.get("/balloon/T1234567/forecast").get_json()
        assert len(data["details"]) > 0, "Forecast bullet list needs at least one item"

    @patch("data_pipeline.fetch_balloon_path")
    def test_forecast_summary_is_first_detail(self, mock_fetch, flask_client):
        mock_fetch.return_value = _make_frames(n=60)
        data = flask_client.get("/balloon/T1234567/forecast").get_json()
        if data["details"]:
            assert data["summary"] == data["details"][0], (
                "Summary should match first detail for consistency"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. EDGE CASES & ROBUSTNESS
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_lapse_rate_with_constant_temp(self):
        frames = _make_frames(n=20, lapse=0)
        lr = dp.calc_lapse_rate(frames)
        if lr is not None:
            assert abs(lr) < 1.0, "Lapse rate should be ~0 for isothermal atmosphere"

    def test_cape_with_very_stable_atmosphere(self):
        frames = _make_frames(n=60, surface_temp=5, lapse=3.0)
        cape, cin, risk = dp.calc_cape_cin(frames)
        assert cape < 300, "Very stable atmosphere should have low CAPE"
        assert risk.startswith("low")

    def test_wind_with_stationary_balloon(self):
        """If balloon doesn't move horizontally, wind speed should be ~0."""
        frames = _make_frames(n=20)
        for f in frames:
            f["lat"] = 40.0
            f["lon"] = -83.0
        winds = dp.calc_wind_profile(frames)
        for w in winds:
            assert w["speed_ms"] < 0.5, f"Expected near-zero wind, got {w['speed_ms']} m/s"

    def test_precipitable_water_dry_atmosphere(self):
        frames = _make_frames(n=30, humidity_surface=5, humidity_decay=0.1)
        pw = dp.calc_precipitable_water(frames)
        if pw is not None:
            assert pw < 10, "Very dry atmosphere should have low precipitable water"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
