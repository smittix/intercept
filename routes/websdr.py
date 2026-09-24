"""HF/Shortwave WebSDR Integration - KiwiSDR network access."""

from __future__ import annotations

import html
import json
import math
import queue
import re
import struct
import threading
import time

from flask import Blueprint, Flask, Response, jsonify, request

from utils.responses import api_error, api_success

try:
    from flask_sock import Sock

    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False

import contextlib

from utils.kiwisdr import KIWI_SAMPLE_RATE, VALID_MODES, KiwiSDRClient, parse_host_port
from utils.logging import get_logger

logger = get_logger("intercept.websdr")

websdr_bp = Blueprint("websdr", __name__, url_prefix="/websdr")

# ============================================
# RECEIVER CACHE
# ============================================

_receiver_cache: list[dict] = []
_cache_lock = threading.Lock()
_cache_timestamp: float = 0
CACHE_TTL = 3600  # 1 hour


def _parse_gps_coord(coord_str: str) -> float | None:
    """Parse a GPS coordinate string like '51.5074' or '(-33.87)' into a float."""
    if not coord_str:
        return None
    # Remove parentheses and whitespace
    cleaned = coord_str.strip().strip("()").strip()
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two GPS coordinates."""
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return R * c


KIWI_DATA_URLS = [
    "https://rx.skywavelinux.com/kiwisdr_com.js",
    "http://rx.linkfanel.net/kiwisdr_com.js",
]


def _clean_text(value: object) -> str:
    """Plain text from a directory field. Receiver owners write HTML and
    entities into their names and antenna notes ("<b>▶ PA0EBC ◀</b> &#128077;");
    the page shows text, so tags go and entities become their characters."""
    text = re.sub(r"<[^>]*>", "", str(value or ""))
    text = html.unescape(text)
    text = re.sub(r"<[^>]*>", "", text)  # tags that were written as entities
    text = re.sub(r"<[a-zA-Z/!][^>]*$", "", text)  # a tag cut off by the field's length limit
    return re.sub(r"\s+", " ", text).strip()


def _fetch_kiwi_receivers() -> list[dict]:
    """Fetch the KiwiSDR receiver list from the public directory."""
    import json
    import urllib.request

    receivers = []
    raw = None

    # Try each data source until one works
    for data_url in KIWI_DATA_URLS:
        try:
            req = urllib.request.Request(
                data_url,
                headers={
                    "User-Agent": "INTERCEPT-SIGINT/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            if raw and len(raw) > 100:
                logger.info(f"Fetched KiwiSDR data from {data_url}")
                break
            raw = None
        except Exception as e:
            logger.warning(f"Failed to fetch from {data_url}: {e}")
            continue

    if not raw:
        logger.error("All KiwiSDR data sources failed")
        return receivers

    # The JS file contains: var kiwisdr_com = [ {...}, {...}, ... ];
    # Extract the JSON array
    match = re.search(r"var\s+kiwisdr_com\s*=\s*(\[.*\])\s*;?", raw, re.DOTALL)
    if not match:
        # Try bare array
        match = re.search(r"(\[\s*\{.*\}\s*\])", raw, re.DOTALL)
        if not match:
            logger.warning("Could not find receiver array in KiwiSDR data")
            return receivers

    arr_str = match.group(1)

    # Parse JSON
    try:
        raw_list = json.loads(arr_str)
    except json.JSONDecodeError:
        # Fix common JS → JSON issues (trailing commas)
        fixed = re.sub(r",\s*}", "}", arr_str)
        fixed = re.sub(r",\s*]", "]", fixed)
        try:
            raw_list = json.loads(fixed)
        except json.JSONDecodeError:
            logger.error("Failed to parse KiwiSDR JSON")
            return receivers

    for entry in raw_list:
        if not isinstance(entry, dict):
            continue

        # Skip offline receivers
        if entry.get("offline") == "yes" or entry.get("status") != "active":
            continue

        name = _clean_text(entry.get("name")) or "Unknown"
        url = entry.get("url", "")
        gps = entry.get("gps", "")
        antenna = _clean_text(entry.get("antenna"))
        location = _clean_text(entry.get("loc"))

        # Parse users (strings in actual data)
        try:
            users = int(entry.get("users", 0))
        except (ValueError, TypeError):
            users = 0
        try:
            users_max = int(entry.get("users_max", 4))
        except (ValueError, TypeError):
            users_max = 4

        # Parse bands field: "0-30000000" (Hz) → freq_lo/freq_hi in kHz
        bands_str = entry.get("bands", "0-30000000")
        freq_lo = 0
        freq_hi = 30000
        if bands_str and "-" in str(bands_str):
            try:
                parts = str(bands_str).split("-")
                freq_lo = int(parts[0]) / 1000  # Hz to kHz
                freq_hi = int(parts[1]) / 1000  # Hz to kHz
            except (ValueError, IndexError):
                pass

        # Parse GPS: "(51.317266, -2.950479)" format
        lat, lon = None, None
        if gps:
            parts = str(gps).replace("(", "").replace(")", "").split(",")
            if len(parts) >= 2:
                lat = _parse_gps_coord(parts[0])
                lon = _parse_gps_coord(parts[1])

        if not url:
            continue

        # Ensure URL has protocol
        if not url.startswith("http"):
            url = "http://" + url

        receivers.append(
            {
                "name": name,
                "url": url.rstrip("/"),
                "lat": lat,
                "lon": lon,
                "location": location,
                "users": users,
                "users_max": users_max,
                "antenna": antenna,
                "bands": bands_str,
                "freq_lo": freq_lo,
                "freq_hi": freq_hi,
                "available": users < users_max,
            }
        )

    return receivers


def get_receivers(force_refresh: bool = False) -> list[dict]:
    """Get cached receiver list, refreshing if stale."""
    global _receiver_cache, _cache_timestamp

    with _cache_lock:
        now = time.time()
        if force_refresh or not _receiver_cache or (now - _cache_timestamp) > CACHE_TTL:
            logger.info("Refreshing KiwiSDR receiver list...")
            _receiver_cache = _fetch_kiwi_receivers()
            _cache_timestamp = now
            logger.info(f"Loaded {len(_receiver_cache)} KiwiSDR receivers")

    return _receiver_cache


# ============================================
# API ENDPOINTS
# ============================================


@websdr_bp.route("/receivers")
def list_receivers() -> Response:
    """List KiwiSDR receivers, with optional filters."""
    freq_khz = request.args.get("freq_khz", type=float)
    available = request.args.get("available", type=str)
    refresh = request.args.get("refresh", type=str)

    receivers = get_receivers(force_refresh=(refresh == "true"))

    filtered = receivers
    if available == "true":
        filtered = [r for r in filtered if r.get("available", True)]

    if freq_khz is not None:
        filtered = [r for r in filtered if r.get("freq_lo", 0) <= freq_khz <= r.get("freq_hi", 30000)]

    return api_success(
        data={
            "receivers": filtered[:100],
            "total": len(filtered),
            "cached_total": len(receivers),
        }
    )


@websdr_bp.route("/receivers/nearest")
def nearest_receivers() -> Response:
    """Find receivers nearest to a given location."""
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    freq_khz = request.args.get("freq_khz", type=float)

    if lat is None or lon is None:
        return api_error("lat and lon are required", 400)

    receivers = get_receivers()

    # Filter by frequency if specified
    if freq_khz is not None:
        receivers = [r for r in receivers if r.get("freq_lo", 0) <= freq_khz <= r.get("freq_hi", 30000)]

    # Calculate distances and sort
    with_distance = []
    for r in receivers:
        if r.get("lat") is not None and r.get("lon") is not None:
            dist = _haversine(lat, lon, r["lat"], r["lon"])
            entry = dict(r)
            entry["distance_km"] = round(dist, 1)
            with_distance.append(entry)

    with_distance.sort(key=lambda x: x["distance_km"])

    return api_success(data={"receivers": with_distance[:10]})


@websdr_bp.route("/spy-station/<station_id>/receivers")
def spy_station_receivers(station_id: str) -> Response:
    """Find receivers that can tune to a spy station's frequency."""
    try:
        from routes.spy_stations import STATIONS
    except ImportError:
        return api_error("Spy stations module not available", 503)

    # Find the station
    station = None
    for s in STATIONS:
        if s.get("id") == station_id:
            station = s
            break

    if not station:
        return api_error("Station not found", 404)

    # Get primary frequency
    freq_khz = None
    for f in station.get("frequencies", []):
        if f.get("primary"):
            freq_khz = f.get("freq_khz")
            break
    if freq_khz is None and station.get("frequencies"):
        freq_khz = station["frequencies"][0].get("freq_khz")

    if freq_khz is None:
        return api_error("No frequency found for station", 404)

    receivers = get_receivers()

    # Filter receivers that cover this frequency and are available
    matching = [
        r for r in receivers if r.get("freq_lo", 0) <= freq_khz <= r.get("freq_hi", 30000) and r.get("available", True)
    ]

    return api_success(
        data={
            "station": {
                "id": station["id"],
                "name": station.get("name", ""),
                "nickname": station.get("nickname", ""),
                "freq_khz": freq_khz,
                "mode": station.get("mode", "USB"),
            },
            "receivers": matching[:20],
            "total": len(matching),
        }
    )


@websdr_bp.route("/status")
def websdr_status() -> Response:
    """Get WebSDR connection and cache status."""
    return jsonify(
        {
            "status": "ok",
            "cached_receivers": len(_receiver_cache),
            "cache_age_seconds": round(time.time() - _cache_timestamp, 0) if _cache_timestamp > 0 else None,
            "cache_ttl": CACHE_TTL,
            "audio_connected": _kiwi_client is not None and _kiwi_client.connected if _kiwi_client else False,
        }
    )


# ============================================
# KIWISDR AUDIO PROXY
# ============================================

_kiwi_client: KiwiSDRClient | None = None
_kiwi_lock = threading.Lock()
_kiwi_audio_queue: queue.Queue = queue.Queue(maxsize=200)


def _disconnect_kiwi() -> None:
    """Disconnect active KiwiSDR client."""
    global _kiwi_client
    with _kiwi_lock:
        if _kiwi_client:
            _kiwi_client.disconnect()
            _kiwi_client = None
    # Drain audio queue
    while not _kiwi_audio_queue.empty():
        try:
            _kiwi_audio_queue.get_nowait()
        except queue.Empty:
            break


def _handle_kiwi_command(ws, cmd: str, data: dict) -> None:
    """Handle a command from the browser client."""
    global _kiwi_client

    if cmd == "connect":
        receiver_url = data.get("url", "")
        host = data.get("host", "")
        port = int(data.get("port", 8073))
        freq_khz = float(data.get("freq_khz", 7000))
        mode = data.get("mode", "am").lower()
        password = data.get("password", "")

        # Parse host/port from URL if provided
        if receiver_url and not host:
            host, port = parse_host_port(receiver_url)

        if mode not in VALID_MODES:
            ws.send(json.dumps({"type": "error", "message": f"Invalid mode: {mode}"}))
            return

        if not host or ";" in host or "&" in host or "|" in host:
            ws.send(json.dumps({"type": "error", "message": "Invalid host"}))
            return

        _disconnect_kiwi()

        def on_audio(pcm_bytes, smeter):
            # Package: 2 bytes smeter (big-endian int16) + PCM data
            header = struct.pack(">h", smeter)
            try:
                _kiwi_audio_queue.put_nowait(header + pcm_bytes)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    _kiwi_audio_queue.get_nowait()
                with contextlib.suppress(queue.Full):
                    _kiwi_audio_queue.put_nowait(header + pcm_bytes)

        def on_error(msg):
            with contextlib.suppress(Exception):
                ws.send(json.dumps({"type": "error", "message": msg}))

        def on_disconnect():
            with contextlib.suppress(Exception):
                ws.send(json.dumps({"type": "disconnected"}))

        with _kiwi_lock:
            _kiwi_client = KiwiSDRClient(
                host=host,
                port=port,
                on_audio=on_audio,
                on_error=on_error,
                on_disconnect=on_disconnect,
                password=password,
            )
            success = _kiwi_client.connect(freq_khz, mode)

        if success:
            ws.send(
                json.dumps(
                    {
                        "type": "connected",
                        "host": host,
                        "port": port,
                        "freq_khz": freq_khz,
                        "mode": mode,
                        "sample_rate": KIWI_SAMPLE_RATE,
                    }
                )
            )
        else:
            ws.send(json.dumps({"type": "error", "message": "Connection to KiwiSDR failed"}))
            _disconnect_kiwi()

    elif cmd == "tune":
        freq_khz = float(data.get("freq_khz", 0))
        mode = data.get("mode", "").lower() or None

        with _kiwi_lock:
            if _kiwi_client and _kiwi_client.connected:
                success = _kiwi_client.tune(freq_khz, mode or _kiwi_client.mode)
                if success:
                    ws.send(
                        json.dumps(
                            {
                                "type": "tuned",
                                "freq_khz": freq_khz,
                                "mode": mode or _kiwi_client.mode,
                            }
                        )
                    )
                else:
                    ws.send(json.dumps({"type": "error", "message": "Retune failed"}))
            else:
                ws.send(json.dumps({"type": "error", "message": "Not connected"}))

    elif cmd == "disconnect":
        _disconnect_kiwi()
        ws.send(json.dumps({"type": "disconnected"}))


def init_websdr_audio(app: Flask) -> None:
    """Initialize WebSocket audio proxy for KiwiSDR. Called from app.py."""
    if not WEBSOCKET_AVAILABLE:
        logger.warning("flask-sock not installed, KiwiSDR audio proxy disabled")
        return

    sock = Sock(app)

    @sock.route("/ws/kiwi-audio")
    def kiwi_audio_stream(ws):
        """WebSocket endpoint: proxy audio between browser and KiwiSDR."""
        logger.info("KiwiSDR audio client connected")

        try:
            while True:
                # Check for commands from browser
                try:
                    msg = ws.receive(timeout=0.005)
                    if msg:
                        data = json.loads(msg)
                        cmd = data.get("cmd", "")
                        _handle_kiwi_command(ws, cmd, data)
                except TimeoutError:
                    pass
                except Exception as e:
                    if "closed" in str(e).lower():
                        break
                    if "timed out" not in str(e).lower():
                        logger.error(f"KiwiSDR WS receive error: {e}")

                # Forward audio from KiwiSDR to browser
                try:
                    audio_data = _kiwi_audio_queue.get_nowait()
                    ws.send(audio_data)
                except queue.Empty:
                    time.sleep(0.005)

        except Exception as e:
            logger.info(f"KiwiSDR WS closed: {e}")
        finally:
            _disconnect_kiwi()
            logger.info("KiwiSDR audio client disconnected")
