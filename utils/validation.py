"""Input validation utilities for API endpoints."""

from __future__ import annotations

import re
from typing import Any


def escape_html(text: str | None) -> str:
    """Escape HTML special characters to prevent XSS attacks."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    html_escape_table = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    }
    return "".join(html_escape_table.get(c, c) for c in text)


def validate_latitude(lat: Any) -> float:
    """Validate and return latitude value."""
    try:
        lat_float = float(lat)
        if not -90 <= lat_float <= 90:
            raise ValueError(f"Latitude must be between -90 and 90, got {lat_float}")
        return lat_float
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid latitude: {lat}") from e


def validate_longitude(lon: Any) -> float:
    """Validate and return longitude value."""
    try:
        lon_float = float(lon)
        if not -180 <= lon_float <= 180:
            raise ValueError(f"Longitude must be between -180 and 180, got {lon_float}")
        return lon_float
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid longitude: {lon}") from e


def validate_frequency(freq: Any, min_mhz: float = 24.0, max_mhz: float = 1766.0) -> float:
    """Validate and return frequency in MHz."""
    try:
        freq_float = float(freq)
        if not min_mhz <= freq_float <= max_mhz:
            raise ValueError(f"Frequency must be between {min_mhz} and {max_mhz} MHz, got {freq_float}")
        return freq_float
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid frequency: {freq}") from e


def validate_device_index(device: Any) -> int:
    """Validate and return RTL-SDR device index."""
    try:
        device_int = int(device)
        if not 0 <= device_int <= 255:
            raise ValueError(f"Device index must be between 0 and 255, got {device_int}")
        return device_int
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid device index: {device}") from e


def validate_gain(gain: Any) -> float:
    """Validate and return gain value."""
    try:
        gain_float = float(gain)
        if not 0 <= gain_float <= 50:
            raise ValueError(f"Gain must be between 0 and 50, got {gain_float}")
        return gain_float
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid gain: {gain}") from e


def validate_ppm(ppm: Any) -> int:
    """Validate and return PPM correction value."""
    try:
        ppm_int = int(ppm)
        if not -1000 <= ppm_int <= 1000:
            raise ValueError(f"PPM must be between -1000 and 1000, got {ppm_int}")
        return ppm_int
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid PPM: {ppm}") from e


def validate_hours(hours: Any, min_hours: int = 1, max_hours: int = 168) -> int:
    """Validate and return hours value (for satellite predictions)."""
    try:
        hours_int = int(hours)
        if not min_hours <= hours_int <= max_hours:
            raise ValueError(f"Hours must be between {min_hours} and {max_hours}, got {hours_int}")
        return hours_int
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid hours: {hours}") from e


def validate_elevation(elevation: Any) -> float:
    """Validate and return elevation angle."""
    try:
        el_float = float(elevation)
        if not 0 <= el_float <= 90:
            raise ValueError(f"Elevation must be between 0 and 90, got {el_float}")
        return el_float
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid elevation: {elevation}") from e


def validate_wifi_channel(channel: Any) -> int:
    """Validate and return WiFi channel."""
    try:
        ch_int = int(channel)
        # Valid WiFi channels: 1-14 (2.4GHz), 32-177 (5GHz)
        valid_2ghz = 1 <= ch_int <= 14
        valid_5ghz = 32 <= ch_int <= 177
        if not (valid_2ghz or valid_5ghz):
            raise ValueError(f"Invalid WiFi channel: {ch_int}")
        return ch_int
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid WiFi channel: {channel}") from e


def validate_mac_address(mac: Any) -> str:
    """Validate and return MAC address."""
    if not mac or not isinstance(mac, str):
        raise ValueError("MAC address is required")
    mac = mac.upper().strip()
    if not re.match(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", mac):
        raise ValueError(f"Invalid MAC address format: {mac}")
    return mac


def validate_positive_int(value: Any, name: str = "value", max_val: int | None = None) -> int:
    """Validate and return a positive integer."""
    try:
        val_int = int(value)
        if val_int < 0:
            raise ValueError(f"{name} must be positive, got {val_int}")
        if max_val is not None and val_int > max_val:
            raise ValueError(f"{name} must be <= {max_val}, got {val_int}")
        return val_int
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid {name}: {value}") from e


def sanitize_callsign(callsign: str | None) -> str:
    """Sanitize aircraft callsign for display."""
    if not callsign:
        return ""
    # Only allow alphanumeric, dash, and space
    return re.sub(r"[^A-Za-z0-9\- ]", "", str(callsign))[:10]


def sanitize_ssid(ssid: str | None) -> str:
    """Sanitize WiFi SSID for display."""
    if not ssid:
        return ""
    # Escape HTML and limit length
    return escape_html(str(ssid)[:64])


def sanitize_device_name(name: str | None) -> str:
    """Sanitize Bluetooth device name for display."""
    if not name:
        return ""
    # Escape HTML and limit length
    return escape_html(str(name)[:64])
