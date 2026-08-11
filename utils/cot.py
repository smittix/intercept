"""
Optional CoT (Cursor-on-Target) export for ATAK / WinTAK / TAK Server.

Converts decoded position events (ADS-B aircraft, AIS vessels, APRS stations)
to CoT XML and sends them over UDP or TCP to a configured host:port — the
default ATAK SA multicast group (239.2.3.1:6969) or a TAK Server. Disabled
when INTERCEPT_COT_HOST is not set — no host, no socket, no error.

Configure via environment variables:

    INTERCEPT_COT_HOST           destination host / multicast group (required to enable)
    INTERCEPT_COT_PORT           destination port (default 6969)
    INTERCEPT_COT_PROTO          "udp" or "tcp" (default "udp")
    INTERCEPT_COT_STALE_SECONDS  how long a client should treat the event as valid (default 60)
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any
from uuid import uuid4
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

_socket_lock = threading.Lock()
_udp_socket: socket.socket | None = None
_enabled: bool | None = None  # None = not yet initialised

# mode -> (cot_type, uid_field, uid_prefix, callsign_field)
_MODE_MAP = {
    "adsb": ("a-f-A", "icao", "ICAO", "callsign"),
    "ais": ("a-f-S", "mmsi", "MMSI", "callsign"),
    "aprs": ("a-f-G-U-C", "callsign", "APRS", "callsign"),
}


def _get_config():
    import config

    return config


def _is_enabled() -> bool:
    global _enabled
    if _enabled is None:
        cfg = _get_config()
        _enabled = bool(cfg.COT_HOST)
    return _enabled


def _get_udp_socket() -> socket.socket:
    global _udp_socket
    if _udp_socket is not None:
        return _udp_socket

    with _socket_lock:
        if _udp_socket is None:
            _udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return _udp_socket


def _build_event_xml(
    cot_type: str,
    uid: str,
    lat: float,
    lon: float,
    hae: float | None,
    callsign: str | None,
    stale_seconds: int,
) -> str:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stale = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + stale_seconds))
    hae_val = hae if hae is not None else 0
    contact = f'<contact callsign="{escape(str(callsign))}"/>' if callsign else ""

    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<event version="2.0" uid="{escape(uid)}" type="{cot_type}" '
        f'time="{now}" start="{now}" stale="{stale}" how="m-g">'
        f'<point lat="{lat}" lon="{lon}" hae="{hae_val}" ce="9999999" le="9999999"/>'
        f"<detail>{contact}</detail>"
        f"</event>"
    )


def event_to_cot_xml(mode: str, event: dict[str, Any]) -> str | None:
    """Build CoT XML for a decoded event, or None if it lacks a position or a known mapping."""
    mapping = _MODE_MAP.get(mode)
    if mapping is None:
        return None

    cot_type, uid_field, uid_prefix, callsign_field = mapping

    lat = event.get("lat")
    lon = event.get("lon")
    if lat is None or lon is None:
        return None

    uid_value = event.get(uid_field)
    uid = f"{uid_prefix}-{uid_value}" if uid_value else f"{uid_prefix}-{uuid4()}"

    hae = event.get("altitude")
    callsign = event.get(callsign_field)
    cfg = _get_config()

    return _build_event_xml(cot_type, uid, lat, lon, hae, callsign, cfg.COT_STALE_SECONDS)


def publish(mode: str, event: dict[str, Any], event_type: str | None = None) -> None:
    """Send a decoded event to the configured CoT destination, if enabled.

    Args:
        mode:       Source module name (e.g. 'adsb', 'ais', 'aprs').
        event:      The decoded event dict.
        event_type: Optional sub-type string (unused for CoT — position-only export).
    """
    if not _is_enabled():
        return

    xml = event_to_cot_xml(mode, event)
    if xml is None:
        return

    cfg = _get_config()
    payload = xml.encode("utf-8")

    try:
        if cfg.COT_PROTO == "tcp":
            with socket.create_connection((cfg.COT_HOST, cfg.COT_PORT), timeout=5) as sock:
                sock.sendall(payload)
        else:
            _get_udp_socket().sendto(payload, (cfg.COT_HOST, cfg.COT_PORT))
    except Exception as exc:
        logger.debug("CoT send error to %s:%d: %s", cfg.COT_HOST, cfg.COT_PORT, exc)
