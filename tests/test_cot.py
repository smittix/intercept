"""Tests for utils/cot.py — CoT (Cursor-on-Target) export."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import utils.cot as cot


def _reset_module_state():
    cot._enabled = None
    cot._udp_socket = None


class FakeConfig:
    COT_HOST = "239.2.3.1"
    COT_PORT = 6969
    COT_PROTO = "udp"
    COT_STALE_SECONDS = 60


class DisabledConfig(FakeConfig):
    COT_HOST = ""


def test_disabled_when_host_empty():
    _reset_module_state()
    with patch.object(cot, "_get_config", return_value=DisabledConfig):
        cot.publish("adsb", {"icao": "ABC123", "lat": -33.9, "lon": 18.4})
        assert cot._is_enabled() is False


def test_event_to_cot_xml_adsb():
    _reset_module_state()
    with patch.object(cot, "_get_config", return_value=FakeConfig):
        event = {"icao": "ABC123", "callsign": "SAA123", "lat": -33.9, "lon": 18.4, "altitude": 35000}
        xml = cot.event_to_cot_xml("adsb", event)

    assert xml is not None
    assert 'type="a-f-A"' in xml
    assert 'uid="ICAO-ABC123"' in xml
    assert 'lat="-33.9"' in xml
    assert 'lon="18.4"' in xml
    assert 'hae="35000"' in xml
    assert 'callsign="SAA123"' in xml


def test_event_to_cot_xml_ais():
    _reset_module_state()
    with patch.object(cot, "_get_config", return_value=FakeConfig):
        event = {"mmsi": "601234000", "callsign": "ZS1234", "lat": -33.9, "lon": 18.4}
        xml = cot.event_to_cot_xml("ais", event)

    assert xml is not None
    assert 'type="a-f-S"' in xml
    assert 'uid="MMSI-601234000"' in xml


def test_event_to_cot_xml_aprs():
    _reset_module_state()
    with patch.object(cot, "_get_config", return_value=FakeConfig):
        event = {"callsign": "ZS1ABC-9", "lat": -33.9, "lon": 18.4}
        xml = cot.event_to_cot_xml("aprs", event)

    assert xml is not None
    assert 'type="a-f-G-U-C"' in xml
    assert 'uid="APRS-ZS1ABC-9"' in xml


def test_event_to_cot_xml_missing_position_returns_none():
    _reset_module_state()
    with patch.object(cot, "_get_config", return_value=FakeConfig):
        assert cot.event_to_cot_xml("adsb", {"icao": "ABC123"}) is None


def test_event_to_cot_xml_unknown_mode_returns_none():
    _reset_module_state()
    with patch.object(cot, "_get_config", return_value=FakeConfig):
        assert cot.event_to_cot_xml("pager", {"lat": -33.9, "lon": 18.4}) is None


def test_publish_sends_udp_datagram():
    _reset_module_state()
    fake_socket = MagicMock()
    with (
        patch.object(cot, "_get_config", return_value=FakeConfig),
        patch.object(cot, "_get_udp_socket", return_value=fake_socket),
    ):
        cot.publish("adsb", {"icao": "ABC123", "lat": -33.9, "lon": 18.4})

    fake_socket.sendto.assert_called_once()
    args, _ = fake_socket.sendto.call_args
    payload, addr = args
    assert addr == ("239.2.3.1", 6969)
    assert b"<event" in payload
    assert b'uid="ICAO-ABC123"' in payload


def test_publish_sends_tcp_when_configured():
    _reset_module_state()

    class TcpConfig(FakeConfig):
        COT_PROTO = "tcp"

    fake_conn = MagicMock()
    fake_conn.__enter__ = MagicMock(return_value=fake_conn)
    fake_conn.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(cot, "_get_config", return_value=TcpConfig),
        patch("socket.create_connection", return_value=fake_conn) as mock_connect,
    ):
        cot.publish("ais", {"mmsi": "601234000", "lat": -33.9, "lon": 18.4})

    mock_connect.assert_called_once_with(("239.2.3.1", 6969), timeout=5)
    fake_conn.sendall.assert_called_once()


def test_publish_swallows_send_errors():
    _reset_module_state()
    fake_socket = MagicMock()
    fake_socket.sendto.side_effect = OSError("network unreachable")
    with (
        patch.object(cot, "_get_config", return_value=FakeConfig),
        patch.object(cot, "_get_udp_socket", return_value=fake_socket),
    ):
        # Should not raise.
        cot.publish("adsb", {"icao": "ABC123", "lat": -33.9, "lon": 18.4})
