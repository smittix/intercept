"""Aircraft database for ICAO hex to type/registration lookup."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("intercept.aircraft_db")

# Database file location (persisted under data/adsb/ for Docker volume compatibility)
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "adsb")
os.makedirs(DB_DIR, exist_ok=True)
DB_FILE = os.path.join(DB_DIR, "aircraft_db.json")
DB_META_FILE = os.path.join(DB_DIR, "aircraft_db_meta.json")

# Mictronics database URLs (raw GitHub)
AIRCRAFT_DB_URL = "https://raw.githubusercontent.com/Mictronics/readsb-protobuf/dev/webapp/src/db/aircrafts.json"
TYPES_DB_URL = "https://raw.githubusercontent.com/Mictronics/readsb-protobuf/dev/webapp/src/db/types.json"
GITHUB_API_URL = (
    "https://api.github.com/repos/Mictronics/readsb-protobuf/commits?path=webapp/src/db/aircrafts.json&per_page=1"
)

# In-memory cache
_aircraft_cache: dict[str, dict[str, str]] = {}
_types_cache: dict[str, str] = {}
_cache_lock = threading.Lock()
_db_loaded = False
_db_version: str | None = None
_update_available: bool = False
_latest_version: str | None = None


def get_db_status() -> dict[str, Any]:
    """Get current database status."""
    exists = os.path.exists(DB_FILE)
    meta = _load_meta()

    return {
        "installed": exists,
        "version": meta.get("version") if meta else None,
        "downloaded": meta.get("downloaded") if meta else None,
        "aircraft_count": len(_aircraft_cache) if _db_loaded else 0,
        "update_available": _update_available,
        "latest_version": _latest_version,
    }


def _load_meta() -> dict[str, Any] | None:
    """Load database metadata."""
    try:
        if os.path.exists(DB_META_FILE):
            with open(DB_META_FILE) as f:
                return json.load(f)
    except json.JSONDecodeError as e:
        logger.warning(f"Corrupt aircraft db meta file, removing: {e}")
        with contextlib.suppress(OSError):
            os.remove(DB_META_FILE)
    except Exception as e:
        logger.warning(f"Error loading aircraft db meta: {e}")
    return None


def _save_meta(version: str) -> None:
    """Save database metadata."""
    try:
        meta = {
            "version": version,
            "downloaded": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        }
        with open(DB_META_FILE, "w") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        logger.warning(f"Error saving aircraft db meta: {e}")


def load_database() -> bool:
    """Load aircraft database into memory. Returns True if successful."""
    global _aircraft_cache, _types_cache, _db_loaded, _db_version

    if not os.path.exists(DB_FILE):
        logger.info("Aircraft database not installed")
        return False

    try:
        with _cache_lock:
            with open(DB_FILE) as f:
                data = json.load(f)

            _aircraft_cache = data.get("aircraft", {})
            _types_cache = data.get("types", {})
            _db_loaded = True

            meta = _load_meta()
            _db_version = meta.get("version") if meta else "unknown"

            logger.info(f"Loaded aircraft database: {len(_aircraft_cache)} aircraft, {len(_types_cache)} types")
            return True
    except Exception as e:
        logger.error(f"Error loading aircraft database: {e}")
        return False


def lookup(icao: str) -> dict[str, str] | None:
    """
    Look up aircraft by ICAO hex code.

    Returns dict with keys: registration, type_code, type_desc
    Or None if not found.
    """
    if not _db_loaded:
        return None

    icao_upper = icao.upper()

    with _cache_lock:
        aircraft = _aircraft_cache.get(icao_upper)
        if not aircraft:
            return None

        # Database format is array: [registration, type_code, flags, ...]
        # Handle both list format (from Mictronics) and dict format (legacy)
        if isinstance(aircraft, list):
            reg = aircraft[0] if len(aircraft) > 0 else ""
            type_code = aircraft[1] if len(aircraft) > 1 else ""
        else:
            # Dict format fallback
            reg = aircraft.get("r", "")
            type_code = aircraft.get("t", "")

        # Look up type description
        type_desc = ""
        if type_code and type_code in _types_cache:
            type_desc = _types_cache[type_code]

        return {
            "registration": reg,
            "type_code": type_code,
            "type_desc": type_desc,
        }


def check_for_updates() -> dict[str, Any]:
    """
    Check GitHub for database updates.
    Returns status dict with update_available flag.
    """
    global _update_available, _latest_version

    try:
        req = Request(GITHUB_API_URL, headers={"User-Agent": "Intercept-SIGINT"})
        with urlopen(req, timeout=10) as response:
            commits = json.loads(response.read().decode("utf-8"))

            if commits and len(commits) > 0:
                latest_sha = commits[0]["sha"][:8]
                latest_date = commits[0]["commit"]["committer"]["date"]
                _latest_version = f"{latest_date[:10]}_{latest_sha}"

                meta = _load_meta()
                current_version = meta.get("version") if meta else None

                _update_available = current_version != _latest_version

                return {
                    "success": True,
                    "current_version": current_version,
                    "latest_version": _latest_version,
                    "update_available": _update_available,
                }
    except URLError as e:
        logger.warning(f"Failed to check for updates: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.warning(f"Error checking for updates: {e}")
        return {"success": False, "error": str(e)}

    return {"success": False, "error": "Unknown error"}


def download_database(progress_callback=None) -> dict[str, Any]:
    """
    Download latest aircraft database from Mictronics repo.
    Returns status dict.
    """
    global _update_available

    try:
        if progress_callback:
            progress_callback("Downloading aircraft database...")

        # Download aircraft database
        req = Request(AIRCRAFT_DB_URL, headers={"User-Agent": "Intercept-SIGINT"})
        with urlopen(req, timeout=60) as response:
            aircraft_data = json.loads(response.read().decode("utf-8"))

        if progress_callback:
            progress_callback("Downloading type codes...")

        # Download types database
        req = Request(TYPES_DB_URL, headers={"User-Agent": "Intercept-SIGINT"})
        with urlopen(req, timeout=30) as response:
            types_data = json.loads(response.read().decode("utf-8"))

        if progress_callback:
            progress_callback("Processing database...")

        # Combine into single file
        combined = {
            "aircraft": aircraft_data,
            "types": types_data,
        }

        # Save to file
        with open(DB_FILE, "w") as f:
            json.dump(combined, f, separators=(",", ":"))  # Compact JSON

        # Get version from GitHub
        version = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        try:
            req = Request(GITHUB_API_URL, headers={"User-Agent": "Intercept-SIGINT"})
            with urlopen(req, timeout=10) as response:
                commits = json.loads(response.read().decode("utf-8"))
                if commits:
                    sha = commits[0]["sha"][:8]
                    date = commits[0]["commit"]["committer"]["date"][:10]
                    version = f"{date}_{sha}"
        except Exception:
            pass

        _save_meta(version)
        _update_available = False

        # Reload into memory
        load_database()

        return {
            "success": True,
            "message": f"Downloaded {len(aircraft_data)} aircraft, {len(types_data)} types",
            "version": version,
        }

    except URLError as e:
        logger.error(f"Download failed: {e}")
        return {"success": False, "error": f"Download failed: {e}"}
    except Exception as e:
        logger.error(f"Error downloading database: {e}")
        return {"success": False, "error": str(e)}


def delete_database() -> dict[str, Any]:
    """Delete local database files."""
    global _aircraft_cache, _types_cache, _db_loaded, _db_version

    try:
        with _cache_lock:
            _aircraft_cache = {}
            _types_cache = {}
            _db_loaded = False
            _db_version = None

        if os.path.exists(DB_FILE):
            os.remove(DB_FILE)
        if os.path.exists(DB_META_FILE):
            os.remove(DB_META_FILE)

        return {"success": True, "message": "Database deleted"}
    except Exception as e:
        return {"success": False, "error": str(e)}
