"""Tests for ISS SSTV route behavior."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from utils.sstv import ISS_SSTV_FREQ


def _login_session(client) -> None:
    """Mark the Flask test session as authenticated."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "test"
        sess["role"] = "admin"


class TestSSTVRoutes:
    """ISS SSTV route tests."""

    def test_status_reports_fm_modulation(self, client):
        """GET /sstv/status should report FM while the decoder is idle."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.decoder_available = "python-sstv"
        mock_decoder.is_running = False
        mock_decoder.frequency = ISS_SSTV_FREQ
        mock_decoder.get_images.return_value = []
        mock_decoder.doppler_enabled = False
        mock_decoder.last_doppler_info = None

        with (
            patch("routes.sstv.is_sstv_available", return_value=True),
            patch("routes.sstv.get_sstv_decoder", return_value=mock_decoder),
        ):
            response = client.get("/sstv/status")

        assert response.status_code == 200
        data = response.get_json()
        assert data["available"] is True
        assert data["modulation"] == "fm"
        assert data["iss_frequency"] == ISS_SSTV_FREQ

    def test_status_reports_running_frequency_and_modulation(self, client):
        """GET /sstv/status should report what a running decoder is tuned to."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.decoder_available = "python-sstv"
        mock_decoder.is_running = True
        mock_decoder.frequency = 437.8
        mock_decoder.modulation = "usb"
        mock_decoder.get_images.return_value = []
        mock_decoder.doppler_enabled = False
        mock_decoder.last_doppler_info = None

        with (
            patch("routes.sstv.is_sstv_available", return_value=True),
            patch("routes.sstv.get_sstv_decoder", return_value=mock_decoder),
        ):
            data = client.get("/sstv/status").get_json()

        assert data["modulation"] == "usb"
        assert data["frequency"] == pytest.approx(437.8)

    def test_start_uses_fm_and_user_defined_frequency(self, client):
        """POST /sstv/start should tune the requested frequency with FM."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.is_running = False
        mock_decoder.start.return_value = True
        mock_decoder.doppler_enabled = False
        mock_decoder.last_doppler_info = None

        payload = {
            "frequency": 437.800,  # Non-default downlink; must not be snapped to 145.800.
            "modulation": "FM",
            "device": 0,
        }

        with (
            patch("routes.sstv.is_sstv_available", return_value=True),
            patch("routes.sstv.get_sstv_decoder", return_value=mock_decoder),
            patch("routes.sstv.app_module.claim_sdr_device", return_value=None),
        ):
            response = client.post(
                "/sstv/start",
                data=json.dumps(payload),
                content_type="application/json",
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "started"
        assert data["modulation"] == "fm"
        assert data["frequency"] == pytest.approx(437.800)

        mock_decoder.start.assert_called_once()
        call_kwargs = mock_decoder.start.call_args.kwargs
        assert call_kwargs["modulation"] == "fm"
        assert call_kwargs["frequency"] == pytest.approx(437.800)
        assert call_kwargs["gain"] is None
        assert call_kwargs["ppm"] is None

    def test_start_passes_sideband_gain_ppm_and_location(self, client):
        """POST /sstv/start should accept sideband modulation, gain, PPM and location."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.is_running = False
        mock_decoder.start.return_value = True
        mock_decoder.doppler_enabled = True
        mock_decoder.last_doppler_info = None

        payload = {
            "frequency": ISS_SSTV_FREQ,
            "modulation": "usb",
            "device": 0,
            "gain": 40,
            "ppm": -5,
            "latitude": 51.5,
            "longitude": -0.12,
        }

        with (
            patch("routes.sstv.is_sstv_available", return_value=True),
            patch("routes.sstv.get_sstv_decoder", return_value=mock_decoder),
            patch("routes.sstv.app_module.claim_sdr_device", return_value=None),
        ):
            response = client.post(
                "/sstv/start",
                data=json.dumps(payload),
                content_type="application/json",
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["modulation"] == "usb"
        assert data["doppler_enabled"] is True

        call_kwargs = mock_decoder.start.call_args.kwargs
        assert call_kwargs["modulation"] == "usb"
        assert call_kwargs["gain"] == pytest.approx(40)
        assert call_kwargs["ppm"] == -5
        assert call_kwargs["latitude"] == pytest.approx(51.5)
        assert call_kwargs["longitude"] == pytest.approx(-0.12)

    def test_start_rejects_invalid_gain(self, client):
        """POST /sstv/start should reject an out-of-range gain."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.is_running = False

        payload = {"frequency": ISS_SSTV_FREQ, "modulation": "fm", "device": 0, "gain": 500}

        with (
            patch("routes.sstv.is_sstv_available", return_value=True),
            patch("routes.sstv.get_sstv_decoder", return_value=mock_decoder),
        ):
            response = client.post(
                "/sstv/start",
                data=json.dumps(payload),
                content_type="application/json",
            )

        assert response.status_code == 400
        mock_decoder.start.assert_not_called()

    def test_start_rejects_unknown_modulation(self, client):
        """POST /sstv/start should reject modulations rtl_fm SSTV decoding can't use."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.is_running = False

        payload = {
            "frequency": ISS_SSTV_FREQ,
            "modulation": "am",
            "device": 0,
        }

        with (
            patch("routes.sstv.is_sstv_available", return_value=True),
            patch("routes.sstv.get_sstv_decoder", return_value=mock_decoder),
        ):
            response = client.post(
                "/sstv/start",
                data=json.dumps(payload),
                content_type="application/json",
            )

        assert response.status_code == 400
        data = response.get_json()
        assert data["status"] == "error"
        assert "Modulation must be fm, usb, or lsb" in data["message"]
        mock_decoder.start.assert_not_called()

    def test_start_rejects_out_of_range_frequency(self, client):
        """POST /sstv/start should reject frequencies outside the SDR range."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.is_running = False

        payload = {
            "frequency": 14.230,
            "modulation": "fm",
            "device": 0,
        }

        with (
            patch("routes.sstv.is_sstv_available", return_value=True),
            patch("routes.sstv.get_sstv_decoder", return_value=mock_decoder),
        ):
            response = client.post(
                "/sstv/start",
                data=json.dumps(payload),
                content_type="application/json",
            )

        assert response.status_code == 400
        data = response.get_json()
        assert data["status"] == "error"
        assert "Invalid frequency" in data["message"]
        mock_decoder.start.assert_not_called()

    @pytest.mark.parametrize("ppm", [None, ""])
    def test_start_uses_saved_device_ppm_when_blank(self, client, ppm):
        """A blank PPM falls back to the device's saved calibration (#238)."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.is_running = False
        mock_decoder.start.return_value = True
        mock_decoder.doppler_enabled = False
        mock_decoder.last_doppler_info = None

        with (
            patch("routes.sstv.is_sstv_available", return_value=True),
            patch("routes.sstv.get_sstv_decoder", return_value=mock_decoder),
            patch("routes.sstv.app_module.claim_sdr_device", return_value=None),
            patch("utils.sdr.device_config.config_for_index", return_value={"ppm": 68}),
        ):
            response = client.post(
                "/sstv/start", data=json.dumps({"device": 0, "ppm": ppm}), content_type="application/json"
            )

        assert response.status_code == 200
        assert mock_decoder.start.call_args.kwargs["ppm"] == 68
