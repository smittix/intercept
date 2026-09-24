"""Clicking along the waterfall faster than the SDR can restart must go
straight to the last frequency, not visit each one in turn."""

from __future__ import annotations

import json

from routes.waterfall_websocket import _coalesce_commands, _drain_waiting


def _start(mhz):
    return {"cmd": "start", "center_freq_mhz": mhz}


def _tune(mhz):
    return {"cmd": "tune", "vfo_freq_mhz": mhz}


def test_a_burst_of_starts_runs_only_the_last():
    assert _coalesce_commands([_start(100.1), _start(100.5), _start(101.3)]) == [_start(101.3)]


def test_a_start_supersedes_earlier_tunes_and_a_tune_earlier_tunes():
    assert _coalesce_commands([_tune(100.1), _start(100.5), _tune(100.6), _tune(100.7)]) == [
        _start(100.5),
        _tune(100.7),
    ]


def test_stop_supersedes_everything_before_it():
    assert _coalesce_commands([_start(100.1), _tune(100.2), {"cmd": "stop"}]) == [{"cmd": "stop"}]


def test_a_start_after_stop_still_runs():
    assert _coalesce_commands([{"cmd": "stop"}, _start(99.0)]) == [{"cmd": "stop"}, _start(99.0)]


def test_other_commands_keep_their_place():
    other = {"cmd": "set_gain", "gain": 30}
    assert _coalesce_commands([_start(100.1), other, _start(100.2)]) == [other, _start(100.2)]


class _FakeSocket:
    def __init__(self, messages):
        self.messages = list(messages)

    def receive(self, timeout=None):
        return self.messages.pop(0) if self.messages else None


def test_draining_takes_what_is_waiting_and_skips_junk():
    ws = _FakeSocket([json.dumps(_start(1.0)), "not json", json.dumps(_tune(2.0))])
    assert _drain_waiting(ws) == [_start(1.0), _tune(2.0)]
    assert _drain_waiting(ws) == []
