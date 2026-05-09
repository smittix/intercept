"""ACARS aircraft messaging routes."""

from __future__ import annotations

import contextlib
import json
import os
import platform
import pty
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.acars_translator import translate_message
from utils.constants import (
    PROCESS_START_WAIT,
    PROCESS_TERMINATE_TIMEOUT,
    SSE_KEEPALIVE_INTERVAL,
    SSE_QUEUE_TIMEOUT,
)
from utils.event_pipeline import process_event
from utils.flight_correlator import get_flight_correlator
from utils.logging import sensor_logger as logger
from utils.process import register_process, unregister_process
from utils.responses import api_error
from utils.sdr import SDRFactory, SDRType
from utils.sse import sse_stream_fanout
from utils.validation import validate_device_index, validate_gain, validate_ppm

acars_bp = Blueprint('acars', __name__, url_prefix='/acars')

# Default VHF ACARS frequencies (MHz) - North America primary
DEFAULT_ACARS_FREQUENCIES = [
    '131.550',  # Primary worldwide / North America
    '130.025',  # North America secondary
    '129.125',  # North America tertiary
    '131.725',  # North America (major US carriers)
    '131.825',  # North America (major US carriers)
]

# Message counter for statistics
acars_message_count = 0
acars_last_message_time = None

# Track which device is being used
acars_active_device: int | None = None
acars_active_sdr_type: str | None = None
acars_bias_t_active: bool = False


def find_acarsdec():
    """Find acarsdec binary."""
    return shutil.which('acarsdec')


def get_acarsdec_json_flag(acarsdec_path: str) -> str:
    """Detect which JSON output flag acarsdec supports.

    Different forks use different flags:
    - TLeconte v4.0+: uses -j for JSON stdout
    - TLeconte v3.x: uses -o 4 for JSON stdout
    - f00b4r0 fork (DragonOS): uses --output json:file:- for JSON stdout
    """
    try:
        # Get help/version by running acarsdec with no args (shows usage)
        result = subprocess.run(
            [acarsdec_path],
            capture_output=True,
            text=True,
            timeout=5
        )
        output = result.stdout + result.stderr

        import re

        # Check for f00b4r0 fork signature: uses --output instead of -j/-o
        # f00b4r0's help shows "--output" for output configuration
        if '--output' in output or 'json:file:' in output.lower():
            logger.debug("Detected f00b4r0 acarsdec fork (--output syntax)")
            return '--output'

        # Parse version from output like "Acarsdec v4.3.1" or "Acarsdec/acarsserv 3.7"
        version_match = re.search(r'acarsdec[^\d]*v?(\d+)\.(\d+)', output, re.IGNORECASE)
        if version_match:
            major = int(version_match.group(1))
            # Version 4.0+ uses -j for JSON stdout
            if major >= 4:
                return '-j'
            # Version 3.x uses -o for output mode
            else:
                return '-o'
    except Exception as e:
        logger.debug(f"Could not detect acarsdec version: {e}")

    # Default to -j (TLeconte modern standard)
    return '-j'


def stream_acars_output(process: subprocess.Popen, is_text_mode: bool = False) -> None:
    """Stream acarsdec JSON output to queue."""
    global acars_message_count, acars_last_message_time

    try:
        app_module.acars_queue.put({'type': 'status', 'status': 'started'})

        # Use appropriate sentinel based on mode (text mode for pty on macOS)
        sentinel = '' if is_text_mode else b''
        for line in iter(process.stdout.readline, sentinel):
            if is_text_mode:
                line = line.strip()
            else:
                line = line.decode('utf-8', errors='replace').strip()
            if not line:
                continue

            try:
                # acarsdec -o 4 outputs JSON, one message per line
                data = json.loads(line)

                # Add our metadata
                data['type'] = 'acars'
                data['timestamp'] = datetime.utcnow().isoformat() + 'Z'

                # Enrich with translated label and parsed fields
                try:
                    translation = translate_message(data)
                    data['label_description'] = translation['label_description']
                    data['message_type'] = translation['message_type']
                    data['parsed'] = translation['parsed']
                except Exception:
                    pass

                # Update stats
                acars_message_count += 1
                acars_last_message_time = time.time()

                app_module.acars_queue.put(data)

                # Feed flight correlator
                with contextlib.suppress(Exception):
                    get_flight_correlator().add_acars_message(data)

                # Log if enabled
                if app_module.logging_enabled:
                    try:
                        with open(app_module.log_file_path, 'a') as f:
                            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            f.write(f"{ts} | ACARS | {json.dumps(data)}\n")
                    except Exception:
                        pass

            except json.JSONDecodeError:
                # Not JSON - could be status message
                if line:
                    logger.debug(f"acarsdec non-JSON: {line[:100]}")

    except Exception as e:
        logger.error(f"ACARS stream error: {e}")
        app_module.acars_queue.put({'type': 'error', 'message': str(e)})
    finally:
        global acars_active_device, acars_active_sdr_type, acars_bias_t_active
        # Ensure process is terminated
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            with contextlib.suppress(Exception):
                process.kill()
        unregister_process(process)
        app_module.acars_queue.put({'type': 'status', 'status': 'stopped'})
        with app_module.acars_lock:
            app_module.acars_process = None
        # Disable Bias-T if it was enabled
        if acars_bias_t_active and acars_active_device is not None:
            from utils.sdr.rtlsdr import disable_bias_t_via_rtl_biast
            disable_bias_t_via_rtl_biast(acars_active_device)
            acars_bias_t_active = False
        # Release SDR device
        if acars_active_device is not None:
            app_module.release_sdr_device(acars_active_device, acars_active_sdr_type or 'rtlsdr')
            acars_active_device = None
            acars_active_sdr_type = None


@acars_bp.route('/tools')
def check_acars_tools() -> Response:
    """Check for ACARS decoding tools."""
    has_acarsdec = find_acarsdec() is not None

    return jsonify({
        'acarsdec': has_acarsdec,
        'ready': has_acarsdec
    })


@acars_bp.route('/status')
def acars_status() -> Response:
    """Get ACARS decoder status."""
    running = False
    if app_module.acars_process:
        running = app_module.acars_process.poll() is None

    return jsonify({
        'running': running,
        'message_count': acars_message_count,
        'last_message_time': acars_last_message_time,
        'queue_size': app_module.acars_queue.qsize()
    })


@acars_bp.route('/start', methods=['POST'])
def start_acars() -> Response:
    """Start ACARS decoder."""
    global acars_message_count, acars_last_message_time, acars_active_device, acars_active_sdr_type, acars_bias_t_active

    with app_module.acars_lock:
        if app_module.acars_process and app_module.acars_process.poll() is None:
            return api_error('ACARS decoder already running', 409)

    # Check for acarsdec
    acarsdec_path = find_acarsdec()
    if not acarsdec_path:
        return api_error('acarsdec not found. Install with: sudo apt install acarsdec', 400)

    data = request.json or {}

    # Validate inputs
    try:
        device = validate_device_index(data.get('device', '0'))
        gain = validate_gain(data.get('gain', '40'))
        ppm = validate_ppm(data.get('ppm', '0'))
    except ValueError as e:
        return api_error(str(e), 400)

    bias_t = bool(data.get('bias_t', False))

    # Resolve SDR type for device selection
    sdr_type_str = data.get('sdr_type', 'rtlsdr')

    # Check if device is available
    device_int = int(device)
    error = app_module.claim_sdr_device(device_int, 'acars', sdr_type_str)
    if error:
        return api_error(error, 409, error_type='DEVICE_BUSY')

    acars_active_device = device_int
    acars_active_sdr_type = sdr_type_str
    acars_bias_t_active = bias_t and not is_soapy

    # Get frequencies - use provided or defaults
    frequencies = data.get('frequencies', DEFAULT_ACARS_FREQUENCIES)
    if isinstance(frequencies, str):
        frequencies = [f.strip() for f in frequencies.split(',')]

    # Clear queue
    while not app_module.acars_queue.empty():
        try:
            app_module.acars_queue.get_nowait()
        except queue.Empty:
            break

    # Reset stats
    acars_message_count = 0
    acars_last_message_time = None

    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR

    is_soapy = sdr_type not in (SDRType.RTL_SDR,)

    # Build acarsdec command
    # Different forks have different syntax:
    # - TLeconte v4+: acarsdec -j -g <gain> -p <ppm> -r <device> <freq1> <freq2> ...
    # - TLeconte v3: acarsdec -o 4 -g <gain> -p <ppm> -r <device> <freq1> <freq2> ...
    # - f00b4r0 (DragonOS): acarsdec --output json:file:- -g <gain> -p <ppm> -r <device> <freq1> ...
    # SoapySDR devices: TLeconte uses -d <device_string>, f00b4r0 uses --soapysdr <device_string>
    # Note: gain/ppm must come BEFORE -r/-d
    json_flag = get_acarsdec_json_flag(acarsdec_path)
    cmd = [acarsdec_path]
    if json_flag == '--output':
        # f00b4r0 fork: --output json:file (no path = stdout)
        cmd.extend(['--output', 'json:file'])
    elif json_flag == '-j':
        cmd.append('-j')         # JSON output (TLeconte v4+)
    else:
        cmd.extend(['-o', '4'])  # JSON output (TLeconte v3.x)

    # Add gain if not auto (must be before -r/-d)
    if gain and str(gain) != '0':
        cmd.extend(['-g', str(gain)])

    # Add PPM correction if specified (must be before -r/-d)
    if ppm and str(ppm) != '0':
        cmd.extend(['-p', str(ppm)])

    # Add device and frequencies
    if is_soapy:
        # SoapySDR device (SDRplay, LimeSDR, Airspy, etc.)
        sdr_device = SDRFactory.create_default_device(sdr_type, index=device_int)
        # Build SoapySDR driver string (e.g., "driver=sdrplay,serial=...")
        builder = SDRFactory.get_builder(sdr_type)
        device_str = builder._build_device_string(sdr_device)
        if json_flag == '--output':
            cmd.extend(['-m', '256'])
            cmd.extend(['--soapysdr', device_str])
        else:
            cmd.extend(['-d', device_str])
    elif json_flag == '--output':
        # f00b4r0 fork RTL-SDR: --rtlsdr <device>
        # Use 3.2 MS/s sample rate for wider bandwidth (handles NA frequency span)
        cmd.extend(['-m', '256'])
        cmd.extend(['--rtlsdr', str(device)])
    else:
        # TLeconte fork RTL-SDR: -r <device>
        cmd.extend(['-r', str(device)])
    cmd.extend(frequencies)

    logger.info(f"Starting ACARS decoder: {' '.join(cmd)}")

    # Enable Bias-T for RTL-SDR if requested (acarsdec has no native flag)
    if bias_t and not is_soapy:
        from utils.sdr.rtlsdr import enable_bias_t_via_rtl_biast
        enable_bias_t_via_rtl_biast(device_int)

    try:
        is_text_mode = False

        # On macOS, use pty to avoid stdout buffering issues
        if platform.system() == 'Darwin':
            master_fd, slave_fd = pty.openpty()
            process = subprocess.Popen(
                cmd,
                stdout=slave_fd,
                stderr=subprocess.PIPE,
                start_new_session=True
            )
            os.close(slave_fd)
            # Wrap master_fd as a text file for line-buffered reading
            process.stdout = open(master_fd, buffering=1)
            is_text_mode = True
        else:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )

        # Wait briefly to check if process started
        time.sleep(PROCESS_START_WAIT)

        if process.poll() is not None:
            # Process died - release device
            if acars_active_device is not None:
                app_module.release_sdr_device(acars_active_device, acars_active_sdr_type or 'rtlsdr')
                acars_active_device = None
                acars_active_sdr_type = None
            stderr = ''
            if process.stderr:
                stderr = process.stderr.read().decode('utf-8', errors='replace')
            if stderr:
                logger.error(f"acarsdec stderr:\n{stderr}")
            error_msg = 'acarsdec failed to start'
            if stderr:
                error_msg += f': {stderr[:500]}'
            logger.error(error_msg)
            return api_error(error_msg, 500)

        app_module.acars_process = process
        register_process(process)

        # Start output streaming thread
        thread = threading.Thread(
            target=stream_acars_output,
            args=(process, is_text_mode),
            daemon=True
        )
        thread.start()

        return jsonify({
            'status': 'started',
            'frequencies': frequencies,
            'device': device,
            'gain': gain
        })

    except Exception as e:
        # Release device on failure
        if acars_active_device is not None:
            app_module.release_sdr_device(acars_active_device, acars_active_sdr_type or 'rtlsdr')
            acars_active_device = None
            acars_active_sdr_type = None
        logger.error(f"Failed to start ACARS decoder: {e}")
        return api_error(str(e), 500)


@acars_bp.route('/stop', methods=['POST'])
def stop_acars() -> Response:
    """Stop ACARS decoder."""
    global acars_active_device, acars_active_sdr_type, acars_bias_t_active

    with app_module.acars_lock:
        if not app_module.acars_process:
            return api_error('ACARS decoder not running', 400)

        try:
            app_module.acars_process.terminate()
            app_module.acars_process.wait(timeout=PROCESS_TERMINATE_TIMEOUT)
        except subprocess.TimeoutExpired:
            app_module.acars_process.kill()
        except Exception as e:
            logger.error(f"Error stopping ACARS: {e}")

        app_module.acars_process = None

    # Disable Bias-T if it was enabled
    if acars_bias_t_active and acars_active_device is not None:
        from utils.sdr.rtlsdr import disable_bias_t_via_rtl_biast
        disable_bias_t_via_rtl_biast(acars_active_device)
        acars_bias_t_active = False

    # Release device from registry
    if acars_active_device is not None:
        app_module.release_sdr_device(acars_active_device, acars_active_sdr_type or 'rtlsdr')
        acars_active_device = None
        acars_active_sdr_type = None

    return jsonify({'status': 'stopped'})


@acars_bp.route('/stream')
def stream_acars() -> Response:
    """SSE stream for ACARS messages."""
    def _on_msg(msg: dict[str, Any]) -> None:
        process_event('acars', msg, msg.get('type'))

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.acars_queue,
            channel_key='acars',
            timeout=SSE_QUEUE_TIMEOUT,
            keepalive_interval=SSE_KEEPALIVE_INTERVAL,
            on_message=_on_msg,
        ),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@acars_bp.route('/messages')
def get_acars_messages() -> Response:
    """Get recent ACARS messages from correlator (for history reload)."""
    limit = request.args.get('limit', 50, type=int)
    limit = max(1, min(limit, 200))
    msgs = get_flight_correlator().get_recent_messages('acars', limit)
    return jsonify(msgs)


@acars_bp.route('/clear', methods=['POST'])
def clear_acars_messages() -> Response:
    """Clear stored ACARS messages and reset counter."""
    global acars_message_count, acars_last_message_time
    get_flight_correlator().clear_acars()
    acars_message_count = 0
    acars_last_message_time = None
    return jsonify({'status': 'cleared'})


@acars_bp.route('/frequencies')
def get_frequencies() -> Response:
    """Get default ACARS frequencies."""
    return jsonify({
        'default': DEFAULT_ACARS_FREQUENCIES,
        'regions': {
            'north_america': ['131.550', '130.025', '129.125', '131.725', '131.825'],
            'europe': ['131.525', '131.725', '131.550'],
            'asia_pacific': ['131.550', '131.450'],
        }
    })
