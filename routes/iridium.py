"""Iridium monitoring routes.

NOTE: This module is currently in DEMO MODE. The burst detection generates
simulated data for demonstration purposes. Real Iridium decoding requires
gr-iridium or iridium-toolkit which are not yet integrated.
"""

from __future__ import annotations

import json
import queue
import random
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Generator

from flask import Blueprint, jsonify, request, Response

import app as app_module
from utils.logging import iridium_logger as logger
from utils.validation import validate_frequency, validate_device_index, validate_gain
from utils.sse import format_sse
from utils.sdr import SDRFactory, SDRType

iridium_bp = Blueprint("iridium", __name__, url_prefix="/iridium")

# Flag indicating this is demo mode (simulated data)
DEMO_MODE = True


def monitor_iridium(process):
    """
    Monitor Iridium capture and detect bursts.

    NOTE: Currently generates SIMULATED data for demonstration.
    Real Iridium decoding is not yet implemented.
    """
    try:
        burst_count = 0
        # Send initial demo mode warning
        app_module.satellite_queue.put(
            {"type": "info", "message": "⚠️ DEMO MODE: Generating simulated Iridium bursts for demonstration"}
        )

        while process.poll() is None:
            data = process.stdout.read(1024)
            if data:
                if len(data) > 0 and burst_count < 100:
                    # DEMO: Generate simulated bursts (1% chance per read)
                    if random.random() < 0.01:
                        burst = {
                            "type": "burst",
                            "demo": True,  # Flag as demo data
                            "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                            "frequency": f"{1616 + random.random() * 10:.3f}",
                            "data": f"[SIMULATED] Frame data - Burst #{burst_count + 1}",
                        }
                        app_module.satellite_queue.put(burst)
                        app_module.iridium_bursts.append(burst)
                        burst_count += 1

            time.sleep(0.1)
    except Exception as e:
        logger.error(f"Monitor error: {e}")


@iridium_bp.route("/tools")
def check_iridium_tools():
    """Check for Iridium decoding tools."""
    has_iridium = shutil.which("iridium-extractor") is not None or shutil.which("iridium-parser") is not None
    has_rtl = shutil.which("rtl_fm") is not None
    return jsonify(
        {
            "available": has_iridium or has_rtl,
            "demo_mode": DEMO_MODE,
            "message": "Demo mode active - generating simulated data" if DEMO_MODE else None,
        }
    )


@iridium_bp.route("/start", methods=["POST"])
def start_iridium():
    """Start Iridium burst capture (DEMO MODE - simulated data)."""
    with app_module.satellite_lock:
        if app_module.satellite_process and app_module.satellite_process.poll() is None:
            return jsonify({"status": "error", "message": "Iridium capture already running"}), 409

    data = request.json or {}

    # Validate inputs
    try:
        freq = validate_frequency(data.get("freq", "1626.0"), min_mhz=1610.0, max_mhz=1650.0)
        gain = validate_gain(data.get("gain", "40"))
        device = validate_device_index(data.get("device", "0"))
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    sample_rate = data.get("sampleRate", "2.048e6")
    # Validate sample rate format
    try:
        float(sample_rate.replace("e", "E"))
    except (ValueError, AttributeError):
        return jsonify({"status": "error", "message": "Invalid sample rate format"}), 400

    # Get SDR type from request
    sdr_type_str = data.get("sdr_type", "rtlsdr")
    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR

    # Check for required tools based on SDR type
    if sdr_type == SDRType.RTL_SDR:
        if not shutil.which("iridium-extractor") and not shutil.which("rtl_fm"):
            return (
                jsonify(
                    {"status": "error", "message": "Iridium tools not found. Requires rtl_fm or iridium-extractor."}
                ),
                503,
            )
    else:
        if not shutil.which("rx_fm"):
            return (
                jsonify(
                    {"status": "error", "message": f"rx_fm not found for {sdr_type.value}. Install SoapySDR tools."}
                ),
                503,
            )

    try:
        # Create device object and build command via abstraction layer
        sdr_device = SDRFactory.create_default_device(sdr_type, index=device)
        builder = SDRFactory.get_builder(sdr_type)

        # Parse sample rate
        sample_rate_hz = int(float(sample_rate))

        # Build FM demodulation command
        cmd = builder.build_fm_demod_command(
            device=sdr_device,
            frequency_mhz=float(freq),
            sample_rate=sample_rate_hz,
            gain=float(gain),
            ppm=None,
            modulation="fm",
            squelch=None,
        )

        app_module.satellite_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        thread = threading.Thread(target=monitor_iridium, args=(app_module.satellite_process,), daemon=True)
        thread.start()

        return jsonify(
            {
                "status": "started",
                "demo_mode": DEMO_MODE,
                "message": "Demo mode active - data is simulated" if DEMO_MODE else None,
            }
        )
    except FileNotFoundError as e:
        logger.error(f"Tool not found: {e}")
        return jsonify({"status": "error", "message": f"Tool not found: {e.filename}"}), 503
    except Exception as e:
        logger.error(f"Start error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@iridium_bp.route("/stop", methods=["POST"])
def stop_iridium():
    """Stop Iridium capture."""
    with app_module.satellite_lock:
        if app_module.satellite_process:
            app_module.satellite_process.terminate()
            try:
                app_module.satellite_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                app_module.satellite_process.kill()
            app_module.satellite_process = None

    return jsonify({"status": "stopped"})


@iridium_bp.route("/stream")
def stream_iridium():
    """SSE stream for Iridium bursts."""

    def generate():
        last_keepalive = time.time()
        keepalive_interval = 30.0

        while True:
            try:
                msg = app_module.satellite_queue.get(timeout=1)
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
    return response
