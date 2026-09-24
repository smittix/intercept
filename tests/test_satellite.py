import queue
import threading
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from routes.satellite import satellite_bp


@pytest.fixture
def app():
    app = Flask(__name__)
    app.register_blueprint(satellite_bp)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_predict_passes_invalid_coords(client):
    """Verify that invalid coordinates return a 400 error."""
    payload = {
        "latitude": 150.0,  # Invalid (>90)
        "longitude": -0.1278,
    }
    response = client.post("/satellite/predict", json=payload)
    assert response.status_code == 400
    assert response.json["status"] == "error"


def test_fetch_celestrak_invalid_category(client):
    """Verify that an unauthorized category is rejected."""
    response = client.get("/satellite/celestrak/category_fake")
    assert response.status_code == 400
    assert response.json["status"] == "error"
    assert "Invalid category" in response.json["message"]


# Mocking Tests (External Calls and Skyfield)
@patch("urllib.request.urlopen")
def test_update_tle_success(mock_urlopen, client):
    """Simulate a successful response from CelesTrak."""
    mock_content = (
        b"ISS (ZARYA)\n"
        b"1 25544U 98067A   23321.52083333  .00016717  00000-0  30171-3 0  9992\n"
        b"2 25544  51.6416  20.4567 0004561  45.3212  67.8912 15.49876543123456\n"
    )

    mock_response = MagicMock()
    mock_response.read.return_value = mock_content
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    response = client.post("/satellite/update-tle")
    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert "ISS" in response.json["updated"]


@patch("skyfield.api.load")
def test_get_satellite_position_skyfield_error(mock_load, client):
    """Test behavior when Skyfield fails or data is missing."""
    # Force the timescale load to fail
    mock_load.side_effect = Exception("Skyfield error")

    payload = {"latitude": 51.5, "longitude": -0.1, "satellites": ["ISS"]}
    response = client.post("/satellite/position", json=payload)
    # Should return success but an empty positions list due to internal try-except
    assert response.status_code == 200
    assert response.json["positions"] == []


def test_tracker_position_has_no_observer_fields():
    """SSE tracker positions must NOT include observer-relative fields.

    The tracker runs server-side with a fixed (potentially wrong) observer
    location. Only the per-request /satellite/position endpoint, which
    receives the client's actual location, should emit elevation/azimuth/
    distance/visible.
    """
    from routes.satellite import _start_satellite_tracker

    ISS_TLE = (
        "ISS (ZARYA)",
        "1 25544U 98067A   24001.00000000  .00016717  00000-0  30171-3 0  9993",
        "2 25544  51.6416  20.4567 0004561  45.3212  67.8912 15.49876543123457",
    )

    sat_q = queue.Queue(maxsize=5)
    mock_app = MagicMock()
    mock_app.satellite_queue = sat_q

    from skyfield.api import load as _real_load

    real_ts = _real_load.timescale(builtin=True)

    # Pre-populate track cache so the tracker loop doesn't block computing 90 points
    tle_key = (ISS_TLE[0], ISS_TLE[1][:20])
    stub_track = [{"lat": 0.0, "lon": float(i), "past": i < 45} for i in range(91)]
    with (
        patch("routes.satellite._get_tle_cache", return_value={"ISS": ISS_TLE}),
        patch("routes.satellite.get_tracked_satellites") as mock_tracked,
        patch("routes.satellite._track_cache", {tle_key: (stub_track, 1e18)}),
        patch("routes.satellite._get_timescale", return_value=real_ts),
        patch.dict("sys.modules", {"app": mock_app}),
    ):
        mock_tracked.return_value = [
            {
                "name": "ISS (ZARYA)",
                "norad_id": 25544,
                "tle_line1": ISS_TLE[1],
                "tle_line2": ISS_TLE[2],
            }
        ]

        t = threading.Thread(target=_start_satellite_tracker, daemon=True)
        t.start()
        msg = sat_q.get(timeout=10)

    assert msg["type"] == "positions"
    pos = msg["positions"][0]
    for forbidden in ("elevation", "azimuth", "distance", "visible"):
        assert forbidden not in pos, f"SSE tracker must not emit '{forbidden}'"
    for required in ("lat", "lon", "altitude", "satellite", "norad_id"):
        assert required in pos, f"SSE tracker must emit '{required}'"


def test_predict_passes_currentpos_has_full_fields(client):
    """currentPos in pass results must include altitude, elevation, azimuth, distance."""
    payload = {
        "latitude": 51.5074,
        "longitude": -0.1278,
        "hours": 2,
        "minEl": 5,
        "satellites": ["ISS"],
    }
    response = client.post("/satellite/predict", json=payload)
    assert response.status_code == 200
    data = response.json
    assert data["status"] == "success"
    if data["passes"]:
        cp = data["passes"][0].get("currentPos", {})
        for field in ("lat", "lon", "altitude", "elevation", "azimuth", "distance"):
            assert field in cp, f"currentPos missing field: {field}"


@patch("routes.satellite.refresh_tle_data", return_value=["ISS"])
@patch("routes.satellite._load_db_satellites_into_cache")
def test_tle_auto_refresh_schedules_daily_repeat(mock_load_db, mock_refresh):
    """After the first TLE refresh, a 24-hour follow-up timer must be scheduled."""
    import threading as real_threading

    scheduled_delays = []

    class CapturingTimer:
        def __init__(self, delay, fn, *a, **kw):
            scheduled_delays.append(delay)
            self._fn = fn
            self._delay = delay

        def start(self):
            # Execute the startup timer inline so we can capture the follow-up
            if self._delay <= 5:
                self._fn()

    # Never fetched before: the first fetch is at startup (see test_tle_fetch_schedule.py)
    with (
        patch("routes.satellite.threading") as mock_threading,
        patch("routes.satellite._tle_startup_delay", return_value=2.0),
    ):
        mock_threading.Timer = CapturingTimer
        mock_threading.Thread = real_threading.Thread

        from routes.satellite import init_tle_auto_refresh

        init_tle_auto_refresh()

    # First timer: startup delay (≤5s); second timer: 24h repeat (≥86400s)
    assert any(d <= 5 for d in scheduled_delays), f"Expected startup delay timer; got delays: {scheduled_delays}"
    assert any(d >= 86400 for d in scheduled_delays), f"Expected ~24h repeat timer; got delays: {scheduled_delays}"


# Logic Integration Test (Simulating prediction)
def test_predict_passes_empty_cache(client):
    """Verify that if the satellite is not in cache, no passes are returned."""
    payload = {"latitude": 51.5, "longitude": -0.1, "satellites": ["SATELLITE_NON_EXISTENT"]}
    response = client.post("/satellite/predict", json=payload)
    assert response.status_code == 200
    assert len(response.json["passes"]) == 0
