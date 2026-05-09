# tests/test_drone_remote_id.py
import queue
import struct
from unittest.mock import MagicMock, patch

from utils.drone.remote_id import RemoteIDScanner, _parse_ble_remote_id, _parse_wifi_remote_id


def _make_location_payload(lat=51.5, lon=-0.1, alt=50.0, speed=5.0, heading=90.0) -> bytes:
    """Craft a minimal ASTM F3411 Location message (message type 0x01)."""
    msg_type = 0x01
    status = 0x00
    lat_enc = int(lat * 1e7)
    lon_enc = int(lon * 1e7)
    alt_enc = int((alt + 1000) / 0.5)
    speed_enc = int(speed / 0.25)
    heading_enc = int(heading / 0.01)
    return struct.pack("<BBiiHBH", msg_type, status, lat_enc, lon_enc, alt_enc, speed_enc, heading_enc)


def _make_basic_id_payload(serial="SN-TESTSERIAL") -> bytes:
    msg_type = 0x00
    id_type = 0x01
    serial_bytes = serial.encode("ascii").ljust(20, b"\x00")[:20]
    return bytes([msg_type, id_type]) + serial_bytes


def _make_ble_adv_with_remote_id(payload: bytes) -> bytes:
    uuid_bytes = b"\xfa\xff"
    service_data_type = 0x16
    length = len(uuid_bytes) + len(payload) + 1
    return bytes([length, service_data_type]) + uuid_bytes + payload


def test_parse_ble_location_returns_observation():
    payload = _make_location_payload(lat=51.5, lon=-0.1, alt=50.0, speed=5.0, heading=90.0)
    adv = _make_ble_adv_with_remote_id(payload)
    obs = _parse_ble_remote_id(adv)
    assert obs is not None
    assert obs.source == "BLE"
    assert abs(obs.lat - 51.5) < 0.0001
    assert abs(obs.lon - (-0.1)) < 0.0001
    assert abs(obs.altitude_m - 50.0) < 1.0
    assert abs(obs.speed_ms - 5.0) < 0.5


def test_parse_ble_no_uuid_returns_none():
    obs = _parse_ble_remote_id(b"\x00\x01\x02\x03")
    assert obs is None


def test_parse_ble_too_short_returns_none():
    adv = _make_ble_adv_with_remote_id(b"\x01\x00")
    obs = _parse_ble_remote_id(adv)
    assert obs is None


def test_parse_wifi_remote_id_returns_observation():
    payload = _make_location_payload(lat=52.0, lon=0.5)
    obs = _parse_wifi_remote_id(payload)
    assert obs is not None
    assert obs.source == "WIFI"
    assert abs(obs.lat - 52.0) < 0.0001


def test_parse_wifi_non_location_returns_none():
    payload = _make_basic_id_payload()
    obs = _parse_wifi_remote_id(payload)
    assert obs is None


def test_scanner_start_stop():
    q = queue.Queue()
    scanner = RemoteIDScanner(output_queue=q)
    with (
        patch("utils.drone.remote_id.SCAPY_AVAILABLE", True),
        patch("utils.drone.remote_id.AsyncSniffer") as mock_sniffer,
    ):
        mock_sniffer.return_value = MagicMock()
        scanner.start(wifi_iface="wlan0mon")
        assert scanner.running
        scanner.stop()
        assert not scanner.running


def test_scanner_start_without_scapy_still_works():
    q = queue.Queue()
    scanner = RemoteIDScanner(output_queue=q)
    with patch("utils.drone.remote_id.SCAPY_AVAILABLE", False):
        scanner.start(wifi_iface=None)
        assert scanner.running
        scanner.stop()
