"""RTL_433 sensor monitoring routes."""

from __future__ import annotations

import contextlib
import json
import math
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
from utils.responses import api_error, api_success
from utils.sdr import SDRFactory, SDRType
from utils.sdr.device_config import apply_device_defaults
from utils.sse import sse_stream_fanout
from utils.validation import (
    validate_device_index,
    validate_frequency,
    validate_gain,
    validate_ppm,
    validate_rtl_tcp_host,
    validate_rtl_tcp_port,
    validate_sensor_units,
)

sensor_bp = Blueprint("sensor", __name__)

# Track which device is being used
sensor_active_device: int | None = None
sensor_active_sdr_type: str | None = None

# RSSI history per device (model_id -> list of (timestamp, rssi))
sensor_rssi_history: dict[str, list[tuple[float, float]]] = {}
_MAX_RSSI_HISTORY = 60


def _build_scope_waveform(rssi: float, snr: float, noise: float, points: int = 256) -> list[int]:
    """Synthesize a compact waveform from rtl_433 level metrics."""
    points = max(32, min(points, 512))

    # rssi is usually negative; stronger signals are closer to 0 dBm.
    rssi_norm = min(max(abs(rssi) / 40.0, 0.0), 1.0)
    snr_norm = min(max((snr + 5.0) / 35.0, 0.0), 1.0)
    noise_norm = min(max(abs(noise) / 40.0, 0.0), 1.0)

    amplitude = max(0.06, min(1.0, (0.6 * rssi_norm + 0.4 * snr_norm) - (0.22 * noise_norm)))
    cycles = 3.0 + (snr_norm * 8.0)
    harmonic = 0.25 + (0.35 * snr_norm)
    hiss = 0.08 + (0.18 * noise_norm)
    phase = (time.monotonic() * (1.4 + (snr_norm * 2.2))) % (2.0 * math.pi)

    waveform: list[int] = []
    for i in range(points):
        t = i / (points - 1)
        base = math.sin((2.0 * math.pi * cycles * t) + phase)
        overtone = math.sin((2.0 * math.pi * (cycles * 2.4) * t) + (phase * 0.7))
        noise_wobble = math.sin((2.0 * math.pi * (cycles * 7.0) * t) + (phase * 2.1))

        sample = amplitude * (base + (harmonic * overtone) + (hiss * noise_wobble))
        sample /= 1.0 + harmonic + hiss
        packed = int(round(max(-1.0, min(1.0, sample)) * 127.0))
        waveform.append(max(-127, min(127, packed)))

    return waveform


def stream_sensor_output(process: subprocess.Popen[bytes]) -> None:
    """Stream rtl_433 JSON output to queue."""
    try:
        app_module.sensor_queue.put({"type": "status", "text": "started"})

        for line in iter(process.stdout.readline, b""):
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                # rtl_433 outputs JSON objects, one per line
                data = json.loads(line)
                data["type"] = "sensor"
                app_module.sensor_queue.put(data)

                # Track RSSI history per device
                _model = data.get("model", "")
                _dev_id = data.get("id", "")
                _rssi_val = data.get("rssi")
                if _rssi_val is not None and _model:
                    _hist_key = f"{_model}_{_dev_id}"
                    hist = sensor_rssi_history.setdefault(_hist_key, [])
                    hist.append((time.time(), float(_rssi_val)))
                    if len(hist) > _MAX_RSSI_HISTORY:
                        del hist[: len(hist) - _MAX_RSSI_HISTORY]

                # Push scope event when signal level data is present
                rssi = data.get("rssi")
                snr = data.get("snr")
                noise = data.get("noise")
                if rssi is not None or snr is not None:
                    try:
                        rssi_value = float(rssi) if rssi is not None else 0.0
                        snr_value = float(snr) if snr is not None else 0.0
                        noise_value = float(noise) if noise is not None else 0.0
                        app_module.sensor_queue.put_nowait(
                            {
                                "type": "scope",
                                "rssi": rssi_value,
                                "snr": snr_value,
                                "noise": noise_value,
                                "waveform": _build_scope_waveform(
                                    rssi=rssi_value,
                                    snr=snr_value,
                                    noise=noise_value,
                                ),
                            }
                        )
                    except (TypeError, ValueError, queue.Full):
                        pass

                # Log if enabled
                if app_module.logging_enabled:
                    try:
                        with open(app_module.log_file_path, "a") as f:
                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            f.write(f"{timestamp} | {data.get('model', 'Unknown')} | {json.dumps(data)}\n")
                    except Exception:
                        pass
            except json.JSONDecodeError:
                # Not JSON, send as raw
                app_module.sensor_queue.put({"type": "raw", "text": line})

    except Exception as e:
        app_module.sensor_queue.put({"type": "error", "text": str(e)})
    finally:
        global sensor_active_device, sensor_active_sdr_type
        # Ensure process is terminated
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            with contextlib.suppress(Exception):
                process.kill()
        unregister_process(process)
        app_module.sensor_queue.put({"type": "status", "text": "stopped"})
        with app_module.sensor_lock:
            app_module.sensor_process = None
        # Release SDR device
        if sensor_active_device is not None:
            app_module.release_sdr_device(sensor_active_device, sensor_active_sdr_type or "rtlsdr")
            sensor_active_device = None
            sensor_active_sdr_type = None


@sensor_bp.route("/sensor/status")
def sensor_status() -> Response:
    """Check if sensor decoder is currently running."""
    with app_module.sensor_lock:
        running = app_module.sensor_process is not None and app_module.sensor_process.poll() is None
    return jsonify({"running": running})


@sensor_bp.route("/start_sensor", methods=["POST"])
def start_sensor() -> Response:
    global sensor_active_device, sensor_active_sdr_type

    with app_module.sensor_lock:
        if app_module.sensor_process:
            return api_error("Sensor already running", 409)

        data = request.json or {}
        data = apply_device_defaults(data)

        # Validate inputs
        try:
            freq = validate_frequency(data.get("frequency", "433.92"))
            gain = validate_gain(data.get("gain", "0"))
            ppm = validate_ppm(data.get("ppm", "0"))
            device = validate_device_index(data.get("device", "0"))
            units = validate_sensor_units(data.get("units", "si"))
        except ValueError as e:
            return api_error(str(e), 400)

        # Check for rtl_tcp (remote SDR) connection
        rtl_tcp_host = data.get("rtl_tcp_host")
        rtl_tcp_port = data.get("rtl_tcp_port", 1234)

        # Get SDR type early so we can pass it to claim/release
        sdr_type_str = data.get("sdr_type", "rtlsdr")

        # Claim local device if not using remote rtl_tcp
        if not rtl_tcp_host:
            device_int = int(device)
            error = app_module.claim_sdr_device(device_int, "sensor", sdr_type_str)
            if error:
                return api_error(error, 409, error_type="DEVICE_BUSY")
            sensor_active_device = device_int
            sensor_active_sdr_type = sdr_type_str

        # Clear queue
        while not app_module.sensor_queue.empty():
            try:
                app_module.sensor_queue.get_nowait()
            except queue.Empty:
                break

        # Build command via SDR abstraction layer
        try:
            sdr_type = SDRType(sdr_type_str)
        except ValueError:
            sdr_type = SDRType.RTL_SDR

        if rtl_tcp_host:
            # Validate and create network device
            try:
                rtl_tcp_host = validate_rtl_tcp_host(rtl_tcp_host)
                rtl_tcp_port = validate_rtl_tcp_port(rtl_tcp_port)
            except ValueError as e:
                return api_error(str(e), 400)

            sdr_device = SDRFactory.create_network_device(rtl_tcp_host, rtl_tcp_port)
            logger.info(f"Using remote SDR: rtl_tcp://{rtl_tcp_host}:{rtl_tcp_port}")
        else:
            # Create local device object
            sdr_device = SDRFactory.create_default_device(sdr_type, index=device)

        builder = SDRFactory.get_builder(sdr_device.sdr_type)

        # Build ISM band decoder command
        bias_t = data.get("bias_t", False)
        cmd = builder.build_ism_command(
            device=sdr_device,
            frequency_mhz=freq,
            gain=float(gain) if gain and gain != 0 else None,
            ppm=int(ppm) if ppm and ppm != 0 else None,
            bias_t=bias_t,
        )

        full_cmd = " ".join(cmd)
        logger.info(f"Running: {full_cmd}")

        # Add signal level metadata so the frontend scope can display RSSI/SNR
        # Disable stats reporting to suppress "row count limit 50 reached" warnings
        cmd.extend(["-M", "level", "-M", "stats:0"])

        # Unit conversion: let rtl_433 normalise units rather than converting
        # in the frontend, so logged and displayed values always agree.
        cmd.extend(["-C", units])

        try:
            app_module.sensor_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            register_process(app_module.sensor_process)

            # Start output thread
            thread = threading.Thread(target=stream_sensor_output, args=(app_module.sensor_process,))
            thread.daemon = True
            thread.start()

            # Monitor stderr
            # Filter noisy rtl_433 diagnostics that aren't useful to display
            _stderr_noise = (
                "bitbuffer_add_bit",
                "row count limit",
            )

            def monitor_stderr():
                for line in app_module.sensor_process.stderr:
                    err = line.decode("utf-8", errors="replace").strip()
                    if err and not any(noise in err for noise in _stderr_noise):
                        logger.debug(f"[rtl_433] {err}")
                        app_module.sensor_queue.put({"type": "info", "text": f"[rtl_433] {err}"})

            stderr_thread = threading.Thread(target=monitor_stderr)
            stderr_thread.daemon = True
            stderr_thread.start()

            app_module.sensor_queue.put({"type": "info", "text": f"Command: {full_cmd}"})

            return jsonify({"status": "started", "command": full_cmd})

        except FileNotFoundError:
            # Release device on failure
            if sensor_active_device is not None:
                app_module.release_sdr_device(sensor_active_device, sensor_active_sdr_type or "rtlsdr")
                sensor_active_device = None
                sensor_active_sdr_type = None
            return api_error(f"rtl_433 not found. {install_hint('rtl_433')}")
        except Exception as e:
            # Release device on failure
            if sensor_active_device is not None:
                app_module.release_sdr_device(sensor_active_device, sensor_active_sdr_type or "rtlsdr")
                sensor_active_device = None
                sensor_active_sdr_type = None
            return api_error(str(e))


@sensor_bp.route("/stop_sensor", methods=["POST"])
def stop_sensor() -> Response:
    global sensor_active_device, sensor_active_sdr_type

    with app_module.sensor_lock:
        if app_module.sensor_process:
            app_module.sensor_process.terminate()
            try:
                app_module.sensor_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                app_module.sensor_process.kill()
            app_module.sensor_process = None

            # Release device from registry
            if sensor_active_device is not None:
                app_module.release_sdr_device(sensor_active_device, sensor_active_sdr_type or "rtlsdr")
                sensor_active_device = None
                sensor_active_sdr_type = None

            return jsonify({"status": "stopped"})

        return jsonify({"status": "not_running"})


@sensor_bp.route("/stream_sensor")
def stream_sensor() -> Response:
    def _on_msg(msg: dict[str, Any]) -> None:
        process_event("sensor", msg, msg.get("type"))

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.sensor_queue,
            channel_key="sensor",
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


@sensor_bp.route("/sensor/rssi_history")
def get_rssi_history() -> Response:
    """Return RSSI history for all tracked sensor devices."""
    result = {}
    for key, entries in sensor_rssi_history.items():
        result[key] = [{"t": round(t, 1), "rssi": rssi} for t, rssi in entries]
    return api_success(data={"devices": result})
