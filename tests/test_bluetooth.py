import pytest
import json
import subprocess
from unittest.mock import MagicMock, patch
from flask import Flask
from routes.bluetooth import bluetooth_bp, classify_bt_device, detect_tracker

@pytest.fixture(autouse=True)
def mock_app_module(mocker):
    mock_app = mocker.patch('routes.bluetooth.app_module')
    mock_app.bt_devices = {}
    mock_app.bt_beacons = {}
    mock_app.bt_services = {}
    mock_app.bt_queue = MagicMock()
    mock_app.bt_lock = MagicMock()
    mock_app.bt_process = None
    mock_app.bt_interface = 'hci0'
    return mock_app

@pytest.fixture
def app():
    app = Flask(__name__)
    app.register_blueprint(bluetooth_bp)
    return app

@pytest.fixture
def client(app):
    return app.test_client()

def test_classify_bt_device_by_name():
    """Test classification based on common naming patterns."""
    assert classify_bt_device("Sony WH-1000XM4", None, None) == 'audio'
    assert classify_bt_device("iPhone 15", None, None) == 'phone'
    assert classify_bt_device("Garmin Fenix", None, None) == 'wearable'
    assert classify_bt_device("Microsoft Mouse", None, None) == 'input'
    assert classify_bt_device("AirTag", None, None) == 'tracker'
    assert classify_bt_device("Generic Device", None, None) == 'other'

def test_classify_bt_device_by_class():
    """Test classification based on Bluetooth Class of Device (CoD)."""
    assert classify_bt_device(None, 0x0100, None) == 'computer'
    assert classify_bt_device(None, 0x0200, None) == 'phone'
    assert classify_bt_device(None, 0x0400, None) == 'audio'

def test_detect_tracker_by_mac():
    """Test tracker detection using MAC OUI prefixes."""
    # Assuming 'FF:FF:FF' is a mock prefix in patterns for testing
    with patch('routes.bluetooth.TILE_PREFIXES', ['FF:FF']):
        result = detect_tracker("FF:FF:00:11:22:33", "Unknown")
        assert result['type'] == 'tile'

def test_detect_tracker_by_name():
    """Test tracker detection using name strings."""
    result = detect_tracker("00:11:22:33:44:55", "My AirTag")
    assert result['type'] == 'airtag'
    assert result['risk'] == 'high'

# --- Route Tests ---

def test_get_interfaces_route(client, mocker):
    """Test the /interfaces endpoint with mocked system output."""
    mock_run = mocker.patch('subprocess.run')
    # Mocking hciconfig output for a Linux system
    mock_run.return_value = MagicMock(
        stdout="hci0:\tType: Primary  Bus: USB\n\tBD Address: 00:11:22:33:44:55  ACL MTU: 1021:8  SCO MTU: 64:1\n\tUP RUNNING\n"
    )
    mocker.patch('platform.system', return_value='Linux')
    mocker.patch('routes.bluetooth.check_tool', return_value=True)

    response = client.get('/bt/interfaces')
    data = response.get_json()

    assert response.status_code == 200
    assert data['interfaces'][0]['name'] == 'hci0'
    assert data['interfaces'][0]['status'] == 'up'
    assert data['tools']['hcitool'] is True

def test_stop_scan_route(client, mock_app_module):
    """Test stopping a running scan process."""
    mock_process = MagicMock()
    mock_app_module.bt_process = mock_process
    
    response = client.post('/bt/scan/stop')
    
    assert response.status_code == 200
    assert response.get_json()['status'] == 'stopped'
    mock_process.terminate.assert_called_once()

def test_enum_services_error_no_mac(client):
    """Test service enumeration validation."""
    response = client.post('/bt/enum', json={})
    assert response.status_code == 200
    assert response.get_json()['status'] == 'error'

def test_get_devices_route(client, mock_app_module):
    """Test retrieving the current device list from memory."""
    mock_app_module.bt_devices = {
        "00:11:22:33:44:55": {"mac": "00:11:22:33:44:55", "name": "Test Device"}
    }
    
    response = client.get('/bt/devices')
    data = response.get_json()
    
    assert response.status_code == 200
    assert len(data['devices']) == 1
    assert data['devices'][0]['name'] == "Test Device"

def test_reload_oui_route(client, mocker):
    """Test the OUI database reload functionality."""
    mocker.patch('routes.bluetooth.load_oui_database', return_value={"001122": "Test Corp"})
    
    response = client.post('/bt/reload-oui')
    data = response.get_json()
    
    assert response.status_code == 200
    assert data['status'] == 'success'
    assert data['entries'] > 0