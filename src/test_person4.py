"""
Test suite for Person 4 — atmospheric model, assimilation, and SDR integration.

Covers:
  1. interpolation.py — physics formulas (temp, pressure, humidity, wind, density alt)
  2. assimilation.py  — lapse rate update, observation nudging, temporal decay
  3. atmosphere.py    — Flask endpoint integration (mocked data sources)
  4. sdr_integration  — position comparison, SDR status endpoint

All tests are offline (no network). External data access is mocked.
Run with:  cd src && python -m pytest test_person4.py -v
"""

import pytest
import math
import json
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from flask import Flask

import interpolation as interp
import assimilation as assim
from atmosphere import (
    atmosphere_bp,
    _parse_synoptic_obs,
    calc_balloon_age,
    COLUMBUS_DEFAULTS,
    _ordinary_kriging_value,
    _kriging_surface_from_stations,
    get_latest_balloon_data,
)
from sdr_integration import sdr_bp, compare_positions


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def surface():
    """Standard Columbus surface observation."""
    return {
        'station_id': 'KCMH',
        'temp_c': 15.0,
        'dewpoint_c': 8.0,
        'pressure_hpa': 1013.25,
        'elev_m': 247,
        'wind_speed_ms': 3.0,
        'wind_dir_deg': 225,
    }


@pytest.fixture
def hot_humid_surface():
    """Hot summer day in Columbus — should produce high density altitude."""
    return {
        'temp_c': 35.0,
        'dewpoint_c': 24.0,
        'pressure_hpa': 1010.0,
        'elev_m': 247,
        'wind_speed_ms': 2.0,
        'wind_dir_deg': 180,
    }


@pytest.fixture
def cold_dry_surface():
    """Cold winter day."""
    return {
        'temp_c': -5.0,
        'dewpoint_c': -15.0,
        'pressure_hpa': 1025.0,
        'elev_m': 247,
        'wind_speed_ms': 5.0,
        'wind_dir_deg': 300,
    }


@pytest.fixture
def balloon_frames():
    """Synthetic balloon ascent from 300m to 5000m."""
    now = datetime.now(timezone.utc)
    frames = []
    for i, alt in enumerate(range(300, 5100, 100)):
        t = 15.0 - 7.5 * (alt - 247) / 1000
        rh = max(10, 80 - 1.5 * (alt - 247) / 100)
        frames.append({
            'alt': alt,
            'temp': round(t, 1),
            'humidity': round(rh, 1),
            'datetime': (now - timedelta(minutes=50 - i)).isoformat().replace('+00:00', 'Z'),
            'serial': 'T1234567',
            'lat': 39.99,
            'lon': -83.01,
        })
    return frames


@pytest.fixture
def flask_app():
    app = Flask(__name__)
    app.register_blueprint(atmosphere_bp)
    app.register_blueprint(sdr_bp)
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(flask_app):
    with flask_app.test_client() as c:
        yield c


# ═══════════════════════════════════════════════════════════════════════════════
# 1. INTERPOLATION — TEMPERATURE
# ═══════════════════════════════════════════════════════════════════════════════

class TestInterpolateTemperature:
    def test_at_surface_returns_surface_temp(self):
        assert interp.interpolate_temperature(247, 15.0, 247) == 15.0

    def test_decreases_with_altitude(self):
        t_low = interp.interpolate_temperature(500, 15.0, 247)
        t_high = interp.interpolate_temperature(2000, 15.0, 247)
        assert t_low > t_high

    def test_standard_lapse_rate(self):
        t = interp.interpolate_temperature(1247, 15.0, 247, lapse_rate_c_per_km=6.5)
        assert abs(t - 8.5) < 0.01

    def test_custom_lapse_rate(self):
        t = interp.interpolate_temperature(1247, 15.0, 247, lapse_rate_c_per_km=8.0)
        assert abs(t - 7.0) < 0.01

    def test_below_surface_extrapolates(self):
        t = interp.interpolate_temperature(0, 15.0, 247)
        assert t > 15.0


class TestComputeELR:
    def test_standard_atmosphere(self):
        elr = interp.compute_elr(15.0, 247, 8.5, 1247)
        assert abs(elr - 6.5) < 0.01

    def test_unstable_atmosphere(self):
        elr = interp.compute_elr(15.0, 247, 5.0, 1247)
        assert elr > 6.5

    def test_stable_atmosphere(self):
        elr = interp.compute_elr(15.0, 247, 12.0, 1247)
        assert elr < 6.5

    def test_zero_delta_z_returns_default(self):
        elr = interp.compute_elr(15.0, 247, 15.0, 247)
        assert elr == 6.5

    def test_balloon_below_surface_returns_default(self):
        elr = interp.compute_elr(15.0, 247, 15.0, 100)
        assert elr == 6.5


# ═══════════════════════════════════════════════════════════════════════════════
# 2. INTERPOLATION — PRESSURE
# ═══════════════════════════════════════════════════════════════════════════════

class TestInterpolatePressure:
    def test_at_surface_returns_surface_pressure(self):
        p = interp.interpolate_pressure(247, 1013.25, 15.0, 247)
        assert abs(p - 1013.25) < 0.01

    def test_decreases_with_altitude(self):
        p1 = interp.interpolate_pressure(500, 1013.25, 15.0, 247)
        p2 = interp.interpolate_pressure(5000, 1013.25, 15.0, 247)
        assert p1 > p2

    def test_always_positive(self):
        p = interp.interpolate_pressure(30000, 1013.25, 15.0, 247)
        assert p > 0

    def test_sea_level_to_1km_roughly_correct(self):
        p = interp.interpolate_pressure(1247, 1013.25, 15.0, 247)
        assert 880 < p < 920

    def test_higher_lapse_rate_changes_pressure(self):
        p_std = interp.interpolate_pressure(5000, 1013.25, 15.0, 247, lapse_rate=6.5)
        p_high = interp.interpolate_pressure(5000, 1013.25, 15.0, 247, lapse_rate=8.0)
        assert p_std != p_high


# ═══════════════════════════════════════════════════════════════════════════════
# 3. INTERPOLATION — HUMIDITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestInterpolateDewpoint:
    def test_at_surface_returns_surface_dewpoint(self):
        td = interp.interpolate_dewpoint(247, 8.0, 247)
        assert abs(td - 8.0) < 0.01

    def test_decreases_with_altitude(self):
        td1 = interp.interpolate_dewpoint(500, 8.0, 247)
        td2 = interp.interpolate_dewpoint(3000, 8.0, 247)
        assert td1 > td2

    def test_slower_than_temperature_lapse(self):
        """Dew point lapse (2 C/km) should be slower than temp lapse (6.5 C/km)."""
        td_drop = 8.0 - interp.interpolate_dewpoint(1247, 8.0, 247, dewpoint_lapse=2.0)
        t_drop = 15.0 - interp.interpolate_temperature(1247, 15.0, 247, lapse_rate_c_per_km=6.5)
        assert td_drop < t_drop


class TestCalcRelativeHumidity:
    def test_saturated_air(self):
        rh = interp.calc_relative_humidity(15.0, 15.0)
        assert abs(rh - 100.0) < 0.5

    def test_dry_air(self):
        rh = interp.calc_relative_humidity(30.0, -10.0)
        assert rh < 10

    def test_clamped_to_0_100(self):
        rh = interp.calc_relative_humidity(-50, 50)
        assert 0 <= rh <= 100

    def test_moderate_humidity(self):
        rh = interp.calc_relative_humidity(20.0, 10.0)
        assert 40 < rh < 60


class TestInterpolateHumidity:
    def test_increases_with_altitude(self):
        """RH should increase as temp drops faster than dewpoint."""
        rh_low = interp.interpolate_humidity(500, 15.0, 8.0, 247)
        rh_high = interp.interpolate_humidity(3000, 15.0, 8.0, 247)
        assert rh_high > rh_low

    def test_surface_rh_matches_calc(self):
        rh_direct = interp.calc_relative_humidity(15.0, 8.0)
        rh_interp = interp.interpolate_humidity(247, 15.0, 8.0, 247)
        assert abs(rh_direct - rh_interp) < 0.1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. INTERPOLATION — WIND
# ═══════════════════════════════════════════════════════════════════════════════

class TestInterpolateWind:
    def test_increases_with_altitude_in_boundary_layer(self):
        w_low = interp.interpolate_wind(300, 3.0, 225, 247)
        w_mid = interp.interpolate_wind(1000, 3.0, 225, 247)
        assert w_mid['speed_ms'] > w_low['speed_ms']

    def test_direction_veers_in_boundary_layer(self):
        w_sfc = interp.interpolate_wind(257, 3.0, 225, 247)
        w_top = interp.interpolate_wind(1747, 3.0, 225, 247)
        assert w_top['direction_deg'] > w_sfc['direction_deg']

    def test_direction_stays_0_360(self):
        w = interp.interpolate_wind(1000, 5.0, 350, 247)
        assert 0 <= w['direction_deg'] < 360

    def test_above_boundary_layer_uses_power_law(self):
        w_bl = interp.interpolate_wind(1747, 3.0, 225, 247)
        w_free = interp.interpolate_wind(3000, 3.0, 225, 247)
        assert w_free['speed_ms'] > w_bl['speed_ms']

    def test_returns_dict_with_required_keys(self):
        w = interp.interpolate_wind(500, 3.0, 225, 247)
        assert 'speed_ms' in w
        assert 'direction_deg' in w

    def test_zero_surface_wind(self):
        w = interp.interpolate_wind(500, 0.0, 225, 247)
        assert w['speed_ms'] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 5. INTERPOLATION — DENSITY ALTITUDE
# ═══════════════════════════════════════════════════════════════════════════════

class TestVaporPressure:
    def test_increases_with_dewpoint(self):
        e1 = interp.calc_vapor_pressure(5.0)
        e2 = interp.calc_vapor_pressure(20.0)
        assert e2 > e1

    def test_positive(self):
        assert interp.calc_vapor_pressure(-20.0) > 0

    def test_known_value(self):
        e = interp.calc_vapor_pressure(20.0)
        assert 23 < e < 24


class TestVirtualTemperature:
    def test_warmer_than_actual(self):
        """Virtual temp should always be >= actual temp (moist air is lighter)."""
        Tv = interp.calc_virtual_temperature(20.0, 15.0, 1013.25)
        T_actual = 20.0 + 273.15
        assert Tv >= T_actual

    def test_dry_air_approx_equals_actual(self):
        Tv = interp.calc_virtual_temperature(20.0, -40.0, 1013.25)
        T_actual = 20.0 + 273.15
        assert abs(Tv - T_actual) < 0.5


class TestAirDensity:
    def test_standard_atmosphere_density(self):
        """ISA sea-level: ~1.225 kg/m^3."""
        Tv = 288.15
        rho = interp.calc_air_density(1013.25, Tv)
        assert abs(rho - 1.225) < 0.01

    def test_hot_air_less_dense(self):
        rho_cool = interp.calc_air_density(1013.25, 280.0)
        rho_hot = interp.calc_air_density(1013.25, 310.0)
        assert rho_hot < rho_cool

    def test_low_pressure_less_dense(self):
        rho_high = interp.calc_air_density(1013.25, 288.15)
        rho_low = interp.calc_air_density(900.0, 288.15)
        assert rho_low < rho_high


class TestDensityAltitude:
    def test_standard_atmosphere_near_zero(self):
        da = interp.calc_density_altitude(1013.25, 15.0, 8.0)
        assert abs(da) < 200

    def test_hot_day_higher_density_altitude(self):
        da_cool = interp.calc_density_altitude(1013.25, 15.0, 8.0)
        da_hot = interp.calc_density_altitude(1010.0, 35.0, 24.0)
        assert da_hot > da_cool

    def test_hot_humid_columbus_summer(self):
        """35C, 80% RH should give DA 600-900m even at ground level."""
        da = interp.calc_density_altitude(1010.0, 35.0, 28.0)
        assert 500 < da < 1200

    def test_cold_day_negative_or_low(self):
        da = interp.calc_density_altitude(1030.0, -10.0, -20.0)
        assert da < 0

    def test_returns_float(self):
        da = interp.calc_density_altitude(1013.25, 15.0, 8.0)
        assert isinstance(da, float)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. INTERPOLATION — FULL PROFILE
# ═══════════════════════════════════════════════════════════════════════════════

class TestBaselineProfile:
    def test_returns_all_required_keys(self, surface):
        p = interp.baseline_profile(500, surface)
        required = {
            'altitude_m', 'temp_c', 'dewpoint_c', 'pressure_hpa',
            'humidity_pct', 'wind', 'virtual_temp_K', 'air_density_kg_m3',
            'density_altitude_m', 'source',
        }
        assert required.issubset(p.keys())

    def test_source_is_interpolated(self, surface):
        p = interp.baseline_profile(500, surface)
        assert p['source'] == 'interpolated'

    def test_altitude_matches_input(self, surface):
        p = interp.baseline_profile(1234, surface)
        assert p['altitude_m'] == 1234

    def test_wind_is_dict(self, surface):
        p = interp.baseline_profile(500, surface)
        assert isinstance(p['wind'], dict)
        assert 'speed_ms' in p['wind']
        assert 'direction_deg' in p['wind']

    def test_uses_custom_elr(self, surface):
        surface['elr'] = 8.0
        p = interp.baseline_profile(1247, surface)
        expected_t = 15.0 - 8.0 * 1.0
        assert abs(p['temp_c'] - expected_t) < 0.1

    def test_uses_custom_dewpoint_lapse(self, surface):
        surface['dewpoint_lapse'] = 3.0
        p = interp.baseline_profile(1247, surface)
        expected_td = 8.0 - 3.0 * 1.0
        assert abs(p['dewpoint_c'] - expected_td) < 0.1


class TestGenerateFullProfile:
    def test_starts_at_surface_elevation(self, surface):
        profile = interp.generate_full_profile(surface, max_alt=1000, step=100)
        assert profile[0]['altitude_m'] == 247

    def test_monotonically_increasing_altitude(self, surface):
        profile = interp.generate_full_profile(surface, max_alt=5000, step=500)
        alts = [p['altitude_m'] for p in profile]
        assert alts == sorted(alts)

    def test_temperature_decreases(self, surface):
        profile = interp.generate_full_profile(surface, max_alt=5000, step=500)
        temps = [p['temp_c'] for p in profile]
        assert temps[0] > temps[-1]

    def test_pressure_decreases(self, surface):
        profile = interp.generate_full_profile(surface, max_alt=5000, step=500)
        pressures = [p['pressure_hpa'] for p in profile]
        assert pressures[0] > pressures[-1]

    def test_correct_number_of_levels(self, surface):
        profile = interp.generate_full_profile(surface, max_alt=1247, step=100)
        expected = len(range(247, 1248, 100))
        assert len(profile) == expected

    def test_all_levels_are_interpolated(self, surface):
        profile = interp.generate_full_profile(surface, max_alt=1000, step=100)
        for lvl in profile:
            assert lvl['source'] == 'interpolated'


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ASSIMILATION — DEWPOINT FROM RH
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalcDewpointFromRH:
    def test_saturated_returns_temp(self):
        td = assim.calc_dewpoint_from_rh(20.0, 100.0)
        assert abs(td - 20.0) < 0.5

    def test_50_percent_rh(self):
        td = assim.calc_dewpoint_from_rh(20.0, 50.0)
        assert 8 < td < 11

    def test_none_rh_returns_none(self):
        assert assim.calc_dewpoint_from_rh(20.0, None) is None

    def test_zero_rh_returns_none(self):
        assert assim.calc_dewpoint_from_rh(20.0, 0) is None

    def test_negative_rh_returns_none(self):
        assert assim.calc_dewpoint_from_rh(20.0, -5) is None

    def test_always_less_than_or_equal_to_temp(self):
        for rh in [10, 30, 50, 70, 90, 100]:
            td = assim.calc_dewpoint_from_rh(25.0, rh)
            assert td <= 25.0 + 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# 8. ASSIMILATION — LAPSE RATE UPDATE (LEVEL 1)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdateLapseRates:
    def test_updates_elr_when_stable_frame_exists(self, surface, balloon_frames):
        result = assim.update_lapse_rates(dict(surface), balloon_frames)
        assert 'elr' in result
        assert result['elr'] != 6.5

    def test_updates_dewpoint_lapse(self, surface, balloon_frames):
        result = assim.update_lapse_rates(dict(surface), balloon_frames)
        assert 'dewpoint_lapse' in result

    def test_no_update_when_no_stable_frames(self, surface):
        low_frames = [
            {'alt': 250, 'temp': 14.5, 'humidity': 78},
            {'alt': 300, 'temp': 14.0, 'humidity': 76},
        ]
        result = assim.update_lapse_rates(dict(surface), low_frames)
        assert 'elr' not in result

    def test_no_update_with_empty_frames(self, surface):
        result = assim.update_lapse_rates(dict(surface), [])
        assert 'elr' not in result

    def test_no_update_when_frames_missing_temp(self, surface):
        frames = [{'alt': 1000, 'temp': None, 'humidity': 50}]
        result = assim.update_lapse_rates(dict(surface), frames)
        assert 'elr' not in result

    def test_elr_is_positive_for_normal_atmosphere(self, surface, balloon_frames):
        result = assim.update_lapse_rates(dict(surface), balloon_frames)
        assert result['elr'] > 0

    def test_dewpoint_lapse_clamped_above_half(self, surface):
        frames = [{'alt': 1000, 'temp': 10.0, 'humidity': 99}]
        result = assim.update_lapse_rates(dict(surface), frames)
        if 'dewpoint_lapse' in result:
            assert result['dewpoint_lapse'] >= 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# 9. ASSIMILATION — OBSERVATION NUDGING (LEVEL 2)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAssimilatedValue:
    def test_no_obs_returns_background(self):
        val, conf = assim.assimilated_value(1000, -5.0, [])
        assert val == -5.0
        assert conf == 0.0

    def test_exact_altitude_fresh_obs_dominates(self):
        obs = [{'alt': 1000, 'value': -8.0, 'age_hours': 0.0}]
        val, conf = assim.assimilated_value(1000, -5.0, obs)
        assert abs(val - (-8.0)) < 0.1
        assert conf > 0.5

    def test_distant_obs_ignored(self):
        obs = [{'alt': 5000, 'value': -30.0, 'age_hours': 0.0}]
        val, conf = assim.assimilated_value(1000, -5.0, obs)
        assert val == -5.0
        assert conf == 0.0

    def test_old_obs_ignored(self):
        obs = [{'alt': 1000, 'value': -8.0, 'age_hours': 7.0}]
        val, conf = assim.assimilated_value(1000, -5.0, obs, max_age_hours=6.0)
        assert val == -5.0
        assert conf == 0.0

    def test_temporal_decay(self):
        """Fresh obs should pull harder toward the observation than stale obs."""
        obs_fresh = [{'alt': 1200, 'value': -8.0, 'age_hours': 0.5}]
        obs_stale = [{'alt': 1200, 'value': -8.0, 'age_hours': 5.0}]
        val_fresh, conf_fresh = assim.assimilated_value(1000, -5.0, obs_fresh)
        val_stale, conf_stale = assim.assimilated_value(1000, -5.0, obs_stale)
        assert conf_fresh > conf_stale

    def test_spatial_decay(self):
        obs_near = [{'alt': 1050, 'value': -8.0, 'age_hours': 0.0}]
        obs_far = [{'alt': 1900, 'value': -8.0, 'age_hours': 0.0}]
        val_near, conf_near = assim.assimilated_value(1000, -5.0, obs_near)
        val_far, conf_far = assim.assimilated_value(1000, -5.0, obs_far)
        assert conf_near > conf_far

    def test_multiple_obs_blended(self):
        obs = [
            {'alt': 900, 'value': -6.0, 'age_hours': 0.0},
            {'alt': 1100, 'value': -8.0, 'age_hours': 0.0},
        ]
        val, conf = assim.assimilated_value(1000, -5.0, obs)
        assert -8.0 < val < -5.0
        assert conf > 0


class TestApplyObservationNudging:
    def test_nudges_profile_in_place(self, surface, balloon_frames):
        profile = interp.generate_full_profile(surface, max_alt=2000, step=500)
        original_temps = [l['temp_c'] for l in profile]

        assim.apply_observation_nudging(profile, balloon_frames)

        new_temps = [l['temp_c'] for l in profile]
        assert new_temps != original_temps

    def test_marks_assimilated_source(self, surface, balloon_frames):
        profile = interp.generate_full_profile(surface, max_alt=2000, step=500)
        assim.apply_observation_nudging(profile, balloon_frames)
        assimilated = [l for l in profile if l['source'] == 'assimilated']
        assert len(assimilated) > 0

    def test_empty_frames_leaves_profile_unchanged(self, surface):
        profile = interp.generate_full_profile(surface, max_alt=1000, step=100)
        original = [dict(l) for l in profile]
        assim.apply_observation_nudging(profile, [])
        for orig, curr in zip(original, profile):
            assert orig['temp_c'] == curr['temp_c']

    def test_none_frames_leaves_profile_unchanged(self, surface):
        profile = interp.generate_full_profile(surface, max_alt=1000, step=100)
        original_temps = [l['temp_c'] for l in profile]
        assim.apply_observation_nudging(profile, None)
        for orig_t, lvl in zip(original_temps, profile):
            assert orig_t == lvl['temp_c']


# ═══════════════════════════════════════════════════════════════════════════════
# 10. ATMOSPHERE — SYNOPTIC PARSING
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseSynopticObs:
    def test_parses_valid_response(self):
        raw = {
            'STATION': [{
                'STID': 'KCMH',
                'ELEVATION': '247',
                'OBSERVATIONS': {
                    'air_temp_value_1': {'value': '18.3'},
                    'dew_point_temperature_value_1': {'value': '10.5'},
                    'sea_level_pressure_value_1': {'value': '1015.2'},
                    'wind_speed_value_1': {'value': '4.1'},
                    'wind_direction_value_1': {'value': '270'},
                },
            }],
        }
        result = _parse_synoptic_obs(raw)
        assert result is not None
        assert result['station_id'] == 'KCMH'
        assert result['temp_c'] == 18.3
        assert result['dewpoint_c'] == 10.5
        assert result['pressure_hpa'] == 1015.2
        assert result['wind_speed_ms'] == 4.1
        assert result['wind_dir_deg'] == 270.0
        assert result['elev_m'] == 247.0

    def test_missing_temp_returns_none(self):
        raw = {
            'STATION': [{
                'STID': 'KCMH',
                'ELEVATION': '247',
                'OBSERVATIONS': {},
            }],
        }
        assert _parse_synoptic_obs(raw) is None

    def test_missing_optional_fields_uses_defaults(self):
        raw = {
            'STATION': [{
                'STID': 'KCMH',
                'ELEVATION': None,
                'OBSERVATIONS': {
                    'air_temp_value_1': {'value': '20.0'},
                },
            }],
        }
        result = _parse_synoptic_obs(raw)
        assert result is not None
        assert result['elev_m'] == 247
        assert result['pressure_hpa'] == 1013.25
        assert result['dewpoint_c'] == 13.0

    def test_empty_dict_returns_none(self):
        assert _parse_synoptic_obs({}) is None

    def test_malformed_station_returns_none(self):
        assert _parse_synoptic_obs({'STATION': []}) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 11. ATMOSPHERE — BALLOON AGE
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalcBalloonAge:
    def test_recent_balloon(self):
        now = datetime.now(timezone.utc)
        frames = [{'datetime': (now - timedelta(hours=1)).isoformat().replace('+00:00', 'Z')}]
        age = calc_balloon_age(frames)
        assert age is not None
        assert 0.9 < age < 1.1

    def test_old_balloon(self):
        now = datetime.now(timezone.utc)
        frames = [{'datetime': (now - timedelta(hours=10)).isoformat().replace('+00:00', 'Z')}]
        age = calc_balloon_age(frames)
        assert age is not None
        assert 9.5 < age < 10.5

    def test_none_balloon(self):
        assert calc_balloon_age(None) is None

    def test_empty_list(self):
        assert calc_balloon_age([]) is None

    def test_missing_datetime(self):
        assert calc_balloon_age([{'alt': 1000}]) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 12. ATMOSPHERE — KRIGING INTERPOLATION QUALITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestKrigingInterpolation:
    def test_exact_station_reconstruction(self):
        """Kriging should reproduce observed value at station location."""
        samples = [
            {'lat': 40.0, 'lon': -83.0, 'value': 10.0},
            {'lat': 40.2, 'lon': -82.8, 'value': 20.0},
            {'lat': 39.8, 'lon': -83.2, 'value': 15.0},
        ]
        est = _ordinary_kriging_value(samples, 40.0, -83.0)
        assert est is not None
        assert abs(est - 10.0) < 1.0

    def test_interpolated_value_is_bounded(self):
        """For smooth fields, estimate should stay within neighborhood range."""
        samples = [
            {'lat': 39.8, 'lon': -83.2, 'value': 12.0},
            {'lat': 40.2, 'lon': -82.8, 'value': 18.0},
            {'lat': 40.1, 'lon': -83.1, 'value': 16.0},
            {'lat': 39.9, 'lon': -82.9, 'value': 14.0},
        ]
        est = _ordinary_kriging_value(samples, 40.0, -83.0)
        assert est is not None
        assert min(s['value'] for s in samples) - 0.5 <= est <= max(s['value'] for s in samples) + 0.5

    def test_relative_continuity_small_position_change(self):
        """A small target shift should not produce a large jump."""
        samples = [
            {'lat': 39.95, 'lon': -83.05, 'value': 14.0},
            {'lat': 40.05, 'lon': -83.05, 'value': 14.6},
            {'lat': 39.95, 'lon': -82.95, 'value': 15.2},
            {'lat': 40.05, 'lon': -82.95, 'value': 15.8},
        ]
        est_a = _ordinary_kriging_value(samples, 40.000, -83.000)
        est_b = _ordinary_kriging_value(samples, 40.005, -82.995)
        assert est_a is not None and est_b is not None
        assert abs(est_b - est_a) < 0.8

    def test_surface_kriging_preserves_physical_wind(self):
        stations = [
            {
                'station_id': 'A', 'lat': 39.9, 'lon': -83.1,
                'temp_c': 14.0, 'dewpoint_c': 8.0, 'pressure_hpa': 1014.0,
                'elev_m': 240.0, 'wind_speed_ms': 4.0, 'wind_dir_deg': 220.0,
            },
            {
                'station_id': 'B', 'lat': 40.1, 'lon': -82.9,
                'temp_c': 16.0, 'dewpoint_c': 9.0, 'pressure_hpa': 1012.0,
                'elev_m': 255.0, 'wind_speed_ms': 6.0, 'wind_dir_deg': 240.0,
            },
            {
                'station_id': 'C', 'lat': 40.0, 'lon': -83.0,
                'temp_c': 15.0, 'dewpoint_c': 8.5, 'pressure_hpa': 1013.0,
                'elev_m': 247.0, 'wind_speed_ms': 5.0, 'wind_dir_deg': 230.0,
            },
        ]
        surface = _kriging_surface_from_stations(stations)
        assert surface is not None
        assert surface['station_id'].startswith('KRIGING_')
        assert 0 <= surface['wind_dir_deg'] < 360
        assert surface['wind_speed_ms'] >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# 13. ATMOSPHERE — BALLOON PROXIMITY FILTERING
# ═══════════════════════════════════════════════════════════════════════════════

class TestNearbyBalloonSelection:
    def test_uses_only_nearby_balloons(self):
        balloons_payload = {
            'balloons': [
                {'serial': 'NEAR1', 'lat': 39.99, 'lon': -83.01},
                {'serial': 'NEAR2', 'lat': 40.05, 'lon': -83.00},
                {'serial': 'FAR1', 'lat': 44.00, 'lon': -75.00},
            ],
        }

        def _mock_get(url, timeout=5, **kwargs):
            m = MagicMock()
            if url.endswith('/balloons'):
                m.ok = True
                m.json.return_value = balloons_payload
                return m
            if url.endswith('/balloon/NEAR1'):
                m.ok = True
                m.json.return_value = {'path': [{'alt': 1000, 'temp': 5, 'serial': 'NEAR1'}]}
                return m
            if url.endswith('/balloon/NEAR2'):
                m.ok = True
                m.json.return_value = {'path': [{'alt': 1100, 'temp': 4, 'serial': 'NEAR2'}]}
                return m
            if url.endswith('/balloon/FAR1'):
                m.ok = True
                m.json.return_value = {'path': [{'alt': 900, 'temp': 6, 'serial': 'FAR1'}]}
                return m
            m.ok = False
            m.json.return_value = {}
            return m

        with patch('requests.get', side_effect=_mock_get):
            frames = get_latest_balloon_data(target_lat=39.99, target_lon=-83.01)

        assert frames is not None
        serials = {f.get('serial') for f in frames}
        assert 'NEAR1' in serials
        assert 'NEAR2' in serials
        assert 'FAR1' not in serials

    def test_returns_none_when_only_far_balloons(self):
        balloons_payload = {
            'balloons': [
                {'serial': 'FAR1', 'lat': 46.0, 'lon': -90.0},
                {'serial': 'FAR2', 'lat': 45.5, 'lon': -89.8},
            ],
        }

        def _mock_get(url, timeout=5, **kwargs):
            m = MagicMock()
            if url.endswith('/balloons'):
                m.ok = True
                m.json.return_value = balloons_payload
                return m
            m.ok = False
            m.json.return_value = {}
            return m

        with patch('requests.get', side_effect=_mock_get):
            frames = get_latest_balloon_data(target_lat=39.99, target_lon=-83.01)

        assert frames is None


# ═══════════════════════════════════════════════════════════════════════════════
# 14. ATMOSPHERE — FLASK ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAtmosphereEndpoints:
    @patch('atmosphere.get_latest_balloon_data', return_value=None)
    @patch('atmosphere.get_latest_surface_obs')
    def test_density_altitude_endpoint(self, mock_surface, mock_balloon, client):
        mock_surface.return_value = dict(COLUMBUS_DEFAULTS)
        resp = client.get('/atmosphere/density_altitude')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'density_altitude_m' in data
        assert 'density_altitude_ft' in data
        assert 'conditions' in data
        assert isinstance(data['density_altitude_m'], (int, float))
        assert isinstance(data['density_altitude_ft'], (int, float))

    @patch('atmosphere.get_latest_balloon_data', return_value=None)
    @patch('atmosphere.get_latest_surface_obs')
    def test_status_endpoint_no_balloon(self, mock_surface, mock_balloon, client):
        mock_surface.return_value = dict(COLUMBUS_DEFAULTS)
        resp = client.get('/atmosphere/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['mode'] == 'interpolated'
        assert data['confidence'] == 'baseline'
        assert data['lapse_rate_source'] == 'standard'
        assert data['lapse_rate_c_per_km'] == 6.5
        assert data['balloon_age_hours'] is None

    @patch('atmosphere.get_latest_balloon_data')
    @patch('atmosphere.get_latest_surface_obs')
    def test_status_endpoint_with_fresh_balloon(self, mock_surface, mock_balloon, client):
        mock_surface.return_value = dict(COLUMBUS_DEFAULTS)
        now = datetime.now(timezone.utc)
        mock_balloon.return_value = [
            {'alt': 1000, 'temp': 8.0, 'humidity': 55, 'serial': 'T123',
             'datetime': (now - timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%SZ')},
        ]
        resp = client.get('/atmosphere/status')
        data = resp.get_json()
        assert data['mode'] == 'assimilated'
        assert data['confidence'] == 'high'

    @patch('atmosphere.get_latest_balloon_data', return_value=None)
    @patch('atmosphere.get_latest_surface_obs')
    def test_at_altitude_endpoint(self, mock_surface, mock_balloon, client):
        mock_surface.return_value = dict(COLUMBUS_DEFAULTS)
        resp = client.get('/atmosphere/at/1000')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['altitude_m'] == 1000
        assert 'temp_c' in data
        assert 'pressure_hpa' in data
        assert 'humidity_pct' in data
        assert 'wind' in data
        assert 'density_altitude_m' in data
        assert data['source'] == 'interpolated'

    @patch('atmosphere.get_latest_balloon_data', return_value=None)
    @patch('atmosphere.get_latest_surface_obs')
    def test_profile_endpoint_structure(self, mock_surface, mock_balloon, client):
        mock_surface.return_value = dict(COLUMBUS_DEFAULTS)
        resp = client.get('/atmosphere/profile')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'generated_at' in data
        assert 'surface_station' in data
        assert 'balloon_serial' in data
        assert 'assimilation_active' in data
        assert 'lapse_rate_source' in data
        assert 'elr_c_per_km' in data
        assert 'profile' in data
        assert isinstance(data['profile'], list)
        assert len(data['profile']) > 100

    @patch('atmosphere.get_latest_balloon_data', return_value=None)
    @patch('atmosphere.get_latest_surface_obs')
    def test_profile_no_balloon_is_standard(self, mock_surface, mock_balloon, client):
        mock_surface.return_value = dict(COLUMBUS_DEFAULTS)
        data = client.get('/atmosphere/profile').get_json()
        assert data['lapse_rate_source'] == 'standard'
        assert data['assimilation_active'] is False
        assert data['balloon_serial'] is None

    @patch('atmosphere.get_latest_balloon_data')
    @patch('atmosphere.get_latest_surface_obs')
    def test_profile_with_balloon_assimilates(self, mock_surface, mock_balloon, client):
        mock_surface.return_value = dict(COLUMBUS_DEFAULTS)
        now = datetime.now(timezone.utc)
        mock_balloon.return_value = [
            {'alt': 1000, 'temp': 8.0, 'humidity': 55, 'serial': 'T123',
             'datetime': (now - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')},
            {'alt': 2000, 'temp': 0.0, 'humidity': 40, 'serial': 'T123',
             'datetime': (now - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')},
        ]
        data = client.get('/atmosphere/profile').get_json()
        assert data['assimilation_active'] is True
        assert data['balloon_serial'] == 'T123'
        assimilated = [l for l in data['profile'] if l['source'] == 'assimilated']
        assert len(assimilated) > 0

    @patch('atmosphere.get_latest_balloon_data')
    @patch('atmosphere.get_latest_surface_obs')
    def test_profile_reports_multiple_balloon_serials(self, mock_surface, mock_balloon, client):
        mock_surface.return_value = dict(COLUMBUS_DEFAULTS)
        now = datetime.now(timezone.utc)
        mock_balloon.return_value = [
            {'alt': 900, 'temp': 13.0, 'humidity': 60, 'serial': 'A1',
             'datetime': (now - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')},
            {'alt': 1200, 'temp': 11.0, 'humidity': 55, 'serial': 'B2',
             'datetime': (now - timedelta(minutes=8)).strftime('%Y-%m-%dT%H:%M:%SZ')},
        ]
        data = client.get('/atmosphere/profile').get_json()
        assert data['balloon_serial'] is None
        assert sorted(data['balloon_serials']) == ['A1', 'B2']

    @patch('atmosphere.get_latest_balloon_data', return_value=None)
    @patch('atmosphere.get_latest_surface_obs')
    def test_profile_levels_have_all_fields(self, mock_surface, mock_balloon, client):
        mock_surface.return_value = dict(COLUMBUS_DEFAULTS)
        data = client.get('/atmosphere/profile').get_json()
        required = {
            'altitude_m', 'temp_c', 'dewpoint_c', 'pressure_hpa',
            'humidity_pct', 'wind', 'virtual_temp_K', 'air_density_kg_m3',
            'density_altitude_m', 'source',
        }
        for level in data['profile'][:5]:
            assert required.issubset(level.keys()), f"Missing: {required - set(level.keys())}"


# ═══════════════════════════════════════════════════════════════════════════════
# 13. SDR — POSITION COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

class TestComparePositions:
    def test_same_position_matches(self):
        pos = {'lat': 39.99, 'lon': -83.01}
        result = compare_positions(pos, pos)
        assert result['match'] is True
        assert result['distance_m'] == 0.0

    def test_close_positions_match(self):
        a = {'lat': 39.990, 'lon': -83.010}
        b = {'lat': 39.991, 'lon': -83.011}
        result = compare_positions(a, b)
        assert result['match'] is True
        assert result['distance_m'] < 500

    def test_distant_positions_no_match(self):
        a = {'lat': 39.99, 'lon': -83.01}
        b = {'lat': 40.50, 'lon': -83.50}
        result = compare_positions(a, b)
        assert result['match'] is False

    def test_none_inputs(self):
        result = compare_positions(None, {'lat': 40, 'lon': -83})
        assert result['match'] is False
        assert result['distance_m'] is None

    def test_empty_dicts(self):
        result = compare_positions({}, {})
        assert result['match'] is False

    def test_missing_keys(self):
        result = compare_positions({'lat': 40}, {'lat': 40, 'lon': -83})
        assert result['match'] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 14. SDR — STATUS ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

class TestSDRStatusEndpoint:
    @patch('sdr_integration.get_local_sdr_telemetry', return_value=None)
    def test_no_sdr_receiving(self, mock_sdr, client):
        resp = client.get('/sdr/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['receiving'] is False

    @patch('sdr_integration.get_sondehub_telemetry')
    @patch('sdr_integration.get_local_sdr_telemetry')
    def test_sdr_receiving(self, mock_local, mock_sh, client):
        mock_local.return_value = {
            'serial': 'T9999999',
            'frequency': 404.0,
            'frames': [
                {'lat': 39.99, 'lon': -83.01, 'alt': 5000},
                {'lat': 39.99, 'lon': -83.01, 'alt': 5500},
            ],
        }
        mock_sh.return_value = {
            'frames': [
                {'lat': 39.99, 'lon': -83.01, 'alt': 5000},
                {'lat': 39.991, 'lon': -83.011, 'alt': 5500},
            ],
        }
        resp = client.get('/sdr/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['receiving'] is True
        assert data['serial'] == 'T9999999'
        assert data['frequency'] == 404.0
        assert data['local_frames'] == 2
        assert data['sondehub_frames'] == 2
        assert data['feeds_into_model'] is True
        assert 'position_match' in data


# ═══════════════════════════════════════════════════════════════════════════════
# 15. PHYSICS CROSS-CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhysicsCrossChecks:
    """Verify formulas are internally consistent."""

    def test_rh_100_when_temp_equals_dewpoint(self, surface):
        surface['dewpoint_c'] = surface['temp_c']
        p = interp.baseline_profile(247, surface)
        assert abs(p['humidity_pct'] - 100.0) < 1.0

    def test_density_altitude_increases_with_temperature(self):
        da_cool = interp.calc_density_altitude(1013.25, 0.0, -5.0)
        da_warm = interp.calc_density_altitude(1013.25, 30.0, 20.0)
        assert da_warm > da_cool

    def test_pressure_at_sea_level_is_standard(self):
        p = interp.interpolate_pressure(0, 1013.25, 15.0, 0)
        assert abs(p - 1013.25) < 0.01

    def test_wind_increases_monotonically_in_boundary_layer(self):
        speeds = []
        for alt in range(300, 1800, 100):
            w = interp.interpolate_wind(alt, 5.0, 270, 247)
            speeds.append(w['speed_ms'])
        for i in range(1, len(speeds)):
            assert speeds[i] >= speeds[i - 1]

    def test_full_profile_density_decreases_with_altitude(self, surface):
        profile = interp.generate_full_profile(surface, max_alt=10000, step=1000)
        densities = [l['air_density_kg_m3'] for l in profile]
        for i in range(1, len(densities)):
            assert densities[i] < densities[i - 1]

    def test_assimilation_improves_accuracy(self, surface):
        """If balloon reads -20C at 3000m but model says -15C,
        assimilated value should be closer to -20C."""
        obs = [{'alt': 3000, 'value': -20.0, 'age_hours': 0.0}]
        corrected, _ = assim.assimilated_value(3000, -15.0, obs)
        error_before = abs(-15.0 - (-20.0))
        error_after = abs(corrected - (-20.0))
        assert error_after < error_before


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
