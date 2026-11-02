"""Remote SDR (rtl_tcp) start paths.

Found against real rtl_tcp servers: stock rtl_fm cannot open
``rtl_tcp:host:port`` and exits at once with "No supported devices found.",
yet Pager and DSC reported "started". Meters ignored the remote host and
claimed the local dongle instead. rtl_fm modes now demodulate remote devices
with the rtltcp_iq.py | csdr pipeline.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

import app as app_module
from tests.fake_process import FakeProcess
from tests.test_mode_lifecycle import _decoders, _executable, _live, _message

REMOTE = {"rtl_tcp_host": "100.85.185.5", "rtl_tcp_port": 1234}


BUSY = b"rtltcp_iq: no RTL0 header from rtl_tcp server 100.85.185.5:1234 (busy with another client, or not rtl_tcp)\n"


def _is_remote_pipeline(proc):
    return _executable(proc) == "bash" and any("rtltcp_iq.py" in arg for arg in proc.args)


class _RtlTcpBusy(FakeProcess):
    """The rtl_tcp server is serving another client: the pipeline exits at once."""

    def __init__(self, args, *a, **k):
        super().__init__(args, *a, **k)
        if _is_remote_pipeline(self):
            self.emit(BUSY, stream="stderr")
            self._exit(1)


@pytest.fixture
def clean(client):
    yield client
    for path in ("/stop", "/dsc/stop", "/aprs/stop", "/morse/stop", "/stop_rtlamr"):
        client.post(path, json={})
    for proc in FakeProcess.instances:
        proc.kill()
    FakeProcess.instances.clear()
    app_module.sdr_device_registry.clear()


@pytest.mark.parametrize(
    "start,body",
    [
        ("/start", {"frequency": "153.35", **REMOTE}),
        ("/dsc/start", dict(REMOTE)),
    ],
)
def test_remote_source_exiting_at_startup_is_reported(clean, start, body):
    with _decoders(installed=True), patch("subprocess.Popen", _RtlTcpBusy):
        resp = clean.post(start, json=body)
        time.sleep(0.1)
        live = _live()
    assert resp.status_code >= 400, resp.get_json()
    text = _message(resp)
    assert "busy with another client" in text
    assert "rtl_tcp" in text
    assert live == [], [p.args for p in live]
    assert app_module.sdr_device_registry == {}


@pytest.mark.parametrize(
    "start,body",
    [
        ("/start", {"frequency": "153.35", **REMOTE}),
        ("/dsc/start", dict(REMOTE)),
        ("/aprs/start", dict(REMOTE)),
        ("/morse/start", {"frequency": "14.06", **REMOTE}),
    ],
)
def test_remote_start_streams_through_rtltcp_client_not_rtl_fm(clean, start, body):
    with _decoders(installed=True):
        FakeProcess.startup_output["bash"] = bytes(32_768)  # the pipeline's audio
        resp = clean.post(start, json=body)
        procs = list(FakeProcess.instances)
    assert resp.status_code == 200, _message(resp)
    assert "rtl_fm" not in [_executable(p) for p in procs]
    script = next(p for p in procs if _is_remote_pipeline(p)).args[2]
    assert "--host 100.85.185.5 --port 1234" in script
    assert app_module.sdr_device_registry == {}


def test_rtlamr_uses_the_remote_rtl_tcp_server(clean):
    with _decoders(installed=True):
        resp = clean.post("/start_rtlamr", json={"frequency": "912.0", **REMOTE})
        procs = list(FakeProcess.instances)
    assert resp.status_code == 200, _message(resp)
    names = [_executable(p) for p in procs]
    assert "rtl_tcp" not in names, "a remote server needs no local rtl_tcp"
    rtlamr = next(p for p in procs if _executable(p) == "rtlamr")
    assert "-server=100.85.185.5:1234" in rtlamr.args
    assert app_module.sdr_device_registry == {}, "a remote SDR must not claim a local device"


def test_rtlamr_local_rtl_tcp_listens_on_loopback_only(clean):
    with _decoders(installed=True), patch("routes.rtlamr.time.sleep"):
        resp = clean.post("/start_rtlamr", json={"frequency": "912.0"})
        procs = list(FakeProcess.instances)
    assert resp.status_code == 200, _message(resp)
    rtl_tcp = next(p for p in procs if _executable(p) == "rtl_tcp")
    assert rtl_tcp.args[rtl_tcp.args.index("-a") + 1] == "127.0.0.1"
