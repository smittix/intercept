"""Weather Satellite decoder routes.

Provides endpoints for capturing and decoding Meteor LRPT weather
imagery, including shared results produced by the ground-station
observation pipeline.
"""

from __future__ import annotations

import json
import queue
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file

from utils.logging import get_logger
from utils.responses import api_error
from utils.sdr.device_config import apply_device_defaults
from utils.sse import sse_stream
from utils.validation import (
    validate_device_index,
    validate_elevation,
    validate_gain,
    validate_latitude,
    validate_longitude,
    validate_rtl_tcp_host,
    validate_rtl_tcp_port,
)
from utils.weather_sat import (
    DEFAULT_SAMPLE_RATE,
    WEATHER_SATELLITES,
    CaptureProgress,
    get_weather_sat_decoder,
    is_weather_sat_available,
)

logger = get_logger("intercept.weather_sat")

weather_sat_bp = Blueprint("weather_sat", __name__, url_prefix="/weather-sat")

# Queue for SSE progress streaming
_weather_sat_queue: queue.Queue = queue.Queue(maxsize=100)

METEOR_NORAD_IDS = {
    "METEOR-M2-3": 57166,
    "METEOR-M2-4": 59051,
}
ALLOWED_TEST_DECODE_DIRS = (
    Path(__file__).resolve().parent.parent / "data",
    Path(__file__).resolve().parent.parent / "instance" / "ground_station" / "recordings",
)


def _progress_callback(progress: CaptureProgress) -> None:
    """Callback to queue progress updates for SSE stream."""
    try:
        _weather_sat_queue.put_nowait(progress.to_dict())
    except queue.Full:
        try:
            _weather_sat_queue.get_nowait()
            _weather_sat_queue.put_nowait(progress.to_dict())
        except queue.Empty:
            pass


def _release_weather_sat_device(device_index: int) -> None:
    """Release an SDR device only if weather-sat currently owns it."""
    if device_index < 0:
        return

    try:
        import app as app_module
    except ImportError:
        return

    owner = None
    get_status = getattr(app_module, "get_sdr_device_status", None)
    if callable(get_status):
        try:
            owner = get_status().get(device_index)
        except Exception:
            owner = None

    if owner and owner != "weather_sat":
        logger.debug(
            "Skipping SDR release for device %s owned by %s",
            device_index,
            owner,
        )
        return

    app_module.release_sdr_device(device_index)


@weather_sat_bp.route("/status")
def get_status():
    """Get weather satellite decoder status.

    Returns:
        JSON with decoder availability and current status.
    """
    decoder = get_weather_sat_decoder()
    return jsonify(decoder.get_status())


@weather_sat_bp.route("/satellites")
def list_satellites():
    """Get list of supported weather satellites with frequencies.

    Returns:
        JSON with satellite definitions.
    """
    satellites = []
    for key, info in WEATHER_SATELLITES.items():
        satellites.append(
            {
                "key": key,
                "name": info["name"],
                "frequency": info["frequency"],
                "mode": info["mode"],
                "description": info["description"],
                "active": info["active"],
            }
        )

    return jsonify(
        {
            "status": "ok",
            "satellites": satellites,
        }
    )


@weather_sat_bp.route("/start", methods=["POST"])
def start_capture():
    """Start weather satellite capture and decode.

    JSON body:
        {
            "satellite": "METEOR-M2-3", // Required: satellite key
            "device": 0,               // RTL-SDR device index (default: 0)
            "gain": 30.0,              // SDR gain in dB (default: 30)
            "bias_t": false            // Enable bias-T for LNA (default: false)
        }

    Returns:
        JSON with start status.
    """
    if not is_weather_sat_available():
        return jsonify(
            {
                "status": "error",
                "message": "SatDump not installed. Build from source: https://github.com/SatDump/SatDump",
            }
        ), 400

    decoder = get_weather_sat_decoder()

    if decoder.is_running:
        return jsonify(
            {
                "status": "already_running",
                "satellite": decoder.current_satellite,
                "frequency": decoder.current_frequency,
            }
        )

    data = request.get_json(silent=True) or {}
    data = apply_device_defaults(data)
    sdr_type_str = data.get("sdr_type", "rtlsdr")

    if sdr_type_str != "rtlsdr":
        return jsonify(
            {
                "status": "error",
                "message": f"{sdr_type_str.replace('_', ' ').title()} is not yet supported for this mode. Please use an RTL-SDR device.",
            }
        ), 400

    # Validate satellite
    satellite = data.get("satellite")
    if not satellite or satellite not in WEATHER_SATELLITES:
        return jsonify(
            {"status": "error", "message": f"Invalid satellite. Must be one of: {', '.join(WEATHER_SATELLITES.keys())}"}
        ), 400

    # Validate device index and gain
    try:
        device_index = validate_device_index(data.get("device", 0))
        gain = validate_gain(data.get("gain", 30.0))
    except ValueError as e:
        logger.warning("Invalid parameter in start_capture: %s", e)
        return jsonify({"status": "error", "message": "Invalid parameter value"}), 400

    bias_t = bool(data.get("bias_t", False))

    # Check for rtl_tcp (remote SDR) connection
    rtl_tcp_host = data.get("rtl_tcp_host")
    rtl_tcp_port = data.get("rtl_tcp_port", 1234)

    if rtl_tcp_host:
        try:
            rtl_tcp_host = validate_rtl_tcp_host(rtl_tcp_host)
            rtl_tcp_port = validate_rtl_tcp_port(rtl_tcp_port)
        except ValueError as e:
            return api_error(str(e), 400)

    # Claim SDR device (skip for remote rtl_tcp)
    if not rtl_tcp_host:
        try:
            import app as app_module

            error = app_module.claim_sdr_device(device_index, "weather_sat", sdr_type_str)
            if error:
                return api_error(error, 409, error_type="DEVICE_BUSY")
        except ImportError:
            pass

    # Clear queue
    while not _weather_sat_queue.empty():
        try:
            _weather_sat_queue.get_nowait()
        except queue.Empty:
            break

    # Set callback and on-complete handler for SDR release
    decoder.set_callback(_progress_callback)

    def _release_device():
        if not rtl_tcp_host:
            _release_weather_sat_device(device_index)

    decoder.set_on_complete(_release_device)

    success, error_msg = decoder.start(
        satellite=satellite,
        device_index=device_index,
        gain=gain,
        sample_rate=DEFAULT_SAMPLE_RATE,
        bias_t=bias_t,
        rtl_tcp_host=rtl_tcp_host,
        rtl_tcp_port=rtl_tcp_port,
    )

    if success:
        sat_info = WEATHER_SATELLITES[satellite]
        return jsonify(
            {
                "status": "started",
                "satellite": satellite,
                "frequency": sat_info["frequency"],
                "mode": sat_info["mode"],
                "device": device_index,
            }
        )
    else:
        # Release device on failure
        _release_device()
        return jsonify({"status": "error", "message": error_msg or "Failed to start capture"}), 500


@weather_sat_bp.route("/test-decode", methods=["POST"])
def test_decode():
    """Start weather satellite decode from a pre-recorded file.

    No SDR hardware is required — decodes an IQ baseband or WAV file
    using SatDump offline mode.

    JSON body:
        {
            "satellite": "METEOR-M2-3",   // Required: satellite key
            "input_file": "/path/to/file", // Required: server-side file path
            "sample_rate": 1000000         // Sample rate in Hz (default: 1000000)
        }

    Returns:
        JSON with start status.
    """
    if not is_weather_sat_available():
        return jsonify(
            {
                "status": "error",
                "message": "SatDump not installed. Build from source: https://github.com/SatDump/SatDump",
            }
        ), 400

    decoder = get_weather_sat_decoder()

    if decoder.is_running:
        return jsonify(
            {
                "status": "already_running",
                "satellite": decoder.current_satellite,
                "frequency": decoder.current_frequency,
            }
        )

    data = request.get_json(silent=True) or {}

    # Validate satellite
    satellite = data.get("satellite")
    if not satellite or satellite not in WEATHER_SATELLITES:
        return jsonify(
            {"status": "error", "message": f"Invalid satellite. Must be one of: {', '.join(WEATHER_SATELLITES.keys())}"}
        ), 400

    # Validate input file
    input_file = data.get("input_file")
    if not input_file:
        return jsonify({"status": "error", "message": "input_file is required"}), 400

    from pathlib import Path

    input_path = Path(input_file)

    # Restrict test-decode to application-owned sample and recording paths.
    try:
        resolved = input_path.resolve()
        if not any(resolved.is_relative_to(base) for base in ALLOWED_TEST_DECODE_DIRS):
            return jsonify(
                {"status": "error", "message": "input_file must be under INTERCEPT data or ground-station recordings"}
            ), 403
    except (OSError, ValueError):
        return jsonify({"status": "error", "message": "Invalid file path"}), 400

    if not input_path.is_file():
        logger.warning("Test-decode file not found")
        return jsonify({"status": "error", "message": "File not found"}), 404

    # Validate sample rate
    sample_rate = data.get("sample_rate", 1000000)
    try:
        sample_rate = int(sample_rate)
        if sample_rate < 1000 or sample_rate > 20000000:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid sample_rate (1000-20000000)"}), 400

    # Clear queue
    while not _weather_sat_queue.empty():
        try:
            _weather_sat_queue.get_nowait()
        except queue.Empty:
            break

    # Set callback — no on_complete needed (no SDR to release)
    decoder.set_callback(_progress_callback)
    decoder.set_on_complete(None)

    success, error_msg = decoder.start_from_file(
        satellite=satellite,
        input_file=input_file,
        sample_rate=sample_rate,
    )

    if success:
        sat_info = WEATHER_SATELLITES[satellite]
        return jsonify(
            {
                "status": "started",
                "satellite": satellite,
                "frequency": sat_info["frequency"],
                "mode": sat_info["mode"],
                "source": "file",
                "input_file": str(input_file),
            }
        )
    else:
        return jsonify({"status": "error", "message": error_msg or "Failed to start file decode"}), 500


@weather_sat_bp.route("/stop", methods=["POST"])
def stop_capture():
    """Stop weather satellite capture.

    Returns:
        JSON confirmation.
    """
    decoder = get_weather_sat_decoder()
    device_index = decoder.device_index

    decoder.stop()

    _release_weather_sat_device(device_index)

    return jsonify({"status": "stopped"})


@weather_sat_bp.route("/images")
def list_images():
    """Get list of decoded weather satellite images.

    Query parameters:
        limit: Maximum number of images (default: all)
        satellite: Filter by satellite key (optional)

    Returns:
        JSON with list of decoded images.
    """
    decoder = get_weather_sat_decoder()
    images = [
        {
            **img.to_dict(),
            "source": "weather_sat",
            "deletable": True,
        }
        for img in decoder.get_images()
    ]
    images.extend(_get_ground_station_images())

    # Filter by satellite if specified
    satellite_filter = request.args.get("satellite")
    if satellite_filter:
        images = [img for img in images if str(img.get("satellite", "")).upper() == satellite_filter.upper()]

    images.sort(key=lambda img: img.get("timestamp") or "", reverse=True)

    # Apply limit
    limit = request.args.get("limit", type=int)
    if limit and limit > 0:
        images = images[:limit]

    return jsonify(
        {
            "status": "ok",
            "images": images,
            "count": len(images),
        }
    )


@weather_sat_bp.route("/images/<filename>")
def get_image(filename: str):
    """Serve a decoded weather satellite image file.

    Args:
        filename: Image filename

    Returns:
        Image file or 404.
    """
    decoder = get_weather_sat_decoder()

    # Security: only allow safe filenames
    if not filename.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return api_error("Invalid filename", 400)

    if not (filename.endswith(".png") or filename.endswith(".jpg") or filename.endswith(".jpeg")):
        return api_error("Only PNG/JPG files supported", 400)

    image_path = decoder._output_dir / filename

    if not image_path.exists():
        return api_error("Image not found", 404)

    mimetype = "image/png" if filename.endswith(".png") else "image/jpeg"
    return send_file(image_path, mimetype=mimetype)


@weather_sat_bp.route("/images/shared/<int:output_id>")
def get_shared_image(output_id: int):
    """Serve a Meteor image stored in ground-station outputs."""
    try:
        from utils.database import get_db

        with get_db() as conn:
            row = conn.execute(
                """
                SELECT file_path FROM ground_station_outputs
                WHERE id=? AND output_type='image'
                """,
                (output_id,),
            ).fetchone()
    except Exception as e:
        logger.warning("Failed to load shared weather image %s: %s", output_id, e)
        return api_error("Image not found", 404)

    if not row:
        return api_error("Image not found", 404)

    image_path = Path(row["file_path"])
    if not image_path.exists():
        return api_error("Image not found", 404)

    suffix = image_path.suffix.lower()
    mimetype = "image/png" if suffix == ".png" else "image/jpeg"
    return send_file(image_path, mimetype=mimetype)


@weather_sat_bp.route("/images/<filename>", methods=["DELETE"])
def delete_image(filename: str):
    """Delete a decoded image.

    Args:
        filename: Image filename

    Returns:
        JSON confirmation.
    """
    decoder = get_weather_sat_decoder()

    if not filename.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return api_error("Invalid filename", 400)

    if decoder.delete_image(filename):
        return jsonify({"status": "deleted", "filename": filename})
    else:
        return api_error("Image not found", 404)


@weather_sat_bp.route("/images", methods=["DELETE"])
def delete_all_images():
    """Delete all decoded weather satellite images.

    Returns:
        JSON with count of deleted images.
    """
    decoder = get_weather_sat_decoder()
    count = decoder.delete_all_images()
    return jsonify({"status": "ok", "deleted": count})


def _get_ground_station_images() -> list[dict]:
    try:
        from utils.database import get_db

        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, norad_id, file_path, metadata_json, created_at
                FROM ground_station_outputs
                WHERE output_type='image' AND backend='meteor_lrpt'
                ORDER BY created_at DESC
                LIMIT 200
                """
            ).fetchall()
    except Exception as e:
        logger.debug("Failed to fetch ground-station weather outputs: %s", e)
        return []

    images: list[dict] = []
    for row in rows:
        file_path = Path(row["file_path"])
        if not file_path.exists():
            continue

        metadata = {}
        raw_metadata = row["metadata_json"]
        if raw_metadata:
            try:
                metadata = json.loads(raw_metadata)
            except json.JSONDecodeError:
                metadata = {}

        satellite = metadata.get("satellite") or _satellite_from_norad(row["norad_id"])
        images.append(
            {
                "filename": file_path.name,
                "satellite": satellite,
                "mode": metadata.get("mode", "LRPT"),
                "timestamp": metadata.get("timestamp") or row["created_at"],
                "frequency": metadata.get("frequency", 137.9),
                "size_bytes": metadata.get("size_bytes") or file_path.stat().st_size,
                "product": metadata.get("product", ""),
                "url": f"/weather-sat/images/shared/{row['id']}",
                "source": "ground_station",
                "deletable": False,
                "output_id": row["id"],
            }
        )
    return images


def _satellite_from_norad(norad_id: int | None) -> str:
    for satellite, known_norad in METEOR_NORAD_IDS.items():
        if known_norad == norad_id:
            return satellite
    return "METEOR"


@weather_sat_bp.route("/stream")
def stream_progress():
    """SSE stream of capture/decode progress.

    Returns:
        SSE stream (text/event-stream)
    """
    response = Response(sse_stream(_weather_sat_queue), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@weather_sat_bp.route("/passes")
def get_passes():
    """Get upcoming weather satellite passes for observer location.

    Query parameters:
        latitude: Observer latitude (required)
        longitude: Observer longitude (required)
        hours: Hours to predict ahead (default: 24, max: 72)
        min_elevation: Minimum elevation in degrees (default: 15)
        trajectory: Include az/el trajectory points (default: false)
        ground_track: Include lat/lon ground track points (default: false)

    Returns:
        JSON with upcoming passes for all weather satellites.
    """
    include_trajectory = request.args.get("trajectory", "false").lower() in ("true", "1")
    include_ground_track = request.args.get("ground_track", "false").lower() in ("true", "1")

    raw_lat = request.args.get("latitude")
    raw_lon = request.args.get("longitude")

    if raw_lat is None or raw_lon is None:
        return api_error("latitude and longitude parameters required", 400)

    try:
        lat = validate_latitude(raw_lat)
        lon = validate_longitude(raw_lon)
    except ValueError as e:
        logger.warning("Invalid coordinates in get_passes: %s", e)
        return api_error("Invalid coordinates", 400)

    hours = max(1, min(request.args.get("hours", 24, type=int), 72))
    min_elevation = max(0, min(request.args.get("min_elevation", 15, type=float), 90))

    try:
        from utils.weather_sat_predict import predict_passes

        all_passes = predict_passes(
            lat=lat,
            lon=lon,
            hours=hours,
            min_elevation=min_elevation,
            include_trajectory=include_trajectory,
            include_ground_track=include_ground_track,
        )

        return jsonify(
            {
                "status": "ok",
                "passes": all_passes,
                "count": len(all_passes),
                "observer": {"latitude": lat, "longitude": lon},
                "prediction_hours": hours,
                "min_elevation": min_elevation,
            }
        )

    except ImportError:
        return jsonify({"status": "error", "message": "skyfield library not installed"}), 503

    except Exception as e:
        logger.error(f"Error predicting passes: {e}")
        return jsonify({"status": "error", "message": "Pass prediction failed"}), 500


# ========================
# Auto-Scheduler Endpoints
# ========================


def _scheduler_event_callback(event: dict) -> None:
    """Forward scheduler events to the SSE queue."""
    try:
        _weather_sat_queue.put_nowait(event)
    except queue.Full:
        try:
            _weather_sat_queue.get_nowait()
            _weather_sat_queue.put_nowait(event)
        except queue.Empty:
            pass


@weather_sat_bp.route("/schedule/enable", methods=["POST"])
def enable_schedule():
    """Enable auto-scheduling of weather satellite captures.

    JSON body:
        {
            "latitude": 51.5,         // Required
            "longitude": -0.1,        // Required
            "min_elevation": 15,      // Minimum pass elevation (default: 15)
            "device": 0,              // RTL-SDR device index (default: 0)
            "gain": 30.0,             // SDR gain (default: 30)
            "bias_t": false           // Enable bias-T (default: false)
        }

    Returns:
        JSON with scheduler status.
    """
    from utils.weather_sat_scheduler import get_weather_sat_scheduler

    data = request.get_json(silent=True) or {}

    if data.get("latitude") is None or data.get("longitude") is None:
        return jsonify({"status": "error", "message": "latitude and longitude required"}), 400

    try:
        lat = validate_latitude(data.get("latitude"))
        lon = validate_longitude(data.get("longitude"))
        min_elev = validate_elevation(data.get("min_elevation", 15))
        device = validate_device_index(data.get("device", 0))
        gain_val = validate_gain(data.get("gain", 40.0))
    except ValueError as e:
        logger.warning("Invalid parameter in enable_schedule: %s", e)
        return jsonify({"status": "error", "message": "Invalid parameter value"}), 400

    scheduler = get_weather_sat_scheduler()
    scheduler.set_callbacks(_progress_callback, _scheduler_event_callback)

    try:
        result = scheduler.enable(
            lat=lat,
            lon=lon,
            min_elevation=min_elev,
            device=device,
            gain=gain_val,
            bias_t=bool(data.get("bias_t", False)),
        )
    except Exception:
        logger.exception("Failed to enable weather sat scheduler")
        return jsonify({"status": "error", "message": "Failed to enable scheduler"}), 500

    return jsonify({"status": "ok", **result})


@weather_sat_bp.route("/schedule/disable", methods=["POST"])
def disable_schedule():
    """Disable auto-scheduling."""
    from utils.weather_sat_scheduler import get_weather_sat_scheduler

    scheduler = get_weather_sat_scheduler()
    result = scheduler.disable()
    return jsonify(result)


@weather_sat_bp.route("/schedule/status")
def schedule_status():
    """Get current scheduler state."""
    from utils.weather_sat_scheduler import get_weather_sat_scheduler

    scheduler = get_weather_sat_scheduler()
    return jsonify(scheduler.get_status())


@weather_sat_bp.route("/schedule/passes")
def schedule_passes():
    """List scheduled passes."""
    from utils.weather_sat_scheduler import get_weather_sat_scheduler

    scheduler = get_weather_sat_scheduler()
    passes = scheduler.get_passes()
    return jsonify(
        {
            "status": "ok",
            "passes": passes,
            "count": len(passes),
        }
    )


@weather_sat_bp.route("/schedule/skip/<pass_id>", methods=["POST"])
def skip_pass(pass_id: str):
    """Skip a scheduled pass."""
    from utils.weather_sat_scheduler import get_weather_sat_scheduler

    if not pass_id.replace("_", "").replace("-", "").isalnum():
        return api_error("Invalid pass ID", 400)

    scheduler = get_weather_sat_scheduler()
    if scheduler.skip_pass(pass_id):
        return jsonify({"status": "skipped", "pass_id": pass_id})
    else:
        return api_error("Pass not found or already processed", 404)
