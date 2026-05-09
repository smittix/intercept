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
_LOCATION_MSG_TYPE = 0x01
_MIN_LOCATION_PAYLOAD = 15

try:
    from scapy.all import AsyncSniffer, Dot11Beacon, Dot11Elt

    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False
    AsyncSniffer = None
    Dot11Beacon = Dot11Elt = None


def _parse_ble_remote_id(adv_data: bytes) -> RemoteIDObservation | None:
    """Parse a BLE advertisement containing an ASTM F3411 Remote ID payload."""
    idx = adv_data.find(_REMOTE_ID_UUID_LE)
    if idx < 0:
        return None
    payload = adv_data[idx + 2 :]
    return _parse_wifi_remote_id(payload, source="BLE")


def _parse_wifi_remote_id(payload: bytes, source: str = "WIFI") -> RemoteIDObservation | None:
    """Parse raw ASTM F3411 Location payload bytes into a RemoteIDObservation."""
    if not payload or len(payload) < 2:
        return None
    msg_type = payload[0] & 0x0F
    if msg_type != _LOCATION_MSG_TYPE:
        return None
    if len(payload) < _MIN_LOCATION_PAYLOAD:
        return None
    try:
        lat_enc, lon_enc = struct.unpack_from("<ii", payload, 2)
        alt_enc = struct.unpack_from("<H", payload, 10)[0]
        speed_enc = struct.unpack_from("<B", payload, 12)[0]
        heading_enc = struct.unpack_from("<H", payload, 13)[0]
    except struct.error:
        return None

    lat = lat_enc * 1e-7
    lon = lon_enc * 1e-7
    alt = alt_enc * 0.5 - 1000.0
    speed = speed_enc * 0.25
    heading = heading_enc * 0.01

    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None

    return RemoteIDObservation(
        source=source,
        serial_number="",
        operator_id="",
        lat=lat,
        lon=lon,
        altitude_m=alt,
        speed_ms=speed,
        heading=heading,
        timestamp=datetime.now(timezone.utc),
    )


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
