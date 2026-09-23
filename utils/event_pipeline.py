"""Shared event pipeline for alerts and recordings."""

from __future__ import annotations

from typing import Any

from utils.alerts import get_alert_manager
from utils.cot import publish as cot_publish
from utils.mqtt import publish as mqtt_publish
from utils.recording import get_recording_manager
from utils.temporal_patterns import get_pattern_detector

IGNORE_TYPES = {"keepalive", "ping"}


DEVICE_ID_FIELDS = (
    "device_id",
    "id",
    "mac",
    "mac_address",
    "address",
    "bssid",
    "station_mac",
    "client_mac",
    "icao",
    "callsign",
    "mmsi",
    "uuid",
    "hash",
)


def process_event(mode: str, event: dict | Any, event_type: str | None = None) -> None:
    if event_type in IGNORE_TYPES:
        return
    if not isinstance(event, dict):
        return

    device_id = _extract_device_id(event)
    if device_id:
        try:
            get_pattern_detector().record_event(device_id=device_id, mode=mode)
        except Exception:
            # Pattern tracking should not break ingest pipeline
            pass

    try:
        get_recording_manager().record_event(mode, event, event_type)
    except Exception:
        # Recording failures should never break streaming
        pass

    try:
        get_alert_manager().process_event(mode, event, event_type)
    except Exception:
        # Alert failures should never break streaming
        pass

    try:
        mqtt_publish(mode, event, event_type)
    except Exception:
        # MQTT failures should never break streaming
        pass

    try:
        cot_publish(mode, event, event_type)
    except Exception:
        # CoT failures should never break streaming
        pass


def _extract_device_id(event: dict) -> str | None:
    for field in DEVICE_ID_FIELDS:
        value = event.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text

    nested_candidates = ("target", "device", "source", "aircraft", "vessel")
    for key in nested_candidates:
        nested = event.get(key)
        if isinstance(nested, dict):
            nested_id = _extract_device_id(nested)
            if nested_id:
                return nested_id
    return None
