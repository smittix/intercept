"""AIS on a HackRF must point AIS-catcher at that HackRF by serial."""

from unittest.mock import MagicMock

import pytest

import app as app_module
from routes import ais
from utils.sdr import SDRFactory, SDRType


def _hackrf(serial):
    return SDRFactory.create_default_device(SDRType.HACKRF, index=0, serial=serial)


@pytest.fixture
def ais_start(client, monkeypatch):
    """Stub everything past building the command; record Popen's argv and claims."""
    calls = {"claims": [], "cmd": None}
    monkeypatch.setattr(ais, "find_ais_catcher", lambda: "/usr/bin/AIS-catcher")
    monkeypatch.setattr(ais.time, "sleep", lambda s: None)
    monkeypatch.setattr(app_module, "claim_sdr_device", lambda *a: calls["claims"].append(a))
    monkeypatch.setattr(app_module, "release_sdr_device", lambda *a: None)

    def fake_popen(cmd, **kwargs):
        calls["cmd"] = cmd
        proc = MagicMock()
        proc.poll.return_value = 1  # exits at once, so the route stops here
        proc.stderr = None
        return proc

    monkeypatch.setattr(ais.subprocess, "Popen", fake_popen)

    def start(devices):
        monkeypatch.setattr(ais, "detect_all_devices", lambda: devices)
        return client.post("/ais/start", json={"device": 0, "sdr_type": "hackrf", "gain": 40})

    return start, calls


def test_detected_serial_is_passed_to_ais_catcher(ais_start):
    start, calls = ais_start
    start([_hackrf("0000000000000000a06063c8234e925f")])
    assert calls["cmd"][calls["cmd"].index("-d") + 1] == "0000000000000000a06063c8234e925f"


@pytest.mark.parametrize("devices", [[], [_hackrf("N/A")]])
def test_no_serial_fails_clearly_before_claiming(ais_start, devices):
    start, calls = ais_start
    resp = start(devices)
    assert resp.status_code == 400
    assert "serial" in resp.get_json()["message"]
    assert calls["claims"] == [] and calls["cmd"] is None


def test_failed_start_does_not_keep_the_dead_process(ais_start):
    # A kept process object is signalled by the next /ais/stop: in production its
    # pid may have been reused, and here a MagicMock pid became killpg(1, ...),
    # i.e. kill(-1, SIGTERM) against every process the user owns.
    start, _ = ais_start
    resp = start([_hackrf("0000000000000000a06063c8234e925f")])
    assert resp.status_code == 500
    assert app_module.ais_process is None
