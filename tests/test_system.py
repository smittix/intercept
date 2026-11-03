"""Tests for the System Health monitoring blueprint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _login(client):
    """Mark the Flask test session as authenticated."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "test"
        sess["role"] = "admin"


def test_metrics_returns_expected_keys(client):
    """GET /system/metrics returns top-level metric keys."""
    _login(client)
    resp = client.get("/system/metrics")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "system" in data
    assert "processes" in data
    assert "cpu" in data
    assert "memory" in data
    assert "disk" in data
    assert data["system"]["hostname"]
    assert "version" in data["system"]
    assert "uptime_seconds" in data["system"]
    assert "uptime_human" in data["system"]


def test_metrics_enhanced_keys(client):
    """GET /system/metrics returns enhanced metric keys."""
    _login(client)
    resp = client.get("/system/metrics")
    assert resp.status_code == 200
    data = resp.get_json()
    # New enhanced keys
    assert "network" in data
    assert "disk_io" in data
    assert "boot_time" in data
    assert "battery" in data
    assert "fans" in data
    assert "power" in data

    # CPU should have per_core and freq
    if data["cpu"] is not None:
        assert "per_core" in data["cpu"]
        assert "freq" in data["cpu"]

    # Network should have interfaces and connections
    if data["network"] is not None:
        assert "interfaces" in data["network"]
        assert "connections" in data["network"]
        assert "io" in data["network"]


def test_throttle_flags_no_subprocess_without_vcgencmd():
    """No subprocess is spawned when vcgencmd is not on PATH (non-Pi hosts).

    The metrics collector thread runs for the whole process lifetime; if it
    spawns subprocesses on hosts without vcgencmd, those calls leak into
    other tests' subprocess mocks.
    """
    import routes.system as mod

    with patch("routes.system.shutil.which", return_value=None), patch("routes.system.subprocess.run") as mock_run:
        assert mod._collect_throttle_flags() is None
        mock_run.assert_not_called()


def test_metrics_without_psutil(client):
    """Metrics degrade gracefully when psutil is unavailable."""
    _login(client)
    import routes.system as mod

    orig = mod._HAS_PSUTIL
    mod._HAS_PSUTIL = False
    try:
        resp = client.get("/system/metrics")
        assert resp.status_code == 200
        data = resp.get_json()
        # These fields should be None without psutil
        assert data["cpu"] is None
        assert data["memory"] is None
        assert data["disk"] is None
        assert data["network"] is None
        assert data["disk_io"] is None
        assert data["battery"] is None
        assert data["boot_time"] is None
        assert data["power"] is None
    finally:
        mod._HAS_PSUTIL = orig


def test_sdr_devices_returns_list(client):
    """GET /system/sdr_devices returns a devices list."""
    _login(client)
    mock_device = MagicMock()
    mock_device.sdr_type = MagicMock()
    mock_device.sdr_type.value = "rtlsdr"
    mock_device.index = 0
    mock_device.name = "Generic RTL2832U"
    mock_device.serial = "00000001"
    mock_device.driver = "rtlsdr"

    with patch("utils.sdr.detection.detect_all_devices", return_value=[mock_device]):
        resp = client.get("/system/sdr_devices")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "devices" in data
    assert len(data["devices"]) == 1
    assert data["devices"][0]["type"] == "rtlsdr"
    assert data["devices"][0]["name"] == "Generic RTL2832U"


def test_sdr_devices_handles_detection_failure(client):
    """SDR detection failure returns empty list with error."""
    _login(client)
    with patch("utils.sdr.detection.detect_all_devices", side_effect=RuntimeError("no devices")):
        resp = client.get("/system/sdr_devices")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["devices"] == []
    assert "error" in data


def test_stream_returns_sse_content_type(client):
    """GET /system/stream returns text/event-stream."""
    _login(client)
    resp = client.get("/system/stream")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.content_type


def test_location_returns_shape(client):
    """GET /system/location returns lat/lon/source shape."""
    _login(client)
    resp = client.get("/system/location")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "lat" in data
    assert "lon" in data
    assert "source" in data


def test_location_from_gps(client):
    """Location endpoint returns GPS data when fix available."""
    _login(client)
    mock_pos = MagicMock()
    mock_pos.fix_quality = 3
    mock_pos.latitude = 51.5074
    mock_pos.longitude = -0.1278
    mock_pos.satellites = 12
    mock_pos.epx = 2.5
    mock_pos.epy = 3.1
    mock_pos.altitude = 45.0

    with patch("routes.system.get_current_position", return_value=mock_pos, create=True):
        # Patch the import inside the function
        import routes.system as mod

        original = mod._get_observer_location

        def _patched():
            with patch("utils.gps.get_current_position", return_value=mock_pos):
                return original()

        mod._get_observer_location = _patched
        try:
            resp = client.get("/system/location")
        finally:
            mod._get_observer_location = original

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["source"] == "gps"
    assert data["lat"] == 51.5074
    assert data["lon"] == -0.1278
    assert data["gps"]["fix_quality"] == 3
    assert data["gps"]["satellites"] == 12
    assert data["gps"]["accuracy"] == 3.1
    assert data["gps"]["altitude"] == 45.0


def test_location_falls_back_to_defaults(client):
    """Location endpoint returns constants defaults when GPS and config unavailable."""
    _login(client)
    resp = client.get("/system/location")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "source" in data
    # Should get location from config or default constants
    assert data["lat"] is not None
    assert data["lon"] is not None
    assert data["source"] in ("config", "default")


def test_weather_requires_location(client, monkeypatch):
    """Weather endpoint returns error when no location available."""
    import routes.system as mod

    _login(client)
    # Without lat/lon params, GPS or config the location would fall back to
    # London and fetch live weather; pin it to none and keep the test offline.
    monkeypatch.setattr(mod, "_weather_cache", {})
    monkeypatch.setattr(mod, "_get_observer_location", lambda: {"lat": None, "lon": None, "source": "none"})
    with patch("routes.system._requests") as mock_requests:
        resp = client.get("/system/weather")
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "No location available"
    mock_requests.get.assert_not_called()


def test_weather_with_mocked_response(client):
    """Weather endpoint returns parsed weather data with mocked HTTP."""
    _login(client)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "current_condition": [
            {
                "temp_C": "22",
                "temp_F": "72",
                "weatherDesc": [{"value": "Clear"}],
                "humidity": "45",
                "windspeedMiles": "8",
                "winddir16Point": "NW",
                "FeelsLikeC": "20",
                "visibility": "10",
                "pressure": "1013",
            }
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    import routes.system as mod

    # Clear cache
    mod._weather_cache.clear()
    mod._weather_cache_time = 0.0

    with patch("routes.system._requests") as mock_requests:
        mock_requests.get.return_value = mock_resp
        resp = client.get("/system/weather?lat=40.7&lon=-74.0")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["temp_c"] == "22"
    assert data["condition"] == "Clear"
    assert data["humidity"] == "45"
    assert data["wind_mph"] == "8"
