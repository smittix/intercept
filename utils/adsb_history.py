"""ADS-B history persistence — PostgreSQL or SQLite backend."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Iterable
from datetime import datetime, timezone

# psycopg2 is optional — only needed for PostgreSQL backend
try:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor, execute_values
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None  # type: ignore
    execute_values = None  # type: ignore
    Json = None  # type: ignore
    RealDictCursor = None  # type: ignore
    PSYCOPG2_AVAILABLE = False

from config import (
    ADSB_DB_BACKEND,
    ADSB_DB_HOST,
    ADSB_DB_NAME,
    ADSB_DB_PASSWORD,
    ADSB_DB_PORT,
    ADSB_DB_USER,
    ADSB_HISTORY_BATCH_SIZE,
    ADSB_HISTORY_ENABLED,
    ADSB_HISTORY_FLUSH_INTERVAL,
    ADSB_HISTORY_QUEUE_SIZE,
    ADSB_SQLITE_PATH,
)

logger = logging.getLogger('intercept.adsb_history')

# Resolve which backend to use at startup.
# 'auto' prefers postgres when psycopg2 is available, falls back to sqlite.
def _resolve_backend() -> str:
    if ADSB_DB_BACKEND == 'sqlite':
        return 'sqlite'
    if ADSB_DB_BACKEND == 'postgres':
        return 'postgres'
    # auto: prefer postgres if psycopg2 is installed, else sqlite
    return 'postgres' if PSYCOPG2_AVAILABLE else 'sqlite'

HISTORY_BACKEND: str = _resolve_backend()
# History is available whenever enabled — sqlite needs no server
HISTORY_AVAILABLE: bool = ADSB_HISTORY_ENABLED

_MESSAGE_FIELDS = (
    'received_at',
    'msg_time',
    'logged_time',
    'icao',
    'msg_type',
    'callsign',
    'altitude',
    'speed',
    'heading',
    'vertical_rate',
    'lat',
    'lon',
    'squawk',
    'session_id',
    'aircraft_id',
    'flight_id',
    'raw_line',
    'source_host',
)

_SNAPSHOT_FIELDS = (
    'captured_at',
    'icao',
    'callsign',
    'registration',
    'type_code',
    'type_desc',
    'altitude',
    'speed',
    'heading',
    'vertical_rate',
    'lat',
    'lon',
    'squawk',
    'source_host',
    'snapshot',
)

_MESSAGE_INSERT_PG = f"""
    INSERT INTO adsb_messages ({', '.join(_MESSAGE_FIELDS)})
    VALUES %s
"""
_MESSAGE_INSERT_SL = (
    f"INSERT INTO adsb_messages ({', '.join(_MESSAGE_FIELDS)}) "
    f"VALUES ({', '.join(['?'] * len(_MESSAGE_FIELDS))})"
)
_SNAPSHOT_INSERT_PG = f"""
    INSERT INTO adsb_snapshots ({', '.join(_SNAPSHOT_FIELDS)})
    VALUES %s
"""
_SNAPSHOT_INSERT_SL = (
    f"INSERT INTO adsb_snapshots ({', '.join(_SNAPSHOT_FIELDS)}) "
    f"VALUES ({', '.join(['?'] * len(_SNAPSHOT_FIELDS))})"
)

# ── schema helpers ────────────────────────────────────────────────────────────

def _ensure_adsb_schema(conn) -> None:
    """Create PostgreSQL tables for ADS-B history."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS adsb_messages (
                id BIGSERIAL PRIMARY KEY,
                received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                msg_time TIMESTAMPTZ,
                logged_time TIMESTAMPTZ,
                icao TEXT NOT NULL,
                msg_type SMALLINT,
                callsign TEXT,
                altitude INTEGER,
                speed INTEGER,
                heading INTEGER,
                vertical_rate INTEGER,
                lat DOUBLE PRECISION,
                lon DOUBLE PRECISION,
                squawk TEXT,
                session_id TEXT,
                aircraft_id TEXT,
                flight_id TEXT,
                raw_line TEXT,
                source_host TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_adsb_messages_icao_time
            ON adsb_messages (icao, received_at)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_adsb_messages_received_at
            ON adsb_messages (received_at)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_adsb_messages_msg_time
            ON adsb_messages (msg_time)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS adsb_snapshots (
                id BIGSERIAL PRIMARY KEY,
                captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                icao TEXT NOT NULL,
                callsign TEXT,
                registration TEXT,
                type_code TEXT,
                type_desc TEXT,
                altitude INTEGER,
                speed INTEGER,
                heading INTEGER,
                vertical_rate INTEGER,
                lat DOUBLE PRECISION,
                lon DOUBLE PRECISION,
                squawk TEXT,
                source_host TEXT,
                snapshot JSONB
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_adsb_snapshots_icao_time
            ON adsb_snapshots (icao, captured_at)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_adsb_snapshots_captured_at
            ON adsb_snapshots (captured_at)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS adsb_sessions (
                id BIGSERIAL PRIMARY KEY,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ended_at TIMESTAMPTZ,
                device_index INTEGER,
                sdr_type TEXT,
                remote_host TEXT,
                remote_port INTEGER,
                start_source TEXT,
                stop_source TEXT,
                started_by TEXT,
                stopped_by TEXT,
                notes TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_adsb_sessions_started_at
            ON adsb_sessions (started_at)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_adsb_sessions_active
            ON adsb_sessions (ended_at)
            """
        )
    conn.commit()


def _ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
    """Create SQLite tables for ADS-B history."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS adsb_messages (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at  TEXT NOT NULL DEFAULT (datetime('now')),
            msg_time     TEXT,
            logged_time  TEXT,
            icao         TEXT NOT NULL,
            msg_type     INTEGER,
            callsign     TEXT,
            altitude     INTEGER,
            speed        INTEGER,
            heading      INTEGER,
            vertical_rate INTEGER,
            lat          REAL,
            lon          REAL,
            squawk       TEXT,
            session_id   TEXT,
            aircraft_id  TEXT,
            flight_id    TEXT,
            raw_line     TEXT,
            source_host  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_adsb_messages_icao_time
            ON adsb_messages (icao, received_at);
        CREATE INDEX IF NOT EXISTS idx_adsb_messages_received_at
            ON adsb_messages (received_at);
        CREATE INDEX IF NOT EXISTS idx_adsb_messages_msg_time
            ON adsb_messages (msg_time);

        CREATE TABLE IF NOT EXISTS adsb_snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at  TEXT NOT NULL DEFAULT (datetime('now')),
            icao         TEXT NOT NULL,
            callsign     TEXT,
            registration TEXT,
            type_code    TEXT,
            type_desc    TEXT,
            altitude     INTEGER,
            speed        INTEGER,
            heading      INTEGER,
            vertical_rate INTEGER,
            lat          REAL,
            lon          REAL,
            squawk       TEXT,
            source_host  TEXT,
            snapshot     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_adsb_snapshots_icao_time
            ON adsb_snapshots (icao, captured_at);
        CREATE INDEX IF NOT EXISTS idx_adsb_snapshots_captured_at
            ON adsb_snapshots (captured_at);

        CREATE TABLE IF NOT EXISTS adsb_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at   TEXT NOT NULL DEFAULT (datetime('now')),
            ended_at     TEXT,
            device_index INTEGER,
            sdr_type     TEXT,
            remote_host  TEXT,
            remote_port  INTEGER,
            start_source TEXT,
            stop_source  TEXT,
            started_by   TEXT,
            stopped_by   TEXT,
            notes        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_adsb_sessions_started_at
            ON adsb_sessions (started_at);
        CREATE INDEX IF NOT EXISTS idx_adsb_sessions_active
            ON adsb_sessions (ended_at);
    """)


# Module-level flag so schema is only created once per process.
_schema_ensured = False
_schema_lock = threading.Lock()


def _ensure_schema(conn) -> None:
    global _schema_ensured
    if _schema_ensured:
        return
    with _schema_lock:
        if _schema_ensured:
            return
        if HISTORY_BACKEND == 'sqlite':
            _ensure_sqlite_schema(conn)
        else:
            _ensure_adsb_schema(conn)
        _schema_ensured = True


# ── connection helpers ────────────────────────────────────────────────────────

def _make_dsn() -> str:
    return (
        f"host={ADSB_DB_HOST} port={ADSB_DB_PORT} dbname={ADSB_DB_NAME} "
        f"user={ADSB_DB_USER} password={ADSB_DB_PASSWORD}"
    )


def _sqlite_dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _open_sqlite() -> sqlite3.Connection:
    db_path = ADSB_SQLITE_PATH
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = _sqlite_dict_factory
    return conn


@contextlib.contextmanager
def _history_cursor():
    """Context manager yielding a dict-cursor for the active history backend."""
    if HISTORY_BACKEND == 'sqlite':
        conn = _open_sqlite()
        _ensure_schema(conn)
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = psycopg2.connect(_make_dsn())
        _ensure_schema(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


# ── value serialisation ───────────────────────────────────────────────────────

def _to_sqlite_value(value):
    """Convert a Python value to SQLite-storable form."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# ── background writers ────────────────────────────────────────────────────────

class AdsbHistoryWriter:
    """Background writer for ADS-B message records."""

    def __init__(self) -> None:
        self.enabled = ADSB_HISTORY_ENABLED
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=ADSB_HISTORY_QUEUE_SIZE)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._conn = None
        self._dropped = 0

    def start(self) -> None:
        if not self.enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name='adsb-history-writer', daemon=True)
        self._thread.start()
        logger.info("ADS-B history writer started (backend=%s)", HISTORY_BACKEND)

    def stop(self) -> None:
        self._stop_event.set()

    def enqueue(self, record: dict) -> None:
        if not self.enabled:
            return
        if 'received_at' not in record or record['received_at'] is None:
            record['received_at'] = datetime.now(timezone.utc)
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._dropped += 1
            if self._dropped % 1000 == 0:
                logger.warning("ADS-B history queue full, dropped %d records", self._dropped)

    def _run(self) -> None:
        batch: list[dict] = []
        last_flush = time.time()

        while not self._stop_event.is_set():
            timeout = max(0.0, ADSB_HISTORY_FLUSH_INTERVAL - (time.time() - last_flush))
            try:
                item = self._queue.get(timeout=timeout)
                batch.append(item)
            except queue.Empty:
                pass

            now = time.time()
            if batch and (len(batch) >= ADSB_HISTORY_BATCH_SIZE or now - last_flush >= ADSB_HISTORY_FLUSH_INTERVAL):
                if self._flush(batch):
                    batch.clear()
                    last_flush = now

    def _ensure_connection(self):
        if self._conn:
            return self._conn
        try:
            if HISTORY_BACKEND == 'sqlite':
                conn = _open_sqlite()
                _ensure_schema(conn)
            else:
                conn = psycopg2.connect(_make_dsn())
                conn.autocommit = False
                _ensure_schema(conn)
            self._conn = conn
            return self._conn
        except Exception as exc:
            logger.warning("ADS-B history DB connection failed: %s", exc)
            self._conn = None
            return None

    def _flush(self, batch: Iterable[dict]) -> bool:
        conn = self._ensure_connection()
        if not conn:
            time.sleep(2.0)
            return False

        try:
            if HISTORY_BACKEND == 'sqlite':
                rows = [
                    tuple(_to_sqlite_value(record.get(f)) for f in _MESSAGE_FIELDS)
                    for record in batch
                ]
                conn.executemany(_MESSAGE_INSERT_SL, rows)
                conn.commit()
            else:
                values = [
                    tuple(record.get(f) for f in _MESSAGE_FIELDS)
                    for record in batch
                ]
                with conn.cursor() as cur:
                    execute_values(cur, _MESSAGE_INSERT_PG, values)
                conn.commit()
            return True
        except Exception as exc:
            logger.warning("ADS-B history insert failed: %s", exc)
            with contextlib.suppress(Exception):
                conn.rollback()
            self._conn = None
            time.sleep(2.0)
            return False


adsb_history_writer = AdsbHistoryWriter()


class AdsbSnapshotWriter:
    """Background writer for ADS-B snapshot records."""

    def __init__(self) -> None:
        self.enabled = ADSB_HISTORY_ENABLED
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=ADSB_HISTORY_QUEUE_SIZE)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._conn = None
        self._dropped = 0

    def start(self) -> None:
        if not self.enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name='adsb-snapshot-writer', daemon=True)
        self._thread.start()
        logger.info("ADS-B snapshot writer started (backend=%s)", HISTORY_BACKEND)

    def stop(self) -> None:
        self._stop_event.set()

    def enqueue(self, record: dict) -> None:
        if not self.enabled:
            return
        if 'captured_at' not in record or record['captured_at'] is None:
            record['captured_at'] = datetime.now(timezone.utc)
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._dropped += 1
            if self._dropped % 1000 == 0:
                logger.warning("ADS-B snapshot queue full, dropped %d records", self._dropped)

    def _run(self) -> None:
        batch: list[dict] = []
        last_flush = time.time()

        while not self._stop_event.is_set():
            timeout = max(0.0, ADSB_HISTORY_FLUSH_INTERVAL - (time.time() - last_flush))
            try:
                item = self._queue.get(timeout=timeout)
                batch.append(item)
            except queue.Empty:
                pass

            now = time.time()
            if batch and (len(batch) >= ADSB_HISTORY_BATCH_SIZE or now - last_flush >= ADSB_HISTORY_FLUSH_INTERVAL):
                if self._flush(batch):
                    batch.clear()
                    last_flush = now

    def _ensure_connection(self):
        if self._conn:
            return self._conn
        try:
            if HISTORY_BACKEND == 'sqlite':
                conn = _open_sqlite()
                _ensure_schema(conn)
            else:
                conn = psycopg2.connect(_make_dsn())
                conn.autocommit = False
                _ensure_schema(conn)
            self._conn = conn
            return self._conn
        except Exception as exc:
            logger.warning("ADS-B snapshot DB connection failed: %s", exc)
            self._conn = None
            return None

    def _flush(self, batch: Iterable[dict]) -> bool:
        conn = self._ensure_connection()
        if not conn:
            time.sleep(2.0)
            return False

        try:
            if HISTORY_BACKEND == 'sqlite':
                rows = []
                for record in batch:
                    row = []
                    for field in _SNAPSHOT_FIELDS:
                        value = record.get(field)
                        if field == 'snapshot' and value is not None:
                            value = json.dumps(value)
                        else:
                            value = _to_sqlite_value(value)
                        row.append(value)
                    rows.append(tuple(row))
                conn.executemany(_SNAPSHOT_INSERT_SL, rows)
                conn.commit()
            else:
                values = []
                for record in batch:
                    row = []
                    for field in _SNAPSHOT_FIELDS:
                        value = record.get(field)
                        if field == 'snapshot' and value is not None:
                            value = Json(value)
                        row.append(value)
                    values.append(tuple(row))
                with conn.cursor() as cur:
                    execute_values(cur, _SNAPSHOT_INSERT_PG, values)
                conn.commit()
            return True
        except Exception as exc:
            logger.warning("ADS-B snapshot insert failed: %s", exc)
            with contextlib.suppress(Exception):
                conn.rollback()
            self._conn = None
            time.sleep(2.0)
            return False


adsb_snapshot_writer = AdsbSnapshotWriter()
