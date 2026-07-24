"""
DSC message parser.

Parses DSC decoder JSON output and provides utility functions for
MMSI country resolution, distress nature text, etc.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from .constants import (
    CATEGORY_PRIORITY,
    DISTRESS_NATURE_CODES,
    FORMAT_CODES,
    MID_COUNTRY_MAP,
    TELECOMMAND_CODES,
    VALID_EOS,
    VALID_FORMAT_SPECIFIERS,
)

logger = logging.getLogger("intercept.dsc.parser")


def get_country_from_mmsi(mmsi: str) -> str | None:
    """
    Derive country from MMSI using Maritime Identification Digits (MID).

    The first 3 digits of a 9-digit MMSI identify the country.

    Args:
        mmsi: The MMSI number as string

    Returns:
        Country name if found, None otherwise
    """
    if not mmsi or len(mmsi) < 3:
        return None

    # Normal ship MMSI: starts with MID (3 digits)
    mid = mmsi[:3]
    if mid in MID_COUNTRY_MAP:
        return MID_COUNTRY_MAP[mid]

    # Coast station MMSI: starts with 00 + MID
    if mmsi.startswith("00") and len(mmsi) >= 5:
        mid = mmsi[2:5]
        if mid in MID_COUNTRY_MAP:
            return MID_COUNTRY_MAP[mid]

    # Group ship station MMSI: starts with 0 + MID
    if mmsi.startswith("0") and len(mmsi) >= 4:
        mid = mmsi[1:4]
        if mid in MID_COUNTRY_MAP:
            return MID_COUNTRY_MAP[mid]

    return None


def get_distress_nature_text(code: int | str) -> str:
    """Get human-readable text for distress nature code."""
    if isinstance(code, str):
        try:
            code = int(code)
        except ValueError:
            return str(code)

    return DISTRESS_NATURE_CODES.get(code, f"UNKNOWN ({code})")


def get_format_text(code: int | str) -> str:
    """Get human-readable text for format code."""
    if isinstance(code, str):
        try:
            code = int(code)
        except ValueError:
            return str(code)

    return FORMAT_CODES.get(code, f"UNKNOWN ({code})")


def get_telecommand_text(code: int | str) -> str:
    """Get human-readable text for telecommand code."""
    if isinstance(code, str):
        try:
            code = int(code)
        except ValueError:
            return str(code)

    return TELECOMMAND_CODES.get(code, f"UNKNOWN ({code})")


def get_category_priority(category: str) -> int:
    """Get priority level for a category (lower = higher priority)."""
    return CATEGORY_PRIORITY.get(category.upper(), 10)


def parse_dsc_message(raw_line: str) -> dict[str, Any] | None:
    """
    Parse DSC decoder JSON output line.

    The decoder outputs JSON lines with fields like:
    {
        "type": "dsc",
        "format": 112,
        "source_mmsi": "002320310",
        "dest_mmsi": "123456789",
        "category": "DISTRESS",
        "nature": 7,
        "nature_text": "UNDESIGNATED_DISTRESS",
        "position": {"latitude": 51.5, "longitude": -0.1},
        "telecommand1": 100,
        "telecommand2": null,
        "timestamp": "2025-01-15T12:00:00Z",
        "raw": "..."
    }

    Args:
        raw_line: Raw JSON line from decoder

    Returns:
        Parsed message dict or None if parsing fails
    """
    if not raw_line or not raw_line.strip():
        return None

    try:
        data = json.loads(raw_line.strip())
    except json.JSONDecodeError as e:
        logger.debug(f"Failed to parse DSC JSON: {e}")
        return None

    # Validate required fields
    if data.get("type") != "dsc":
        return None

    if "source_mmsi" not in data:
        return None

    # ITU-R M.493 validation: format specifier must be valid
    format_code = data.get("format")
    if format_code not in VALID_FORMAT_SPECIFIERS:
        logger.debug(f"Rejected DSC: invalid format specifier {format_code}")
        return None

    # Validate MMSIs
    source_mmsi = str(data.get("source_mmsi", ""))
    if not validate_mmsi(source_mmsi):
        logger.debug(f"Rejected DSC: invalid source MMSI {source_mmsi}")
        return None

    dest_mmsi_val = data.get("dest_mmsi")
    if dest_mmsi_val is not None:
        dest_mmsi_str = str(dest_mmsi_val)
        if not validate_mmsi(dest_mmsi_str):
            logger.debug(f"Rejected DSC: invalid dest MMSI {dest_mmsi_str}")
            return None

    # Validate raw field structure if present
    raw = data.get("raw")
    if raw is not None:
        raw_str = str(raw)
        if not re.match(r"^\d+$", raw_str):
            logger.debug("Rejected DSC: raw field contains non-digits")
            return None
        if len(raw_str) % 3 != 0:
            logger.debug("Rejected DSC: raw field length not divisible by 3")
            return None
        # Last 3-digit token must be a valid EOS symbol
        if len(raw_str) >= 3:
            last_token = int(raw_str[-3:])
            if last_token not in VALID_EOS:
                logger.debug(f"Rejected DSC: raw EOS token {last_token} not valid")
                return None

    # Validate telecommand values if present (must be 100-127)
    for tc_field in ("telecommand1", "telecommand2"):
        tc_val = data.get(tc_field)
        if tc_val is not None:
            try:
                tc_int = int(tc_val)
            except (ValueError, TypeError):
                logger.debug(f"Rejected DSC: invalid {tc_field} value {tc_val}")
                return None
            if tc_int < 100 or tc_int > 127:
                logger.debug(f"Rejected DSC: {tc_field} {tc_int} out of range 100-127")
                return None

    # Build parsed message
    msg: dict[str, Any] = {
        "type": "dsc_message",
        "source_mmsi": source_mmsi,
        "dest_mmsi": str(data.get("dest_mmsi", "")) if data.get("dest_mmsi") is not None else None,
        "format_code": format_code,
        "format_text": get_format_text(format_code),
        "category": data.get("category", "UNKNOWN").upper(),
        "timestamp": data.get("timestamp") or datetime.utcnow().isoformat(),
    }

    # Add country from MMSI
    country = get_country_from_mmsi(msg["source_mmsi"])
    if country:
        msg["source_country"] = country

    # Add distress nature if present
    if data.get("nature") is not None:
        msg["nature_code"] = data["nature"]
        msg["nature_of_distress"] = get_distress_nature_text(data["nature"])

    # Add position if present
    position = data.get("position")
    if position and isinstance(position, dict):
        lat = position.get("latitude")
        lon = position.get("longitude")
        if lat is not None and lon is not None:
            try:
                msg["latitude"] = float(lat)
                msg["longitude"] = float(lon)
            except (ValueError, TypeError):
                pass

    # Add telecommand info
    if data.get("telecommand1") is not None:
        msg["telecommand1"] = data["telecommand1"]
        msg["telecommand1_text"] = get_telecommand_text(data["telecommand1"])

    if data.get("telecommand2") is not None:
        msg["telecommand2"] = data["telecommand2"]
        msg["telecommand2_text"] = get_telecommand_text(data["telecommand2"])

    # Add EOS (End of Sequence) info
    if "eos" in data:
        msg["eos"] = data["eos"]
    if "eos_text" in data:
        msg["eos_text"] = data["eos_text"]

    # Add raw message for debugging
    if "raw" in data:
        msg["raw_message"] = data["raw"]

    # Calculate priority
    msg["priority"] = get_category_priority(msg["category"])

    # Mark if this is a critical alert
    msg["is_critical"] = msg["category"] in (
        "DISTRESS",
        "DISTRESS_RELAY",
        "URGENCY",
        "SAFETY",
        "ALL_SHIPS",
    )

    return msg


def format_dsc_for_display(msg: dict) -> str:
    """
    Format a DSC message for human-readable display.

    Args:
        msg: Parsed DSC message dict

    Returns:
        Formatted string for display
    """
    lines = []

    # Header with category and MMSI
    category = msg.get("category", "UNKNOWN")
    mmsi = msg.get("source_mmsi", "UNKNOWN")
    country = msg.get("source_country", "")

    header = f"[{category}] MMSI: {mmsi}"
    if country:
        header += f" ({country})"
    lines.append(header)

    # Format type
    fmt = msg.get("format_text", "")
    if fmt:
        lines.append(f" Format: {fmt}")

    # Destination if present
    if msg.get("dest_mmsi"):
        lines.append(f" To: {msg['dest_mmsi']}")

    # Distress nature
    if msg.get("nature_of_distress"):
        lines.append(f" Nature: {msg['nature_of_distress']}")

    # Position
    if msg.get("latitude") is not None and msg.get("longitude") is not None:
        lat = msg["latitude"]
        lon = msg["longitude"]
        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        lines.append(f" Position: {abs(lat):.4f}{lat_dir} {abs(lon):.4f}{lon_dir}")

    # Telecommand
    if msg.get("telecommand1_text"):
        lines.append(f" Request: {msg['telecommand1_text']}")

    # EOS
    if msg.get("eos_text"):
        lines.append(f" EOS: {msg['eos_text']}")

    # Timestamp
    if msg.get("timestamp"):
        lines.append(f" Time: {msg['timestamp']}")

    return "\n".join(lines)


def validate_mmsi(mmsi: str) -> bool:
    """
    Validate MMSI format.

    MMSI is a 9-digit number. Ship stations start with non-zero digit.
    Coast stations start with 00. Group stations start with 0.

    Args:
        mmsi: MMSI string to validate

    Returns:
        True if valid MMSI format
    """
    if not mmsi:
        return False

    # Must be 9 digits
    if not re.match(r"^\d{9}$", mmsi):
        return False

    # All zeros is invalid, all same digit is invalid
    if mmsi == "000000000" or all(c == mmsi[0] for c in mmsi):
        return False

    return True


def classify_mmsi(mmsi: str) -> str:
    """
    Classify MMSI type.

    Args:
        mmsi: MMSI string

    Returns:
        Classification: 'ship', 'coast', 'group', 'sar', 'aton', or 'unknown'
    """
    if not validate_mmsi(mmsi):
        return "unknown"

    first_digit = mmsi[0]
    first_two = mmsi[:2]
    first_three = mmsi[:3]

    # Coast station: starts with 00
    if first_two == "00":
        return "coast"

    # Group call: starts with 0
    if first_digit == "0":
        return "group"

    # SAR aircraft: starts with 111
    if first_three == "111":
        return "sar"

    # Aids to Navigation: starts with 99
    if first_two == "99":
        return "aton"

    # Ship station: starts with MID (2-7)
    if first_digit in "234567":
        return "ship"

    return "unknown"
