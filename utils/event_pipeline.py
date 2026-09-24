"""Shared event pipeline for alerts, recordings, MQTT and the activity feed.

Every decoded event should pass through process_event() exactly once,
whether or not a browser is watching. Ingestion points call submit(),
which hands the event to one worker thread, so a slow step (an MQTT
broker, a recording write) never holds up a decoder's reader thread.
Mode queues drained by the SSE fan-out get the same through
ingest_hook(), registered at startup.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

from utils.alerts import get_alert_manager
from utils.mqtt import publish as mqtt_publish
from utils.observations import emit_from_event
from utils.recording import get_recording_manager
from utils.temporal_patterns import get_pattern_detector

logger = logging.getLogger("intercept.event_pipeline")

IGNORE_TYPES = {"keepalive", "ping"}

_PENDING_LIMIT = 10_000
_pending: queue.Queue = queue.Queue(maxsize=_PENDING_LIMIT)
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_dropped = 0
_last_drop_warning = 0.0


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
        emit_from_event(mode, event, event_type)
    except Exception:
        # The activity feed must never break streaming either
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


# =============================================================================
# Ingestion
# =============================================================================


def submit(mode: str, event: dict | Any, event_type: str | None = None) -> None:
    """Queue an event for process_event() on the pipeline worker.

    Never blocks. If the pipeline has fallen _PENDING_LIMIT events behind,
    the event is dropped and counted rather than stalling the decoder.
    """
    global _dropped, _last_drop_warning
    if event_type in IGNORE_TYPES or not isinstance(event, dict):
        return
    _ensure_worker()
    try:
        _pending.put_nowait((mode, event, event_type))
    except queue.Full:
        _dropped += 1
        now = time.monotonic()
        if now - _last_drop_warning > 60:
            _last_drop_warning = now
            logger.warning("Event pipeline is behind; %d events dropped so far", _dropped)


def ingest_hook(mode: str) -> Callable[[Any], None]:
    """An SSE fan-out ingest hook submitting each message of a mode's queue."""

    def hook(msg: Any) -> None:
        if isinstance(msg, dict):
            submit(mode, msg, msg.get("type"))

    return hook


def drain(timeout: float = 5.0) -> bool:
    """Wait until every submitted event has been processed (tests, shutdown)."""
    deadline = time.monotonic() + timeout
    while _pending.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    return not _pending.unfinished_tasks


def _ensure_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="event-pipeline", daemon=True)
            _worker.start()


def _work() -> None:
    while True:
        mode, event, event_type = _pending.get()
        try:
            process_event(mode, event, event_type)
        except Exception as e:  # process_event guards each step; this is the last resort
            logger.debug("Event pipeline error for %s: %s", mode, e)
        finally:
            _pending.task_done()
