"""CW/Morse code decoder routes."""

from __future__ import annotations

import contextlib
import queue
import subprocess
import threading
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.event_pipeline import process_event
from utils.logging import sensor_logger as logger
from utils.morse import morse_decoder_thread
from utils.process import register_process, safe_terminate, unregister_process
from utils.sdr import SDRFactory, SDRType
from utils.sse import sse_stream_fanout
from utils.validation import (
    validate_device_index,
    validate_frequency,
    validate_gain,
    validate_ppm,
)

morse_bp = Blueprint('morse', __name__)

# Track which device is being used
morse_active_device: int | None = None


def _validate_tone_freq(value: Any) -> float:
    """Validate CW tone frequency (300-1200 Hz)."""
    try:
        freq = float(value)
        if not 300 <= freq <= 1200:
            raise ValueError("Tone frequency must be between 300 and 1200 Hz")
        return freq
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid tone frequency: {value}") from e


def _validate_wpm(value: Any) -> int:
    """Validate words per minute (5-50)."""
    try:
        wpm = int(value)
        if not 5 <= wpm <= 50:
            raise ValueError("WPM must be between 5 and 50")
        return wpm
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid WPM: {value}") from e


def _validate_detect_mode(value: Any) -> str:
    """Validate detection mode ('goertzel' or 'envelope')."""
    mode = str(value).lower().strip()
    if mode not in ('goertzel', 'envelope'):
        raise ValueError("detect_mode must be 'goertzel' or 'envelope'")
    return mode


@morse_bp.route('/morse/start', methods=['POST'])
def start_morse() -> Response:
    global morse_active_device

    with app_module.morse_lock:
        if app_module.morse_process:
            return jsonify({'status': 'error', 'message': 'Morse decoder already running'}), 409

        data = request.json or {}

        # Validate standard SDR inputs
        try:
            freq = validate_frequency(data.get('frequency', '14.060'))
            gain = validate_gain(data.get('gain', '0'))
            ppm = validate_ppm(data.get('ppm', '0'))
            device = validate_device_index(data.get('device', '0'))
        except ValueError as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400

        # Validate Morse-specific inputs
        try:
            detect_mode = _validate_detect_mode(data.get('detect_mode', 'goertzel'))
        except ValueError as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400

        try:
            tone_freq = _validate_tone_freq(data.get('tone_freq', '700'))
        except ValueError as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400

        try:
            wpm = _validate_wpm(data.get('wpm', '15'))
        except ValueError as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400

        # Claim SDR device
        device_int = int(device)
        error = app_module.claim_sdr_device(device_int, 'morse')
        if error:
            return jsonify({
                'status': 'error',
                'error_type': 'DEVICE_BUSY',
                'message': error,
            }), 409
        morse_active_device = device_int

        # Clear queue
        while not app_module.morse_queue.empty():
            try:
                app_module.morse_queue.get_nowait()
            except queue.Empty:
                break

        # Build rtl_fm demodulation command
        sdr_type_str = data.get('sdr_type', 'rtlsdr')
        try:
            sdr_type = SDRType(sdr_type_str)
        except ValueError:
            sdr_type = SDRType.RTL_SDR

        sdr_device = SDRFactory.create_default_device(sdr_type, index=device)
        builder = SDRFactory.get_builder(sdr_device.sdr_type)

        # Envelope mode (OOK/AM): use AM demod, higher sample rate for better
        # envelope resolution.  Goertzel mode (HF CW): use USB demod at 8kHz.
        if detect_mode == 'envelope':
            sample_rate = 48000
            modulation = 'am'
        else:
            sample_rate = 8000
            modulation = 'usb'

        bias_t = data.get('bias_t', False)

        rtl_cmd = builder.build_fm_demod_command(
            device=sdr_device,
            frequency_mhz=freq,
            sample_rate=sample_rate,
            gain=float(gain) if gain and gain != '0' else None,
            ppm=int(ppm) if ppm and ppm != '0' else None,
            modulation=modulation,
            bias_t=bias_t,
        )

        full_cmd = ' '.join(rtl_cmd)
        logger.info(f"Morse decoder running: {full_cmd}")

        try:
            rtl_process = subprocess.Popen(
                rtl_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            register_process(rtl_process)

            # Monitor rtl_fm stderr
            def monitor_stderr():
                for line in rtl_process.stderr:
                    err_text = line.decode('utf-8', errors='replace').strip()
                    if err_text:
                        logger.debug(f"[rtl_fm/morse] {err_text}")

            stderr_thread = threading.Thread(target=monitor_stderr)
            stderr_thread.daemon = True
            stderr_thread.start()

            # Start Morse decoder thread
            stop_event = threading.Event()
            decoder_thread = threading.Thread(
                target=morse_decoder_thread,
                args=(
                    rtl_process.stdout,
                    app_module.morse_queue,
                    stop_event,
                    sample_rate,
                    tone_freq,
                    wpm,
                    detect_mode,
                ),
            )
            decoder_thread.daemon = True
            decoder_thread.start()

            app_module.morse_process = rtl_process
            app_module.morse_process._stop_decoder = stop_event
            app_module.morse_process._decoder_thread = decoder_thread

            app_module.morse_queue.put({'type': 'status', 'status': 'started'})

            return jsonify({
                'status': 'started',
                'command': full_cmd,
                'detect_mode': detect_mode,
                'modulation': modulation,
                'tone_freq': tone_freq,
                'wpm': wpm,
            })

        except FileNotFoundError as e:
            if morse_active_device is not None:
                app_module.release_sdr_device(morse_active_device)
                morse_active_device = None
            return jsonify({'status': 'error', 'message': f'Tool not found: {e.filename}'}), 400

        except Exception as e:
            # Clean up rtl_fm if it was started
            try:
                rtl_process.terminate()
                rtl_process.wait(timeout=2)
            except Exception:
                with contextlib.suppress(Exception):
                    rtl_process.kill()
            unregister_process(rtl_process)
            if morse_active_device is not None:
                app_module.release_sdr_device(morse_active_device)
                morse_active_device = None
            return jsonify({'status': 'error', 'message': str(e)}), 500


@morse_bp.route('/morse/stop', methods=['POST'])
def stop_morse() -> Response:
    global morse_active_device

    with app_module.morse_lock:
        if app_module.morse_process:
            # Signal decoder thread to stop
            stop_event = getattr(app_module.morse_process, '_stop_decoder', None)
            if stop_event:
                stop_event.set()

            safe_terminate(app_module.morse_process)
            unregister_process(app_module.morse_process)
            app_module.morse_process = None

            if morse_active_device is not None:
                app_module.release_sdr_device(morse_active_device)
                morse_active_device = None

            app_module.morse_queue.put({'type': 'status', 'status': 'stopped'})
            return jsonify({'status': 'stopped'})

        return jsonify({'status': 'not_running'})


@morse_bp.route('/morse/status')
def morse_status() -> Response:
    with app_module.morse_lock:
        running = (
            app_module.morse_process is not None
            and app_module.morse_process.poll() is None
        )
        return jsonify({'running': running})


@morse_bp.route('/morse/stream')
def morse_stream() -> Response:
    def _on_msg(msg: dict[str, Any]) -> None:
        process_event('morse', msg, msg.get('type'))

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.morse_queue,
            channel_key='morse',
            timeout=1.0,
            keepalive_interval=30.0,
            on_message=_on_msg,
        ),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response
