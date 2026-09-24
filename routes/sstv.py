"""ISS SSTV (Slow-Scan Television) decoder routes.

Provides endpoints for decoding SSTV images from the International Space Station.
ISS SSTV events occur during special commemorations and typically transmit on 145.800 MHz FM.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, request, send_file

import app as app_module
from routes.satellite import get_cached_tle
from utils.event_pipeline import process_event
from utils.logging import get_logger
from utils.responses import api_error, start_failure
from utils.sse import sse_stream_fanout
from utils.sstv import (
    ISS_SSTV_FREQ,
    get_sstv_decoder,
    is_sstv_available,
)

logger = get_logger("intercept.sstv")

sstv_bp = Blueprint("sstv", __name__, url_prefix="/sstv")

# ISS SSTV runs on a fixed downlink; allow a small entry tolerance so users
# can type nearby values and still land on the canonical center frequency.
ISS_SSTV_MODULATION = "fm"
ISS_SSTV_FREQUENCIES = (ISS_SSTV_FREQ,)
ISS_SSTV_FREQ_TOLERANCE_MHZ = 0.05

# Queue for SSE progress streaming
_sstv_queue: queue.Queue = queue.Queue(maxsize=100)

# ---------------------------------------------------------------------------
# Caching — ISS position (external API) and schedule (skyfield computation)
# ---------------------------------------------------------------------------
_iss_position_cache: dict | None = None
_iss_position_cache_time: float = 0
_iss_position_lock = threading.Lock()
ISS_POSITION_CACHE_TTL = 10  # seconds

_iss_schedule_cache: dict | None = None
_iss_schedule_cache_time: float = 0
_iss_schedule_cache_key: str | None = None
_iss_schedule_lock = threading.Lock()
ISS_SCHEDULE_CACHE_TTL = 900  # 15 minutes

# Reusable skyfield timescale (expensive to create)
_timescale = None
_timescale_lock = threading.Lock()

# Track which device is being used
sstv_active_device: int | None = None
sstv_active_sdr_type: str = "rtlsdr"


def _progress_callback(data: dict) -> None:
    """Callback to queue progress/scope updates for SSE stream."""
    try:
        _sstv_queue.put_nowait(data)
    except queue.Full:
        try:
            _sstv_queue.get_nowait()
            _sstv_queue.put_nowait(data)
        except queue.Empty:
            pass


def _normalize_iss_frequency(frequency_mhz: float) -> float | None:
    """Snap near-match user input to a supported ISS SSTV center frequency."""
    for supported in ISS_SSTV_FREQUENCIES:
        if abs(frequency_mhz - supported) <= ISS_SSTV_FREQ_TOLERANCE_MHZ:
            return supported
    return None


@sstv_bp.route("/status")
def get_status():
    """
    Get SSTV decoder status.

    Returns:
        JSON with decoder availability and current status.
    """
    available = is_sstv_available()
    decoder = get_sstv_decoder()

    result = {
        "available": available,
        "decoder": decoder.decoder_available,
        "running": decoder.is_running,
        "iss_frequency": ISS_SSTV_FREQ,
        "modulation": ISS_SSTV_MODULATION,
        "image_count": len(decoder.get_images()),
        "doppler_enabled": decoder.doppler_enabled,
    }

    # Include Doppler info if available
    doppler_info = decoder.last_doppler_info
    if doppler_info:
        result["doppler"] = doppler_info.to_dict()

    return jsonify(result)


@sstv_bp.route("/start", methods=["POST"])
def start_decoder():
    """
    Start SSTV decoder.

    JSON body (optional):
        {
            "frequency": 145.800,  // Frequency in MHz (default: ISS 145.800)
            "modulation": "fm",    // ISS mode is FM-only
            "device": 0,           // RTL-SDR device index
            "latitude": 40.7128,   // Observer latitude for Doppler correction
            "longitude": -74.0060  // Observer longitude for Doppler correction
        }

    If latitude and longitude are provided, real-time Doppler shift compensation
    will be enabled, which improves reception by tracking the ISS frequency shift
    as it passes overhead (up to ±3.5 kHz at 145.800 MHz).

    Returns:
        JSON with start status.
    """
    if not is_sstv_available():
        return jsonify(
            {
                "status": "error",
                "message": "SSTV decoder not available. Install numpy and Pillow: pip install numpy Pillow",
            }
        ), 400

    decoder = get_sstv_decoder()

    if decoder.is_running:
        return jsonify(
            {
                "status": "already_running",
                "frequency": ISS_SSTV_FREQ,
                "modulation": ISS_SSTV_MODULATION,
                "doppler_enabled": decoder.doppler_enabled,
            }
        )

    # Clear queue
    while not _sstv_queue.empty():
        try:
            _sstv_queue.get_nowait()
        except queue.Empty:
            break

    # Get parameters
    data = request.get_json(silent=True) or {}
    sdr_type_str = data.get("sdr_type", "rtlsdr")

    if sdr_type_str != "rtlsdr":
        return jsonify(
            {
                "status": "error",
                "message": f"{sdr_type_str.replace('_', ' ').title()} is not yet supported for this mode. Please use an RTL-SDR device.",
            }
        ), 400

    frequency = data.get("frequency", ISS_SSTV_FREQ)
    modulation = str(data.get("modulation", ISS_SSTV_MODULATION)).strip().lower()
    device_index = data.get("device", 0)
    latitude = data.get("latitude")
    longitude = data.get("longitude")

    # Validate modulation (ISS mode is FM-only)
    if modulation != ISS_SSTV_MODULATION:
        return jsonify(
            {"status": "error", "message": f"Modulation must be {ISS_SSTV_MODULATION} for ISS SSTV mode"}
        ), 400

    # Validate frequency
    try:
        frequency = float(frequency)
        normalized_frequency = _normalize_iss_frequency(frequency)
        if normalized_frequency is None:
            supported = ", ".join(f"{freq:.3f}" for freq in ISS_SSTV_FREQUENCIES)
            return jsonify({"status": "error", "message": f"Supported ISS SSTV frequency: {supported} MHz FM"}), 400
        frequency = normalized_frequency
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid frequency"}), 400

    # Validate location if provided
    if latitude is not None and longitude is not None:
        try:
            latitude = float(latitude)
            longitude = float(longitude)
            if not (-90 <= latitude <= 90):
                return jsonify({"status": "error", "message": "Latitude must be between -90 and 90"}), 400
            if not (-180 <= longitude <= 180):
                return jsonify({"status": "error", "message": "Longitude must be between -180 and 180"}), 400
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid latitude or longitude"}), 400
    else:
        latitude = None
        longitude = None

    # Claim SDR device
    global sstv_active_device, sstv_active_sdr_type
    device_int = int(device_index)
    error = app_module.claim_sdr_device(device_int, "sstv", sdr_type_str)
    if error:
        return jsonify({"status": "error", "error_type": "DEVICE_BUSY", "message": error}), 409

    # Set callback and start
    decoder.set_callback(_progress_callback)
    success = decoder.start(
        frequency=frequency,
        device_index=device_index,
        latitude=latitude,
        longitude=longitude,
        modulation=ISS_SSTV_MODULATION,
    )

    if success:
        sstv_active_device = device_int
        sstv_active_sdr_type = sdr_type_str

        result = {
            "status": "started",
            "frequency": frequency,
            "modulation": ISS_SSTV_MODULATION,
            "device": device_index,
            "doppler_enabled": decoder.doppler_enabled,
        }

        # Include initial Doppler info if available
        if decoder.doppler_enabled and decoder.last_doppler_info:
            result["doppler"] = decoder.last_doppler_info.to_dict()

        return jsonify(result)
    else:
        # Release device on failure
        app_module.release_sdr_device(device_int, sdr_type_str)
        return start_failure(decoder.last_start_error)


@sstv_bp.route("/stop", methods=["POST"])
def stop_decoder():
    """
    Stop SSTV decoder.

    Returns:
        JSON confirmation.
    """
    global sstv_active_device, sstv_active_sdr_type
    decoder = get_sstv_decoder()
    decoder.stop()

    # Release device from registry
    if sstv_active_device is not None:
        app_module.release_sdr_device(sstv_active_device, sstv_active_sdr_type)
        sstv_active_device = None

    return jsonify({"status": "stopped"})


@sstv_bp.route("/doppler")
def get_doppler():
    """
    Get current Doppler shift information.

    Returns real-time Doppler shift data if tracking is enabled.

    Returns:
        JSON with Doppler shift information.
    """
    decoder = get_sstv_decoder()

    if not decoder.doppler_enabled:
        return jsonify(
            {
                "status": "disabled",
                "message": "Doppler tracking not enabled. Provide latitude/longitude when starting decoder.",
            }
        )

    doppler_info = decoder.last_doppler_info
    if not doppler_info:
        return jsonify({"status": "unavailable", "message": "Doppler data not yet available"})

    return jsonify(
        {
            "status": "ok",
            "doppler": doppler_info.to_dict(),
            "nominal_frequency_mhz": ISS_SSTV_FREQ,
            "corrected_frequency_mhz": doppler_info.frequency_hz / 1_000_000,
        }
    )


@sstv_bp.route("/images")
def list_images():
    """
    Get list of decoded SSTV images.

    Query parameters:
        limit: Maximum number of images to return (default: all)

    Returns:
        JSON with list of decoded images.
    """
    decoder = get_sstv_decoder()
    images = decoder.get_images()

    limit = request.args.get("limit", type=int)
    if limit and limit > 0:
        images = images[-limit:]

    return jsonify({"status": "ok", "images": [img.to_dict() for img in images], "count": len(images)})


@sstv_bp.route("/images/<filename>")
def get_image(filename: str):
    """
    Get a decoded SSTV image file.

    Args:
        filename: Image filename

    Returns:
        Image file or 404.
    """
    decoder = get_sstv_decoder()

    # Security: only allow alphanumeric filenames with .png extension
    if not filename.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return api_error("Invalid filename", 400)

    if not filename.endswith(".png"):
        return api_error("Only PNG files supported", 400)

    # Find image in decoder's output directory
    image_path = decoder._output_dir / filename

    if not image_path.exists():
        return api_error("Image not found", 404)

    return send_file(image_path, mimetype="image/png")


@sstv_bp.route("/images/<filename>/download")
def download_image(filename: str):
    """
    Download a decoded SSTV image file.

    Args:
        filename: Image filename

    Returns:
        Image file as attachment or 404.
    """
    decoder = get_sstv_decoder()

    # Security: only allow alphanumeric filenames with .png extension
    if not filename.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return api_error("Invalid filename", 400)

    if not filename.endswith(".png"):
        return api_error("Only PNG files supported", 400)

    image_path = decoder._output_dir / filename

    if not image_path.exists():
        return api_error("Image not found", 404)

    return send_file(image_path, mimetype="image/png", as_attachment=True, download_name=filename)


@sstv_bp.route("/images/<filename>", methods=["DELETE"])
def delete_image(filename: str):
    """
    Delete a decoded SSTV image.

    Args:
        filename: Image filename

    Returns:
        JSON confirmation.
    """
    decoder = get_sstv_decoder()

    # Security: only allow alphanumeric filenames with .png extension
    if not filename.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return api_error("Invalid filename", 400)

    if not filename.endswith(".png"):
        return api_error("Only PNG files supported", 400)

    if decoder.delete_image(filename):
        return jsonify({"status": "ok"})
    else:
        return api_error("Image not found", 404)


@sstv_bp.route("/images", methods=["DELETE"])
def delete_all_images():
    """
    Delete all decoded SSTV images.

    Returns:
        JSON with count of deleted images.
    """
    decoder = get_sstv_decoder()
    count = decoder.delete_all_images()
    return jsonify({"status": "ok", "deleted": count})


@sstv_bp.route("/stream")
def stream_progress():
    """
    SSE stream of SSTV decode progress.

    Provides real-time Server-Sent Events stream of decode progress.

    Event format:
        data: {"type": "sstv_progress", "status": "decoding", "mode": "PD120", ...}

    Returns:
        SSE stream (text/event-stream)
    """

    def _on_msg(msg: dict[str, Any]) -> None:
        process_event("sstv", msg, msg.get("type"))

    response = Response(
        sse_stream_fanout(
            source_queue=_sstv_queue,
            channel_key="sstv",
            timeout=1.0,
            keepalive_interval=30.0,
            on_message=_on_msg,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


def _get_timescale():
    """Return a cached skyfield timescale (expensive to create)."""
    global _timescale
    with _timescale_lock:
        if _timescale is None:
            from skyfield.api import load

            _timescale = load.timescale(builtin=True)
        return _timescale


@sstv_bp.route("/iss-schedule")
def iss_schedule():
    """
    Get ISS pass schedule for SSTV reception.

    Calculates ISS passes directly using skyfield.
    Results are cached for 15 minutes per rounded location.

    Query parameters:
        latitude: Observer latitude (required)
        longitude: Observer longitude (required)
        hours: Hours to look ahead (default: 48)

    Returns:
        JSON with ISS pass schedule.
    """
    global _iss_schedule_cache, _iss_schedule_cache_time, _iss_schedule_cache_key

    lat = request.args.get("latitude", type=float)
    lon = request.args.get("longitude", type=float)
    hours = request.args.get("hours", 48, type=int)

    if lat is None or lon is None:
        return jsonify({"status": "error", "message": "latitude and longitude parameters required"}), 400

    # Cache key: rounded lat/lon (1 decimal place) so nearby locations share cache
    cache_key = f"{round(lat, 1)}:{round(lon, 1)}:{hours}"

    with _iss_schedule_lock:
        now = time.time()
        if (
            _iss_schedule_cache is not None
            and cache_key == _iss_schedule_cache_key
            and (now - _iss_schedule_cache_time) < ISS_SCHEDULE_CACHE_TTL
        ):
            return jsonify(_iss_schedule_cache)

    try:
        from datetime import timedelta

        from skyfield.almanac import find_discrete
        from skyfield.api import EarthSatellite, wgs84

        # Get ISS TLE from live cache (kept fresh by auto-refresh)
        iss_tle = get_cached_tle("ISS")
        if not iss_tle:
            return jsonify({"status": "error", "message": "ISS TLE data not available"}), 500

        ts = _get_timescale()
        satellite = EarthSatellite(iss_tle[1], iss_tle[2], iss_tle[0], ts)
        observer = wgs84.latlon(lat, lon)

        t0 = ts.now()
        t1 = ts.utc(t0.utc_datetime() + timedelta(hours=hours))

        def above_horizon(t):
            diff = satellite - observer
            topocentric = diff.at(t)
            alt, _, _ = topocentric.altaz()
            return alt.degrees > 0

        above_horizon.step_days = 1 / 720

        times, events = find_discrete(t0, t1, above_horizon)

        passes = []
        i = 0
        while i < len(times):
            if i < len(events) and events[i]:  # Rising
                rise_time = times[i]
                set_time = None

                for j in range(i + 1, len(times)):
                    if not events[j]:  # Setting
                        set_time = times[j]
                        i = j
                        break
                else:
                    i += 1
                    continue

                if set_time is None:
                    i += 1
                    continue

                # Calculate max elevation
                max_el = 0
                duration_seconds = (set_time.utc_datetime() - rise_time.utc_datetime()).total_seconds()
                duration_minutes = int(duration_seconds / 60)

                for k in range(30):
                    frac = k / 29
                    t_point = ts.utc(rise_time.utc_datetime() + timedelta(seconds=duration_seconds * frac))
                    diff = satellite - observer
                    topocentric = diff.at(t_point)
                    alt, _, _ = topocentric.altaz()
                    if alt.degrees > max_el:
                        max_el = alt.degrees

                if max_el >= 10:  # Min elevation filter
                    passes.append(
                        {
                            "satellite": "ISS",
                            "startTime": rise_time.utc_datetime().strftime("%Y-%m-%d %H:%M UTC"),
                            "startTimeISO": rise_time.utc_datetime().isoformat(),
                            "maxEl": round(max_el, 1),
                            "duration": duration_minutes,
                            "color": "#00ffff",
                        }
                    )

            i += 1

        result = {
            "status": "ok",
            "passes": passes,
            "count": len(passes),
            "sstv_frequency": ISS_SSTV_FREQ,
            "note": "ISS SSTV events are not continuous. Check ARISS.org for scheduled events.",
        }

        # Update cache
        with _iss_schedule_lock:
            _iss_schedule_cache = result
            _iss_schedule_cache_time = time.time()
            _iss_schedule_cache_key = cache_key

        return jsonify(result)

    except ImportError:
        return jsonify({"status": "error", "message": "skyfield library not installed"}), 503

    except Exception as e:
        logger.error(f"Error getting ISS schedule: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


def _fetch_iss_position() -> dict | None:
    """Fetch raw ISS lat/lon/altitude from external APIs, with 10s cache."""
    global _iss_position_cache, _iss_position_cache_time

    with _iss_position_lock:
        now = time.time()
        if _iss_position_cache is not None and (now - _iss_position_cache_time) < ISS_POSITION_CACHE_TTL:
            return _iss_position_cache

    import requests

    cached = None

    # Try primary API: Where The ISS At
    try:
        response = requests.get("https://api.wheretheiss.at/v1/satellites/25544", timeout=3)
        if response.status_code == 200:
            data = response.json()
            cached = {
                "lat": float(data["latitude"]),
                "lon": float(data["longitude"]),
                "altitude": float(data.get("altitude", 420)),
                "source": "wheretheiss",
            }
    except Exception as e:
        logger.warning(f"Where The ISS At API failed: {e}")

    # Try fallback API: Open Notify
    if cached is None:
        try:
            response = requests.get("http://api.open-notify.org/iss-now.json", timeout=3)
            if response.status_code == 200:
                data = response.json()
                if data.get("message") == "success":
                    cached = {
                        "lat": float(data["iss_position"]["latitude"]),
                        "lon": float(data["iss_position"]["longitude"]),
                        "altitude": 420,
                        "source": "open-notify",
                    }
        except Exception as e:
            logger.warning(f"Open Notify API failed: {e}")

    if cached is not None:
        with _iss_position_lock:
            _iss_position_cache = cached
            _iss_position_cache_time = time.time()

    return cached


@sstv_bp.route("/iss-position")
def iss_position():
    """
    Get current ISS position from real-time API.

    Uses the "Where The ISS At" API for accurate real-time position,
    with fallback to Open Notify API.  Raw position is cached for 10 seconds;
    observer-relative data (elevation/azimuth) is computed per-request.

    Query parameters:
        latitude: Observer latitude (optional, for elevation calc)
        longitude: Observer longitude (optional, for elevation calc)

    Returns:
        JSON with ISS current position.
    """
    from datetime import datetime

    observer_lat = request.args.get("latitude", type=float)
    observer_lon = request.args.get("longitude", type=float)

    pos = _fetch_iss_position()
    if pos is None:
        return jsonify({"status": "error", "message": "Unable to fetch ISS position from real-time APIs"}), 503

    result = {
        "status": "ok",
        "lat": pos["lat"],
        "lon": pos["lon"],
        "altitude": pos["altitude"],
        "timestamp": datetime.utcnow().isoformat(),
        "source": pos["source"],
    }

    # Calculate observer-relative data if location provided
    if observer_lat is not None and observer_lon is not None:
        result.update(_calculate_observer_data(pos["lat"], pos["lon"], observer_lat, observer_lon))

    return jsonify(result)


def _calculate_observer_data(iss_lat: float, iss_lon: float, obs_lat: float, obs_lon: float) -> dict:
    """Calculate elevation, azimuth, and distance from observer to ISS."""
    import math

    # ISS altitude in km
    iss_alt_km = 420

    # Earth radius in km
    earth_radius = 6371

    # Convert to radians
    lat1 = math.radians(obs_lat)
    lat2 = math.radians(iss_lat)
    lon1 = math.radians(obs_lon)
    lon2 = math.radians(iss_lon)

    # Haversine for ground distance
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    ground_distance = earth_radius * c

    # Calculate elevation angle (simplified)
    # Using spherical geometry approximation
    iss_height = iss_alt_km
    slant_range = math.sqrt(ground_distance**2 + iss_height**2)

    if ground_distance > 0:
        elevation = math.degrees(math.atan2(iss_height - (ground_distance**2 / (2 * earth_radius)), ground_distance))
    else:
        elevation = 90.0

    # Calculate azimuth
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    azimuth = math.degrees(math.atan2(y, x))
    azimuth = (azimuth + 360) % 360

    return {"elevation": round(elevation, 1), "azimuth": round(azimuth, 1), "distance": round(slant_range, 1)}


@sstv_bp.route("/iss-track")
def iss_track():
    """
    Return ISS ground track points propagated from TLE data.

    Uses skyfield SGP4 propagation over ±90 minutes (roughly one full orbit)
    to produce an accurate track that accounts for Earth's rotation.

    Returns:
        JSON with list of {lat, lon, past} points.
    """
    try:
        from datetime import timedelta

        from skyfield.api import EarthSatellite, wgs84

        iss_tle = get_cached_tle("ISS")
        if not iss_tle:
            return jsonify({"status": "error", "message": "ISS TLE not available"}), 500

        ts = _get_timescale()
        satellite = EarthSatellite(iss_tle[1], iss_tle[2], iss_tle[0], ts)
        now = ts.now()
        now_dt = now.utc_datetime()

        track = []
        for minutes_offset in range(-90, 91, 1):
            t_point = ts.utc(now_dt + timedelta(minutes=minutes_offset))
            try:
                geo = satellite.at(t_point)
                sp = wgs84.subpoint(geo)
                track.append(
                    {
                        "lat": round(float(sp.latitude.degrees), 4),
                        "lon": round(float(sp.longitude.degrees), 4),
                        "past": minutes_offset < 0,
                    }
                )
            except Exception:
                continue

        return jsonify({"status": "ok", "track": track})

    except ImportError:
        return jsonify({"status": "error", "message": "skyfield not installed"}), 503
    except Exception as e:
        logger.error(f"Error computing ISS track: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@sstv_bp.route("/decode-file", methods=["POST"])
def decode_file():
    """
    Decode SSTV from an uploaded audio file.

    Expects multipart/form-data with 'audio' file field.

    Returns:
        JSON with decoded images.
    """
    if "audio" not in request.files:
        return jsonify({"status": "error", "message": "No audio file provided"}), 400

    audio_file = request.files["audio"]

    if not audio_file.filename:
        return jsonify({"status": "error", "message": "No file selected"}), 400

    # Save to temp file
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        decoder = get_sstv_decoder()
        images = decoder.decode_file(tmp_path)

        return jsonify({"status": "ok", "images": [img.to_dict() for img in images], "count": len(images)})

    except Exception as e:
        logger.error(f"Error decoding file: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

    finally:
        # Clean up temp file
        with contextlib.suppress(Exception):
            Path(tmp_path).unlink()
