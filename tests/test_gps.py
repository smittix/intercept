"""Tests for utils/gps.py — external (non-gpsd) position fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import utils.gps as gps


def _reset_module_state():
    gps._external_position = None
    gps._gps_client = None


def test_get_current_position_returns_none_when_nothing_available():
    _reset_module_state()
    assert gps.get_current_position() is None


def test_external_position_used_when_no_gpsd_client():
    _reset_module_state()
    pos = gps.GPSPosition(latitude=-33.9, longitude=18.4, device="meshtastic:!55890aeb")
    gps.set_external_position(pos)

    result = gps.get_current_position()

    assert result is pos
    assert gps.get_external_position() is pos


def test_gpsd_position_takes_priority_over_external():
    _reset_module_state()
    gpsd_pos = gps.GPSPosition(latitude=1.0, longitude=2.0, device="gpsd://localhost:2947")
    external_pos = gps.GPSPosition(latitude=-33.9, longitude=18.4, device="meshtastic:!55890aeb")
    gps.set_external_position(external_pos)

    fake_client = MagicMock()
    fake_client.position = gpsd_pos

    with patch.object(gps, "get_gps_reader", return_value=fake_client):
        result = gps.get_current_position()

    assert result is gpsd_pos


def test_external_position_used_when_gpsd_client_has_no_position():
    _reset_module_state()
    external_pos = gps.GPSPosition(latitude=-33.9, longitude=18.4, device="meshtastic:!55890aeb")
    gps.set_external_position(external_pos)

    fake_client = MagicMock()
    fake_client.position = None

    with patch.object(gps, "get_gps_reader", return_value=fake_client):
        result = gps.get_current_position()

    assert result is external_pos
