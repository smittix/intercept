"""Tests for Flask routes and API endpoints."""

import json
from unittest.mock import patch

import pytest


@pytest.fixture(scope="session")
def app():
    """Create application for testing."""
    import app as app_module
    from routes import register_blueprints
    from utils.database import init_db

    app_module.app.config["TESTING"] = True

    # Initialize database for settings tests
    init_db()

    # Register blueprints only if not already registered (normally done in main())
    # Check if any blueprint is already registered to avoid re-registration
    if "pager" not in app_module.app.blueprints:
        register_blueprints(app_module.app)

    return app_module.app


@pytest.fixture
def client(app):
    """Create test client."""
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["logged_in"] = True
    return c


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    def test_health_check(self, client):
        """Test health endpoint returns expected data."""
        response = client.get("/health")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "healthy"
        assert "version" in data
        assert "uptime_seconds" in data
        assert "processes" in data
        assert "data" in data

    def test_health_process_status(self, client):
        """Test health endpoint reports process status."""
        response = client.get("/health")
        data = json.loads(response.data)

        processes = data["processes"]
        assert "pager" in processes
        assert "sensor" in processes
        assert "adsb" in processes
        assert "wifi" in processes
        assert "bluetooth" in processes


class TestDevicesEndpoint:
    """Tests for devices endpoint."""

    def test_get_devices(self, client):
        """Test getting device list."""
        response = client.get("/devices")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert isinstance(data, list)

    @patch("app.SDRFactory.detect_devices")
    def test_devices_returns_list(self, mock_detect, client):
        """Test devices endpoint returns list format."""
        from utils.sdr import RTLSDRCommandBuilder, SDRDevice, SDRType

        mock_detect.return_value = [
            SDRDevice(
                sdr_type=SDRType.RTL_SDR,
                index=0,
                name="Test RTL-SDR",
                serial="00000001",
                driver="rtlsdr",
                capabilities=RTLSDRCommandBuilder.CAPABILITIES,
            )
        ]

        response = client.get("/devices")
        data = json.loads(response.data)

        assert len(data) == 1
        assert data[0]["name"] == "Test RTL-SDR"


class TestDependenciesEndpoint:
    """Tests for dependencies endpoint."""

    def test_get_dependencies(self, client):
        """Test getting dependency status."""
        response = client.get("/dependencies")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"
        assert "os" in data
        assert "pkg_manager" in data
        assert "modes" in data


class TestSettingsEndpoints:
    """Tests for settings API endpoints."""

    def test_get_settings(self, client):
        """Test getting all settings."""
        response = client.get("/settings")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"
        assert "settings" in data

    def test_save_settings(self, client):
        """Test saving settings."""
        response = client.post(
            "/settings", data=json.dumps({"test_key": "test_value"}), content_type="application/json"
        )
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"
        assert "test_key" in data["saved"]

    def test_save_empty_settings(self, client):
        """Test saving empty settings returns error."""
        response = client.post("/settings", data=json.dumps({}), content_type="application/json")
        assert response.status_code == 400

    def test_get_single_setting(self, client):
        """Test getting a single setting."""
        # First save a setting
        client.post("/settings", data=json.dumps({"my_setting": "my_value"}), content_type="application/json")

        # Then retrieve it
        response = client.get("/settings/my_setting")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"
        assert data["value"] == "my_value"

    def test_get_nonexistent_setting(self, client):
        """Test getting a setting that doesn't exist."""
        response = client.get("/settings/nonexistent_key_xyz")
        assert response.status_code == 404

    def test_update_setting(self, client):
        """Test updating a setting via PUT."""
        response = client.put(
            "/settings/update_test", data=json.dumps({"value": "updated_value"}), content_type="application/json"
        )
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"
        assert data["value"] == "updated_value"

    def test_delete_setting(self, client):
        """Test deleting a setting."""
        # First create a setting
        client.post("/settings", data=json.dumps({"delete_me": "value"}), content_type="application/json")

        # Then delete it
        response = client.delete("/settings/delete_me")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"
        assert data["deleted"] is True

    def test_save_observer_location_updates_env_and_runtime_defaults(self, client, monkeypatch, tmp_path):
        """Saving observer location should persist to .env and update in-memory defaults."""
        import app as app_module
        import config
        from routes import adsb as adsb_routes
        from routes import ais as ais_routes
        from routes import settings as settings_routes

        with client.session_transaction() as sess:
            sess["logged_in"] = True

        env_path = tmp_path / ".env"
        monkeypatch.setattr(settings_routes, "_get_env_file_path", lambda: env_path)

        response = client.post(
            "/settings/observer-location", data=json.dumps({"lat": 48.0, "lon": 16.16}), content_type="application/json"
        )
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"
        assert data["lat"] == 48.0
        assert data["lon"] == 16.16

        env_text = env_path.read_text()
        assert "INTERCEPT_DEFAULT_LAT=48.0" in env_text
        assert "INTERCEPT_DEFAULT_LON=16.16" in env_text

        assert config.DEFAULT_LATITUDE == 48.0
        assert config.DEFAULT_LONGITUDE == 16.16
        assert app_module.DEFAULT_LATITUDE == 48.0
        assert app_module.DEFAULT_LONGITUDE == 16.16
        assert adsb_routes.DEFAULT_LATITUDE == 48.0
        assert adsb_routes.DEFAULT_LONGITUDE == 16.16
        assert ais_routes.DEFAULT_LATITUDE == 48.0
        assert ais_routes.DEFAULT_LONGITUDE == 16.16

    def test_save_observer_location_rejects_invalid_values(self, client):
        """Observer location save should validate coordinates."""
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        response = client.post(
            "/settings/observer-location", data=json.dumps({"lat": 200, "lon": 16.16}), content_type="application/json"
        )
        assert response.status_code == 400


class TestCorrelationEndpoints:
    """Tests for correlation API endpoints."""

    def test_get_correlations(self, client):
        """Test getting device correlations."""
        response = client.get("/correlation")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"
        assert "correlations" in data
        assert "wifi_count" in data
        assert "bt_count" in data

    def test_correlations_with_confidence_filter(self, client):
        """Test correlation endpoint respects confidence filter."""
        response = client.get("/correlation?min_confidence=0.8")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"


class TestListeningPostEndpoints:
    """Tests for listening post endpoints."""

    def test_tools_check(self, client):
        """Test listening post tools availability check."""
        response = client.get("/receiver/tools")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert "rtl_fm" in data
        assert "available" in data

    def test_scanner_status(self, client):
        """Test scanner status endpoint."""
        response = client.get("/receiver/scanner/status")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert "running" in data
        assert "paused" in data
        assert "current_freq" in data

    def test_presets(self, client):
        """Test scanner presets endpoint."""
        response = client.get("/receiver/presets")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert "presets" in data
        assert len(data["presets"]) > 0

        # Check preset structure
        preset = data["presets"][0]
        assert "name" in preset
        assert "start" in preset
        assert "end" in preset
        assert "mod" in preset

    def test_scanner_stop_when_not_running(self, client):
        """Test stopping scanner when not running."""
        response = client.post("/receiver/scanner/stop")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "stopped"

    def test_activity_log(self, client):
        """Test getting activity log."""
        response = client.get("/receiver/scanner/log")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert "log" in data
        assert "total" in data

    def test_scanner_skip_when_not_running(self, client):
        """Test skip signal when scanner not running returns error."""
        response = client.post("/receiver/scanner/skip")
        assert response.status_code == 400

        data = json.loads(response.data)
        assert data["status"] == "error"


class TestAudioEndpoints:
    """Tests for audio demodulation endpoints."""

    def test_audio_status(self, client):
        """Test audio status endpoint."""
        response = client.get("/receiver/audio/status")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert "running" in data
        assert "frequency" in data
        assert "modulation" in data

    def test_audio_stop_when_not_running(self, client):
        """Test stopping audio when not running."""
        response = client.post("/receiver/audio/stop")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "stopped"

    def test_audio_start_missing_frequency(self, client):
        """Test starting audio without frequency returns error."""
        response = client.post("/receiver/audio/start", data=json.dumps({}), content_type="application/json")
        assert response.status_code == 400

        data = json.loads(response.data)
        assert data["status"] == "error"
        assert "frequency" in data["message"].lower()

    def test_audio_start_invalid_modulation(self, client):
        """Test starting audio with invalid modulation returns error."""
        response = client.post(
            "/receiver/audio/start",
            data=json.dumps({"frequency": 98.1, "modulation": "invalid_mode"}),
            content_type="application/json",
        )
        assert response.status_code == 400

        data = json.loads(response.data)
        assert data["status"] == "error"
        assert "modulation" in data["message"].lower()

    def test_audio_stream_when_not_running(self, client):
        """Test audio stream when not running returns empty response."""
        response = client.get("/receiver/audio/stream")
        assert response.status_code == 204


class TestExportEndpoints:
    """Tests for data export endpoints."""

    def test_export_aircraft_json(self, client):
        """Test exporting aircraft data as JSON."""
        response = client.get("/export/aircraft?format=json")
        assert response.status_code == 200
        assert response.content_type == "application/json"

    def test_export_aircraft_csv(self, client):
        """Test exporting aircraft data as CSV."""
        response = client.get("/export/aircraft?format=csv")
        assert response.status_code == 200
        assert "text/csv" in response.content_type

    def test_export_wifi_json(self, client):
        """Test exporting WiFi data as JSON."""
        response = client.get("/export/wifi?format=json")
        assert response.status_code == 200
        assert response.content_type == "application/json"

    def test_export_wifi_csv(self, client):
        """Test exporting WiFi data as CSV."""
        response = client.get("/export/wifi?format=csv")
        assert response.status_code == 200
        assert "text/csv" in response.content_type

    def test_export_bluetooth_json(self, client):
        """Test exporting Bluetooth data as JSON."""
        response = client.get("/export/bluetooth?format=json")
        assert response.status_code == 200
        assert response.content_type == "application/json"

    def test_export_bluetooth_csv(self, client):
        """Test exporting Bluetooth data as CSV."""
        response = client.get("/export/bluetooth?format=csv")
        assert response.status_code == 200
        assert "text/csv" in response.content_type
