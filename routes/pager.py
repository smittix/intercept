"""Pager decoding routes (POCSAG/FLEX)."""

from __future__ import annotations

import os
import pathlib
import re
import pty
import queue
import select
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Generator

from flask import Blueprint, jsonify, request, Response

import app as app_module
from utils.logging import pager_logger as logger
from utils.validation import (
    validate_frequency, validate_device_index, validate_gain, validate_ppm,
    validate_rtl_tcp_host, validate_rtl_tcp_port
)
from utils.sse import format_sse
from utils.process import safe_terminate, register_process
from utils.sdr import SDRFactory, SDRType, SDRValidationError

pager_bp = Blueprint("pager", __name__)


def parse_multimon_output(line: str) -> dict[str, str] | None:
    """Parse multimon-ng output line."""
    line = line.strip()

    # POCSAG parsing - with message content
    pocsag_match = re.match(r"(POCSAG\d+):\s*Address:\s*(\d+)\s+Function:\s*(\d+)\s+(Alpha|Numeric):\s*(.*)", line)
    if pocsag_match:
        return {
            "protocol": pocsag_match.group(1),
            "address": pocsag_match.group(2),
            "function": pocsag_match.group(3),
            "msg_type": pocsag_match.group(4),
            "message": pocsag_match.group(5).strip() or "[No Message]",
        }

    # POCSAG parsing - address only (no message content)
    pocsag_addr_match = re.match(r"(POCSAG\d+):\s*Address:\s*(\d+)\s+Function:\s*(\d+)\s*$", line)
    if pocsag_addr_match:
        return {
            "protocol": pocsag_addr_match.group(1),
            "address": pocsag_addr_match.group(2),
            "function": pocsag_addr_match.group(3),
            "msg_type": "Tone",
            "message": "[Tone Only]",
        }

    # FLEX parsing (standard format)
    flex_match = re.match(
        r"FLEX[:\|]\s*[\d\-]+[\s\|]+[\d:]+[\s\|]+([\d/A-Z]+)[\s\|]+([\d.]+)[\s\|]+\[?(\d+)\]?[\s\|]+(\w+)[\s\|]+(.*)",
        line,
    )
    if flex_match:
        return {
            "protocol": "FLEX",
            "address": flex_match.group(3),
            "function": flex_match.group(1),
            "msg_type": flex_match.group(4),
            "message": flex_match.group(5).strip() or "[No Message]",
        }

    # Simple FLEX format
    flex_simple = re.match(r"FLEX:\s*(.+)", line)
    if flex_simple:
        return {
            "protocol": "FLEX",
            "address": "Unknown",
            "function": "",
            "msg_type": "Unknown",
            "message": flex_simple.group(1).strip(),
        }

    return None


def log_message(msg: dict[str, Any]) -> None:
    """Log a message to file if logging is enabled."""
    if not app_module.logging_enabled:
        return
    try:
        with open(app_module.log_file_path, "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(
                f"{timestamp} | {msg.get('protocol', 'UNKNOWN')} | {msg.get('address', '')} | {msg.get('message', '')}\n"
            )
    except Exception as e:
        logger.error(f"Failed to log message: {e}")


def stream_decoder(master_fd: int, process: subprocess.Popen[bytes]) -> None:
    """Stream decoder output to queue using PTY for unbuffered output."""
    try:
        app_module.output_queue.put({"type": "status", "text": "started"})

        buffer = ""
        while True:
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

                        parsed = parse_multimon_output(line)
                        if parsed:
                            parsed["timestamp"] = datetime.now().strftime("%H:%M:%S")
                            app_module.output_queue.put({"type": "message", **parsed})
                            log_message(parsed)
                        else:
                            app_module.output_queue.put({"type": "raw", "text": line})
                except OSError:
                    break

            if process.poll() is not None:
                break

    except Exception as e:
        app_module.output_queue.put({"type": "error", "text": str(e)})
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        process.wait()
        app_module.output_queue.put({"type": "status", "text": "stopped"})
        with app_module.process_lock:
            app_module.current_process = None


@pager_bp.route("/start", methods=["POST"])
def start_decoding() -> Response:
    with app_module.process_lock:
        if app_module.current_process:
            return jsonify({"status": "error", "message": "Already running"}), 409

        data = request.json or {}

        # Validate inputs
        try:
            freq = validate_frequency(data.get("frequency", "929.6125"))
            gain = validate_gain(data.get("gain", "0"))
            ppm = validate_ppm(data.get("ppm", "0"))
            device = validate_device_index(data.get("device", "0"))
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400

        squelch = data.get("squelch", "0")
        try:
            squelch = int(squelch)
            if not 0 <= squelch <= 1000:
                raise ValueError("Squelch must be between 0 and 1000")
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "Invalid squelch value"}), 400

        # Validate protocols
        valid_protocols = ["POCSAG512", "POCSAG1200", "POCSAG2400", "FLEX"]
        protocols = data.get("protocols", valid_protocols)
        if not isinstance(protocols, list):
            return jsonify({"status": "error", "message": "Protocols must be a list"}), 400
        protocols = [p for p in protocols if p in valid_protocols]
        if not protocols:
            protocols = valid_protocols

        # Clear queue
        while not app_module.output_queue.empty():
            try:
                app_module.output_queue.get_nowait()
            except queue.Empty:
                break

        # Build multimon-ng decoder arguments
        decoders = []
        for proto in protocols:
            if proto == "POCSAG512":
                decoders.extend(["-a", "POCSAG512"])
            elif proto == "POCSAG1200":
                decoders.extend(["-a", "POCSAG1200"])
            elif proto == "POCSAG2400":
                decoders.extend(["-a", "POCSAG2400"])
            elif proto == "FLEX":
                decoders.extend(["-a", "FLEX"])

        # Get SDR type and build command via abstraction layer
        sdr_type_str = data.get("sdr_type", "rtlsdr")
        try:
            sdr_type = SDRType(sdr_type_str)
        except ValueError:
            sdr_type = SDRType.RTL_SDR

        # Check for rtl_tcp (remote SDR) connection
        rtl_tcp_host = data.get('rtl_tcp_host')
        rtl_tcp_port = data.get('rtl_tcp_port', 1234)

        if rtl_tcp_host:
            # Validate and create network device
            try:
                rtl_tcp_host = validate_rtl_tcp_host(rtl_tcp_host)
                rtl_tcp_port = validate_rtl_tcp_port(rtl_tcp_port)
            except ValueError as e:
                return jsonify({'status': 'error', 'message': str(e)}), 400

            sdr_device = SDRFactory.create_network_device(rtl_tcp_host, rtl_tcp_port)
            logger.info(f"Using remote SDR: rtl_tcp://{rtl_tcp_host}:{rtl_tcp_port}")
        else:
            # Create local device object
            sdr_device = SDRFactory.create_default_device(sdr_type, index=device)

        builder = SDRFactory.get_builder(sdr_device.sdr_type)

        # Build FM demodulation command
        rtl_cmd = builder.build_fm_demod_command(
            device=sdr_device,
            frequency_mhz=freq,
            sample_rate=22050,
            gain=float(gain) if gain and gain != "0" else None,
            ppm=int(ppm) if ppm and ppm != "0" else None,
            modulation="fm",
            squelch=squelch if squelch and squelch != 0 else None,
        )

        multimon_cmd = ["multimon-ng", "-t", "raw"] + decoders + ["-f", "alpha", "-"]

        full_cmd = " ".join(rtl_cmd) + " | " + " ".join(multimon_cmd)
        logger.info(f"Running: {full_cmd}")

        try:
            # Create pipe: rtl_fm | multimon-ng
            rtl_process = subprocess.Popen(rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Start a thread to monitor rtl_fm stderr for errors
            def monitor_rtl_stderr():
                for line in rtl_process.stderr:
                    err_text = line.decode("utf-8", errors="replace").strip()
                    if err_text:
                        logger.debug(f"[RTL_FM] {err_text}")
                        app_module.output_queue.put({"type": "raw", "text": f"[rtl_fm] {err_text}"})

            rtl_stderr_thread = threading.Thread(target=monitor_rtl_stderr)
            rtl_stderr_thread.daemon = True
            rtl_stderr_thread.start()

            # Create a pseudo-terminal for multimon-ng output
            master_fd, slave_fd = pty.openpty()

            multimon_process = subprocess.Popen(
                multimon_cmd, stdin=rtl_process.stdout, stdout=slave_fd, stderr=slave_fd, close_fds=True
            )

            os.close(slave_fd)
            rtl_process.stdout.close()

            app_module.current_process = multimon_process
            app_module.current_process._rtl_process = rtl_process
            app_module.current_process._master_fd = master_fd

            # Start output thread with PTY master fd
            thread = threading.Thread(target=stream_decoder, args=(master_fd, multimon_process))
            thread.daemon = True
            thread.start()

            app_module.output_queue.put({"type": "info", "text": f"Command: {full_cmd}"})

            return jsonify({"status": "started", "command": full_cmd})

        except FileNotFoundError as e:
            return jsonify({"status": "error", "message": f"Tool not found: {e.filename}"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})


@pager_bp.route("/stop", methods=["POST"])
def stop_decoding() -> Response:
    with app_module.process_lock:
        if app_module.current_process:
            # Kill rtl_fm process first
            if hasattr(app_module.current_process, "_rtl_process"):
                try:
                    app_module.current_process._rtl_process.terminate()
                    app_module.current_process._rtl_process.wait(timeout=2)
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        app_module.current_process._rtl_process.kill()
                    except OSError:
                        pass

            # Close PTY master fd
            if hasattr(app_module.current_process, "_master_fd"):
                try:
                    os.close(app_module.current_process._master_fd)
                except OSError:
                    pass

            # Kill multimon-ng
            app_module.current_process.terminate()
            try:
                app_module.current_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                app_module.current_process.kill()

            app_module.current_process = None
            return jsonify({"status": "stopped"})

        return jsonify({"status": "not_running"})


@pager_bp.route("/status")
def get_status() -> Response:
    """Check if decoder is currently running."""
    with app_module.process_lock:
        if app_module.current_process and app_module.current_process.poll() is None:
            return jsonify(
                {"running": True, "logging": app_module.logging_enabled, "log_file": app_module.log_file_path}
            )
        return jsonify({"running": False, "logging": app_module.logging_enabled, "log_file": app_module.log_file_path})


@pager_bp.route("/logging", methods=["POST"])
def toggle_logging() -> Response:
    """Toggle message logging."""
    data = request.json or {}
    if "enabled" in data:
        app_module.logging_enabled = bool(data["enabled"])

    if "log_file" in data and data["log_file"]:
        # Validate path to prevent directory traversal
        try:
            requested_path = pathlib.Path(data["log_file"]).resolve()
            # Only allow files in the current directory or logs subdirectory
            cwd = pathlib.Path(".").resolve()
            logs_dir = (cwd / "logs").resolve()

            # Check if path is within allowed directories
            is_in_cwd = str(requested_path).startswith(str(cwd))
            is_in_logs = str(requested_path).startswith(str(logs_dir))

            if not (is_in_cwd or is_in_logs):
                return jsonify({"status": "error", "message": "Invalid log file path"}), 400

            # Ensure it's not a directory
            if requested_path.is_dir():
                return jsonify({"status": "error", "message": "Log file path must be a file, not a directory"}), 400

            app_module.log_file_path = str(requested_path)
        except (ValueError, OSError) as e:
            logger.warning(f"Invalid log file path: {e}")
            return jsonify({"status": "error", "message": "Invalid log file path"}), 400

    return jsonify({"logging": app_module.logging_enabled, "log_file": app_module.log_file_path})


@pager_bp.route("/stream")
def stream() -> Response:
    import json

    def generate() -> Generator[str, None, None]:
        last_keepalive = time.time()
        keepalive_interval = 30.0  # Send keepalive every 30 seconds instead of 1 second

        while True:
            try:
                msg = app_module.output_queue.get(timeout=1)
                last_keepalive = time.time()
                yield format_sse(msg)
            except queue.Empty:
                now = time.time()
                if now - last_keepalive >= keepalive_interval:
                    yield format_sse({"type": "keepalive"})
                    last_keepalive = now

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response
