"""APRS amateur radio position reporting routes."""

from __future__ import annotations

import contextlib
import csv
import json
import os
import pty
import queue
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from subprocess import PIPE
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from utils.constants import (
    PROCESS_START_WAIT,
    PROCESS_TERMINATE_TIMEOUT,
    SSE_KEEPALIVE_INTERVAL,
    SSE_QUEUE_TIMEOUT,
)
from utils.dependencies import install_hint
from utils.event_pipeline import process_event
from utils.logging import sensor_logger as logger
from utils.responses import api_error, api_success
from utils.sdr import SDRFactory, SDRType
from utils.sdr.device_config import apply_device_defaults
from utils.sse import sse_stream_fanout
from utils.validation import (
    validate_device_index,
    validate_gain,
    validate_ppm,
    validate_rtl_tcp_host,
    validate_rtl_tcp_port,
)

aprs_bp = Blueprint("aprs", __name__, url_prefix="/aprs")

# Track which SDR device is being used
aprs_active_device: int | None = None
aprs_active_sdr_type: str | None = None

# APRS frequencies by region (MHz)
APRS_FREQUENCIES = {
    "north_america": "144.390",
    "europe": "144.800",
    "uk": "144.800",
    "australia": "145.175",
    "new_zealand": "144.575",
    "argentina": "144.930",
    "brazil": "145.570",
    "japan": "144.640",
    "china": "144.640",
    "iss": "145.825",
    "sonate2": "145.825",
}

# Statistics
aprs_packet_count = 0
aprs_station_count = 0
aprs_last_packet_time = None
aprs_stations = {}  # callsign -> station data
APRS_MAX_STATIONS = 500  # Limit tracked stations to prevent memory growth

# Meter rate limiting
_last_meter_time = 0.0
_last_meter_level = -1
METER_MIN_INTERVAL = 0.1  # Max 10 updates/sec
METER_MIN_CHANGE = 2  # Only send if level changes by at least this much


def find_direwolf() -> str | None:
    """Find direwolf binary."""
    return shutil.which("direwolf")


def find_multimon_ng() -> str | None:
    """Find multimon-ng binary."""
    return shutil.which("multimon-ng")


def find_rtl_fm() -> str | None:
    """Find rtl_fm binary."""
    return shutil.which("rtl_fm")


def find_rx_fm() -> str | None:
    """Find SoapySDR rx_fm binary."""
    return shutil.which("rx_fm")


def find_rtl_power() -> str | None:
    """Find rtl_power binary for spectrum scanning."""
    return shutil.which("rtl_power")


# Path to direwolf config file
DIREWOLF_CONFIG_PATH = os.path.join(tempfile.gettempdir(), "intercept_direwolf.conf")


def create_direwolf_config() -> str:
    """Create a minimal direwolf config for receive-only operation."""
    config = """# Minimal direwolf config for INTERCEPT (receive-only)
# Audio input is handled via stdin

ADEVICE stdin null
CHANNEL 0
MYCALL N0CALL
MODEM 1200
FIX_BITS 1
AGWPORT 0
KISSPORT 0
"""
    with open(DIREWOLF_CONFIG_PATH, "w") as f:
        f.write(config)
    return DIREWOLF_CONFIG_PATH


def normalize_aprs_output_line(line: str) -> str:
    """Normalize a decoder output line to raw APRS packet format.

    Handles common decoder prefixes:
    - multimon-ng: ``AFSK1200: ...``
    - direwolf tags: ``[0.4] ...``, ``[0L] ...``, etc.
    """
    if not line:
        return ""

    normalized = line.strip()
    if normalized.startswith("AFSK1200:"):
        normalized = normalized[9:].strip()

    # Strip one or more leading bracket tags emitted by decoders.
    # Examples: [0.4], [0L], [NONE]
    normalized = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", normalized)
    return normalized


def parse_aprs_packet(raw_packet: str) -> dict | None:
    """Parse APRS packet into structured data.

    Supports all major APRS packet types:
    - Position reports (standard, compressed, Mic-E)
    - Weather reports (standalone and in position packets)
    - Objects and Items
    - Messages (including ACK/REJ and telemetry definitions)
    - Telemetry data
    - Status reports
    - Queries and capabilities
    - Third-party traffic
    - Raw GPS/NMEA data
    - User-defined formats
    """
    try:
        raw_packet = normalize_aprs_output_line(raw_packet)
        if not raw_packet:
            return None

        # Basic APRS packet format: CALLSIGN>PATH:DATA
        # Example: N0CALL-9>APRS,TCPIP*:@092345z4903.50N/07201.75W_090/000g005t077

        # Source callsigns can include tactical suffixes like "/1" on some stations.
        match = re.match(r"^([A-Z0-9/\-]+)>([^:]+):(.+)$", raw_packet, re.IGNORECASE)
        if not match:
            return None

        callsign = match.group(1).upper()
        path = match.group(2)
        data = match.group(3)

        packet = {
            "type": "aprs",
            "callsign": callsign,
            "path": path,
            "raw": raw_packet,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # Extract destination from path (first element before any comma)
        dest_parts = path.split(",")
        dest = dest_parts[0] if dest_parts else ""

        # Check for Mic-E format first (data starts with ` or ' and dest is 6+ chars)
        if len(data) >= 9 and data[0] in "`'" and len(dest) >= 6:
            mic_e_data = parse_mic_e(dest, data)
            if mic_e_data:
                packet["packet_type"] = "position"
                packet["position_format"] = "mic_e"
                packet.update(mic_e_data)
                return packet

        # Determine packet type and parse accordingly
        if data.startswith("!") or data.startswith("="):
            # Position without timestamp (! = no messaging, = = with messaging)
            packet["packet_type"] = "position"
            packet["messaging_capable"] = data.startswith("=")
            pos_data = data[1:]

            # Check for compressed format (starts with /\[A-Za-z])
            if len(pos_data) >= 13 and pos_data[0] in "/\\":
                pos = parse_compressed_position(pos_data)
                if pos:
                    packet["position_format"] = "compressed"
                    packet.update(pos)
            else:
                pos = parse_position(pos_data)
                if pos:
                    packet["position_format"] = "uncompressed"
                    packet.update(pos)

            # Check for weather data in position packet (after position)
            if "_" in pos_data or "g" in pos_data or "t" in pos_data[15:] if len(pos_data) > 15 else False:
                weather = parse_weather(pos_data)
                if weather:
                    packet["weather"] = weather

            # Check for PHG data
            phg = parse_phg(pos_data)
            if phg:
                packet.update(phg)

            # Check for RNG data
            rng = parse_rng(pos_data)
            if rng:
                packet.update(rng)

            # Check for DF report data
            df = parse_df_report(pos_data)
            if df:
                packet.update(df)

        elif data.startswith("/") or data.startswith("@"):
            # Position with timestamp (/ = no messaging, @ = with messaging)
            packet["packet_type"] = "position"
            packet["messaging_capable"] = data.startswith("@")

            # Parse timestamp (first 7 chars after type indicator)
            if len(data) > 8:
                ts_data = parse_timestamp(data[1:8])
                if ts_data:
                    packet.update(ts_data)

                pos_data = data[8:]

                # Check for compressed format
                if len(pos_data) >= 13 and pos_data[0] in "/\\":
                    pos = parse_compressed_position(pos_data)
                    if pos:
                        packet["position_format"] = "compressed"
                        packet.update(pos)
                else:
                    pos = parse_position(pos_data)
                    if pos:
                        packet["position_format"] = "uncompressed"
                        packet.update(pos)

                # Check for weather data in position packet
                weather = parse_weather(pos_data)
                if weather:
                    packet["weather"] = weather

                # Check for PHG data
                phg = parse_phg(pos_data)
                if phg:
                    packet.update(phg)

                # Check for RNG data
                rng = parse_rng(pos_data)
                if rng:
                    packet.update(rng)

        elif data.startswith(">"):
            # Status message
            packet["packet_type"] = "status"
            status_data = data[1:]
            packet["status"] = status_data

            # Check for Maidenhead grid locator in status (common pattern)
            grid_match = re.match(r"^([A-R]{2}[0-9]{2}[A-X]{0,2})\s*", status_data, re.IGNORECASE)
            if grid_match:
                packet["grid"] = grid_match.group(1).upper()

        elif data.startswith(":"):
            # Message format - check for various subtypes
            packet["packet_type"] = "message"

            # Standard message: :ADDRESSEE:MESSAGE
            msg_match = re.match(r"^:([A-Z0-9 -]{9}):(.*)$", data, re.IGNORECASE)
            if msg_match:
                addressee = msg_match.group(1).strip()
                message = msg_match.group(2)
                packet["addressee"] = addressee

                # Check for telemetry definition messages
                telem_def_match = re.match(r"^(PARM|UNIT|EQNS|BITS)\.(.*)$", message)
                if telem_def_match:
                    packet["packet_type"] = "telemetry_definition"
                    telem_def = parse_telemetry_definition(
                        addressee, telem_def_match.group(1), telem_def_match.group(2)
                    )
                    if telem_def:
                        packet.update(telem_def)
                else:
                    packet["message"] = message

                    # Check for ACK/REJ
                    ack_match = re.match(r"^ack(\w+)$", message, re.IGNORECASE)
                    if ack_match:
                        packet["message_type"] = "ack"
                        packet["ack_id"] = ack_match.group(1)

                    rej_match = re.match(r"^rej(\w+)$", message, re.IGNORECASE)
                    if rej_match:
                        packet["message_type"] = "rej"
                        packet["rej_id"] = rej_match.group(1)

                    # Check for message ID (for acknowledgment)
                    msgid_match = re.search(r"\{(\w{1,5})$", message)
                    if msgid_match:
                        packet["message_id"] = msgid_match.group(1)
                        packet["message"] = message[: message.rfind("{")]

            # Bulletin format: :BLNn     :message
            elif data[1:4] == "BLN":
                packet["packet_type"] = "bulletin"
                bln_match = re.match(r"^:BLN([0-9A-Z])[ ]*:(.*)$", data, re.IGNORECASE)
                if bln_match:
                    packet["bulletin_id"] = bln_match.group(1)
                    packet["bulletin"] = bln_match.group(2)

            # NWS weather alert: :NWS-xxxxx:message
            elif data[1:5] == "NWS-":
                packet["packet_type"] = "nws_alert"
                nws_match = re.match(r"^:NWS-([A-Z]+)[ ]*:(.*)$", data, re.IGNORECASE)
                if nws_match:
                    packet["nws_id"] = nws_match.group(1)
                    packet["alert"] = nws_match.group(2)

        elif data.startswith("_"):
            # Weather report (Positionless)
            packet["packet_type"] = "weather"
            packet["weather"] = parse_weather(data)

        elif data.startswith(";"):
            # Object format: ;OBJECTNAME*DDHHMMzPOSITION or ;OBJECTNAME_DDHHMMzPOSITION
            packet["packet_type"] = "object"
            obj_data = parse_object(data)
            if obj_data:
                packet.update(obj_data)

                # Check for weather data in object
                remaining = data[18:] if len(data) > 18 else ""
                weather = parse_weather(remaining)
                if weather:
                    packet["weather"] = weather

        elif data.startswith(")"):
            # Item format: )ITEMNAME!POSITION or )ITEMNAME_POSITION
            packet["packet_type"] = "item"
            item_data = parse_item(data)
            if item_data:
                packet.update(item_data)

        elif data.startswith("T"):
            # Telemetry
            packet["packet_type"] = "telemetry"
            telem = parse_telemetry(data)
            if telem:
                packet.update(telem)

        elif data.startswith("}"):
            # Third-party traffic
            packet["packet_type"] = "third_party"
            third = parse_third_party(data)
            if third:
                packet.update(third)

        elif data.startswith("$"):
            # Raw GPS NMEA data
            packet["packet_type"] = "nmea"
            nmea = parse_nmea(data)
            if nmea:
                packet.update(nmea)

        elif data.startswith("{"):
            # User-defined format
            packet["packet_type"] = "user_defined"
            user = parse_user_defined(data)
            if user:
                packet.update(user)

        elif data.startswith("<"):
            # Station capabilities
            packet["packet_type"] = "capabilities"
            caps = parse_capabilities(data)
            if caps:
                packet.update(caps)

        elif data.startswith("?"):
            # Query
            packet["packet_type"] = "query"
            query = parse_capabilities(data)
            if query:
                packet.update(query)

        else:
            packet["packet_type"] = "other"
            packet["data"] = data

        # Extract comment if present (after standard data)
        # Many APRS packets have freeform comments at the end
        if "data" not in packet and packet["packet_type"] in ("position", "object", "item"):
            # Look for common comment patterns
            comment_match = re.search(r"/([^/]+)$", data)
            if comment_match and not re.match(r"^A=[-\d]+", comment_match.group(1)):
                potential_comment = comment_match.group(1)
                # Exclude things that look like data fields
                if len(potential_comment) > 3 and not re.match(r"^\d{3}/", potential_comment):
                    packet["comment"] = potential_comment

        return packet

    except Exception as e:
        logger.debug(f"Failed to parse APRS packet: {e}")
        return None


def parse_position(data: str) -> dict | None:
    """Parse APRS position data."""
    try:
        # Format: DDMM.mmN/DDDMM.mmW (or similar with symbols)
        # Example: 4903.50N/07201.75W

        pos_match = re.match(r"^(\d{2})(\d{2}\.\d+)([NS])(.)(\d{3})(\d{2}\.\d+)([EW])(.)?", data)

        if pos_match:
            lat_deg = int(pos_match.group(1))
            lat_min = float(pos_match.group(2))
            lat_dir = pos_match.group(3)
            symbol_table = pos_match.group(4)
            lon_deg = int(pos_match.group(5))
            lon_min = float(pos_match.group(6))
            lon_dir = pos_match.group(7)
            symbol_code = pos_match.group(8) or ""

            lat = lat_deg + lat_min / 60.0
            if lat_dir == "S":
                lat = -lat

            lon = lon_deg + lon_min / 60.0
            if lon_dir == "W":
                lon = -lon

            result = {
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "symbol": symbol_table + symbol_code,
            }

            # Parse additional data after position (course/speed, altitude, etc.)
            remaining = data[18:] if len(data) > 18 else ""

            # Course/Speed: CCC/SSS
            cs_match = re.search(r"(\d{3})/(\d{3})", remaining)
            if cs_match:
                result["course"] = int(cs_match.group(1))
                result["speed"] = int(cs_match.group(2))  # knots

            # Altitude: /A=NNNNNN
            alt_match = re.search(r"/A=(-?\d+)", remaining)
            if alt_match:
                result["altitude"] = int(alt_match.group(1))  # feet

            return result

        # Legacy/no-decimal variant occasionally seen in degraded decodes:
        # DDMMN/DDDMMW (symbol chars still present between/after coords).
        nodot_match = re.match(r"^(\d{2})(\d{2})([NS])(.)(\d{3})(\d{2})([EW])(.)?", data)
        if nodot_match:
            lat_deg = int(nodot_match.group(1))
            lat_min = float(nodot_match.group(2))
            lat_dir = nodot_match.group(3)
            symbol_table = nodot_match.group(4)
            lon_deg = int(nodot_match.group(5))
            lon_min = float(nodot_match.group(6))
            lon_dir = nodot_match.group(7)
            symbol_code = nodot_match.group(8) or ""

            lat = lat_deg + lat_min / 60.0
            if lat_dir == "S":
                lat = -lat

            lon = lon_deg + lon_min / 60.0
            if lon_dir == "W":
                lon = -lon

            result = {
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "symbol": symbol_table + symbol_code,
            }

            remaining = data[13:] if len(data) > 13 else ""

            cs_match = re.search(r"(\d{3})/(\d{3})", remaining)
            if cs_match:
                result["course"] = int(cs_match.group(1))
                result["speed"] = int(cs_match.group(2))

            alt_match = re.search(r"/A=(-?\d+)", remaining)
            if alt_match:
                result["altitude"] = int(alt_match.group(1))

            return result

        # Fallback: tolerate APRS ambiguity spaces in minute fields.
        # Example: 4903.  N/07201.  W
        if len(data) >= 18:
            lat_field = data[0:7]
            lat_dir = data[7]
            symbol_table = data[8] if len(data) > 8 else ""
            lon_field = data[9:17] if len(data) >= 17 else ""
            lon_dir = data[17] if len(data) > 17 else ""
            symbol_code = data[18] if len(data) > 18 else ""

            if len(lat_field) == 7 and len(lon_field) == 8 and lat_dir in ("N", "S") and lon_dir in ("E", "W"):
                lat_deg_txt = lat_field[:2]
                lat_min_txt = lat_field[2:].replace(" ", "0")
                lon_deg_txt = lon_field[:3]
                lon_min_txt = lon_field[3:].replace(" ", "0")

                if (
                    lat_deg_txt.isdigit()
                    and lon_deg_txt.isdigit()
                    and re.match(r"^\d{2}\.\d+$", lat_min_txt)
                    and re.match(r"^\d{2}\.\d+$", lon_min_txt)
                ):
                    lat_deg = int(lat_deg_txt)
                    lon_deg = int(lon_deg_txt)
                    lat_min = float(lat_min_txt)
                    lon_min = float(lon_min_txt)

                    lat = lat_deg + lat_min / 60.0
                    if lat_dir == "S":
                        lat = -lat

                    lon = lon_deg + lon_min / 60.0
                    if lon_dir == "W":
                        lon = -lon

                    result = {
                        "lat": round(lat, 6),
                        "lon": round(lon, 6),
                        "symbol": symbol_table + symbol_code,
                    }

                    # Keep same extension parsing behavior as primary branch.
                    remaining = data[19:] if len(data) > 19 else ""

                    cs_match = re.search(r"(\d{3})/(\d{3})", remaining)
                    if cs_match:
                        result["course"] = int(cs_match.group(1))
                        result["speed"] = int(cs_match.group(2))

                    alt_match = re.search(r"/A=(-?\d+)", remaining)
                    if alt_match:
                        result["altitude"] = int(alt_match.group(1))

                    return result

    except Exception as e:
        logger.debug(f"Failed to parse position: {e}")

    return None


def parse_object(data: str) -> dict | None:
    """Parse APRS object data.

    Object format: ;OBJECTNAME*DDHHMMzPOSITION or ;OBJECTNAME_DDHHMMzPOSITION
    - ; is the object marker
    - OBJECTNAME is exactly 9 characters (padded with spaces if needed)
    - * means object is live, _ means object is killed/deleted
    - DDHHMMz is the timestamp (day/hour/minute zulu) - 7 chars
    - Position follows in standard APRS format

    Some implementations have whitespace variations, so we search for the status
    character rather than assuming exact position.
    """
    try:
        if not data.startswith(";") or len(data) < 18:
            return None

        # Find the status character (* or _) which marks end of object name
        # It should be around position 10, but allow some flexibility
        status_pos = -1
        for i in range(10, min(13, len(data))):
            if data[i] in "*_":
                status_pos = i
                break

        if status_pos == -1:
            # Fallback: assume standard position
            status_pos = 10

        # Extract object name (chars between ; and status)
        obj_name = data[1:status_pos].strip()

        # Get status character
        status_char = data[status_pos] if status_pos < len(data) else "*"
        is_live = status_char == "*"

        # Timestamp is 7 chars after status, position follows
        pos_start = status_pos + 8  # status + 7 char timestamp
        if len(data) > pos_start:
            pos = parse_position(data[pos_start:])
        else:
            pos = None

        result = {
            "object_name": obj_name,
            "object_live": is_live,
        }

        if pos:
            result.update(pos)

        return result

    except Exception as e:
        logger.debug(f"Failed to parse object: {e}")
        return None


def parse_item(data: str) -> dict | None:
    """Parse APRS item data.

    Item format: )ITEMNAME!POSITION or )ITEMNAME_POSITION
    - ) is the item marker
    - ITEMNAME is 3-9 characters
    - ! means item is live, _ means item is killed/deleted
    - Position follows immediately in standard APRS format
    """
    try:
        if not data.startswith(")") or len(data) < 5:
            return None

        # Find the status delimiter (! or _) which terminates the name
        # Item name is 3-9 chars, so check positions 4-10 (1-based: chars 4-10 after ')')
        status_pos = -1
        for i in range(4, min(11, len(data))):
            if data[i] in "!_":
                status_pos = i
                break

        if status_pos == -1:
            return None

        # Extract item name and status
        item_name = data[1:status_pos].strip()
        status_char = data[status_pos]
        is_live = status_char == "!"

        # Parse position after status character
        if len(data) > status_pos + 1:
            pos = parse_position(data[status_pos + 1 :])
        else:
            pos = None

        result = {
            "item_name": item_name,
            "item_live": is_live,
        }

        if pos:
            result.update(pos)

        return result

    except Exception as e:
        logger.debug(f"Failed to parse item: {e}")
        return None


def parse_weather(data: str) -> dict:
    """Parse APRS weather data.

    Weather data can appear in positionless weather reports (starting with _)
    or as an extension after position data. Supports all standard APRS weather fields.
    """
    weather = {}

    # Wind direction: cCCC (degrees) or _CCC at start of positionless
    match = re.search(r"c(\d{3})", data)
    if match:
        weather["wind_direction"] = int(match.group(1))
    elif data.startswith("_") and len(data) > 4:
        # Positionless format starts with _MMDDhhmm then wind dir
        wind_match = re.match(r"_\d{8}(\d{3})", data)
        if wind_match:
            weather["wind_direction"] = int(wind_match.group(1))

    # Wind speed: sSSS (mph)
    match = re.search(r"s(\d{3})", data)
    if match:
        weather["wind_speed"] = int(match.group(1))

    # Wind gust: gGGG (mph)
    match = re.search(r"g(\d{3})", data)
    if match:
        weather["wind_gust"] = int(match.group(1))

    # Temperature: tTTT (Fahrenheit, can be negative)
    match = re.search(r"t(-?\d{2,3})", data)
    if match:
        weather["temperature"] = int(match.group(1))

    # Rain last hour: rRRR (hundredths of inch)
    match = re.search(r"r(\d{3})", data)
    if match:
        weather["rain_1h"] = int(match.group(1)) / 100.0

    # Rain last 24h: pPPP (hundredths of inch)
    match = re.search(r"p(\d{3})", data)
    if match:
        weather["rain_24h"] = int(match.group(1)) / 100.0

    # Rain since midnight: PPPP (hundredths of inch)
    match = re.search(r"P(\d{3})", data)
    if match:
        weather["rain_midnight"] = int(match.group(1)) / 100.0

    # Humidity: hHH (%, 00 = 100%)
    match = re.search(r"h(\d{2})", data)
    if match:
        h = int(match.group(1))
        weather["humidity"] = 100 if h == 0 else h

    # Barometric pressure: bBBBBB (tenths of millibars)
    match = re.search(r"b(\d{5})", data)
    if match:
        weather["pressure"] = int(match.group(1)) / 10.0

    # Luminosity: LLLL (watts per square meter)
    # L = 0-999 W/m², l = 1000-1999 W/m² (subtract 1000)
    match = re.search(r"L(\d{3})", data)
    if match:
        weather["luminosity"] = int(match.group(1))
    else:
        match = re.search(r"l(\d{3})", data)
        if match:
            weather["luminosity"] = int(match.group(1)) + 1000

    # Snow (last 24h): #SSS (inches)
    match = re.search(r"#(\d{3})", data)
    if match:
        weather["snow_24h"] = int(match.group(1))

    # Raw rain counter: !RRR (for Peet Bros stations)
    match = re.search(r"!(\d{3})", data)
    if match:
        weather["rain_raw"] = int(match.group(1))

    # Radiation: X### (nanosieverts/hour) - some weather stations
    match = re.search(r"X(\d{3})", data)
    if match:
        weather["radiation"] = int(match.group(1))

    # Flooding/water level: F### (feet above/below flood stage)
    match = re.search(r"F(-?\d{3})", data)
    if match:
        weather["flood_level"] = int(match.group(1))

    # Voltage: V### (volts, for battery monitoring)
    match = re.search(r"V(\d{3})", data)
    if match:
        weather["voltage"] = int(match.group(1)) / 10.0

    # Software type often at end (e.g., "Davis" or "Arduino")
    # Extract as weather station type
    wx_type_match = re.search(r"([A-Za-z]{4,})$", data)
    if wx_type_match:
        weather["wx_station_type"] = wx_type_match.group(1)

    return weather


# Mic-E encoding tables
MIC_E_DEST_TABLE = {
    "0": (0, "S", 0),
    "1": (1, "S", 0),
    "2": (2, "S", 0),
    "3": (3, "S", 0),
    "4": (4, "S", 0),
    "5": (5, "S", 0),
    "6": (6, "S", 0),
    "7": (7, "S", 0),
    "8": (8, "S", 0),
    "9": (9, "S", 0),
    "A": (0, "S", 1),
    "B": (1, "S", 1),
    "C": (2, "S", 1),
    "D": (3, "S", 1),
    "E": (4, "S", 1),
    "F": (5, "S", 1),
    "G": (6, "S", 1),
    "H": (7, "S", 1),
    "I": (8, "S", 1),
    "J": (9, "S", 1),
    "K": (0, "S", 1),
    "L": (0, "S", 0),
    "P": (0, "N", 1),
    "Q": (1, "N", 1),
    "R": (2, "N", 1),
    "S": (3, "N", 1),
    "T": (4, "N", 1),
    "U": (5, "N", 1),
    "V": (6, "N", 1),
    "W": (7, "N", 1),
    "X": (8, "N", 1),
    "Y": (9, "N", 1),
    "Z": (0, "N", 1),
}

# Mic-E message types encoded in destination
MIC_E_MESSAGE_TYPES = {
    (1, 1, 1): ("off_duty", "Off Duty"),
    (1, 1, 0): ("en_route", "En Route"),
    (1, 0, 1): ("in_service", "In Service"),
    (1, 0, 0): ("returning", "Returning"),
    (0, 1, 1): ("committed", "Committed"),
    (0, 1, 0): ("special", "Special"),
    (0, 0, 1): ("priority", "Priority"),
    (0, 0, 0): ("emergency", "Emergency"),
}


def parse_mic_e(dest: str, data: str) -> dict | None:
    """Parse Mic-E encoded position from destination and data fields.

    Mic-E is a highly compressed format that encodes:
    - Latitude in the destination address (6 chars)
    - Longitude, speed, course in the information field
    - Status message type in destination address bits

    Data field format: starts with ` or ' then:
    - byte 0: longitude degrees + 28
    - byte 1: longitude minutes + 28
    - byte 2: longitude hundredths + 28
    - byte 3: speed (tens) + course (hundreds) + 28
    - byte 4: speed (units) + course (tens) + 28
    - byte 5: course (units) + 28
    - byte 6: symbol code
    - byte 7: symbol table
    - remaining: optional altitude, telemetry, status text
    """
    try:
        if len(dest) < 6 or len(data) < 9:
            return None

        # First char indicates Mic-E type: ` = current, ' = old
        mic_e_type = "current" if data[0] == "`" else "old"

        # Parse latitude from destination (first 6 chars)
        lat_digits = []
        lat_dir = "N"
        lon_offset = 0
        msg_bits = []

        for i, char in enumerate(dest[:6]):
            if char not in MIC_E_DEST_TABLE:
                # Try uppercase
                char = char.upper()
                if char not in MIC_E_DEST_TABLE:
                    return None

            digit, ns, msg_bit = MIC_E_DEST_TABLE[char]
            lat_digits.append(digit)

            # First 3 chars determine N/S and message type
            if i < 3:
                msg_bits.append(msg_bit)
            # Char 4 determines latitude N/S
            if i == 3:
                lat_dir = ns
            # Char 5 determines longitude offset (100 degrees)
            if i == 4:
                lon_offset = 100 if ns == "N" else 0
            # Char 6 determines longitude W/E
            if i == 5:
                lon_dir = "W" if ns == "N" else "E"

        # Calculate latitude
        lat_deg = lat_digits[0] * 10 + lat_digits[1]
        lat_min = lat_digits[2] * 10 + lat_digits[3] + (lat_digits[4] * 10 + lat_digits[5]) / 100.0
        lat = lat_deg + lat_min / 60.0
        if lat_dir == "S":
            lat = -lat

        # Parse longitude from data (bytes 1-3 after type char)
        d = data[1:]  # Skip type char

        # Longitude degrees (adjusted for offset)
        lon_deg = ord(d[0]) - 28
        if lon_offset == 100:
            lon_deg += 100
        if lon_deg >= 180 and lon_deg <= 189:
            lon_deg -= 80
        elif lon_deg >= 190 and lon_deg <= 199:
            lon_deg -= 190

        # Longitude minutes
        lon_min = ord(d[1]) - 28
        if lon_min >= 60:
            lon_min -= 60

        # Longitude hundredths of minutes
        lon_hun = ord(d[2]) - 28

        lon = lon_deg + (lon_min + lon_hun / 100.0) / 60.0
        if lon_dir == "W":
            lon = -lon

        # Parse speed and course (bytes 4-6)
        sp = ord(d[3]) - 28
        dc = ord(d[4]) - 28
        se = ord(d[5]) - 28

        speed = (sp * 10) + (dc // 10)
        if speed >= 800:
            speed -= 800

        course = ((dc % 10) * 100) + se
        if course >= 400:
            course -= 400

        # Get symbol (bytes 7-8)
        symbol_code = d[6]
        symbol_table = d[7]

        result = {
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "symbol": symbol_table + symbol_code,
            "speed": speed,  # knots
            "course": course,
            "mic_e_type": mic_e_type,
        }

        # Decode message type from first 3 destination chars
        msg_tuple = tuple(msg_bits)
        if msg_tuple in MIC_E_MESSAGE_TYPES:
            result["mic_e_status"] = MIC_E_MESSAGE_TYPES[msg_tuple][0]
            result["mic_e_status_text"] = MIC_E_MESSAGE_TYPES[msg_tuple][1]

        # Parse optional fields after symbol (byte 9 onwards)
        if len(d) > 8:
            extra = d[8:]

            # Altitude: `XXX} where XXX is base-91 encoded
            alt_match = re.search(r"([\x21-\x7b]{3})\}", extra)
            if alt_match:
                alt_chars = alt_match.group(1)
                alt = (
                    (ord(alt_chars[0]) - 33) * 91 * 91
                    + (ord(alt_chars[1]) - 33) * 91
                    + (ord(alt_chars[2]) - 33)
                    - 10000
                )
                result["altitude"] = alt  # meters

            # Status text (after altitude or at end)
            status_text = re.sub(r"[\x21-\x7b]{3}\}", "", extra).strip()
            if status_text:
                result["status"] = status_text

        return result

    except Exception as e:
        logger.debug(f"Failed to parse Mic-E: {e}")
        return None


def parse_compressed_position(data: str) -> dict | None:
    r"""Parse compressed position format (Base-91 encoding).

    Compressed format: /YYYYXXXX$csT
    - / or \\ = symbol table
    - YYYY = 4-char base-91 latitude
    - XXXX = 4-char base-91 longitude
    - $ = symbol code
    - cs = compressed course/speed or altitude
    - T = compression type byte
    """
    try:
        # Compressed positions start with symbol table char followed by 4+4+1+2+1 chars
        if len(data) < 13:
            return None

        symbol_table = data[0]

        # Decode base-91 latitude (chars 1-4)
        lat_chars = data[1:5]
        lat_val = 0
        for c in lat_chars:
            lat_val = lat_val * 91 + (ord(c) - 33)
        lat = 90.0 - (lat_val / 380926.0)

        # Decode base-91 longitude (chars 5-8)
        lon_chars = data[5:9]
        lon_val = 0
        for c in lon_chars:
            lon_val = lon_val * 91 + (ord(c) - 33)
        lon = -180.0 + (lon_val / 190463.0)

        # Symbol code
        symbol_code = data[9]

        result = {
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "symbol": symbol_table + symbol_code,
            "compressed": True,
        }

        # Course/speed or altitude (chars 10-11) and type byte (char 12)
        if len(data) >= 13:
            c = ord(data[10]) - 33
            s = ord(data[11]) - 33
            t = ord(data[12]) - 33

            # Type byte bits:
            # bit 5 (0x20): GPS fix - current (1) or old (0)
            # bit 4 (0x10): NMEA source - GGA (1) or other (0)
            # bit 3 (0x08): Origin - compressed (1) or software (0)
            # bits 0-2: compression type

            comp_type = t & 0x07

            if comp_type == 0:
                # c/s are course/speed
                if c != 0 or s != 0:
                    result["course"] = c * 4
                    result["speed"] = round(1.08**s - 1, 1)  # knots
            elif comp_type == 1:
                # c/s are altitude
                if c != 0 or s != 0:
                    alt = 1.002 ** (c * 91 + s)
                    result["altitude"] = round(alt)  # feet
            elif comp_type == 2:
                # Radio range
                if s != 0:
                    result["range"] = round(2 * 1.08**s, 1)  # miles

            # GPS fix quality from type byte
            if t & 0x20:
                result["gps_fix"] = "current"
            else:
                result["gps_fix"] = "old"

        return result

    except Exception as e:
        logger.debug(f"Failed to parse compressed position: {e}")
        return None


def parse_telemetry(data: str) -> dict | None:
    """Parse APRS telemetry data.

    Format: T#sss,aaa,aaa,aaa,aaa,aaa,bbbbbbbb
    - T#sss = sequence number (001-999 or MIC)
    - aaa = analog values (0-255, up to 5 channels)
    - bbbbbbbb = 8 digital bits
    """
    try:
        if not data.startswith("T"):
            return None

        result = {"packet_type": "telemetry"}

        # Match telemetry format
        match = re.match(r"^T#(\d{3}|MIC),(\d{1,3}),(\d{1,3}),(\d{1,3}),(\d{1,3}),(\d{1,3}),([01]{8})", data)

        if match:
            result["sequence"] = match.group(1)
            result["analog"] = [
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5)),
                int(match.group(6)),
            ]
            result["digital"] = match.group(7)
            result["digital_bits"] = [int(b) for b in match.group(7)]
            return result

        # Try simpler format without digital bits
        match = re.match(r"^T#(\d{3}|MIC),(\d{1,3}),(\d{1,3}),(\d{1,3}),(\d{1,3}),(\d{1,3})", data)

        if match:
            result["sequence"] = match.group(1)
            result["analog"] = [
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5)),
                int(match.group(6)),
            ]
            return result

        # Even simpler - just sequence and some analog
        match = re.match(r"^T#(\d{3}|MIC),(.+)$", data)
        if match:
            result["sequence"] = match.group(1)
            values = match.group(2).split(",")
            result["analog"] = [int(v) for v in values if v.isdigit()]
            return result

        return None

    except Exception as e:
        logger.debug(f"Failed to parse telemetry: {e}")
        return None


def parse_telemetry_definition(callsign: str, msg_type: str, content: str) -> dict | None:
    """Parse telemetry definition messages (PARM, UNIT, EQNS, BITS).

    These messages define the meaning of telemetry values for a station.
    Format: :CALLSIGN :PARM.p1,p2,p3,p4,p5,b1,b2,b3,b4,b5,b6,b7,b8
    """
    try:
        result = {
            "telemetry_definition": True,
            "definition_type": msg_type,
            "for_station": callsign.strip(),
        }

        values = [v.strip() for v in content.split(",")]

        if msg_type == "PARM":
            # Parameter names
            result["param_names"] = values[:5]  # Analog names
            result["bit_names"] = values[5:13]  # Digital bit names

        elif msg_type == "UNIT":
            # Units for parameters
            result["param_units"] = values[:5]
            result["bit_labels"] = values[5:13]

        elif msg_type == "EQNS":
            # Equations: a*x^2 + b*x + c for each analog channel
            # Format: a1,b1,c1,a2,b2,c2,a3,b3,c3,a4,b4,c4,a5,b5,c5
            result["equations"] = []
            for i in range(0, min(15, len(values)), 3):
                if i + 2 < len(values):
                    result["equations"].append(
                        {
                            "a": float(values[i]) if values[i] else 0,
                            "b": float(values[i + 1]) if values[i + 1] else 1,
                            "c": float(values[i + 2]) if values[i + 2] else 0,
                        }
                    )

        elif msg_type == "BITS":
            # Bit sense and project name
            # Format: bbbbbbbb,Project Name
            if values:
                result["bit_sense"] = values[0][:8]
                if len(values) > 1:
                    result["project_name"] = ",".join(values[1:])

        return result

    except Exception as e:
        logger.debug(f"Failed to parse telemetry definition: {e}")
        return None


def parse_phg(data: str) -> dict | None:
    """Parse PHG (Power/Height/Gain/Directivity) data.

    Format: PHGphgd
    - p = power code (0-9)
    - h = height code (0-9)
    - g = gain code (0-9)
    - d = directivity code (0-9)
    """
    try:
        match = re.search(r"PHG(\d)(\d)(\d)(\d)", data)
        if not match:
            return None

        p, h, g, d = [int(x) for x in match.groups()]

        # Power in watts: p^2
        power_watts = p * p

        # Height in feet: 10 * 2^h
        height_feet = 10 * (2**h)

        # Gain in dB
        gain_db = g

        # Directivity (0=omni, 1-8 = 45° sectors starting from N)
        directions = ["omni", "NE", "E", "SE", "S", "SW", "W", "NW", "N"]
        directivity = directions[d] if d < len(directions) else "omni"

        return {
            "phg": True,
            "power_watts": power_watts,
            "height_feet": height_feet,
            "gain_db": gain_db,
            "directivity": directivity,
            "directivity_code": d,
        }

    except Exception as e:
        logger.debug(f"Failed to parse PHG: {e}")
        return None


def parse_rng(data: str) -> dict | None:
    """Parse RNG (radio range) data.

    Format: RNGrrrr where rrrr is range in miles.
    """
    try:
        match = re.search(r"RNG(\d{4})", data)
        if match:
            return {"range_miles": int(match.group(1))}
        return None
    except Exception:
        return None


def parse_df_report(data: str) -> dict | None:
    """Parse Direction Finding (DF) report.

    Format: CSE/SPD/BRG/NRQ or similar patterns.
    - BRG = bearing to signal
    - NRQ = Number/Range/Quality
    """
    try:
        result = {}

        # DF bearing format: /BRG (3 digits)
        brg_match = re.search(r"/(\d{3})/", data)
        if brg_match:
            result["df_bearing"] = int(brg_match.group(1))

        # NRQ format
        nrq_match = re.search(r"/(\d)(\d)(\d)$", data)
        if nrq_match:
            n, r, q = [int(x) for x in nrq_match.groups()]
            result["df_hits"] = n  # Number of signal hits
            result["df_range"] = r  # Range: 0=useless, 8=exact
            result["df_quality"] = q  # Quality: 0=useless, 8=excellent

        return result if result else None

    except Exception:
        return None


def parse_timestamp(data: str) -> dict | None:
    """Parse APRS timestamp from position data.

    Formats:
    - DDHHMMz = day/hour/minute zulu
    - HHMMSSh = hour/minute/second local
    - DDHHMMl = day/hour/minute local (with /or not followed by position)
    """
    try:
        result = {}

        # Zulu time: DDHHMMz
        match = re.match(r"^(\d{2})(\d{2})(\d{2})z", data)
        if match:
            result["time_day"] = int(match.group(1))
            result["time_hour"] = int(match.group(2))
            result["time_minute"] = int(match.group(3))
            result["time_format"] = "zulu"
            return result

        # Local time: HHMMSSh
        match = re.match(r"^(\d{2})(\d{2})(\d{2})h", data)
        if match:
            result["time_hour"] = int(match.group(1))
            result["time_minute"] = int(match.group(2))
            result["time_second"] = int(match.group(3))
            result["time_format"] = "local"
            return result

        # Local with day: DDHHMMl (less common)
        match = re.match(r"^(\d{2})(\d{2})(\d{2})/", data)
        if match:
            result["time_day"] = int(match.group(1))
            result["time_hour"] = int(match.group(2))
            result["time_minute"] = int(match.group(3))
            result["time_format"] = "local_day"
            return result

        return None

    except Exception:
        return None


def parse_third_party(data: str) -> dict | None:
    """Parse third-party traffic (packets relayed from another network).

    Format: }CALL>PATH:DATA (the } indicates third-party)
    """
    try:
        if not data.startswith("}"):
            return None

        # The rest is a standard APRS packet
        inner_packet = data[1:]

        # Parse the inner packet
        inner = parse_aprs_packet(inner_packet)
        if inner:
            return {
                "third_party": True,
                "inner_packet": inner,
            }

        return {"third_party": True, "inner_raw": inner_packet}

    except Exception:
        return None


def parse_user_defined(data: str) -> dict | None:
    """Parse user-defined data format.

    Format: {UUXXXX...
    - { = user-defined marker
    - UU = 2-char user ID (experimental use)
    - XXXX = user-defined data
    """
    try:
        if not data.startswith("{") or len(data) < 3:
            return None

        return {
            "user_defined": True,
            "user_id": data[1:3],
            "user_data": data[3:],
        }

    except Exception:
        return None


def parse_capabilities(data: str) -> dict | None:
    """Parse station capabilities response.

    Format: <capability1,capability2,...
    or query format: ?APRS? or ?WX? etc.
    """
    try:
        if data.startswith("<"):
            # Capabilities response
            caps = data[1:].split(",")
            return {
                "capabilities": [c.strip() for c in caps if c.strip()],
            }

        elif data.startswith("?"):
            # Query
            query_match = re.match(r"\?([A-Z]+)\?", data)
            if query_match:
                return {
                    "query": True,
                    "query_type": query_match.group(1),
                }

        return None

    except Exception:
        return None


def parse_nmea(data: str) -> dict | None:
    """Parse raw GPS NMEA sentences.

    APRS can include raw NMEA data starting with $.
    """
    try:
        if not data.startswith("$"):
            return None

        result = {
            "nmea": True,
            "nmea_sentence": data,
        }

        # Try to identify sentence type
        if data.startswith("$GPGGA") or data.startswith("$GNGGA"):
            result["nmea_type"] = "GGA"
        elif data.startswith("$GPRMC") or data.startswith("$GNRMC"):
            result["nmea_type"] = "RMC"
        elif data.startswith("$GPGLL") or data.startswith("$GNGLL"):
            result["nmea_type"] = "GLL"

        return result

    except Exception:
        return None


def parse_audio_level(line: str) -> int | None:
    """Parse direwolf audio level line and return normalized level (0-100).

    Direwolf outputs lines like:
        Audio level = 34(18/16)   [NONE]   __||||||______
        [0.4] Audio level = 57(34/32)   [NONE]   __||||||||||||______

    The first number after "Audio level = " is the main level indicator.
    We normalize it to 0-100 scale (direwolf typically outputs 0-100+).
    """
    # Match "Audio level = NN" pattern
    match = re.search(r"Audio level\s*=\s*(\d+)", line, re.IGNORECASE)
    if match:
        raw_level = int(match.group(1))
        # Normalize: direwolf levels are typically 0-100, but can go higher
        # Clamp to 0-100 range
        normalized = min(max(raw_level, 0), 100)
        return normalized
    return None


def should_send_meter_update(level: int) -> bool:
    """Rate-limit meter updates to avoid spamming SSE.

    Only send if:
    - At least METER_MIN_INTERVAL seconds have passed, OR
    - Level changed by at least METER_MIN_CHANGE
    """
    global _last_meter_time, _last_meter_level

    now = time.time()
    time_ok = (now - _last_meter_time) >= METER_MIN_INTERVAL
    change_ok = abs(level - _last_meter_level) >= METER_MIN_CHANGE

    if time_ok or change_ok:
        _last_meter_time = now
        _last_meter_level = level
        return True
    return False


def stream_aprs_output(master_fd: int, rtl_process: subprocess.Popen, decoder_process: subprocess.Popen) -> None:
    """Stream decoded APRS packets and audio level meter to queue.

    Reads from a PTY master fd to get line-buffered output from the decoder,
    avoiding the 15-minute pipe buffering delay. Uses select() + os.read()
    to poll the PTY (same pattern as pager.py).

    Outputs two types of messages to the queue:
    - type='aprs': Decoded APRS packets
    - type='meter': Audio level meter readings (rate-limited)
    """
    global aprs_packet_count, aprs_station_count, aprs_last_packet_time, aprs_stations
    global _last_meter_time, _last_meter_level, aprs_active_device, aprs_active_sdr_type

    # Capture the device claimed by THIS session so the finally block only
    # releases our own device, not one claimed by a subsequent start.
    my_device = aprs_active_device

    # Reset meter state
    _last_meter_time = 0.0
    _last_meter_level = -1

    try:
        app_module.aprs_queue.put({"type": "status", "status": "started"})

        # Read from PTY using select() for non-blocking reads.
        # PTY forces the decoder to line-buffer, so output arrives immediately
        # instead of waiting for a full 4-8KB pipe buffer to fill.
        buffer = ""
        while True:
            try:
                ready, _, _ = select.select([master_fd], [], [], 1.0)
            except Exception:
                break

            if ready:
                try:
                    data = os.read(master_fd, 1024)
                    if not data:
                        break
                    buffer += data.decode("utf-8", errors="replace")
                except OSError:
                    break

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    # Check for audio level line first (for signal meter)
                    audio_level = parse_audio_level(line)
                    if audio_level is not None:
                        if should_send_meter_update(audio_level):
                            meter_msg = {
                                "type": "meter",
                                "level": audio_level,
                                "ts": datetime.utcnow().isoformat() + "Z",
                            }
                            app_module.aprs_queue.put(meter_msg)
                        continue  # Audio level lines are not packets

                    # Normalize decoder prefixes (multimon/direwolf) before parsing.
                    line = normalize_aprs_output_line(line)

                    # Skip non-packet lines (APRS format: CALL>PATH:DATA)
                    if ">" not in line or ":" not in line:
                        continue

                    packet = parse_aprs_packet(line)
                    if packet:
                        aprs_packet_count += 1
                        aprs_last_packet_time = time.time()

                        # Track unique stations
                        callsign = packet.get("callsign")
                        if callsign and callsign not in aprs_stations:
                            aprs_station_count += 1

                        # Update station data, preserving last known coordinates when
                        # packets do not contain position fields.
                        if callsign:
                            existing = aprs_stations.get(callsign, {})
                            packet_lat = packet.get("lat")
                            packet_lon = packet.get("lon")
                            aprs_stations[callsign] = {
                                "callsign": callsign,
                                "lat": packet_lat if packet_lat is not None else existing.get("lat"),
                                "lon": packet_lon if packet_lon is not None else existing.get("lon"),
                                "symbol": packet.get("symbol") or existing.get("symbol"),
                                "last_seen": packet.get("timestamp"),
                                "packet_type": packet.get("packet_type"),
                            }
                            # Geofence check
                            _aprs_lat = packet_lat
                            _aprs_lon = packet_lon
                            if _aprs_lat is not None and _aprs_lon is not None:
                                try:
                                    from utils.geofence import get_geofence_manager

                                    for _gf_evt in get_geofence_manager().check_position(
                                        callsign, "aprs_station", _aprs_lat, _aprs_lon, {"callsign": callsign}
                                    ):
                                        process_event("aprs", _gf_evt, "geofence")
                                except Exception:
                                    pass
                            # Evict oldest stations when limit is exceeded
                            if len(aprs_stations) > APRS_MAX_STATIONS:
                                oldest = min(
                                    aprs_stations,
                                    key=lambda k: aprs_stations[k].get("last_seen", ""),
                                )
                                del aprs_stations[oldest]

                        app_module.aprs_queue.put(packet)

                        # Log if enabled
                        if app_module.logging_enabled:
                            try:
                                with open(app_module.log_file_path, "a") as f:
                                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    f.write(f"{ts} | APRS | {json.dumps(packet)}\n")
                            except Exception:
                                pass

    except Exception as e:
        logger.error(f"APRS stream error: {e}")
        app_module.aprs_queue.put({"type": "error", "message": str(e)})
    finally:
        with contextlib.suppress(OSError):
            os.close(master_fd)
        app_module.aprs_queue.put({"type": "status", "status": "stopped"})
        # Cleanup processes
        for proc in [rtl_process, decoder_process]:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                with contextlib.suppress(Exception):
                    proc.kill()
        # Release SDR device — only if it's still ours (not reclaimed by a new start)
        if my_device is not None and aprs_active_device == my_device:
            app_module.release_sdr_device(my_device, aprs_active_sdr_type or "rtlsdr")
            aprs_active_device = None
            aprs_active_sdr_type = None


@aprs_bp.route("/tools")
def check_aprs_tools() -> Response:
    """Check for APRS decoding tools."""
    has_rtl_fm = find_rtl_fm() is not None
    has_rx_fm = find_rx_fm() is not None
    has_direwolf = find_direwolf() is not None
    has_multimon = find_multimon_ng() is not None
    has_fm_demod = has_rtl_fm or has_rx_fm

    return jsonify(
        {
            "rtl_fm": has_rtl_fm,
            "rx_fm": has_rx_fm,
            "direwolf": has_direwolf,
            "multimon_ng": has_multimon,
            "ready": has_fm_demod and (has_direwolf or has_multimon),
            "decoder": "direwolf" if has_direwolf else ("multimon-ng" if has_multimon else None),
        }
    )


@aprs_bp.route("/status")
def aprs_status() -> Response:
    """Get APRS decoder status."""
    running = False
    if app_module.aprs_process:
        running = app_module.aprs_process.poll() is None

    return jsonify(
        {
            "running": running,
            "packet_count": aprs_packet_count,
            "station_count": aprs_station_count,
            "last_packet_time": aprs_last_packet_time,
            "queue_size": app_module.aprs_queue.qsize(),
        }
    )


@aprs_bp.route("/stations")
def get_stations() -> Response:
    """Get all tracked APRS stations."""
    return jsonify({"stations": list(aprs_stations.values()), "count": len(aprs_stations)})


@aprs_bp.route("/export")
def export_data() -> Response:
    """Export tracked APRS stations as JSON or CSV.

    Query params:
        format: 'json' or 'csv' (default: json)
    """
    export_format = request.args.get("format", "json").lower()
    stations = list(aprs_stations.values())
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if export_format == "csv":
        import io

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Callsign", "Latitude", "Longitude", "Symbol", "Packet Type", "Last Seen"])
        for s in stations:
            writer.writerow(
                [
                    s.get("callsign", ""),
                    s.get("lat", ""),
                    s.get("lon", ""),
                    s.get("symbol", ""),
                    s.get("packet_type", ""),
                    s.get("last_seen", ""),
                ]
            )
        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = f"attachment; filename=aprs_stations_{timestamp}.csv"
        return response

    data = {
        "stations": stations,
        "count": len(stations),
        "exported_at": datetime.now().isoformat(),
    }
    response = Response(json.dumps(data, indent=2), mimetype="application/json")
    response.headers["Content-Disposition"] = f"attachment; filename=aprs_stations_{timestamp}.json"
    return response


@aprs_bp.route("/data")
def aprs_data() -> Response:
    """Get APRS data snapshot for remote controller polling compatibility."""
    running = False
    if app_module.aprs_process:
        running = app_module.aprs_process.poll() is None

    return api_success(
        data={
            "running": running,
            "stations": list(aprs_stations.values()),
            "count": len(aprs_stations),
            "packet_count": aprs_packet_count,
            "station_count": aprs_station_count,
            "last_packet_time": aprs_last_packet_time,
        }
    )


@aprs_bp.route("/start", methods=["POST"])
def start_aprs() -> Response:
    """Start APRS decoder."""
    global aprs_packet_count, aprs_station_count, aprs_last_packet_time, aprs_stations
    global aprs_active_device, aprs_active_sdr_type

    with app_module.aprs_lock:
        if app_module.aprs_process and app_module.aprs_process.poll() is None:
            return api_error("APRS decoder already running", 409)

    # Check for decoder (prefer direwolf, fallback to multimon-ng)
    direwolf_path = find_direwolf()
    multimon_path = find_multimon_ng()

    if not direwolf_path and not multimon_path:
        return api_error("No APRS decoder found. Install direwolf or multimon-ng", 400)

    data = request.json or {}
    data = apply_device_defaults(data)

    # Validate inputs
    try:
        device = validate_device_index(data.get("device", "0"))
        gain = validate_gain(data.get("gain", "40"))
        ppm = validate_ppm(data.get("ppm", "0"))
    except ValueError as e:
        return api_error(str(e), 400)

    # Check for rtl_tcp (remote SDR) connection
    rtl_tcp_host = data.get("rtl_tcp_host")
    rtl_tcp_port = data.get("rtl_tcp_port", 1234)

    sdr_type_str = str(data.get("sdr_type", "rtlsdr")).lower()
    try:
        sdr_type = SDRType(sdr_type_str)
    except ValueError:
        sdr_type = SDRType.RTL_SDR

    if sdr_type == SDRType.RTL_SDR:
        if find_rtl_fm() is None:
            return api_error(f"rtl_fm not found. {install_hint('rtl_fm')}", 400)
    else:
        if find_rx_fm() is None:
            return api_error(f"rx_fm not found. Install SoapySDR tools for {sdr_type.value}.", 400)

    # Reserve SDR device to prevent conflicts (skip for remote rtl_tcp)
    if not rtl_tcp_host:
        error = app_module.claim_sdr_device(device, "aprs", sdr_type_str)
        if error:
            return api_error(error, 409, error_type="DEVICE_BUSY")
        aprs_active_device = device
        aprs_active_sdr_type = sdr_type_str

    # Get frequency for region
    region = data.get("region", "north_america")
    frequency = APRS_FREQUENCIES.get(region, "144.390")

    # Allow custom frequency override
    if data.get("frequency"):
        frequency = data.get("frequency")

    # Clear queue and reset stats
    while not app_module.aprs_queue.empty():
        try:
            app_module.aprs_queue.get_nowait()
        except queue.Empty:
            break

    aprs_packet_count = 0
    aprs_station_count = 0
    aprs_last_packet_time = None
    aprs_stations = {}

    # Build FM demod command for APRS (AFSK1200 @ 22050 Hz) via SDR abstraction.
    try:
        if rtl_tcp_host:
            try:
                rtl_tcp_host = validate_rtl_tcp_host(rtl_tcp_host)
                rtl_tcp_port = validate_rtl_tcp_port(rtl_tcp_port)
            except ValueError as e:
                return api_error(str(e), 400)
            sdr_device = SDRFactory.create_network_device(rtl_tcp_host, rtl_tcp_port)
            logger.info(f"Using remote SDR: rtl_tcp://{rtl_tcp_host}:{rtl_tcp_port}")
        else:
            sdr_device = SDRFactory.create_default_device(sdr_type, index=device)
        builder = SDRFactory.get_builder(sdr_device.sdr_type)
        rtl_cmd = builder.build_fm_demod_command(
            device=sdr_device,
            frequency_mhz=float(frequency),
            sample_rate=22050,
            gain=float(gain) if gain and str(gain) != "0" else None,
            ppm=int(ppm) if ppm and str(ppm) != "0" else None,
            modulation="nfm" if sdr_type == SDRType.RTL_SDR else "fm",
            squelch=None,
            bias_t=bool(data.get("bias_t", False)),
        )

        if sdr_type == SDRType.RTL_SDR and rtl_cmd and rtl_cmd[-1] == "-":
            # APRS benefits from DC blocking + fast AGC on rtl_fm.
            rtl_cmd = rtl_cmd[:-1] + ["-E", "dc", "-A", "fast", "-"]
    except Exception as e:
        if aprs_active_device is not None:
            app_module.release_sdr_device(aprs_active_device, aprs_active_sdr_type or "rtlsdr")
            aprs_active_device = None
            aprs_active_sdr_type = None
        return api_error(f"Failed to build SDR command: {e}", 500)

    # Build decoder command
    if direwolf_path:
        # Create minimal config file for direwolf
        config_path = create_direwolf_config()

        # direwolf flags for receiving AFSK1200 from stdin:
        # -c config = config file path (must come before other options)
        # -n 1 = mono audio channel
        # -r 22050 = sample rate (must match rtl_fm -s)
        # -b 16 = 16-bit signed samples
        # -t 0 = disable text colors (for cleaner parsing)
        # NOTE: We do NOT use -q h here so we get audio level lines for the signal meter
        # - = read audio from stdin (must be last argument)
        decoder_cmd = [direwolf_path, "-c", config_path, "-n", "1", "-r", "22050", "-b", "16", "-t", "0", "-"]
        decoder_name = "direwolf"
    else:
        # Fallback to multimon-ng
        decoder_cmd = [multimon_path, "-t", "raw", "-a", "AFSK1200", "-"]
        decoder_name = "multimon-ng"

    logger.info(f"Starting APRS decoder: {' '.join(rtl_cmd)} | {' '.join(decoder_cmd)}")

    try:
        # Start rtl_fm with stdout piped to decoder.
        # stderr is captured via PIPE so errors are reported to the user.
        # NOTE: RTL-SDR Blog V4 may show offset-tuned frequency in logs - this is normal.
        rtl_process = subprocess.Popen(rtl_cmd, stdout=PIPE, stderr=PIPE, start_new_session=True)

        # Start a thread to monitor rtl_fm stderr for errors
        def monitor_rtl_stderr():
            for line in rtl_process.stderr:
                err_text = line.decode("utf-8", errors="replace").strip()
                if err_text:
                    logger.debug(f"[RTL_FM] {err_text}")

        rtl_stderr_thread = threading.Thread(target=monitor_rtl_stderr, daemon=True)
        rtl_stderr_thread.start()

        # Create a pseudo-terminal for decoder output.  PTY forces the
        # decoder to line-buffer its stdout, avoiding the 15-minute delay
        # caused by full pipe buffering (~4-8KB) on small APRS packets.
        master_fd, slave_fd = pty.openpty()

        # Start decoder with stdin wired to rtl_fm's stdout.
        # stdout/stderr go to the PTY slave so output is line-buffered.
        decoder_process = subprocess.Popen(
            decoder_cmd,
            stdin=rtl_process.stdout,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
        )

        # Close slave fd in parent — decoder owns it now.
        os.close(slave_fd)

        # Close rtl_fm's stdout in parent so decoder owns it exclusively.
        # This ensures proper EOF propagation when rtl_fm terminates.
        rtl_process.stdout.close()

        # Wait briefly to check if processes started successfully
        time.sleep(PROCESS_START_WAIT)

        if rtl_process.poll() is not None:
            # rtl_fm exited early - capture stderr for diagnostics
            stderr_output = ""
            try:
                remaining = rtl_process.stderr.read()
                if remaining:
                    stderr_output = remaining.decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            if stderr_output:
                logger.error(f"rtl_fm stderr:\n{stderr_output}")
            error_msg = f"rtl_fm failed to start (exit code {rtl_process.returncode})"
            if stderr_output:
                error_msg += f": {stderr_output[:500]}"
            logger.error(error_msg)
            with contextlib.suppress(OSError):
                os.close(master_fd)
            with contextlib.suppress(Exception):
                decoder_process.kill()
            if aprs_active_device is not None:
                app_module.release_sdr_device(aprs_active_device, aprs_active_sdr_type or "rtlsdr")
                aprs_active_device = None
                aprs_active_sdr_type = None
            return api_error(error_msg, 500)

        if decoder_process.poll() is not None:
            # Decoder exited early - capture any output from PTY
            error_output = ""
            try:
                ready, _, _ = select.select([master_fd], [], [], 0.5)
                if ready:
                    raw = os.read(master_fd, 500)
                    error_output = raw.decode("utf-8", errors="replace")
            except Exception:
                pass
            error_msg = f"{decoder_name} failed to start"
            if error_output:
                error_msg += f": {error_output}"
            logger.error(error_msg)
            with contextlib.suppress(OSError):
                os.close(master_fd)
            with contextlib.suppress(Exception):
                rtl_process.kill()
            if aprs_active_device is not None:
                app_module.release_sdr_device(aprs_active_device, aprs_active_sdr_type or "rtlsdr")
                aprs_active_device = None
                aprs_active_sdr_type = None
            return api_error(error_msg, 500)

        # Store references for status checks and cleanup
        app_module.aprs_process = decoder_process
        app_module.aprs_rtl_process = rtl_process
        app_module.aprs_master_fd = master_fd

        # Start background thread to read decoder output and push to queue
        thread = threading.Thread(
            target=stream_aprs_output, args=(master_fd, rtl_process, decoder_process), daemon=True
        )
        thread.start()

        return jsonify(
            {
                "status": "started",
                "frequency": frequency,
                "region": region,
                "device": device,
                "sdr_type": sdr_type.value,
                "decoder": decoder_name,
            }
        )

    except Exception as e:
        logger.error(f"Failed to start APRS decoder: {e}")
        if aprs_active_device is not None:
            app_module.release_sdr_device(aprs_active_device, aprs_active_sdr_type or "rtlsdr")
            aprs_active_device = None
            aprs_active_sdr_type = None
        return api_error(str(e), 500)


@aprs_bp.route("/stop", methods=["POST"])
def stop_aprs() -> Response:
    """Stop APRS decoder.

    Releases the SDR device immediately so the status panel updates
    without waiting for process termination. Process cleanup runs in a
    background thread to avoid blocking the HTTP response (which caused
    frontend timeout errors when two processes each took up to 2s to die).
    """
    global aprs_active_device, aprs_active_sdr_type

    with app_module.aprs_lock:
        processes_to_stop = []

        if hasattr(app_module, "aprs_rtl_process") and app_module.aprs_rtl_process:
            processes_to_stop.append(app_module.aprs_rtl_process)

        if app_module.aprs_process:
            processes_to_stop.append(app_module.aprs_process)

        if not processes_to_stop:
            return jsonify({"status": "not_running"})

        # Release SDR device immediately so status panel reflects the
        # change without waiting for process termination.
        if aprs_active_device is not None:
            app_module.release_sdr_device(aprs_active_device, aprs_active_sdr_type or "rtlsdr")
            aprs_active_device = None
            aprs_active_sdr_type = None

        # Capture refs to clear before releasing the lock
        master_fd = getattr(app_module, "aprs_master_fd", None)
        app_module.aprs_process = None
        if hasattr(app_module, "aprs_rtl_process"):
            app_module.aprs_rtl_process = None
        app_module.aprs_master_fd = None

    # Terminate processes in background so the response returns fast.
    # Each proc.wait() can block up to PROCESS_TERMINATE_TIMEOUT (2s),
    # which previously caused the frontend 2200ms fetch to abort.
    def _cleanup():
        # Close PTY master fd first — this unblocks the stream thread
        if master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(master_fd)
        for proc in processes_to_stop:
            try:
                proc.terminate()
                proc.wait(timeout=PROCESS_TERMINATE_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
            except Exception as e:
                logger.error(f"Error stopping APRS process: {e}")

    threading.Thread(target=_cleanup, daemon=True).start()

    return jsonify({"status": "stopped"})


@aprs_bp.route("/stream")
def stream_aprs() -> Response:
    """SSE stream for APRS packets."""

    def _on_msg(msg: dict[str, Any]) -> None:
        process_event("aprs", msg, msg.get("type"))

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.aprs_queue,
            channel_key="aprs",
            timeout=SSE_QUEUE_TIMEOUT,
            keepalive_interval=SSE_KEEPALIVE_INTERVAL,
            on_message=_on_msg,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@aprs_bp.route("/frequencies")
def get_frequencies() -> Response:
    """Get APRS frequencies by region."""
    return jsonify(APRS_FREQUENCIES)


@aprs_bp.route("/spectrum", methods=["GET", "POST"])
def scan_aprs_spectrum() -> Response:
    """Scan spectrum around APRS frequency for signal visibility debugging.

    This endpoint runs rtl_power briefly to detect signal activity near the
    APRS frequency. Useful for headless/remote debugging to verify antenna
    and SDR are receiving signals.

    Query params or JSON body:
        device: SDR device index (default: 0)
        gain: Gain in dB, 0=auto (default: 0)
        region: Region for frequency lookup (default: europe)
        frequency: Override frequency in MHz (optional)
        duration: Scan duration in seconds (default: 10, max: 60)

    Returns JSON with peak detection and signal analysis.
    """
    rtl_power_path = find_rtl_power()
    if not rtl_power_path:
        return api_error(f"rtl_power not found. {install_hint('rtl_power')}", 400)

    # Get parameters from JSON body or query args
    if request.is_json:
        data = request.json or {}
    else:
        data = {}

    device = data.get("device", request.args.get("device", "0"))
    gain = data.get("gain", request.args.get("gain", "0"))
    region = data.get("region", request.args.get("region", "europe"))
    frequency = data.get("frequency", request.args.get("frequency"))
    duration = data.get("duration", request.args.get("duration", "10"))

    # Validate inputs
    try:
        device = validate_device_index(device)
        gain = validate_gain(gain)
        duration = min(max(int(duration), 5), 60)  # Clamp 5-60 seconds
    except ValueError as e:
        return api_error(str(e), 400)

    # Get center frequency
    if frequency:
        center_freq_mhz = float(frequency)
    else:
        center_freq_mhz = float(APRS_FREQUENCIES.get(region, "144.800"))

    # Scan 20 kHz around center frequency (±10 kHz)
    start_freq_mhz = center_freq_mhz - 0.010
    end_freq_mhz = center_freq_mhz + 0.010
    bin_size_hz = 200  # 200 Hz bins for good resolution

    # Create temp file for rtl_power output
    tmp_file = os.path.join(tempfile.gettempdir(), f"intercept_rtl_power_{os.getpid()}.csv")

    try:
        # Build rtl_power command
        # Format: rtl_power -f start:end:bin_size -d device -g gain -i interval -e duration output_file
        rtl_power_cmd = [
            rtl_power_path,
            "-f",
            f"{start_freq_mhz}M:{end_freq_mhz}M:{bin_size_hz}",
            "-d",
            str(device),
            "-i",
            "1",  # 1 second integration
            "-e",
            f"{duration}s",
        ]

        # Gain: 0 means auto
        if gain and str(gain) != "0":
            rtl_power_cmd.extend(["-g", str(gain)])

        rtl_power_cmd.append(tmp_file)

        logger.info(f"Running spectrum scan: {' '.join(rtl_power_cmd)}")

        # Run rtl_power with timeout
        result = subprocess.run(
            rtl_power_cmd,
            capture_output=True,
            text=True,
            timeout=duration + 15,  # Allow extra time for startup/shutdown
        )

        if result.returncode != 0:
            error_msg = result.stderr[:200] if result.stderr else f"Exit code {result.returncode}"
            return api_error(f"rtl_power failed: {error_msg}", 500)

        # Parse rtl_power CSV output
        # Format: date, time, start_hz, end_hz, step_hz, samples, db1, db2, db3, ...
        if not os.path.exists(tmp_file):
            return api_error("rtl_power did not produce output file", 500)

        bins = []
        with open(tmp_file) as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 7:
                    continue
                try:
                    row_start_hz = float(row[2])
                    row_step_hz = float(row[4])
                    # dB values start at column 6
                    for i, db_str in enumerate(row[6:]):
                        db_val = float(db_str.strip())
                        freq_hz = row_start_hz + (i * row_step_hz)
                        bins.append({"freq_hz": freq_hz, "db": db_val})
                except (ValueError, IndexError):
                    continue

        if not bins:
            return api_error("No spectrum data collected. Check SDR connection and antenna.", 500)

        # Calculate statistics
        db_values = [b["db"] for b in bins]
        avg_db = sum(db_values) / len(db_values)
        max_bin = max(bins, key=lambda x: x["db"])
        min_db = min(db_values)

        # Find peak near center frequency (within 5 kHz)
        center_hz = center_freq_mhz * 1e6
        near_center_bins = [b for b in bins if abs(b["freq_hz"] - center_hz) < 5000]
        if near_center_bins:
            peak_near_center = max(near_center_bins, key=lambda x: x["db"])
        else:
            peak_near_center = max_bin

        # Signal analysis
        peak_above_noise = peak_near_center["db"] - avg_db
        signal_detected = peak_above_noise > 3  # 3 dB above noise floor

        # Generate advice
        if peak_above_noise < 1:
            advice = "No signal detected near APRS frequency. Check antenna connection and orientation."
        elif peak_above_noise < 3:
            advice = "Weak signal detected. Consider improving antenna or reducing noise sources."
        elif peak_above_noise < 6:
            advice = "Moderate signal detected. Decoding should work for strong stations."
        else:
            advice = "Good signal detected. Decoding should work well."

        return api_success(
            data={
                "scan_params": {
                    "center_freq_mhz": center_freq_mhz,
                    "start_freq_mhz": start_freq_mhz,
                    "end_freq_mhz": end_freq_mhz,
                    "bin_size_hz": bin_size_hz,
                    "duration_seconds": duration,
                    "device": device,
                    "gain": gain,
                    "region": region,
                },
                "results": {
                    "total_bins": len(bins),
                    "noise_floor_db": round(avg_db, 1),
                    "min_db": round(min_db, 1),
                    "peak_freq_mhz": round(max_bin["freq_hz"] / 1e6, 6),
                    "peak_db": round(max_bin["db"], 1),
                    "peak_near_aprs_freq_mhz": round(peak_near_center["freq_hz"] / 1e6, 6),
                    "peak_near_aprs_db": round(peak_near_center["db"], 1),
                    "signal_above_noise_db": round(peak_above_noise, 1),
                    "signal_detected": signal_detected,
                },
                "advice": advice,
            }
        )

    except subprocess.TimeoutExpired:
        return api_error(f"Spectrum scan timed out after {duration + 15} seconds", 500)
    except Exception as e:
        logger.error(f"Spectrum scan error: {e}")
        return api_error(str(e), 500)
    finally:
        # Cleanup temp file
        try:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        except Exception:
            pass
