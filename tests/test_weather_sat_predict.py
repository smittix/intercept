"""Tests for weather satellite pass prediction.

Covers predict_passes() function, TLE handling, trajectory computation,
and ground track generation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from utils.weather_sat_predict import _format_utc_iso, predict_passes

# Controlled single-satellite config used by tests that need exactly one active satellite.
# NOAA-18 was decommissioned Jun 2025 and is inactive in the real WEATHER_SATELLITES,
# so tests that assert on satellite-specific fields patch the module-level name.
_MOCK_WEATHER_SATS = {
    "NOAA-18": {
        "name": "NOAA 18",
        "frequency": 137.9125,
        "mode": "APT",
        "pipeline": "noaa_apt",
        "tle_key": "NOAA-18",
        "active": True,
    }
}


class TestPredictPasses:
    """Tests for predict_passes() function."""

    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    def test_predict_passes_no_tle_data(self, mock_tle, mock_load):
        """predict_passes() should handle missing TLE data."""
        mock_tle.get.return_value = None
        mock_ts = MagicMock()
        mock_ts.now.return_value = MagicMock()
        mock_ts.utc.return_value = MagicMock()
        mock_load.timescale.return_value = mock_ts

        passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15)

        assert passes == []

    @patch("utils.weather_sat_predict.WEATHER_SATELLITES", _MOCK_WEATHER_SATS)
    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    @patch("utils.weather_sat_predict.wgs84")
    @patch("utils.weather_sat_predict.EarthSatellite")
    @patch("utils.weather_sat_predict.find_discrete")
    def test_predict_passes_basic(self, mock_find, mock_sat, mock_wgs84, mock_tle, mock_load):
        """predict_passes() should predict basic passes."""
        # Mock timescale
        mock_ts = MagicMock()
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_now = MagicMock()
        mock_now.utc_datetime.return_value = now
        mock_ts.now.return_value = mock_now
        mock_ts.utc.side_effect = lambda dt: self._mock_time(dt)
        mock_load.timescale.return_value = mock_ts

        # Mock TLE data
        mock_tle.get.return_value = (
            "NOAA-18",
            "1 28654U 05018A   24001.50000000  .00000000  00000-0  00000-0 0  9999",
            "2 28654  98.7000 100.0000 0001000   0.0000   0.0000 14.12500000000000",
        )

        # Mock observer
        mock_observer = MagicMock()
        mock_wgs84.latlon.return_value = mock_observer

        # Mock satellite
        mock_satellite_obj = MagicMock()
        mock_sat.return_value = mock_satellite_obj

        # Mock pass detection - one pass
        rise_time = MagicMock()
        rise_time.utc_datetime.return_value = now + timedelta(hours=2)
        set_time = MagicMock()
        set_time.utc_datetime.return_value = now + timedelta(hours=2, minutes=15)

        mock_find.return_value = ([rise_time, set_time], [True, False])

        # Mock topocentric calculations
        def mock_topocentric(t):
            topo = MagicMock()
            alt = MagicMock()
            alt.degrees = 45.0
            az = MagicMock()
            az.degrees = 180.0
            topo.altaz.return_value = (alt, az, MagicMock())
            return topo

        mock_diff = MagicMock()
        mock_diff.at.side_effect = mock_topocentric
        mock_satellite_obj.__sub__.return_value = mock_diff

        passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15)

        assert len(passes) == 1
        pass_data = passes[0]
        assert pass_data["satellite"] == "NOAA-18"
        assert pass_data["name"] == "NOAA 18"
        assert pass_data["frequency"] == 137.9125
        assert pass_data["mode"] == "APT"
        assert "maxEl" in pass_data
        assert "duration" in pass_data
        assert "quality" in pass_data

    @patch("utils.weather_sat_predict.WEATHER_SATELLITES", _MOCK_WEATHER_SATS)
    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    @patch("utils.weather_sat_predict.wgs84")
    @patch("utils.weather_sat_predict.EarthSatellite")
    @patch("utils.weather_sat_predict.find_discrete")
    def test_predict_passes_below_min_elevation(self, mock_find, mock_sat, mock_wgs84, mock_tle, mock_load):
        """predict_passes() should filter passes below min elevation."""
        mock_ts = MagicMock()
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_now = MagicMock()
        mock_now.utc_datetime.return_value = now
        mock_ts.now.return_value = mock_now
        mock_ts.utc.side_effect = lambda dt: self._mock_time(dt)
        mock_load.timescale.return_value = mock_ts

        mock_tle.get.return_value = (
            "NOAA-18",
            "1 28654U 05018A   24001.50000000  .00000000  00000-0  00000-0 0  9999",
            "2 28654  98.7000 100.0000 0001000   0.0000   0.0000 14.12500000000000",
        )

        mock_observer = MagicMock()
        mock_wgs84.latlon.return_value = mock_observer

        mock_satellite_obj = MagicMock()
        mock_sat.return_value = mock_satellite_obj

        rise_time = MagicMock()
        rise_time.utc_datetime.return_value = now + timedelta(hours=2)
        set_time = MagicMock()
        set_time.utc_datetime.return_value = now + timedelta(hours=2, minutes=15)

        mock_find.return_value = ([rise_time, set_time], [True, False])

        # Mock low elevation pass
        def mock_topocentric(t):
            topo = MagicMock()
            alt = MagicMock()
            alt.degrees = 10.0  # Below min_elevation of 15
            az = MagicMock()
            az.degrees = 180.0
            topo.altaz.return_value = (alt, az, MagicMock())
            return topo

        mock_diff = MagicMock()
        mock_diff.at.side_effect = mock_topocentric
        mock_satellite_obj.__sub__.return_value = mock_diff

        passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15)

        assert len(passes) == 0

    @patch("utils.weather_sat_predict.WEATHER_SATELLITES", _MOCK_WEATHER_SATS)
    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    @patch("utils.weather_sat_predict.wgs84")
    @patch("utils.weather_sat_predict.EarthSatellite")
    @patch("utils.weather_sat_predict.find_discrete")
    def test_predict_passes_with_trajectory(self, mock_find, mock_sat, mock_wgs84, mock_tle, mock_load):
        """predict_passes() should include trajectory when requested."""
        mock_ts = MagicMock()
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_now = MagicMock()
        mock_now.utc_datetime.return_value = now
        mock_ts.now.return_value = mock_now
        mock_ts.utc.side_effect = lambda dt: self._mock_time(dt)
        mock_load.timescale.return_value = mock_ts

        mock_tle.get.return_value = (
            "NOAA-18",
            "1 28654U 05018A   24001.50000000  .00000000  00000-0  00000-0 0  9999",
            "2 28654  98.7000 100.0000 0001000   0.0000   0.0000 14.12500000000000",
        )

        mock_observer = MagicMock()
        mock_wgs84.latlon.return_value = mock_observer

        mock_satellite_obj = MagicMock()
        mock_sat.return_value = mock_satellite_obj

        rise_time = MagicMock()
        rise_time.utc_datetime.return_value = now + timedelta(hours=2)
        set_time = MagicMock()
        set_time.utc_datetime.return_value = now + timedelta(hours=2, minutes=15)

        mock_find.return_value = ([rise_time, set_time], [True, False])

        def mock_topocentric(t):
            topo = MagicMock()
            alt = MagicMock()
            alt.degrees = 45.0
            az = MagicMock()
            az.degrees = 180.0
            topo.altaz.return_value = (alt, az, MagicMock())
            return topo

        mock_diff = MagicMock()
        mock_diff.at.side_effect = mock_topocentric
        mock_satellite_obj.__sub__.return_value = mock_diff

        passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15, include_trajectory=True)

        assert len(passes) == 1
        assert "trajectory" in passes[0]
        assert len(passes[0]["trajectory"]) == 30

    @patch("utils.weather_sat_predict.WEATHER_SATELLITES", _MOCK_WEATHER_SATS)
    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    @patch("utils.weather_sat_predict.wgs84")
    @patch("utils.weather_sat_predict.EarthSatellite")
    @patch("utils.weather_sat_predict.find_discrete")
    def test_predict_passes_with_ground_track(self, mock_find, mock_sat, mock_wgs84, mock_tle, mock_load):
        """predict_passes() should include ground track when requested."""
        mock_ts = MagicMock()
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_now = MagicMock()
        mock_now.utc_datetime.return_value = now
        mock_ts.now.return_value = mock_now
        mock_ts.utc.side_effect = lambda dt: self._mock_time(dt)
        mock_load.timescale.return_value = mock_ts

        mock_tle.get.return_value = (
            "NOAA-18",
            "1 28654U 05018A   24001.50000000  .00000000  00000-0  00000-0 0  9999",
            "2 28654  98.7000 100.0000 0001000   0.0000   0.0000 14.12500000000000",
        )

        mock_observer = MagicMock()
        mock_wgs84.latlon.return_value = mock_observer

        mock_satellite_obj = MagicMock()
        mock_sat.return_value = mock_satellite_obj

        rise_time = MagicMock()
        rise_time.utc_datetime.return_value = now + timedelta(hours=2)
        set_time = MagicMock()
        set_time.utc_datetime.return_value = now + timedelta(hours=2, minutes=15)

        mock_find.return_value = ([rise_time, set_time], [True, False])

        def mock_topocentric(t):
            topo = MagicMock()
            alt = MagicMock()
            alt.degrees = 45.0
            az = MagicMock()
            az.degrees = 180.0
            topo.altaz.return_value = (alt, az, MagicMock())
            return topo

        mock_diff = MagicMock()
        mock_diff.at.side_effect = mock_topocentric
        mock_satellite_obj.__sub__.return_value = mock_diff

        # Mock geocentric position
        def mock_at(t):
            geocentric = MagicMock()
            return geocentric

        mock_satellite_obj.at.side_effect = mock_at

        # Mock subpoint
        mock_subpoint = MagicMock()
        mock_lat = MagicMock()
        mock_lat.degrees = 51.5
        mock_lon = MagicMock()
        mock_lon.degrees = -0.1
        mock_subpoint.latitude = mock_lat
        mock_subpoint.longitude = mock_lon
        mock_wgs84.subpoint.return_value = mock_subpoint

        passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15, include_ground_track=True)

        assert len(passes) == 1
        assert "groundTrack" in passes[0]
        assert len(passes[0]["groundTrack"]) == 60

    @patch("utils.weather_sat_predict.WEATHER_SATELLITES", _MOCK_WEATHER_SATS)
    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    @patch("utils.weather_sat_predict.wgs84")
    @patch("utils.weather_sat_predict.EarthSatellite")
    @patch("utils.weather_sat_predict.find_discrete")
    def test_predict_passes_quality_excellent(self, mock_find, mock_sat, mock_wgs84, mock_tle, mock_load):
        """predict_passes() should mark high elevation passes as excellent."""
        mock_ts = MagicMock()
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_now = MagicMock()
        mock_now.utc_datetime.return_value = now
        mock_ts.now.return_value = mock_now
        mock_ts.utc.side_effect = lambda dt: self._mock_time(dt)
        mock_load.timescale.return_value = mock_ts

        mock_tle.get.return_value = (
            "NOAA-18",
            "1 28654U 05018A   24001.50000000  .00000000  00000-0  00000-0 0  9999",
            "2 28654  98.7000 100.0000 0001000   0.0000   0.0000 14.12500000000000",
        )

        mock_observer = MagicMock()
        mock_wgs84.latlon.return_value = mock_observer

        mock_satellite_obj = MagicMock()
        mock_sat.return_value = mock_satellite_obj

        rise_time = MagicMock()
        rise_time.utc_datetime.return_value = now + timedelta(hours=2)
        set_time = MagicMock()
        set_time.utc_datetime.return_value = now + timedelta(hours=2, minutes=15)

        mock_find.return_value = ([rise_time, set_time], [True, False])

        def mock_topocentric(t):
            topo = MagicMock()
            alt = MagicMock()
            alt.degrees = 75.0  # Excellent pass
            az = MagicMock()
            az.degrees = 180.0
            topo.altaz.return_value = (alt, az, MagicMock())
            return topo

        mock_diff = MagicMock()
        mock_diff.at.side_effect = mock_topocentric
        mock_satellite_obj.__sub__.return_value = mock_diff

        passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15)

        assert len(passes) == 1
        assert passes[0]["quality"] == "excellent"
        assert passes[0]["maxEl"] >= 60

    @patch("utils.weather_sat_predict.WEATHER_SATELLITES", _MOCK_WEATHER_SATS)
    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    @patch("utils.weather_sat_predict.wgs84")
    @patch("utils.weather_sat_predict.EarthSatellite")
    @patch("utils.weather_sat_predict.find_discrete")
    def test_predict_passes_quality_good(self, mock_find, mock_sat, mock_wgs84, mock_tle, mock_load):
        """predict_passes() should mark medium elevation passes as good."""
        mock_ts = MagicMock()
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_now = MagicMock()
        mock_now.utc_datetime.return_value = now
        mock_ts.now.return_value = mock_now
        mock_ts.utc.side_effect = lambda dt: self._mock_time(dt)
        mock_load.timescale.return_value = mock_ts

        mock_tle.get.return_value = (
            "NOAA-18",
            "1 28654U 05018A   24001.50000000  .00000000  00000-0  00000-0 0  9999",
            "2 28654  98.7000 100.0000 0001000   0.0000   0.0000 14.12500000000000",
        )

        mock_observer = MagicMock()
        mock_wgs84.latlon.return_value = mock_observer

        mock_satellite_obj = MagicMock()
        mock_sat.return_value = mock_satellite_obj

        rise_time = MagicMock()
        rise_time.utc_datetime.return_value = now + timedelta(hours=2)
        set_time = MagicMock()
        set_time.utc_datetime.return_value = now + timedelta(hours=2, minutes=15)

        mock_find.return_value = ([rise_time, set_time], [True, False])

        def mock_topocentric(t):
            topo = MagicMock()
            alt = MagicMock()
            alt.degrees = 45.0  # Good pass
            az = MagicMock()
            az.degrees = 180.0
            topo.altaz.return_value = (alt, az, MagicMock())
            return topo

        mock_diff = MagicMock()
        mock_diff.at.side_effect = mock_topocentric
        mock_satellite_obj.__sub__.return_value = mock_diff

        passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15)

        assert len(passes) == 1
        assert passes[0]["quality"] == "good"
        assert 30 <= passes[0]["maxEl"] < 60

    @patch("utils.weather_sat_predict.WEATHER_SATELLITES", _MOCK_WEATHER_SATS)
    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    @patch("utils.weather_sat_predict.wgs84")
    @patch("utils.weather_sat_predict.EarthSatellite")
    @patch("utils.weather_sat_predict.find_discrete")
    def test_predict_passes_quality_fair(self, mock_find, mock_sat, mock_wgs84, mock_tle, mock_load):
        """predict_passes() should mark low elevation passes as fair."""
        mock_ts = MagicMock()
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_now = MagicMock()
        mock_now.utc_datetime.return_value = now
        mock_ts.now.return_value = mock_now
        mock_ts.utc.side_effect = lambda dt: self._mock_time(dt)
        mock_load.timescale.return_value = mock_ts

        mock_tle.get.return_value = (
            "NOAA-18",
            "1 28654U 05018A   24001.50000000  .00000000  00000-0  00000-0 0  9999",
            "2 28654  98.7000 100.0000 0001000   0.0000   0.0000 14.12500000000000",
        )

        mock_observer = MagicMock()
        mock_wgs84.latlon.return_value = mock_observer

        mock_satellite_obj = MagicMock()
        mock_sat.return_value = mock_satellite_obj

        rise_time = MagicMock()
        rise_time.utc_datetime.return_value = now + timedelta(hours=2)
        set_time = MagicMock()
        set_time.utc_datetime.return_value = now + timedelta(hours=2, minutes=15)

        mock_find.return_value = ([rise_time, set_time], [True, False])

        def mock_topocentric(t):
            topo = MagicMock()
            alt = MagicMock()
            alt.degrees = 20.0  # Fair pass
            az = MagicMock()
            az.degrees = 180.0
            topo.altaz.return_value = (alt, az, MagicMock())
            return topo

        mock_diff = MagicMock()
        mock_diff.at.side_effect = mock_topocentric
        mock_satellite_obj.__sub__.return_value = mock_diff

        passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15)

        assert len(passes) == 1
        assert passes[0]["quality"] == "fair"
        assert passes[0]["maxEl"] < 30

    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    @patch("utils.weather_sat_predict.wgs84")
    @patch("utils.weather_sat_predict.EarthSatellite")
    @patch("utils.weather_sat_predict.find_discrete")
    def test_predict_passes_inactive_satellite(self, mock_find, mock_sat, mock_wgs84, mock_tle, mock_load):
        """predict_passes() should skip inactive satellites."""
        mock_ts = MagicMock()
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_now = MagicMock()
        mock_now.utc_datetime.return_value = now
        mock_ts.now.return_value = mock_now
        mock_load.timescale.return_value = mock_ts

        # Temporarily mark satellite as inactive
        from utils.weather_sat import WEATHER_SATELLITES

        original_active = WEATHER_SATELLITES["NOAA-18"]["active"]
        WEATHER_SATELLITES["NOAA-18"]["active"] = False

        try:
            passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15)
            # Should not include NOAA-18
            noaa_18_passes = [p for p in passes if p["satellite"] == "NOAA-18"]
            assert len(noaa_18_passes) == 0
        finally:
            WEATHER_SATELLITES["NOAA-18"]["active"] = original_active

    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    @patch("utils.weather_sat_predict.wgs84")
    @patch("utils.weather_sat_predict.EarthSatellite")
    @patch("utils.weather_sat_predict.find_discrete")
    def test_predict_passes_exception_handling(self, mock_find, mock_sat, mock_wgs84, mock_tle, mock_load):
        """predict_passes() should handle exceptions gracefully."""
        mock_ts = MagicMock()
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_now = MagicMock()
        mock_now.utc_datetime.return_value = now
        mock_ts.now.return_value = mock_now
        mock_ts.utc.side_effect = lambda dt: self._mock_time(dt)
        mock_load.timescale.return_value = mock_ts

        mock_tle.get.return_value = (
            "NOAA-18",
            "1 28654U 05018A   24001.50000000  .00000000  00000-0  00000-0 0  9999",
            "2 28654  98.7000 100.0000 0001000   0.0000   0.0000 14.12500000000000",
        )

        mock_observer = MagicMock()
        mock_wgs84.latlon.return_value = mock_observer

        mock_satellite_obj = MagicMock()
        mock_sat.return_value = mock_satellite_obj

        # Make find_discrete raise exception
        mock_find.side_effect = Exception("Computation error")

        # Should not raise, just skip this satellite
        passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15)
        # May include passes from other satellites or be empty
        assert isinstance(passes, list)

    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    def test_predict_passes_uses_tle_cache(self, mock_tle, mock_load):
        """predict_passes() should use live TLE store if available."""
        with patch(
            "utils.weather_sat_predict._get_tle_source", return_value={"NOAA-18": ("NOAA-18", "line1", "line2")}
        ):
            mock_ts = MagicMock()
            mock_ts.now.return_value = MagicMock()
            mock_ts.utc.return_value = MagicMock()
            mock_load.timescale.return_value = mock_ts

            # Even though TLE_SATELLITES is mocked, should use the unified store
            with (
                patch("utils.weather_sat_predict.wgs84"),
                patch("utils.weather_sat_predict.EarthSatellite"),
                patch("utils.weather_sat_predict.find_discrete", return_value=([], [])),
            ):
                predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15)
                # Should not raise

    @patch("utils.weather_sat_predict.WEATHER_SATELLITES", _MOCK_WEATHER_SATS)
    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    @patch("utils.weather_sat_predict.wgs84")
    @patch("utils.weather_sat_predict.EarthSatellite")
    @patch("utils.weather_sat_predict.find_discrete")
    def test_predict_passes_sorted_by_time(self, mock_find, mock_sat, mock_wgs84, mock_tle, mock_load):
        """predict_passes() should return passes sorted by start time."""
        mock_ts = MagicMock()
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_now = MagicMock()
        mock_now.utc_datetime.return_value = now
        mock_ts.now.return_value = mock_now
        mock_ts.utc.side_effect = lambda dt: self._mock_time(dt)
        mock_load.timescale.return_value = mock_ts

        mock_tle.get.return_value = (
            "NOAA-18",
            "1 28654U 05018A   24001.50000000  .00000000  00000-0  00000-0 0  9999",
            "2 28654  98.7000 100.0000 0001000   0.0000   0.0000 14.12500000000000",
        )

        mock_observer = MagicMock()
        mock_wgs84.latlon.return_value = mock_observer

        mock_satellite_obj = MagicMock()
        mock_sat.return_value = mock_satellite_obj

        # Two passes
        rise1 = MagicMock()
        rise1.utc_datetime.return_value = now + timedelta(hours=4)
        set1 = MagicMock()
        set1.utc_datetime.return_value = now + timedelta(hours=4, minutes=15)
        rise2 = MagicMock()
        rise2.utc_datetime.return_value = now + timedelta(hours=2)
        set2 = MagicMock()
        set2.utc_datetime.return_value = now + timedelta(hours=2, minutes=15)

        # Return in non-chronological order
        mock_find.return_value = ([rise1, set1, rise2, set2], [True, False, True, False])

        def mock_topocentric(t):
            topo = MagicMock()
            alt = MagicMock()
            alt.degrees = 45.0
            az = MagicMock()
            az.degrees = 180.0
            topo.altaz.return_value = (alt, az, MagicMock())
            return topo

        mock_diff = MagicMock()
        mock_diff.at.side_effect = mock_topocentric
        mock_satellite_obj.__sub__.return_value = mock_diff

        passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15)

        # Should be sorted with earliest pass first
        if len(passes) >= 2:
            assert passes[0]["startTimeISO"] < passes[1]["startTimeISO"]

    @staticmethod
    def _mock_time(dt):
        """Helper to create mock time object."""
        mock_t = MagicMock()
        if isinstance(dt, datetime):
            mock_t.utc_datetime.return_value = dt
        else:
            mock_t.utc_datetime.return_value = datetime.now(timezone.utc)
        return mock_t


class TestPassDataStructure:
    """Tests for pass data structure."""

    @patch("utils.weather_sat_predict.WEATHER_SATELLITES", _MOCK_WEATHER_SATS)
    @patch("utils.weather_sat_predict.load")
    @patch("utils.weather_sat_predict.TLE_SATELLITES")
    @patch("utils.weather_sat_predict.wgs84")
    @patch("utils.weather_sat_predict.EarthSatellite")
    @patch("utils.weather_sat_predict.find_discrete")
    def test_pass_data_fields(self, mock_find, mock_sat, mock_wgs84, mock_tle, mock_load):
        """Pass data should contain all required fields."""
        mock_ts = MagicMock()
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_now = MagicMock()
        mock_now.utc_datetime.return_value = now
        mock_ts.now.return_value = mock_now
        mock_ts.utc.side_effect = lambda dt: TestPredictPasses._mock_time(dt)
        mock_load.timescale.return_value = mock_ts

        mock_tle.get.return_value = (
            "NOAA-18",
            "1 28654U 05018A   24001.50000000  .00000000  00000-0  00000-0 0  9999",
            "2 28654  98.7000 100.0000 0001000   0.0000   0.0000 14.12500000000000",
        )

        mock_observer = MagicMock()
        mock_wgs84.latlon.return_value = mock_observer

        mock_satellite_obj = MagicMock()
        mock_sat.return_value = mock_satellite_obj

        rise_time = MagicMock()
        rise_time.utc_datetime.return_value = now + timedelta(hours=2)
        set_time = MagicMock()
        set_time.utc_datetime.return_value = now + timedelta(hours=2, minutes=15)

        mock_find.return_value = ([rise_time, set_time], [True, False])

        def mock_topocentric(t):
            topo = MagicMock()
            alt = MagicMock()
            alt.degrees = 45.0
            az = MagicMock()
            az.degrees = 180.0
            topo.altaz.return_value = (alt, az, MagicMock())
            return topo

        mock_diff = MagicMock()
        mock_diff.at.side_effect = mock_topocentric
        mock_satellite_obj.__sub__.return_value = mock_diff

        passes = predict_passes(lat=51.5, lon=-0.1, hours=24, min_elevation=15)

        assert len(passes) == 1
        pass_data = passes[0]

        # Check all required fields
        required_fields = [
            "id",
            "satellite",
            "name",
            "frequency",
            "mode",
            "startTime",
            "startTimeISO",
            "endTimeISO",
            "maxEl",
            "maxElAz",
            "riseAz",
            "setAz",
            "duration",
            "quality",
        ]
        for field in required_fields:
            assert field in pass_data, f"Missing required field: {field}"

    def test_import_error_propagates(self):
        """predict_passes() should raise ImportError if skyfield unavailable."""
        with patch.dict("sys.modules", {"skyfield": None, "skyfield.api": None}):
            with pytest.raises((ImportError, AttributeError)):
                predict_passes(lat=51.5, lon=-0.1)


class TestTimestampFormatting:
    """Tests for UTC timestamp serialization helpers."""

    def test_format_utc_iso_from_aware_datetime(self):
        """Aware UTC datetimes should not get a duplicate UTC suffix."""
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        value = _format_utc_iso(dt)
        assert value == "2024-01-01T12:00:00Z"
        assert "+00:00Z" not in value

    def test_format_utc_iso_from_naive_datetime(self):
        """Naive datetimes should be treated as UTC and serialized consistently."""
        dt = datetime(2024, 1, 1, 12, 0, 0)
        value = _format_utc_iso(dt)
        assert value == "2024-01-01T12:00:00Z"


def test_a_real_pass_is_found():
    """Unmocked: Meteor-M2-3 passed 64 degrees over Cincinnati at 02:32 UTC on 2026-11-06.
    find_discrete sampled every half day before, and found no pass at all."""
    from unittest.mock import patch

    from skyfield.api import load

    from utils import weather_sat_predict

    tle = ('METEOR-M2 3', '1 57166U 23091A   26308.55232156 -.00000024  00000+0  83279-5 0  9998', '2 57166  98.5914   0.5312 0004972 116.3913 243.7777 14.24055491174497')
    ts = load.timescale(builtin=True)
    start = ts.utc(2026, 11, 6, 1, 30)

    class FixedClock:
        @staticmethod
        def timescale(builtin=True):
            ts.now = lambda: start
            return ts

    with patch.object(weather_sat_predict, "load", FixedClock), patch.object(
        weather_sat_predict, "_get_tle_source", return_value={"METEOR-M2-3": tle, "METEOR-M2-4": None}
    ):
        passes = weather_sat_predict.predict_passes(39.2287, -84.3541, hours=3, min_elevation=40)
    meteor = [p for p in passes if p["satellite"] == "METEOR-M2-3"]
    assert len(meteor) == 1
    assert meteor[0]["startTimeISO"].startswith("2026-11-06T02:")
    assert meteor[0]["maxEl"] > 60
