"""ADS-B aircraft tracking routes."""

from __future__ import annotations

import csv
import io
import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, Response, jsonify, make_response, render_template, request

from utils.responses import api_error, api_success

# psycopg2 is optional - only needed for PostgreSQL history persistence
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None  # type: ignore
    RealDictCursor = None  # type: ignore
    PSYCOPG2_AVAILABLE = False

import contextlib

import app as app_module
from config import (
    ADSB_AUTO_START,
    ADSB_DB_HOST,
    ADSB_DB_NAME,
    ADSB_DB_PASSWORD,
    ADSB_DB_PORT,
    ADSB_DB_USER,
    ADSB_HISTORY_ENABLED,
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    SHARED_OBSERVER_LOCATION_ENABLED,
)
from utils import aircraft_db
from utils.acars_translator import translate_message
from utils.adsb_history import _ensure_adsb_schema, adsb_history_writer, adsb_snapshot_writer
from utils.constants import (
    ADSB_SBS_PORT,
    ADSB_TERMINATE_TIMEOUT,
    ADSB_UPDATE_INTERVAL,
    DUMP1090_START_WAIT,
    PROCESS_TERMINATE_TIMEOUT,
    SBS_RECONNECT_DELAY,
    SBS_SOCKET_TIMEOUT,
    SOCKET_BUFFER_SIZE,
    SOCKET_CONNECT_TIMEOUT,
    SSE_KEEPALIVE_INTERVAL,
    SSE_QUEUE_TIMEOUT,
)
from utils.event_pipeline import process_event
from utils.flight_correlator import get_flight_correlator
from utils.logging import adsb_logger as logger
from utils.process import cleanup_stale_dump1090, clear_dump1090_pid, write_dump1090_pid
from utils.sdr import SDRFactory, SDRType
from utils.sse import format_sse
from utils.validation import validate_device_index, validate_gain, validate_rtl_tcp_host, validate_rtl_tcp_port

adsb_bp = Blueprint('adsb', __name__, url_prefix='/adsb')

# Track if using service
adsb_using_service = False
adsb_connected = False
adsb_messages_received = 0
adsb_last_message_time = None
adsb_bytes_received = 0
adsb_lines_received = 0
adsb_active_device = None  # Track which device index is being used
adsb_active_sdr_type: str | None = None
adsb_bias_t_active = False  # Track if bias-t was enabled at start (for cleanup on stop)
_sbs_error_logged = False  # Suppress repeated connection error logs

# Track ICAOs already looked up in aircraft database (avoid repeated lookups)
_looked_up_icaos: set[str] = set()

# Per-client SSE queues for ADS-B stream fanout.
_adsb_stream_subscribers: set[queue.Queue] = set()
_adsb_stream_subscribers_lock = threading.Lock()
_ADSB_STREAM_CLIENT_QUEUE_SIZE = 500

# Load aircraft database at module init
aircraft_db.load_database()

# Common installation paths for dump1090 (when not in PATH)
DUMP1090_PATHS = [
    # Homebrew on Apple Silicon (M1/M2/M3)
    '/opt/homebrew/bin/dump1090',
    '/opt/homebrew/bin/dump1090-fa',
    '/opt/homebrew/bin/dump1090-mutability',
    # Homebrew on Intel Mac
    '/usr/local/bin/dump1090',
    '/usr/local/bin/dump1090-fa',
    '/usr/local/bin/dump1090-mutability',
    # Linux system paths
    '/usr/bin/dump1090',
    '/usr/bin/dump1090-fa',
    '/usr/bin/dump1090-mutability',
]


def _get_part(parts: list[str], index: int) -> str | None:
    if len(parts) <= index:
        return None
    value = parts[index].strip()
    return value or None


def _parse_sbs_timestamp(date_str: str | None, time_str: str | None) -> datetime | None:
    if not date_str or not time_str:
        return None
    combined = f"{date_str} {time_str}"
    for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(combined, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _build_history_record(
    parts: list[str],
    msg_type: str,
    icao: str,
    msg_time: datetime | None,
    logged_time: datetime | None,
    service_addr: str,
    raw_line: str,
) -> dict[str, Any]:
    return {
        'received_at': datetime.now(timezone.utc),
        'msg_time': msg_time,
        'logged_time': logged_time,
        'icao': icao,
        'msg_type': _parse_int(msg_type),
        'callsign': _get_part(parts, 10),
        'altitude': _parse_int(_get_part(parts, 11)),
        'speed': _parse_int(_get_part(parts, 12)),
        'heading': _parse_int(_get_part(parts, 13)),
        'vertical_rate': _parse_int(_get_part(parts, 16)),
        'lat': _parse_float(_get_part(parts, 14)),
        'lon': _parse_float(_get_part(parts, 15)),
        'squawk': _get_part(parts, 17),
        'session_id': _get_part(parts, 2),
        'aircraft_id': _get_part(parts, 3),
        'flight_id': _get_part(parts, 5),
        'raw_line': raw_line,
        'source_host': service_addr,
    }


_history_schema_checked = False


def _get_history_connection():
    return psycopg2.connect(
        host=ADSB_DB_HOST,
        port=ADSB_DB_PORT,
        dbname=ADSB_DB_NAME,
        user=ADSB_DB_USER,
        password=ADSB_DB_PASSWORD,
    )


def _ensure_history_schema() -> None:
    global _history_schema_checked
    if _history_schema_checked:
        return
    try:
        with _get_history_connection() as conn:
            _ensure_adsb_schema(conn)
        _history_schema_checked = True
    except Exception as exc:
        logger.warning("ADS-B schema check failed: %s", exc)


MILITARY_ICAO_RANGES = [
    (0xADF7C0, 0xADFFFF),  # US
    (0xAE0000, 0xAEFFFF),  # US
    (0x3F4000, 0x3F7FFF),  # FR
    (0x43C000, 0x43CFFF),  # UK
    (0x3D0000, 0x3DFFFF),  # DE
    (0x501C00, 0x501FFF),  # NATO
]

MILITARY_CALLSIGN_PREFIXES = (
    'REACH', 'JAKE', 'DOOM', 'IRON', 'HAWK', 'VIPER', 'COBRA', 'THUNDER',
    'SHADOW', 'NIGHT', 'STEEL', 'GRIM', 'REAPER', 'BLADE', 'STRIKE',
    'RCH', 'CNV', 'MCH', 'EVAC', 'TOPCAT', 'ASCOT', 'RRR', 'HRK',
    'NAVY', 'ARMY', 'USAF', 'RAF', 'RCAF', 'RAAF', 'IAF', 'PAF',
)


def _is_military_aircraft(icao: str, callsign: str | None) -> bool:
    """Return True if the ICAO hex or callsign indicates a military aircraft."""
    try:
        hex_val = int(icao, 16)
        for start, end in MILITARY_ICAO_RANGES:
            if start <= hex_val <= end:
                return True
    except (ValueError, TypeError):
        pass
    if callsign:
        upper = callsign.upper().strip()
        for prefix in MILITARY_CALLSIGN_PREFIXES:
            if upper.startswith(prefix):
                return True
    return False


def _parse_int_param(value: str | None, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (ValueError, TypeError):
        parsed = default
    if min_value is not None:
        parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.endswith('Z'):
        cleaned = f"{cleaned[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_export_scope(
    args: Any,
) -> tuple[str, int, datetime | None, datetime | None]:
    scope = str(args.get('scope') or 'window').strip().lower()
    if scope not in {'window', 'all', 'custom'}:
        scope = 'window'
    since_minutes = _parse_int_param(args.get('since_minutes'), 1440, 1, 525600)
    start = _parse_iso_datetime(args.get('start'))
    end = _parse_iso_datetime(args.get('end'))
    if scope == 'custom' and (start is None or end is None or end <= start):
        scope = 'window'
    return scope, since_minutes, start, end


def _add_time_filter(
    *,
    where_parts: list[str],
    params: list[Any],
    scope: str,
    timestamp_field: str,
    since_minutes: int,
    start: datetime | None,
    end: datetime | None,
) -> None:
    if scope == 'all':
        return
    if scope == 'custom' and start is not None and end is not None:
        where_parts.append(f"{timestamp_field} >= %s AND {timestamp_field} < %s")
        params.extend([start, end])
        return
    where_parts.append(f"{timestamp_field} >= NOW() - INTERVAL %s")
    params.append(f'{since_minutes} minutes')


def _serialize_export_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _rows_to_serializable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _serialize_export_value(value) for key, value in row.items()} for row in rows]


def _build_export_csv(
    *,
    exported_at: str,
    scope: str,
    since_minutes: int | None,
    icao: str,
    search: str,
    classification: str,
    messages: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    export_type: str,
) -> str:
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(['Exported At', exported_at])
    writer.writerow(['Scope', scope])
    if since_minutes is not None:
        writer.writerow(['Since Minutes', since_minutes])
    if icao:
        writer.writerow(['ICAO Filter', icao])
    if search:
        writer.writerow(['Search Filter', search])
    if classification != 'all':
        writer.writerow(['Classification', classification])
    writer.writerow([])

    def write_section(title: str, rows: list[dict[str, Any]], columns: list[str]) -> None:
        writer.writerow([title])
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_serialize_export_value(row.get(col)) for col in columns])
        writer.writerow([])

    if export_type in {'messages', 'all'}:
        write_section(
            'Messages',
            messages,
            [
                'received_at', 'msg_time', 'logged_time', 'icao', 'msg_type', 'callsign',
                'altitude', 'speed', 'heading', 'vertical_rate', 'lat', 'lon', 'squawk',
                'session_id', 'aircraft_id', 'flight_id', 'source_host', 'raw_line',
            ],
        )

    if export_type in {'snapshots', 'all'}:
        write_section(
            'Snapshots',
            snapshots,
            [
                'captured_at', 'icao', 'callsign', 'registration', 'type_code', 'type_desc',
                'altitude', 'speed', 'heading', 'vertical_rate', 'lat', 'lon', 'squawk',
                'source_host',
            ],
        )

    if export_type in {'sessions', 'all'}:
        write_section(
            'Sessions',
            sessions,
            [
                'id', 'started_at', 'ended_at', 'device_index', 'sdr_type', 'remote_host',
                'remote_port', 'start_source', 'stop_source', 'started_by', 'stopped_by', 'notes',
            ],
        )

    return output.getvalue()


def _broadcast_adsb_update(payload: dict[str, Any]) -> None:
    """Fan out a payload to all active ADS-B SSE subscribers."""
    with _adsb_stream_subscribers_lock:
        subscribers = tuple(_adsb_stream_subscribers)

    for subscriber in subscribers:
        try:
            subscriber.put_nowait(payload)
        except queue.Full:
            # Drop oldest queued event for that client and try once more.
            try:
                subscriber.get_nowait()
                subscriber.put_nowait(payload)
            except (queue.Empty, queue.Full):
                # Client queue remains saturated; skip this payload.
                continue


def _adsb_stream_queue_depth() -> int:
    """Best-effort aggregate queue depth across connected ADS-B SSE clients."""
    with _adsb_stream_subscribers_lock:
        subscribers = tuple(_adsb_stream_subscribers)
    return sum(subscriber.qsize() for subscriber in subscribers)


def _get_active_session() -> dict[str, Any] | None:
    if not ADSB_HISTORY_ENABLED or not PSYCOPG2_AVAILABLE:
        return None
    _ensure_history_schema()
    try:
        with _get_history_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                    SELECT *
                    FROM adsb_sessions
                    WHERE ended_at IS NULL
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
            )
            return cur.fetchone()
    except Exception as exc:
        logger.warning("ADS-B session lookup failed: %s", exc)
        return None


def _record_session_start(
    *,
    device_index: int | None,
    sdr_type: str | None,
    remote_host: str | None,
    remote_port: int | None,
    start_source: str | None,
    started_by: str | None,
) -> dict[str, Any] | None:
    if not ADSB_HISTORY_ENABLED or not PSYCOPG2_AVAILABLE:
        return None
    _ensure_history_schema()
    try:
        with _get_history_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                    INSERT INTO adsb_sessions (
                        device_index,
                        sdr_type,
                        remote_host,
                        remote_port,
                        start_source,
                        started_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                (
                    device_index,
                    sdr_type,
                    remote_host,
                    remote_port,
                    start_source,
                    started_by,
                ),
            )
            return cur.fetchone()
    except Exception as exc:
        logger.warning("ADS-B session start record failed: %s", exc)
        return None


def _record_session_stop(*, stop_source: str | None, stopped_by: str | None) -> dict[str, Any] | None:
    if not ADSB_HISTORY_ENABLED or not PSYCOPG2_AVAILABLE:
        return None
    _ensure_history_schema()
    try:
        with _get_history_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                    UPDATE adsb_sessions
                    SET ended_at = NOW(),
                        stop_source = COALESCE(%s, stop_source),
                        stopped_by = COALESCE(%s, stopped_by)
                    WHERE ended_at IS NULL
                    RETURNING *
                    """,
                (stop_source, stopped_by),
            )
            return cur.fetchone()
    except Exception as exc:
        logger.warning("ADS-B session stop record failed: %s", exc)
        return None

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
    """Check if dump1090 SBS port is available."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(SOCKET_CONNECT_TIMEOUT)
        result = sock.connect_ex(('localhost', ADSB_SBS_PORT))
        sock.close()
        if result == 0:
            return f'localhost:{ADSB_SBS_PORT}'
    except OSError:
        pass
    return None


def parse_sbs_stream(service_addr):
    """Parse SBS format data from dump1090 SBS port."""
    global adsb_using_service, adsb_connected, adsb_messages_received, adsb_last_message_time, adsb_bytes_received, adsb_lines_received, _sbs_error_logged

    adsb_history_writer.start()
    adsb_snapshot_writer.start()

    host, port = service_addr.split(':')
    port = int(port)

    logger.info(f"SBS stream parser started, connecting to {host}:{port}")
    adsb_connected = False
    adsb_messages_received = 0
    _sbs_error_logged = False

    while adsb_using_service:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(SBS_SOCKET_TIMEOUT)
            sock.connect((host, port))
            adsb_connected = True
            _sbs_error_logged = False  # Reset so we log next error
            logger.info("Connected to SBS stream")

            buffer = ""
            last_update = time.time()
            pending_updates = set()
            adsb_bytes_received = 0
            adsb_lines_received = 0

            def flush_pending_updates(force: bool = False) -> None:
                nonlocal last_update
                if not pending_updates:
                    return

                now = time.time()
                if not force and now - last_update < ADSB_UPDATE_INTERVAL:
                    return

                captured_at = datetime.now(timezone.utc)
                for update_icao in tuple(pending_updates):
                    if update_icao in app_module.adsb_aircraft:
                        snapshot = app_module.adsb_aircraft[update_icao]
                        _broadcast_adsb_update({
                            'type': 'aircraft',
                            **snapshot
                        })
                        adsb_snapshot_writer.enqueue({
                            'captured_at': captured_at,
                            'icao': update_icao,
                            'callsign': snapshot.get('callsign'),
                            'registration': snapshot.get('registration'),
                            'type_code': snapshot.get('type_code'),
                            'type_desc': snapshot.get('type_desc'),
                            'altitude': snapshot.get('altitude'),
                            'speed': snapshot.get('speed'),
                            'heading': snapshot.get('heading'),
                            'vertical_rate': snapshot.get('vertical_rate'),
                            'lat': snapshot.get('lat'),
                            'lon': snapshot.get('lon'),
                            'squawk': snapshot.get('squawk'),
                            'source_host': service_addr,
                            'snapshot': snapshot,
                        })
                        # Geofence check
                        _gf_lat = snapshot.get('lat')
                        _gf_lon = snapshot.get('lon')
                        if _gf_lat is not None and _gf_lon is not None:
                            try:
                                from utils.geofence import get_geofence_manager
                                for _gf_evt in get_geofence_manager().check_position(
                                    update_icao, 'aircraft', _gf_lat, _gf_lon,
                                    {'callsign': snapshot.get('callsign'), 'altitude': snapshot.get('altitude')}
                                ):
                                    process_event('adsb', _gf_evt, 'geofence')
                            except Exception:
                                pass

                pending_updates.clear()
                last_update = now

            while adsb_using_service:
                try:
                    data = sock.recv(SOCKET_BUFFER_SIZE).decode('utf-8', errors='ignore')
                    if not data:
                        flush_pending_updates(force=True)
                        logger.warning("SBS connection closed (no data)")
                        break
                    adsb_bytes_received += len(data)
                    buffer += data

                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue

                        adsb_lines_received += 1
                        # Log first few lines for debugging
                        if adsb_lines_received <= 3:
                            logger.info(f"SBS line {adsb_lines_received}: {line[:100]}")

                        parts = line.split(',')
                        if len(parts) < 11 or parts[0] != 'MSG':
                            if adsb_lines_received <= 5:
                                logger.debug(f"Skipping non-MSG line: {line[:50]}")
                            continue

                        msg_type = parts[1]
                        icao = parts[4].upper()
                        if not icao:
                            continue

                        msg_time = _parse_sbs_timestamp(_get_part(parts, 6), _get_part(parts, 7))
                        logged_time = _parse_sbs_timestamp(_get_part(parts, 8), _get_part(parts, 9))
                        history_record = _build_history_record(
                            parts=parts,
                            msg_type=msg_type,
                            icao=icao,
                            msg_time=msg_time,
                            logged_time=logged_time,
                            service_addr=service_addr,
                            raw_line=line,
                        )
                        adsb_history_writer.enqueue(history_record)

                        aircraft = app_module.adsb_aircraft.get(icao) or {'icao': icao}

                        # Look up aircraft type from database (once per ICAO)
                        if icao not in _looked_up_icaos:
                            _looked_up_icaos.add(icao)
                            db_info = aircraft_db.lookup(icao)
                            if db_info:
                                if db_info['registration']:
                                    aircraft['registration'] = db_info['registration']
                                if db_info['type_code']:
                                    aircraft['type_code'] = db_info['type_code']
                                if db_info['type_desc']:
                                    aircraft['type_desc'] = db_info['type_desc']

                        if msg_type == '1' and len(parts) > 10:
                            callsign = parts[10].strip()
                            if callsign:
                                aircraft['callsign'] = callsign

                        elif msg_type == '3' and len(parts) > 15:
                            if parts[11]:
                                with contextlib.suppress(ValueError, TypeError):
                                    aircraft['altitude'] = int(float(parts[11]))
                            if parts[14] and parts[15]:
                                try:
                                    aircraft['lat'] = float(parts[14])
                                    aircraft['lon'] = float(parts[15])
                                except (ValueError, TypeError):
                                    pass

                        elif msg_type == '4' and len(parts) > 16:
                            if parts[12]:
                                with contextlib.suppress(ValueError, TypeError):
                                    aircraft['speed'] = int(float(parts[12]))
                            if parts[13]:
                                with contextlib.suppress(ValueError, TypeError):
                                    aircraft['heading'] = int(float(parts[13]))
                            if parts[16]:
                                try:
                                    aircraft['vertical_rate'] = int(float(parts[16]))
                                    if abs(aircraft['vertical_rate']) > 4000:
                                        process_event('adsb', {
                                            'type': 'vertical_rate_anomaly', 'icao': icao,
                                            'callsign': aircraft.get('callsign', ''),
                                            'vertical_rate': aircraft['vertical_rate'],
                                        }, 'vertical_rate_anomaly')
                                except (ValueError, TypeError):
                                    pass

                        elif msg_type == '5' and len(parts) > 11:
                            if parts[10]:
                                callsign = parts[10].strip()
                                if callsign:
                                    aircraft['callsign'] = callsign
                            if parts[11]:
                                with contextlib.suppress(ValueError, TypeError):
                                    aircraft['altitude'] = int(float(parts[11]))

                        elif msg_type == '6' and len(parts) > 17:
                            if parts[17]:
                                aircraft['squawk'] = parts[17]
                                sq = parts[17].strip()
                                _EMERGENCY_SQUAWKS = {'7700': 'General Emergency', '7600': 'Comms Failure', '7500': 'Hijack'}
                                if sq in _EMERGENCY_SQUAWKS:
                                    process_event('adsb', {
                                        'type': 'squawk_emergency', 'icao': icao,
                                        'callsign': aircraft.get('callsign', ''),
                                        'squawk': sq, 'meaning': _EMERGENCY_SQUAWKS[sq],
                                    }, 'squawk_emergency')

                        elif msg_type == '2' and len(parts) > 15:
                            if parts[11]:
                                with contextlib.suppress(ValueError, TypeError):
                                    aircraft['altitude'] = int(float(parts[11]))
                            if parts[12]:
                                with contextlib.suppress(ValueError, TypeError):
                                    aircraft['speed'] = int(float(parts[12]))
                            if parts[13]:
                                with contextlib.suppress(ValueError, TypeError):
                                    aircraft['heading'] = int(float(parts[13]))
                            if parts[14] and parts[15]:
                                try:
                                    aircraft['lat'] = float(parts[14])
                                    aircraft['lon'] = float(parts[15])
                                except (ValueError, TypeError):
                                    pass

                        app_module.adsb_aircraft.set(icao, aircraft)
                        pending_updates.add(icao)
                        adsb_messages_received += 1
                        adsb_last_message_time = time.time()
                        flush_pending_updates()

                except socket.timeout:
                    flush_pending_updates()
                    continue

            flush_pending_updates(force=True)
            adsb_connected = False
        except OSError as e:
            adsb_connected = False
            if not _sbs_error_logged:
                logger.warning(f"SBS connection error: {e}, reconnecting...")
                _sbs_error_logged = True
            time.sleep(SBS_RECONNECT_DELAY)
        finally:
            if sock:
                with contextlib.suppress(OSError):
                    sock.close()

    adsb_connected = False
    logger.info("SBS stream parser stopped")


@adsb_bp.route('/tools')
def check_adsb_tools():
    """Check for ADS-B decoding tools and hardware."""
    # Check available decoders
    has_dump1090 = find_dump1090() is not None
    has_readsb = shutil.which('readsb') is not None
    has_rtl_adsb = shutil.which('rtl_adsb') is not None

    return jsonify({
        'dump1090': has_dump1090,
        'readsb': has_readsb,
        'rtl_adsb': has_rtl_adsb,
        'has_rtlsdr': None,
        'has_soapy_sdr': None,
        'soapy_types': [],
        'needs_readsb': False
    })


@adsb_bp.route('/status')
def adsb_status():
    """Get ADS-B tracking status for debugging."""
    # Check if dump1090 process is still running
    dump1090_running = False
    if app_module.adsb_process:
        dump1090_running = app_module.adsb_process.poll() is None

    return jsonify({
        'tracking_active': adsb_using_service,
        'active_device': adsb_active_device,
        'connected_to_sbs': adsb_connected,
        'messages_received': adsb_messages_received,
        'bytes_received': adsb_bytes_received,
        'lines_received': adsb_lines_received,
        'last_message_time': adsb_last_message_time,
        'aircraft_count': len(app_module.adsb_aircraft),
        'aircraft': dict(app_module.adsb_aircraft),  # Full aircraft data
        'queue_size': _adsb_stream_queue_depth(),
        'dump1090_path': find_dump1090(),
        'dump1090_running': dump1090_running,
        'port_30003_open': check_dump1090_service() is not None
    })


@adsb_bp.route('/aircraft')
def adsb_aircraft_export():
    """Export current ADS-B aircraft data as JSON.

    Returns a snapshot of all tracked aircraft suitable for integration
    with external tools. For SBS (BaseStation) format, connect directly
    to port 30003 which dump1090 exposes natively.

    Query parameters:
        icao: Filter to a specific ICAO hex code (optional)
        military: 'true' to return only military aircraft (optional)

    Returns:
        JSON with aircraft list and metadata.
    """
    aircraft = dict(app_module.adsb_aircraft)

    icao_filter = request.args.get('icao', '').upper()
    if icao_filter:
        aircraft = {k: v for k, v in aircraft.items() if k.upper() == icao_filter}

    if request.args.get('military') == 'true':
        try:
            from utils.military_icao import is_military_icao
            aircraft = {k: v for k, v in aircraft.items() if is_military_icao(k)}
        except ImportError:
            pass

    return jsonify({
        'count': len(aircraft),
        'aircraft': list(aircraft.values()),
        'sbs_port': 30003,  # dump1090 SBS stream for tools like Virtual Radar Server
    })


@adsb_bp.route('/session')
def adsb_session():
    """Get ADS-B session status and uptime."""
    session = _get_active_session()
    uptime_seconds = None
    if session and session.get('started_at'):
        started_at = session['started_at']
        if isinstance(started_at, datetime):
            uptime_seconds = int((datetime.now(timezone.utc) - started_at).total_seconds())
    return jsonify({
        'tracking_active': adsb_using_service,
        'connected_to_sbs': adsb_connected,
        'active_device': adsb_active_device,
        'session': session,
        'uptime_seconds': uptime_seconds,
    })


@adsb_bp.route('/start', methods=['POST'])
def start_adsb():
    """Start ADS-B tracking."""
    global adsb_using_service, adsb_active_device, adsb_active_sdr_type, adsb_bias_t_active

    with app_module.adsb_lock:
        if adsb_using_service:
            session = _get_active_session()
            return jsonify({
                'status': 'already_running',
                'message': 'ADS-B tracking already active',
                'session': session
            }), 409

    data = request.get_json(silent=True) or {}
    start_source = data.get('source')
    started_by = request.remote_addr

    # Validate inputs
    try:
        gain = int(validate_gain(data.get('gain', '40')))
        device = validate_device_index(data.get('device', '0'))
    except ValueError as e:
        return api_error(str(e), 400)

    # Check for remote SBS connection (e.g., remote dump1090)
    remote_sbs_host = data.get('remote_sbs_host')
    remote_sbs_port = data.get('remote_sbs_port', 30003)

    if remote_sbs_host:
        # Validate and connect to remote dump1090 SBS output
        try:
            remote_sbs_host = validate_rtl_tcp_host(remote_sbs_host)
            remote_sbs_port = validate_rtl_tcp_port(remote_sbs_port)
        except ValueError as e:
            return api_error(str(e), 400)

        remote_addr = f"{remote_sbs_host}:{remote_sbs_port}"
        logger.info(f"Connecting to remote dump1090 SBS at {remote_addr}")
        adsb_using_service = True
        thread = threading.Thread(target=parse_sbs_stream, args=(remote_addr,), daemon=True)
        thread.start()
        session = _record_session_start(
            device_index=device,
            sdr_type='remote',
            remote_host=remote_sbs_host,
            remote_port=remote_sbs_port,
            start_source=start_source,
            started_by=started_by,
        )
        return jsonify({
            'status': 'started',
            'message': f'Connected to remote dump1090 at {remote_addr}',
            'session': session
        })

    # Kill any stale app-spawned dump1090 from a previous run before checking the port
    cleanup_stale_dump1090()

    # Check if dump1090 is already running externally (e.g., user started it manually)
    existing_service = check_dump1090_service()
    if existing_service:
        logger.info(f"Found existing dump1090 service at {existing_service}")
        adsb_using_service = True
        thread = threading.Thread(target=parse_sbs_stream, args=(existing_service,), daemon=True)
        thread.start()
        session = _record_session_start(
            device_index=device,
            sdr_type='external',
            remote_host='localhost',
            remote_port=ADSB_SBS_PORT,
            start_source=start_source,
            started_by=started_by,
        )
        return jsonify({
            'status': 'started',
            'message': 'Connected to existing dump1090 service',
            'session': session
        })

    # Get SDR type from request
    sdr_type_str = data.get('sdr_type', 'rtlsdr')
    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR
        sdr_type_str = sdr_type.value

    # For RTL-SDR, use dump1090. For other hardware, need readsb with SoapySDR
    if sdr_type == SDRType.RTL_SDR:
        dump1090_path = find_dump1090()
        if not dump1090_path:
            return api_error('dump1090 not found. Install dump1090/dump1090-fa or ensure it is in /usr/local/bin/')
    else:
        # For LimeSDR/HackRF, check for readsb (dump1090 with SoapySDR support)
        dump1090_path = shutil.which('readsb') or find_dump1090()
        if not dump1090_path:
            return api_error(f'readsb or dump1090 not found for {sdr_type.value}. Install readsb with SoapySDR support.')

    # Kill any stale app-started process (use process group to ensure full cleanup)
    if app_module.adsb_process:
        try:
            pgid = os.getpgid(app_module.adsb_process.pid)
            os.killpg(pgid, 15)  # SIGTERM
            app_module.adsb_process.wait(timeout=PROCESS_TERMINATE_TIMEOUT)
        except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
            try:
                pgid = os.getpgid(app_module.adsb_process.pid)
                os.killpg(pgid, 9)  # SIGKILL
            except (ProcessLookupError, OSError):
                pass
        app_module.adsb_process = None
        clear_dump1090_pid()
        logger.info("Killed stale ADS-B process")

    # Check if device is available before starting local dump1090
    device_int = int(device)
    error = app_module.claim_sdr_device(device_int, 'adsb', sdr_type_str)
    if error:
        return jsonify({
            'status': 'error',
            'error_type': 'DEVICE_BUSY',
            'message': error
        }), 409

    # Track claimed device immediately so stop_adsb() can always release it
    adsb_active_device = device
    adsb_active_sdr_type = sdr_type_str

    # Create device object and build command via abstraction layer
    sdr_device = SDRFactory.create_default_device(sdr_type, index=device)
    builder = SDRFactory.get_builder(sdr_type)

    # Build ADS-B decoder command
    bias_t = data.get('bias_t', False)
    adsb_bias_t_active = bias_t
    cmd = builder.build_adsb_command(
        device=sdr_device,
        gain=float(gain),
        bias_t=bias_t
    )

    # Ensure we use the resolved binary path for all SDR types
    cmd[0] = dump1090_path

    try:
        logger.info(f"Starting dump1090 with device index {device}: {' '.join(cmd)}")
        app_module.adsb_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True  # Create new process group for clean shutdown
        )
        write_dump1090_pid(app_module.adsb_process.pid)

        # Poll for dump1090 readiness instead of blind sleep
        dump1090_ready = False
        poll_interval = 0.1
        elapsed = 0.0
        while elapsed < DUMP1090_START_WAIT:
            if app_module.adsb_process.poll() is not None:
                break  # Process exited early — handle below
            if check_dump1090_service():
                dump1090_ready = True
                break
            time.sleep(poll_interval)
            elapsed += poll_interval

        if app_module.adsb_process.poll() is not None:
            # Process exited - release device and get error message
            app_module.release_sdr_device(device_int, sdr_type_str)
            adsb_active_device = None
            adsb_active_sdr_type = None
            stderr_output = ''
            if app_module.adsb_process.stderr:
                with contextlib.suppress(Exception):
                    stderr_output = app_module.adsb_process.stderr.read().decode('utf-8', errors='ignore').strip()

            # Parse stderr to provide specific guidance
            error_type = 'START_FAILED'
            stderr_lower = stderr_output.lower()

            sdr_label = sdr_type.value

            if 'usb_claim_interface' in stderr_lower or 'libusb_error_busy' in stderr_lower or 'device or resource busy' in stderr_lower:
                error_msg = 'SDR device is busy. Another process may be using it.'
                suggestion = 'Try: 1) Stop other SDR applications, 2) Run "pkill -f rtl_" to kill stale processes, or 3) Remove and reinsert the SDR device.'
                error_type = 'DEVICE_BUSY'
            elif 'no hackrf boards found' in stderr_lower or 'hackrf_open' in stderr_lower:
                error_msg = f'{sdr_label} device not found.'
                suggestion = 'Ensure the HackRF is connected. Try removing and reinserting the device.'
                error_type = 'DEVICE_NOT_FOUND'
            elif 'soapysdr not found' in stderr_lower or 'soapy' in stderr_lower and 'not found' in stderr_lower:
                error_msg = f'SoapySDR driver not found for {sdr_label}.'
                suggestion = f'Install SoapySDR and the {sdr_label} module (e.g., soapysdr-module-hackrf).'
                error_type = 'DRIVER_NOT_FOUND'
            elif 'no supported devices' in stderr_lower or 'no rtl-sdr' in stderr_lower or 'failed to open' in stderr_lower:
                error_msg = f'{sdr_label} device not found.'
                suggestion = 'Ensure the device is connected. Try removing and reinserting the SDR.'
                error_type = 'DEVICE_NOT_FOUND'
            elif 'kernel driver is active' in stderr_lower or 'dvb' in stderr_lower:
                error_msg = 'Kernel DVB-T driver is blocking the device.'
                suggestion = 'Blacklist the DVB drivers: Go to Settings > Hardware > "Blacklist DVB Drivers" or run "sudo rmmod dvb_usb_rtl28xxu".'
                error_type = 'KERNEL_DRIVER'
            elif 'permission' in stderr_lower or 'access' in stderr_lower:
                error_msg = f'Permission denied accessing {sdr_label} device.'
                suggestion = f'Run Intercept with sudo, or add udev rules for {sdr_label} devices.'
                error_type = 'PERMISSION_DENIED'
            elif sdr_type == SDRType.RTL_SDR:
                error_msg = 'dump1090 failed to start.'
                suggestion = 'Try removing and reinserting the SDR device, or check if another application is using it.'
            else:
                error_msg = f'ADS-B decoder failed to start for {sdr_label}.'
                suggestion = 'Ensure readsb is installed with SoapySDR support and the device is connected.'

            full_msg = f'{error_msg} {suggestion}'
            if stderr_output and len(stderr_output) < 300:
                full_msg += f' (Details: {stderr_output})'

            return jsonify({
                'status': 'error',
                'error_type': error_type,
                'message': full_msg
            })

        # dump1090 is still running but SBS port never came up — device may be
        # held by a stale process from a previous mode.  Kill it so the USB
        # device is released and report a clear error to the frontend.
        if not dump1090_ready:
            logger.warning("dump1090 running but SBS port not available after %.1fs — killing", DUMP1090_START_WAIT)
            try:
                pgid = os.getpgid(app_module.adsb_process.pid)
                os.killpg(pgid, 15)
                app_module.adsb_process.wait(timeout=ADSB_TERMINATE_TIMEOUT)
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                try:
                    pgid = os.getpgid(app_module.adsb_process.pid)
                    os.killpg(pgid, 9)
                except (ProcessLookupError, OSError):
                    pass
            app_module.adsb_process = None
            clear_dump1090_pid()
            app_module.release_sdr_device(device_int, sdr_type_str)
            adsb_active_device = None
            adsb_active_sdr_type = None
            return jsonify({
                'status': 'error',
                'error_type': 'DEVICE_BUSY',
                'message': (
                    'SDR device did not become ready in time. '
                    'Another mode may still be releasing the device. '
                    'Please wait a moment and try again.'
                ),
            })

        adsb_using_service = True
        thread = threading.Thread(target=parse_sbs_stream, args=(f'localhost:{ADSB_SBS_PORT}',), daemon=True)
        thread.start()

        session = _record_session_start(
            device_index=device,
            sdr_type=sdr_type.value,
            remote_host=None,
            remote_port=None,
            start_source=start_source,
            started_by=started_by,
        )
        return jsonify({
            'status': 'started',
            'message': 'ADS-B tracking started',
            'device': device,
            'session': session
        })
    except Exception as e:
        # Release device on failure
        app_module.release_sdr_device(device_int, sdr_type_str)
        adsb_active_device = None
        adsb_active_sdr_type = None
        return api_error(str(e))


@adsb_bp.route('/stop', methods=['POST'])
def stop_adsb():
    """Stop ADS-B tracking."""
    global adsb_using_service, adsb_active_device, adsb_active_sdr_type, adsb_bias_t_active
    data = request.get_json(silent=True) or {}
    stop_source = data.get('source')
    stopped_by = request.remote_addr

    with app_module.adsb_lock:
        if app_module.adsb_process:
            try:
                # Kill the entire process group to ensure all child processes are terminated
                pgid = os.getpgid(app_module.adsb_process.pid)
                os.killpg(pgid, 15)  # SIGTERM
                app_module.adsb_process.wait(timeout=ADSB_TERMINATE_TIMEOUT)
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                try:
                    # Force kill if terminate didn't work
                    pgid = os.getpgid(app_module.adsb_process.pid)
                    os.killpg(pgid, 9)  # SIGKILL
                except (ProcessLookupError, OSError):
                    pass
            app_module.adsb_process = None
            clear_dump1090_pid()
            logger.info("ADS-B process stopped")

        # Turn off bias-T if it was enabled at start — the hardware register
        # persists after the device is closed, so we must explicitly disable it.
        if adsb_bias_t_active and (adsb_active_sdr_type or 'rtlsdr') == 'rtlsdr':
            from utils.sdr.rtlsdr import disable_bias_t_via_rtl_biast
            disable_bias_t_via_rtl_biast(adsb_active_device or 0)
        adsb_bias_t_active = False

        # Release device from registry
        if adsb_active_device is not None:
            app_module.release_sdr_device(adsb_active_device, adsb_active_sdr_type or 'rtlsdr')

        adsb_using_service = False
        adsb_active_device = None
        adsb_active_sdr_type = None

    app_module.adsb_aircraft.clear()
    _looked_up_icaos.clear()
    session = _record_session_stop(stop_source=stop_source, stopped_by=stopped_by)
    return jsonify({'status': 'stopped', 'session': session})


@adsb_bp.route('/stream')
def stream_adsb():
    """SSE stream for ADS-B aircraft."""
    client_queue: queue.Queue = queue.Queue(maxsize=_ADSB_STREAM_CLIENT_QUEUE_SIZE)
    with _adsb_stream_subscribers_lock:
        _adsb_stream_subscribers.add(client_queue)

    # Prime new clients with current known aircraft so they don't wait for the
    # next positional update before rendering.
    for snapshot in list(app_module.adsb_aircraft.values()):
        try:
            client_queue.put_nowait({'type': 'aircraft', **snapshot})
        except queue.Full:
            break

    def generate():
        last_keepalive = time.time()
        # Send immediate keepalive so Werkzeug dev server flushes response
        # headers right away (it buffers until first body byte is written).
        yield format_sse({'type': 'keepalive'})

        try:
            while True:
                try:
                    msg = client_queue.get(timeout=SSE_QUEUE_TIMEOUT)
                    last_keepalive = time.time()
                    with contextlib.suppress(Exception):
                        process_event('adsb', msg, msg.get('type'))
                    yield format_sse(msg)
                except queue.Empty:
                    now = time.time()
                    if now - last_keepalive >= SSE_KEEPALIVE_INTERVAL:
                        yield format_sse({'type': 'keepalive'})
                        last_keepalive = now
        finally:
            with _adsb_stream_subscribers_lock:
                _adsb_stream_subscribers.discard(client_queue)

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@adsb_bp.route('/dashboard')
def adsb_dashboard():
    """Popout ADS-B dashboard."""
    embedded = request.args.get('embedded', 'false') == 'true'
    return render_template(
        'adsb_dashboard.html',
        shared_observer_location=SHARED_OBSERVER_LOCATION_ENABLED,
        adsb_auto_start=ADSB_AUTO_START,
        default_latitude=DEFAULT_LATITUDE,
        default_longitude=DEFAULT_LONGITUDE,
        embedded=embedded,
    )


@adsb_bp.route('/history')
def adsb_history():
    """ADS-B history reporting dashboard."""
    history_available = ADSB_HISTORY_ENABLED and PSYCOPG2_AVAILABLE
    resp = make_response(render_template('adsb_history.html', history_enabled=history_available))
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@adsb_bp.route('/history/summary')
def adsb_history_summary():
    """Summary stats for ADS-B history window."""
    if not ADSB_HISTORY_ENABLED or not PSYCOPG2_AVAILABLE:
        return api_error('ADS-B history is disabled', 503)
    _ensure_history_schema()

    since_minutes = _parse_int_param(request.args.get('since_minutes'), 1440, 1, 10080)
    window = f'{since_minutes} minutes'

    sql = """
        SELECT
            (SELECT COUNT(*) FROM adsb_messages WHERE received_at >= NOW() - INTERVAL %s) AS message_count,
            (SELECT COUNT(*) FROM adsb_snapshots WHERE captured_at >= NOW() - INTERVAL %s) AS snapshot_count,
            (SELECT COUNT(DISTINCT icao) FROM adsb_snapshots WHERE captured_at >= NOW() - INTERVAL %s) AS aircraft_count,
            (SELECT MIN(captured_at) FROM adsb_snapshots WHERE captured_at >= NOW() - INTERVAL %s) AS first_seen,
            (SELECT MAX(captured_at) FROM adsb_snapshots WHERE captured_at >= NOW() - INTERVAL %s) AS last_seen
    """

    try:
        with _get_history_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (window, window, window, window, window))
            row = cur.fetchone() or {}
        return jsonify(row)
    except Exception as exc:
        logger.warning("ADS-B history summary failed: %s", exc)
        return api_error('History database unavailable', 503)


@adsb_bp.route('/history/aircraft')
def adsb_history_aircraft():
    """List latest aircraft snapshots for a time window."""
    if not ADSB_HISTORY_ENABLED or not PSYCOPG2_AVAILABLE:
        return api_error('ADS-B history is disabled', 503)
    _ensure_history_schema()

    since_minutes = _parse_int_param(request.args.get('since_minutes'), 1440, 1, 10080)
    limit = _parse_int_param(request.args.get('limit'), 200, 1, 2000)
    search = (request.args.get('search') or '').strip()
    window = f'{since_minutes} minutes'
    pattern = f'%{search}%'

    sql = """
        SELECT *
        FROM (
            SELECT DISTINCT ON (icao)
                icao,
                callsign,
                registration,
                type_code,
                type_desc,
                altitude,
                speed,
                heading,
                vertical_rate,
                lat,
                lon,
                squawk,
                captured_at AS last_seen
            FROM adsb_snapshots
            WHERE captured_at >= NOW() - INTERVAL %s
              AND (%s = '' OR icao ILIKE %s OR callsign ILIKE %s OR registration ILIKE %s)
            ORDER BY icao, captured_at DESC
        ) latest
        ORDER BY last_seen DESC
        LIMIT %s
    """

    try:
        with _get_history_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (window, search, pattern, pattern, pattern, limit))
            rows = cur.fetchall()
        return jsonify({'aircraft': rows, 'count': len(rows)})
    except Exception as exc:
        logger.warning("ADS-B history aircraft query failed: %s", exc)
        return api_error('History database unavailable', 503)


@adsb_bp.route('/history/timeline')
def adsb_history_timeline():
    """Timeline snapshots for a specific aircraft."""
    if not ADSB_HISTORY_ENABLED or not PSYCOPG2_AVAILABLE:
        return api_error('ADS-B history is disabled', 503)
    _ensure_history_schema()

    icao = (request.args.get('icao') or '').strip().upper()
    if not icao:
        return api_error('icao is required', 400)

    since_minutes = _parse_int_param(request.args.get('since_minutes'), 1440, 1, 10080)
    limit = _parse_int_param(request.args.get('limit'), 2000, 1, 20000)
    window = f'{since_minutes} minutes'

    sql = """
        SELECT captured_at, altitude, speed, heading, vertical_rate, lat, lon, squawk
        FROM adsb_snapshots
        WHERE icao = %s
          AND captured_at >= NOW() - INTERVAL %s
        ORDER BY captured_at ASC
        LIMIT %s
    """

    try:
        with _get_history_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (icao, window, limit))
            rows = cur.fetchall()
        return jsonify({'icao': icao, 'timeline': rows, 'count': len(rows)})
    except Exception as exc:
        logger.warning("ADS-B history timeline query failed: %s", exc)
        return api_error('History database unavailable', 503)


@adsb_bp.route('/history/messages')
def adsb_history_messages():
    """Raw message history for a specific aircraft."""
    if not ADSB_HISTORY_ENABLED or not PSYCOPG2_AVAILABLE:
        return api_error('ADS-B history is disabled', 503)
    _ensure_history_schema()

    icao = (request.args.get('icao') or '').strip().upper()
    since_minutes = _parse_int_param(request.args.get('since_minutes'), 30, 1, 10080)
    limit = _parse_int_param(request.args.get('limit'), 200, 1, 2000)
    window = f'{since_minutes} minutes'

    sql = """
        SELECT received_at, msg_type, callsign, altitude, speed, heading, vertical_rate, lat, lon, squawk
        FROM adsb_messages
        WHERE received_at >= NOW() - INTERVAL %s
          AND (%s = '' OR icao = %s)
        ORDER BY received_at DESC
        LIMIT %s
    """

    try:
        with _get_history_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (window, icao, icao, limit))
            rows = cur.fetchall()
        return jsonify({'icao': icao, 'messages': rows, 'count': len(rows)})
    except Exception as exc:
        logger.warning("ADS-B history message query failed: %s", exc)
        return api_error('History database unavailable', 503)


@adsb_bp.route('/history/export')
def adsb_history_export():
    """Export ADS-B history data in CSV or JSON format."""
    if not ADSB_HISTORY_ENABLED or not PSYCOPG2_AVAILABLE:
        return api_error('ADS-B history is disabled', 503)
    _ensure_history_schema()

    export_format = str(request.args.get('format') or 'csv').strip().lower()
    export_type = str(request.args.get('type') or 'all').strip().lower()
    if export_format not in {'csv', 'json'}:
        return api_error('format must be csv or json', 400)
    if export_type not in {'messages', 'snapshots', 'sessions', 'all'}:
        return api_error('type must be messages, snapshots, sessions, or all', 400)

    scope, since_minutes, start, end = _parse_export_scope(request.args)
    icao = (request.args.get('icao') or '').strip().upper()
    search = (request.args.get('search') or '').strip()
    classification = str(request.args.get('classification') or 'all').strip().lower()
    if classification not in {'all', 'military', 'civilian'}:
        classification = 'all'
    pattern = f'%{search}%'

    snapshots: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []

    def _filter_by_classification(
        rows: list[dict[str, Any]],
        icao_key: str = 'icao',
        callsign_key: str = 'callsign',
    ) -> list[dict[str, Any]]:
        if classification == 'all':
            return rows
        want_military = classification == 'military'
        return [
            r for r in rows
            if _is_military_aircraft(r.get(icao_key, ''), r.get(callsign_key)) == want_military
        ]

    try:
        with _get_history_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            if export_type in {'snapshots', 'all'}:
                snapshot_where: list[str] = []
                snapshot_params: list[Any] = []
                _add_time_filter(
                    where_parts=snapshot_where,
                    params=snapshot_params,
                    scope=scope,
                    timestamp_field='captured_at',
                    since_minutes=since_minutes,
                    start=start,
                    end=end,
                )
                if icao:
                    snapshot_where.append("icao = %s")
                    snapshot_params.append(icao)
                if search:
                    snapshot_where.append("(icao ILIKE %s OR callsign ILIKE %s OR registration ILIKE %s)")
                    snapshot_params.extend([pattern, pattern, pattern])

                snapshot_sql = """
                        SELECT captured_at, icao, callsign, registration, type_code, type_desc,
                               altitude, speed, heading, vertical_rate, lat, lon, squawk, source_host
                        FROM adsb_snapshots
                    """
                if snapshot_where:
                    snapshot_sql += " WHERE " + " AND ".join(snapshot_where)
                snapshot_sql += " ORDER BY captured_at DESC"
                cur.execute(snapshot_sql, tuple(snapshot_params))
                snapshots = _filter_by_classification(cur.fetchall())

            if export_type in {'messages', 'all'}:
                message_where: list[str] = []
                message_params: list[Any] = []
                _add_time_filter(
                    where_parts=message_where,
                    params=message_params,
                    scope=scope,
                    timestamp_field='received_at',
                    since_minutes=since_minutes,
                    start=start,
                    end=end,
                )
                if icao:
                    message_where.append("icao = %s")
                    message_params.append(icao)
                if search:
                    message_where.append("(icao ILIKE %s OR callsign ILIKE %s)")
                    message_params.extend([pattern, pattern])

                message_sql = """
                        SELECT received_at, msg_time, logged_time, icao, msg_type, callsign,
                               altitude, speed, heading, vertical_rate, lat, lon, squawk,
                               session_id, aircraft_id, flight_id, source_host, raw_line
                        FROM adsb_messages
                    """
                if message_where:
                    message_sql += " WHERE " + " AND ".join(message_where)
                message_sql += " ORDER BY received_at DESC"
                cur.execute(message_sql, tuple(message_params))
                messages = _filter_by_classification(cur.fetchall())

            if export_type in {'sessions', 'all'}:
                session_where: list[str] = []
                session_params: list[Any] = []
                if scope == 'custom' and start is not None and end is not None:
                    session_where.append("COALESCE(ended_at, %s) >= %s AND started_at < %s")
                    session_params.extend([end, start, end])
                elif scope == 'window':
                    session_where.append("COALESCE(ended_at, NOW()) >= NOW() - INTERVAL %s")
                    session_params.append(f'{since_minutes} minutes')

                session_sql = """
                        SELECT id, started_at, ended_at, device_index, sdr_type, remote_host,
                               remote_port, start_source, stop_source, started_by, stopped_by, notes
                        FROM adsb_sessions
                    """
                if session_where:
                    session_sql += " WHERE " + " AND ".join(session_where)
                session_sql += " ORDER BY started_at DESC"
                cur.execute(session_sql, tuple(session_params))
                sessions = cur.fetchall()
    except Exception as exc:
        logger.warning("ADS-B history export failed: %s", exc)
        return api_error('History database unavailable', 503)

    exported_at = datetime.now(timezone.utc).isoformat()
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    filename_scope = 'all' if scope == 'all' else ('custom' if scope == 'custom' else f'{since_minutes}m')
    filename = f'adsb_history_{export_type}_{filename_scope}_{timestamp}.{export_format}'

    if export_format == 'json':
        payload = {
            'exported_at': exported_at,
            'format': export_format,
            'type': export_type,
            'scope': scope,
            'since_minutes': None if scope != 'window' else since_minutes,
            'filters': {
                'icao': icao or None,
                'search': search or None,
                'classification': classification,
                'start': start.isoformat() if start else None,
                'end': end.isoformat() if end else None,
            },
            'counts': {
                'messages': len(messages),
                'snapshots': len(snapshots),
                'sessions': len(sessions),
            },
            'messages': _rows_to_serializable(messages),
            'snapshots': _rows_to_serializable(snapshots),
            'sessions': _rows_to_serializable(sessions),
        }
        response = Response(
            json.dumps(payload, indent=2, default=str),
            mimetype='application/json',
        )
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        return response

    csv_data = _build_export_csv(
        exported_at=exported_at,
        scope=scope,
        since_minutes=since_minutes if scope == 'window' else None,
        icao=icao,
        search=search,
        classification=classification,
        messages=messages,
        snapshots=snapshots,
        sessions=sessions,
        export_type=export_type,
    )
    response = Response(csv_data, mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response


@adsb_bp.route('/history/prune', methods=['POST'])
def adsb_history_prune():
    """Delete ADS-B history for a selected time range or entire dataset."""
    if not ADSB_HISTORY_ENABLED or not PSYCOPG2_AVAILABLE:
        return api_error('ADS-B history is disabled', 503)
    _ensure_history_schema()

    payload = request.get_json(silent=True) or {}
    mode = str(payload.get('mode') or 'range').strip().lower()
    if mode not in {'range', 'all'}:
        return api_error('mode must be range or all', 400)

    try:
        with _get_history_connection() as conn, conn.cursor() as cur:
            deleted = {'messages': 0, 'snapshots': 0}

            if mode == 'all':
                cur.execute("DELETE FROM adsb_messages")
                deleted['messages'] = max(0, cur.rowcount or 0)
                cur.execute("DELETE FROM adsb_snapshots")
                deleted['snapshots'] = max(0, cur.rowcount or 0)
                return jsonify({
                    'status': 'ok',
                    'mode': 'all',
                    'deleted': deleted,
                    'total_deleted': deleted['messages'] + deleted['snapshots'],
                })

            start = _parse_iso_datetime(payload.get('start'))
            end = _parse_iso_datetime(payload.get('end'))
            if start is None or end is None:
                return api_error('start and end ISO datetime values are required', 400)
            if end <= start:
                return api_error('end must be after start', 400)
            if end - start > timedelta(days=31):
                return api_error('range cannot exceed 31 days', 400)

            cur.execute(
                """
                    DELETE FROM adsb_messages
                    WHERE received_at >= %s
                      AND received_at < %s
                    """,
                (start, end),
            )
            deleted['messages'] = max(0, cur.rowcount or 0)

            cur.execute(
                """
                    DELETE FROM adsb_snapshots
                    WHERE captured_at >= %s
                      AND captured_at < %s
                    """,
                (start, end),
            )
            deleted['snapshots'] = max(0, cur.rowcount or 0)

            return jsonify({
                'status': 'ok',
                'mode': 'range',
                'start': start.isoformat(),
                'end': end.isoformat(),
                'deleted': deleted,
                'total_deleted': deleted['messages'] + deleted['snapshots'],
            })
    except Exception as exc:
        logger.warning("ADS-B history prune failed: %s", exc)
        return api_error('History database unavailable', 503)


# ============================================
# AIRCRAFT DATABASE MANAGEMENT
# ============================================

@adsb_bp.route('/aircraft-db/status')
def aircraft_db_status():
    """Get aircraft database status."""
    return jsonify(aircraft_db.get_db_status())


@adsb_bp.route('/aircraft-db/check-updates')
def aircraft_db_check_updates():
    """Check for aircraft database updates."""
    result = aircraft_db.check_for_updates()
    return jsonify(result)


@adsb_bp.route('/aircraft-db/download', methods=['POST'])
def aircraft_db_download():
    """Download/update aircraft database."""
    global _looked_up_icaos
    result = aircraft_db.download_database()
    if result.get('success'):
        # Clear lookup cache so new data is used
        _looked_up_icaos.clear()
    return jsonify(result)


@adsb_bp.route('/aircraft-db/delete', methods=['POST'])
def aircraft_db_delete():
    """Delete aircraft database."""
    result = aircraft_db.delete_database()
    return jsonify(result)


@adsb_bp.route('/aircraft-photo/<registration>')
def aircraft_photo(registration: str):
    """Fetch aircraft photo from Planespotters.net API."""
    import requests

    # Validate registration format (alphanumeric with dashes)
    if not registration or not all(c.isalnum() or c == '-' for c in registration):
        return api_error('Invalid registration', 400)

    try:
        # Planespotters.net public API
        url = f'https://api.planespotters.net/pub/photos/reg/{registration}'
        resp = requests.get(url, timeout=5, headers={
            'User-Agent': 'INTERCEPT-ADS-B/1.0'
        })

        if resp.status_code == 200:
            data = resp.json()
            if data.get('photos') and len(data['photos']) > 0:
                photo = data['photos'][0]
                return jsonify({
                    'success': True,
                    'thumbnail': photo.get('thumbnail_large', {}).get('src'),
                    'link': photo.get('link'),
                    'photographer': photo.get('photographer')
                })

        return jsonify({'success': False, 'error': 'No photo found'})

    except requests.Timeout:
        return jsonify({'success': False, 'error': 'Request timeout'}), 504
    except Exception as e:
        logger.debug(f"Error fetching aircraft photo: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@adsb_bp.route('/aircraft/<icao>/messages')
def get_aircraft_messages(icao: str):
    """Get correlated ACARS/VDL2 messages for an aircraft."""
    if not icao or not all(c in '0123456789ABCDEFabcdef' for c in icao):
        return api_error('Invalid ICAO', 400)

    aircraft = app_module.adsb_aircraft.get(icao.upper())
    callsign = aircraft.get('callsign') if aircraft else None
    registration = aircraft.get('registration') if aircraft else None

    messages = get_flight_correlator().get_messages_for_aircraft(
        icao=icao.upper(), callsign=callsign, registration=registration
    )

    # Backfill translation on messages missing label_description
    try:
        for msg in messages.get('acars', []):
            if not msg.get('label_description'):
                translation = translate_message(msg)
                msg['label_description'] = translation['label_description']
                msg['message_type'] = translation['message_type']
                msg['parsed'] = translation['parsed']
    except Exception:
        pass

    return api_success(data={'icao': icao.upper(), **messages})
