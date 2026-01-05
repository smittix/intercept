"""Configuration settings for intercept application."""

from __future__ import annotations

import logging
import os
import sys

# Application version
VERSION = "1.1.0"


def _get_env(key: str, default: str) -> str:
    """Get environment variable with default."""
    return os.environ.get(f"INTERCEPT_{key}", default)


def _get_env_int(key: str, default: int) -> int:
    """Get environment variable as integer with default."""
    try:
        return int(os.environ.get(f"INTERCEPT_{key}", str(default)))
    except ValueError:
        return default


def _get_env_float(key: str, default: float) -> float:
    """Get environment variable as float with default."""
    try:
        return float(os.environ.get(f"INTERCEPT_{key}", str(default)))
    except ValueError:
        return default


def _get_env_bool(key: str, default: bool) -> bool:
    """Get environment variable as boolean with default."""
    val = os.environ.get(f"INTERCEPT_{key}", "").lower()
    if val in ("true", "1", "yes", "on"):
        return True
    if val in ("false", "0", "no", "off"):
        return False
    return default


# Logging configuration
_log_level_str = _get_env("LOG_LEVEL", "WARNING").upper()
LOG_LEVEL = getattr(logging, _log_level_str, logging.WARNING)
LOG_FORMAT = _get_env("LOG_FORMAT", "%(asctime)s - %(levelname)s - %(message)s")

# Server settings
HOST = _get_env("HOST", "0.0.0.0")
PORT = _get_env_int("PORT", 5050)
DEBUG = _get_env_bool("DEBUG", False)
THREADED = _get_env_bool("THREADED", True)

# Default RTL-SDR settings
DEFAULT_GAIN = _get_env("DEFAULT_GAIN", "40")
DEFAULT_DEVICE = _get_env("DEFAULT_DEVICE", "0")

# Pager defaults
DEFAULT_PAGER_FREQ = _get_env("PAGER_FREQ", "929.6125M")

# Timeouts
PROCESS_TIMEOUT = _get_env_int("PROCESS_TIMEOUT", 5)
SOCKET_TIMEOUT = _get_env_int("SOCKET_TIMEOUT", 5)
SSE_TIMEOUT = _get_env_int("SSE_TIMEOUT", 1)

# WiFi settings
WIFI_UPDATE_INTERVAL = _get_env_float("WIFI_UPDATE_INTERVAL", 2.0)
AIRODUMP_HEADER_LINES = _get_env_int("AIRODUMP_HEADER_LINES", 2)

# Bluetooth settings
BT_SCAN_TIMEOUT = _get_env_int("BT_SCAN_TIMEOUT", 10)
BT_UPDATE_INTERVAL = _get_env_float("BT_UPDATE_INTERVAL", 2.0)

# ADS-B settings
ADSB_SBS_PORT = _get_env_int("ADSB_SBS_PORT", 30003)
ADSB_UPDATE_INTERVAL = _get_env_float("ADSB_UPDATE_INTERVAL", 1.0)

# Satellite settings
SATELLITE_UPDATE_INTERVAL = _get_env_int("SATELLITE_UPDATE_INTERVAL", 30)
SATELLITE_TRAJECTORY_POINTS = _get_env_int("SATELLITE_TRAJECTORY_POINTS", 30)
SATELLITE_ORBIT_MINUTES = _get_env_int("SATELLITE_ORBIT_MINUTES", 45)

def configure_logging() -> None:
    """Configure application logging."""
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, stream=sys.stderr)
    # Suppress Flask development server warning
    logging.getLogger("werkzeug").setLevel(LOG_LEVEL)
