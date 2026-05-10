"""
UAT 978MHz ADS-B Route

Decodes UAT (Universal Access Transceiver) traffic on 978MHz using
dump978 piped from rtl_sdr. UAT is used by US aircraft below 18,000ft
as an alternative to 1090MHz ADS-B.

Pipeline: rtl_sdr -f 978000000 -s 2083334 | dump978 | uat2json
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time

from flask import Blueprint, Response, jsonify
from utils.constants import SSE_KEEPALIVE_INTERVAL, SSE_QUEUE_TIMEOUT
from utils.logging import get_logger
from utils.responses import api_error, api_success
from utils.sse import format_sse
from utils.validation import validate_device_index, validate_gain

logger = get_logger('intercept.uat')

uat_bp = Blueprint('uat', __name__, url_prefix='/uat')

# State
_uat_process: subprocess.Popen | None = None
_uat_lock = threading.Lock()
_uat_active = False
_uat_aircraft: dict = {}
_uat_queue: queue.Queue = queue.Queue(maxsize=500)

UAT_FREQUENCY = 978_000_000
UAT_SAMPLE_RATE = 2_083_334


def _uat_running() -> bool:
    return _uat_process is not None and _uat_process.poll() is None


def _parse_uat_line(line: str) -> dict | None:
    """Parse a dump978 output line into a structured dict."""
    line = line.strip()
    if not line or ';' not in line:
        return None
    direction = line[0]  # '+' uplink, '-' downlink
    payload = line[1:line.index(';')]
    return {'direction': direction, 'payload': payload, 'raw': line}


def _reader_thread(proc: subprocess.Popen) -> None:
    """Read dump978 stdout and push parsed messages to the SSE queue."""
    global _uat_active, _uat_aircraft
    try:
        for raw in proc.stdout:
            if not _uat_active:
                break
            line = raw.decode(errors='replace').strip()
            msg = _parse_uat_line(line)
            if msg:
                msg['ts'] = time.time()
                try:
                    _uat_queue.put_nowait({'type': 'message', 'data': msg})
                except queue.Full:
                    pass
    except Exception as e:
        logger.debug(f'UAT reader thread ended: {e}')


@uat_bp.route('/start', methods=['POST'])
def uat_start():
    global _uat_process, _uat_active

    rtl_sdr = shutil.which('rtl_sdr')
    dump978 = shutil.which('dump978')

    if not rtl_sdr:
        return api_error('rtl_sdr not found. Install rtl-sdr package.', 404)
    if not dump978:
        return api_error('dump978 not found. Build from https://github.com/mutability/dump978', 404)

    with _uat_lock:
        if _uat_running():
            return jsonify({'status': 'already_running'})

        from flask import request
        data = request.get_json(silent=True) or {}
        try:
            device = validate_device_index(data.get('device', '0'))
            gain = int(validate_gain(data.get('gain', '48')))
        except ValueError as e:
            return api_error(str(e), 400)

        import app as app_module
        error = app_module.claim_sdr_device(int(device), 'uat', 'rtlsdr')
        if error:
            return jsonify({'status': 'error', 'error_type': 'DEVICE_BUSY', 'message': error}), 409

        rtl_cmd = [rtl_sdr, '-f', str(UAT_FREQUENCY), '-s', str(UAT_SAMPLE_RATE),
                   '-d', str(device), '-g', str(gain), '-']
        dump_cmd = [dump978]

        logger.info(f'Starting UAT pipeline: {" ".join(rtl_cmd)} | dump978')
        try:
            rtl_proc = subprocess.Popen(rtl_cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, start_new_session=True)
            _uat_process = subprocess.Popen(dump_cmd, stdin=rtl_proc.stdout,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            start_new_session=True)
            rtl_proc.stdout.close()
        except Exception as e:
            import app as app_module
            app_module.release_sdr_device(int(device), 'rtlsdr')
            return api_error(f'Failed to start UAT pipeline: {e}', 500)

        _uat_active = True
        threading.Thread(target=_reader_thread, args=(_uat_process,), daemon=True).start()

    return jsonify({'status': 'started', 'device': device,
                    'frequency_mhz': UAT_FREQUENCY / 1e6, 'sample_rate': UAT_SAMPLE_RATE})


@uat_bp.route('/stop', methods=['POST'])
def uat_stop():
    global _uat_process, _uat_active
    with _uat_lock:
        if not _uat_running():
            return jsonify({'status': 'not_running'})
        _uat_active = False
        try:
            pgid = os.getpgid(_uat_process.pid)
            os.killpg(pgid, 15)
            _uat_process.wait(timeout=5)
        except Exception:
            pass
        _uat_process = None
        import app as app_module
        app_module.release_sdr_device(0, 'rtlsdr')
    return jsonify({'status': 'stopped'})


@uat_bp.route('/status')
def uat_status():
    return jsonify({
        'running': _uat_running(),
        'pid': _uat_process.pid if _uat_running() else None,
        'dump978': shutil.which('dump978'),
        'rtl_sdr': shutil.which('rtl_sdr'),
        'frequency_mhz': UAT_FREQUENCY / 1e6,
    })


@uat_bp.route('/stream')
def uat_stream():
    """SSE stream of raw UAT messages."""
    def generate():
        last_keepalive = time.time()
        while True:
            try:
                event = _uat_queue.get(timeout=SSE_QUEUE_TIMEOUT)
                yield format_sse(json.dumps(event))
            except queue.Empty:
                now = time.time()
                if now - last_keepalive >= SSE_KEEPALIVE_INTERVAL:
                    yield format_sse('{"type":"keepalive"}')
                    last_keepalive = now

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
