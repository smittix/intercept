"""Monitor audio from the waterfall's shared IQ capture, and when not to use it."""

import pytest

import app as app_module
import routes.listening_post as _state
import routes.listening_post.audio as audio
import routes.waterfall_websocket as wf_ws


@pytest.fixture
def shared_capture(monkeypatch):
    """A waterfall capture running on device 0; record shared-monitor starts."""
    calls = []
    monkeypatch.setattr(wf_ws, "get_shared_capture_status", lambda: {"running": True, "device": 0})
    monkeypatch.setattr(wf_ws, "start_shared_monitor_from_capture", lambda **kw: calls.append(kw) or (True, "started"))
    # The waterfall still holds the SDR: a fresh claim fails
    monkeypatch.setattr(app_module, "claim_sdr_device", lambda *a: "SDR device hackrf:0 is in use by waterfall")
    monkeypatch.setattr(audio.time, "sleep", lambda s: None)
    yield calls
    _state.audio_running = False
    _state.audio_source = "process"


def _start(client, **extra):
    body = {"frequency": 96.5, "modulation": "wfm", "sdr_type": "hackrf", "device": 0, **extra}
    return client.post("/receiver/audio/start", json=body)


def test_monitor_shares_the_running_waterfall_capture(client, shared_capture):
    resp = _start(client)
    assert resp.status_code == 200
    assert resp.get_json()["source"] == "waterfall"
    assert len(shared_capture) == 1


def test_allow_shared_false_never_attaches_to_the_capture(client, shared_capture):
    # The client sends this right after pausing the waterfall: attaching to a
    # capture that is winding down left the monitor with no audio at all.
    resp = _start(client, allow_shared=False)
    assert shared_capture == []
    assert resp.status_code == 409
    assert resp.get_json()["error_type"] == "DEVICE_BUSY"
