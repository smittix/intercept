"""VDL2 aircraft datalink routes."""

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

vdl2_bp = Blueprint('vdl2', __name__, url_prefix='/vdl2')

# Default VDL2 frequencies (MHz) - common worldwide
DEFAULT_VDL2_FREQUENCIES = [
    '136975000',  # Primary worldwide
    '136725000',  # Europe
    '136775000',  # Europe
    '136800000',  # Multi-region
    '136875000',  # Multi-region
]

# Message counter for statistics
vdl2_message_count = 0
vdl2_last_message_time = None

# Track which device is being used
vdl2_active_device: int | None = None
vdl2_active_sdr_type: str | None = None


def find_dumpvdl2():
    """Find dumpvdl2 binary."""
    return shutil.which('dumpvdl2')


def stream_vdl2_output(process: subprocess.Popen, is_text_mode: bool = False) -> None:
    """Stream dumpvdl2 JSON output to queue."""
    global vdl2_message_count, vdl2_last_message_time

    try:
        app_module.vdl2_queue.put({'type': 'status', 'status': 'started'})

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
                data = json.loads(line)

                # Add our metadata
                data['type'] = 'vdl2'
                data['timestamp'] = datetime.utcnow().isoformat() + 'Z'

                # Flatten nested VDL2 identifying fields to top level for correlator matching
                # dumpvdl2 nests flight/reg inside vdl2.avlc.acars and ICAO in avlc.src.addr
                try:
                    vdl2_inner = data.get('vdl2', data)
                    avlc = vdl2_inner.get('avlc') or {}
                    acars_payload = avlc.get('acars') or {}

                    # Promote AVLC source address — this is the aircraft ICAO hex
                    # Do this FIRST so even non-ACARS VDL2 frames can be correlated
                    src = avlc.get('src') or {}
                    src_addr = src.get('addr', '')
                    src_type = src.get('type', '')
                    if src_addr and src_type == 'Aircraft':
                        data['icao'] = src_addr.upper()
                        data['addr'] = src_addr.upper()

                    # Promote ACARS fields to top level so FlightCorrelator can match them
                    if acars_payload.get('flight'):
                        data['flight'] = acars_payload['flight']
                    if acars_payload.get('reg'):
                        data['reg'] = acars_payload['reg']
                        data['tail'] = acars_payload['reg']
                    if acars_payload.get('label'):
                        data['label'] = acars_payload['label']
                    if acars_payload.get('msg_text'):
                        data['text'] = acars_payload['msg_text']

                    # Enrich with translated ACARS label (consistent with ACARS route)
                    if acars_payload.get('label'):
                        translation = translate_message({
                            'label': acars_payload.get('label'),
                            'text': acars_payload.get('msg_text', ''),
                        })
                        data['label_description'] = translation['label_description']
                        data['message_type'] = translation['message_type']
                        data['parsed'] = translation['parsed']
                except Exception:
                    pass

                # Update stats
                vdl2_message_count += 1
                vdl2_last_message_time = time.time()

                app_module.vdl2_queue.put(data)

                # Feed flight correlator
                with contextlib.suppress(Exception):
                    get_flight_correlator().add_vdl2_message(data)

                # Log if enabled
                if app_module.logging_enabled:
                    try:
                        with open(app_module.log_file_path, 'a') as f:
                            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            f.write(f"{ts} | VDL2 | {json.dumps(data)}\n")
                    except Exception:
                        pass

            except json.JSONDecodeError:
                # Not JSON - could be status message
                if line:
                    logger.debug(f"dumpvdl2 non-JSON: {line[:100]}")

    except Exception as e:
        logger.error(f"VDL2 stream error: {e}")
        app_module.vdl2_queue.put({'type': 'error', 'message': str(e)})
    finally:
        global vdl2_active_device, vdl2_active_sdr_type
        # Ensure process is terminated
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            with contextlib.suppress(Exception):
                process.kill()
        unregister_process(process)
        app_module.vdl2_queue.put({'type': 'status', 'status': 'stopped'})
        with app_module.vdl2_lock:
            app_module.vdl2_process = None
        # Release SDR device
        if vdl2_active_device is not None:
            app_module.release_sdr_device(vdl2_active_device, vdl2_active_sdr_type or 'rtlsdr')
            vdl2_active_device = None
            vdl2_active_sdr_type = None


@vdl2_bp.route('/tools')
def check_vdl2_tools() -> Response:
    """Check for VDL2 decoding tools."""
    has_dumpvdl2 = find_dumpvdl2() is not None

    return jsonify({
        'dumpvdl2': has_dumpvdl2,
        'ready': has_dumpvdl2
    })


@vdl2_bp.route('/status')
def vdl2_status() -> Response:
    """Get VDL2 decoder status."""
    running = False
    if app_module.vdl2_process:
        running = app_module.vdl2_process.poll() is None

    return jsonify({
        'running': running,
        'message_count': vdl2_message_count,
        'last_message_time': vdl2_last_message_time,
        'queue_size': app_module.vdl2_queue.qsize()
    })


@vdl2_bp.route('/start', methods=['POST'])
def start_vdl2() -> Response:
    """Start VDL2 decoder."""
    global vdl2_message_count, vdl2_last_message_time, vdl2_active_device, vdl2_active_sdr_type

    with app_module.vdl2_lock:
        if app_module.vdl2_process and app_module.vdl2_process.poll() is None:
            return api_error('VDL2 decoder already running', 409)

    # Check for dumpvdl2
    dumpvdl2_path = find_dumpvdl2()
    if not dumpvdl2_path:
        return api_error('dumpvdl2 not found. Install from: https://github.com/szpajder/dumpvdl2', 400)

    data = request.json or {}

    # Validate inputs
    try:
        device = validate_device_index(data.get('device', '0'))
        gain = validate_gain(data.get('gain', '40'))
        ppm = validate_ppm(data.get('ppm', '0'))
    except ValueError as e:
        return api_error(str(e), 400)

    # Resolve SDR type for device selection
    sdr_type_str = data.get('sdr_type', 'rtlsdr')
    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR

    # Check if device is available
    device_int = int(device)
    error = app_module.claim_sdr_device(device_int, 'vdl2', sdr_type_str)
    if error:
        return api_error(error, 409, error_type='DEVICE_BUSY')

    vdl2_active_device = device_int
    vdl2_active_sdr_type = sdr_type_str

    # Get frequencies - use provided or defaults
    # dumpvdl2 expects frequencies in Hz (integers)
    frequencies = data.get('frequencies', DEFAULT_VDL2_FREQUENCIES)
    if isinstance(frequencies, str):
        frequencies = [f.strip() for f in frequencies.split(',')]

    # Clear queue
    while not app_module.vdl2_queue.empty():
        try:
            app_module.vdl2_queue.get_nowait()
        except queue.Empty:
            break

    # Reset stats
    vdl2_message_count = 0
    vdl2_last_message_time = None

    is_soapy = sdr_type not in (SDRType.RTL_SDR,)

    # Build dumpvdl2 command
    # dumpvdl2 --output decoded:json --rtlsdr <device> --gain <gain> --correction <ppm> <freq1> <freq2> ...
    cmd = [dumpvdl2_path]
    cmd.extend(['--output', 'decoded:json:file:path=-'])

    if is_soapy:
        # SoapySDR device
        sdr_device = SDRFactory.create_default_device(sdr_type, index=device_int)
        builder = SDRFactory.get_builder(sdr_type)
        device_str = builder._build_device_string(sdr_device)
        cmd.extend(['--soapysdr', device_str])
    else:
        cmd.extend(['--rtlsdr', str(device)])

    # Add gain
    if gain and str(gain) != '0':
        cmd.extend(['--gain', str(gain)])

    # Add PPM correction if specified
    if ppm and str(ppm) != '0':
        cmd.extend(['--correction', str(ppm)])

    # Add frequencies (dumpvdl2 takes them as positional args in Hz)
    cmd.extend(frequencies)

    logger.info(f"Starting VDL2 decoder: {' '.join(cmd)}")

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
            if vdl2_active_device is not None:
                app_module.release_sdr_device(vdl2_active_device, vdl2_active_sdr_type or 'rtlsdr')
                vdl2_active_device = None
                vdl2_active_sdr_type = None
            stderr = ''
            if process.stderr:
                stderr = process.stderr.read().decode('utf-8', errors='replace')
            if stderr:
                logger.error(f"dumpvdl2 stderr:\n{stderr}")
            error_msg = 'dumpvdl2 failed to start'
            if stderr:
                error_msg += f': {stderr[:500]}'
            logger.error(error_msg)
            return api_error(error_msg, 500)

        app_module.vdl2_process = process
        register_process(process)

        # Start output streaming thread
        thread = threading.Thread(
            target=stream_vdl2_output,
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
        if vdl2_active_device is not None:
            app_module.release_sdr_device(vdl2_active_device, vdl2_active_sdr_type or 'rtlsdr')
            vdl2_active_device = None
            vdl2_active_sdr_type = None
        logger.error(f"Failed to start VDL2 decoder: {e}")
        return api_error(str(e), 500)


@vdl2_bp.route('/stop', methods=['POST'])
def stop_vdl2() -> Response:
    """Stop VDL2 decoder."""
    global vdl2_active_device, vdl2_active_sdr_type

    with app_module.vdl2_lock:
        if not app_module.vdl2_process:
            return api_error('VDL2 decoder not running', 400)

        try:
            app_module.vdl2_process.terminate()
            app_module.vdl2_process.wait(timeout=PROCESS_TERMINATE_TIMEOUT)
        except subprocess.TimeoutExpired:
            app_module.vdl2_process.kill()
        except Exception as e:
            logger.error(f"Error stopping VDL2: {e}")

        app_module.vdl2_process = None

    # Release device from registry
    if vdl2_active_device is not None:
        app_module.release_sdr_device(vdl2_active_device, vdl2_active_sdr_type or 'rtlsdr')
        vdl2_active_device = None
        vdl2_active_sdr_type = None

    return jsonify({'status': 'stopped'})


@vdl2_bp.route('/stream')
def stream_vdl2() -> Response:
    """SSE stream for VDL2 messages."""
    def _on_msg(msg: dict[str, Any]) -> None:
        process_event('vdl2', msg, msg.get('type'))

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.vdl2_queue,
            channel_key='vdl2',
            timeout=SSE_QUEUE_TIMEOUT,
            keepalive_interval=SSE_KEEPALIVE_INTERVAL,
            on_message=_on_msg,
        ),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response



@vdl2_bp.route('/messages')
def get_vdl2_messages() -> Response:
    """Get recent VDL2 messages from correlator (for history reload)."""
    limit = request.args.get('limit', 50, type=int)
    limit = max(1, min(limit, 200))
    msgs = get_flight_correlator().get_recent_messages('vdl2', limit)
    return jsonify(msgs)


@vdl2_bp.route('/clear', methods=['POST'])
def clear_vdl2_messages() -> Response:
    """Clear stored VDL2 messages and reset counter."""
    global vdl2_message_count, vdl2_last_message_time
    get_flight_correlator().clear_vdl2()
    vdl2_message_count = 0
    vdl2_last_message_time = None
    return jsonify({'status': 'cleared'})


@vdl2_bp.route('/frequencies')
def get_frequencies() -> Response:
    """Get default VDL2 frequencies."""
    return jsonify({
        'default': DEFAULT_VDL2_FREQUENCIES,
        'regions': {
            'north_america': ['136975000', '136100000', '136650000', '136700000', '136800000'],
            'europe': ['136975000', '136675000', '136725000', '136775000', '136825000'],
            'asia_pacific': ['136975000', '136900000'],
        }
    })
