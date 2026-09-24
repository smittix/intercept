"""General SSTV (Slow-Scan Television) decoder routes.

Provides endpoints for decoding terrestrial SSTV images on common HF/VHF/UHF
frequencies used by amateur radio operators worldwide.
"""

from __future__ import annotations

import contextlib
import queue
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file

import app as app_module
from utils.logging import get_logger
from utils.responses import api_error, start_failure
from utils.sse import sse_stream_fanout
from utils.sstv import (
    get_general_sstv_decoder,
)

logger = get_logger("intercept.sstv_general")

sstv_general_bp = Blueprint("sstv_general", __name__, url_prefix="/sstv-general")

# Queue for SSE progress streaming
_sstv_general_queue: queue.Queue = queue.Queue(maxsize=100)

# Track which device is being used
_sstv_general_active_device: int | None = None
_sstv_general_active_sdr_type: str = "rtlsdr"

# Predefined SSTV frequencies
SSTV_FREQUENCIES = [
    {
        "band": "80 m",
        "frequency": 3.845,
        "modulation": "lsb",
        "notes": "Common US SSTV calling frequency",
        "type": "Terrestrial HF",
    },
    {
        "band": "80 m",
        "frequency": 3.730,
        "modulation": "lsb",
        "notes": "Europe primary (analog/digital variants)",
        "type": "Terrestrial HF",
    },
    {
        "band": "40 m",
        "frequency": 7.171,
        "modulation": "lsb",
        "notes": "Common international/US/EU SSTV activity",
        "type": "Terrestrial HF",
    },
    {
        "band": "40 m",
        "frequency": 7.040,
        "modulation": "lsb",
        "notes": "Alternative US/Europe calling",
        "type": "Terrestrial HF",
    },
    {
        "band": "30 m",
        "frequency": 10.132,
        "modulation": "usb",
        "notes": "Narrowband SSTV (e.g., MP73-N digital)",
        "type": "Terrestrial HF",
    },
    {
        "band": "20 m",
        "frequency": 14.230,
        "modulation": "usb",
        "notes": "Most popular international SSTV frequency",
        "type": "Terrestrial HF",
    },
    {
        "band": "20 m",
        "frequency": 14.233,
        "modulation": "usb",
        "notes": "Digital SSTV calling / alternative activity",
        "type": "Terrestrial HF",
    },
    {"band": "20 m", "frequency": 14.240, "modulation": "usb", "notes": "Europe alternative", "type": "Terrestrial HF"},
    {
        "band": "15 m",
        "frequency": 21.340,
        "modulation": "usb",
        "notes": "International calling frequency",
        "type": "Terrestrial HF",
    },
    {
        "band": "10 m",
        "frequency": 28.680,
        "modulation": "usb",
        "notes": "International calling frequency",
        "type": "Terrestrial HF",
    },
    {
        "band": "6 m",
        "frequency": 50.950,
        "modulation": "usb",
        "notes": "SSTV calling (less common)",
        "type": "Terrestrial VHF",
    },
    {
        "band": "2 m",
        "frequency": 145.500,
        "modulation": "fm",
        "notes": "Australia/common simplex (FM sometimes used)",
        "type": "Terrestrial VHF",
    },
    {
        "band": "70 cm",
        "frequency": 433.775,
        "modulation": "fm",
        "notes": "Australia/common simplex",
        "type": "Terrestrial UHF",
    },
]

# Build a lookup for auto-detecting modulation from frequency
_FREQ_MODULATION_MAP = {entry["frequency"]: entry["modulation"] for entry in SSTV_FREQUENCIES}


def _progress_callback(data: dict) -> None:
    """Callback to queue progress/scope updates for SSE stream."""
    try:
        _sstv_general_queue.put_nowait(data)
    except queue.Full:
        try:
            _sstv_general_queue.get_nowait()
            _sstv_general_queue.put_nowait(data)
        except queue.Empty:
            pass


@sstv_general_bp.route("/frequencies")
def get_frequencies():
    """Return the predefined SSTV frequency table."""
    return jsonify(
        {
            "status": "ok",
            "frequencies": SSTV_FREQUENCIES,
        }
    )


@sstv_general_bp.route("/status")
def get_status():
    """Get general SSTV decoder status."""
    decoder = get_general_sstv_decoder()

    return jsonify(
        {
            "available": decoder.decoder_available is not None,
            "decoder": decoder.decoder_available,
            "running": decoder.is_running,
            "image_count": len(decoder.get_images()),
        }
    )


@sstv_general_bp.route("/start", methods=["POST"])
def start_decoder():
    """
    Start general SSTV decoder.

    JSON body:
        {
            "frequency": 14.230,     // Frequency in MHz (required)
            "modulation": "usb",     // fm, usb, or lsb (auto-detected from frequency table if omitted)
            "device": 0              // RTL-SDR device index
        }
    """
    decoder = get_general_sstv_decoder()

    if decoder.decoder_available is None:
        return api_error("SSTV decoder not available. Install numpy and Pillow: pip install numpy Pillow", 400)

    if decoder.is_running:
        return jsonify(
            {
                "status": "already_running",
            }
        )

    # Clear queue
    while not _sstv_general_queue.empty():
        try:
            _sstv_general_queue.get_nowait()
        except queue.Empty:
            break

    data = request.get_json(silent=True) or {}
    sdr_type_str = data.get("sdr_type", "rtlsdr")

    if sdr_type_str != "rtlsdr":
        return api_error(
            f"{sdr_type_str.replace('_', ' ').title()} is not yet supported for this mode. Please use an RTL-SDR device.",
            400,
        )

    frequency = data.get("frequency")
    modulation = data.get("modulation")
    device_index = data.get("device", 0)

    # Validate frequency
    if frequency is None:
        return api_error("Frequency is required", 400)

    try:
        frequency = float(frequency)
        if not (1 <= frequency <= 500):
            return api_error("Frequency must be between 1-500 MHz (HF requires upconverter for RTL-SDR)", 400)
    except (TypeError, ValueError):
        return api_error("Invalid frequency", 400)

    # Auto-detect modulation from frequency table if not specified
    if not modulation:
        modulation = _FREQ_MODULATION_MAP.get(frequency, "usb")

    # Validate modulation
    if modulation not in ("fm", "usb", "lsb"):
        return api_error("Modulation must be fm, usb, or lsb", 400)

    # Claim SDR device
    global _sstv_general_active_device, _sstv_general_active_sdr_type
    device_int = int(device_index)
    error = app_module.claim_sdr_device(device_int, "sstv_general", sdr_type_str)
    if error:
        return api_error(error, 409, error_type="DEVICE_BUSY")

    # Set callback and start
    decoder.set_callback(_progress_callback)
    success = decoder.start(
        frequency=frequency,
        device_index=device_index,
        modulation=modulation,
    )

    if success:
        _sstv_general_active_device = device_int
        _sstv_general_active_sdr_type = sdr_type_str
        return jsonify(
            {
                "status": "started",
                "frequency": frequency,
                "modulation": modulation,
                "device": device_index,
            }
        )
    else:
        app_module.release_sdr_device(device_int, sdr_type_str)
        return start_failure(decoder.last_start_error)


@sstv_general_bp.route("/stop", methods=["POST"])
def stop_decoder():
    """Stop general SSTV decoder."""
    global _sstv_general_active_device, _sstv_general_active_sdr_type
    decoder = get_general_sstv_decoder()
    decoder.stop()

    if _sstv_general_active_device is not None:
        app_module.release_sdr_device(_sstv_general_active_device, _sstv_general_active_sdr_type)
        _sstv_general_active_device = None

    return jsonify({"status": "stopped"})


@sstv_general_bp.route("/images")
def list_images():
    """Get list of decoded SSTV images."""
    decoder = get_general_sstv_decoder()
    images = decoder.get_images()

    limit = request.args.get("limit", type=int)
    if limit and limit > 0:
        images = images[-limit:]

    return jsonify(
        {
            "status": "ok",
            "images": [img.to_dict() for img in images],
            "count": len(images),
        }
    )


@sstv_general_bp.route("/images/<filename>")
def get_image(filename: str):
    """Get a decoded SSTV image file."""
    decoder = get_general_sstv_decoder()

    # Security: only allow alphanumeric filenames with .png extension
    if not filename.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return api_error("Invalid filename", 400)

    if not filename.endswith(".png"):
        return api_error("Only PNG files supported", 400)

    image_path = decoder._output_dir / filename

    if not image_path.exists():
        return api_error("Image not found", 404)

    return send_file(image_path, mimetype="image/png")


@sstv_general_bp.route("/images/<filename>/download")
def download_image(filename: str):
    """Download a decoded SSTV image file."""
    decoder = get_general_sstv_decoder()

    # Security: only allow alphanumeric filenames with .png extension
    if not filename.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return api_error("Invalid filename", 400)

    if not filename.endswith(".png"):
        return api_error("Only PNG files supported", 400)

    image_path = decoder._output_dir / filename

    if not image_path.exists():
        return api_error("Image not found", 404)

    return send_file(image_path, mimetype="image/png", as_attachment=True, download_name=filename)


@sstv_general_bp.route("/images/<filename>", methods=["DELETE"])
def delete_image(filename: str):
    """Delete a decoded SSTV image."""
    decoder = get_general_sstv_decoder()

    # Security: only allow alphanumeric filenames with .png extension
    if not filename.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return api_error("Invalid filename", 400)

    if not filename.endswith(".png"):
        return api_error("Only PNG files supported", 400)

    if decoder.delete_image(filename):
        return jsonify({"status": "ok"})
    else:
        return api_error("Image not found", 404)


@sstv_general_bp.route("/images", methods=["DELETE"])
def delete_all_images():
    """Delete all decoded SSTV images."""
    decoder = get_general_sstv_decoder()
    count = decoder.delete_all_images()
    return jsonify({"status": "ok", "deleted": count})


@sstv_general_bp.route("/stream")
def stream_progress():
    """SSE stream of SSTV decode progress."""

    response = Response(
        sse_stream_fanout(
            source_queue=_sstv_general_queue,
            channel_key="sstv_general",
            timeout=1.0,
            keepalive_interval=30.0,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@sstv_general_bp.route("/decode-file", methods=["POST"])
def decode_file():
    """Decode SSTV from an uploaded audio file."""
    if "audio" not in request.files:
        return api_error("No audio file provided", 400)

    audio_file = request.files["audio"]

    if not audio_file.filename:
        return api_error("No file selected", 400)

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        decoder = get_general_sstv_decoder()
        images = decoder.decode_file(tmp_path)

        return jsonify(
            {
                "status": "ok",
                "images": [img.to_dict() for img in images],
                "count": len(images),
            }
        )

    except Exception as e:
        logger.error(f"Error decoding file: {e}")
        return api_error(str(e), 500)

    finally:
        with contextlib.suppress(Exception):
            Path(tmp_path).unlink()
