# tests/test_drone_remote_id.py
import queue
import struct
from unittest.mock import MagicMock, patch

from utils.drone.remote_id import RemoteIDScanner, _parse_ble_remote_id, _parse_wifi_remote_id


def _make_location_payload(lat=51.5, lon=-0.1, alt=50.0, speed=5.0, heading=90.0) -> bytes:
    """Craft an ASTM F3411-22a Location message (type 1, protocol version 2, 25 bytes)."""
    ew = 0x02 if heading >= 180 else 0x00
    direction = int(heading - 180) if heading >= 180 else int(heading)
    alt_enc = int((alt + 1000) / 0.5)
    return struct.pack(
        "<BBBBbiiHHHBBHBB",
        0x12,  # message type 1 (Location), protocol version 2
        (2 << 4) | ew,  # status airborne, E/W direction bit, speed multiplier 0
        direction,
        int(speed / 0.25),
        0,  # vertical speed
        int(lat * 1e7),
        int(lon * 1e7),
        0,  # pressure altitude unknown
        alt_enc,  # geodetic altitude
        0,  # height unknown
        0x0B,  # vertical / horizontal accuracy (< 3 m)
        0x00,
        0xFFFF,  # timestamp unknown
        0,
        0,
    )


def _make_basic_id_payload(serial="1596F35074529ABCDEFG") -> bytes:
    """Basic ID message: serial number (ID type 1), multirotor (UA type 2)."""
    return bytes([0x02, 0x12]) + serial.encode("ascii").ljust(20, b"\x00")[:20] + b"\x00\x00\x00"


def _make_pack(*messages: bytes) -> bytes:
    return bytes([0xF2, 25, len(messages)]) + b"".join(messages)


def _make_vendor_ie(pack: bytes, counter: int = 1) -> bytes:
    """Body of the Remote ID vendor element, as scapy's Dot11Elt.info gives it."""
    return b"\xfa\x0b\xbc\x0d" + bytes([counter]) + pack


def _make_ble_adv_with_remote_id(payload: bytes, counter: int = 1) -> bytes:
    body = b"\xfa\xff\x0d" + bytes([counter]) + payload
    return bytes([len(body) + 1, 0x16]) + body


# First beacon of odid_wifi_bcn_sample.pcap from opendroneid/wireshark-dissector (Apache-2.0):
# the Remote ID vendor element body with a pack of Basic ID, Location, Self ID, System, Operator ID.
_ODID_SAMPLE_VENDOR_IE = bytes.fromhex(
    "fa0bbc0dd0f0190500004d464731413031323334353637383900000000000050f610005c527ebcba251ba88cb4b6"
    "0000aa099808394100000a00300052656372656174696f6e616c00000000000000000000004004a485251b6edbb3"
    "b601003200000000150000000000000050004742522d4f502d31323341424344000000000000000000"
)


def test_parse_wifi_open_drone_id_sample():
    obs = _parse_wifi_remote_id(_ODID_SAMPLE_VENDOR_IE)
    assert obs is not None
    assert obs.source == "WIFI"
    assert obs.serial_number == "MFG1A0123456789"
    assert obs.operator_id == "GBR-OP-123ABCD"
    assert abs(obs.lat - 45.5457468) < 1e-7
    assert abs(obs.lon - (-122.9681496)) < 1e-7
    assert obs.altitude_m == 237.0
    assert obs.speed_ms == 20.5
    assert obs.heading == 92.0


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
    assert obs.heading == 90.0


def test_parse_ble_heading_west_uses_ew_bit():
    adv = _make_ble_adv_with_remote_id(_make_location_payload(heading=270.0))
    obs = _parse_ble_remote_id(adv)
    assert obs is not None
    assert obs.heading == 270.0


def test_parse_ble_no_uuid_returns_none():
    obs = _parse_ble_remote_id(b"\x00\x01\x02\x03")
    assert obs is None


def test_parse_ble_too_short_returns_none():
    adv = _make_ble_adv_with_remote_id(b"\x12\x00")
    obs = _parse_ble_remote_id(adv)
    assert obs is None


def test_parse_wifi_remote_id_returns_observation():
    pack = _make_pack(_make_basic_id_payload(), _make_location_payload(lat=52.0, lon=0.5))
    obs = _parse_wifi_remote_id(_make_vendor_ie(pack))
    assert obs is not None
    assert obs.source == "WIFI"
    assert obs.serial_number == "1596F35074529ABCDEFG"
    assert abs(obs.lat - 52.0) < 0.0001


def test_parse_wifi_non_location_returns_none():
    obs = _parse_wifi_remote_id(_make_vendor_ie(_make_pack(_make_basic_id_payload())))
    assert obs is None


def test_parse_wifi_other_vendor_element_returns_none():
    # Microsoft WPS element: not Remote ID.
    assert _parse_wifi_remote_id(b"\x00\x50\xf2\x04\x10\x4a\x00\x01\x10") is None


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


def test_parse_wifi_basic_id_v1_is_not_a_location():
    # 0x01 is a version 1 Basic ID header (type in the high nibble), not a Location.
    basic_v1 = bytes([0x01]) + _make_basic_id_payload()[1:]
    assert _parse_wifi_remote_id(_make_vendor_ie(_make_pack(basic_v1))) is None
