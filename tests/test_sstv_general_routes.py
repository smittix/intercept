"""Tests for general (terrestrial) SSTV route behavior."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _login_session(client) -> None:
    """Mark the Flask test session as authenticated."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "test"
        sess["role"] = "admin"


def _post_start(client, decoder, payload):
    with (
        patch("routes.sstv_general.get_general_sstv_decoder", return_value=decoder),
        patch("routes.sstv_general.app_module.claim_sdr_device", return_value=None),
    ):
        return client.post("/sstv-general/start", data=json.dumps(payload), content_type="application/json")


class TestSSTVGeneralRoutes:
    """General SSTV route tests."""

    def test_status_reports_running_frequency_and_modulation(self, client):
        """GET /sstv-general/status should report what the decoder is tuned to."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.decoder_available = "python-sstv"
        mock_decoder.is_running = True
        mock_decoder.frequency = 14.230
        mock_decoder.modulation = "usb"
        mock_decoder.get_images.return_value = []

        with patch("routes.sstv_general.get_general_sstv_decoder", return_value=mock_decoder):
            data = client.get("/sstv-general/status").get_json()

        assert data["running"] is True
        assert data["frequency"] == pytest.approx(14.230)
        assert data["modulation"] == "usb"

    def test_start_passes_gain_and_ppm(self, client):
        """POST /sstv-general/start should pass validated gain and PPM to the decoder."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.decoder_available = "python-sstv"
        mock_decoder.is_running = False
        mock_decoder.start.return_value = True

        response = _post_start(
            client, mock_decoder, {"frequency": 14.230, "modulation": "usb", "device": 0, "gain": 35.5, "ppm": 3}
        )

        assert response.status_code == 200
        call_kwargs = mock_decoder.start.call_args.kwargs
        assert call_kwargs["modulation"] == "usb"
        assert call_kwargs["gain"] == pytest.approx(35.5)
        assert call_kwargs["ppm"] == 3

    def test_start_defaults_to_auto_gain_and_no_ppm(self, client):
        """Omitted or blank gain/PPM should reach the decoder as None."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.decoder_available = "python-sstv"
        mock_decoder.is_running = False
        mock_decoder.start.return_value = True

        response = _post_start(client, mock_decoder, {"frequency": 14.230, "modulation": "usb", "gain": ""})

        assert response.status_code == 200
        call_kwargs = mock_decoder.start.call_args.kwargs
        assert call_kwargs["gain"] is None
        assert call_kwargs["ppm"] is None

    def test_start_rejects_invalid_ppm(self, client):
        """POST /sstv-general/start should reject an out-of-range PPM."""
        _login_session(client)
        mock_decoder = MagicMock()
        mock_decoder.decoder_available = "python-sstv"
        mock_decoder.is_running = False

        response = _post_start(client, mock_decoder, {"frequency": 14.230, "modulation": "usb", "ppm": 5000})

        assert response.status_code == 400
        mock_decoder.start.assert_not_called()
