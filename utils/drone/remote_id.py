# utils/drone/remote_id.py
"""Remote ID scanner — WiFi beacon + BLE advertisement parsing (ASTM F3411)."""

from __future__ import annotations

import contextlib
import logging
import queue
import struct
from datetime import datetime, timezone

from .models import RemoteIDObservation

logger = logging.getLogger("intercept.drone.remote_id")

_REMOTE_ID_UUID_LE = b"\xfa\xff"
_ODID_OUI = b"\xfa\x0b\xbc"
_ODID_APP_CODE = 0x0D  # vendor element type (Wi-Fi) and application code (BLE)
_MSG_SIZE = 25
_MSG_BASIC_ID = 0x0
_MSG_LOCATION = 0x1
_MSG_OPERATOR_ID = 0x5
_MSG_PACK = 0xF

try:
    from scapy.all import AsyncSniffer, Dot11Beacon, Dot11Elt

    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False
    AsyncSniffer = None
    Dot11Beacon = Dot11Elt = None


def _messages(data: bytes) -> list[bytes]:
    """Split ASTM F3411 data (one 25-byte message or a message pack) into messages."""
    if not data:
        return []
    if data[0] >> 4 == _MSG_PACK:
        if len(data) < 3 or data[1] != _MSG_SIZE:
            return []
        return [
            data[3 + i * _MSG_SIZE : 3 + (i + 1) * _MSG_SIZE]
            for i in range(data[2])
            if 3 + (i + 1) * _MSG_SIZE <= len(data)
        ]
    return [data[:_MSG_SIZE]] if len(data) >= _MSG_SIZE else []


def _text(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def _altitude(enc: int) -> float | None:
    # 0.5 m steps from -1000 m; 0 encodes -1000 m, the "unknown" value.
    return None if enc == 0 else enc * 0.5 - 1000.0


def _observation(data: bytes, source: str) -> RemoteIDObservation | None:
    """Build an observation from the messages in ``data``; None without a usable Location."""
    serial = operator = ""
    loc = None
    for msg in _messages(data):
        msg_type = msg[0] >> 4  # message type is the high nibble, protocol version the low one
        if msg_type == _MSG_BASIC_ID and not serial:
            serial = _text(msg[2:22])
        elif msg_type == _MSG_OPERATOR_ID:
            operator = _text(msg[2:22])
        elif msg_type == _MSG_LOCATION:
            loc = msg
    if loc is None:
        return None
    flags = loc[1]
    lat_enc, lon_enc = struct.unpack_from("<ii", loc, 5)
    if lat_enc == 0 and lon_enc == 0:  # position unknown
        return None
    lat = lat_enc * 1e-7
    lon = lon_enc * 1e-7
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    # Direction: whole degrees, the E/W bit adds 180; 361 means unknown.
    heading = loc[2] + (180 if flags & 0x02 else 0)
    # Speed: 0.25 m/s steps, or 0.75 m/s steps above 63.75 m/s with the multiplier bit; 255 means unknown.
    speed_enc = loc[3]
    if speed_enc == 255:
        speed = 0.0
    elif flags & 0x01:
        speed = speed_enc * 0.75 + 63.75
    else:
        speed = speed_enc * 0.25
    alt_baro, alt_geo = struct.unpack_from("<HH", loc, 13)
    alt = _altitude(alt_geo)
    if alt is None:
        alt = _altitude(alt_baro)

    return RemoteIDObservation(
        source=source,
        serial_number=serial,
        operator_id=operator,
        lat=lat,
        lon=lon,
        altitude_m=alt if alt is not None else 0.0,
        speed_ms=speed,
        heading=float(heading) if heading <= 360 else 0.0,
        timestamp=datetime.now(timezone.utc),
    )


def _parse_ble_remote_id(adv_data: bytes) -> RemoteIDObservation | None:
    """Parse BLE advertising data carrying ASTM F3411 service data (UUID 0xFFFA, app code 0x0D)."""
    i = 0
    while i + 1 < len(adv_data):
        length = adv_data[i]
        if length == 0:
            break
        field = adv_data[i + 1 : i + 1 + length]
        # AD type 0x16, UUID 0xFFFA, application code, message counter, then the message (or pack)
        if len(field) >= 5 and field[0] == 0x16 and field[1:3] == _REMOTE_ID_UUID_LE and field[3] == _ODID_APP_CODE:
            return _observation(field[5:], source="BLE")
        i += 1 + length
    return None


def _parse_wifi_remote_id(payload: bytes, source: str = "WIFI") -> RemoteIDObservation | None:
    """Parse a Remote ID Wi-Fi vendor element body (OUI FA:0B:BC, type 0x0D, counter, message pack)."""
    if len(payload) < 5 or payload[:3] != _ODID_OUI or payload[3] != _ODID_APP_CODE:
        return None
    return _observation(payload[5:], source)


class RemoteIDScanner:
    def __init__(self, output_queue: queue.Queue) -> None:
        self._queue = output_queue
        self._sniffer = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _on_wifi_packet(self, pkt) -> None:
        if not (Dot11Beacon and pkt.haslayer(Dot11Beacon)):
            return
        elt = pkt.getlayer(Dot11Elt)
        while elt:
            if elt.ID == 221 and elt.info:
                obs = _parse_wifi_remote_id(elt.info)
                if obs:
                    with contextlib.suppress(queue.Full):
                        self._queue.put_nowait(obs)
            elt = elt.payload if hasattr(elt, "payload") and isinstance(elt.payload, Dot11Elt) else None

    def start(self, wifi_iface: str | None = None) -> None:
        if self._running:
            return
        self._running = True
        if SCAPY_AVAILABLE and wifi_iface:
            try:
                sniffer = AsyncSniffer(
                    iface=wifi_iface,
                    filter="type mgt subtype beacon",
                    prn=self._on_wifi_packet,
                    store=False,
                )
                sniffer.start()
                self._sniffer = sniffer
                logger.info("WiFi Remote ID sniffer started on %s", wifi_iface)
            except Exception as exc:
                logger.warning("WiFi Remote ID sniffer failed to start: %s", exc)
        else:
            logger.info("WiFi Remote ID unavailable (scapy=%s, iface=%s)", SCAPY_AVAILABLE, wifi_iface)

    def stop(self) -> None:
        self._running = False
        if self._sniffer:
            with contextlib.suppress(Exception):
                self._sniffer.stop()
            self._sniffer = None
