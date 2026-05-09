"""
INTERCEPT - Signal Intelligence Platform

Flask application and shared state.
"""

from __future__ import annotations

import site
import sys

from utils.database import get_db

# Ensure user site-packages is available (may be disabled when running as root/sudo)
if not site.ENABLE_USER_SITE:
    user_site = site.getusersitepackages()
    if user_site and user_site not in sys.path:
        sys.path.insert(0, user_site)

import logging
import os
import platform
import queue
import subprocess
import threading
from pathlib import Path

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from config import CHANGELOG, DEFAULT_LATITUDE, DEFAULT_LONGITUDE, SHARED_OBSERVER_LOCATION_ENABLED, VERSION
from utils.cleanup import DataStore, cleanup_manager
from utils.constants import (
    MAX_AIRCRAFT_AGE_SECONDS,
    MAX_BT_DEVICE_AGE_SECONDS,
    MAX_DEAUTH_ALERTS_AGE_SECONDS,
    MAX_DSC_MESSAGE_AGE_SECONDS,
    MAX_VESSEL_AGE_SECONDS,
    MAX_WIFI_NETWORK_AGE_SECONDS,
    QUEUE_MAX_SIZE,
)
from utils.dependencies import check_all_dependencies, check_tool
from utils.process import cleanup_stale_dump1090, cleanup_stale_processes
from utils.sdr import SDRFactory

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    _has_limiter = True
except ImportError:
    _has_limiter = False
try:
    from flask_compress import Compress

    _has_compress = True
except ImportError:
    _has_compress = False
try:
    from flask_wtf.csrf import CSRFProtect

    _has_csrf = True
except ImportError:
    _has_csrf = False
# Track application start time for uptime calculation
import contextlib
import time as _time

_app_start_time = _time.time()
logger = logging.getLogger("intercept.database")

# Create Flask app
app = Flask(__name__)


def _load_or_generate_secret_key():
    """Load secret key from env var or instance file, generating if needed."""
    env_key = os.environ.get("INTERCEPT_SECRET_KEY")
    if env_key:
        return env_key
    key_path = Path("instance/secret.key")
    if key_path.exists():
        return key_path.read_text().strip()
    key_path.parent.mkdir(exist_ok=True)
    key = os.urandom(32).hex()
    key_path.write_text(key)
    return key


app.secret_key = _load_or_generate_secret_key()

# Set up HTTP compression (gzip/brotli for HTML, CSS, JS, JSON)
if _has_compress:
    Compress(app)
else:
    logging.getLogger("intercept").warning(
        "flask-compress not installed – HTTP compression disabled. Install with: pip install flask-compress"
    )

# Set up rate limiting
if _has_limiter:
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        storage_uri="memory://",
    )
else:
    logging.getLogger("intercept").warning(
        "flask-limiter not installed – rate limiting disabled. Install with: pip install flask-limiter"
    )

    class _NoopLimiter:
        """Stub so @limiter.limit() decorators are silently ignored."""

        def limit(self, *a, **kw):
            def decorator(f):
                return f

            return decorator

    limiter = _NoopLimiter()

# Set up CSRF protection
if _has_csrf:
    csrf = CSRFProtect(app)
else:
    logging.getLogger("intercept").warning(
        "flask-wtf not installed – CSRF protection disabled. Install with: pip install flask-wtf"
    )
    csrf = None

# Disable Werkzeug debugger PIN (not needed for local development tool)
os.environ["WERKZEUG_DEBUG_PIN"] = "off"


# ============================================
# ERROR HANDLERS
# ============================================
@app.errorhandler(429)
def ratelimit_handler(e):
    logger.warning(f"Rate limit exceeded for IP: {request.remote_addr}")
    flash("Too many login attempts. Please wait one minute before trying again.", "error")
    return render_template("login.html", version=VERSION), 429


# ============================================
# SECURITY HEADERS
# ============================================


@app.after_request
def add_security_headers(response):
    """Add security headers to all responses."""
    # Prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Prevent clickjacking
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    # Enable XSS filter
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # Referrer policy
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Permissions policy (disable unnecessary features)
    response.headers["Permissions-Policy"] = "geolocation=(self), microphone=()"
    # Cache-Control for static assets
    if request.path.startswith("/static/"):
        if "/vendor/" in request.path:
            response.headers["Cache-Control"] = "public, max-age=604800"  # 7 days for vendored libs
        else:
            response.headers["Cache-Control"] = "public, max-age=86400"  # 24h for app assets
    return response


# ============================================
# CONTEXT PROCESSORS
# ============================================


@app.context_processor
def inject_offline_settings():
    """Inject offline settings into all templates."""
    from utils.database import get_setting

    # Privacy-first defaults: keep dashboard assets/fonts local to avoid
    # third-party tracker/storage defenses in strict browsers.
    assets_source = str(get_setting("offline.assets_source", "local") or "local").lower()
    fonts_source = str(get_setting("offline.fonts_source", "local") or "local").lower()
    if assets_source not in ("local", "cdn"):
        assets_source = "local"
    if fonts_source not in ("local", "cdn"):
        fonts_source = "local"
    # Force local delivery for core dashboard pages.
    assets_source = "local"
    fonts_source = "local"

    return {
        "offline_settings": {
            "enabled": get_setting("offline.enabled", False),
            "assets_source": assets_source,
            "fonts_source": fonts_source,
            "tile_provider": get_setting("offline.tile_provider", "cartodb_dark_cyan"),
            "tile_server_url": get_setting("offline.tile_server_url", ""),
        }
    }


# ============================================
# GLOBAL PROCESS MANAGEMENT
# ============================================

# Pager decoder
current_process = None
output_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
process_lock = threading.Lock()

# RTL_433 sensor
sensor_process = None
sensor_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
sensor_lock = threading.Lock()

# WiFi
wifi_process = None
wifi_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
wifi_lock = threading.Lock()

# Bluetooth
bt_process = None
bt_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
bt_lock = threading.Lock()

# ADS-B aircraft
adsb_process = None
adsb_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
adsb_lock = threading.Lock()

# Satellite/Iridium
satellite_process = None
satellite_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
satellite_lock = threading.Lock()

# ACARS aircraft messaging
acars_process = None
acars_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
acars_lock = threading.Lock()

# VDL2 aircraft datalink
vdl2_process = None
vdl2_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
vdl2_lock = threading.Lock()

# APRS amateur radio tracking
aprs_process = None
aprs_rtl_process = None
aprs_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
aprs_lock = threading.Lock()

# RTLAMR utility meter reading
rtlamr_process = None
rtlamr_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
rtlamr_lock = threading.Lock()

# AIS vessel tracking
ais_process = None
ais_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
ais_lock = threading.Lock()

# DSC (Digital Selective Calling)
dsc_process = None
dsc_rtl_process = None
dsc_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
dsc_lock = threading.Lock()

# TSCM (Technical Surveillance Countermeasures)
tscm_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
tscm_lock = threading.Lock()

# Ground Station automation
ground_station_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)

# SubGHz Transceiver (HackRF)
subghz_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
subghz_lock = threading.Lock()

# Radiosonde weather balloon tracking
radiosonde_process = None
radiosonde_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
radiosonde_lock = threading.Lock()

# CW/Morse code decoder
morse_process = None
morse_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
morse_lock = threading.Lock()

# Meteor scatter detection
meteor_process = None
meteor_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
meteor_lock = threading.Lock()

# Generic OOK signal decoder
ook_process = None
ook_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
ook_lock = threading.Lock()

# Deauth Attack Detection
deauth_detector = None
deauth_detector_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
deauth_detector_lock = threading.Lock()

# Drone Intelligence
drone_queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)

# ============================================
# GLOBAL STATE DICTIONARIES
# ============================================

# Logging settings
logging_enabled = False
log_file_path = "pager_messages.log"

# WiFi state - using DataStore for automatic cleanup
wifi_monitor_interface = None
wifi_networks = DataStore(max_age_seconds=MAX_WIFI_NETWORK_AGE_SECONDS, name="wifi_networks")
wifi_clients = DataStore(max_age_seconds=MAX_WIFI_NETWORK_AGE_SECONDS, name="wifi_clients")
wifi_handshakes = []  # Captured handshakes (list, not auto-cleaned)

# Bluetooth state - using DataStore for automatic cleanup
bt_interface = None
bt_devices = DataStore(max_age_seconds=MAX_BT_DEVICE_AGE_SECONDS, name="bt_devices")
bt_beacons = DataStore(max_age_seconds=MAX_BT_DEVICE_AGE_SECONDS, name="bt_beacons")
bt_services = {}  # MAC -> list of services (not auto-cleaned, user-requested)

# Aircraft (ADS-B) state - using DataStore for automatic cleanup
adsb_aircraft = DataStore(max_age_seconds=MAX_AIRCRAFT_AGE_SECONDS, name="adsb_aircraft")

# Vessel (AIS) state - using DataStore for automatic cleanup
ais_vessels = DataStore(max_age_seconds=MAX_VESSEL_AGE_SECONDS, name="ais_vessels")

# DSC (Digital Selective Calling) state - using DataStore for automatic cleanup
dsc_messages = DataStore(max_age_seconds=MAX_DSC_MESSAGE_AGE_SECONDS, name="dsc_messages")

# Deauth alerts - using DataStore for automatic cleanup
deauth_alerts = DataStore(max_age_seconds=MAX_DEAUTH_ALERTS_AGE_SECONDS, name="deauth_alerts")

# Satellite state
satellite_passes = []  # Predicted satellite passes (not auto-cleaned, calculated)

# Register data stores with cleanup manager
cleanup_manager.register(wifi_networks)
cleanup_manager.register(wifi_clients)
cleanup_manager.register(bt_devices)
cleanup_manager.register(bt_beacons)
cleanup_manager.register(adsb_aircraft)
cleanup_manager.register(ais_vessels)
cleanup_manager.register(dsc_messages)
cleanup_manager.register(deauth_alerts)

# ============================================
# SDR DEVICE REGISTRY
# ============================================
# Tracks which mode is using which SDR device to prevent conflicts
# Key: "sdr_type:device_index" (str), Value: mode_name (str)
sdr_device_registry: dict[str, str] = {}
sdr_device_registry_lock = threading.Lock()


def claim_sdr_device(device_index: int, mode_name: str, sdr_type: str = "rtlsdr") -> str | None:
    """Claim an SDR device for a mode.

    Checks the in-app registry first, then probes the USB device to
    catch stale handles held by external processes (e.g. a leftover
    rtl_fm from a previous crash).

    Args:
        device_index: The SDR device index to claim
        mode_name: Name of the mode claiming the device (e.g., 'sensor', 'rtlamr')
        sdr_type: SDR type string (e.g., 'rtlsdr', 'hackrf', 'limesdr')

    Returns:
        Error message if device is in use, None if successfully claimed
    """
    key = f"{sdr_type}:{device_index}"
    with sdr_device_registry_lock:
        if key in sdr_device_registry:
            in_use_by = sdr_device_registry[key]
            return f"SDR device {sdr_type}:{device_index} is in use by {in_use_by}. Stop {in_use_by} first or use a different device."

        # Probe the USB device to catch external processes holding the handle
        if sdr_type == "rtlsdr":
            try:
                from utils.sdr.detection import probe_rtlsdr_device

                usb_error = probe_rtlsdr_device(device_index)
                if usb_error:
                    return usb_error
            except Exception:
                pass  # If probe fails, let the caller proceed normally

        sdr_device_registry[key] = mode_name
        return None


def release_sdr_device(device_index: int, sdr_type: str = "rtlsdr") -> None:
    """Release an SDR device from the registry.

    Args:
        device_index: The SDR device index to release
        sdr_type: SDR type string (e.g., 'rtlsdr', 'hackrf', 'limesdr')
    """
    key = f"{sdr_type}:{device_index}"
    with sdr_device_registry_lock:
        sdr_device_registry.pop(key, None)


def get_sdr_device_status() -> dict[str, str]:
    """Get current SDR device allocations.

    Returns:
        Dictionary mapping 'sdr_type:device_index' keys to mode names
    """
    with sdr_device_registry_lock:
        return dict(sdr_device_registry)


# ============================================
# MAIN ROUTES
# ============================================


@app.before_request
def require_login():
    # Skip auth entirely when INTERCEPT_DISABLE_AUTH is set
    if os.environ.get('INTERCEPT_DISABLE_AUTH', '').lower() in ('1', 'true', 'yes'):
        session['logged_in'] = True
        return None

    # Routes that don't require login (to avoid infinite redirect loop)
    allowed_routes = ["login", "static", "favicon", "health", "health_check"]

    # Allow audio streaming endpoints without session auth
    if request.path.startswith("/listening/audio/"):
        return None

    # Allow WebSocket upgrade requests (page load already required auth)
    if request.path.startswith("/ws/"):
        return None

    # Controller API endpoints use API key auth, not session auth
    # Allow agent push/pull endpoints without session login
    if request.path.startswith("/controller/"):
        return None  # Skip session check, controller routes handle their own auth

    # If user is not logged in and the current route is not allowed...
    if "logged_in" not in session and request.endpoint not in allowed_routes:
        return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")  # Limit to 5 login attempts per minute per IP
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        # Connect to DB and find user
        with get_db() as conn:
            cursor = conn.execute("SELECT password_hash, role FROM users WHERE username = ?", (username,))
            user = cursor.fetchone()

        # Verify user exists and password is correct
        if user and check_password_hash(user["password_hash"], password):
            # Store data in session
            session["logged_in"] = True
            session["username"] = username
            session["role"] = user["role"]

            logger.info(f"User '{username}' logged in successfully.")
            return redirect(url_for("index"))
        else:
            logger.warning(f"Failed login attempt for username: {username}")
            flash("ACCESS DENIED: INVALID CREDENTIALS", "error")

    return render_template("login.html", version=VERSION)


@app.route("/")
def index() -> str:
    if request.args.get("mode") == "satellite":
        return redirect(url_for("satellite.satellite_dashboard"))

    tools = {
        "rtl_fm": check_tool("rtl_fm"),
        "multimon": check_tool("multimon-ng"),
        "rtl_433": check_tool("rtl_433"),
        "rtlamr": check_tool("rtlamr"),
    }
    devices = [d.to_dict() for d in SDRFactory.detect_devices()]
    return render_template(
        "index.html",
        tools=tools,
        devices=devices,
        version=VERSION,
        changelog=CHANGELOG,
        shared_observer_location=SHARED_OBSERVER_LOCATION_ENABLED,
        default_latitude=DEFAULT_LATITUDE,
        default_longitude=DEFAULT_LONGITUDE,
    )


@app.route("/favicon.svg")
def favicon() -> Response:
    return send_file("static/favicon.svg", mimetype="image/svg+xml")


@app.route("/sw.js")
def service_worker() -> Response:
    resp = send_from_directory("static", "sw.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/manifest.json")
def pwa_manifest() -> Response:
    return send_from_directory("static", "manifest.json", mimetype="application/manifest+json")


@app.route("/devices")
def get_devices() -> Response:
    """Get all detected SDR devices with hardware type info."""
    devices = SDRFactory.detect_devices()
    return jsonify([d.to_dict() for d in devices])


@app.route("/devices/status")
def get_devices_status() -> Response:
    """Get all SDR devices with usage status."""
    devices = SDRFactory.detect_devices()
    registry = get_sdr_device_status()

    result = []
    for device in devices:
        d = device.to_dict()
        key = f"{device.sdr_type.value}:{device.index}"
        d["in_use"] = key in registry
        d["used_by"] = registry.get(key)
        result.append(d)

    return jsonify(result)


@app.route("/devices/debug")
def get_devices_debug() -> Response:
    """Get detailed SDR device detection diagnostics."""
    import shutil

    diagnostics = {
        "tools": {},
        "rtl_test": {},
        "soapy": {},
        "usb": {},
        "kernel_modules": {},
        "detected_devices": [],
        "suggestions": [],
    }

    # Check for required tools
    diagnostics["tools"]["rtl_test"] = shutil.which("rtl_test") is not None
    diagnostics["tools"]["SoapySDRUtil"] = shutil.which("SoapySDRUtil") is not None
    diagnostics["tools"]["lsusb"] = shutil.which("lsusb") is not None

    # Run rtl_test and capture full output
    if diagnostics["tools"]["rtl_test"]:
        try:
            result = subprocess.run(["rtl_test", "-t"], capture_output=True, text=True, timeout=5)
            diagnostics["rtl_test"] = {
                "returncode": result.returncode,
                "stdout": result.stdout[:2000] if result.stdout else "",
                "stderr": result.stderr[:2000] if result.stderr else "",
            }

            # Check for common errors
            combined = (result.stdout or "") + (result.stderr or "")
            if "No supported devices found" in combined:
                diagnostics["suggestions"].append("No RTL-SDR device detected. Check USB connection.")
            if "usb_claim_interface error" in combined:
                diagnostics["suggestions"].append(
                    "Device busy - kernel DVB driver may have claimed it. Run: sudo modprobe -r dvb_usb_rtl28xxu"
                )
            if "Permission denied" in combined.lower():
                diagnostics["suggestions"].append("USB permission denied. Add udev rules or run as root.")

        except subprocess.TimeoutExpired:
            diagnostics["rtl_test"] = {"error": "Timeout after 5 seconds"}
        except Exception as e:
            diagnostics["rtl_test"] = {"error": str(e)}
    else:
        diagnostics["suggestions"].append("rtl_test not found. Install rtl-sdr package.")

    # Run SoapySDRUtil
    if diagnostics["tools"]["SoapySDRUtil"]:
        try:
            result = subprocess.run(["SoapySDRUtil", "--find"], capture_output=True, text=True, timeout=10)
            diagnostics["soapy"] = {
                "returncode": result.returncode,
                "stdout": result.stdout[:2000] if result.stdout else "",
                "stderr": result.stderr[:2000] if result.stderr else "",
            }
        except subprocess.TimeoutExpired:
            diagnostics["soapy"] = {"error": "Timeout after 10 seconds"}
        except Exception as e:
            diagnostics["soapy"] = {"error": str(e)}

    # Check USB devices (Linux)
    if diagnostics["tools"]["lsusb"]:
        try:
            result = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
            # Filter for common SDR vendor IDs
            sdr_vendors = ["0bda", "1d50", "1df7", "0403"]  # Realtek, OpenMoko/HackRF, SDRplay, FTDI
            usb_lines = [
                l
                for l in result.stdout.split("\n")
                if any(v in l.lower() for v in sdr_vendors) or "rtl" in l.lower() or "sdr" in l.lower()
            ]
            diagnostics["usb"]["devices"] = usb_lines if usb_lines else ["No SDR-related USB devices found"]
        except Exception as e:
            diagnostics["usb"] = {"error": str(e)}

    # Check for loaded kernel modules that conflict (Linux)
    if platform.system() == "Linux":
        try:
            result = subprocess.run(["lsmod"], capture_output=True, text=True, timeout=5)
            conflicting = ["dvb_usb_rtl28xxu", "rtl2832", "rtl2830"]
            loaded = [m for m in conflicting if m in result.stdout]
            diagnostics["kernel_modules"]["conflicting_loaded"] = loaded
            if loaded:
                diagnostics["suggestions"].append(
                    f"Conflicting kernel modules loaded: {', '.join(loaded)}. Run: sudo modprobe -r {' '.join(loaded)}"
                )
        except Exception as e:
            diagnostics["kernel_modules"] = {"error": str(e)}

    # Get detected devices
    devices = SDRFactory.detect_devices()
    diagnostics["detected_devices"] = [d.to_dict() for d in devices]

    if not devices and not diagnostics["suggestions"]:
        diagnostics["suggestions"].append("No devices detected. Check USB connection and driver installation.")

    return jsonify(diagnostics)


@app.route("/dependencies")
def get_dependencies() -> Response:
    """Get status of all tool dependencies."""
    results = check_all_dependencies()

    # Determine OS for install instructions
    system = platform.system().lower()
    if system == "darwin":
        pkg_manager = "brew"
    elif system == "linux":
        pkg_manager = "apt"
    else:
        pkg_manager = "manual"

    return jsonify({"status": "success", "os": system, "pkg_manager": pkg_manager, "modes": results})


@app.route("/export/aircraft", methods=["GET"])
def export_aircraft() -> Response:
    """Export aircraft data as JSON or CSV."""
    import csv
    import io

    format_type = request.args.get("format", "json").lower()

    if format_type == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["icao", "callsign", "altitude", "speed", "heading", "lat", "lon", "squawk", "last_seen"])

        for icao, ac in adsb_aircraft.items():
            writer.writerow(
                [
                    icao,
                    ac.get("callsign", "") if isinstance(ac, dict) else "",
                    ac.get("altitude", "") if isinstance(ac, dict) else "",
                    ac.get("speed", "") if isinstance(ac, dict) else "",
                    ac.get("heading", "") if isinstance(ac, dict) else "",
                    ac.get("lat", "") if isinstance(ac, dict) else "",
                    ac.get("lon", "") if isinstance(ac, dict) else "",
                    ac.get("squawk", "") if isinstance(ac, dict) else "",
                    ac.get("lastSeen", "") if isinstance(ac, dict) else "",
                ]
            )

        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=aircraft.csv"
        return response
    else:
        return jsonify(
            {"timestamp": __import__("datetime").datetime.utcnow().isoformat(), "aircraft": adsb_aircraft.values()}
        )


@app.route("/export/wifi", methods=["GET"])
def export_wifi() -> Response:
    """Export WiFi networks as JSON or CSV."""
    import csv
    import io

    format_type = request.args.get("format", "json").lower()

    if format_type == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["bssid", "ssid", "channel", "signal", "encryption", "clients"])

        for bssid, net in wifi_networks.items():
            writer.writerow(
                [
                    bssid,
                    net.get("ssid", "") if isinstance(net, dict) else "",
                    net.get("channel", "") if isinstance(net, dict) else "",
                    net.get("signal", "") if isinstance(net, dict) else "",
                    net.get("encryption", "") if isinstance(net, dict) else "",
                    net.get("clients", 0) if isinstance(net, dict) else 0,
                ]
            )

        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=wifi_networks.csv"
        return response
    else:
        return jsonify(
            {
                "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
                "networks": wifi_networks.values(),
                "clients": wifi_clients.values(),
            }
        )


@app.route("/export/bluetooth", methods=["GET"])
def export_bluetooth() -> Response:
    """Export Bluetooth devices as JSON or CSV."""
    import csv
    import io

    format_type = request.args.get("format", "json").lower()

    if format_type == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["mac", "name", "rssi", "type", "manufacturer", "last_seen"])

        for mac, dev in bt_devices.items():
            writer.writerow(
                [
                    mac,
                    dev.get("name", "") if isinstance(dev, dict) else "",
                    dev.get("rssi", "") if isinstance(dev, dict) else "",
                    dev.get("type", "") if isinstance(dev, dict) else "",
                    dev.get("manufacturer", "") if isinstance(dev, dict) else "",
                    dev.get("lastSeen", "") if isinstance(dev, dict) else "",
                ]
            )

        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=bluetooth_devices.csv"
        return response
    else:
        return jsonify(
            {
                "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
                "devices": bt_devices.values(),
                "beacons": bt_beacons.values(),
            }
        )


def _get_subghz_active() -> bool:
    """Check if SubGHz manager has an active process."""
    try:
        from utils.subghz import get_subghz_manager

        return get_subghz_manager().active_mode != "idle"
    except Exception:
        return False


def _get_singleton_running(module_path: str, getter_name: str, attr: str) -> bool:
    """Safely check if a singleton-based mode is running without creating instances."""
    try:
        import importlib

        mod = importlib.import_module(module_path)
        getter = getattr(mod, getter_name)
        instance = getter()
        if instance is None:
            return False
        return bool(getattr(instance, attr, False))
    except Exception:
        return False


def _get_tscm_active() -> bool:
    """Check if a TSCM sweep is running."""
    try:
        from routes.tscm import _sweep_running

        return bool(_sweep_running)
    except Exception:
        return False


def _get_bluetooth_health() -> tuple[bool, int]:
    """Return Bluetooth active state and best-effort device count."""
    legacy_running = bt_process is not None and (bt_process.poll() is None if bt_process else False)
    scanner_running = False
    scanner_count = 0

    try:
        from utils.bluetooth.scanner import _scanner_instance as bt_scanner

        if bt_scanner is not None:
            scanner_running = bool(bt_scanner.is_scanning)
            scanner_count = int(bt_scanner.device_count)
    except Exception:
        scanner_running = False
        scanner_count = 0

    locate_running = False
    try:
        from utils.bt_locate import get_locate_session

        session = get_locate_session()
        if session and getattr(session, "active", False):
            scanner = getattr(session, "_scanner", None)
            locate_running = bool(scanner and scanner.is_scanning)
    except Exception:
        locate_running = False

    return (legacy_running or scanner_running or locate_running), max(len(bt_devices), scanner_count)


def _get_wifi_health() -> tuple[bool, int, int]:
    """Return WiFi active state and best-effort network/client counts."""
    legacy_running = wifi_process is not None and (wifi_process.poll() is None if wifi_process else False)
    scanner_running = False
    scanner_networks = 0
    scanner_clients = 0

    try:
        from utils.wifi.scanner import _scanner_instance as wifi_scanner

        if wifi_scanner is not None:
            status = wifi_scanner.get_status()
            scanner_running = bool(status.is_scanning)
            scanner_networks = int(status.networks_found or 0)
            scanner_clients = int(status.clients_found or 0)
    except Exception:
        scanner_running = False
        scanner_networks = 0
        scanner_clients = 0

    return (
        legacy_running or scanner_running,
        max(len(wifi_networks), scanner_networks),
        max(len(wifi_clients), scanner_clients),
    )


@app.route("/health")
def health_check() -> Response:
    """Health check endpoint for monitoring."""
    import platform
    import time

    bt_active, bt_device_count = _get_bluetooth_health()
    wifi_active, wifi_network_count, wifi_client_count = _get_wifi_health()

    # Database health check
    db_ok = True
    try:
        from utils.database import get_connection

        get_connection().execute("SELECT 1")
    except Exception:
        db_ok = False

    # SDR device count (cached, non-blocking)
    sdr_count = 0
    try:
        from utils.sdr.detection import get_cached_devices

        cached = get_cached_devices()
        if cached is not None:
            sdr_count = len(cached)
    except (ImportError, Exception):
        pass

    overall_status = "healthy" if db_ok else "degraded"
    status_code = 200 if db_ok else 503

    response = jsonify(
        {
            "status": overall_status,
            "version": VERSION,
            "uptime_seconds": round(time.time() - _app_start_time, 2),
            "system": {
                "python_version": platform.python_version(),
                "platform": platform.platform(),
            },
            "database": db_ok,
            "sdr_devices": sdr_count,
            "sdr_claims": get_sdr_device_status(),
            "rate_limiting": _has_limiter,
            "processes": {
                "pager": current_process is not None and (current_process.poll() is None if current_process else False),
                "sensor": sensor_process is not None and (sensor_process.poll() is None if sensor_process else False),
                "adsb": adsb_process is not None and (adsb_process.poll() is None if adsb_process else False),
                "ais": ais_process is not None and (ais_process.poll() is None if ais_process else False),
                "acars": acars_process is not None and (acars_process.poll() is None if acars_process else False),
                "vdl2": vdl2_process is not None and (vdl2_process.poll() is None if vdl2_process else False),
                "aprs": aprs_process is not None and (aprs_process.poll() is None if aprs_process else False),
                "wifi": wifi_active,
                "bluetooth": bt_active,
                "dsc": dsc_process is not None and (dsc_process.poll() is None if dsc_process else False),
                "radiosonde": radiosonde_process is not None
                and (radiosonde_process.poll() is None if radiosonde_process else False),
                "morse": morse_process is not None and (morse_process.poll() is None if morse_process else False),
                "subghz": _get_subghz_active(),
                "rtlamr": rtlamr_process is not None and (rtlamr_process.poll() is None if rtlamr_process else False),
                "meshtastic": _get_singleton_running("utils.meshtastic", "get_meshtastic_client", "is_running"),
                "sstv": _get_singleton_running("utils.sstv", "get_sstv_decoder", "is_running"),
                "weathersat": _get_singleton_running("utils.weather_sat", "get_weather_sat_decoder", "is_running"),
                "wefax": _get_singleton_running("utils.wefax", "get_wefax_decoder", "is_running"),
                "sstv_general": _get_singleton_running("utils.sstv", "get_general_sstv_decoder", "is_running"),
                "tscm": _get_tscm_active(),
                "gps": _get_singleton_running("utils.gps", "get_gps_reader", "is_running"),
                "bt_locate": _get_singleton_running("utils.bt_locate", "get_locate_session", "is_active"),
            },
            "data": {
                "aircraft_count": len(adsb_aircraft),
                "vessel_count": len(ais_vessels),
                "wifi_networks_count": wifi_network_count,
                "wifi_clients_count": wifi_client_count,
                "bt_devices_count": bt_device_count,
                "dsc_messages_count": len(dsc_messages),
            },
        }
    )
    response.status_code = status_code
    return response


@app.route("/killall", methods=["POST"])
@(csrf.exempt if csrf else lambda f: f)
def kill_all() -> Response:
    """Kill all decoder, WiFi, and Bluetooth processes."""
    global current_process, sensor_process, wifi_process, adsb_process, ais_process, acars_process
    global vdl2_process, morse_process, radiosonde_process, ook_process
    global aprs_process, aprs_rtl_process, dsc_process, dsc_rtl_process, bt_process

    # Import modules to reset their state
    from routes import adsb as adsb_module
    from routes import ais as ais_module
    from routes import radiosonde as radiosonde_module
    from utils.bluetooth import reset_bluetooth_scanner

    killed = []
    processes_to_kill = [
        "rtl_fm",
        "multimon-ng",
        "rtl_433",
        "airodump-ng",
        "aireplay-ng",
        "airmon-ng",
        "dump1090",
        "acarsdec",
        "dumpvdl2",
        "direwolf",
        "AIS-catcher",
        "hcitool",
        "bluetoothctl",
        "satdump",
        "rtl_tcp",
        "rtl_power",
        "rtlamr",
        "ffmpeg",
        "hackrf_transfer",
        "hackrf_sweep",
        "auto_rx",
    ]

    for proc in processes_to_kill:
        try:
            result = subprocess.run(["pkill", "-f", proc], capture_output=True)
            if result.returncode == 0:
                killed.append(proc)
        except (subprocess.SubprocessError, OSError):
            pass

    with process_lock:
        current_process = None

    with sensor_lock:
        sensor_process = None

    with wifi_lock:
        wifi_process = None

    # Reset ADS-B state
    with adsb_lock:
        adsb_process = None
        adsb_module.adsb_using_service = False

    # Reset AIS state
    with ais_lock:
        ais_process = None
        ais_module.ais_running = False

    # Reset Radiosonde state
    with radiosonde_lock:
        radiosonde_process = None
        radiosonde_module.radiosonde_running = False

    # Reset ACARS state
    with acars_lock:
        acars_process = None

    # Reset VDL2 state
    with vdl2_lock:
        vdl2_process = None

    # Reset Morse state
    with morse_lock:
        morse_process = None

    # Reset OOK state (full cleanup: parser thread, pipes, SDR release)
    with ook_lock:
        try:
            from routes.ook import cleanup_ook

            cleanup_ook(emit_status=False)
        except Exception:
            if ook_process:
                safe_terminate(ook_process)
                unregister_process(ook_process)
            ook_process = None

    # Reset APRS state
    with aprs_lock:
        aprs_process = None
        aprs_rtl_process = None

    # Reset DSC state
    with dsc_lock:
        dsc_process = None
        dsc_rtl_process = None

    # Reset Bluetooth state (legacy)
    with bt_lock:
        if bt_process:
            try:
                bt_process.terminate()
                bt_process.wait(timeout=2)
            except Exception:
                with contextlib.suppress(Exception):
                    bt_process.kill()
        bt_process = None

    # Reset Bluetooth v2 scanner
    try:
        reset_bluetooth_scanner()
        killed.append("bluetooth")
    except Exception:
        pass

    # Reset SubGHz state
    try:
        from utils.subghz import get_subghz_manager

        get_subghz_manager().stop_all()
    except Exception:
        pass

    # Clear SDR device registry
    with sdr_device_registry_lock:
        sdr_device_registry.clear()

    return jsonify({"status": "killed", "processes": killed})


def _ensure_self_signed_cert(cert_dir: str) -> tuple:
    """Generate a self-signed certificate if one doesn't already exist.

    Returns (cert_path, key_path) tuple.
    """
    cert_path = os.path.join(cert_dir, "intercept.crt")
    key_path = os.path.join(cert_dir, "intercept.key")

    if os.path.exists(cert_path) and os.path.exists(key_path):
        print(f"Using existing SSL certificate: {cert_path}")
        return cert_path, key_path

    os.makedirs(cert_dir, exist_ok=True)
    print("Generating self-signed SSL certificate...")

    import subprocess

    result = subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            key_path,
            "-out",
            cert_path,
            "-days",
            "365",
            "-nodes",
            "-subj",
            "/CN=intercept/O=INTERCEPT/C=US",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to generate SSL certificate: {result.stderr}")

    print(f"SSL certificate generated: {cert_path}")
    return cert_path, key_path


_app_initialized = False


def _init_app() -> None:
    """Initialize blueprints, database, and websockets.

    Safe to call multiple times — subsequent calls are no-ops.
    Called automatically at module level for gunicorn, and also
    from main() for the Flask dev server path.

    Heavy/network operations (TLE updates, process cleanup) are
    deferred to a background thread so the worker can serve
    requests immediately.
    """
    global _app_initialized
    if _app_initialized:
        return
    _app_initialized = True

    import os

    # Initialize database for settings storage
    from utils.database import init_db

    init_db()

    # Register blueprints (essential — without these, all routes 404)
    from routes import register_blueprints

    register_blueprints(app)

    # Initialize WebSocket for audio streaming
    try:
        from routes.audio_websocket import init_audio_websocket

        init_audio_websocket(app)
    except Exception:
        pass

    # Initialize KiwiSDR WebSocket audio proxy
    try:
        from routes.websdr import init_websdr_audio

        init_websdr_audio(app)
    except Exception:
        pass

    # Initialize WebSocket for waterfall streaming
    try:
        from routes.waterfall_websocket import init_waterfall_websocket

        init_waterfall_websocket(app)
    except Exception:
        pass

    # Initialize WebSocket for meteor scatter monitoring
    try:
        from routes.meteor_websocket import init_meteor_websocket

        init_meteor_websocket(app)
    except Exception:
        pass

    # Initialize WebSocket for ground station live waterfall
    try:
        from routes.ground_station import init_ground_station_websocket

        init_ground_station_websocket(app)
    except Exception:
        pass

    # Defer heavy/network operations so the worker can serve requests immediately
    import threading

    def _deferred_init():
        """Run heavy initialization after a short delay."""
        import time

        time.sleep(1)  # Let the worker start serving first

        # Clean up stale processes from previous runs
        try:
            cleanup_stale_processes()
            cleanup_stale_dump1090()
        except Exception as e:
            logger.warning(f"Stale process cleanup failed: {e}")

        # Register and start database cleanup
        try:
            from utils.database import (
                cleanup_old_dsc_alerts,
                cleanup_old_payloads,
                cleanup_old_signal_history,
                cleanup_old_timeline_entries,
            )

            cleanup_manager.register_db_cleanup(cleanup_old_signal_history, interval_multiplier=1440)
            cleanup_manager.register_db_cleanup(cleanup_old_timeline_entries, interval_multiplier=1440)
            cleanup_manager.register_db_cleanup(cleanup_old_dsc_alerts, interval_multiplier=1440)
            cleanup_manager.register_db_cleanup(cleanup_old_payloads, interval_multiplier=1440)
            cleanup_manager.start()
        except Exception as e:
            logger.warning(f"Cleanup manager init failed: {e}")

        # Initialize TLE auto-refresh (must be after blueprint registration)
        try:
            from routes.satellite import init_tle_auto_refresh

            if not os.environ.get("TESTING"):
                init_tle_auto_refresh()
        except Exception as e:
            logger.warning(f"Failed to initialize TLE auto-refresh: {e}")

        # Pre-warm SatNOGS transmitter cache so first dashboard load is instant
        try:
            if not os.environ.get("TESTING"):
                from utils.satnogs import prefetch_transmitters

                prefetch_transmitters()
        except Exception as e:
            logger.warning(f"SatNOGS prefetch failed: {e}")

        # Wire ground station scheduler event → SSE queue
        try:
            import app as _self
            from utils.ground_station.scheduler import get_ground_station_scheduler

            gs_scheduler = get_ground_station_scheduler()

            def _gs_event_to_sse(event: dict) -> None:
                try:
                    _self.ground_station_queue.put_nowait(event)
                except Exception:
                    pass

            gs_scheduler.set_event_callback(_gs_event_to_sse)
        except Exception as e:
            logger.warning(f"Ground station scheduler init failed: {e}")

    threading.Thread(target=_deferred_init, daemon=True).start()


# Auto-initialize when imported (e.g. by gunicorn)
_init_app()


def main() -> None:
    """Main entry point."""
    import argparse

    import config

    parser = argparse.ArgumentParser(
        description="INTERCEPT - Signal Intelligence Platform",
        epilog="Environment variables: INTERCEPT_HOST, INTERCEPT_PORT, INTERCEPT_DEBUG, INTERCEPT_LOG_LEVEL",
    )
    parser.add_argument(
        "-p", "--port", type=int, default=config.PORT, help=f"Port to run server on (default: {config.PORT})"
    )
    parser.add_argument("-H", "--host", default=config.HOST, help=f"Host to bind to (default: {config.HOST})")
    parser.add_argument("-d", "--debug", action="store_true", default=config.DEBUG, help="Enable debug mode")
    parser.add_argument(
        "--https", action="store_true", default=config.HTTPS, help="Enable HTTPS with self-signed certificate"
    )
    parser.add_argument("--check-deps", action="store_true", help="Check dependencies and exit")
    args = parser.parse_args()

    # Check dependencies only
    if args.check_deps:
        results = check_all_dependencies()
        print("Dependency Status:")
        print("-" * 40)
        for _mode, info in results.items():
            status = "✓" if info["ready"] else "✗"
            print(f"\n{status} {info['name']}:")
            for tool, tool_info in info["tools"].items():
                tool_status = "✓" if tool_info["installed"] else "✗"
                req = " (required)" if tool_info["required"] else ""
                print(f"    {tool_status} {tool}{req}")
        sys.exit(0)

    print("=" * 50)
    print("  INTERCEPT // Signal Intelligence")
    print("  Pager / 433MHz / Aircraft / ACARS / Satellite / WiFi / BT")
    print("=" * 50)
    print()

    # Check if running as root (required for WiFi monitor mode, some BT operations)
    import os

    if os.geteuid() != 0:
        print("\033[93m" + "=" * 50)
        print("  ⚠️  WARNING: Not running as root/sudo")
        print("=" * 50)
        print("  Some features require root privileges:")
        print("    - WiFi monitor mode and scanning")
        print("    - Bluetooth low-level operations")
        print("    - RTL-SDR access (on some systems)")
        print()
        print("  To run with full capabilities:")
        print("    sudo -E venv/bin/python intercept.py")
        print("=" * 50 + "\033[0m")
        print()
        # Store for API access
        app.config["RUNNING_AS_ROOT"] = False
    else:
        app.config["RUNNING_AS_ROOT"] = True
        print("Running as root - full capabilities enabled")
        print()

    # Ensure app is initialized (no-op if already done by module-level call)
    _init_app()

    # Configure SSL if HTTPS is enabled
    ssl_context = None
    if args.https:
        cert_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "certs")
        if config.SSL_CERT and config.SSL_KEY:
            ssl_context = (config.SSL_CERT, config.SSL_KEY)
            print(f"Using provided SSL certificate: {config.SSL_CERT}")
        else:
            ssl_context = _ensure_self_signed_cert(cert_dir)

    protocol = "https" if ssl_context else "http"
    print(f"Open {protocol}://localhost:{args.port} in your browser")
    print()
    print("Press Ctrl+C to stop")
    print()

    # Avoid loading a global ~/.env when running the script directly.
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True,
        load_dotenv=False,
        ssl_context=ssl_context,
    )
