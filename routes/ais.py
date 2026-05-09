"""AIS vessel tracking routes."""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time

from flask import Blueprint, Response, jsonify, render_template, request

import app as app_module
from config import DEFAULT_LATITUDE, DEFAULT_LONGITUDE, SHARED_OBSERVER_LOCATION_ENABLED
from utils.constants import (
    AIS_RECONNECT_DELAY,
    AIS_SOCKET_TIMEOUT,
    AIS_TCP_PORT,
    AIS_TERMINATE_TIMEOUT,
    AIS_UPDATE_INTERVAL,
    PROCESS_TERMINATE_TIMEOUT,
    SOCKET_BUFFER_SIZE,
    SSE_KEEPALIVE_INTERVAL,
    SSE_QUEUE_TIMEOUT,
)
from utils.event_pipeline import process_event
from utils.logging import get_logger
from utils.responses import api_error, api_success
from utils.sdr import SDRFactory, SDRType
from utils.sse import sse_stream_fanout
from utils.validation import validate_device_index, validate_gain, validate_ppm

logger = get_logger('intercept.ais')

ais_bp = Blueprint('ais', __name__, url_prefix='/ais')

# Track AIS state
ais_running = False
ais_connected = False
ais_messages_received = 0
ais_last_message_time = None
ais_active_device = None
ais_active_sdr_type: str | None = None
_ais_error_logged = True

# Common installation paths for AIS-catcher
AIS_CATCHER_PATHS = [
    '/usr/local/bin/AIS-catcher',
    '/usr/bin/AIS-catcher',
    '/opt/homebrew/bin/AIS-catcher',
    '/opt/homebrew/bin/aiscatcher',
]


def find_ais_catcher():
    """Find AIS-catcher binary, checking PATH and common locations."""
    # First try PATH
    for name in ['AIS-catcher', 'aiscatcher']:
        path = shutil.which(name)
        if path:
            return path
    # Check common installation paths
    for path in AIS_CATCHER_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def parse_ais_stream(port: int):
    """Parse JSON data from AIS-catcher TCP server."""
    global ais_running, ais_connected, ais_messages_received, ais_last_message_time, _ais_error_logged

    logger.info(f"AIS stream parser started, connecting to localhost:{port}")
    ais_connected = True
    ais_messages_received = 0
    _ais_error_logged = True

    while ais_running:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(AIS_SOCKET_TIMEOUT)
            sock.connect(('localhost', port))
            ais_connected = True
            _ais_error_logged = True
            logger.info("Connected to AIS-catcher TCP server")

            buffer = ""
            last_update = time.time()
            pending_updates = set()

            while ais_running:
                try:
                    data = sock.recv(SOCKET_BUFFER_SIZE).decode('utf-8', errors='ignore')
                    if not data:
                        logger.warning("AIS connection closed (no data)")
                        break
                    buffer += data

                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            msg = json.loads(line)
                            vessel = process_ais_message(msg)
                            if vessel:
                                mmsi = vessel.get('mmsi')
                                if mmsi:
                                    app_module.ais_vessels.set(mmsi, vessel)
                                    pending_updates.add(mmsi)
                                    ais_messages_received += 1
                                    ais_last_message_time = time.time()
                        except json.JSONDecodeError:
                            if ais_messages_received < 5:
                                logger.debug(f"Invalid JSON: {line[:100]}")

                    # Batch updates
                    now = time.time()
                    if now - last_update >= AIS_UPDATE_INTERVAL:
                        for mmsi in pending_updates:
                            if mmsi in app_module.ais_vessels:
                                _vessel_snap = app_module.ais_vessels[mmsi]
                                with contextlib.suppress(queue.Full):
                                    app_module.ais_queue.put_nowait({
                                        'type': 'vessel',
                                        **_vessel_snap
                                    })
                                # Geofence check
                                _v_lat = _vessel_snap.get('lat')
                                _v_lon = _vessel_snap.get('lon')
                                if _v_lat and _v_lon:
                                    try:
                                        from utils.geofence import get_geofence_manager
                                        for _gf_evt in get_geofence_manager().check_position(
                                            mmsi, 'vessel', _v_lat, _v_lon,
                                            {'name': _vessel_snap.get('name'), 'ship_type': _vessel_snap.get('ship_type_text')}
                                        ):
                                            process_event('ais', _gf_evt, 'geofence')
                                    except Exception:
                                        pass
                        pending_updates.clear()
                        last_update = now

                except socket.timeout:
                    continue

            ais_connected = False
        except OSError as e:
            ais_connected = False
            if not _ais_error_logged:
                logger.warning(f"AIS connection error: {e}, reconnecting...")
                _ais_error_logged = True
            time.sleep(AIS_RECONNECT_DELAY)
        finally:
            if sock:
                with contextlib.suppress(OSError):
                    sock.close()

    ais_connected = False
    logger.info("AIS stream parser stopped")


def process_ais_message(msg: dict) -> dict | None:
    """Process AIS-catcher JSON message and extract vessel data."""
    # AIS-catcher outputs different message types
    # We're interested in position reports and static data

    mmsi = msg.get('mmsi')
    if not mmsi:
        return None

    mmsi = str(mmsi)

    # Get existing vessel data or create new
    vessel = app_module.ais_vessels.get(mmsi) or {'mmsi': mmsi}

    # Extract common fields
    # AIS-catcher JSON_FULL uses 'longitude'/'latitude', but some versions use 'lon'/'lat'
    lat_val = msg.get('latitude') or msg.get('lat')
    lon_val = msg.get('longitude') or msg.get('lon')
    if lat_val is not None and lon_val is not None:
        try:
            lat = float(lat_val)
            lon = float(lon_val)
            # Validate coordinates (AIS uses 181 for unavailable)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                vessel['lat'] = lat
                vessel['lon'] = lon
        except (ValueError, TypeError):
            pass

    # Speed over ground (knots)
    if 'speed' in msg:
        try:
            speed = float(msg['speed'])
            if speed < 102.3:  # 102.3 = not available
                vessel['speed'] = round(speed, 1)
        except (ValueError, TypeError):
            pass

    # Course over ground (degrees)
    if 'course' in msg:
        try:
            course = float(msg['course'])
            if course < 360:  # 360 = not available
                vessel['course'] = round(course, 1)
        except (ValueError, TypeError):
            pass

    # True heading (degrees)
    if 'heading' in msg:
        try:
            heading = int(msg['heading'])
            if heading < 511:  # 511 = not available
                vessel['heading'] = heading
        except (ValueError, TypeError):
            pass

    # Navigation status
    if 'status' in msg:
        vessel['nav_status'] = msg['status']
    if 'status_text' in msg:
        vessel['nav_status_text'] = msg['status_text']

    # Vessel name (from Type 5 or Type 24 messages)
    if 'shipname' in msg:
        name = msg['shipname'].strip().strip('@')
        if name:
            vessel['name'] = name

    # Callsign
    if 'callsign' in msg:
        callsign = msg['callsign'].strip().strip('@')
        if callsign:
            vessel['callsign'] = callsign

    # Ship type
    if 'shiptype' in msg:
        vessel['ship_type'] = msg['shiptype']
    if 'shiptype_text' in msg:
        vessel['ship_type_text'] = msg['shiptype_text']

    # Destination
    if 'destination' in msg:
        dest = msg['destination'].strip().strip('@')
        if dest:
            vessel['destination'] = dest

    # ETA
    if 'eta' in msg:
        vessel['eta'] = msg['eta']

    # Dimensions
    if 'to_bow' in msg and 'to_stern' in msg:
        try:
            length = int(msg['to_bow']) + int(msg['to_stern'])
            if length > 0:
                vessel['length'] = length
        except (ValueError, TypeError):
            pass

    if 'to_port' in msg and 'to_starboard' in msg:
        try:
            width = int(msg['to_port']) + int(msg['to_starboard'])
            if width > 0:
                vessel['width'] = width
        except (ValueError, TypeError):
            pass

    # Draught
    if 'draught' in msg:
        try:
            draught = float(msg['draught'])
            if draught > 0:
                vessel['draught'] = draught
        except (ValueError, TypeError):
            pass

    # Rate of turn
    if 'turn' in msg:
        try:
            turn = float(msg['turn'])
            if -127 <= turn <= 127:  # Valid range
                vessel['rate_of_turn'] = turn
        except (ValueError, TypeError):
            pass

    # Message type for debugging
    if 'type' in msg:
        vessel['last_msg_type'] = msg['type']

    # Timestamp
    vessel['last_seen'] = time.time()

    # Check for DSC DISTRESS matching this MMSI
    try:
        for _dsc_key, _dsc_msg in app_module.dsc_messages.items():
            if (str(_dsc_msg.get('source_mmsi', '')) == mmsi
                    and _dsc_msg.get('category', '').upper() == 'DISTRESS'):
                vessel['dsc_distress'] = True
                break
    except Exception:
        pass

    return vessel


@ais_bp.route('/tools')
def check_ais_tools():
    """Check for AIS decoding tools and hardware."""
    has_ais_catcher = find_ais_catcher() is not None

    # Check what SDR hardware is detected
    devices = SDRFactory.detect_devices()
    has_rtlsdr = any(d.sdr_type == SDRType.RTL_SDR for d in devices)

    return jsonify({
        'ais_catcher': has_ais_catcher,
        'ais_catcher_path': find_ais_catcher(),
        'has_rtlsdr': has_rtlsdr,
        'device_count': len(devices)
    })


@ais_bp.route('/status')
def ais_status():
    """Get AIS tracking status for debugging."""
    process_running = False
    if app_module.ais_process:
        process_running = app_module.ais_process.poll() is None

    return jsonify({
        'tracking_active': ais_running,
        'active_device': ais_active_device,
        'connected': ais_connected,
        'messages_received': ais_messages_received,
        'last_message_time': ais_last_message_time,
        'vessel_count': len(app_module.ais_vessels),
        'vessels': dict(app_module.ais_vessels),
        'queue_size': app_module.ais_queue.qsize(),
        'ais_catcher_path': find_ais_catcher(),
        'process_running': process_running
    })


@ais_bp.route('/start', methods=['POST'])
def start_ais():
    """Start AIS tracking."""
    global ais_running, ais_active_device, ais_active_sdr_type

    with app_module.ais_lock:
        if ais_running:
            return api_error('AIS tracking already active', 409)

    data = request.json or {}

    # Validate inputs
    try:
        gain = int(validate_gain(data.get('gain', '40')))
        device = validate_device_index(data.get('device', '0'))
        ppm = int(validate_ppm(data.get('ppm', '0')))
    except ValueError as e:
        return api_error(str(e), 400)

    # Find AIS-catcher
    ais_catcher_path = find_ais_catcher()
    if not ais_catcher_path:
        return api_error('AIS-catcher not found. Install from https://github.com/jvde-github/AIS-catcher/releases', 400)

    # Get SDR type from request
    sdr_type_str = data.get('sdr_type', 'rtlsdr')
    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR

    # Kill any existing process
    if app_module.ais_process:
        try:
            pgid = os.getpgid(app_module.ais_process.pid)
            os.killpg(pgid, 15)
            app_module.ais_process.wait(timeout=PROCESS_TERMINATE_TIMEOUT)
        except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
            try:
                pgid = os.getpgid(app_module.ais_process.pid)
                os.killpg(pgid, 9)
            except (ProcessLookupError, OSError):
                pass
        app_module.ais_process = None
        logger.info("Killed existing AIS process")

    # Check if device is available
    device_int = int(device)
    error = app_module.claim_sdr_device(device_int, 'ais', sdr_type_str)
    if error:
        return api_error(error, 409, error_type='DEVICE_BUSY')

    # Build command using SDR abstraction
    sdr_device = SDRFactory.create_default_device(sdr_type, index=device)
    builder = SDRFactory.get_builder(sdr_type)

    bias_t = data.get('bias_t', False)
    tcp_port = AIS_TCP_PORT

    # Optional UDP NMEA forwarding (e.g. for OpenCPN on port 10110)
    udp_host = data.get('udp_host') or None
    udp_port = None
    if udp_host:
        try:
            udp_port = int(data.get('udp_port', 10110))
            if not 1 <= udp_port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            return api_error('Invalid udp_port (1-65535)', 400)

    cmd = builder.build_ais_command(
        device=sdr_device,
        gain=float(gain),
        bias_t=bias_t,
        tcp_port=tcp_port,
        udp_host=udp_host,
        udp_port=udp_port,
        ppm=ppm,
    )

    # Use the found AIS-catcher path
    cmd[0] = ais_catcher_path

    try:
        logger.info(f"Starting AIS-catcher with device {device}: {' '.join(cmd)}")
        app_module.ais_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True
        )

        # Wait for process to start
        time.sleep(2.0)

        if app_module.ais_process.poll() is not None:
            # Release device on failure
            app_module.release_sdr_device(device_int, sdr_type_str)
            stderr_output = ''
            if app_module.ais_process.stderr:
                with contextlib.suppress(Exception):
                    stderr_output = app_module.ais_process.stderr.read().decode('utf-8', errors='ignore').strip()
            if stderr_output:
                logger.error(f"AIS-catcher stderr:\n{stderr_output}")
            error_msg = 'AIS-catcher failed to start. Check SDR device connection.'
            if stderr_output:
                error_msg += f' Error: {stderr_output[:500]}'
            return api_error(error_msg, 500)

        ais_running = True
        ais_active_device = device
        ais_active_sdr_type = sdr_type_str

        # Start TCP parser thread
        thread = threading.Thread(target=parse_ais_stream, args=(tcp_port,), daemon=True)
        thread.start()

        return jsonify({
            'status': 'started',
            'message': 'AIS tracking started',
            'device': device,
            'port': tcp_port
        })
    except Exception as e:
        # Release device on failure
        app_module.release_sdr_device(device_int, sdr_type_str)
        logger.error(f"Failed to start AIS-catcher: {e}")
        return api_error(str(e), 500)


@ais_bp.route('/stop', methods=['POST'])
def stop_ais():
    """Stop AIS tracking."""
    global ais_running, ais_active_device, ais_active_sdr_type

    with app_module.ais_lock:
        if app_module.ais_process:
            try:
                pgid = os.getpgid(app_module.ais_process.pid)
                os.killpg(pgid, 15)
                app_module.ais_process.wait(timeout=AIS_TERMINATE_TIMEOUT)
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                try:
                    pgid = os.getpgid(app_module.ais_process.pid)
                    os.killpg(pgid, 9)
                except (ProcessLookupError, OSError):
                    pass
            app_module.ais_process = None
            logger.info("AIS process stopped")

        # Release device from registry
        if ais_active_device is not None:
            app_module.release_sdr_device(ais_active_device, ais_active_sdr_type or 'rtlsdr')

        ais_running = False
        ais_active_device = None
        ais_active_sdr_type = None

    app_module.ais_vessels.clear()
    return jsonify({'status': 'stopped'})


@ais_bp.route('/stream')
def stream_ais():
    """SSE stream for AIS vessels."""
    def _on_msg(msg: dict[str, Any]) -> None:
        process_event('ais', msg, msg.get('type'))

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.ais_queue,
            channel_key='ais',
            timeout=SSE_QUEUE_TIMEOUT,
            keepalive_interval=SSE_KEEPALIVE_INTERVAL,
            on_message=_on_msg,
        ),
        mimetype='text/event-stream',
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@ais_bp.route('/vessel/<mmsi>/dsc')
def get_vessel_dsc(mmsi: str):
    """Get DSC messages associated with a vessel MMSI."""
    if not mmsi or not mmsi.isdigit():
        return api_error('Invalid MMSI', 400)

    matches = []
    try:
        for _key, msg in app_module.dsc_messages.items():
            if str(msg.get('source_mmsi', '')) == mmsi:
                matches.append(dict(msg))
    except Exception:
        pass

    return api_success(data={'mmsi': mmsi, 'dsc_messages': matches})


@ais_bp.route('/vessels')
def ais_vessels():
    """Export current AIS vessel data as JSON.

    Returns a snapshot of all tracked vessels suitable for integration
    with external tools (OpenCPN, ship tracking apps, etc.).

    Query parameters:
        mmsi: Filter to a specific MMSI (optional)

    Returns:
        JSON with vessel list and metadata.
    """
    vessels = dict(app_module.ais_vessels)

    mmsi_filter = request.args.get('mmsi')
    if mmsi_filter:
        vessels = {k: v for k, v in vessels.items() if str(k) == str(mmsi_filter)}

    return jsonify({
        'count': len(vessels),
        'vessels': list(vessels.values()),
    })


@ais_bp.route('/dashboard')
def ais_dashboard():
    """Popout AIS dashboard."""
    embedded = request.args.get('embedded', 'false') == 'true'
    return render_template(
        'ais_dashboard.html',
        shared_observer_location=SHARED_OBSERVER_LOCATION_ENABLED,
        default_latitude=DEFAULT_LATITUDE,
        default_longitude=DEFAULT_LONGITUDE,
        embedded=embedded,
    )
