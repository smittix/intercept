"""System Health monitoring blueprint.

Provides real-time system metrics (CPU, memory, disk, temperatures,
network, battery, fans), active process status, SDR device enumeration,
location, and weather data via SSE streaming and REST endpoints.
"""

from __future__ import annotations

import contextlib
import os
import platform
import queue
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, request

from utils.constants import SSE_KEEPALIVE_INTERVAL, SSE_QUEUE_TIMEOUT
from utils.logging import sensor_logger as logger
from utils.responses import api_error
from utils.sse import sse_stream_fanout

try:
    import psutil

    _HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _HAS_PSUTIL = False

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore[assignment]

system_bp = Blueprint("system", __name__, url_prefix="/system")

# ---------------------------------------------------------------------------
# Background metrics collector
# ---------------------------------------------------------------------------

_metrics_queue: queue.Queue = queue.Queue(maxsize=500)
_collector_started = False
_collector_lock = threading.Lock()
_app_start_time: float | None = None

# Weather cache
_weather_cache: dict[str, Any] = {}
_weather_cache_time: float = 0.0
_WEATHER_CACHE_TTL = 600  # 10 minutes


def _get_app_start_time() -> float:
    """Return the application start timestamp from the main app module."""
    global _app_start_time
    if _app_start_time is None:
        try:
            import app as app_module

            _app_start_time = getattr(app_module, "_app_start_time", time.time())
        except Exception:
            _app_start_time = time.time()
    return _app_start_time


def _get_app_version() -> str:
    """Return the application version string."""
    try:
        from config import VERSION

        return VERSION
    except Exception:
        return "unknown"


def _format_uptime(seconds: float) -> str:
    """Format seconds into a human-readable uptime string."""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _collect_process_status() -> dict[str, bool]:
    """Return running/stopped status for each decoder process.

    Mirrors the logic in app.py health_check().
    """
    try:
        import app as app_module

        def _alive(attr: str) -> bool:
            proc = getattr(app_module, attr, None)
            if proc is None:
                return False
            try:
                return proc.poll() is None
            except Exception:
                return False

        processes: dict[str, bool] = {
            "pager": _alive("current_process"),
            "sensor": _alive("sensor_process"),
            "adsb": _alive("adsb_process"),
            "ais": _alive("ais_process"),
            "acars": _alive("acars_process"),
            "vdl2": _alive("vdl2_process"),
            "aprs": _alive("aprs_process"),
            "dsc": _alive("dsc_process"),
            "morse": _alive("morse_process"),
        }

        # WiFi
        try:
            from app import _get_wifi_health

            wifi_active, _, _ = _get_wifi_health()
            processes["wifi"] = wifi_active
        except Exception:
            processes["wifi"] = False

        # Bluetooth
        try:
            from app import _get_bluetooth_health

            bt_active, _ = _get_bluetooth_health()
            processes["bluetooth"] = bt_active
        except Exception:
            processes["bluetooth"] = False

        # SubGHz
        try:
            from app import _get_subghz_active

            processes["subghz"] = _get_subghz_active()
        except Exception:
            processes["subghz"] = False

        return processes
    except Exception:
        return {}


def _collect_throttle_flags() -> str | None:
    """Read Raspberry Pi throttle flags via vcgencmd (Linux/Pi only)."""
    if shutil.which("vcgencmd") is None:
        return None
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and "throttled=" in result.stdout:
            return result.stdout.strip().split("=", 1)[1]
    except Exception:
        pass
    return None


def _collect_power_draw() -> float | None:
    """Read power draw in watts from sysfs (Linux only)."""
    try:
        power_supply = Path("/sys/class/power_supply")
        if not power_supply.exists():
            return None
        for supply_dir in power_supply.iterdir():
            power_file = supply_dir / "power_now"
            if power_file.exists():
                val = int(power_file.read_text().strip())
                return round(val / 1_000_000, 2)  # microwatts to watts
    except Exception:
        pass
    return None


def _collect_metrics() -> dict[str, Any]:
    """Gather a snapshot of system metrics."""
    now = time.time()
    start = _get_app_start_time()
    uptime_seconds = round(now - start, 2)

    metrics: dict[str, Any] = {
        "type": "system_metrics",
        "timestamp": now,
        "system": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "version": _get_app_version(),
            "uptime_seconds": uptime_seconds,
            "uptime_human": _format_uptime(uptime_seconds),
        },
        "processes": _collect_process_status(),
    }

    if _HAS_PSUTIL:
        # CPU — overall + per-core + frequency
        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_count = psutil.cpu_count() or 1
        try:
            load_1, load_5, load_15 = os.getloadavg()
        except (OSError, AttributeError):
            load_1 = load_5 = load_15 = 0.0

        per_core = []
        with contextlib.suppress(Exception):
            per_core = psutil.cpu_percent(interval=None, percpu=True)

        freq_data = None
        with contextlib.suppress(Exception):
            freq = psutil.cpu_freq()
            if freq:
                freq_data = {
                    "current": round(freq.current, 0),
                    "min": round(freq.min, 0),
                    "max": round(freq.max, 0),
                }

        metrics["cpu"] = {
            "percent": cpu_percent,
            "count": cpu_count,
            "load_1": round(load_1, 2),
            "load_5": round(load_5, 2),
            "load_15": round(load_15, 2),
            "per_core": per_core,
            "freq": freq_data,
        }

        # Memory
        mem = psutil.virtual_memory()
        metrics["memory"] = {
            "total": mem.total,
            "used": mem.used,
            "available": mem.available,
            "percent": mem.percent,
        }

        swap = psutil.swap_memory()
        metrics["swap"] = {
            "total": swap.total,
            "used": swap.used,
            "percent": swap.percent,
        }

        # Disk — usage + I/O counters
        try:
            disk = psutil.disk_usage("/")
            metrics["disk"] = {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent,
                "path": "/",
            }
        except Exception:
            metrics["disk"] = None

        disk_io = None
        with contextlib.suppress(Exception):
            dio = psutil.disk_io_counters()
            if dio:
                disk_io = {
                    "read_bytes": dio.read_bytes,
                    "write_bytes": dio.write_bytes,
                    "read_count": dio.read_count,
                    "write_count": dio.write_count,
                }
        metrics["disk_io"] = disk_io

        # Temperatures
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                temp_data: dict[str, list[dict[str, Any]]] = {}
                for chip, entries in temps.items():
                    temp_data[chip] = [
                        {
                            "label": e.label or chip,
                            "current": e.current,
                            "high": e.high,
                            "critical": e.critical,
                        }
                        for e in entries
                    ]
                metrics["temperatures"] = temp_data
            else:
                metrics["temperatures"] = None
        except (AttributeError, Exception):
            metrics["temperatures"] = None

        # Fans
        fans_data = None
        with contextlib.suppress(Exception):
            fans = psutil.sensors_fans()
            if fans:
                fans_data = {}
                for chip, entries in fans.items():
                    fans_data[chip] = [{"label": e.label or chip, "current": e.current} for e in entries]
        metrics["fans"] = fans_data

        # Battery
        battery_data = None
        with contextlib.suppress(Exception):
            bat = psutil.sensors_battery()
            if bat:
                battery_data = {
                    "percent": bat.percent,
                    "plugged": bat.power_plugged,
                    "secs_left": bat.secsleft if bat.secsleft != psutil.POWER_TIME_UNLIMITED else None,
                }
        metrics["battery"] = battery_data

        # Network interfaces
        net_ifaces: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            for iface_name in sorted(addrs.keys()):
                if iface_name == "lo":
                    continue
                iface_info: dict[str, Any] = {"name": iface_name}
                # Get addresses
                for addr in addrs[iface_name]:
                    if addr.family == socket.AF_INET:
                        iface_info["ipv4"] = addr.address
                    elif addr.family == socket.AF_INET6:
                        iface_info.setdefault("ipv6", addr.address)
                    elif addr.family == psutil.AF_LINK:
                        iface_info["mac"] = addr.address
                # Get stats
                if iface_name in stats:
                    st = stats[iface_name]
                    iface_info["is_up"] = st.isup
                    iface_info["speed"] = st.speed  # Mbps
                    iface_info["mtu"] = st.mtu
                net_ifaces.append(iface_info)
        metrics["network"] = {"interfaces": net_ifaces}

        # Network I/O counters (raw — JS computes deltas)
        net_io = None
        with contextlib.suppress(Exception):
            counters = psutil.net_io_counters(pernic=True)
            if counters:
                net_io = {}
                for nic, c in counters.items():
                    if nic == "lo":
                        continue
                    net_io[nic] = {
                        "bytes_sent": c.bytes_sent,
                        "bytes_recv": c.bytes_recv,
                    }
        metrics["network"]["io"] = net_io

        # Connection count
        conn_count = 0
        with contextlib.suppress(Exception):
            conn_count = len(psutil.net_connections())
        metrics["network"]["connections"] = conn_count

        # Boot time
        boot_ts = None
        with contextlib.suppress(Exception):
            boot_ts = psutil.boot_time()
        metrics["boot_time"] = boot_ts

        # Power / throttle (Pi-specific)
        metrics["power"] = {
            "throttled": _collect_throttle_flags(),
            "draw_watts": _collect_power_draw(),
        }
    else:
        metrics["cpu"] = None
        metrics["memory"] = None
        metrics["swap"] = None
        metrics["disk"] = None
        metrics["disk_io"] = None
        metrics["temperatures"] = None
        metrics["fans"] = None
        metrics["battery"] = None
        metrics["network"] = None
        metrics["boot_time"] = None
        metrics["power"] = None

    return metrics


def _collector_loop() -> None:
    """Background thread that pushes metrics onto the queue every 3 seconds."""
    # Seed psutil's CPU measurement so the first real read isn't 0%.
    if _HAS_PSUTIL:
        with contextlib.suppress(Exception):
            psutil.cpu_percent(interval=None)

    while True:
        try:
            metrics = _collect_metrics()
            # Non-blocking put — drop oldest if full
            try:
                _metrics_queue.put_nowait(metrics)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    _metrics_queue.get_nowait()
                _metrics_queue.put_nowait(metrics)
        except Exception as exc:
            logger.debug("system metrics collection error: %s", exc)
        time.sleep(3)


def _ensure_collector() -> None:
    """Start the background collector thread once."""
    global _collector_started
    if _collector_started:
        return
    with _collector_lock:
        if _collector_started:
            return
        t = threading.Thread(target=_collector_loop, daemon=True, name="system-metrics-collector")
        t.start()
        _collector_started = True
        logger.info("System metrics collector started")


def _get_observer_location() -> dict[str, Any]:
    """Get observer location from GPS state or config defaults."""
    lat, lon, source = None, None, "none"
    gps_meta: dict[str, Any] = {}

    # Try GPS via utils.gps
    with contextlib.suppress(Exception):
        from utils.gps import get_current_position

        pos = get_current_position()
        if pos and pos.fix_quality >= 2:
            lat, lon, source = pos.latitude, pos.longitude, "gps"
            gps_meta["fix_quality"] = pos.fix_quality
            gps_meta["satellites"] = pos.satellites
            if pos.epx is not None and pos.epy is not None:
                gps_meta["accuracy"] = round(max(pos.epx, pos.epy), 1)
            if pos.altitude is not None:
                gps_meta["altitude"] = round(pos.altitude, 1)

    # Fall back to config env vars
    if lat is None:
        with contextlib.suppress(Exception):
            from config import DEFAULT_LATITUDE, DEFAULT_LONGITUDE

            if DEFAULT_LATITUDE != 0.0 or DEFAULT_LONGITUDE != 0.0:
                lat, lon, source = DEFAULT_LATITUDE, DEFAULT_LONGITUDE, "config"

    # Fall back to hardcoded constants (London)
    if lat is None:
        with contextlib.suppress(Exception):
            from utils.constants import DEFAULT_LATITUDE as CONST_LAT
            from utils.constants import DEFAULT_LONGITUDE as CONST_LON

            lat, lon, source = CONST_LAT, CONST_LON, "default"

    result: dict[str, Any] = {"lat": lat, "lon": lon, "source": source}
    if gps_meta:
        result["gps"] = gps_meta
    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@system_bp.route("/metrics")
def get_metrics() -> Response:
    """REST snapshot of current system metrics."""
    _ensure_collector()
    return jsonify(_collect_metrics())


@system_bp.route("/stream")
def stream_system() -> Response:
    """SSE stream for real-time system metrics."""
    _ensure_collector()

    response = Response(
        sse_stream_fanout(
            source_queue=_metrics_queue,
            channel_key="system",
            timeout=SSE_QUEUE_TIMEOUT,
            keepalive_interval=SSE_KEEPALIVE_INTERVAL,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@system_bp.route("/sdr_devices")
def get_sdr_devices() -> Response:
    """Enumerate all connected SDR devices (on-demand, not every tick)."""
    try:
        from utils.sdr.detection import detect_all_devices
        from utils.sdr.device_config import display_names

        devices = detect_all_devices()
        result = []
        for d, name in zip(devices, display_names(devices)):
            result.append(
                {
                    "type": d.sdr_type.value if hasattr(d.sdr_type, "value") else str(d.sdr_type),
                    "index": d.index,
                    "name": name,
                    "serial": d.serial or "",
                    "driver": d.driver or "",
                }
            )
        return jsonify({"devices": result})
    except Exception as exc:
        logger.warning("SDR device detection failed: %s", exc)
        return jsonify({"devices": [], "error": str(exc)})


@system_bp.route("/location")
def get_location() -> Response:
    """Return observer location from GPS or config."""
    return jsonify(_get_observer_location())


@system_bp.route("/weather")
def get_weather() -> Response:
    """Proxy weather from wttr.in, cached for 10 minutes."""
    global _weather_cache, _weather_cache_time

    now = time.time()
    if _weather_cache and (now - _weather_cache_time) < _WEATHER_CACHE_TTL:
        return jsonify(_weather_cache)

    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    if lat is None or lon is None:
        loc = _get_observer_location()
        lat, lon = loc.get("lat"), loc.get("lon")

    if lat is None or lon is None:
        return api_error("No location available")

    if _requests is None:
        return api_error("requests library not available")

    try:
        resp = _requests.get(
            f"https://wttr.in/{lat},{lon}?format=j1",
            timeout=5,
            headers={"User-Agent": "INTERCEPT-SystemHealth/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()

        current = data.get("current_condition", [{}])[0]
        weather = {
            "temp_c": current.get("temp_C"),
            "temp_f": current.get("temp_F"),
            "condition": current.get("weatherDesc", [{}])[0].get("value", ""),
            "humidity": current.get("humidity"),
            "wind_mph": current.get("windspeedMiles"),
            "wind_dir": current.get("winddir16Point"),
            "feels_like_c": current.get("FeelsLikeC"),
            "visibility": current.get("visibility"),
            "pressure": current.get("pressure"),
        }
        _weather_cache = weather
        _weather_cache_time = now
        return jsonify(weather)
    except Exception as exc:
        logger.debug("Weather fetch failed: %s", exc)
        return api_error(str(exc))
