"""The event pipeline runs once per decoded event, with or without a browser.

It used to run inside each browser's SSE stream: an alert rule, a
recording, an MQTT publish and an activity-feed observation happened once
per open tab, and not at all with none open.
"""

from __future__ import annotations

import queue
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import app as app_module
from utils import event_pipeline
from utils.sse import subscribe_fanout_queue

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def processed():
    """Every (mode, event, event_type) the pipeline processes during a test."""
    calls = []
    event_pipeline.drain()
    with patch.object(event_pipeline, "process_event", side_effect=lambda *args: calls.append(args)):
        yield calls
        event_pipeline.drain()


def _wait_for(predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _mine(calls, marker):
    return [c for c in calls if isinstance(c[1], dict) and c[1].get("marker") == marker]


class TestModeQueues:
    def test_an_event_is_processed_with_no_browser_open(self, processed):
        event = {"type": "sensor", "id": "Acurite:1", "marker": "no-browser"}
        app_module.sensor_queue.put(event)
        assert _wait_for(lambda: _mine(processed, "no-browser"))
        assert _mine(processed, "no-browser") == [("sensor", event, "sensor")]

    def test_an_event_is_processed_once_with_two_browsers_open(self, processed):
        tabs = [subscribe_fanout_queue(app_module.output_queue, "pager") for _ in range(2)]
        try:
            event = {"type": "message", "address": "1234567", "marker": "two-tabs"}
            app_module.output_queue.put(event)
            received = [tab.get(timeout=4) for tab, _ in tabs]
            assert received == [event, event], "each browser still receives the event"
            event_pipeline.drain()
            assert _mine(processed, "two-tabs") == [("pager", event, "message")]
        finally:
            for _, unsubscribe in tabs:
                unsubscribe()

    def test_every_pipeline_channel_is_registered_at_startup(self):
        """A stream route that subscribes with a key the startup table lacks
        would leave its mode out of the pipeline; one that registers a
        second key on the same queue would split its messages between two
        distributors."""
        from utils.sse import _fanout_channels

        for key in (
            "pager",
            "sensor",
            "rtlamr",
            "wifi",
            "bluetooth",
            "acars",
            "vdl2",
            "aprs",
            "ais",
            "dsc",
            "ook",
            "morse",
            "tscm",
            "sstv",
            "sstv_general",
            "receiver_scanner",
            "receiver_waterfall",
        ):
            channel = _fanout_channels[key]
            assert channel.on_ingest is not None, key
            assert channel.distributor is not None and channel.distributor.is_alive(), key

    def test_no_stream_route_runs_the_pipeline_per_subscriber(self):
        offenders = []
        for path in sorted(ROOT.joinpath("routes").rglob("*.py")):
            source = path.read_text(errors="ignore")
            if "on_message=" in source:
                offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, offenders


class TestDirectIngestion:
    def test_adsb_update_is_processed_with_no_browser_open(self, processed):
        from routes.adsb import _broadcast_adsb_update

        update = {"type": "aircraft", "icao": "4CA7B1", "marker": "adsb"}
        _broadcast_adsb_update(update)
        event_pipeline.drain()
        assert _mine(processed, "adsb") == [("adsb", update, "aircraft")]

    def test_bluetooth_scanner_event_uses_the_stream_name_and_shape(self, processed):
        from utils.bluetooth.scanner import BluetoothScanner

        scanner = BluetoothScanner.__new__(BluetoothScanner)
        scanner._event_queue = queue.Queue(maxsize=10)
        device = {"address": "AA:BB:CC:DD:EE:01", "marker": "bt"}
        scanner._queue_event({"type": "device", "device": device})
        event_pipeline.drain()
        assert _mine(processed, "bt") == [("bluetooth", device, "device_update")]

    def test_wifi_scanner_event_is_processed_with_no_browser_open(self, processed):
        from utils.wifi.scanner import UnifiedWiFiScanner

        scanner = UnifiedWiFiScanner.__new__(UnifiedWiFiScanner)
        scanner._event_queue = queue.Queue(maxsize=10)
        event = {"type": "network_update", "bssid": "AA:BB:CC:DD:EE:FF", "marker": "wifi"}
        scanner._queue_event(event)
        event_pipeline.drain()
        assert _mine(processed, "wifi") == [("wifi", event, "network_update")]


def test_a_backed_up_pipeline_drops_rather_than_blocking_the_decoder(monkeypatch):
    monkeypatch.setattr(event_pipeline, "_pending", queue.Queue(maxsize=1))
    monkeypatch.setattr(event_pipeline, "_ensure_worker", lambda: None)  # nothing drains it
    monkeypatch.setattr(event_pipeline, "_dropped", 0)
    started = time.monotonic()
    for i in range(3):
        event_pipeline.submit("adsb", {"type": "aircraft", "icao": f"{i:06X}"})
    assert time.monotonic() - started < 0.5
    assert event_pipeline._dropped == 2
