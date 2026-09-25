"""Pytest configuration and fixtures."""

import contextlib
import os
import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest

# Must be set before importing app: stops the deferred background-init
# thread, whose subprocess/DB cleanup fires mid-session and races with
# test mocks (e.g. a patched subprocess.Popen catching its pkill call)
os.environ.setdefault("INTERCEPT_SKIP_DEFERRED_INIT", "1")

# A developer shell with auth disabled would make every gate test pass
# against an open app. Tests control auth through fixtures instead.
os.environ.pop("INTERCEPT_DISABLE_AUTH", None)

# Keep the whole session off the developer's own database. Importing app runs
# init_db(), which seeds users, and tests that emit observations or record
# events would otherwise write to instance/intercept.db. Must precede the
# app import; tests that patch DB_PATH for their own temp file still can.
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

import utils.database as _database  # noqa: E402

_SESSION_DB_DIR = tempfile.mkdtemp(prefix="intercept-tests-")
_database.DB_DIR = type(_database.DB_DIR)(_SESSION_DB_DIR)
_database.DB_PATH = _database.DB_DIR / "intercept.db"
atexit.register(shutil.rmtree, _SESSION_DB_DIR, ignore_errors=True)

from app import app as flask_app
from routes import register_blueprints


def pytest_ignore_collect(collection_path, config):
    """The browser smoke test (tests/smoke) runs only when asked for by path,
    e.g. `pytest tests/smoke`. It drives the whole app in a browser, which
    leaves state (caches) that other tests do not expect."""
    if "smoke" in collection_path.parts and not any("smoke" in str(arg) for arg in config.args):
        return True
    return None


@pytest.fixture(scope="session")
def app():
    """Create application for testing.

    Authentication stays enabled. Tests that only care about route behaviour
    use the logged-in `client`; tests of the gate itself use `anon_client`.
    """
    flask_app.config["TESTING"] = True
    # Disable CSRF for tests
    flask_app.config["WTF_CSRF_ENABLED"] = False
    # Register blueprints only if not already registered
    if "pager" not in flask_app.blueprints:
        register_blueprints(flask_app)
    return flask_app


@pytest.fixture
def client(app):
    """A logged-in test client."""
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["role"] = "admin"
    return c


@pytest.fixture
def anon_client(app):
    """An unauthenticated client, for testing the auth gate itself."""
    return app.test_client()


@pytest.fixture
def mock_subprocess():
    """Patch subprocess.Popen and subprocess.run with configurable returns.

    Usage:
        def test_example(mock_subprocess):
            mock_subprocess['run'].return_value.stdout = 'output'
            mock_subprocess['run'].return_value.returncode = 0
    """
    with patch("subprocess.Popen") as mock_popen, patch("subprocess.run") as mock_run:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        yield {
            "popen": mock_popen,
            "process": mock_process,
            "run": mock_run,
        }


@pytest.fixture
def mock_sdr_device():
    """Return a mock SDRDevice with configurable type and index.

    Usage:
        def test_example(mock_sdr_device):
            device = mock_sdr_device(device_type='rtlsdr', index=0)
    """

    def _factory(device_type="rtlsdr", index=0):
        device = MagicMock()
        device.device_type = device_type
        device.device_index = index
        device.name = f"Mock {device_type} #{index}"
        device.is_available.return_value = True
        device.build_command.return_value = ["rtl_fm", "-f", "100M"]
        return device

    return _factory


@pytest.fixture
def mock_app_state():
    """Patch common app module attributes for route tests.

    Provides mock process, queue, and lock objects on the app module.
    """
    import queue

    import app as app_module

    mock_process = MagicMock()
    mock_process.poll.return_value = None
    mock_queue = queue.Queue()
    mock_lock = MagicMock()

    patches = {
        "current_process": mock_process,
        "pager_queue": mock_queue,
        "pager_lock": mock_lock,
    }
    originals = {}
    for attr, value in patches.items():
        originals[attr] = getattr(app_module, attr, None)
        setattr(app_module, attr, value)

    yield {
        "process": mock_process,
        "queue": mock_queue,
        "lock": mock_lock,
        "module": app_module,
    }

    for attr, orig in originals.items():
        if orig is None:
            with contextlib.suppress(AttributeError):
                delattr(app_module, attr)
        else:
            setattr(app_module, attr, orig)


@pytest.fixture
def mock_check_tool():
    """Patch check_tool() to return True for all tools."""
    with patch("utils.dependencies.check_tool", return_value=True) as mock:
        yield mock


@pytest.fixture
def test_db(tmp_path):
    """Provide an isolated in-memory SQLite database for tests."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _isolate_tle_store(tmp_path, monkeypatch):
    """Every test gets a throwaway TLE store; nothing touches instance/tle.db."""
    from utils import tle_store

    monkeypatch.setattr(tle_store, "_DB_PATH", tmp_path / "tle.db")
    tle_store._reset_for_tests()
    yield
    tle_store._reset_for_tests()


@pytest.fixture
def fake_process():
    """Factory for complete subprocess.Popen replacements.

    Hand-rolled Popen mocks keep missing two things: __enter__ (subprocess.run
    wraps Popen in a context manager) and a communicate() tuple. Use this
    factory instead of building MagicMock processes inline.

    Defaults are str (for text=True subprocesses); pass bytes explicitly for binary-mode callers.
    """

    def _make(returncode=0, stdout="", stderr="", running=True, pid=12345):
        proc = MagicMock()
        proc.poll.return_value = None if running else returncode
        proc.returncode = returncode
        proc.pid = pid
        proc.wait.return_value = returncode
        proc.communicate.return_value = (stdout, stderr)
        proc.stdout.read.return_value = stdout
        proc.stderr.read.return_value = stderr
        proc.stdin = MagicMock()
        proc.__enter__.return_value = proc
        proc.__exit__.return_value = False
        return proc

    return _make


@pytest.fixture
def tscm_survey():
    """A complete TSCM survey, as the report routes would assemble it.

    Returns keyword arguments for utils.tscm.reports.generate_report(). The
    device profiles and timelines are built from the real correlation and
    timeline classes and serialised with their own to_dict(), so the shapes
    cannot drift from what the routes pass in production.

    Five devices across three protocols: two high interest (an AirTag seen
    during the meeting, a persistent 433 MHz carrier), two needing review
    (an unknown audio-capable speaker, a hidden-SSID access point) and one
    informational (the site's known printer).

    Two clocks, as in production: the sweep record's times are UTC, as
    SQLite's CURRENT_TIMESTAMP stores them; device and meeting times are
    local, as the correlation engine records datetime.now(). The sweep runs
    09:00-10:30 UTC, which is 10:00-11:30 local in September (BST).
    """
    from datetime import datetime, timedelta

    from utils.tscm.advanced import DeviceTimeline
    from utils.tscm.correlation import DeviceProfile, IndicatorType

    start = datetime(2026, 9, 24, 10, 0, 0)  # local

    def profile(identifier, protocol, name, indicators, rssi, seen_minutes, count, **extra):
        p = DeviceProfile(identifier=identifier, protocol=protocol, name=name, **extra)
        for kind, description in indicators:
            p.add_indicator(kind, description)
        p.first_seen = start + timedelta(minutes=5)
        p.last_seen = p.first_seen + timedelta(minutes=seen_minutes)
        p.detection_count = count
        p.rssi_samples = [(p.first_seen + timedelta(minutes=i), rssi) for i in range(min(count, 10))]
        return p

    profiles = [
        profile(
            "4C:E6:76:12:34:56",
            "bluetooth",
            None,
            [
                (IndicatorType.AIRTAG_DETECTED, "Apple AirTag advertisement"),
                (IndicatorType.PERSISTENT, "Present for most of the sweep"),
                (IndicatorType.MEETING_CORRELATED, "First seen during the board meeting"),
            ],
            rssi=-48,
            seen_minutes=80,
            count=40,
            tracker_type="airtag",
        ),
        profile(
            "433.920",
            "rf",
            "433.92 MHz carrier",
            [
                (IndicatorType.NARROWBAND_SIGNAL, "Narrowband emission"),
                (IndicatorType.ALWAYS_ON_CARRIER, "Carrier present throughout"),
                (IndicatorType.PERSISTENT, "Present for most of the sweep"),
            ],
            rssi=-61,
            seen_minutes=85,
            count=30,
            frequency=433.92,
        ),
        profile(
            "F0:12:34:AB:CD:EF",
            "bluetooth",
            "JBL Flip 5",
            [
                (IndicatorType.AUDIO_CAPABLE, "Advertises an audio service"),
                (IndicatorType.UNKNOWN_DEVICE, "Not in the baseline"),
            ],
            rssi=-74,
            seen_minutes=2,
            count=2,
        ),
        profile(
            "02:11:22:33:44:55",
            "wifi",
            None,
            [
                (IndicatorType.HIDDEN_IDENTITY, "Hidden SSID"),
                (IndicatorType.UNKNOWN_DEVICE, "Not in the baseline"),
            ],
            rssi=-66,
            seen_minutes=60,
            count=12,
            is_hidden=True,
        ),
        profile("00:1B:A9:77:88:99", "wifi", "Office Printer", [], rssi=-58, seen_minutes=85, count=50),
    ]
    profiles[4].known_device = True
    profiles[4].known_device_name = "Reception printer"

    def timeline(p, rssi_min, rssi_max, pattern, meeting):
        return DeviceTimeline(
            identifier=p.identifier,
            protocol=p.protocol,
            name=p.name,
            first_seen=p.first_seen,
            last_seen=p.last_seen,
            total_observations=p.detection_count,
            presence_ratio=0.9,
            rssi_min=rssi_min,
            rssi_max=rssi_max,
            rssi_mean=(rssi_min + rssi_max) / 2,
            rssi_stability=0.8,
            appears_stationary=pattern == "stationary",
            movement_pattern=pattern,
            meeting_correlated=meeting,
            meeting_observations=6 if meeting else 0,
        ).to_dict()

    timelines = [
        timeline(profiles[0], -52, -45, "stationary", True),
        timeline(profiles[1], -63, -59, "stationary", False),
        timeline(profiles[2], -78, -70, "mobile", False),
        timeline(profiles[3], -69, -63, "stationary", False),
        timeline(profiles[4], -60, -56, "stationary", False),
    ]

    return {
        "sweep_id": 42,
        "sweep_data": {
            "id": 42,
            "baseline_id": 7,
            "started_at": "2026-09-24 09:00:00",
            "completed_at": "2026-09-24 10:30:00",
            "status": "completed",
            "sweep_type": "standard",
            "results": {"wifi_count": 14, "wifi_client_count": 9, "bt_count": 23, "rf_count": 4},
            "anomalies": [],
            "threats_found": 2,
        },
        "device_profiles": [p.to_dict() for p in profiles],
        "capabilities": {
            "wifi": {"mode": "monitor", "interface": "wlan1", "limitations": []},
            "bluetooth": {"mode": "bluez", "adapter": "hci0", "limitations": []},
            "rf": {"device_type": "rtlsdr", "available": True, "limitations": ["No coverage above 1.7 GHz"]},
            "all_limitations": ["No coverage above 1.7 GHz", "Single-antenna Bluetooth; no direction finding"],
        },
        "timelines": timelines,
        "baseline_diff": {
            "baseline_id": 7,
            "summary": {"new_devices": 3, "missing_devices": 1, "changed_devices": 0},
        },
        "meeting_summaries": [
            {
                "name": "Board meeting",
                "start_time": "2026-09-24T10:30:00",
                "end_time": "2026-09-24T11:15:00",
                "duration_minutes": 45,
                "devices_first_seen": 1,
                "behavior_changes": 0,
                "high_interest_devices": 1,
            }
        ],
        "site_name": "Head Office, 3rd floor boardroom",
        "examiner_name": "A. Examiner",
    }


# Peak memory for the whole run. A MagicMock standing in for a queue once
# leaked 16 GB here and went unnoticed for months. A full run peaks near
# 350 MB (each CI shard near 270 MB), so a real leak cannot hide under 1 GB.
RSS_CEILING_MB = int(os.environ.get("INTERCEPT_TEST_RSS_CEILING_MB", "1024"))


def pytest_sessionfinish(session, exitstatus):
    try:
        import resource
    except ImportError:  # Windows
        return
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024  # bytes on macOS, KiB on Linux
    if peak_mb > RSS_CEILING_MB:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        message = f"peak RSS {peak_mb:.0f} MB exceeds the {RSS_CEILING_MB} MB ceiling (memory leak?)"
        if reporter:
            reporter.write_line(f"FAILED: {message}", red=True, bold=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
