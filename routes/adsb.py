"""ADS-B aircraft tracking routes."""

from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
from typing import Any, Generator

from flask import Blueprint, jsonify, request, Response, render_template

import app as app_module
from utils.logging import adsb_logger as logger
from utils.validation import validate_device_index, validate_gain
from utils.sse import format_sse

adsb_bp = Blueprint('adsb', __name__, url_prefix='/adsb')

# Track if using service
adsb_using_service = False
adsb_connected = False
adsb_messages_received = 0
adsb_last_message_time = None

# Common installation paths for dump1090 (when not in PATH)
DUMP1090_PATHS = [
    '/usr/local/bin/dump1090',
    '/usr/local/bin/dump1090-fa',
    '/usr/local/bin/dump1090-mutability',
    '/usr/bin/dump1090',
    '/usr/bin/dump1090-fa',
    '/usr/bin/dump1090-mutability',
]


def find_dump1090():
    """Find dump1090 binary, checking PATH and common locations."""
    # First try PATH
    for name in ['dump1090', 'dump1090-mutability', 'dump1090-fa']:
        path = shutil.which(name)
        if path:
            return path
    # Check common installation paths directly
    for path in DUMP1090_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def check_dump1090_service():
    """Check if dump1090 SBS port (30003) is available."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('localhost', 30003))
        sock.close()
        if result == 0:
            return 'localhost:30003'
    except Exception:
        pass
    return None


def parse_sbs_stream(service_addr):
    """Parse SBS format data from dump1090 port 30003."""
    global adsb_using_service, adsb_connected, adsb_messages_received, adsb_last_message_time

    host, port = service_addr.split(':')
    port = int(port)

    logger.info(f"SBS stream parser started, connecting to {host}:{port}")
    adsb_connected = False
    adsb_messages_received = 0

    while adsb_using_service:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((host, port))
            adsb_connected = True
            logger.info("Connected to SBS stream")

            buffer = ""
            last_update = time.time()
            pending_updates = set()

            while adsb_using_service:
                try:
                    data = sock.recv(4096).decode('utf-8', errors='ignore')
                    if not data:
                        break
                    buffer += data

                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue

                        parts = line.split(',')
                        if len(parts) < 11 or parts[0] != 'MSG':
                            continue

                        msg_type = parts[1]
                        icao = parts[4].upper()
                        if not icao:
                            continue

                        aircraft = app_module.adsb_aircraft.get(icao, {'icao': icao})

                        if msg_type == '1' and len(parts) > 10:
                            callsign = parts[10].strip()
                            if callsign:
                                aircraft['callsign'] = callsign

                        elif msg_type == '3' and len(parts) > 15:
                            if parts[11]:
                                try:
                                    aircraft['altitude'] = int(float(parts[11]))
                                except (ValueError, TypeError):
                                    pass
                            if parts[14] and parts[15]:
                                try:
                                    aircraft['lat'] = float(parts[14])
                                    aircraft['lon'] = float(parts[15])
                                except (ValueError, TypeError):
                                    pass

                        elif msg_type == '4' and len(parts) > 13:
                            if parts[12]:
                                try:
                                    aircraft['speed'] = int(float(parts[12]))
                                except (ValueError, TypeError):
                                    pass
                            if parts[13]:
                                try:
                                    aircraft['heading'] = int(float(parts[13]))
                                except (ValueError, TypeError):
                                    pass

                        elif msg_type == '5' and len(parts) > 11:
                            if parts[10]:
                                callsign = parts[10].strip()
                                if callsign:
                                    aircraft['callsign'] = callsign
                            if parts[11]:
                                try:
                                    aircraft['altitude'] = int(float(parts[11]))
                                except (ValueError, TypeError):
                                    pass

                        elif msg_type == '6' and len(parts) > 17:
                            if parts[17]:
                                aircraft['squawk'] = parts[17]

                        app_module.adsb_aircraft[icao] = aircraft
                        pending_updates.add(icao)
                        adsb_messages_received += 1
                        adsb_last_message_time = time.time()

                        now = time.time()
                        if now - last_update >= 1.0:
                            for update_icao in pending_updates:
                                if update_icao in app_module.adsb_aircraft:
                                    app_module.adsb_queue.put({
                                        'type': 'aircraft',
                                        **app_module.adsb_aircraft[update_icao]
                                    })
                            pending_updates.clear()
                            last_update = now

                except socket.timeout:
                    continue

            sock.close()
            adsb_connected = False
        except Exception as e:
            adsb_connected = False
            logger.warning(f"SBS connection error: {e}, reconnecting...")
            time.sleep(2)

    adsb_connected = False
    logger.info("SBS stream parser stopped")


@adsb_bp.route('/tools')
def check_adsb_tools():
    """Check for ADS-B decoding tools."""
    return jsonify({
        'dump1090': find_dump1090() is not None,
        'rtl_adsb': shutil.which('rtl_adsb') is not None
    })


@adsb_bp.route('/status')
def adsb_status():
    """Get ADS-B tracking status for debugging."""
    return jsonify({
        'tracking_active': adsb_using_service,
        'connected_to_sbs': adsb_connected,
        'messages_received': adsb_messages_received,
        'last_message_time': adsb_last_message_time,
        'aircraft_count': len(app_module.adsb_aircraft),
        'aircraft': dict(app_module.adsb_aircraft),  # Full aircraft data
        'queue_size': app_module.adsb_queue.qsize(),
        'dump1090_path': find_dump1090(),
        'port_30003_open': check_dump1090_service() is not None
    })


@adsb_bp.route('/start', methods=['POST'])
def start_adsb():
    """Start ADS-B tracking."""
    global adsb_using_service

    with app_module.adsb_lock:
        if adsb_using_service:
            return jsonify({'status': 'already_running', 'message': 'ADS-B tracking already active'}), 409

    data = request.json or {}

    # Validate inputs
    try:
        gain = int(validate_gain(data.get('gain', '40')))
        device = validate_device_index(data.get('device', '0'))
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    # Check if dump1090 is already running externally (e.g., user started it manually)
    existing_service = check_dump1090_service()
    if existing_service:
        logger.info(f"Found existing dump1090 service at {existing_service}")
        adsb_using_service = True
        thread = threading.Thread(target=parse_sbs_stream, args=(existing_service,), daemon=True)
        thread.start()
        return jsonify({'status': 'started', 'message': 'Connected to existing dump1090 service'})

    # No existing service, need to start dump1090 ourselves
    dump1090_path = find_dump1090()

    if not dump1090_path:
        return jsonify({'status': 'error', 'message': 'dump1090 not found. Install dump1090/dump1090-fa or ensure it is in /usr/local/bin/'})

    # Kill any stale app-started process
    if app_module.adsb_process:
        try:
            app_module.adsb_process.terminate()
            app_module.adsb_process.wait(timeout=2)
        except Exception:
            pass
        app_module.adsb_process = None

    cmd = [dump1090_path, '--net', '--gain', str(gain), '--device-index', str(device), '--quiet']

    try:
        app_module.adsb_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        time.sleep(3)

        if app_module.adsb_process.poll() is not None:
            return jsonify({'status': 'error', 'message': 'dump1090 failed to start. Check RTL-SDR device permissions or if another process is using it.'})

        adsb_using_service = True
        thread = threading.Thread(target=parse_sbs_stream, args=('localhost:30003',), daemon=True)
        thread.start()

        return jsonify({'status': 'started', 'message': 'ADS-B tracking started'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@adsb_bp.route('/stop', methods=['POST'])
def stop_adsb():
    """Stop ADS-B tracking."""
    global adsb_using_service

    with app_module.adsb_lock:
        if app_module.adsb_process:
            app_module.adsb_process.terminate()
            try:
                app_module.adsb_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                app_module.adsb_process.kill()
            app_module.adsb_process = None
        adsb_using_service = False

    app_module.adsb_aircraft = {}
    return jsonify({'status': 'stopped'})


@adsb_bp.route('/stream')
def stream_adsb():
    """SSE stream for ADS-B aircraft."""
    def generate():
        last_keepalive = time.time()
        keepalive_interval = 30.0

        while True:
            try:
                msg = app_module.adsb_queue.get(timeout=1)
                last_keepalive = time.time()
                yield format_sse(msg)
            except queue.Empty:
                now = time.time()
                if now - last_keepalive >= keepalive_interval:
                    yield format_sse({'type': 'keepalive'})
                    last_keepalive = now

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@adsb_bp.route('/dashboard')
def adsb_dashboard():
    """Popout ADS-B dashboard."""
    return render_template('adsb_dashboard.html')
