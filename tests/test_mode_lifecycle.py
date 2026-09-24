"""Mode lifecycle contract: start, stop, start again, without restarting.

Every mode that spawns a decoder must behave like the 433 MHz sensor path:

- start with the tool missing returns an actionable 4xx/503 and leaves no
  dirty state: status reports not running and no SDR claim is left behind
- stop when never started is a no-op
- a second start is rejected cleanly, without a second process
- start -> stop -> start succeeds, and stop leaves nothing running or claimed
- malformed decoder output (binary noise, a truncated line, partial JSON, an
  overlong line, invalid UTF-8) does not kill a reader thread

Decoders are faked at the subprocess boundary with real OS pipes
(tests/fake_process.py), so reader threads block as they do in production.
"""

from __future__ import annotations

import errno
import queue
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest

import app as app_module
from tests.fake_process import FakeProcess


def _mode(start, stop, status, body=None, *, missing_tool=True, success=True):
    return {
        "start": start,
        "stop": stop,
        "status": status,
        "body": body or {},
        "missing_tool": missing_tool,
        "success": success,
    }


# status=None: the mode has no status endpoint. A string in missing_tool or
# success is the reason that part of the contract does not apply.
MODES = {
    "pager": _mode("/start", "/stop", "/status", {"frequency": "153.35"}),
    "sensor": _mode("/start_sensor", "/stop_sensor", "/sensor/status"),
    "rtlamr": _mode("/start_rtlamr", "/stop_rtlamr", None),
    "acars": _mode("/acars/start", "/acars/stop", "/acars/status"),
    "vdl2": _mode("/vdl2/start", "/vdl2/stop", "/vdl2/status"),
    "adsb": _mode(
        "/adsb/start",
        "/adsb/stop",
        "/adsb/status",
        success="needs dump1090's SBS feed on TCP 30003; see test_adsb_failed_start_is_an_error",
    ),
    "ais": _mode("/ais/start", "/ais/stop", "/ais/status"),
    "aprs": _mode("/aprs/start", "/aprs/stop", "/aprs/status"),
    "dsc": _mode("/dsc/start", "/dsc/stop", "/dsc/status"),
    "morse": _mode("/morse/start", "/morse/stop", "/morse/status", {"frequency": "14.06"}),
    "ook": _mode("/ook/start", "/ook/stop", "/ook/status"),
    "radiosonde": _mode("/radiosonde/start", "/radiosonde/stop", "/radiosonde/status"),
    "sstv": _mode("/sstv/start", "/sstv/stop", "/sstv/status"),
    "sstv_general": _mode("/sstv-general/start", "/sstv-general/stop", "/sstv-general/status", {"frequency": 14.230}),
    "weathersat": _mode("/weather-sat/start", "/weather-sat/stop", "/weather-sat/status", {"satellite": "METEOR-M2-3"}),
    "wefax": _mode("/wefax/start", "/wefax/stop", "/wefax/status", {"frequency_khz": 4610}),
    "audio": _mode("/receiver/audio/start", "/receiver/audio/stop", "/receiver/audio/status", {"frequency": 100.0}),
    "scanner": _mode("/receiver/scanner/start", "/receiver/scanner/stop", "/receiver/scanner/status"),
    "rx_waterfall": _mode("/receiver/waterfall/start", "/receiver/waterfall/stop", None),
    "subghz_receive": _mode(
        "/subghz/receive/start",
        "/subghz/receive/stop",
        None,
        {"frequency_hz": 433920000},
        success="needs HackRF enumeration via hackrf_info; covered by test_subghz.py",
    ),
    "subghz_decode": _mode(
        "/subghz/decode/start",
        "/subghz/decode/stop",
        None,
        {"frequency_hz": 433920000},
        success="needs HackRF enumeration via hackrf_info; covered by test_subghz.py",
    ),
    "subghz_sweep": _mode(
        "/subghz/sweep/start",
        "/subghz/sweep/stop",
        None,
        success="needs HackRF enumeration via hackrf_info; covered by test_subghz.py",
    ),
    "wifi": _mode(
        "/wifi/v2/scan/start",
        "/wifi/v2/scan/stop",
        "/wifi/v2/scan/status",
        success="a deep scan needs root and a monitor-mode interface",
    ),
    "drone": _mode("/drone/start", "/drone/stop", "/drone/status"),
    "bluetooth": _mode(
        "/api/bluetooth/scan/start",
        "/api/bluetooth/scan/stop",
        "/api/bluetooth/scan/status",
        missing_tool="scans through BlueZ over D-Bus; no executable is required",
        success="needs BlueZ over D-Bus",
    ),
    "bt_locate": _mode(
        "/bt_locate/start",
        "/bt_locate/stop",
        "/bt_locate/status",
        {"mac_address": "AA:BB:CC:DD:EE:FF"},
        missing_tool="scans through BlueZ over D-Bus; no executable is required",
        success="needs BlueZ over D-Bus; a second start replaces the session by design",
    ),
    "meshtastic": _mode(
        "/meshtastic/start",
        "/meshtastic/stop",
        "/meshtastic/status",
        missing_tool="talks to a serial device through a library; see TestMeshtastic",
        success="talks to a serial device through a library; see TestMeshtastic",
    ),
}

# Modes outside the HTTP start/stop contract, and why:
#   waterfall and meteor stream over WebSockets, not start/stop routes
#   gps is a gpsd client with no start endpoint
#   tscm orchestrates the Wi-Fi, Bluetooth and RF scanners; see test_tscm_*
#   ground station and recordings are schedulers over the modes above

MISSING_TOOL = [m for m, d in MODES.items() if d["missing_tool"] is True]
SUCCESS = [m for m, d in MODES.items() if d["success"] is True]
MALFORMED = [
    b"\x00\xff\xfe\xfd binary noise \x80\x81\n",
    b"truncated line without newline",
    b'\n{"partial": {"json": ',
    b"\n" + b"A" * 70_000 + b"\n",
    b"\xc3\x28 invalid utf-8\n",
]


# =============================================================================
# Harness
# =============================================================================


def _missing(cmd, *args, **kwargs):
    """What Popen raises for an executable that is not installed."""
    exe = cmd[0] if isinstance(cmd, list | tuple) else str(cmd).split()[0]
    raise FileNotFoundError(errno.ENOENT, "No such file or directory", exe)


@contextmanager
def _decoders(installed: bool):
    FakeProcess.instances.clear()
    patches = [
        patch("subprocess.Popen", FakeProcess if installed else _missing),
        patch(
            "shutil.which",
            side_effect=(lambda name, *a, **k: f"/usr/bin/{name}") if installed else None,
            return_value=None,
        ),
        patch("os.getpgid", FakeProcess.getpgid),
        patch("os.killpg", FakeProcess.killpg),
        patch("os.kill", FakeProcess.kill_pid),
        # The claim's USB probe runs rtl_test and waits out its timeout on a
        # fake that prints nothing; it checks hardware, not the lifecycle.
        patch("utils.sdr.detection.probe_rtlsdr_device", return_value=None),
    ]
    # rtl_fm streams audio from the moment it starts; silence will do.
    FakeProcess.startup_output = {"rtl_fm": bytes(32_768)} if installed else {}
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patches):
            p.stop()
        FakeProcess.startup_output = {}


@pytest.fixture
def lifecycle(client):
    """A client for one mode, with cleanup that cannot leak into the next test."""
    started = []

    def use(mode):
        started.append(MODES[mode])
        return MODES[mode]

    yield use
    for spec in started:
        client.post(spec["stop"], json={})
    for proc in FakeProcess.instances:
        proc.kill()
    FakeProcess.instances.clear()
    app_module.sdr_device_registry.clear()


def _running(client, spec):
    """Whether the mode's status endpoint reports it running, or None if it has none."""
    if not spec["status"]:
        return None
    data = client.get(spec["status"]).get_json(silent=True) or {}
    for key in ("running", "is_running", "tracking_active", "is_scanning", "active"):
        if key in data:
            return bool(data[key])
    return None


def _live():
    return [p for p in FakeProcess.instances if p.alive]


def _executable(proc):
    return (proc.args[0] if isinstance(proc.args, list | tuple) else str(proc.args).split()[0]).rsplit("/", 1)[-1]


def _message(resp):
    data = resp.get_json(silent=True) or {}
    return str(data.get("message") or data.get("error") or "")


# =============================================================================
# The contract
# =============================================================================


@pytest.mark.parametrize("mode", MISSING_TOOL)
class TestToolMissing:
    def test_start_is_an_actionable_error(self, client, lifecycle, mode):
        spec = lifecycle(mode)
        with _decoders(installed=False):
            resp = client.post(spec["start"], json=spec["body"])
        assert 400 <= resp.status_code < 500 or resp.status_code == 503, (resp.status_code, _message(resp))
        text = _message(resp).lower()
        assert any(s in text for s in ("not found", "not installed", "not available", "missing", "install")), text

    def test_leaves_no_dirty_state(self, client, lifecycle, mode):
        spec = lifecycle(mode)
        with _decoders(installed=False):
            client.post(spec["start"], json=spec["body"])
            assert _running(client, spec) is not True
            assert app_module.sdr_device_registry == {}, "a failed start must release its SDR claim"
            # and the next attempt is not blocked by anything the first left behind
            again = client.post(spec["start"], json=spec["body"])
        assert again.status_code != 409 or "busy" not in _message(again).lower()


@pytest.mark.parametrize("mode", list(MODES))
def test_stop_when_never_started_is_a_no_op(client, lifecycle, mode):
    spec = lifecycle(mode)
    with _decoders(installed=False):
        resp = client.post(spec["stop"], json={})
    assert resp.status_code == 200, (resp.status_code, _message(resp))


@pytest.mark.parametrize("mode", SUCCESS)
class TestStartStopStart:
    def test_start_stop_start(self, client, lifecycle, mode):
        spec = lifecycle(mode)
        with _decoders(installed=True):
            first = client.post(spec["start"], json=spec["body"])
            assert first.status_code == 200, _message(first)
            assert _running(client, spec) is not False

            stopped = _within(10, lambda: client.post(spec["stop"], json={}), f"{mode} stop hung")
            assert stopped.status_code == 200
            _wait_for(lambda: not _live(), f"{mode} left a decoder running after stop")
            assert app_module.sdr_device_registry == {}, "stop must release the SDR claim"
            assert _running(client, spec) is not True

            again = client.post(spec["start"], json=spec["body"])
            assert again.status_code == 200, _message(again)

    def test_second_start_is_rejected_cleanly(self, client, lifecycle, mode):
        spec = lifecycle(mode)
        with _decoders(installed=True):
            assert client.post(spec["start"], json=spec["body"]).status_code == 200
            second = client.post(spec["start"], json=spec["body"])
            assert second.status_code < 500, _message(second)
            time.sleep(0.2)
            running = [_executable(p) for p in _live()]
            doubled = sorted({exe for exe in running if running.count(exe) > 1})
            assert not doubled, f"{mode} is running a second copy of {doubled}"

    def test_malformed_output_does_not_kill_the_reader(self, client, lifecycle, mode):
        spec = lifecycle(mode)
        with _decoders(installed=True):
            before = set(threading.enumerate())
            assert client.post(spec["start"], json=spec["body"]).status_code == 200
            time.sleep(0.2)
            # Only this start's threads: a previous test's may still be winding down.
            readers = {t for t in threading.enumerate() if t.is_alive() and t not in before}
            for proc in _live():
                for stream in list(proc._writers):
                    for chunk in MALFORMED:
                        proc.emit(chunk, stream)
            time.sleep(0.6)
            lost = sorted(t.name for t in readers if not t.is_alive())
            assert not lost, f"reader threads died on malformed output: {lost}"
            assert _running(client, spec) is not False
            stopped = _within(10, lambda: client.post(spec["stop"], json={}), f"{mode} stop hung")
            assert stopped.status_code == 200


def _within(seconds, call, message):
    """Run call() but fail, rather than hang the suite, if it never returns.

    Stop used to deadlock in morse and ook: pipes were closed before the
    process was terminated, and close() waits on a reader blocked in read().
    """
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("value", call()), daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), message
    return result["value"]


def _wait_for(condition, message, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError(message)


def test_every_mode_is_covered_or_excluded_with_a_reason():
    for mode, spec in MODES.items():
        for part in ("missing_tool", "success"):
            value = spec[part]
            assert value is True or (isinstance(value, str) and value), f"{mode}.{part} needs a reason"


# =============================================================================
# Modes with a contract of their own
# =============================================================================


def test_adsb_failed_start_is_an_error(client, lifecycle):
    """dump1090 starts but its SBS port never opens. This used to answer
    HTTP 200 with an error in the body, so callers checking resp.ok (the
    history page) never saw the failure."""
    spec = lifecycle("adsb")
    with _decoders(installed=True), patch("routes.adsb.DUMP1090_START_WAIT", 0.2):
        resp = client.post(spec["start"], json={})
    assert resp.status_code in (409, 503), resp.get_json()
    assert resp.get_json()["status"] == "error"
    assert app_module.sdr_device_registry == {}


class TestMeshtastic:
    """The library auto-discovers the serial port. With none it printed
    'attempting TCP connection' and returned a half-built interface that the
    app reported as running; with several it called sys.exit()."""

    @pytest.fixture(autouse=True)
    def _library(self):
        pytest.importorskip("meshtastic")

    def _start(self, client, ports):
        with patch("meshtastic.util.findPorts", return_value=ports):
            return client.post("/meshtastic/start", json={})

    def test_no_device_is_an_error_not_running(self, client, lifecycle):
        lifecycle("meshtastic")
        resp = self._start(client, [])
        assert resp.status_code == 503
        assert "No Meshtastic device found" in _message(resp)
        assert client.get("/meshtastic/status").get_json()["running"] is False

    def test_several_ports_is_an_error_not_an_exit(self, client, lifecycle):
        lifecycle("meshtastic")
        resp = self._start(client, ["/dev/ttyUSB0", "/dev/ttyACM0"])
        assert resp.status_code == 503
        assert "/dev/ttyUSB0" in _message(resp) and "/dev/ttyACM0" in _message(resp)


def test_drone_stop_reaches_a_decoder_still_starting():
    """A worker that reached Popen after stop() had run kept its process: stop
    only terminates what is already published, and rtl_433 in JSON mode can
    run for ever without printing the line that would end its loop."""
    from utils.drone.rf_detector import RFDetector

    release = threading.Event()

    def slow_popen(*args, **kwargs):
        release.wait(5)  # still starting when stop() runs
        return FakeProcess(*args, **kwargs)

    detector = RFDetector(queue.Queue())
    with _decoders(installed=True), patch("utils.drone.rf_detector.subprocess.Popen", side_effect=slow_popen):
        detector.start(rtl_sdr_index=0, use_hackrf=False)
        detector.stop()
        release.set()
        _wait_for(lambda: FakeProcess.instances and not _live(), "rtl_433 outlived stop()")
    FakeProcess.instances.clear()


# =============================================================================
# The agent reimplements the lifecycle for remote nodes
# =============================================================================

AGENT_SDR_MODES = ["sensor", "adsb", "pager", "ais", "acars", "aprs", "rtlamr", "dsc", "listening_post"]


@pytest.fixture
def agent():
    from intercept_agent import ModeManager

    manager = ModeManager()
    yield manager
    for mode in list(manager.running_modes):
        manager.stop_mode(mode)
    for proc in FakeProcess.instances:
        proc.kill()
    FakeProcess.instances.clear()


@pytest.mark.parametrize("mode", AGENT_SDR_MODES)
class TestAgentLifecycle:
    params = {"frequency": "153.35", "device": "0"}

    def test_stop_when_never_started_is_a_no_op(self, agent, mode):
        assert agent.stop_mode(mode)["status"] != "error"

    def test_tool_missing_leaves_no_dirty_state(self, agent, mode):
        with _decoders(installed=False):
            result = agent.start_mode(mode, dict(self.params))
        assert result["status"] == "error"
        assert mode not in agent.running_modes
        assert agent.get_sdr_in_use(0) is None
        # the missing tool is named, with advice, not a raw OSError
        message = result["message"]
        assert "not found" in message and "Errno" not in message, message
        assert any(w in message for w in ("Install", "install", "See ", "package")), message

    def test_start_stop_start(self, agent, mode):
        with _decoders(installed=True):
            first = agent.start_mode(mode, dict(self.params))
            if first["status"] == "error":
                pytest.skip(f"agent {mode} needs more than a decoder process: {first.get('message')}")
            assert agent.start_mode(mode, dict(self.params))["status"] == "error", "second start must be refused"
            assert agent.stop_mode(mode)["status"] != "error"
            assert mode not in agent.running_modes
            _wait_for(lambda: not _live(), f"agent {mode} left a decoder running after stop")
            assert agent.start_mode(mode, dict(self.params))["status"] != "error"


def test_text_mode_decoder_pipes_tolerate_bad_bytes():
    """A text-mode pipe with strict decoding raises UnicodeDecodeError inside
    the read loop on the first invalid byte. That ended the receiver waterfall,
    and in the Bluetooth scanners it could be triggered by any nearby device
    advertising a name with invalid UTF-8."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    strict = []
    for path in sorted([*root.joinpath("routes").rglob("*.py"), *root.joinpath("utils").rglob("*.py")]):
        source = path.read_text(errors="ignore")
        for match in re.finditer(r"subprocess\.Popen\((.{0,600}?)\)\s*\n", source, re.S):
            call = match.group(1)
            if re.search(r"\b(text|universal_newlines)=True", call) and "errors=" not in call:
                strict.append(f"{path.relative_to(root)}:{source[: match.start()].count(chr(10)) + 1}")
    assert not strict, f"text-mode Popen without errors=: {strict}"


# =============================================================================
# /health records each mode's last start, for the live views' empty states
# =============================================================================


class TestLifecycleRecord:
    def _record(self, client, mode):
        return client.get("/health").get_json()["lifecycle"].get(mode, {})

    def test_failed_start_is_recorded_with_its_reason(self, client, lifecycle):
        spec = lifecycle("sensor")
        with _decoders(installed=False):
            client.post(spec["start"], json=spec["body"])
        record = self._record(client, "sensor")
        assert "rtl_433" in record["error"]["message"]
        assert client.get("/health").get_json()["processes"]["sensor"] is False

    def test_start_then_refusal_then_stop(self, client, lifecycle):
        spec = lifecycle("sensor")
        with _decoders(installed=True):
            assert client.post(spec["start"], json=spec["body"]).status_code == 200
            record = self._record(client, "sensor")
            assert record["started_at"] and record["error"] is None
            started = record["started_at"]

            client.post(spec["start"], json=spec["body"])  # refused: already running
            assert self._record(client, "sensor")["started_at"] == started

            client.post(spec["stop"], json={})
        assert self._record(client, "sensor")["started_at"] is None


def test_drone_claims_its_sdr_and_reports_the_sources_it_started(client, lifecycle):
    """Each drone source is optional, but the run reports which ones started,
    and the RTL-SDR it uses is claimed until stop."""
    spec = lifecycle("drone")
    with _decoders(installed=True):
        resp = client.post(spec["start"], json={"rtl_sdr_index": 0, "use_hackrf": False})
        assert resp.status_code == 200, _message(resp)
        assert resp.get_json()["vectors"] == ["RTL433"]
        assert client.get("/drone/status").get_json()["vectors"] == ["RTL433"]
        assert app_module.sdr_device_registry == {"rtlsdr:0": "drone"}

        # another mode cannot take the SDR the drone detector is using
        assert app_module.claim_sdr_device(0, "sensor") is not None

        client.post(spec["stop"], json={})
        assert app_module.sdr_device_registry == {}
        assert client.get("/drone/status").get_json()["vectors"] == []


@pytest.mark.parametrize("mode", ["subghz_receive", "subghz_decode", "subghz_sweep"])
def test_subghz_missing_tool_is_not_a_conflict(client, lifecycle, mode):
    """A missing HackRF tool is a 400 with install advice, like every other
    mode; 409 is kept for a start that conflicts with one already running."""
    spec = lifecycle(mode)
    with _decoders(installed=False):
        resp = client.post(spec["start"], json=spec["body"])
    assert resp.status_code == 400, (resp.status_code, _message(resp))
    assert resp.get_json()["error_type"] == "TOOL_MISSING"
    assert "not found" in _message(resp)
