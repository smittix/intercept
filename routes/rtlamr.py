"""RTLAMR utility meter monitoring routes."""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import threading
import time
from datetime import datetime
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.dependencies import install_hint
from utils.event_pipeline import process_event
from utils.logging import sensor_logger as logger
from utils.process import register_process, unregister_process
from utils.responses import api_error
from utils.sdr.device_config import apply_device_defaults
from utils.sse import sse_stream_fanout
from utils.validation import validate_device_index, validate_frequency, validate_gain, validate_ppm

rtlamr_bp = Blueprint("rtlamr", __name__)

# Store rtl_tcp process separately
rtl_tcp_process = None
rtl_tcp_lock = threading.Lock()

# Track which device is being used
rtlamr_active_device: int | None = None
rtlamr_active_sdr_type: str = "rtlsdr"


def stream_rtlamr_output(process: subprocess.Popen[bytes]) -> None:
    """Stream rtlamr JSON output to queue."""
    try:
        app_module.rtlamr_queue.put({"type": "status", "text": "started"})

        for line in iter(process.stdout.readline, b""):
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                # rtlamr outputs JSON objects, one per line
                data = json.loads(line)
                data["type"] = "rtlamr"
                app_module.rtlamr_queue.put(data)

                # Log if enabled
                if app_module.logging_enabled:
                    try:
                        with open(app_module.log_file_path, "a") as f:
                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            f.write(f"{timestamp} | RTLAMR | {json.dumps(data)}\n")
                    except Exception:
                        pass
            except json.JSONDecodeError:
                # Not JSON, send as raw
                app_module.rtlamr_queue.put({"type": "raw", "text": line})

    except Exception as e:
        app_module.rtlamr_queue.put({"type": "error", "text": str(e)})
    finally:
        global rtl_tcp_process, rtlamr_active_device, rtlamr_active_sdr_type
        # Ensure rtlamr process is terminated
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            with contextlib.suppress(Exception):
                process.kill()
        unregister_process(process)
        # Kill companion rtl_tcp process
        with rtl_tcp_lock:
            if rtl_tcp_process:
                try:
                    rtl_tcp_process.terminate()
                    rtl_tcp_process.wait(timeout=2)
                except Exception:
                    with contextlib.suppress(Exception):
                        rtl_tcp_process.kill()
                unregister_process(rtl_tcp_process)
                rtl_tcp_process = None
        app_module.rtlamr_queue.put({"type": "status", "text": "stopped"})
        with app_module.rtlamr_lock:
            app_module.rtlamr_process = None
        # Release SDR device
        if rtlamr_active_device is not None:
            app_module.release_sdr_device(rtlamr_active_device, rtlamr_active_sdr_type)
            rtlamr_active_device = None


@rtlamr_bp.route("/start_rtlamr", methods=["POST"])
def start_rtlamr() -> Response:
    global rtl_tcp_process, rtlamr_active_device, rtlamr_active_sdr_type

    with app_module.rtlamr_lock:
        if app_module.rtlamr_process:
            return api_error("RTLAMR already running", 409)

        data = request.json or {}
        data = apply_device_defaults(data)
        sdr_type_str = data.get("sdr_type", "rtlsdr")

        if sdr_type_str != "rtlsdr":
            return api_error(
                f"{sdr_type_str.replace('_', ' ').title()} is not yet supported for this mode. Please use an RTL-SDR device.",
                400,
            )

        # Validate inputs
        try:
            freq = validate_frequency(data.get("frequency", "912.0"))
            gain = validate_gain(data.get("gain", "0"))
            ppm = validate_ppm(data.get("ppm", "0"))
            device = validate_device_index(data.get("device", "0"))
        except ValueError as e:
            return api_error(str(e), 400)

        # Check if device is available
        device_int = int(device)
        error = app_module.claim_sdr_device(device_int, "rtlamr", sdr_type_str)
        if error:
            return api_error(error, 409, error_type="DEVICE_BUSY")

        rtlamr_active_device = device_int
        rtlamr_active_sdr_type = sdr_type_str

        # Clear queue
        while not app_module.rtlamr_queue.empty():
            try:
                app_module.rtlamr_queue.get_nowait()
            except queue.Empty:
                break

        # Get message type (default to scm)
        msgtype = data.get("msgtype", "scm")
        output_format = data.get("format", "json")

        # Start rtl_tcp first
        rtl_tcp_just_started = False
        rtl_tcp_cmd_str = ""
        with rtl_tcp_lock:
            if not rtl_tcp_process:
                logger.info("Starting rtl_tcp server...")
                try:
                    rtl_tcp_cmd = ["rtl_tcp", "-a", "0.0.0.0"]

                    # Add device index if not 0
                    if device and device != "0":
                        rtl_tcp_cmd.extend(["-d", str(device)])

                    # Add gain if not auto
                    if gain and gain != "0":
                        rtl_tcp_cmd.extend(["-g", str(gain)])

                    # Add PPM correction if not 0 (-P; rtl_tcp's -p is the listen port)
                    if ppm and ppm != "0":
                        rtl_tcp_cmd.extend(["-P", str(ppm)])

                    rtl_tcp_process = subprocess.Popen(rtl_tcp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    register_process(rtl_tcp_process)
                    rtl_tcp_just_started = True
                    rtl_tcp_cmd_str = " ".join(rtl_tcp_cmd)
                except Exception as e:
                    logger.error(f"Failed to start rtl_tcp: {e}")
                    # Release SDR device on rtl_tcp failure
                    if rtlamr_active_device is not None:
                        app_module.release_sdr_device(rtlamr_active_device, rtlamr_active_sdr_type)
                        rtlamr_active_device = None
                    if isinstance(e, FileNotFoundError):
                        return api_error(f"rtl_tcp not found. {install_hint('rtl_tcp')}", 400)
                    return api_error(f"Failed to start rtl_tcp: {e}", 500)

        # Wait for rtl_tcp to start outside lock
        if rtl_tcp_just_started:
            time.sleep(3)
            logger.info(f"rtl_tcp started: {rtl_tcp_cmd_str}")
            app_module.rtlamr_queue.put({"type": "info", "text": f"rtl_tcp: {rtl_tcp_cmd_str}"})

        # Build rtlamr command
        cmd = [
            "rtlamr",
            "-server=127.0.0.1:1234",
            f"-msgtype={msgtype}",
            f"-format={output_format}",
            f"-centerfreq={int(float(freq) * 1e6)}",
        ]

        # Add filter options if provided
        filterid = data.get("filterid")
        if filterid:
            cmd.append(f"-filterid={filterid}")

        filtertype = data.get("filtertype")
        if filtertype:
            cmd.append(f"-filtertype={filtertype}")

        # Unique messages only
        if data.get("unique", True):
            cmd.append("-unique=true")

        full_cmd = " ".join(cmd)
        logger.info(f"Running: {full_cmd}")

        try:
            app_module.rtlamr_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            register_process(app_module.rtlamr_process)

            # Start output thread
            thread = threading.Thread(target=stream_rtlamr_output, args=(app_module.rtlamr_process,))
            thread.daemon = True
            thread.start()

            # Monitor stderr
            def monitor_stderr():
                for line in app_module.rtlamr_process.stderr:
                    err = line.decode("utf-8", errors="replace").strip()
                    if err:
                        logger.debug(f"[rtlamr] {err}")
                        app_module.rtlamr_queue.put({"type": "info", "text": f"[rtlamr] {err}"})

            stderr_thread = threading.Thread(target=monitor_stderr)
            stderr_thread.daemon = True
            stderr_thread.start()

            app_module.rtlamr_queue.put({"type": "info", "text": f"Command: {full_cmd}"})

            return jsonify({"status": "started", "command": full_cmd})

        except FileNotFoundError:
            # If rtlamr fails, clean up rtl_tcp and release device
            with rtl_tcp_lock:
                if rtl_tcp_process:
                    rtl_tcp_process.terminate()
                    rtl_tcp_process.wait(timeout=2)
                    rtl_tcp_process = None
            if rtlamr_active_device is not None:
                app_module.release_sdr_device(rtlamr_active_device, rtlamr_active_sdr_type)
                rtlamr_active_device = None
            return api_error("rtlamr not found. Install from https://github.com/bemasher/rtlamr")
        except Exception as e:
            # If rtlamr fails, clean up rtl_tcp and release device
            with rtl_tcp_lock:
                if rtl_tcp_process:
                    rtl_tcp_process.terminate()
                    rtl_tcp_process.wait(timeout=2)
                    rtl_tcp_process = None
            if rtlamr_active_device is not None:
                app_module.release_sdr_device(rtlamr_active_device, rtlamr_active_sdr_type)
                rtlamr_active_device = None
            return api_error(str(e))


@rtlamr_bp.route("/stop_rtlamr", methods=["POST"])
def stop_rtlamr() -> Response:
    global rtl_tcp_process, rtlamr_active_device, rtlamr_active_sdr_type

    # Grab process refs inside locks, clear state, then terminate outside
    rtlamr_proc = None
    with app_module.rtlamr_lock:
        if app_module.rtlamr_process:
            rtlamr_proc = app_module.rtlamr_process
            app_module.rtlamr_process = None

    if rtlamr_proc:
        rtlamr_proc.terminate()
        try:
            rtlamr_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            rtlamr_proc.kill()

    # Also stop rtl_tcp
    tcp_proc = None
    with rtl_tcp_lock:
        if rtl_tcp_process:
            tcp_proc = rtl_tcp_process
            rtl_tcp_process = None

    if tcp_proc:
        tcp_proc.terminate()
        try:
            tcp_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            tcp_proc.kill()
        logger.info("rtl_tcp stopped")

    # Release device from registry
    if rtlamr_active_device is not None:
        app_module.release_sdr_device(rtlamr_active_device, rtlamr_active_sdr_type)
        rtlamr_active_device = None

    return jsonify({"status": "stopped"})


@rtlamr_bp.route("/stream_rtlamr")
def stream_rtlamr() -> Response:
    def _on_msg(msg: dict[str, Any]) -> None:
        process_event("rtlamr", msg, msg.get("type"))

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.rtlamr_queue,
            channel_key="rtlamr",
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
