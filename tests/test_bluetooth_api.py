"""API endpoint tests for Bluetooth v2 routes."""

import queue
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from routes.bluetooth_v2 import bluetooth_v2_bp
from utils.bluetooth.models import BTDeviceAggregate, ScanStatus, SystemCapabilities


@pytest.fixture
def app():
    """Create Flask application for testing."""
    app = Flask(__name__)
    app.register_blueprint(bluetooth_v2_bp)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def mock_scanner():
    """Create mock BluetoothScanner."""
    with patch("routes.bluetooth_v2.get_bluetooth_scanner") as mock_get:
        scanner = MagicMock()
        # A real queue, not the auto-created MagicMock attribute. The SSE
        # fanout distributor thread calls source_queue.get(timeout=...) in a
        # loop and relies on it blocking and raising queue.Empty. A MagicMock
        # returns instantly, so the thread spins at full speed and every call
        # is recorded in the mock -- an unbounded allocation that outlives the
        # test, because the thread is a daemon held in a module-level registry.
        scanner._event_queue = queue.Queue()
        scanner.is_scanning = False
        scanner.scan_mode = None
        scanner.scan_start_time = None
        scanner.device_count = 0
        scanner.get_status.return_value = ScanStatus(
            is_scanning=False,
            mode="auto",
            backend=None,
            adapter_id=None,
        )
        mock_get.return_value = scanner
        yield scanner


@pytest.fixture
def sample_device():
    """Create sample BTDeviceAggregate."""
    return BTDeviceAggregate(
        device_id="AA:BB:CC:DD:EE:FF:public",
        address="AA:BB:CC:DD:EE:FF",
        address_type="public",
        protocol="ble",
        first_seen=datetime.now(),
        last_seen=datetime.now(),
        seen_count=5,
        seen_rate=1.0,
        rssi_samples=[],
        rssi_current=-55,
        rssi_median=-57.0,
        rssi_min=-60,
        rssi_max=-50,
        rssi_variance=4.0,
        rssi_confidence=0.85,
        range_band="close",
        range_confidence=0.75,
        name="Test Device",
        manufacturer_id=76,
        manufacturer_name="Apple, Inc.",
        manufacturer_bytes=None,
        service_uuids=["0000180f-0000-1000-8000-00805f9b34fb"],
        is_new=False,
        is_persistent=True,
        is_beacon_like=False,
        is_strong_stable=True,
        has_random_address=False,
    )


class TestScanEndpoints:
    """Tests for scan control endpoints."""

    def test_start_scan_success(self, client, mock_scanner):
        """Test starting a scan successfully."""
        mock_scanner.start_scan.return_value = True
        mock_scanner.get_status.return_value = ScanStatus(
            is_scanning=True,
            mode="dbus",
            backend="dbus",
            adapter_id="/org/bluez/hci0",
        )

        response = client.post("/api/bluetooth/scan/start", json={"mode": "auto", "duration_s": 30})

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "started"
        mock_scanner.start_scan.assert_called_once()

    def test_start_scan_already_scanning(self, client, mock_scanner):
        """Test starting scan when already scanning."""
        mock_scanner.is_scanning = True

        response = client.post("/api/bluetooth/scan/start", json={})

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "already_scanning"

    def test_start_scan_failed(self, client, mock_scanner):
        """Test start scan failure."""
        mock_scanner.start_scan.return_value = False
        mock_scanner.get_status.return_value = ScanStatus(
            is_scanning=False,
            mode="auto",
            error="Failed to start scan",
        )

        response = client.post("/api/bluetooth/scan/start", json={})

        assert response.status_code == 500
        data = response.get_json()
        assert data["status"] == "failed"

    def test_stop_scan_success(self, client, mock_scanner):
        """Test stopping a scan."""
        mock_scanner.is_scanning = True

        response = client.post("/api/bluetooth/scan/stop")

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "stopped"
        mock_scanner.stop_scan.assert_called_once()

    def test_get_scan_status(self, client, mock_scanner):
        """Test getting scan status."""
        mock_scanner.get_status.return_value = ScanStatus(
            is_scanning=True,
            mode="dbus",
            backend="dbus",
            adapter_id="/org/bluez/hci0",
            devices_found=10,
        )

        response = client.get("/api/bluetooth/scan/status")

        assert response.status_code == 200
        data = response.get_json()
        assert data["is_scanning"] is True
        assert data["mode"] == "dbus"
        assert data["devices_found"] == 10


class TestDeviceEndpoints:
    """Tests for device listing and detail endpoints."""

    def test_list_devices(self, client, mock_scanner, sample_device):
        """Test listing all devices."""
        mock_scanner.get_devices.return_value = [sample_device]

        response = client.get("/api/bluetooth/devices")

        assert response.status_code == 200
        data = response.get_json()
        assert len(data["devices"]) == 1
        assert data["devices"][0]["address"] == "AA:BB:CC:DD:EE:FF"

    def test_list_devices_with_filters(self, client, mock_scanner, sample_device):
        """Test listing devices with filters."""
        mock_scanner.get_devices.return_value = [sample_device]

        response = client.get("/api/bluetooth/devices?protocol=ble&min_rssi=-60&sort=rssi_current")

        assert response.status_code == 200
        mock_scanner.get_devices.assert_called_with(
            sort_by="rssi_current",
            sort_desc=True,
            protocol="ble",
            min_rssi=-60,
            max_age_seconds=300.0,
        )

    def test_list_devices_new_only(self, client, mock_scanner, sample_device):
        """Test listing only new devices via heuristic filter."""
        sample_device.is_new = True
        mock_scanner.get_devices.return_value = [sample_device]

        response = client.get("/api/bluetooth/devices?heuristic=new")

        assert response.status_code == 200
        mock_scanner.get_devices.assert_called_with(
            sort_by="last_seen",
            sort_desc=True,
            protocol=None,
            min_rssi=None,
            max_age_seconds=300.0,
        )

    def test_get_device_detail(self, client, mock_scanner, sample_device):
        """Test getting device details."""
        mock_scanner.get_device.return_value = sample_device

        response = client.get("/api/bluetooth/devices/AA:BB:CC:DD:EE:FF:public")

        assert response.status_code == 200
        data = response.get_json()
        assert data["address"] == "AA:BB:CC:DD:EE:FF"
        assert data["manufacturer_name"] == "Apple, Inc."

    def test_get_device_not_found(self, client, mock_scanner):
        """Test getting non-existent device."""
        mock_scanner.get_device.return_value = None

        response = client.get("/api/bluetooth/devices/NONEXISTENT")

        assert response.status_code == 404
        data = response.get_json()
        assert data["status"] == "error"


class TestBaselineEndpoints:
    """Tests for baseline management endpoints."""

    def test_set_baseline(self, client, mock_scanner):
        """Test setting baseline."""
        mock_scanner.set_baseline.return_value = 15
        mock_scanner.get_devices.return_value = []

        response = client.post("/api/bluetooth/baseline/set", json={})

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "success"
        assert data["device_count"] == 15

    def test_clear_baseline(self, client, mock_scanner):
        """Test clearing baseline."""
        response = client.post("/api/bluetooth/baseline/clear")

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] in ("cleared", "no_baseline")
        mock_scanner.clear_baseline.assert_called_once()


class TestCapabilitiesEndpoint:
    """Tests for capabilities check endpoint."""

    def test_get_capabilities(self, client):
        """Test getting system capabilities."""
        mock_caps = SystemCapabilities(
            has_dbus=True,
            has_bluez=True,
            bluez_version="5.66",
            adapters=[{"id": "/org/bluez/hci0", "name": "hci0"}],
            is_root=True,
            has_bleak=True,
            has_hcitool=True,
        )

        with patch("routes.bluetooth_v2.check_capabilities", return_value=mock_caps):
            response = client.get("/api/bluetooth/capabilities")

        assert response.status_code == 200
        data = response.get_json()
        assert data["available"] is True
        assert data["has_dbus"] is True

    def test_capabilities_not_available(self, client):
        """Test capabilities when Bluetooth not available."""
        mock_caps = SystemCapabilities(
            has_dbus=False,
            has_bluez=False,
            bluez_version=None,
            adapters=[],
            issues=["No Bluetooth adapter found"],
        )

        with patch("routes.bluetooth_v2.check_capabilities", return_value=mock_caps):
            response = client.get("/api/bluetooth/capabilities")

        assert response.status_code == 200
        data = response.get_json()
        assert data["available"] is False
        assert "No Bluetooth adapter found" in data["issues"]


class TestExportEndpoint:
    """Tests for data export endpoint."""

    def test_export_json(self, client, mock_scanner, sample_device):
        """Test JSON export."""
        mock_scanner.get_devices.return_value = [sample_device]

        response = client.get("/api/bluetooth/export?format=json")

        assert response.status_code == 200
        assert response.content_type == "application/json"
        data = response.get_json()
        assert "devices" in data
        assert "exported_at" in data

    def test_export_csv(self, client, mock_scanner, sample_device):
        """Test CSV export."""
        mock_scanner.get_devices.return_value = [sample_device]

        response = client.get("/api/bluetooth/export?format=csv")

        assert response.status_code == 200
        assert "text/csv" in response.content_type

        # Check CSV content
        csv_content = response.data.decode("utf-8")
        assert "address" in csv_content.lower()
        assert "AA:BB:CC:DD:EE:FF" in csv_content

    def test_export_empty_devices(self, client, mock_scanner):
        """Test export with no devices."""
        mock_scanner.get_devices.return_value = []

        response = client.get("/api/bluetooth/export?format=json")

        assert response.status_code == 200
        data = response.get_json()
        assert data["devices"] == []


class TestStreamEndpoint:
    """Tests for SSE streaming endpoint."""

    def test_stream_headers(self, client, mock_scanner):
        """Test SSE stream has correct headers."""
        mock_scanner.stream_events.return_value = iter([])

        response = client.get("/api/bluetooth/stream")

        assert response.content_type.startswith("text/event-stream")
        assert response.headers.get("Cache-Control") == "no-cache"

    def test_stream_returns_generator(self, client, mock_scanner):
        """Test stream endpoint returns a generator response."""
        mock_scanner.stream_events.return_value = iter(
            [{"event": "device_update", "data": {"address": "AA:BB:CC:DD:EE:FF"}}]
        )

        response = client.get("/api/bluetooth/stream")

        # Should be a streaming response
        assert response.is_streamed is True


class TestTSCMIntegration:
    """Tests for TSCM integration helper."""

    def test_get_tscm_bluetooth_snapshot(self, mock_scanner, sample_device):
        """Test TSCM snapshot function."""
        from routes.bluetooth_v2 import get_tscm_bluetooth_snapshot

        mock_scanner.get_devices.return_value = [sample_device]

        with patch("routes.bluetooth_v2.get_bluetooth_scanner", return_value=mock_scanner):
            devices = get_tscm_bluetooth_snapshot(duration=8)

        assert len(devices) == 1
        device = devices[0]
        # Should be converted to TSCM format
        assert "mac" in device
        assert device["mac"] == "AA:BB:CC:DD:EE:FF"

    def test_tscm_snapshot_empty(self, mock_scanner):
        """Test TSCM snapshot with no devices."""
        from routes.bluetooth_v2 import get_tscm_bluetooth_snapshot

        mock_scanner.get_devices.return_value = []

        with patch("routes.bluetooth_v2.get_bluetooth_scanner", return_value=mock_scanner):
            devices = get_tscm_bluetooth_snapshot()

        assert devices == []


class TestErrorHandling:
    """Tests for error handling."""

    def test_invalid_json_body(self, client, mock_scanner):
        """Test handling of invalid JSON body."""
        response = client.post("/api/bluetooth/scan/start", data="not json", content_type="application/json")

        # Should handle gracefully
        assert response.status_code in [200, 400]

    def test_scanner_exception(self, client, mock_scanner):
        """Test handling of scanner failure — route returns 'failed' with HTTP 500."""
        mock_scanner.start_scan.return_value = False
        mock_scanner.get_status.return_value = ScanStatus(
            is_scanning=False,
            mode="auto",
            error="Scanner error",
        )

        response = client.post("/api/bluetooth/scan/start", json={})

        assert response.status_code == 500
        data = response.get_json()
        assert data["status"] == "failed"
        assert "Scanner error" in data["error"]

    def test_invalid_device_id_format(self, client, mock_scanner):
        """Test handling of invalid device ID format."""
        mock_scanner.get_device.return_value = None

        response = client.get("/api/bluetooth/devices/invalid-id-format")

        assert response.status_code == 404


class TestDeviceSerialization:
    """Tests for device serialization."""

    def test_device_to_dict_complete(self, sample_device):
        """Test device serialization includes all fields."""
        from routes.bluetooth_v2 import device_to_dict

        result = device_to_dict(sample_device)

        assert result["device_id"] == sample_device.device_id
        assert result["address"] == sample_device.address
        assert result["address_type"] == sample_device.address_type
        assert result["protocol"] == sample_device.protocol
        assert result["rssi_current"] == sample_device.rssi_current
        assert result["rssi_median"] == sample_device.rssi_median
        assert result["range_band"] == sample_device.range_band
        assert result["is_new"] == sample_device.is_new
        assert result["is_persistent"] == sample_device.is_persistent
        assert result["manufacturer_name"] == sample_device.manufacturer_name

    def test_device_to_dict_timestamps(self, sample_device):
        """Test device serialization handles timestamps correctly."""
        from routes.bluetooth_v2 import device_to_dict

        result = device_to_dict(sample_device)

        # Timestamps should be ISO format strings
        assert isinstance(result["first_seen"], str)
        assert isinstance(result["last_seen"], str)

    def test_device_to_dict_null_values(self):
        """Test device serialization handles null values."""
        from routes.bluetooth_v2 import device_to_dict

        device = BTDeviceAggregate(
            device_id="test:public",
            address="test",
            address_type="public",
            protocol="ble",
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            seen_count=1,
            seen_rate=1.0,
            rssi_samples=[],
            rssi_current=None,
            rssi_median=None,
            rssi_min=None,
            rssi_max=None,
            rssi_variance=None,
            rssi_confidence=0.0,
            range_band="unknown",
            range_confidence=0.0,
            name=None,
            manufacturer_id=None,
            manufacturer_name=None,
            manufacturer_bytes=None,
            service_uuids=[],
            is_new=False,
            is_persistent=False,
            is_beacon_like=False,
            is_strong_stable=False,
            has_random_address=False,
        )

        result = device_to_dict(device)

        assert result["rssi_current"] is None
        assert result["name"] is None
        assert result["manufacturer_name"] is None
