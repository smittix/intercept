"""Unified TLE store.

Single source of truth for TLE data, shared by satellite tracking,
weather-satellite prediction, SSTV doppler, and the remote agent.
Backed by SQLite; seeded once from the static data/satellites.py.

Replaces three previous stores: routes/satellite._tle_cache (which
persisted by rewriting data/satellites.py at runtime),
utils/weather_sat_predict._tle_cache, and the agent's own download.
"""

import os
import sqlite3
import threading
from pathlib import Path

from utils.logging import get_logger

logger = get_logger("intercept.tle_store")

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "instance" / "tle.db"
_DB_PATH = _DEFAULT_DB_PATH
_lock = threading.Lock()
_cache: dict[str, tuple[str, str, str]] | None = None


def _tle_db_path() -> Path:
    override = os.environ.get("INTERCEPT_INSTANCE_DIR", "").strip()
    if override:
        return Path(override) / "tle.db"
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    db_path = _tle_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5)
    try:
        from utils.unraid import recommended_sqlite_journal_mode

        journal = recommended_sqlite_journal_mode(db_path)
    except Exception:
        journal = "WAL"
    if journal not in {"DELETE", "WAL", "TRUNCATE", "MEMORY"}:
        journal = "WAL"
    conn.execute(f"PRAGMA journal_mode = {journal}")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tle_entries (
            key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            line1 TEXT NOT NULL,
            line2 TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    _seed_if_empty(conn)
    return conn


def _seed_if_empty(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM tle_entries").fetchone()[0]
    if count:
        return
    try:
        from data.satellites import TLE_SATELLITES
    except ImportError:
        logger.warning("data/satellites.py unavailable; TLE store starts empty")
        return
    conn.executemany(
        "INSERT OR REPLACE INTO tle_entries (key, name, line1, line2) VALUES (?, ?, ?, ?)",
        [(key, name, l1, l2) for key, (name, l1, l2) in TLE_SATELLITES.items()],
    )
    conn.commit()
    logger.info(f"Seeded TLE store with {len(TLE_SATELLITES)} entries")


def _load() -> dict[str, tuple[str, str, str]]:
    global _cache
    if _cache is None:
        with _lock:
            if _cache is None:
                conn = _connect()
                try:
                    rows = conn.execute("SELECT key, name, line1, line2 FROM tle_entries").fetchall()
                    # Single-statement assignment of the fully built dict —
                    # the DCL pattern is only safe because readers never see
                    # a partially populated cache
                    _cache = {key: (name, l1, l2) for key, name, l1, l2 in rows}
                finally:
                    conn.close()
    return _cache


def all_tles() -> dict[str, tuple[str, str, str]]:
    """Return all TLEs as {key: (name, line1, line2)}."""
    return dict(_load())


def get_tle(key: str) -> tuple[str, str, str] | None:
    """Return (name, line1, line2) for a satellite key, or None."""
    return _load().get(key)


def update(entries: dict[str, tuple[str, str, str]]) -> None:
    """Insert or replace TLE entries and refresh the cache."""
    if not entries:
        return
    with _lock:
        conn = _connect()
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO tle_entries (key, name, line1, line2, updated_at)"
                " VALUES (?, ?, ?, ?, datetime('now'))",
                [(key, name, l1, l2) for key, (name, l1, l2) in entries.items()],
            )
            conn.commit()
        finally:
            conn.close()
        global _cache
        _cache = None  # rebuilt on next read


def _reset_for_tests() -> None:
    """Clear the in-memory cache so the next read hits the database.

    Seeding only runs when the table is empty, so a reset never overwrites
    entries written by a test.
    """
    global _cache
    _cache = None
