"""
SQLite database utilities for persistent settings storage.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger("intercept.database")

# Database file location
DB_DIR = Path(__file__).parent.parent / "instance"
DB_PATH = DB_DIR / "intercept.db"

# Per-greenlet (or per-thread) local storage for connections.
# Under gunicorn + gevent, all greenlets share a single OS thread, so
# threading.local() would give every concurrent request the same connection,
# causing sqlite3.ProgrammingError / database-locked crashes.  Use gevent's
# local() when available so each greenlet gets its own connection.
try:
    from gevent.local import local as _LocalClass
except ImportError:
    _LocalClass = threading.local  # type: ignore[assignment]

_local = _LocalClass()


def get_instance_dir() -> Path:
    """Return the SQLite directory, honoring INTERCEPT_INSTANCE_DIR."""
    override = os.environ.get("INTERCEPT_INSTANCE_DIR", "").strip()
    if override:
        return Path(override)
    return DB_DIR


def get_db_path() -> Path:
    """Get the database file path, creating directory if needed."""
    instance_dir = get_instance_dir()
    instance_dir.mkdir(parents=True, exist_ok=True)
    try:
        if instance_dir.resolve() == Path(DB_DIR).resolve():
            return DB_PATH
    except OSError:
        if instance_dir == DB_DIR:
            return DB_PATH
    return instance_dir / "intercept.db"


def _journal_mode_for(db_path: Path) -> str:
    """WAL locally; DELETE on Unraid user shares and other FUSE/network mounts."""
    allowed = {"DELETE", "WAL", "TRUNCATE", "MEMORY"}
    try:
        from utils.unraid import recommended_sqlite_journal_mode

        mode = recommended_sqlite_journal_mode(db_path)
    except Exception:
        return "WAL"
    return mode if mode in allowed else "WAL"


def get_connection() -> sqlite3.Connection:
    """Get a thread-local database connection."""
    if not hasattr(_local, "connection") or _local.connection is None:
        db_path = get_db_path()
        try:
            _local.connection = sqlite3.connect(str(db_path), check_same_thread=False)
            _local.connection.row_factory = sqlite3.Row
            # Enable foreign keys
            _local.connection.execute("PRAGMA foreign_keys = ON")
            journal_mode = _journal_mode_for(db_path)
            _local.connection.execute(f"PRAGMA journal_mode = {journal_mode}")
        except sqlite3.OperationalError as e:
            logger.error(
                f"Cannot open database at {db_path}: {e}. "
                f"If the file is owned by root, fix with: "
                f"sudo chown -R $(whoami) {get_instance_dir()}"
            )
            raise
    return _local.connection


@contextmanager
def get_db():
    """Context manager for database operations."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _check_db_writable(db_path: Path) -> None:
    """Verify the database file and directory are writable, raising a clear
    error with fix instructions if they are not."""
    import os

    # Check directory writability (needed for SQLite journal/WAL files)
    db_dir = db_path.parent
    if db_dir.exists() and not os.access(db_dir, os.W_OK):
        owner = _file_owner(db_dir)
        msg = (
            f"Database directory {db_dir} is not writable (owned by {owner}). "
            f"Fix with: sudo chown -R $(whoami) {db_dir}"
        )
        logger.error(msg)
        raise sqlite3.OperationalError(msg)

    # Check file writability if it already exists
    if db_path.exists() and not os.access(db_path, os.W_OK):
        owner = _file_owner(db_path)
        msg = f"Database {db_path} is not writable (owned by {owner}). Fix with: sudo chown -R $(whoami) {db_dir}"
        logger.error(msg)
        raise sqlite3.OperationalError(msg)


def _file_owner(path: Path) -> str:
    """Return the owner username of a file, or UID if lookup fails."""
    try:
        import pwd

        return pwd.getpwuid(path.stat().st_uid).pw_name
    except (ImportError, KeyError):
        return str(path.stat().st_uid)


def init_db() -> None:
    """Initialize the database schema."""
    db_path = get_db_path()
    logger.info(f"Initializing database at {db_path}")

    _check_db_writable(db_path)

    with get_db() as conn:
        # Settings table for key-value storage
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                value_type TEXT DEFAULT 'string',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Signal history table for graphs
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                device_id TEXT NOT NULL,
                signal_strength REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT
            )
        """)

        # Create index for faster queries
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_history_mode_device
            ON signal_history(mode, device_id, timestamp)
        """)

        # Device correlation table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS device_correlations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wifi_mac TEXT,
                bt_mac TEXT,
                confidence REAL,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT,
                UNIQUE(wifi_mac, bt_mac)
            )
        """)

        # Alert rules
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mode TEXT,
                event_type TEXT,
                match TEXT,
                severity TEXT DEFAULT 'medium',
                enabled BOOLEAN DEFAULT 1,
                notify TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Alert events
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER,
                mode TEXT,
                event_type TEXT,
                severity TEXT DEFAULT 'medium',
                title TEXT,
                message TEXT,
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (rule_id) REFERENCES alert_rules(id) ON DELETE SET NULL
            )
        """)

        # Session recordings
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recording_sessions (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                label TEXT,
                started_at TIMESTAMP NOT NULL,
                stopped_at TIMESTAMP,
                file_path TEXT NOT NULL,
                event_count INTEGER DEFAULT 0,
                size_bytes INTEGER DEFAULT 0,
                metadata TEXT
            )
        """)

        # Alert rules
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mode TEXT,
                event_type TEXT,
                match TEXT,
                severity TEXT DEFAULT 'medium',
                enabled BOOLEAN DEFAULT 1,
                notify TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Alert events
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER,
                mode TEXT,
                event_type TEXT,
                severity TEXT DEFAULT 'medium',
                title TEXT,
                message TEXT,
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (rule_id) REFERENCES alert_rules(id) ON DELETE SET NULL
            )
        """)

        # Session recordings
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recording_sessions (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                label TEXT,
                started_at TIMESTAMP NOT NULL,
                stopped_at TIMESTAMP,
                file_path TEXT NOT NULL,
                event_count INTEGER DEFAULT 0,
                size_bytes INTEGER DEFAULT 0,
                metadata TEXT
            )
        """)

        # Users table for authentication
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        from config import ADMIN_PASSWORD, ADMIN_USERNAME

        cursor = conn.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            # First run — seed the admin user from config / env vars.
            admin_password = ADMIN_PASSWORD
            if not admin_password:
                import secrets as _secrets

                admin_password = _secrets.token_urlsafe(16)
                logger.warning(f"Generated admin password: {admin_password}")
                logger.warning("Set INTERCEPT_ADMIN_PASSWORD env var to use a fixed password.")
                try:
                    pw_path = Path("instance/.initial_password")
                    pw_path.parent.mkdir(parents=True, exist_ok=True)
                    pw_path.write_text(f"{ADMIN_USERNAME}:{admin_password}\n")
                except OSError as e:
                    logger.warning(f"Could not write initial password file: {e}")

            logger.info(f"Creating default admin user: {ADMIN_USERNAME}")
            hashed_pw = generate_password_hash(admin_password)

            conn.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (?, ?, ?)
            """,
                (ADMIN_USERNAME, hashed_pw, "admin"),
            )
        elif ADMIN_PASSWORD:
            # Sync admin credentials from config on every startup so that
            # changes to config.py / env vars take effect without wiping the DB.
            row = conn.execute(
                "SELECT password_hash FROM users WHERE username = ? AND role = ?",
                (ADMIN_USERNAME, "admin"),
            ).fetchone()
            if row:
                if not check_password_hash(row["password_hash"], ADMIN_PASSWORD):
                    conn.execute(
                        "UPDATE users SET password_hash = ? WHERE username = ? AND role = ?",
                        (generate_password_hash(ADMIN_PASSWORD), ADMIN_USERNAME, "admin"),
                    )
                    logger.info(f"Admin password updated from config for user '{ADMIN_USERNAME}'.")
            else:
                # Admin user doesn't exist (maybe renamed) — create it.
                conn.execute(
                    "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                    (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD), "admin"),
                )
                logger.info(f"Created admin user '{ADMIN_USERNAME}' from config.")
        # =====================================================================
        # TSCM (Technical Surveillance Countermeasures) Tables
        # =====================================================================

        # TSCM Baselines - Environment snapshots for comparison
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_baselines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                location TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                wifi_networks TEXT,
                wifi_clients TEXT,
                bt_devices TEXT,
                rf_frequencies TEXT,
                gps_coords TEXT,
                is_active BOOLEAN DEFAULT 0
            )
        """)

        # Ensure new columns exist for older databases
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tscm_baselines)")}
            if "wifi_clients" not in columns:
                conn.execute("ALTER TABLE tscm_baselines ADD COLUMN wifi_clients TEXT")
        except Exception as e:
            logger.debug(f"Schema update skipped for tscm_baselines: {e}")

        # TSCM Sweeps - Individual sweep sessions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_sweeps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                baseline_id INTEGER,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                status TEXT DEFAULT 'running',
                sweep_type TEXT,
                wifi_enabled BOOLEAN DEFAULT 1,
                bt_enabled BOOLEAN DEFAULT 1,
                rf_enabled BOOLEAN DEFAULT 1,
                results TEXT,
                anomalies TEXT,
                threats_found INTEGER DEFAULT 0,
                FOREIGN KEY (baseline_id) REFERENCES tscm_baselines(id)
            )
        """)

        # TSCM Threats - Detected threats/anomalies
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_threats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sweep_id INTEGER,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                threat_type TEXT NOT NULL,
                severity TEXT DEFAULT 'medium',
                source TEXT,
                identifier TEXT,
                name TEXT,
                signal_strength INTEGER,
                frequency REAL,
                details TEXT,
                acknowledged BOOLEAN DEFAULT 0,
                notes TEXT,
                gps_coords TEXT,
                FOREIGN KEY (sweep_id) REFERENCES tscm_sweeps(id)
            )
        """)

        # TSCM Scheduled Sweeps
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                baseline_id INTEGER,
                zone_name TEXT,
                cron_expression TEXT,
                sweep_type TEXT DEFAULT 'standard',
                enabled BOOLEAN DEFAULT 1,
                last_run TIMESTAMP,
                next_run TIMESTAMP,
                notify_on_threat BOOLEAN DEFAULT 1,
                notify_email TEXT,
                FOREIGN KEY (baseline_id) REFERENCES tscm_baselines(id)
            )
        """)

        # TSCM Device Timelines - Periodic observations per device
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_device_timelines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_identifier TEXT NOT NULL,
                protocol TEXT NOT NULL,
                sweep_id INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                rssi INTEGER,
                presence BOOLEAN DEFAULT 1,
                channel INTEGER,
                frequency REAL,
                attributes TEXT,
                FOREIGN KEY (sweep_id) REFERENCES tscm_sweeps(id)
            )
        """)

        # TSCM Known-Good Registry - Whitelist of expected devices
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_known_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL UNIQUE,
                protocol TEXT NOT NULL,
                name TEXT,
                description TEXT,
                location TEXT,
                scope TEXT DEFAULT 'global',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                added_by TEXT,
                last_verified TIMESTAMP,
                score_modifier INTEGER DEFAULT -2,
                metadata TEXT
            )
        """)

        # TSCM Cases - Grouping sweeps, threats, and notes
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                location TEXT,
                status TEXT DEFAULT 'open',
                priority TEXT DEFAULT 'normal',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP,
                created_by TEXT,
                assigned_to TEXT,
                notes TEXT,
                metadata TEXT
            )
        """)

        # TSCM Case Sweeps - Link sweeps to cases
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_case_sweeps (
                case_id INTEGER,
                sweep_id INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (case_id, sweep_id),
                FOREIGN KEY (case_id) REFERENCES tscm_cases(id),
                FOREIGN KEY (sweep_id) REFERENCES tscm_sweeps(id)
            )
        """)

        # TSCM Case Threats - Link threats to cases
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_case_threats (
                case_id INTEGER,
                threat_id INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (case_id, threat_id),
                FOREIGN KEY (case_id) REFERENCES tscm_cases(id),
                FOREIGN KEY (threat_id) REFERENCES tscm_threats(id)
            )
        """)

        # TSCM Case Notes - Notes attached to cases
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_case_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER,
                content TEXT NOT NULL,
                note_type TEXT DEFAULT 'general',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT,
                FOREIGN KEY (case_id) REFERENCES tscm_cases(id)
            )
        """)

        # TSCM Meeting Windows - Track sensitive periods
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_meeting_windows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sweep_id INTEGER,
                name TEXT,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                location TEXT,
                notes TEXT,
                FOREIGN KEY (sweep_id) REFERENCES tscm_sweeps(id)
            )
        """)

        # TSCM Sweep Capabilities - Store sweep capability snapshot
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tscm_sweep_capabilities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sweep_id INTEGER UNIQUE,
                capabilities TEXT NOT NULL,
                limitations TEXT,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sweep_id) REFERENCES tscm_sweeps(id)
            )
        """)

        # TSCM indexes for performance
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tscm_threats_sweep
            ON tscm_threats(sweep_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tscm_threats_severity
            ON tscm_threats(severity, detected_at)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tscm_sweeps_baseline
            ON tscm_sweeps(baseline_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tscm_timelines_device
            ON tscm_device_timelines(device_identifier, timestamp)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tscm_known_devices_identifier
            ON tscm_known_devices(identifier)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tscm_cases_status
            ON tscm_cases(status, created_at)
        """)

        # =====================================================================
        # DSC (Digital Selective Calling) Tables
        # =====================================================================

        # DSC Alerts - Permanent storage for DISTRESS/URGENCY messages
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dsc_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source_mmsi TEXT NOT NULL,
                source_name TEXT,
                dest_mmsi TEXT,
                format_code TEXT NOT NULL,
                category TEXT NOT NULL,
                nature_of_distress TEXT,
                latitude REAL,
                longitude REAL,
                raw_message TEXT,
                acknowledged BOOLEAN DEFAULT 0,
                notes TEXT
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dsc_alerts_category
            ON dsc_alerts(category, received_at)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dsc_alerts_mmsi
            ON dsc_alerts(source_mmsi, received_at)
        """)

        # =====================================================================
        # Remote Agent Tables (for distributed/controller mode)
        # =====================================================================

        # Remote agents registry
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                base_url TEXT NOT NULL,
                description TEXT,
                api_key TEXT,
                capabilities TEXT,
                interfaces TEXT,
                gps_coords TEXT,
                last_seen TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
        """)

        # Push payloads received from remote agents
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_payloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                scan_type TEXT NOT NULL,
                interface TEXT,
                payload TEXT NOT NULL,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (agent_id) REFERENCES agents(id)
            )
        """)

        # Indexes for agent tables
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_agents_name
            ON agents(name)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_push_payloads_agent
            ON push_payloads(agent_id, received_at)
        """)

        # Tracked satellites table for persistent satellite management
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_satellites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                norad_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                tle_line1 TEXT,
                tle_line2 TEXT,
                enabled BOOLEAN DEFAULT 1,
                builtin BOOLEAN DEFAULT 0,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Seed builtin satellites if not already present
        conn.execute("""
            INSERT OR IGNORE INTO tracked_satellites (norad_id, name, tle_line1, tle_line2, enabled, builtin)
            VALUES ('25544', 'ISS (ZARYA)', NULL, NULL, 1, 1)
        """)
        conn.execute("""
            INSERT OR IGNORE INTO tracked_satellites (norad_id, name, tle_line1, tle_line2, enabled, builtin)
            VALUES ('40069', 'METEOR-M2', NULL, NULL, 1, 1)
        """)
        conn.execute("""
            INSERT OR IGNORE INTO tracked_satellites (norad_id, name, tle_line1, tle_line2, enabled, builtin)
            VALUES ('57166', 'METEOR-M2-3', NULL, NULL, 1, 1)
        """)
        conn.execute("""
            INSERT OR IGNORE INTO tracked_satellites (norad_id, name, tle_line1, tle_line2, enabled, builtin)
            VALUES ('59051', 'METEOR-M2-4', NULL, NULL, 1, 1)
        """)

        # =====================================================================
        # Ground Station Tables (automated observations, IQ recordings)
        # =====================================================================

        # Observation profiles — per-satellite capture configuration
        conn.execute("""
            CREATE TABLE IF NOT EXISTS observation_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                norad_id INTEGER UNIQUE NOT NULL,
                name TEXT NOT NULL,
                frequency_mhz REAL NOT NULL,
                decoder_type TEXT NOT NULL DEFAULT 'fm',
                tasks_json TEXT,
                gain REAL DEFAULT 40.0,
                bandwidth_hz INTEGER DEFAULT 200000,
                min_elevation REAL DEFAULT 10.0,
                enabled BOOLEAN DEFAULT 1,
                record_iq BOOLEAN DEFAULT 0,
                iq_sample_rate INTEGER DEFAULT 2400000,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Observation history — one row per captured pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ground_station_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER,
                norad_id INTEGER NOT NULL,
                satellite TEXT NOT NULL,
                aos_time TEXT,
                los_time TEXT,
                status TEXT DEFAULT 'scheduled',
                output_path TEXT,
                packets_decoded INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (profile_id) REFERENCES observation_profiles(id) ON DELETE SET NULL
            )
        """)

        # Per-observation events (packets decoded, Doppler updates, etc.)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ground_station_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id INTEGER,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (observation_id) REFERENCES ground_station_observations(id) ON DELETE CASCADE
            )
        """)

        # SigMF recordings — one row per IQ recording file pair
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sigmf_recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id INTEGER,
                sigmf_data_path TEXT NOT NULL,
                sigmf_meta_path TEXT NOT NULL,
                size_bytes INTEGER DEFAULT 0,
                sample_rate INTEGER,
                center_freq_hz INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (observation_id) REFERENCES ground_station_observations(id) ON DELETE SET NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ground_station_outputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id INTEGER,
                norad_id INTEGER,
                output_type TEXT NOT NULL,
                backend TEXT,
                file_path TEXT NOT NULL,
                preview_path TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (observation_id) REFERENCES ground_station_observations(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ground_station_decode_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id INTEGER,
                norad_id INTEGER,
                backend TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                input_path TEXT,
                output_dir TEXT,
                error_message TEXT,
                details_json TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (observation_id) REFERENCES ground_station_observations(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_gs_observations_norad
            ON ground_station_observations(norad_id, created_at)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_gs_events_observation
            ON ground_station_events(observation_id, timestamp)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_gs_outputs_observation
            ON ground_station_outputs(observation_id, created_at)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_gs_decode_jobs_observation
            ON ground_station_decode_jobs(observation_id, created_at)
        """)

        # Lightweight schema migrations for existing installs.
        profile_cols = {row["name"] for row in conn.execute("PRAGMA table_info(observation_profiles)")}
        if "tasks_json" not in profile_cols:
            conn.execute("ALTER TABLE observation_profiles ADD COLUMN tasks_json TEXT")

        logger.info("Database initialized successfully")


def close_db() -> None:
    """Close the per-greenlet (or per-thread) database connection."""
    if hasattr(_local, "connection") and _local.connection is not None:
        _local.connection.close()
        _local.connection = None


# =============================================================================
# Settings Functions
# =============================================================================


def get_setting(key: str, default: Any = None) -> Any:
    """
    Get a setting value by key.

    Args:
        key: Setting key
        default: Default value if not found

    Returns:
        Setting value (auto-converted from JSON for complex types)
    """
    try:
        with get_db() as conn:
            cursor = conn.execute("SELECT value, value_type FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()

            if row is None:
                return default

            value, value_type = row["value"], row["value_type"]

            # Convert based on type
            if value_type == "json":
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return default
            elif value_type == "int":
                return int(value)
            elif value_type == "float":
                return float(value)
            elif value_type == "bool":
                return value.lower() in ("true", "1", "yes")
            else:
                return value
    except sqlite3.OperationalError:
        logger.warning("Database unavailable reading setting '%s', using default", key)
        return default


def set_setting(key: str, value: Any) -> None:
    """
    Set a setting value.

    Args:
        key: Setting key
        value: Setting value (will be JSON-encoded for complex types)
    """
    # Determine value type and string representation
    if isinstance(value, bool):
        value_type = "bool"
        str_value = "true" if value else "false"
    elif isinstance(value, int):
        value_type = "int"
        str_value = str(value)
    elif isinstance(value, float):
        value_type = "float"
        str_value = str(value)
    elif isinstance(value, (dict, list)):
        value_type = "json"
        str_value = json.dumps(value)
    else:
        value_type = "string"
        str_value = str(value)

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, value_type, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                value_type = excluded.value_type,
                updated_at = CURRENT_TIMESTAMP
        """,
            (key, str_value, value_type),
        )


def delete_setting(key: str) -> bool:
    """
    Delete a setting.

    Args:
        key: Setting key

    Returns:
        True if setting was deleted, False if not found
    """
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        return cursor.rowcount > 0


def get_all_settings() -> dict[str, Any]:
    """Get all settings as a dictionary."""
    with get_db() as conn:
        cursor = conn.execute("SELECT key, value, value_type FROM settings")
        settings = {}

        for row in cursor:
            key, value, value_type = row["key"], row["value"], row["value_type"]

            if value_type == "json":
                try:
                    settings[key] = json.loads(value)
                except json.JSONDecodeError:
                    settings[key] = value
            elif value_type == "int":
                settings[key] = int(value)
            elif value_type == "float":
                settings[key] = float(value)
            elif value_type == "bool":
                settings[key] = value.lower() in ("true", "1", "yes")
            else:
                settings[key] = value

        return settings


# =============================================================================
# Signal History Functions
# =============================================================================


def add_signal_reading(mode: str, device_id: str, signal_strength: float, metadata: dict | None = None) -> None:
    """Add a signal strength reading."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO signal_history (mode, device_id, signal_strength, metadata)
            VALUES (?, ?, ?, ?)
        """,
            (mode, device_id, signal_strength, json.dumps(metadata) if metadata else None),
        )


def get_signal_history(mode: str, device_id: str, limit: int = 100, since_minutes: int = 60) -> list[dict]:
    """
    Get signal history for a device.

    Args:
        mode: Mode (wifi, bluetooth, adsb, etc.)
        device_id: Device identifier (MAC, ICAO, etc.)
        limit: Maximum number of readings
        since_minutes: Only get readings from last N minutes

    Returns:
        List of signal readings with timestamp
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            SELECT signal_strength, timestamp, metadata
            FROM signal_history
            WHERE mode = ? AND device_id = ?
              AND timestamp > datetime('now', ?)
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (mode, device_id, f"-{since_minutes} minutes", limit),
        )

        results = []
        for row in cursor:
            results.append(
                {
                    "signal": row["signal_strength"],
                    "timestamp": row["timestamp"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
                }
            )

        return list(reversed(results))  # Return in chronological order


def cleanup_old_signal_history(max_age_hours: int = 24) -> int:
    """
    Remove old signal history entries.

    Args:
        max_age_hours: Maximum age in hours

    Returns:
        Number of deleted entries
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            DELETE FROM signal_history
            WHERE timestamp < datetime('now', ?)
        """,
            (f"-{max_age_hours} hours",),
        )
        return cursor.rowcount


# =============================================================================
# Device Correlation Functions
# =============================================================================


def add_correlation(wifi_mac: str, bt_mac: str, confidence: float, metadata: dict | None = None) -> None:
    """Add or update a device correlation."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO device_correlations (wifi_mac, bt_mac, confidence, metadata, last_seen)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(wifi_mac, bt_mac) DO UPDATE SET
                confidence = excluded.confidence,
                last_seen = CURRENT_TIMESTAMP,
                metadata = excluded.metadata
        """,
            (wifi_mac, bt_mac, confidence, json.dumps(metadata) if metadata else None),
        )


def get_correlations(min_confidence: float = 0.5) -> list[dict]:
    """Get all device correlations above minimum confidence."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            SELECT wifi_mac, bt_mac, confidence, first_seen, last_seen, metadata
            FROM device_correlations
            WHERE confidence >= ?
            ORDER BY confidence DESC
        """,
            (min_confidence,),
        )

        results = []
        for row in cursor:
            results.append(
                {
                    "wifi_mac": row["wifi_mac"],
                    "bt_mac": row["bt_mac"],
                    "confidence": row["confidence"],
                    "first_seen": row["first_seen"],
                    "last_seen": row["last_seen"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
                }
            )

        return results


# =============================================================================
# TSCM Functions
# =============================================================================


def create_tscm_baseline(
    name: str,
    location: str | None = None,
    description: str | None = None,
    wifi_networks: list | None = None,
    wifi_clients: list | None = None,
    bt_devices: list | None = None,
    rf_frequencies: list | None = None,
    gps_coords: dict | None = None,
) -> int:
    """
    Create a new TSCM baseline.

    Returns:
        The ID of the created baseline
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tscm_baselines
            (name, location, description, wifi_networks, wifi_clients, bt_devices, rf_frequencies, gps_coords)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                name,
                location,
                description,
                json.dumps(wifi_networks) if wifi_networks else None,
                json.dumps(wifi_clients) if wifi_clients else None,
                json.dumps(bt_devices) if bt_devices else None,
                json.dumps(rf_frequencies) if rf_frequencies else None,
                json.dumps(gps_coords) if gps_coords else None,
            ),
        )
        return cursor.lastrowid


def get_tscm_baseline(baseline_id: int) -> dict | None:
    """Get a specific TSCM baseline by ID."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            SELECT * FROM tscm_baselines WHERE id = ?
        """,
            (baseline_id,),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return {
            "id": row["id"],
            "name": row["name"],
            "location": row["location"],
            "description": row["description"],
            "created_at": row["created_at"],
            "wifi_networks": json.loads(row["wifi_networks"]) if row["wifi_networks"] else [],
            "wifi_clients": json.loads(row["wifi_clients"]) if row["wifi_clients"] else [],
            "bt_devices": json.loads(row["bt_devices"]) if row["bt_devices"] else [],
            "rf_frequencies": json.loads(row["rf_frequencies"]) if row["rf_frequencies"] else [],
            "gps_coords": json.loads(row["gps_coords"]) if row["gps_coords"] else None,
            "is_active": bool(row["is_active"]),
        }


def get_all_tscm_baselines() -> list[dict]:
    """Get all TSCM baselines."""
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT id, name, location, description, created_at, is_active
            FROM tscm_baselines
            ORDER BY created_at DESC
        """)

        return [dict(row) for row in cursor]


def get_active_tscm_baseline() -> dict | None:
    """Get the currently active TSCM baseline."""
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT * FROM tscm_baselines WHERE is_active = 1 LIMIT 1
        """)
        row = cursor.fetchone()

        if row is None:
            return None

        return get_tscm_baseline(row["id"])


def set_active_tscm_baseline(baseline_id: int) -> bool:
    """Set a baseline as active (deactivates others)."""
    with get_db() as conn:
        # Deactivate all
        conn.execute("UPDATE tscm_baselines SET is_active = 0")
        # Activate selected
        cursor = conn.execute("UPDATE tscm_baselines SET is_active = 1 WHERE id = ?", (baseline_id,))
        return cursor.rowcount > 0


def update_tscm_baseline(
    baseline_id: int,
    wifi_networks: list | None = None,
    wifi_clients: list | None = None,
    bt_devices: list | None = None,
    rf_frequencies: list | None = None,
) -> bool:
    """Update baseline device lists."""
    updates = []
    params = []

    if wifi_networks is not None:
        updates.append("wifi_networks = ?")
        params.append(json.dumps(wifi_networks))
    if wifi_clients is not None:
        updates.append("wifi_clients = ?")
        params.append(json.dumps(wifi_clients))
    if bt_devices is not None:
        updates.append("bt_devices = ?")
        params.append(json.dumps(bt_devices))
    if rf_frequencies is not None:
        updates.append("rf_frequencies = ?")
        params.append(json.dumps(rf_frequencies))

    if not updates:
        return False

    params.append(baseline_id)

    with get_db() as conn:
        cursor = conn.execute(f"UPDATE tscm_baselines SET {', '.join(updates)} WHERE id = ?", params)
        return cursor.rowcount > 0


def delete_tscm_baseline(baseline_id: int) -> bool:
    """Delete a TSCM baseline."""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM tscm_baselines WHERE id = ?", (baseline_id,))
        return cursor.rowcount > 0


def create_tscm_sweep(
    sweep_type: str,
    baseline_id: int | None = None,
    wifi_enabled: bool = True,
    bt_enabled: bool = True,
    rf_enabled: bool = True,
) -> int:
    """
    Create a new TSCM sweep session.

    Returns:
        The ID of the created sweep
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tscm_sweeps
            (baseline_id, sweep_type, wifi_enabled, bt_enabled, rf_enabled)
            VALUES (?, ?, ?, ?, ?)
        """,
            (baseline_id, sweep_type, wifi_enabled, bt_enabled, rf_enabled),
        )
        return cursor.lastrowid


def update_tscm_sweep(
    sweep_id: int,
    status: str | None = None,
    results: dict | None = None,
    anomalies: list | None = None,
    threats_found: int | None = None,
    completed: bool = False,
) -> bool:
    """Update a TSCM sweep."""
    updates = []
    params = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if results is not None:
        updates.append("results = ?")
        params.append(json.dumps(results))
    if anomalies is not None:
        updates.append("anomalies = ?")
        params.append(json.dumps(anomalies))
    if threats_found is not None:
        updates.append("threats_found = ?")
        params.append(threats_found)
    if completed:
        updates.append("completed_at = CURRENT_TIMESTAMP")

    if not updates:
        return False

    params.append(sweep_id)

    with get_db() as conn:
        cursor = conn.execute(f"UPDATE tscm_sweeps SET {', '.join(updates)} WHERE id = ?", params)
        return cursor.rowcount > 0


def get_tscm_sweep(sweep_id: int) -> dict | None:
    """Get a specific TSCM sweep by ID."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM tscm_sweeps WHERE id = ?", (sweep_id,))
        row = cursor.fetchone()

        if row is None:
            return None

        return {
            "id": row["id"],
            "baseline_id": row["baseline_id"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "status": row["status"],
            "sweep_type": row["sweep_type"],
            "wifi_enabled": bool(row["wifi_enabled"]),
            "bt_enabled": bool(row["bt_enabled"]),
            "rf_enabled": bool(row["rf_enabled"]),
            "results": json.loads(row["results"]) if row["results"] else None,
            "anomalies": json.loads(row["anomalies"]) if row["anomalies"] else [],
            "threats_found": row["threats_found"],
        }


def add_tscm_threat(
    sweep_id: int,
    threat_type: str,
    severity: str,
    source: str,
    identifier: str,
    name: str | None = None,
    signal_strength: int | None = None,
    frequency: float | None = None,
    details: dict | None = None,
    gps_coords: dict | None = None,
) -> int:
    """
    Add a detected threat to a TSCM sweep.

    Returns:
        The ID of the created threat
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tscm_threats
            (sweep_id, threat_type, severity, source, identifier, name,
             signal_strength, frequency, details, gps_coords)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                sweep_id,
                threat_type,
                severity,
                source,
                identifier,
                name,
                signal_strength,
                frequency,
                json.dumps(details) if details else None,
                json.dumps(gps_coords) if gps_coords else None,
            ),
        )
        return cursor.lastrowid


def get_tscm_threats(
    sweep_id: int | None = None, severity: str | None = None, acknowledged: bool | None = None, limit: int = 100
) -> list[dict]:
    """Get TSCM threats with optional filters."""
    conditions = []
    params = []

    if sweep_id is not None:
        conditions.append("sweep_id = ?")
        params.append(sweep_id)
    if severity is not None:
        conditions.append("severity = ?")
        params.append(severity)
    if acknowledged is not None:
        conditions.append("acknowledged = ?")
        params.append(1 if acknowledged else 0)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    with get_db() as conn:
        cursor = conn.execute(
            f"""
            SELECT * FROM tscm_threats
            {where_clause}
            ORDER BY detected_at DESC
            LIMIT ?
        """,
            params,
        )

        results = []
        for row in cursor:
            results.append(
                {
                    "id": row["id"],
                    "sweep_id": row["sweep_id"],
                    "detected_at": row["detected_at"],
                    "threat_type": row["threat_type"],
                    "severity": row["severity"],
                    "source": row["source"],
                    "identifier": row["identifier"],
                    "name": row["name"],
                    "signal_strength": row["signal_strength"],
                    "frequency": row["frequency"],
                    "details": json.loads(row["details"]) if row["details"] else None,
                    "acknowledged": bool(row["acknowledged"]),
                    "notes": row["notes"],
                    "gps_coords": json.loads(row["gps_coords"]) if row["gps_coords"] else None,
                }
            )

        return results


def acknowledge_tscm_threat(threat_id: int, notes: str | None = None) -> bool:
    """Acknowledge a TSCM threat."""
    with get_db() as conn:
        if notes:
            cursor = conn.execute(
                "UPDATE tscm_threats SET acknowledged = 1, notes = ? WHERE id = ?", (notes, threat_id)
            )
        else:
            cursor = conn.execute("UPDATE tscm_threats SET acknowledged = 1 WHERE id = ?", (threat_id,))
        return cursor.rowcount > 0


def get_tscm_threat_summary() -> dict:
    """Get summary counts of threats by severity."""
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT severity, COUNT(*) as count
            FROM tscm_threats
            WHERE acknowledged = 0
            GROUP BY severity
        """)

        summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        for row in cursor:
            summary[row["severity"]] = row["count"]
            summary["total"] += row["count"]

        return summary


# =============================================================================
# TSCM Device Timeline Functions
# =============================================================================


def add_device_timeline_entry(
    device_identifier: str,
    protocol: str,
    sweep_id: int | None = None,
    rssi: int | None = None,
    presence: bool = True,
    channel: int | None = None,
    frequency: float | None = None,
    attributes: dict | None = None,
) -> int:
    """Add a device timeline observation entry."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tscm_device_timelines
            (device_identifier, protocol, sweep_id, rssi, presence, channel, frequency, attributes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                device_identifier,
                protocol,
                sweep_id,
                rssi,
                presence,
                channel,
                frequency,
                json.dumps(attributes) if attributes else None,
            ),
        )
        return cursor.lastrowid


def get_device_timeline(device_identifier: str, limit: int = 100, since_hours: int = 24) -> list[dict]:
    """Get timeline entries for a device."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            SELECT * FROM tscm_device_timelines
            WHERE device_identifier = ?
              AND timestamp > datetime('now', ?)
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (device_identifier, f"-{since_hours} hours", limit),
        )

        results = []
        for row in cursor:
            results.append(
                {
                    "id": row["id"],
                    "device_identifier": row["device_identifier"],
                    "protocol": row["protocol"],
                    "sweep_id": row["sweep_id"],
                    "timestamp": row["timestamp"],
                    "rssi": row["rssi"],
                    "presence": bool(row["presence"]),
                    "channel": row["channel"],
                    "frequency": row["frequency"],
                    "attributes": json.loads(row["attributes"]) if row["attributes"] else None,
                }
            )
        return list(reversed(results))


def cleanup_old_timeline_entries(max_age_hours: int = 72) -> int:
    """Remove old timeline entries."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            DELETE FROM tscm_device_timelines
            WHERE timestamp < datetime('now', ?)
        """,
            (f"-{max_age_hours} hours",),
        )
        return cursor.rowcount


# =============================================================================
# TSCM Known-Good Registry Functions
# =============================================================================


def add_known_device(
    identifier: str,
    protocol: str,
    name: str | None = None,
    description: str | None = None,
    location: str | None = None,
    scope: str = "global",
    added_by: str | None = None,
    score_modifier: int = -2,
    metadata: dict | None = None,
) -> int:
    """Add a device to the known-good registry."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tscm_known_devices
            (identifier, protocol, name, description, location, scope, added_by, score_modifier, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(identifier) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                location = excluded.location,
                scope = excluded.scope,
                score_modifier = excluded.score_modifier,
                metadata = excluded.metadata,
                last_verified = CURRENT_TIMESTAMP
        """,
            (
                identifier.upper(),
                protocol,
                name,
                description,
                location,
                scope,
                added_by,
                score_modifier,
                json.dumps(metadata) if metadata else None,
            ),
        )
        return cursor.lastrowid


def get_known_device(identifier: str) -> dict | None:
    """Get a known device by identifier."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM tscm_known_devices WHERE identifier = ?", (identifier.upper(),))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "identifier": row["identifier"],
            "protocol": row["protocol"],
            "name": row["name"],
            "description": row["description"],
            "location": row["location"],
            "scope": row["scope"],
            "added_at": row["added_at"],
            "added_by": row["added_by"],
            "last_verified": row["last_verified"],
            "score_modifier": row["score_modifier"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
        }


def get_all_known_devices(location: str | None = None, scope: str | None = None) -> list[dict]:
    """Get all known devices, optionally filtered by location or scope."""
    conditions = []
    params = []

    if location:
        conditions.append("(location = ? OR scope = ?)")
        params.extend([location, "global"])
    if scope:
        conditions.append("scope = ?")
        params.append(scope)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with get_db() as conn:
        cursor = conn.execute(
            f"""
            SELECT * FROM tscm_known_devices
            {where_clause}
            ORDER BY added_at DESC
        """,
            params,
        )

        return [
            {
                "id": row["id"],
                "identifier": row["identifier"],
                "protocol": row["protocol"],
                "name": row["name"],
                "description": row["description"],
                "location": row["location"],
                "scope": row["scope"],
                "added_at": row["added_at"],
                "added_by": row["added_by"],
                "last_verified": row["last_verified"],
                "score_modifier": row["score_modifier"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
            }
            for row in cursor
        ]


def delete_known_device(identifier: str) -> bool:
    """Remove a device from the known-good registry."""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM tscm_known_devices WHERE identifier = ?", (identifier.upper(),))
        return cursor.rowcount > 0


# =============================================================================
# TSCM Schedule Functions
# =============================================================================


def create_tscm_schedule(
    name: str,
    cron_expression: str,
    sweep_type: str = "standard",
    baseline_id: int | None = None,
    zone_name: str | None = None,
    enabled: bool = True,
    notify_on_threat: bool = True,
    notify_email: str | None = None,
    last_run: str | None = None,
    next_run: str | None = None,
) -> int:
    """Create a new TSCM sweep schedule."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tscm_schedules
            (name, baseline_id, zone_name, cron_expression, sweep_type,
             enabled, last_run, next_run, notify_on_threat, notify_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                name,
                baseline_id,
                zone_name,
                cron_expression,
                sweep_type,
                1 if enabled else 0,
                last_run,
                next_run,
                1 if notify_on_threat else 0,
                notify_email,
            ),
        )
        return cursor.lastrowid


def get_tscm_schedule(schedule_id: int) -> dict | None:
    """Get a TSCM schedule by ID."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM tscm_schedules WHERE id = ?", (schedule_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_tscm_schedules(enabled: bool | None = None, limit: int = 200) -> list[dict]:
    """Get all TSCM schedules."""
    conditions = []
    params = []

    if enabled is not None:
        conditions.append("enabled = ?")
        params.append(1 if enabled else 0)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    with get_db() as conn:
        cursor = conn.execute(
            f"""
            SELECT * FROM tscm_schedules
            {where_clause}
            ORDER BY id DESC
            LIMIT ?
        """,
            params,
        )
        return [dict(row) for row in cursor]


def update_tscm_schedule(schedule_id: int, **fields) -> bool:
    """Update a TSCM schedule."""
    if not fields:
        return False

    updates = []
    params = []

    for key, value in fields.items():
        updates.append(f"{key} = ?")
        params.append(value)

    params.append(schedule_id)

    with get_db() as conn:
        cursor = conn.execute(f"UPDATE tscm_schedules SET {', '.join(updates)} WHERE id = ?", params)
        return cursor.rowcount > 0


def delete_tscm_schedule(schedule_id: int) -> bool:
    """Delete a TSCM schedule."""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM tscm_schedules WHERE id = ?", (schedule_id,))
        return cursor.rowcount > 0


def is_known_good_device(identifier: str, location: str | None = None) -> dict | None:
    """Check if a device is in the known-good registry for a location."""
    with get_db() as conn:
        if location:
            cursor = conn.execute(
                """
                SELECT * FROM tscm_known_devices
                WHERE identifier = ? AND (location = ? OR scope = 'global')
            """,
                (identifier.upper(), location),
            )
        else:
            cursor = conn.execute("SELECT * FROM tscm_known_devices WHERE identifier = ?", (identifier.upper(),))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "identifier": row["identifier"],
            "name": row["name"],
            "score_modifier": row["score_modifier"],
            "scope": row["scope"],
        }


# =============================================================================
# TSCM Case Functions
# =============================================================================


def create_tscm_case(
    name: str,
    description: str | None = None,
    location: str | None = None,
    priority: str = "normal",
    created_by: str | None = None,
    metadata: dict | None = None,
) -> int:
    """Create a new TSCM case."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tscm_cases
            (name, description, location, priority, created_by, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (name, description, location, priority, created_by, json.dumps(metadata) if metadata else None),
        )
        return cursor.lastrowid


def get_tscm_case(case_id: int) -> dict | None:
    """Get a TSCM case by ID."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM tscm_cases WHERE id = ?", (case_id,))
        row = cursor.fetchone()
        if not row:
            return None

        case = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "location": row["location"],
            "status": row["status"],
            "priority": row["priority"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "closed_at": row["closed_at"],
            "created_by": row["created_by"],
            "assigned_to": row["assigned_to"],
            "notes": row["notes"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
            "sweeps": [],
            "threats": [],
            "case_notes": [],
        }

        # Get linked sweeps
        cursor = conn.execute(
            """
            SELECT s.* FROM tscm_sweeps s
            JOIN tscm_case_sweeps cs ON s.id = cs.sweep_id
            WHERE cs.case_id = ?
            ORDER BY s.started_at DESC
        """,
            (case_id,),
        )
        case["sweeps"] = [dict(row) for row in cursor]

        # Get linked threats
        cursor = conn.execute(
            """
            SELECT t.* FROM tscm_threats t
            JOIN tscm_case_threats ct ON t.id = ct.threat_id
            WHERE ct.case_id = ?
            ORDER BY t.detected_at DESC
        """,
            (case_id,),
        )
        case["threats"] = [dict(row) for row in cursor]

        # Get case notes
        cursor = conn.execute(
            """
            SELECT * FROM tscm_case_notes
            WHERE case_id = ?
            ORDER BY created_at DESC
        """,
            (case_id,),
        )
        case["case_notes"] = [dict(row) for row in cursor]

        return case


def get_all_tscm_cases(status: str | None = None, limit: int = 50) -> list[dict]:
    """Get all TSCM cases."""
    conditions = []
    params = []

    if status:
        conditions.append("status = ?")
        params.append(status)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    with get_db() as conn:
        cursor = conn.execute(
            f"""
            SELECT * FROM tscm_cases
            {where_clause}
            ORDER BY updated_at DESC
            LIMIT ?
        """,
            params,
        )
        return [dict(row) for row in cursor]


def update_tscm_case(
    case_id: int,
    status: str | None = None,
    priority: str | None = None,
    assigned_to: str | None = None,
    notes: str | None = None,
) -> bool:
    """Update a TSCM case."""
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params = []

    if status:
        updates.append("status = ?")
        params.append(status)
        if status == "closed":
            updates.append("closed_at = CURRENT_TIMESTAMP")
    if priority:
        updates.append("priority = ?")
        params.append(priority)
    if assigned_to is not None:
        updates.append("assigned_to = ?")
        params.append(assigned_to)
    if notes is not None:
        updates.append("notes = ?")
        params.append(notes)

    params.append(case_id)

    with get_db() as conn:
        cursor = conn.execute(f"UPDATE tscm_cases SET {', '.join(updates)} WHERE id = ?", params)
        return cursor.rowcount > 0


def add_sweep_to_case(case_id: int, sweep_id: int) -> bool:
    """Link a sweep to a case."""
    with get_db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO tscm_case_sweeps (case_id, sweep_id)
                VALUES (?, ?)
            """,
                (case_id, sweep_id),
            )
            conn.execute("UPDATE tscm_cases SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (case_id,))
            return True
        except sqlite3.IntegrityError:
            return False


def add_threat_to_case(case_id: int, threat_id: int) -> bool:
    """Link a threat to a case."""
    with get_db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO tscm_case_threats (case_id, threat_id)
                VALUES (?, ?)
            """,
                (case_id, threat_id),
            )
            conn.execute("UPDATE tscm_cases SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (case_id,))
            return True
        except sqlite3.IntegrityError:
            return False


def add_case_note(case_id: int, content: str, note_type: str = "general", created_by: str | None = None) -> int:
    """Add a note to a case."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tscm_case_notes (case_id, content, note_type, created_by)
            VALUES (?, ?, ?, ?)
        """,
            (case_id, content, note_type, created_by),
        )
        conn.execute("UPDATE tscm_cases SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (case_id,))
        return cursor.lastrowid


# =============================================================================
# TSCM Meeting Window Functions
# =============================================================================


def start_meeting_window(
    sweep_id: int | None = None, name: str | None = None, location: str | None = None, notes: str | None = None
) -> int:
    """Start a meeting window."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tscm_meeting_windows (sweep_id, name, start_time, location, notes)
            VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
        """,
            (sweep_id, name, location, notes),
        )
        return cursor.lastrowid


def end_meeting_window(meeting_id: int) -> bool:
    """End a meeting window."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE tscm_meeting_windows
            SET end_time = CURRENT_TIMESTAMP
            WHERE id = ? AND end_time IS NULL
        """,
            (meeting_id,),
        )
        return cursor.rowcount > 0


def get_active_meeting_window(sweep_id: int | None = None) -> dict | None:
    """Get currently active meeting window."""
    with get_db() as conn:
        if sweep_id:
            cursor = conn.execute(
                """
                SELECT * FROM tscm_meeting_windows
                WHERE sweep_id = ? AND end_time IS NULL
                ORDER BY start_time DESC LIMIT 1
            """,
                (sweep_id,),
            )
        else:
            cursor = conn.execute("""
                SELECT * FROM tscm_meeting_windows
                WHERE end_time IS NULL
                ORDER BY start_time DESC LIMIT 1
            """)
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def get_meeting_windows(sweep_id: int) -> list[dict]:
    """Get all meeting windows for a sweep."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            SELECT * FROM tscm_meeting_windows
            WHERE sweep_id = ?
            ORDER BY start_time
        """,
            (sweep_id,),
        )
        return [dict(row) for row in cursor]


# =============================================================================
# TSCM Sweep Capabilities Functions
# =============================================================================


def save_sweep_capabilities(sweep_id: int, capabilities: dict, limitations: list[str] | None = None) -> int:
    """Save sweep capabilities snapshot."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tscm_sweep_capabilities (sweep_id, capabilities, limitations)
            VALUES (?, ?, ?)
            ON CONFLICT(sweep_id) DO UPDATE SET
                capabilities = excluded.capabilities,
                limitations = excluded.limitations,
                recorded_at = CURRENT_TIMESTAMP
        """,
            (sweep_id, json.dumps(capabilities), json.dumps(limitations) if limitations else None),
        )
        return cursor.lastrowid


def get_sweep_capabilities(sweep_id: int) -> dict | None:
    """Get capabilities for a sweep."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM tscm_sweep_capabilities WHERE sweep_id = ?", (sweep_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "sweep_id": row["sweep_id"],
            "capabilities": json.loads(row["capabilities"]),
            "limitations": json.loads(row["limitations"]) if row["limitations"] else [],
            "recorded_at": row["recorded_at"],
        }


# =============================================================================
# DSC (Digital Selective Calling) Functions
# =============================================================================


def store_dsc_alert(
    source_mmsi: str,
    format_code: str,
    category: str,
    source_name: str | None = None,
    dest_mmsi: str | None = None,
    nature_of_distress: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    raw_message: str | None = None,
) -> int:
    """
    Store a DSC alert (typically DISTRESS or URGENCY) to permanent storage.

    Returns:
        The ID of the created alert
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO dsc_alerts
            (source_mmsi, source_name, dest_mmsi, format_code, category,
             nature_of_distress, latitude, longitude, raw_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                source_mmsi,
                source_name,
                dest_mmsi,
                format_code,
                category,
                nature_of_distress,
                latitude,
                longitude,
                raw_message,
            ),
        )
        return cursor.lastrowid


def get_dsc_alerts(
    category: str | None = None,
    acknowledged: bool | None = None,
    source_mmsi: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """
    Get DSC alerts with optional filters.

    Args:
        category: Filter by category (DISTRESS, URGENCY, SAFETY, ROUTINE)
        acknowledged: Filter by acknowledgement status
        source_mmsi: Filter by source MMSI
        limit: Maximum number of results
        offset: Offset for pagination

    Returns:
        List of DSC alert records
    """
    conditions = []
    params = []

    if category is not None:
        conditions.append("category = ?")
        params.append(category)
    if acknowledged is not None:
        conditions.append("acknowledged = ?")
        params.append(1 if acknowledged else 0)
    if source_mmsi is not None:
        conditions.append("source_mmsi = ?")
        params.append(source_mmsi)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    with get_db() as conn:
        cursor = conn.execute(
            f"""
            SELECT * FROM dsc_alerts
            {where_clause}
            ORDER BY received_at DESC
            LIMIT ? OFFSET ?
        """,
            params,
        )

        results = []
        for row in cursor:
            results.append(
                {
                    "id": row["id"],
                    "received_at": row["received_at"],
                    "source_mmsi": row["source_mmsi"],
                    "source_name": row["source_name"],
                    "dest_mmsi": row["dest_mmsi"],
                    "format_code": row["format_code"],
                    "category": row["category"],
                    "nature_of_distress": row["nature_of_distress"],
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                    "raw_message": row["raw_message"],
                    "acknowledged": bool(row["acknowledged"]),
                    "notes": row["notes"],
                }
            )
        return results


def get_dsc_alert(alert_id: int) -> dict | None:
    """Get a specific DSC alert by ID."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM dsc_alerts WHERE id = ?", (alert_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "received_at": row["received_at"],
            "source_mmsi": row["source_mmsi"],
            "source_name": row["source_name"],
            "dest_mmsi": row["dest_mmsi"],
            "format_code": row["format_code"],
            "category": row["category"],
            "nature_of_distress": row["nature_of_distress"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "raw_message": row["raw_message"],
            "acknowledged": bool(row["acknowledged"]),
            "notes": row["notes"],
        }


def acknowledge_dsc_alert(alert_id: int, notes: str | None = None) -> bool:
    """
    Acknowledge a DSC alert.

    Args:
        alert_id: The alert ID to acknowledge
        notes: Optional notes about the acknowledgement

    Returns:
        True if alert was found and updated, False otherwise
    """
    with get_db() as conn:
        if notes:
            cursor = conn.execute("UPDATE dsc_alerts SET acknowledged = 1, notes = ? WHERE id = ?", (notes, alert_id))
        else:
            cursor = conn.execute("UPDATE dsc_alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
        return cursor.rowcount > 0


def get_dsc_alert_summary() -> dict:
    """Get summary counts of DSC alerts by category."""
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT category, COUNT(*) as count
            FROM dsc_alerts
            WHERE acknowledged = 0
            GROUP BY category
        """)

        summary = {"distress": 0, "urgency": 0, "safety": 0, "routine": 0, "total": 0}
        for row in cursor:
            cat = row["category"].lower()
            if cat in summary:
                summary[cat] = row["count"]
            summary["total"] += row["count"]

        return summary


def cleanup_old_dsc_alerts(max_age_days: int = 30) -> int:
    """
    Remove old acknowledged DSC alerts (keeps unacknowledged ones).

    Args:
        max_age_days: Maximum age in days for acknowledged alerts

    Returns:
        Number of deleted alerts
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            DELETE FROM dsc_alerts
            WHERE acknowledged = 1
              AND received_at < datetime('now', ?)
        """,
            (f"-{max_age_days} days",),
        )
        return cursor.rowcount


# =============================================================================
# Remote Agent Functions (for distributed/controller mode)
# =============================================================================


def create_agent(
    name: str,
    base_url: str,
    api_key: str | None = None,
    description: str | None = None,
    capabilities: dict | None = None,
    interfaces: dict | None = None,
    gps_coords: dict | None = None,
) -> int:
    """
    Create a new remote agent.

    Returns:
        The ID of the created agent
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO agents
            (name, base_url, api_key, description, capabilities, interfaces, gps_coords)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                name,
                base_url.rstrip("/"),
                api_key,
                description,
                json.dumps(capabilities) if capabilities else None,
                json.dumps(interfaces) if interfaces else None,
                json.dumps(gps_coords) if gps_coords else None,
            ),
        )
        return cursor.lastrowid


def get_agent(agent_id: int) -> dict | None:
    """Get an agent by ID."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_agent(row)


def get_agent_by_name(name: str) -> dict | None:
    """Get an agent by name."""
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM agents WHERE name = ?", (name,))
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_agent(row)


def _row_to_agent(row) -> dict:
    """Convert database row to agent dict."""
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "description": row["description"],
        "api_key": row["api_key"],
        "capabilities": json.loads(row["capabilities"]) if row["capabilities"] else None,
        "interfaces": json.loads(row["interfaces"]) if row["interfaces"] else None,
        "gps_coords": json.loads(row["gps_coords"]) if row["gps_coords"] else None,
        "last_seen": row["last_seen"],
        "created_at": row["created_at"],
        "is_active": bool(row["is_active"]),
    }


def list_agents(active_only: bool = True) -> list[dict]:
    """Get all agents."""
    with get_db() as conn:
        if active_only:
            cursor = conn.execute("SELECT * FROM agents WHERE is_active = 1 ORDER BY name")
        else:
            cursor = conn.execute("SELECT * FROM agents ORDER BY name")
        return [_row_to_agent(row) for row in cursor]


def update_agent(
    agent_id: int,
    base_url: str | None = None,
    description: str | None = None,
    api_key: str | None = None,
    capabilities: dict | None = None,
    interfaces: dict | None = None,
    gps_coords: dict | None = None,
    is_active: bool | None = None,
    update_last_seen: bool = False,
) -> bool:
    """Update an agent's fields."""
    updates = []
    params = []

    if base_url is not None:
        updates.append("base_url = ?")
        params.append(base_url.rstrip("/"))
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if api_key is not None:
        updates.append("api_key = ?")
        params.append(api_key)
    if capabilities is not None:
        updates.append("capabilities = ?")
        params.append(json.dumps(capabilities))
    if interfaces is not None:
        updates.append("interfaces = ?")
        params.append(json.dumps(interfaces))
    if gps_coords is not None:
        updates.append("gps_coords = ?")
        params.append(json.dumps(gps_coords))
    if is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if is_active else 0)
    if update_last_seen:
        updates.append("last_seen = CURRENT_TIMESTAMP")

    if not updates:
        return False

    params.append(agent_id)

    with get_db() as conn:
        cursor = conn.execute(f"UPDATE agents SET {', '.join(updates)} WHERE id = ?", params)
        return cursor.rowcount > 0


def delete_agent(agent_id: int) -> bool:
    """Delete an agent and its push payloads."""
    with get_db() as conn:
        # Delete push payloads first (foreign key)
        conn.execute("DELETE FROM push_payloads WHERE agent_id = ?", (agent_id,))
        cursor = conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        return cursor.rowcount > 0


def store_push_payload(
    agent_id: int, scan_type: str, payload: dict, interface: str | None = None, received_at: str | None = None
) -> int:
    """
    Store a push payload from a remote agent.

    Returns:
        The ID of the created payload record
    """
    with get_db() as conn:
        if received_at:
            cursor = conn.execute(
                """
                INSERT INTO push_payloads (agent_id, scan_type, interface, payload, received_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (agent_id, scan_type, interface, json.dumps(payload), received_at),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO push_payloads (agent_id, scan_type, interface, payload)
                VALUES (?, ?, ?, ?)
            """,
                (agent_id, scan_type, interface, json.dumps(payload)),
            )

        # Update agent last_seen
        conn.execute("UPDATE agents SET last_seen = CURRENT_TIMESTAMP WHERE id = ?", (agent_id,))

        return cursor.lastrowid


def get_recent_payloads(agent_id: int | None = None, scan_type: str | None = None, limit: int = 100) -> list[dict]:
    """Get recent push payloads, optionally filtered."""
    conditions = []
    params = []

    if agent_id is not None:
        conditions.append("p.agent_id = ?")
        params.append(agent_id)
    if scan_type is not None:
        conditions.append("p.scan_type = ?")
        params.append(scan_type)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    with get_db() as conn:
        cursor = conn.execute(
            f"""
            SELECT p.*, a.name as agent_name
            FROM push_payloads p
            JOIN agents a ON p.agent_id = a.id
            {where_clause}
            ORDER BY p.received_at DESC
            LIMIT ?
        """,
            params,
        )

        results = []
        for row in cursor:
            results.append(
                {
                    "id": row["id"],
                    "agent_id": row["agent_id"],
                    "agent_name": row["agent_name"],
                    "scan_type": row["scan_type"],
                    "interface": row["interface"],
                    "payload": json.loads(row["payload"]),
                    "received_at": row["received_at"],
                }
            )
        return results


def cleanup_old_payloads(max_age_hours: int = 24) -> int:
    """Remove old push payloads."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            DELETE FROM push_payloads
            WHERE received_at < datetime('now', ?)
        """,
            (f"-{max_age_hours} hours",),
        )
        return cursor.rowcount


# =============================================================================
# Tracked Satellites Functions
# =============================================================================


def get_tracked_satellites(enabled_only: bool = False) -> list[dict]:
    """Return all tracked satellites, optionally filtered to enabled only."""
    with get_db() as conn:
        if enabled_only:
            rows = conn.execute(
                "SELECT norad_id, name, tle_line1, tle_line2, enabled, builtin, added_at "
                "FROM tracked_satellites WHERE enabled = 1 ORDER BY builtin DESC, name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT norad_id, name, tle_line1, tle_line2, enabled, builtin, added_at "
                "FROM tracked_satellites ORDER BY builtin DESC, name"
            ).fetchall()
        return [
            {
                "norad_id": r[0],
                "name": r[1],
                "tle_line1": r[2],
                "tle_line2": r[3],
                "enabled": bool(r[4]),
                "builtin": bool(r[5]),
                "added_at": r[6],
            }
            for r in rows
        ]


def add_tracked_satellite(
    norad_id: str,
    name: str,
    tle_line1: str | None = None,
    tle_line2: str | None = None,
    enabled: bool = True,
    builtin: bool = False,
) -> bool:
    """Insert a tracked satellite. Returns True if inserted, False if duplicate."""
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO tracked_satellites "
                "(norad_id, name, tle_line1, tle_line2, enabled, builtin) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(norad_id), name, tle_line1, tle_line2, int(enabled), int(builtin)),
            )
            return conn.total_changes > 0
        except sqlite3.Error as e:
            logger.error(f"Error adding tracked satellite {norad_id}: {e}")
            return False


def bulk_add_tracked_satellites(satellites_list: list[dict]) -> int:
    """Insert many tracked satellites at once. Returns count of newly inserted."""
    if not satellites_list:
        return 0

    rows = []
    for sat in satellites_list:
        try:
            rows.append(
                (
                    str(sat["norad_id"]),
                    sat["name"],
                    sat.get("tle_line1"),
                    sat.get("tle_line2"),
                    int(sat.get("enabled", True)),
                    int(sat.get("builtin", False)),
                )
            )
        except (KeyError, TypeError) as e:
            logger.warning(f"Skipping malformed satellite entry: {e}")

    norad_ids = [r[0] for r in rows]
    placeholders = ",".join("?" * len(norad_ids))
    count_sql = f"SELECT COUNT(*) FROM tracked_satellites WHERE norad_id IN ({placeholders})"

    with get_db() as conn:
        before = conn.execute(count_sql, norad_ids).fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO tracked_satellites "
            "(norad_id, name, tle_line1, tle_line2, enabled, builtin) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        after = conn.execute(count_sql, norad_ids).fetchone()[0]
    return after - before


def update_tracked_satellite(norad_id: str, enabled: bool) -> bool:
    """Toggle enabled state for a tracked satellite."""
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE tracked_satellites SET enabled = ? WHERE norad_id = ?",
            (int(enabled), str(norad_id)),
        )
        return cursor.rowcount > 0


def remove_tracked_satellite(norad_id: str) -> tuple[bool, str]:
    """Delete a tracked satellite by NORAD ID. Refuses to delete builtins."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT builtin FROM tracked_satellites WHERE norad_id = ?",
            (str(norad_id),),
        ).fetchone()
        if row is None:
            return False, "Satellite not found"
        if row[0]:
            return False, "Cannot remove builtin satellite"
        conn.execute(
            "DELETE FROM tracked_satellites WHERE norad_id = ?",
            (str(norad_id),),
        )
        return True, "Removed"
