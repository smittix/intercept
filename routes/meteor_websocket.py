"""WebSocket-based meteor scatter monitoring with waterfall display and ping detection.

Provides:
- WebSocket at /ws/meteor for binary waterfall frames (reuses waterfall_fft pipeline)
- SSE at /meteor/stream for detection events and stats
- REST endpoints for status, events, and export
"""

from __future__ import annotations

import json
import queue
import shutil
import socket
import subprocess
import threading
import time
from contextlib import suppress
from typing import Any

from flask import Blueprint, Flask, Response, jsonify, request

from utils.responses import api_error

try:
    from flask_sock import Sock

    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    Sock = None

from utils.logging import get_logger
from utils.meteor_detector import MeteorDetector
from utils.process import register_process, safe_terminate, unregister_process
from utils.sdr import SDRFactory, SDRType
from utils.sdr.base import SDRCapabilities, SDRDevice
from utils.sdr.device_config import apply_device_defaults
from utils.sse import sse_stream_fanout
from utils.validation import validate_device_index, validate_frequency, validate_gain, validate_ppm
from utils.waterfall_fft import (
    build_binary_frame,
    compute_power_spectrum,
    cu8_to_complex,
    quantize_to_uint8,
)

logger = get_logger("intercept.meteor")

# Module-level shared state
_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "running": False,
    "device": None,
    "frequency_mhz": 0.0,
    "sample_rate": 0,
}
_detector: MeteorDetector | None = None
_sse_queue: queue.Queue = queue.Queue(maxsize=500)

# Maximum bandwidth per SDR type (Hz)
MAX_BANDWIDTH = {
    SDRType.RTL_SDR: 2400000,
    SDRType.HACKRF: 20000000,
    SDRType.LIME_SDR: 20000000,
    SDRType.AIRSPY: 10000000,
    SDRType.SDRPLAY: 2000000,
    SDRType.USRP: 25000000,
    SDRType.BLADE_RF: 40000000,
    SDRType.HYDRA_SDR: 10000000,
}


def _push_sse(data: dict[str, Any]) -> None:
    """Push a message to the SSE queue, dropping oldest if full."""
    try:
        _sse_queue.put_nowait(data)
    except queue.Full:
        try:
            _sse_queue.get_nowait()
            _sse_queue.put_nowait(data)
        except (queue.Empty, queue.Full):
            pass


def _resolve_sdr_type(sdr_type_str: str) -> SDRType:
    mapping = {
        "rtlsdr": SDRType.RTL_SDR,
        "rtl_sdr": SDRType.RTL_SDR,
        "hackrf": SDRType.HACKRF,
        "limesdr": SDRType.LIME_SDR,
        "airspy": SDRType.AIRSPY,
        "sdrplay": SDRType.SDRPLAY,
        "usrp": SDRType.USRP,
        "bladerf": SDRType.BLADE_RF,
        "hydrasdr": SDRType.HYDRA_SDR,
    }
    return mapping.get(sdr_type_str.lower(), SDRType.RTL_SDR)


def _build_dummy_device(device_index: int, sdr_type: SDRType) -> SDRDevice:
    builder = SDRFactory.get_builder(sdr_type)
    caps = builder.get_capabilities()
    return SDRDevice(
        sdr_type=sdr_type,
        index=device_index,
        name=f"{sdr_type.value}-{device_index}",
        serial="N/A",
        driver=sdr_type.value,
        capabilities=caps,
    )


def _pick_sample_rate(span_hz: int, caps: SDRCapabilities, sdr_type: SDRType) -> int:
    valid_rates = sorted({int(r) for r in caps.sample_rates if int(r) > 0})
    if valid_rates:
        return min(valid_rates, key=lambda rate: abs(rate - span_hz))
    max_bw = MAX_BANDWIDTH.get(sdr_type, 2400000)
    return max(62500, min(span_hz, max_bw))


# ── Blueprint for REST/SSE endpoints ──

meteor_bp = Blueprint("meteor", __name__, url_prefix="/meteor")


@meteor_bp.route("/status")
def meteor_status():
    """Return current meteor monitoring status."""
    with _state_lock:
        running = _state["running"]
        freq = _state["frequency_mhz"]
        device = _state["device"]
        sr = _state["sample_rate"]

    detector = _detector
    stats = None
    if detector:
        stats = detector._build_stats(time.time())

    return jsonify(
        {
            "running": running,
            "frequency_mhz": freq,
            "device": device,
            "sample_rate": sr,
            "stats": stats,
        }
    )


@meteor_bp.route("/stream")
def meteor_stream():
    """SSE endpoint for meteor detection events and stats."""
    response = Response(
        sse_stream_fanout(
            source_queue=_sse_queue,
            channel_key="meteor",
            timeout=1.0,
            keepalive_interval=30.0,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@meteor_bp.route("/events")
def meteor_events():
    """Return detected events as JSON."""
    detector = _detector
    if not detector:
        return jsonify({"events": []})
    limit = request.args.get("limit", 500, type=int)
    return jsonify({"events": detector.get_events(limit=limit)})


@meteor_bp.route("/events/export")
def meteor_events_export():
    """Export events as CSV or JSON."""
    detector = _detector
    if not detector:
        return api_error("No active session", 400)

    fmt = request.args.get("format", "json").lower()
    if fmt == "csv":
        csv_data = detector.export_events_csv()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=meteor_events.csv"},
        )
    else:
        json_data = detector.export_events_json()
        return Response(
            json_data,
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=meteor_events.json"},
        )


@meteor_bp.route("/events/clear", methods=["POST"])
def meteor_events_clear():
    """Clear all detected events."""
    detector = _detector
    if not detector:
        return jsonify({"cleared": 0})
    count = detector.clear_events()
    return jsonify({"cleared": count})


# ── WebSocket handler ──


def init_meteor_websocket(app: Flask):
    """Initialize WebSocket meteor scatter streaming."""
    global _detector

    if not WEBSOCKET_AVAILABLE:
        logger.warning("flask-sock not installed, WebSocket meteor disabled")
        return

    sock = Sock(app)

    @sock.route("/ws/meteor")
    def meteor_stream_ws(ws):
        """WebSocket endpoint for meteor scatter waterfall + detection."""
        global _detector
        logger.info("WebSocket meteor client connected")

        import app as app_module

        iq_process = None
        reader_thread = None
        stop_event = threading.Event()
        claimed_device = None
        claimed_sdr_type = "rtlsdr"
        send_queue: queue.Queue = queue.Queue(maxsize=120)

        try:
            while True:
                # Drain send queue
                while True:
                    try:
                        outgoing = send_queue.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        ws.send(outgoing)
                    except Exception:
                        stop_event.set()
                        break

                try:
                    msg = ws.receive(timeout=0.01)
                except Exception as e:
                    err = str(e).lower()
                    if "closed" in err:
                        break
                    if "timed out" not in err:
                        logger.error(f"WebSocket receive error: {e}")
                    continue

                if msg is None:
                    if not ws.connected:
                        break
                    if stop_event.is_set():
                        break
                    continue

                try:
                    data = json.loads(msg)
                except (json.JSONDecodeError, TypeError):
                    continue

                cmd = data.get("cmd")

                if cmd == "start":
                    # Stop any existing capture
                    was_restarting = iq_process is not None
                    stop_event.set()
                    if reader_thread and reader_thread.is_alive():
                        reader_thread.join(timeout=2)
                    if iq_process:
                        safe_terminate(iq_process)
                        unregister_process(iq_process)
                        iq_process = None
                    if claimed_device is not None:
                        app_module.release_sdr_device(claimed_device, claimed_sdr_type)
                        claimed_device = None
                    with _state_lock:
                        _state["running"] = False
                    stop_event.clear()
                    while not send_queue.empty():
                        try:
                            send_queue.get_nowait()
                        except queue.Empty:
                            break
                    if was_restarting:
                        time.sleep(0.5)

                    data = apply_device_defaults(data)
                    # Parse config
                    try:
                        frequency_mhz = float(data.get("frequency_mhz", 143.05))
                        validate_frequency(frequency_mhz)
                        gain_raw = data.get("gain")
                        if gain_raw is None or str(gain_raw).lower() == "auto":
                            gain = None
                        else:
                            gain = validate_gain(float(gain_raw))
                        device_index = validate_device_index(int(data.get("device", 0)))
                        sdr_type_str = data.get("sdr_type", "rtlsdr")
                        sample_rate_req = int(data.get("sample_rate", 250000))
                        fft_size = int(data.get("fft_size", 1024))
                        fps = int(data.get("fps", 20))
                        avg_count = int(data.get("avg_count", 4))
                        ppm = data.get("ppm")
                        if ppm is not None:
                            ppm = validate_ppm(ppm)
                        bias_t = bool(data.get("bias_t", False))

                        # Detection settings
                        snr_threshold = float(data.get("snr_threshold", 6.0))
                        min_duration = float(data.get("min_duration_ms", 50.0))
                        cooldown = float(data.get("cooldown_ms", 200.0))
                        freq_drift = float(data.get("freq_drift_tolerance_hz", 500.0))
                    except (TypeError, ValueError) as exc:
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": f"Invalid configuration: {exc}",
                                }
                            )
                        )
                        continue

                    # Clamp values
                    fft_size = max(256, min(4096, fft_size))
                    fps = max(5, min(30, fps))
                    avg_count = max(1, min(16, avg_count))

                    # Resolve SDR type and sample rate
                    sdr_type = _resolve_sdr_type(sdr_type_str)
                    builder = SDRFactory.get_builder(sdr_type)
                    caps = builder.get_capabilities()
                    sample_rate = _pick_sample_rate(sample_rate_req, caps, sdr_type)

                    # Compute frequency range
                    span_mhz = sample_rate / 1e6
                    start_freq = frequency_mhz - span_mhz / 2
                    end_freq = frequency_mhz + span_mhz / 2

                    # Claim SDR device
                    max_claim_attempts = 4 if was_restarting else 1
                    claim_err = None
                    for _attempt in range(max_claim_attempts):
                        claim_err = app_module.claim_sdr_device(device_index, "meteor", sdr_type_str)
                        if not claim_err:
                            break
                        if _attempt < max_claim_attempts - 1:
                            time.sleep(0.4)
                    if claim_err:
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": claim_err,
                                    "error_type": "DEVICE_BUSY",
                                }
                            )
                        )
                        continue
                    claimed_device = device_index
                    claimed_sdr_type = sdr_type_str

                    # Build I/Q capture command
                    try:
                        device = _build_dummy_device(device_index, sdr_type)
                        iq_cmd = builder.build_iq_capture_command(
                            device=device,
                            frequency_mhz=frequency_mhz,
                            sample_rate=sample_rate,
                            gain=gain,
                            ppm=ppm,
                            bias_t=bias_t,
                        )
                    except NotImplementedError as e:
                        app_module.release_sdr_device(device_index, sdr_type_str)
                        claimed_device = None
                        ws.send(json.dumps({"status": "error", "message": str(e)}))
                        continue

                    # Check binary exists
                    if not shutil.which(iq_cmd[0]):
                        app_module.release_sdr_device(device_index, sdr_type_str)
                        claimed_device = None
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": f'Required tool "{iq_cmd[0]}" not found.',
                                }
                            )
                        )
                        continue

                    # Spawn I/Q capture
                    max_attempts = 3 if was_restarting else 1
                    try:
                        for attempt in range(max_attempts):
                            logger.info(
                                f"Starting meteor I/Q capture: {frequency_mhz:.6f} MHz, "
                                f"sr={sample_rate}, fft={fft_size}"
                            )
                            iq_process = subprocess.Popen(
                                iq_cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                bufsize=0,
                            )
                            register_process(iq_process)

                            time.sleep(0.3)
                            if iq_process.poll() is not None:
                                stderr_out = ""
                                if iq_process.stderr:
                                    with suppress(Exception):
                                        stderr_out = iq_process.stderr.read().decode("utf-8", errors="replace").strip()
                                unregister_process(iq_process)
                                iq_process = None
                                if attempt < max_attempts - 1:
                                    time.sleep(0.5)
                                    continue
                                detail = f": {stderr_out}" if stderr_out else ""
                                raise RuntimeError(f"I/Q process exited immediately{detail}")
                            break
                    except Exception as e:
                        logger.error(f"Failed to start meteor I/Q capture: {e}")
                        if iq_process:
                            safe_terminate(iq_process)
                            unregister_process(iq_process)
                            iq_process = None
                        app_module.release_sdr_device(device_index, sdr_type_str)
                        claimed_device = None
                        ws.send(
                            json.dumps(
                                {
                                    "status": "error",
                                    "message": f"Failed to start I/Q capture: {e}",
                                }
                            )
                        )
                        continue

                    # Initialize detector
                    _detector = MeteorDetector(
                        snr_threshold_db=snr_threshold,
                        min_duration_ms=min_duration,
                        cooldown_ms=cooldown,
                        freq_drift_tolerance_hz=freq_drift,
                    )

                    with _state_lock:
                        _state["running"] = True
                        _state["device"] = device_index
                        _state["frequency_mhz"] = frequency_mhz
                        _state["sample_rate"] = sample_rate

                    # Send confirmation
                    ws.send(
                        json.dumps(
                            {
                                "status": "started",
                                "frequency_mhz": frequency_mhz,
                                "start_freq": start_freq,
                                "end_freq": end_freq,
                                "fft_size": fft_size,
                                "sample_rate": sample_rate,
                                "span_mhz": span_mhz,
                            }
                        )
                    )

                    # Start FFT reader + detection thread
                    def fft_reader(
                        proc,
                        _send_q,
                        stop_evt,
                        detector,
                        _fft_size,
                        _avg_count,
                        _fps,
                        _sample_rate,
                        _start_freq,
                        _end_freq,
                        _freq_mhz,
                    ):
                        required_fft_samples = _fft_size * _avg_count
                        timeslice_samples = max(required_fft_samples, int(_sample_rate / max(1, _fps)))
                        bytes_per_frame = timeslice_samples * 2
                        frame_interval = 1.0 / _fps
                        start_freq_hz = _start_freq * 1e6
                        end_freq_hz = _end_freq * 1e6
                        last_stats_push = 0.0

                        try:
                            while not stop_evt.is_set():
                                if proc.poll() is not None:
                                    break

                                frame_start = time.monotonic()

                                # Read raw I/Q
                                raw = b""
                                remaining = bytes_per_frame
                                while remaining > 0 and not stop_evt.is_set():
                                    chunk = proc.stdout.read(min(remaining, 65536))
                                    if not chunk:
                                        break
                                    raw += chunk
                                    remaining -= len(chunk)

                                if len(raw) < _fft_size * 2:
                                    break

                                # FFT pipeline
                                samples = cu8_to_complex(raw)
                                fft_samples = (
                                    samples[-required_fft_samples:] if len(samples) > required_fft_samples else samples
                                )
                                power_db = compute_power_spectrum(
                                    fft_samples,
                                    fft_size=_fft_size,
                                    avg_count=_avg_count,
                                )
                                quantized = quantize_to_uint8(power_db)
                                frame = build_binary_frame(_start_freq, _end_freq, quantized)

                                # Send waterfall frame via WS
                                with suppress(queue.Full):
                                    _send_q.put_nowait(frame)

                                # Run detection on raw dB spectrum
                                now = time.time()
                                stats, event = detector.process_frame(
                                    power_db,
                                    start_freq_hz,
                                    end_freq_hz,
                                    now,
                                )

                                # Push event immediately via SSE
                                if event:
                                    _push_sse(
                                        {
                                            "type": "event",
                                            "event": event.to_dict(),
                                        }
                                    )
                                    # Also send as JSON via WS for immediate UI update
                                    event_msg = json.dumps(
                                        {
                                            "type": "detection",
                                            "event": event.to_dict(),
                                        }
                                    )
                                    with suppress(queue.Full):
                                        _send_q.put_nowait(event_msg)

                                # Push stats every ~1s via SSE
                                if now - last_stats_push >= 1.0:
                                    _push_sse(stats)
                                    last_stats_push = now

                                # Pace to target FPS
                                elapsed = time.monotonic() - frame_start
                                sleep_time = frame_interval - elapsed
                                if sleep_time > 0:
                                    stop_evt.wait(sleep_time)

                        except Exception as e:
                            logger.debug(f"Meteor FFT reader stopped: {e}")

                    reader_thread = threading.Thread(
                        target=fft_reader,
                        args=(
                            iq_process,
                            send_queue,
                            stop_event,
                            _detector,
                            fft_size,
                            avg_count,
                            fps,
                            sample_rate,
                            start_freq,
                            end_freq,
                            frequency_mhz,
                        ),
                        daemon=True,
                    )
                    reader_thread.start()

                elif cmd == "update_threshold":
                    detector = _detector
                    if detector:
                        detector.update_settings(
                            snr_threshold_db=data.get("snr_threshold"),
                            min_duration_ms=data.get("min_duration_ms"),
                            cooldown_ms=data.get("cooldown_ms"),
                            freq_drift_tolerance_hz=data.get("freq_drift_tolerance_hz"),
                        )
                        ws.send(json.dumps({"status": "threshold_updated"}))

                elif cmd == "stop":
                    stop_event.set()
                    if reader_thread and reader_thread.is_alive():
                        reader_thread.join(timeout=2)
                        reader_thread = None
                    if iq_process:
                        safe_terminate(iq_process)
                        unregister_process(iq_process)
                        iq_process = None
                    if claimed_device is not None:
                        app_module.release_sdr_device(claimed_device, claimed_sdr_type)
                        claimed_device = None
                    with _state_lock:
                        _state["running"] = False
                        _state["device"] = None
                    stop_event.clear()
                    ws.send(json.dumps({"status": "stopped"}))

        except Exception as e:
            logger.info(f"WebSocket meteor closed: {e}")
        finally:
            stop_event.set()
            if reader_thread and reader_thread.is_alive():
                reader_thread.join(timeout=2)
            if iq_process:
                safe_terminate(iq_process)
                unregister_process(iq_process)
            if claimed_device is not None:
                app_module.release_sdr_device(claimed_device, claimed_sdr_type)
            with _state_lock:
                _state["running"] = False
                _state["device"] = None
            with suppress(Exception):
                ws.close()
            with suppress(Exception):
                ws.sock.shutdown(socket.SHUT_RDWR)
            with suppress(Exception):
                ws.sock.close()
            logger.info("WebSocket meteor client disconnected")
