"""Generic OOK signal decoder routes.

Captures raw OOK frames using rtl_433's flex decoder and streams decoded
bit/hex data to the browser for live ASCII interpretation.  Supports
PWM, PPM, and Manchester modulation with fully configurable pulse timing.
"""

from __future__ import annotations

import contextlib
import os
import queue
import signal
import subprocess
import threading
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.logging import sensor_logger as logger
from utils.ook import ook_parser_thread
from utils.process import register_process, safe_terminate, unregister_process
from utils.responses import api_error
from utils.sdr import SDRFactory, SDRType
from utils.sdr.device_config import apply_device_defaults
from utils.sse import sse_stream_fanout
from utils.validation import (
    validate_device_index,
    validate_frequency,
    validate_gain,
    validate_positive_int,
    validate_ppm,
    validate_rtl_tcp_host,
    validate_rtl_tcp_port,
)

ook_bp = Blueprint("ook", __name__)

# Track which device / SDR type is being used
ook_active_device: int | None = None
ook_active_sdr_type: str | None = None

# Parser thread state (avoids monkey-patching subprocess.Popen)
_ook_stop_event: threading.Event | None = None
_ook_parser_thread: threading.Thread | None = None

# Supported modulation schemes → rtl_433 flex decoder modulation string
_MODULATION_MAP = {
    "pwm": "OOK_PWM",
    "ppm": "OOK_PPM",
    "manchester": "OOK_MC_ZEROBIT",
}


def _validate_encoding(value: Any) -> str:
    enc = str(value).lower().strip()
    if enc not in _MODULATION_MAP:
        raise ValueError(f"encoding must be one of: {', '.join(_MODULATION_MAP)}")
    return enc


@ook_bp.route("/ook/start", methods=["POST"])
def start_ook() -> Response:
    global ook_active_device, ook_active_sdr_type, _ook_stop_event, _ook_parser_thread

    with app_module.ook_lock:
        if app_module.ook_process:
            # If the process exited/crashed, clean up stale state and allow restart
            if app_module.ook_process.poll() is not None:
                cleanup_ook(emit_status=False)
            else:
                return api_error("OOK decoder already running", 409)

        data = request.json or {}
        data = apply_device_defaults(data)

        try:
            freq = validate_frequency(data.get("frequency", "433.920"))
            gain = validate_gain(data.get("gain", "0"))
            ppm = validate_ppm(data.get("ppm", "0"))
            device = validate_device_index(data.get("device", "0"))
        except ValueError as e:
            return api_error(str(e), 400)

        try:
            encoding = _validate_encoding(data.get("encoding", "pwm"))
        except ValueError as e:
            return api_error(str(e), 400)

        # OOK flex decoder timing parameters (server-side range validation)
        try:
            short_pulse = validate_positive_int(data.get("short_pulse", 300), "short_pulse", max_val=100000)
            long_pulse = validate_positive_int(data.get("long_pulse", 600), "long_pulse", max_val=100000)
            reset_limit = validate_positive_int(data.get("reset_limit", 8000), "reset_limit", max_val=1000000)
            gap_limit = validate_positive_int(data.get("gap_limit", 5000), "gap_limit", max_val=1000000)
            tolerance = validate_positive_int(data.get("tolerance", 150), "tolerance", max_val=50000)
            min_bits = validate_positive_int(data.get("min_bits", 8), "min_bits", max_val=4096)
        except ValueError as e:
            return api_error(f"Invalid timing parameter: {e}", 400)
        if min_bits < 1:
            return api_error("min_bits must be >= 1", 400)
        if short_pulse < 1 or long_pulse < 1:
            return api_error("Pulse widths must be >= 1", 400)
        deduplicate = bool(data.get("deduplicate", False))

        # Parse SDR type early — needed for device claim
        sdr_type_str = data.get("sdr_type", "rtlsdr")
        try:
            sdr_type = SDRType(sdr_type_str)
        except ValueError:
            sdr_type = SDRType.RTL_SDR
            sdr_type_str = "rtlsdr"

        rtl_tcp_host = data.get("rtl_tcp_host") or None
        rtl_tcp_port = data.get("rtl_tcp_port", 1234)

        if not rtl_tcp_host:
            device_int = int(device)
            error = app_module.claim_sdr_device(device_int, "ook", sdr_type_str)
            if error:
                return api_error(error, 409, error_type="DEVICE_BUSY")
            ook_active_device = device_int
            ook_active_sdr_type = sdr_type_str

        while not app_module.ook_queue.empty():
            try:
                app_module.ook_queue.get_nowait()
            except queue.Empty:
                break

        if rtl_tcp_host:
            try:
                rtl_tcp_host = validate_rtl_tcp_host(rtl_tcp_host)
                rtl_tcp_port = validate_rtl_tcp_port(rtl_tcp_port)
            except ValueError as e:
                return api_error(str(e), 400)
            sdr_device = SDRFactory.create_network_device(rtl_tcp_host, rtl_tcp_port)
            logger.info(f"Using remote SDR: rtl_tcp://{rtl_tcp_host}:{rtl_tcp_port}")
        else:
            sdr_device = SDRFactory.create_default_device(sdr_type, index=device)

        builder = SDRFactory.get_builder(sdr_device.sdr_type)
        bias_t = data.get("bias_t", False)

        # Build base ISM command then replace protocol flags with flex decoder
        cmd = builder.build_ism_command(
            device=sdr_device,
            frequency_mhz=freq,
            gain=float(gain) if gain and gain != 0 else None,
            ppm=int(ppm) if ppm and ppm != 0 else None,
            bias_t=bias_t,
        )

        modulation = _MODULATION_MAP[encoding]
        flex_spec = (
            f"n=ook,m={modulation},"
            f"s={short_pulse},l={long_pulse},"
            f"r={reset_limit},g={gap_limit},"
            f"t={tolerance},bits>={min_bits}"
        )

        # Strip any existing -R flags from the base command
        filtered_cmd: list[str] = []
        skip_next = False
        for arg in cmd:
            if skip_next:
                skip_next = False
                continue
            if arg == "-R":
                skip_next = True
                continue
            filtered_cmd.append(arg)

        filtered_cmd.extend(["-M", "level", "-R", "0", "-X", flex_spec])

        full_cmd = " ".join(filtered_cmd)
        logger.info(f"OOK decoder running: {full_cmd}")

        try:
            rtl_process = subprocess.Popen(
                filtered_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            register_process(rtl_process)

            _stderr_noise = ("bitbuffer_add_bit", "row count limit")

            def monitor_stderr() -> None:
                for line in rtl_process.stderr:
                    err_text = line.decode("utf-8", errors="replace").strip()
                    if err_text and not any(n in err_text for n in _stderr_noise):
                        logger.debug(f"[rtl_433/ook] {err_text}")

            stderr_thread = threading.Thread(target=monitor_stderr)
            stderr_thread.daemon = True
            stderr_thread.start()

            stop_event = threading.Event()
            parser_thread = threading.Thread(
                target=ook_parser_thread,
                args=(
                    rtl_process.stdout,
                    app_module.ook_queue,
                    stop_event,
                    encoding,
                    deduplicate,
                ),
            )
            parser_thread.daemon = True
            parser_thread.start()

            app_module.ook_process = rtl_process
            _ook_stop_event = stop_event
            _ook_parser_thread = parser_thread

            try:
                app_module.ook_queue.put_nowait({"type": "status", "text": "started"})
            except queue.Full:
                logger.warning("OOK 'started' status dropped — queue full")

            return jsonify(
                {
                    "status": "started",
                    "command": full_cmd,
                    "encoding": encoding,
                    "modulation": modulation,
                    "flex_spec": flex_spec,
                    "deduplicate": deduplicate,
                }
            )

        except FileNotFoundError as e:
            if ook_active_device is not None:
                app_module.release_sdr_device(ook_active_device, ook_active_sdr_type or "rtlsdr")
                ook_active_device = None
                ook_active_sdr_type = None
            return api_error(f"Tool not found: {e.filename}", 400)

        except Exception as e:
            try:
                rtl_process.terminate()
                rtl_process.wait(timeout=2)
            except Exception:
                with contextlib.suppress(Exception):
                    rtl_process.kill()
            unregister_process(rtl_process)
            if ook_active_device is not None:
                app_module.release_sdr_device(ook_active_device, ook_active_sdr_type or "rtlsdr")
                ook_active_device = None
                ook_active_sdr_type = None
            return api_error(str(e), 500)


def _close_pipe(pipe_obj) -> None:
    """Close a subprocess pipe, suppressing errors."""
    if pipe_obj is not None:
        with contextlib.suppress(Exception):
            pipe_obj.close()


def cleanup_ook(*, emit_status: bool = True) -> None:
    """Full OOK cleanup: stop parser, terminate process, release SDR device.

    Safe to call from ``stop_ook()`` and ``kill_all()``.  Caller must hold
    ``app_module.ook_lock``.
    """
    global ook_active_device, ook_active_sdr_type, _ook_stop_event, _ook_parser_thread

    proc = app_module.ook_process
    if not proc:
        return

    # Signal parser thread to stop
    if _ook_stop_event:
        _ook_stop_event.set()

    # Kill the entire process group so child processes are cleaned up.
    # This is what unblocks the parser's readline(): the pipe reaches EOF.
    # Closing the pipes first does not; close() waits on the blocked reader.
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            proc.wait(timeout=3)
    except (ProcessLookupError, OSError):
        # Process already dead — fall back to normal terminate
        safe_terminate(proc)
    _close_pipe(getattr(proc, "stdout", None))
    _close_pipe(getattr(proc, "stderr", None))
    unregister_process(proc)
    app_module.ook_process = None

    # Join parser thread with timeout
    if _ook_parser_thread:
        _ook_parser_thread.join(timeout=0.5)

    _ook_stop_event = None
    _ook_parser_thread = None

    if ook_active_device is not None:
        app_module.release_sdr_device(ook_active_device, ook_active_sdr_type or "rtlsdr")
        ook_active_device = None
        ook_active_sdr_type = None

    if emit_status:
        try:
            app_module.ook_queue.put_nowait({"type": "status", "text": "stopped"})
        except queue.Full:
            logger.warning("OOK 'stopped' status dropped — queue full")


@ook_bp.route("/ook/stop", methods=["POST"])
def stop_ook() -> Response:
    with app_module.ook_lock:
        if app_module.ook_process:
            cleanup_ook()
            return jsonify({"status": "stopped"})

        return jsonify({"status": "not_running"})


@ook_bp.route("/ook/status")
def ook_status() -> Response:
    with app_module.ook_lock:
        running = app_module.ook_process is not None and app_module.ook_process.poll() is None
        return jsonify({"running": running})


@ook_bp.route("/ook/stream")
def ook_stream() -> Response:
    response = Response(
        sse_stream_fanout(
            source_queue=app_module.ook_queue,
            channel_key="ook",
            timeout=1.0,
            keepalive_interval=30.0,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response
