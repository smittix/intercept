"""Observations: one sighting of something, from any mode.

Four concepts, and deliberately no more:

  Observation  one sighting: {ts, source, identifier, entity, rssi?, lat?, lon?, summary, raw}
  Entity       what is seen repeatedly: the identifier itself (an ICAO address, an
               MMSI, a BSSID), or for Bluetooth the identity cluster the TSCM
               engine has already resolved under MAC randomisation
  Session      a bounded time and place: tscm_cases and meeting windows already
               are this, and the feed's time filter covers the rest; no new table
  Alert        alert_rules / alert_events, unchanged

Adoption is incremental. process_event() emits for every mode that already
calls it; sensor (433 MHz) and Wi-Fi also emit where their data is ingested; the
rest emit nothing yet, and the feed works regardless.

Volume is part of the design, not a follow-up. A busy ADS-B feed produces
hundreds of position reports a minute, so each (source, identifier) is kept
to one observation per MIN_INTERVAL and each source to a token-bucket rate;
writes are batched on one thread; and retention (hours and a row cap) is
registered with the cleanup manager.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger("intercept.observations")

# Seconds between observations of the same identifier from the same source.
# Position feeds report continuously; one sighting a quarter-minute is plenty.
MIN_INTERVAL: dict[str, float] = {"adsb": 15.0, "ais": 15.0, "wifi": 10.0, "bluetooth": 10.0}
DEFAULT_MIN_INTERVAL = 5.0
# Per-source rate: sustained observations per second, and the burst allowed.
SOURCE_RATE = 5.0
SOURCE_BURST = 20.0

RAW_LIMIT = 2000  # bytes of the original event kept with each observation
FLUSH_INTERVAL = 0.5

_lock = threading.Lock()
_last_seen: dict[tuple[str, str], float] = {}
_buckets: dict[str, list[float]] = {}  # source -> [tokens, updated_at]
_dropped: dict[str, int] = {}
_emitted: dict[str, int] = {}
_direct_sources: set[str] = set()

_pending: deque = deque()
_flush_lock = threading.Lock()  # a reader's flush waits for a batch already in flight
_writer: threading.Thread | None = None
_writer_wake = threading.Event()

# Live tail for the feed's SSE stream. A real Queue: the fan-out blocks on it.
live_queue: queue.Queue = queue.Queue(maxsize=1000)


# =============================================================================
# Rate limiting
# =============================================================================


def _allow(source: str, identifier: str, now: float) -> bool:
    key = (source, identifier)
    with _lock:
        last = _last_seen.get(key)
        if last is not None and now - last < MIN_INTERVAL.get(source, DEFAULT_MIN_INTERVAL):
            _dropped[source] = _dropped.get(source, 0) + 1
            return False

        tokens, updated = _buckets.get(source, [SOURCE_BURST, now])
        tokens = min(SOURCE_BURST, tokens + max(0.0, now - updated) * SOURCE_RATE)  # a late timestamp adds nothing
        if tokens < 1.0:
            _buckets[source] = [tokens, max(now, updated)]
            _dropped[source] = _dropped.get(source, 0) + 1
            return False
        _buckets[source] = [tokens - 1.0, max(now, updated)]
        _last_seen[key] = now
        _emitted[source] = _emitted.get(source, 0) + 1
        if len(_last_seen) > 50_000:  # forget the oldest identifiers
            for stale in sorted(_last_seen, key=_last_seen.get)[:10_000]:
                del _last_seen[stale]
        return True


def stats() -> dict[str, dict[str, int]]:
    """Emitted and rate-limited counts per source since start."""
    with _lock:
        sources = set(_emitted) | set(_dropped)
        return {s: {"emitted": _emitted.get(s, 0), "rate_limited": _dropped.get(s, 0)} for s in sorted(sources)}


def reset() -> None:
    """Forget rate-limit state and pending writes (tests)."""
    with _lock:
        _last_seen.clear()
        _buckets.clear()
        _dropped.clear()
        _emitted.clear()
        _direct_sources.clear()
    _pending.clear()
    with contextlib.suppress(queue.Empty):
        while True:
            live_queue.get_nowait()


# =============================================================================
# Emitting
# =============================================================================


def _entity_for(source: str, identifier: str) -> str:
    """The identity cluster the TSCM engine resolved for a Bluetooth address,
    if it has one; otherwise the identifier. Read-only: nothing is ingested."""
    if source != "bluetooth":
        return identifier
    try:
        from utils.tscm.device_identity import get_identity_engine

        for cluster in get_identity_engine().get_clusters():
            if identifier in cluster.linked_macs or identifier.upper() in cluster.linked_macs:
                return cluster.cluster_id
    except Exception:
        pass
    return identifier


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def emit_observation(
    source: str,
    identifier: Any,
    *,
    rssi: Any = None,
    lat: Any = None,
    lon: Any = None,
    summary: str | None = None,
    raw: Any = None,
    ts: float | None = None,
    direct: bool = False,
) -> dict | None:
    """Record one sighting. Returns the observation, or None if it was
    rate-limited or had no identifier.

    `direct=True` marks a source that emits where its data is ingested, so
    process_event() does not emit for it a second time.
    """
    if direct:
        _direct_sources.add(source)
    identifier = str(identifier).strip() if identifier is not None else ""
    if not source or not identifier or len(identifier) > 128:
        return None
    now = time.time() if ts is None else float(ts)
    if not _allow(source, identifier, now):
        return None

    raw_text = None
    if raw is not None:
        with contextlib.suppress(TypeError, ValueError):
            raw_text = json.dumps(raw, default=str)[:RAW_LIMIT]
    observation = {
        "ts": now,
        "source": source,
        "identifier": identifier,
        "entity": _entity_for(source, identifier),
        "rssi": _number(rssi),
        "lat": _number(lat),
        "lon": _number(lon),
        "summary": (summary or "")[:200] or None,
        "raw": raw_text,
    }
    _pending.append(observation)
    _ensure_writer()
    with contextlib.suppress(queue.Full):
        live_queue.put_nowait({"type": "observation", **{k: v for k, v in observation.items() if k != "raw"}})
    return observation


_RSSI_FIELDS = ("rssi", "signal", "rssi_current", "signal_level", "dbm")
_SUMMARY_FIELDS = ("name", "callsign", "flight", "essid", "ssid", "model", "long_name", "vessel_name", "shipname")
# Not sightings: status chatter, and geofence events, which are alerts about a sighting already made.
_SKIP_TYPES = {
    "status",
    "info",
    "error",
    "keepalive",
    "ping",
    "raw",
    "scan_error",
    "stats",
    "sweep_progress",
    "geofence",
}


def emit_from_event(mode: str, event: dict, event_type: str | None = None) -> dict | None:
    """The process_event() hook: an observation from a mode's event, if it
    names a device and the mode does not already emit at ingestion."""
    if mode in _direct_sources or (event_type or event.get("type")) in _SKIP_TYPES:
        return None
    from utils.event_pipeline import _extract_device_id

    identifier = _extract_device_id(event)
    if not identifier:
        return None
    rssi = next((event[f] for f in _RSSI_FIELDS if event.get(f) is not None), None)
    lat = event.get("lat", event.get("latitude"))
    lon = event.get("lon", event.get("longitude"))
    summary = next((str(event[f]) for f in _SUMMARY_FIELDS if event.get(f)), None)
    return emit_observation(mode, identifier, rssi=rssi, lat=lat, lon=lon, summary=summary, raw=event)


# =============================================================================
# Storage
# =============================================================================


def _ensure_writer() -> None:
    global _writer
    if _writer is not None and _writer.is_alive():
        _writer_wake.set()
        return
    _writer = threading.Thread(target=_write_loop, name="observations-writer", daemon=True)
    _writer.start()


def _write_loop() -> None:
    from utils.database import close_db

    while True:
        _writer_wake.wait(FLUSH_INTERVAL)
        _writer_wake.clear()
        if flush():
            # Connections are cached per thread; this one lives for ever, so it
            # releases its handle rather than keep a path it never re-checks.
            close_db()


def flush() -> int:
    """Write pending observations in one transaction. Returns the count.

    Serialised, so query() sees every observation emitted before it: a batch
    the writer thread has taken but not yet committed is waited for.
    """
    with _flush_lock:
        return _flush_locked()


def _flush_locked() -> int:
    batch = []
    while _pending and len(batch) < 1000:
        batch.append(_pending.popleft())
    if not batch:
        return 0
    try:
        from utils.database import get_db

        with get_db() as conn:
            conn.executemany(
                """
                INSERT INTO observations (ts, source, identifier, entity, rssi, lat, lon, summary, raw)
                VALUES (:ts, :source, :identifier, :entity, :rssi, :lat, :lon, :summary, :raw)
            """,
                batch,
            )
    except Exception as e:
        logger.warning("Dropped %d observations: %s", len(batch), e)
        return 0
    return len(batch)


def query(
    source: list[str] | None = None,
    identifier: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = 200,
) -> list[dict]:
    """Observations, newest first, filtered by source, identifier (or entity)
    and time window."""
    from utils.database import get_db

    flush()
    conditions, params = [], []
    if source:
        conditions.append(f"source IN ({','.join('?' * len(source))})")
        params.extend(source)
    if identifier:
        conditions.append("(identifier = ? OR entity = ?)")
        params.extend([identifier, identifier])
    if since is not None:
        conditions.append("ts >= ?")
        params.append(since)
    if until is not None:
        conditions.append("ts <= ?")
        params.append(until)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT ts, source, identifier, entity, rssi, lat, lon, summary FROM observations {where} "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (*params, max(1, min(int(limit), 1000))),
        ).fetchall()
    return [dict(row) for row in rows]


def cleanup_old_observations(max_age_hours: float | None = None, max_rows: int | None = None) -> int:
    """The retention policy: drop observations older than max_age_hours, then
    the oldest beyond max_rows. Registered with the cleanup manager."""
    import config
    from utils.database import get_db

    max_age_hours = config.OBSERVATION_RETENTION_HOURS if max_age_hours is None else max_age_hours
    max_rows = config.OBSERVATION_MAX_ROWS if max_rows is None else max_rows
    flush()
    with get_db() as conn:
        deleted = conn.execute("DELETE FROM observations WHERE ts < ?", (time.time() - max_age_hours * 3600,)).rowcount
        deleted += conn.execute(
            "DELETE FROM observations WHERE id IN (SELECT id FROM observations ORDER BY ts DESC, id DESC LIMIT -1 OFFSET ?)",
            (max_rows,),
        ).rowcount
    return deleted
