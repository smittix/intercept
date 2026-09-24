"""SubGHz transceiver routes.

Provides endpoints for HackRF-based SubGHz signal capture, protocol decoding,
signal replay/transmit, and wideband spectrum analysis.
"""

from __future__ import annotations

import contextlib
import queue

from flask import Blueprint, Response, jsonify, request, send_file

from utils.constants import (
    SUBGHZ_FREQ_MAX_MHZ,
    SUBGHZ_FREQ_MIN_MHZ,
    SUBGHZ_LNA_GAIN_MAX,
    SUBGHZ_PRESETS,
    SUBGHZ_SAMPLE_RATES,
    SUBGHZ_TX_MAX_DURATION,
    SUBGHZ_TX_VGA_GAIN_MAX,
    SUBGHZ_VGA_GAIN_MAX,
)
from utils.event_pipeline import process_event
from utils.logging import get_logger
from utils.responses import api_error
from utils.sse import sse_stream
from utils.subghz import get_subghz_manager

logger = get_logger("intercept.subghz")

subghz_bp = Blueprint("subghz", __name__, url_prefix="/subghz")


def _start_status(result: dict) -> int:
    """HTTP status for a start result: a missing tool is the operator's to
    fix (400); anything else refused a start that conflicts (409)."""
    if result.get("status") != "error":
        return 200
    return 400 if result.get("error_type") == "TOOL_MISSING" else 409


# SSE queue for streaming events to frontend
_subghz_queue: queue.Queue = queue.Queue(maxsize=200)


def _event_callback(event: dict) -> None:
    """Forward SubGhzManager events to the SSE queue."""
    with contextlib.suppress(Exception):
        process_event("subghz", event, event.get("type"))
    try:
        _subghz_queue.put_nowait(event)
    except queue.Full:
        try:
            _subghz_queue.get_nowait()
            _subghz_queue.put_nowait(event)
        except queue.Empty:
            pass


def _validate_frequency_hz(data: dict, key: str = "frequency_hz") -> tuple[int | None, str | None]:
    """Validate frequency in Hz from request data. Returns (freq_hz, error_msg)."""
    raw = data.get(key)
    if raw is None:
        return None, f"{key} is required"
    try:
        freq_hz = int(raw)
        freq_mhz = freq_hz / 1_000_000
        if not (SUBGHZ_FREQ_MIN_MHZ <= freq_mhz <= SUBGHZ_FREQ_MAX_MHZ):
            return None, f"Frequency must be between {SUBGHZ_FREQ_MIN_MHZ}-{SUBGHZ_FREQ_MAX_MHZ} MHz"
        return freq_hz, None
    except (ValueError, TypeError):
        return None, f"Invalid {key}"


def _validate_serial(data: dict) -> str | None:
    """Extract and validate optional HackRF device serial."""
    serial = data.get("device_serial", "")
    if not serial or not isinstance(serial, str):
        return None
    # HackRF serials are hex strings
    serial = serial.strip()
    if serial and all(c in "0123456789abcdefABCDEF" for c in serial):
        return serial
    return None


def _validate_int(data: dict, key: str, default: int, min_val: int, max_val: int) -> int:
    """Validate integer parameter with bounds clamping."""
    try:
        val = int(data.get(key, default))
        return max(min_val, min(max_val, val))
    except (ValueError, TypeError):
        return default


def _validate_decode_profile(data: dict, default: str = "weather") -> str:
    profile = data.get("decode_profile", default)
    if not isinstance(profile, str):
        return default
    profile = profile.strip().lower()
    if profile in {"weather", "all"}:
        return profile
    return default


def _validate_optional_float(data: dict, key: str) -> tuple[float | None, str | None]:
    raw = data.get(key)
    if raw is None or raw == "":
        return None, None
    try:
        return float(raw), None
    except (ValueError, TypeError):
        return None, f"Invalid {key}"


def _validate_bool(data: dict, key: str, default: bool = False) -> bool:
    raw = data.get(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return default


# ------------------------------------------------------------------
# STATUS
# ------------------------------------------------------------------


@subghz_bp.route("/status")
def get_status():
    manager = get_subghz_manager()
    return jsonify(manager.get_status())


@subghz_bp.route("/presets")
def get_presets():
    return jsonify({"presets": SUBGHZ_PRESETS, "sample_rates": SUBGHZ_SAMPLE_RATES})


# ------------------------------------------------------------------
# RECEIVE
# ------------------------------------------------------------------


@subghz_bp.route("/receive/start", methods=["POST"])
def start_receive():
    data = request.get_json(silent=True) or {}

    freq_hz, err = _validate_frequency_hz(data)
    if err:
        return api_error(err, 400)

    sample_rate = _validate_int(data, "sample_rate", 2000000, 2000000, 20000000)
    lna_gain = _validate_int(data, "lna_gain", 32, 0, SUBGHZ_LNA_GAIN_MAX)
    vga_gain = _validate_int(data, "vga_gain", 20, 0, SUBGHZ_VGA_GAIN_MAX)
    trigger_enabled = _validate_bool(data, "trigger_enabled", False)
    trigger_pre_ms = _validate_int(data, "trigger_pre_ms", 350, 50, 5000)
    trigger_post_ms = _validate_int(data, "trigger_post_ms", 700, 100, 10000)
    device_serial = _validate_serial(data)

    manager = get_subghz_manager()
    manager.set_callback(_event_callback)

    result = manager.start_receive(
        frequency_hz=freq_hz,
        sample_rate=sample_rate,
        lna_gain=lna_gain,
        vga_gain=vga_gain,
        trigger_enabled=trigger_enabled,
        trigger_pre_ms=trigger_pre_ms,
        trigger_post_ms=trigger_post_ms,
        device_serial=device_serial,
    )

    return jsonify(result), _start_status(result)


@subghz_bp.route("/receive/stop", methods=["POST"])
def stop_receive():
    manager = get_subghz_manager()
    result = manager.stop_receive()
    return jsonify(result)


# ------------------------------------------------------------------
# DECODE
# ------------------------------------------------------------------


@subghz_bp.route("/decode/start", methods=["POST"])
def start_decode():
    data = request.get_json(silent=True) or {}

    freq_hz, err = _validate_frequency_hz(data)
    if err:
        return api_error(err, 400)

    sample_rate = _validate_int(data, "sample_rate", 2000000, 2000000, 20000000)
    lna_gain = _validate_int(data, "lna_gain", 32, 0, SUBGHZ_LNA_GAIN_MAX)
    vga_gain = _validate_int(data, "vga_gain", 20, 0, SUBGHZ_VGA_GAIN_MAX)
    decode_profile = _validate_decode_profile(data)
    device_serial = _validate_serial(data)

    manager = get_subghz_manager()
    manager.set_callback(_event_callback)

    result = manager.start_decode(
        frequency_hz=freq_hz,
        sample_rate=sample_rate,
        lna_gain=lna_gain,
        vga_gain=vga_gain,
        decode_profile=decode_profile,
        device_serial=device_serial,
    )

    return jsonify(result), _start_status(result)


@subghz_bp.route("/decode/stop", methods=["POST"])
def stop_decode():
    manager = get_subghz_manager()
    result = manager.stop_decode()
    return jsonify(result)


# ------------------------------------------------------------------
# TRANSMIT
# ------------------------------------------------------------------


@subghz_bp.route("/transmit", methods=["POST"])
def start_transmit():
    data = request.get_json(silent=True) or {}

    capture_id = data.get("capture_id")
    if not capture_id or not isinstance(capture_id, str):
        return api_error("capture_id is required", 400)

    # Sanitize capture_id
    if not capture_id.isalnum():
        return api_error("Invalid capture_id", 400)

    tx_gain = _validate_int(data, "tx_gain", 20, 0, SUBGHZ_TX_VGA_GAIN_MAX)
    max_duration = _validate_int(data, "max_duration", 10, 1, SUBGHZ_TX_MAX_DURATION)
    start_seconds, start_err = _validate_optional_float(data, "start_seconds")
    if start_err:
        return api_error(start_err, 400)
    duration_seconds, duration_err = _validate_optional_float(data, "duration_seconds")
    if duration_err:
        return api_error(duration_err, 400)
    device_serial = _validate_serial(data)

    manager = get_subghz_manager()
    manager.set_callback(_event_callback)

    result = manager.transmit(
        capture_id=capture_id,
        tx_gain=tx_gain,
        max_duration=max_duration,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        device_serial=device_serial,
    )

    status_code = 200 if result.get("status") != "error" else 400
    return jsonify(result), status_code


@subghz_bp.route("/transmit/stop", methods=["POST"])
def stop_transmit():
    manager = get_subghz_manager()
    result = manager.stop_transmit()
    return jsonify(result)


# ------------------------------------------------------------------
# SWEEP
# ------------------------------------------------------------------


@subghz_bp.route("/sweep/start", methods=["POST"])
def start_sweep():
    data = request.get_json(silent=True) or {}

    try:
        freq_start = float(data.get("freq_start_mhz", 300))
        freq_end = float(data.get("freq_end_mhz", 928))
        if freq_start >= freq_end:
            return api_error("freq_start must be less than freq_end", 400)
        if freq_start < SUBGHZ_FREQ_MIN_MHZ or freq_end > SUBGHZ_FREQ_MAX_MHZ:
            return api_error(f"Frequency range: {SUBGHZ_FREQ_MIN_MHZ}-{SUBGHZ_FREQ_MAX_MHZ} MHz", 400)
    except (ValueError, TypeError):
        return api_error("Invalid frequency range", 400)

    bin_width = _validate_int(data, "bin_width", 100000, 10000, 5000000)
    device_serial = _validate_serial(data)

    manager = get_subghz_manager()
    manager.set_callback(_event_callback)

    result = manager.start_sweep(
        freq_start_mhz=freq_start,
        freq_end_mhz=freq_end,
        bin_width=bin_width,
        device_serial=device_serial,
    )

    return jsonify(result), _start_status(result)


@subghz_bp.route("/sweep/stop", methods=["POST"])
def stop_sweep():
    manager = get_subghz_manager()
    result = manager.stop_sweep()
    return jsonify(result)


# ------------------------------------------------------------------
# CAPTURES LIBRARY
# ------------------------------------------------------------------


@subghz_bp.route("/captures")
def list_captures():
    manager = get_subghz_manager()
    captures = manager.list_captures()
    return jsonify(
        {
            "status": "ok",
            "captures": [c.to_dict() for c in captures],
            "count": len(captures),
        }
    )


@subghz_bp.route("/captures/<capture_id>")
def get_capture(capture_id: str):
    if not capture_id.isalnum():
        return api_error("Invalid capture_id", 400)

    manager = get_subghz_manager()
    capture = manager.get_capture(capture_id)
    if not capture:
        return api_error("Capture not found", 404)

    return jsonify({"status": "ok", "capture": capture.to_dict()})


@subghz_bp.route("/captures/<capture_id>/download")
def download_capture(capture_id: str):
    if not capture_id.isalnum():
        return api_error("Invalid capture_id", 400)

    manager = get_subghz_manager()
    path = manager.get_capture_path(capture_id)
    if not path:
        return api_error("Capture not found", 404)

    return send_file(
        path,
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=path.name,
    )


@subghz_bp.route("/captures/<capture_id>/trim", methods=["POST"])
def trim_capture(capture_id: str):
    if not capture_id.isalnum():
        return api_error("Invalid capture_id", 400)

    data = request.get_json(silent=True) or {}
    start_seconds, start_err = _validate_optional_float(data, "start_seconds")
    if start_err:
        return api_error(start_err, 400)
    duration_seconds, duration_err = _validate_optional_float(data, "duration_seconds")
    if duration_err:
        return api_error(duration_err, 400)

    label = data.get("label", "")
    if label is None:
        label = ""
    if not isinstance(label, str) or len(label) > 100:
        return api_error("Label must be a string (max 100 chars)", 400)

    manager = get_subghz_manager()
    result = manager.trim_capture(
        capture_id=capture_id,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        label=label,
    )

    if result.get("status") == "ok":
        return jsonify(result), 200
    message = str(result.get("message") or "Trim failed")
    status_code = 404 if "not found" in message.lower() else 400
    return jsonify(result), status_code


@subghz_bp.route("/captures/<capture_id>", methods=["DELETE"])
def delete_capture(capture_id: str):
    if not capture_id.isalnum():
        return api_error("Invalid capture_id", 400)

    manager = get_subghz_manager()
    if manager.delete_capture(capture_id):
        return jsonify({"status": "deleted", "id": capture_id})
    return api_error("Capture not found", 404)


@subghz_bp.route("/captures/<capture_id>", methods=["PATCH"])
def update_capture(capture_id: str):
    if not capture_id.isalnum():
        return api_error("Invalid capture_id", 400)

    data = request.get_json(silent=True) or {}
    label = data.get("label", "")

    if not isinstance(label, str) or len(label) > 100:
        return api_error("Label must be a string (max 100 chars)", 400)

    manager = get_subghz_manager()
    if manager.update_capture_label(capture_id, label):
        return jsonify({"status": "updated", "id": capture_id, "label": label})
    return api_error("Capture not found", 404)


# ------------------------------------------------------------------
# SSE STREAM
# ------------------------------------------------------------------


@subghz_bp.route("/stream")
def stream():
    response = Response(sse_stream(_subghz_queue), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response
