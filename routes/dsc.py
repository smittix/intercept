"""VHF DSC (Digital Selective Calling) routes.

DSC operates on VHF Channel 70 (156.525 MHz) for maritime
distress and safety communications per ITU-R M.493.
"""

from __future__ import annotations

import contextlib
import logging
import os
import pty
import queue
import select
import shutil
import subprocess
import threading
import time
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.constants import (
    DSC_SAMPLE_RATE,
    DSC_TERMINATE_TIMEOUT,
    DSC_VHF_FREQUENCY_MHZ,
)
from utils.database import (
    acknowledge_dsc_alert,
    get_dsc_alert,
    get_dsc_alert_summary,
    get_dsc_alerts,
    store_dsc_alert,
)
from utils.dependencies import get_tool_path
from utils.dsc.parser import parse_dsc_message
from utils.event_pipeline import process_event
from utils.process import register_process, unregister_process
from utils.responses import api_error
from utils.sdr import SDRFactory, SDRType
from utils.sdr.device_config import apply_device_defaults
from utils.sse import sse_stream_fanout
from utils.validation import (
    validate_device_index,
    validate_gain,
    validate_ppm,
    validate_rtl_tcp_host,
    validate_rtl_tcp_port,
)

logger = logging.getLogger("intercept.dsc")

dsc_bp = Blueprint("dsc", __name__, url_prefix="/dsc")

# Module state (track if running independent of process state)
dsc_running = False

# Track which device is being used
dsc_active_device: int | None = None
dsc_active_sdr_type: str | None = None


def _get_dsc_decoder_path() -> str | None:
    """Get path to DSC decoder."""
    # Check for our custom decoder
    project_bin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "dsc-decoder")
    if os.path.isfile(project_bin) and os.access(project_bin, os.X_OK):
        return project_bin

    # Check system PATH
    system_decoder = shutil.which("dsc-decoder")
    if system_decoder:
        return system_decoder

    return None


def _check_dsc_tools() -> dict:
    """Check availability of DSC decoding tools."""
    rtl_fm_path = get_tool_path("rtl_fm")
    decoder_path = _get_dsc_decoder_path()

    # Check for scipy/numpy (needed for decoder)
    scipy_available = False
    try:
        import numpy
        import scipy

        scipy_available = True
    except ImportError:
        pass

    return {
        "rtl_fm": {"available": rtl_fm_path is not None, "path": rtl_fm_path},
        "dsc_decoder": {"available": decoder_path is not None, "path": decoder_path},
        "scipy": {"available": scipy_available, "note": "Required for DSC signal processing"},
        "ready": rtl_fm_path is not None and decoder_path is not None and scipy_available,
    }


def stream_dsc_decoder(master_fd: int, decoder_process: subprocess.Popen) -> None:
    """
    Stream DSC decoder output to queue using PTY for unbuffered output.

    Args:
        master_fd: PTY master file descriptor
        decoder_process: Decoder subprocess
    """
    global dsc_running

    try:
        app_module.dsc_queue.put({"type": "status", "status": "started"})

        buffer = ""
        while dsc_running:
            try:
                ready, _, _ = select.select([master_fd], [], [], 1.0)
            except Exception:
                break

            if ready:
                try:
                    data = os.read(master_fd, 1024)
                    if not data:
                        break
                    buffer += data.decode("utf-8", errors="replace")

                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue

                        # Parse DSC message
                        parsed = parse_dsc_message(line)
                        if parsed:
                            # Generate unique message ID
                            msg_id = f"{parsed['source_mmsi']}_{int(time.time() * 1000)}"
                            parsed["id"] = msg_id

                            # Store in transient DataStore
                            app_module.dsc_messages.set(msg_id, parsed)

                            # Queue for SSE
                            try:
                                app_module.dsc_queue.put_nowait(parsed)
                            except queue.Full:
                                logger.warning("DSC queue full, dropping message")

                            # Store critical alerts permanently
                            if parsed.get("is_critical"):
                                _store_critical_alert(parsed)
                        else:
                            # Raw output for debugging
                            app_module.dsc_queue.put({"type": "raw", "text": line})
                except OSError:
                    break

            # Check if process is still running
            if decoder_process.poll() is not None:
                break

    except Exception as e:
        logger.error(f"DSC decoder error: {e}")
        app_module.dsc_queue.put({"type": "error", "error": str(e)})
    finally:
        global dsc_active_device, dsc_active_sdr_type
        with contextlib.suppress(OSError):
            os.close(master_fd)
        dsc_running = False
        # Cleanup both processes
        with app_module.dsc_lock:
            rtl_proc = app_module.dsc_rtl_process
        for proc in [rtl_proc, decoder_process]:
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    with contextlib.suppress(Exception):
                        proc.kill()
                unregister_process(proc)
        app_module.dsc_queue.put({"type": "status", "status": "stopped"})
        with app_module.dsc_lock:
            app_module.dsc_process = None
            app_module.dsc_rtl_process = None
        # Release SDR device
        if dsc_active_device is not None:
            app_module.release_sdr_device(dsc_active_device, dsc_active_sdr_type or "rtlsdr")
            dsc_active_device = None
            dsc_active_sdr_type = None


def _store_critical_alert(msg: dict) -> None:
    """Store critical DSC alert (DISTRESS/URGENCY) to database."""
    try:
        store_dsc_alert(
            source_mmsi=msg.get("source_mmsi", ""),
            format_code=str(msg.get("format_code", "")),
            category=msg.get("category", "UNKNOWN"),
            source_name=msg.get("source_name"),
            dest_mmsi=msg.get("dest_mmsi"),
            nature_of_distress=msg.get("nature_of_distress"),
            latitude=msg.get("latitude"),
            longitude=msg.get("longitude"),
            raw_message=msg.get("raw_message"),
        )
        logger.info(f"Stored {msg.get('category')} alert from {msg.get('source_mmsi')}")
    except Exception as e:
        logger.error(f"Failed to store DSC alert: {e}")


def monitor_rtl_stderr(process: subprocess.Popen) -> None:
    """Monitor rtl_fm stderr for errors."""
    global dsc_running

    try:
        for line in process.stderr:
            if not dsc_running:
                break
            err_text = line.decode("utf-8", errors="replace").strip()
            if err_text:
                logger.debug(f"[RTL_FM] {err_text}")

                # Check for device busy error
                if "usb_claim_interface" in err_text.lower():
                    app_module.dsc_queue.put(
                        {
                            "type": "error",
                            "error": "SDR device busy",
                            "error_type": "DEVICE_BUSY",
                            "suggestion": "Use a different SDR device or stop other SDR processes",
                        }
                    )

                # Check for other common errors
                if "no supported devices" in err_text.lower():
                    app_module.dsc_queue.put(
                        {"type": "error", "error": "No SDR device found", "error_type": "NO_DEVICE"}
                    )
    except Exception:
        pass


@dsc_bp.route("/status")
def get_status() -> Response:
    """Get DSC decoder status."""
    global dsc_running

    with app_module.dsc_lock:
        running = dsc_running and app_module.dsc_process is not None and app_module.dsc_process.poll() is None

    # Get message counts
    message_count = len(app_module.dsc_messages)
    alert_summary = get_dsc_alert_summary()

    return jsonify(
        {
            "running": running,
            "frequency": DSC_VHF_FREQUENCY_MHZ,
            "message_count": message_count,
            "alerts": alert_summary,
        }
    )


@dsc_bp.route("/tools")
def check_tools() -> Response:
    """Check DSC decoder tool availability."""
    tools = _check_dsc_tools()
    return jsonify(tools)


@dsc_bp.route("/start", methods=["POST"])
def start_decoding() -> Response:
    """Start DSC decoder."""
    global dsc_running

    with app_module.dsc_lock:
        if app_module.dsc_process and app_module.dsc_process.poll() is None:
            return jsonify({"status": "error", "message": "DSC decoder already running"}), 409

        # Check tools
        tools = _check_dsc_tools()
        if not tools["ready"]:
            missing = []
            if not tools["rtl_fm"]["available"]:
                missing.append("rtl_fm")
            if not tools["dsc_decoder"]["available"]:
                missing.append("dsc-decoder")
            if not tools["scipy"]["available"]:
                missing.append("scipy/numpy")

            return jsonify({"status": "error", "message": f"Missing required tools: {', '.join(missing)}"}), 400

        data = request.json or {}
        data = apply_device_defaults(data)

        # Validate device
        try:
            device = validate_device_index(data.get("device", "0"))
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400

        # Validate gain and PPM
        try:
            gain = validate_gain(data.get("gain", "40"))
            ppm = validate_ppm(data.get("ppm", "0"))
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400

        # Get SDR type from request
        sdr_type_str = data.get("sdr_type", "rtlsdr")

        # Check for rtl_tcp (remote SDR) connection
        rtl_tcp_host = data.get("rtl_tcp_host")
        rtl_tcp_port = data.get("rtl_tcp_port", 1234)

        try:
            sdr_type = SDRType(sdr_type_str)
        except ValueError:
            sdr_type = SDRType.RTL_SDR

        # Check if device is available using centralized registry (skip for remote rtl_tcp)
        global dsc_active_device, dsc_active_sdr_type
        if not rtl_tcp_host:
            device_int = int(device)
            error = app_module.claim_sdr_device(device_int, "dsc", sdr_type_str)
            if error:
                return jsonify({"status": "error", "error_type": "DEVICE_BUSY", "message": error}), 409

            dsc_active_device = device_int
            dsc_active_sdr_type = sdr_type_str

        # Clear queue
        while not app_module.dsc_queue.empty():
            try:
                app_module.dsc_queue.get_nowait()
            except queue.Empty:
                break

        # Build rtl_fm command via SDR abstraction layer
        decoder_path = tools["dsc_decoder"]["path"]

        if rtl_tcp_host:
            try:
                rtl_tcp_host = validate_rtl_tcp_host(rtl_tcp_host)
                rtl_tcp_port = validate_rtl_tcp_port(rtl_tcp_port)
            except ValueError as e:
                return api_error(str(e), 400)
            sdr_device = SDRFactory.create_network_device(rtl_tcp_host, rtl_tcp_port)
            logger.info(f"Using remote SDR: rtl_tcp://{rtl_tcp_host}:{rtl_tcp_port}")
        else:
            sdr_device = SDRFactory.create_default_device(sdr_type, index=int(device))

        builder = SDRFactory.get_builder(sdr_device.sdr_type)
        rtl_cmd = list(
            builder.build_fm_demod_command(
                device=sdr_device,
                frequency_mhz=DSC_VHF_FREQUENCY_MHZ,
                sample_rate=DSC_SAMPLE_RATE,
                gain=float(gain) if gain and str(gain) != "0" else None,
                ppm=ppm or None,
                modulation="fm",
                squelch=0,
            )
        )
        # Ensure trailing '-' for stdin piping and add DC blocking filter
        if rtl_cmd and rtl_cmd[-1] == "-":
            rtl_cmd = rtl_cmd[:-1] + ["-E", "dc", "-"]

        # Decoder command
        decoder_cmd = [decoder_path]

        full_cmd = " ".join(rtl_cmd) + " | " + " ".join(decoder_cmd)
        logger.info(f"Starting DSC decoder: {full_cmd}")

        try:
            # Start rtl_fm subprocess
            rtl_process = subprocess.Popen(rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            register_process(rtl_process)

            # Start stderr monitor thread
            stderr_thread = threading.Thread(target=monitor_rtl_stderr, args=(rtl_process,), daemon=True)
            stderr_thread.start()

            # Create PTY for decoder output
            master_fd, slave_fd = pty.openpty()

            # Start decoder subprocess
            decoder_process = subprocess.Popen(
                decoder_cmd, stdin=rtl_process.stdout, stdout=slave_fd, stderr=slave_fd, close_fds=True
            )
            register_process(decoder_process)

            os.close(slave_fd)
            rtl_process.stdout.close()

            # Store process references
            app_module.dsc_process = decoder_process
            app_module.dsc_rtl_process = rtl_process
            dsc_running = True

            # Start output streaming thread
            output_thread = threading.Thread(target=stream_dsc_decoder, args=(master_fd, decoder_process), daemon=True)
            output_thread.start()

            return jsonify(
                {
                    "status": "started",
                    "frequency": DSC_VHF_FREQUENCY_MHZ,
                    "device": device,
                    "gain": gain,
                    "command": full_cmd,
                }
            )

        except FileNotFoundError as e:
            # Kill orphaned rtl_fm process
            try:
                rtl_process.terminate()
                rtl_process.wait(timeout=2)
            except Exception:
                with contextlib.suppress(Exception):
                    rtl_process.kill()
            # Release device on failure
            if dsc_active_device is not None:
                app_module.release_sdr_device(dsc_active_device, dsc_active_sdr_type or "rtlsdr")
                dsc_active_device = None
                dsc_active_sdr_type = None
            return jsonify({"status": "error", "message": f"Tool not found: {e.filename}"}), 400
        except Exception as e:
            # Kill orphaned rtl_fm process if it was started
            try:
                rtl_process.terminate()
                rtl_process.wait(timeout=2)
            except Exception:
                with contextlib.suppress(Exception):
                    rtl_process.kill()
            # Release device on failure
            if dsc_active_device is not None:
                app_module.release_sdr_device(dsc_active_device, dsc_active_sdr_type or "rtlsdr")
                dsc_active_device = None
                dsc_active_sdr_type = None
            logger.error(f"Failed to start DSC decoder: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500


@dsc_bp.route("/stop", methods=["POST"])
def stop_decoding() -> Response:
    """Stop DSC decoder."""
    global dsc_running, dsc_active_device, dsc_active_sdr_type

    with app_module.dsc_lock:
        if not app_module.dsc_process:
            return jsonify({"status": "not_running"})

        dsc_running = False

        # Terminate rtl_fm process first
        if app_module.dsc_rtl_process:
            try:
                app_module.dsc_rtl_process.terminate()
                app_module.dsc_rtl_process.wait(timeout=DSC_TERMINATE_TIMEOUT)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    app_module.dsc_rtl_process.kill()
            except OSError:
                pass

        # Terminate decoder process
        if app_module.dsc_process:
            try:
                app_module.dsc_process.terminate()
                app_module.dsc_process.wait(timeout=DSC_TERMINATE_TIMEOUT)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    app_module.dsc_process.kill()
            except OSError:
                pass

        app_module.dsc_process = None
        app_module.dsc_rtl_process = None

        # Release device from registry
        if dsc_active_device is not None:
            app_module.release_sdr_device(dsc_active_device, dsc_active_sdr_type or "rtlsdr")
            dsc_active_device = None
            dsc_active_sdr_type = None

        return jsonify({"status": "stopped"})


@dsc_bp.route("/stream")
def stream() -> Response:
    """SSE stream for real-time DSC messages."""

    def _on_msg(msg: dict[str, Any]) -> None:
        process_event("dsc", msg, msg.get("type"))

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.dsc_queue,
            channel_key="dsc",
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


@dsc_bp.route("/messages")
def get_messages() -> Response:
    """Get current DSC messages from transient store."""
    messages = list(app_module.dsc_messages.values())

    # Sort by timestamp (newest first)
    messages.sort(key=lambda m: m.get("timestamp", ""), reverse=True)

    return jsonify({"count": len(messages), "messages": messages})


@dsc_bp.route("/alerts")
def get_alerts_endpoint() -> Response:
    """Get stored DSC alerts (paginated)."""
    # Parse query params
    category = request.args.get("category")
    acknowledged = request.args.get("acknowledged")
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    # Convert acknowledged param
    ack_filter = None
    if acknowledged is not None:
        ack_filter = acknowledged.lower() in ("true", "1", "yes")

    alerts = get_dsc_alerts(category=category, acknowledged=ack_filter, limit=limit, offset=offset)

    summary = get_dsc_alert_summary()

    return jsonify(
        {"alerts": alerts, "count": len(alerts), "summary": summary, "pagination": {"limit": limit, "offset": offset}}
    )


@dsc_bp.route("/alerts/<int:alert_id>")
def get_alert(alert_id: int) -> Response:
    """Get a specific DSC alert by ID."""
    alert = get_dsc_alert(alert_id)
    if not alert:
        return jsonify({"status": "error", "message": "Alert not found"}), 404

    return jsonify(alert)


@dsc_bp.route("/alerts/<int:alert_id>/acknowledge", methods=["POST"])
def acknowledge_alert(alert_id: int) -> Response:
    """Acknowledge a DSC alert."""
    data = request.json or {}
    notes = data.get("notes")

    success = acknowledge_dsc_alert(alert_id, notes)
    if not success:
        return jsonify({"status": "error", "message": "Alert not found"}), 404

    return jsonify({"status": "acknowledged", "alert_id": alert_id})


@dsc_bp.route("/alerts/summary")
def get_alerts_summary() -> Response:
    """Get summary of unacknowledged DSC alerts."""
    summary = get_dsc_alert_summary()
    return jsonify(summary)
