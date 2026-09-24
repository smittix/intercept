"""Radiosonde weather balloon tracking routes.

Uses radiosonde_auto_rx to automatically scan for and decode radiosonde
telemetry (position, altitude, temperature, humidity, pressure) on the
400-406 MHz band.  Telemetry arrives as JSON over UDP.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.constants import (
    MAX_RADIOSONDE_AGE_SECONDS,
    PROCESS_TERMINATE_TIMEOUT,
    RADIOSONDE_TERMINATE_TIMEOUT,
    RADIOSONDE_UDP_PORT,
    SSE_KEEPALIVE_INTERVAL,
    SSE_QUEUE_TIMEOUT,
)
from utils.gps import is_gpsd_running
from utils.logging import get_logger
from utils.responses import api_error, api_success
from utils.sdr import SDRFactory, SDRType
from utils.sdr.device_config import apply_device_defaults
from utils.sse import sse_stream_fanout
from utils.validation import (
    validate_device_index,
    validate_gain,
    validate_latitude,
    validate_longitude,
    validate_ppm,
)

logger = get_logger("intercept.radiosonde")

radiosonde_bp = Blueprint("radiosonde", __name__, url_prefix="/radiosonde")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Track radiosonde state
radiosonde_running = False
radiosonde_active_device: int | None = None
radiosonde_active_sdr_type: str | None = None

# Active balloon data: serial -> telemetry dict
radiosonde_balloons: dict[str, dict[str, Any]] = {}
_balloons_lock = threading.Lock()

# UDP listener socket reference (so /stop can close it)
_udp_socket: socket.socket | None = None

# Common installation paths for radiosonde_auto_rx
AUTO_RX_PATHS = [
    "/opt/radiosonde_auto_rx/auto_rx/auto_rx.py",
    "/usr/local/bin/radiosonde_auto_rx",
    "/opt/auto_rx/auto_rx.py",
]


def find_auto_rx() -> str | None:
    """Find radiosonde_auto_rx script/binary."""
    # Check PATH first
    path = shutil.which("radiosonde_auto_rx")
    if path:
        return path
    # Check common locations
    for p in AUTO_RX_PATHS:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    # Check for Python script (not executable but runnable)
    for p in AUTO_RX_PATHS:
        if os.path.isfile(p):
            return p
    return None


def _resolve_shebang_interpreter(script_path: str) -> str | None:
    """Resolve a Python interpreter from a script shebang if possible."""
    try:
        with open(script_path, encoding="utf-8", errors="ignore") as handle:
            first_line = handle.readline().strip()
    except OSError:
        return None

    if not first_line.startswith("#!"):
        return None

    parts = shlex.split(first_line[2:].strip())
    if not parts:
        return None

    if os.path.basename(parts[0]) == "env" and len(parts) > 1:
        return shutil.which(parts[1])

    return parts[0]


def _resolve_pip_python(pip_bin: str | None) -> str | None:
    """Resolve the Python interpreter used by a pip executable."""
    if not pip_bin:
        return None
    return _resolve_shebang_interpreter(pip_bin)


def _build_auto_rx_env(auto_rx_dir: str) -> dict[str, str]:
    """Build environment for radiosonde_auto_rx with compatibility shims."""
    env = os.environ.copy()
    python_path_entries = [PROJECT_ROOT, auto_rx_dir]
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        python_path_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(entry for entry in python_path_entries if entry)
    return env


def _iter_auto_rx_python_candidates(auto_rx_path: str):
    """Yield plausible Python interpreters for radiosonde_auto_rx."""
    auto_rx_abs = os.path.abspath(auto_rx_path)
    auto_rx_dir = os.path.dirname(auto_rx_abs)
    install_root = os.path.dirname(auto_rx_dir)
    install_parent = os.path.dirname(install_root)

    candidates = [
        _resolve_shebang_interpreter(auto_rx_abs),
        sys.executable,
        os.path.join(install_root, "venv", "bin", "python"),
        os.path.join(install_root, "venv", "bin", "python3"),
        os.path.join(install_root, ".venv", "bin", "python"),
        os.path.join(install_root, ".venv", "bin", "python3"),
        os.path.join(auto_rx_dir, "venv", "bin", "python"),
        os.path.join(auto_rx_dir, "venv", "bin", "python3"),
        os.path.join(auto_rx_dir, ".venv", "bin", "python"),
        os.path.join(auto_rx_dir, ".venv", "bin", "python3"),
        os.path.join(install_parent, "venv", "bin", "python"),
        os.path.join(install_parent, "venv", "bin", "python3"),
        os.path.join(install_parent, ".venv", "bin", "python"),
        os.path.join(install_parent, ".venv", "bin", "python3"),
        _resolve_pip_python(shutil.which("pip3")),
        _resolve_pip_python(shutil.which("pip")),
        shutil.which("python3"),
        shutil.which("python"),
        "/usr/local/bin/python3",
        "/usr/local/bin/python",
        "/usr/bin/python3",
    ]

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        candidate_abs = os.path.abspath(candidate)
        if candidate_abs in seen:
            continue
        seen.add(candidate_abs)
        if os.path.isfile(candidate_abs) and os.access(candidate_abs, os.X_OK):
            yield candidate_abs


def _resolve_auto_rx_python(auto_rx_path: str) -> tuple[str | None, str, list[str]]:
    """Pick a Python interpreter that can import autorx.scan successfully."""
    auto_rx_dir = os.path.dirname(os.path.abspath(auto_rx_path))
    auto_rx_env = _build_auto_rx_env(auto_rx_dir)
    checked: list[str] = []
    last_error = "No usable Python interpreter found"

    for python_bin in _iter_auto_rx_python_candidates(auto_rx_path):
        checked.append(python_bin)
        try:
            dep_check = subprocess.run(
                [python_bin, "-c", "import autorx.scan"],
                cwd=auto_rx_dir,
                env=auto_rx_env,
                capture_output=True,
                timeout=10,
            )
        except Exception as exc:
            last_error = str(exc)
            continue

        if dep_check.returncode == 0:
            return python_bin, "", checked

        stderr_output = dep_check.stderr.decode("utf-8", errors="ignore").strip()
        stdout_output = dep_check.stdout.decode("utf-8", errors="ignore").strip()
        last_error = stderr_output or stdout_output or f"Interpreter exited with code {dep_check.returncode}"

    return None, last_error, checked


def generate_station_cfg(
    freq_min: float = 400.0,
    freq_max: float = 406.0,
    gain: float = 40.0,
    device_index: int = 0,
    ppm: int = 0,
    bias_t: bool = False,
    udp_port: int = RADIOSONDE_UDP_PORT,
    latitude: float = 0.0,
    longitude: float = 0.0,
    station_alt: float = 0.0,
    gpsd_enabled: bool = False,
) -> str:
    """Generate a station.cfg for radiosonde_auto_rx and return the file path."""
    cfg_dir = os.path.abspath(os.path.join("data", "radiosonde"))
    log_dir = os.path.join(cfg_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    cfg_path = os.path.join(cfg_dir, "station.cfg")

    # Full station.cfg based on radiosonde_auto_rx v1.8+ example config.
    # All sections and keys included to avoid missing-key crashes.
    cfg = f"""# Auto-generated by INTERCEPT for radiosonde_auto_rx

[sdr]
sdr_type = RTLSDR
sdr_quantity = 1
sdr_hostname = localhost
sdr_port = 5555

[sdr_1]
device_idx = {device_index}
ppm = {ppm}
gain = {gain}
bias = {str(bias_t)}

[search_params]
min_freq = {freq_min}
max_freq = {freq_max}
rx_timeout = 180
only_scan = []
never_scan = []
always_scan = []
always_decode = []

[location]
station_lat = {latitude}
station_lon = {longitude}
station_alt = {station_alt}
gpsd_enabled = {str(gpsd_enabled)}
gpsd_host = localhost
gpsd_port = 2947

[habitat]
uploader_callsign = INTERCEPT
upload_listener_position = False
uploader_antenna = unknown

[sondehub]
sondehub_enabled = False
sondehub_upload_rate = 15
sondehub_contact_email = none@none.com

[aprs]
aprs_enabled = False
aprs_user = N0CALL
aprs_pass = 00000
upload_rate = 30
aprs_server = radiosondy.info
aprs_port = 14580
station_beacon_enabled = False
station_beacon_rate = 30
station_beacon_comment = radiosonde_auto_rx
station_beacon_icon = /`
aprs_object_id = <id>
aprs_use_custom_object_id = False
aprs_custom_comment = <type> <freq>

[oziplotter]
ozi_enabled = False
ozi_update_rate = 5
ozi_host = 127.0.0.1
ozi_port = 8942
payload_summary_enabled = True
payload_summary_host = 127.0.0.1
payload_summary_port = {udp_port}

[email]
email_enabled = False
launch_notifications = True
landing_notifications = True
encrypted_sonde_notifications = True
landing_range_threshold = 30
landing_altitude_threshold = 1000
error_notifications = False
smtp_server = localhost
smtp_port = 25
smtp_authentication = None
smtp_login = None
smtp_password = None
from = sonde@localhost
to = none@none.com
subject = Sonde launch detected

[rotator]
rotator_enabled = False
update_rate = 30
rotation_threshold = 5.0
rotator_hostname = 127.0.0.1
rotator_port = 4533
rotator_homing_enabled = False
rotator_homing_delay = 10
rotator_home_azimuth = 0.0
rotator_home_elevation = 0.0
azimuth_only = False

[logging]
per_sonde_log = True
save_system_log = False
enable_debug_logging = False
save_cal_data = False

[web]
web_host = 127.0.0.1
web_port = 0
archive_age = 120
web_control = False
web_password = none
kml_refresh_rate = 10

[debugging]
save_detection_audio = False
save_decode_audio = False
save_decode_iq = False
save_raw_hex = False

[advanced]
search_step = 800
snr_threshold = 10
max_peaks = 10
min_distance = 1000
scan_dwell_time = 20
detect_dwell_time = 5
scan_delay = 10
quantization = 10000
decoder_spacing_limit = 15000
temporary_block_time = 120
max_async_scan_workers = 4
synchronous_upload = True
payload_id_valid = 3
sdr_fm_path = rtl_fm
sdr_power_path = rtl_power
ss_iq_path = ./ss_iq
ss_power_path = ./ss_power

[filtering]
max_altitude = 50000
max_radius_km = 1000
min_radius_km = 0
radius_temporary_block = False
sonde_time_threshold = 3
"""

    try:
        with open(cfg_path, "w") as f:
            f.write(cfg)
    except OSError as e:
        logger.error(f"Cannot write station.cfg to {cfg_path}: {e}")
        raise RuntimeError(
            f"Cannot write radiosonde config to {cfg_path}: {e}. "
            f"Fix permissions with: sudo chown -R $(whoami) {cfg_dir}"
        ) from e

    # When running as root via sudo, fix ownership so next non-root run
    # can still read/write these files.
    _fix_data_ownership(cfg_dir)

    logger.info(f"Generated station.cfg at {cfg_path}")
    return cfg_path


def _fix_data_ownership(path: str) -> None:
    """Recursively chown a path to the real (non-root) user when running via sudo."""
    uid = os.environ.get("INTERCEPT_SUDO_UID")
    gid = os.environ.get("INTERCEPT_SUDO_GID")
    if uid is None or gid is None:
        return
    try:
        uid_int, gid_int = int(uid), int(gid)
        for dirpath, _dirnames, filenames in os.walk(path):
            os.chown(dirpath, uid_int, gid_int)
            for fname in filenames:
                os.chown(os.path.join(dirpath, fname), uid_int, gid_int)
    except OSError as e:
        logger.warning(f"Could not fix ownership of {path}: {e}")


def parse_radiosonde_udp(udp_port: int) -> None:
    """Thread function: listen for radiosonde_auto_rx UDP JSON telemetry."""
    global radiosonde_running, _udp_socket

    logger.info(f"Radiosonde UDP listener started on port {udp_port}")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", udp_port))
        sock.settimeout(2.0)
        _udp_socket = sock
    except OSError as e:
        logger.error(f"Failed to bind UDP port {udp_port}: {e}")
        return

    while radiosonde_running:
        try:
            data, _addr = sock.recvfrom(4096)
        except socket.timeout:
            # Clean up stale balloons
            _cleanup_stale_balloons()
            continue
        except OSError:
            break

        try:
            msg = json.loads(data.decode("utf-8", errors="ignore"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        balloon = _process_telemetry(msg)
        if balloon:
            serial = balloon.get("id", "")
            if serial:
                with _balloons_lock:
                    radiosonde_balloons[serial] = balloon
                with contextlib.suppress(queue.Full):
                    app_module.radiosonde_queue.put_nowait(
                        {
                            "type": "balloon",
                            **balloon,
                        }
                    )

    with contextlib.suppress(OSError):
        sock.close()
    _udp_socket = None
    logger.info("Radiosonde UDP listener stopped")


def _process_telemetry(msg: dict) -> dict | None:
    """Extract relevant fields from a radiosonde_auto_rx UDP telemetry packet."""
    # auto_rx broadcasts packets with a 'type' field
    # Telemetry packets have type 'payload_summary' or individual sonde data
    serial = msg.get("id") or msg.get("serial")
    if not serial:
        return None

    balloon: dict[str, Any] = {"id": str(serial)}

    # Sonde type (RS41, RS92, DFM, M10, etc.) — prefer subtype if available
    if "subtype" in msg:
        balloon["sonde_type"] = msg["subtype"]
    elif "type" in msg:
        balloon["sonde_type"] = msg["type"]

    # Timestamp
    if "datetime" in msg:
        balloon["datetime"] = msg["datetime"]

    # Position
    for key in ("lat", "latitude"):
        if key in msg:
            with contextlib.suppress(ValueError, TypeError):
                balloon["lat"] = float(msg[key])
            break
    for key in ("lon", "longitude"):
        if key in msg:
            with contextlib.suppress(ValueError, TypeError):
                balloon["lon"] = float(msg[key])
            break

    # Altitude (metres)
    if "alt" in msg:
        with contextlib.suppress(ValueError, TypeError):
            balloon["alt"] = float(msg["alt"])

    # Meteorological data
    for field in ("temp", "humidity", "pressure"):
        if field in msg:
            with contextlib.suppress(ValueError, TypeError):
                balloon[field] = float(msg[field])

    # Velocity
    if "vel_h" in msg:
        with contextlib.suppress(ValueError, TypeError):
            balloon["vel_h"] = float(msg["vel_h"])
    if "vel_v" in msg:
        with contextlib.suppress(ValueError, TypeError):
            balloon["vel_v"] = float(msg["vel_v"])
    if "heading" in msg:
        with contextlib.suppress(ValueError, TypeError):
            balloon["heading"] = float(msg["heading"])

    # GPS satellites
    if "sats" in msg:
        with contextlib.suppress(ValueError, TypeError):
            balloon["sats"] = int(msg["sats"])

    # Battery voltage
    if "batt" in msg:
        with contextlib.suppress(ValueError, TypeError):
            balloon["batt"] = float(msg["batt"])

    # Frequency
    if "freq" in msg:
        with contextlib.suppress(ValueError, TypeError):
            balloon["freq"] = float(msg["freq"])

    balloon["last_seen"] = time.time()
    return balloon


def _cleanup_stale_balloons() -> None:
    """Remove balloons not seen within the retention window."""
    now = time.time()
    with _balloons_lock:
        stale = [k for k, v in radiosonde_balloons.items() if now - v.get("last_seen", 0) > MAX_RADIOSONDE_AGE_SECONDS]
        for k in stale:
            del radiosonde_balloons[k]


@radiosonde_bp.route("/tools")
def check_tools():
    """Check for radiosonde decoding tools and hardware."""
    auto_rx_path = find_auto_rx()
    devices = SDRFactory.detect_devices()
    has_rtlsdr = any(d.sdr_type == SDRType.RTL_SDR for d in devices)

    return jsonify(
        {
            "auto_rx": auto_rx_path is not None,
            "auto_rx_path": auto_rx_path,
            "has_rtlsdr": has_rtlsdr,
            "device_count": len(devices),
        }
    )


@radiosonde_bp.route("/status")
def radiosonde_status():
    """Get radiosonde tracking status."""
    process_running = False
    if app_module.radiosonde_process:
        process_running = app_module.radiosonde_process.poll() is None

    with _balloons_lock:
        balloon_count = len(radiosonde_balloons)
        balloons_snapshot = dict(radiosonde_balloons)

    return jsonify(
        {
            "tracking_active": radiosonde_running,
            "active_device": radiosonde_active_device,
            "balloon_count": balloon_count,
            "balloons": balloons_snapshot,
            "queue_size": app_module.radiosonde_queue.qsize(),
            "auto_rx_path": find_auto_rx(),
            "process_running": process_running,
        }
    )


@radiosonde_bp.route("/start", methods=["POST"])
def start_radiosonde():
    """Start radiosonde tracking."""
    global radiosonde_running, radiosonde_active_device, radiosonde_active_sdr_type

    with app_module.radiosonde_lock:
        if radiosonde_running:
            return api_error("Radiosonde tracking already active", 409)

    data = request.json or {}
    data = apply_device_defaults(data)

    # Validate inputs
    try:
        gain = float(validate_gain(data.get("gain", "40")))
        device = validate_device_index(data.get("device", "0"))
        ppm = validate_ppm(data.get("ppm", 0))
    except ValueError as e:
        return api_error(str(e), 400)

    freq_min = data.get("freq_min", 400.0)
    freq_max = data.get("freq_max", 406.0)
    try:
        freq_min = float(freq_min)
        freq_max = float(freq_max)
        if not (380.0 <= freq_min <= 410.0) or not (380.0 <= freq_max <= 410.0):
            raise ValueError("Frequency out of range")
        if freq_min >= freq_max:
            raise ValueError("Min frequency must be less than max")
    except (ValueError, TypeError) as e:
        return api_error(f"Invalid frequency range: {e}", 400)

    bias_t = data.get("bias_t", False)

    # Validate optional location
    latitude = 0.0
    longitude = 0.0
    if data.get("latitude") is not None and data.get("longitude") is not None:
        try:
            latitude = validate_latitude(data["latitude"])
            longitude = validate_longitude(data["longitude"])
        except ValueError:
            latitude = 0.0
            longitude = 0.0

    # Check if gpsd is available for live position updates
    gpsd_enabled = is_gpsd_running()

    # Find auto_rx
    auto_rx_path = find_auto_rx()
    if not auto_rx_path:
        return api_error(
            "radiosonde_auto_rx not found. Install from https://github.com/projecthorus/radiosonde_auto_rx", 400
        )

    # Get SDR type
    sdr_type_str = data.get("sdr_type", "rtlsdr")

    # Kill any existing process
    if app_module.radiosonde_process:
        try:
            pgid = os.getpgid(app_module.radiosonde_process.pid)
            os.killpg(pgid, 15)
            app_module.radiosonde_process.wait(timeout=PROCESS_TERMINATE_TIMEOUT)
        except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
            try:
                pgid = os.getpgid(app_module.radiosonde_process.pid)
                os.killpg(pgid, 9)
            except (ProcessLookupError, OSError):
                pass
        app_module.radiosonde_process = None
        logger.info("Killed existing radiosonde process")

    # Claim SDR device
    device_int = int(device)
    error = app_module.claim_sdr_device(device_int, "radiosonde", sdr_type_str)
    if error:
        return api_error(error, 409, error_type="DEVICE_BUSY")

    # Generate config
    try:
        cfg_path = generate_station_cfg(
            freq_min=freq_min,
            freq_max=freq_max,
            gain=gain,
            device_index=device_int,
            ppm=ppm,
            bias_t=bias_t,
            latitude=latitude,
            longitude=longitude,
            gpsd_enabled=gpsd_enabled,
        )
    except (OSError, RuntimeError) as e:
        app_module.release_sdr_device(device_int, sdr_type_str)
        logger.error(f"Failed to generate radiosonde config: {e}")
        return api_error(str(e), 500)

    # Build command - auto_rx -c expects the path to station.cfg
    cfg_abs = os.path.abspath(cfg_path)
    if auto_rx_path.endswith(".py"):
        selected_python, dep_error, checked_interpreters = _resolve_auto_rx_python(auto_rx_path)
        if not selected_python:
            logger.error(
                "radiosonde_auto_rx dependency check failed across interpreters %s: %s",
                checked_interpreters,
                dep_error,
            )
            app_module.release_sdr_device(device_int, sdr_type_str)
            checked_msg = ", ".join(checked_interpreters) if checked_interpreters else "none"
            return api_error(
                "radiosonde_auto_rx dependencies not satisfied. "
                "Install or repair its Python environment (missing packages such as semver). "
                f"Checked interpreters: {checked_msg}. "
                f"Last error: {dep_error[:500]}",
                500,
            )
        cmd = [selected_python, auto_rx_path, "-c", cfg_abs]
    else:
        cmd = [auto_rx_path, "-c", cfg_abs]

    # Set cwd to the auto_rx directory so 'from autorx.scan import ...' works
    auto_rx_dir = os.path.dirname(os.path.abspath(auto_rx_path))
    auto_rx_env = _build_auto_rx_env(auto_rx_dir)

    try:
        logger.info(f"Starting radiosonde_auto_rx: {' '.join(cmd)}")
        app_module.radiosonde_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
            cwd=auto_rx_dir,
            env=auto_rx_env,
        )

        # Wait briefly for process to start
        time.sleep(2.0)

        if app_module.radiosonde_process.poll() is not None:
            app_module.release_sdr_device(device_int, sdr_type_str)
            stderr_output = ""
            if app_module.radiosonde_process.stderr:
                with contextlib.suppress(Exception):
                    stderr_output = app_module.radiosonde_process.stderr.read().decode("utf-8", errors="ignore").strip()
            if stderr_output:
                logger.error(f"radiosonde_auto_rx stderr:\n{stderr_output}")
            if stderr_output and ("ImportError" in stderr_output or "ModuleNotFoundError" in stderr_output):
                error_msg = (
                    "radiosonde_auto_rx failed to start due to missing Python "
                    "dependencies. Re-run setup.sh or reinstall radiosonde_auto_rx."
                )
            else:
                error_msg = "radiosonde_auto_rx failed to start. Check SDR device connection."
            if stderr_output:
                error_msg += f" Error: {stderr_output[:500]}"
            return api_error(error_msg, 500)

        radiosonde_running = True
        radiosonde_active_device = device_int
        radiosonde_active_sdr_type = sdr_type_str

        # Clear stale data
        with _balloons_lock:
            radiosonde_balloons.clear()

        # Start UDP listener thread
        udp_thread = threading.Thread(
            target=parse_radiosonde_udp,
            args=(RADIOSONDE_UDP_PORT,),
            daemon=True,
        )
        udp_thread.start()

        return jsonify(
            {
                "status": "started",
                "message": "Radiosonde tracking started",
                "device": device,
            }
        )
    except Exception as e:
        app_module.release_sdr_device(device_int, sdr_type_str)
        logger.error(f"Failed to start radiosonde_auto_rx: {e}")
        return api_error(str(e), 500)


@radiosonde_bp.route("/stop", methods=["POST"])
def stop_radiosonde():
    """Stop radiosonde tracking."""
    global radiosonde_running, radiosonde_active_device, radiosonde_active_sdr_type, _udp_socket

    with app_module.radiosonde_lock:
        if app_module.radiosonde_process:
            try:
                pgid = os.getpgid(app_module.radiosonde_process.pid)
                os.killpg(pgid, 15)
                app_module.radiosonde_process.wait(timeout=RADIOSONDE_TERMINATE_TIMEOUT)
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                try:
                    pgid = os.getpgid(app_module.radiosonde_process.pid)
                    os.killpg(pgid, 9)
                except (ProcessLookupError, OSError):
                    pass
            app_module.radiosonde_process = None
            logger.info("Radiosonde process stopped")

        # Close UDP socket to unblock listener thread
        if _udp_socket:
            with contextlib.suppress(OSError):
                _udp_socket.close()
            _udp_socket = None

        # Release SDR device
        if radiosonde_active_device is not None:
            app_module.release_sdr_device(
                radiosonde_active_device,
                radiosonde_active_sdr_type or "rtlsdr",
            )

        radiosonde_running = False
        radiosonde_active_device = None
        radiosonde_active_sdr_type = None

    with _balloons_lock:
        radiosonde_balloons.clear()

    return jsonify({"status": "stopped"})


@radiosonde_bp.route("/stream")
def stream_radiosonde():
    """SSE stream for radiosonde telemetry."""
    response = Response(
        sse_stream_fanout(
            source_queue=app_module.radiosonde_queue,
            channel_key="radiosonde",
            timeout=SSE_QUEUE_TIMEOUT,
            keepalive_interval=SSE_KEEPALIVE_INTERVAL,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@radiosonde_bp.route("/balloons")
def get_balloons():
    """Get current balloon data."""
    with _balloons_lock:
        return api_success(
            data={
                "count": len(radiosonde_balloons),
                "balloons": dict(radiosonde_balloons),
            }
        )
