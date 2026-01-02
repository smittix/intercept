"""
GPS dongle support for INTERCEPT.

Provides detection and reading of USB GPS dongles via serial port.
Parses NMEA sentences to extract location data.
"""

from __future__ import annotations

import logging
import os
import re
import glob
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable

logger = logging.getLogger('intercept.gps')

# Try to import serial, but don't fail if not available
try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    logger.warning("pyserial not installed - GPS dongle support disabled")


@dataclass
class GPSPosition:
    """GPS position data."""
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    speed: Optional[float] = None  # knots
    heading: Optional[float] = None  # degrees
    satellites: Optional[int] = None
    fix_quality: int = 0  # 0=invalid, 1=GPS, 2=DGPS
    timestamp: Optional[datetime] = None
    device: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'latitude': self.latitude,
            'longitude': self.longitude,
            'altitude': self.altitude,
            'speed': self.speed,
            'heading': self.heading,
            'satellites': self.satellites,
            'fix_quality': self.fix_quality,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'device': self.device,
        }


def detect_gps_devices() -> list[dict]:
    """
    Detect potential GPS serial devices.

    Returns a list of device info dictionaries.
    """
    devices = []

    # Common GPS device patterns by platform
    patterns = []

    if os.name == 'posix':
        # Linux
        patterns.extend([
            '/dev/ttyUSB*',      # USB serial adapters
            '/dev/ttyACM*',      # USB CDC ACM devices (many GPS)
            '/dev/gps*',         # gpsd symlinks
        ])
        # macOS
        patterns.extend([
            '/dev/tty.usbserial*',
            '/dev/tty.usbmodem*',
            '/dev/cu.usbserial*',
            '/dev/cu.usbmodem*',
        ])

    for pattern in patterns:
        for path in glob.glob(pattern):
            # Try to get device info
            device_info = {
                'path': path,
                'name': os.path.basename(path),
                'type': 'serial',
            }

            # Check if it's readable
            if os.access(path, os.R_OK):
                device_info['accessible'] = True
            else:
                device_info['accessible'] = False
                device_info['error'] = 'Permission denied'

            devices.append(device_info)

    return devices


def parse_nmea_coordinate(coord: str, direction: str) -> Optional[float]:
    """
    Parse NMEA coordinate format to decimal degrees.

    NMEA format: DDDMM.MMMM or DDMM.MMMM
    """
    if not coord or not direction:
        return None

    try:
        # Find the decimal point
        dot_pos = coord.index('.')

        # Degrees are everything before the last 2 digits before decimal
        degrees = int(coord[:dot_pos - 2])
        minutes = float(coord[dot_pos - 2:])

        result = degrees + (minutes / 60.0)

        # Apply direction
        if direction in ('S', 'W'):
            result = -result

        return result
    except (ValueError, IndexError):
        return None


def parse_gga(parts: list[str]) -> Optional[GPSPosition]:
    """
    Parse GPGGA/GNGGA sentence (Global Positioning System Fix Data).

    Format: $GPGGA,time,lat,N/S,lon,E/W,quality,satellites,hdop,altitude,M,...
    """
    if len(parts) < 10:
        return None

    try:
        fix_quality = int(parts[6]) if parts[6] else 0

        # No fix
        if fix_quality == 0:
            return None

        lat = parse_nmea_coordinate(parts[2], parts[3])
        lon = parse_nmea_coordinate(parts[4], parts[5])

        if lat is None or lon is None:
            return None

        # Parse optional fields
        satellites = int(parts[7]) if parts[7] else None
        altitude = float(parts[9]) if parts[9] else None

        # Parse time (HHMMSS.sss)
        timestamp = None
        if parts[1]:
            try:
                time_str = parts[1].split('.')[0]
                if len(time_str) >= 6:
                    now = datetime.utcnow()
                    timestamp = now.replace(
                        hour=int(time_str[0:2]),
                        minute=int(time_str[2:4]),
                        second=int(time_str[4:6]),
                        microsecond=0
                    )
            except (ValueError, IndexError):
                pass

        return GPSPosition(
            latitude=lat,
            longitude=lon,
            altitude=altitude,
            satellites=satellites,
            fix_quality=fix_quality,
            timestamp=timestamp,
        )
    except (ValueError, IndexError) as e:
        logger.debug(f"GGA parse error: {e}")
        return None


def parse_rmc(parts: list[str]) -> Optional[GPSPosition]:
    """
    Parse GPRMC/GNRMC sentence (Recommended Minimum).

    Format: $GPRMC,time,status,lat,N/S,lon,E/W,speed,heading,date,...
    """
    if len(parts) < 8:
        return None

    try:
        # Check status (A=active/valid, V=void/invalid)
        if parts[2] != 'A':
            return None

        lat = parse_nmea_coordinate(parts[3], parts[4])
        lon = parse_nmea_coordinate(parts[5], parts[6])

        if lat is None or lon is None:
            return None

        # Parse optional fields
        speed = float(parts[7]) if parts[7] else None  # knots
        heading = float(parts[8]) if len(parts) > 8 and parts[8] else None

        # Parse timestamp
        timestamp = None
        if parts[1] and len(parts) > 9 and parts[9]:
            try:
                time_str = parts[1].split('.')[0]
                date_str = parts[9]
                if len(time_str) >= 6 and len(date_str) >= 6:
                    timestamp = datetime(
                        year=2000 + int(date_str[4:6]),
                        month=int(date_str[2:4]),
                        day=int(date_str[0:2]),
                        hour=int(time_str[0:2]),
                        minute=int(time_str[2:4]),
                        second=int(time_str[4:6]),
                    )
            except (ValueError, IndexError):
                pass

        return GPSPosition(
            latitude=lat,
            longitude=lon,
            speed=speed,
            heading=heading,
            timestamp=timestamp,
            fix_quality=1,  # RMC with A status means valid fix
        )
    except (ValueError, IndexError) as e:
        logger.debug(f"RMC parse error: {e}")
        return None


def parse_nmea_sentence(sentence: str) -> Optional[GPSPosition]:
    """
    Parse an NMEA sentence and extract position data.

    Supports: GGA, RMC sentences (with GP, GN, GL prefixes)
    """
    sentence = sentence.strip()

    # Validate checksum if present
    if '*' in sentence:
        data, checksum = sentence.rsplit('*', 1)
        if data.startswith('$'):
            data = data[1:]

        # Calculate checksum
        calc_checksum = 0
        for char in data:
            calc_checksum ^= ord(char)

        try:
            if int(checksum, 16) != calc_checksum:
                logger.debug(f"Checksum mismatch: {sentence}")
                return None
        except ValueError:
            pass

    # Remove $ prefix if present
    if sentence.startswith('$'):
        sentence = sentence[1:]

    # Remove checksum for parsing
    if '*' in sentence:
        sentence = sentence.split('*')[0]

    parts = sentence.split(',')
    if not parts:
        return None

    msg_type = parts[0]

    # Handle various NMEA talker IDs (GP=GPS, GN=GNSS, GL=GLONASS, GA=Galileo)
    if msg_type.endswith('GGA'):
        return parse_gga(parts)
    elif msg_type.endswith('RMC'):
        return parse_rmc(parts)

    return None


class GPSReader:
    """
    Reads GPS data from a serial device.

    Runs in a background thread and maintains current position.
    """

    def __init__(self, device_path: str, baudrate: int = 9600):
        self.device_path = device_path
        self.baudrate = baudrate
        self._position: Optional[GPSPosition] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._serial: Optional['serial.Serial'] = None
        self._last_update: Optional[datetime] = None
        self._error: Optional[str] = None
        self._callbacks: list[Callable[[GPSPosition], None]] = []

    @property
    def position(self) -> Optional[GPSPosition]:
        """Get the current GPS position."""
        with self._lock:
            return self._position

    @property
    def is_running(self) -> bool:
        """Check if the reader is running."""
        return self._running

    @property
    def last_update(self) -> Optional[datetime]:
        """Get the time of the last position update."""
        with self._lock:
            return self._last_update

    @property
    def error(self) -> Optional[str]:
        """Get any error message."""
        with self._lock:
            return self._error

    def add_callback(self, callback: Callable[[GPSPosition], None]) -> None:
        """Add a callback to be called on position updates."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[GPSPosition], None]) -> None:
        """Remove a position update callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def start(self) -> bool:
        """Start reading GPS data in a background thread."""
        if not SERIAL_AVAILABLE:
            self._error = "pyserial not installed"
            return False

        if self._running:
            return True

        try:
            self._serial = serial.Serial(
                self.device_path,
                baudrate=self.baudrate,
                timeout=1.0
            )
            self._running = True
            self._error = None

            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()

            logger.info(f"Started GPS reader on {self.device_path}")
            return True

        except serial.SerialException as e:
            self._error = str(e)
            logger.error(f"Failed to open GPS device {self.device_path}: {e}")
            return False

    def stop(self) -> None:
        """Stop reading GPS data."""
        self._running = False

        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

        logger.info(f"Stopped GPS reader on {self.device_path}")

    def _read_loop(self) -> None:
        """Background thread loop for reading GPS data."""
        buffer = ""
        sentence_count = 0
        bytes_read = 0

        logger.info(f"GPS read loop started on {self.device_path} at {self.baudrate} baud")

        while self._running and self._serial:
            try:
                # Read available data
                if self._serial.in_waiting:
                    data = self._serial.read(self._serial.in_waiting)
                    bytes_read += len(data)
                    if bytes_read <= 100 or bytes_read % 1000 == 0:
                        logger.info(f"GPS: Read {len(data)} bytes (total: {bytes_read})")
                    buffer += data.decode('ascii', errors='ignore')

                    # Process complete lines
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()

                        if line.startswith('$'):
                            sentence_count += 1
                            # Log first few sentences and periodically after that
                            if sentence_count <= 5 or sentence_count % 100 == 0:
                                logger.debug(f"GPS NMEA [{sentence_count}]: {line[:60]}...")

                            position = parse_nmea_sentence(line)
                            if position:
                                logger.info(f"GPS fix: {position.latitude:.6f}, {position.longitude:.6f} (sats: {position.satellites}, quality: {position.fix_quality})")
                                position.device = self.device_path
                                self._update_position(position)
                else:
                    time.sleep(0.1)

            except serial.SerialException as e:
                logger.error(f"GPS read error: {e}")
                with self._lock:
                    self._error = str(e)
                break
            except Exception as e:
                logger.debug(f"GPS parse error: {e}")

    def _update_position(self, position: GPSPosition) -> None:
        """Update the current position and notify callbacks."""
        with self._lock:
            # Merge data from different sentence types
            if self._position:
                # Keep altitude from GGA if RMC doesn't have it
                if position.altitude is None and self._position.altitude:
                    position.altitude = self._position.altitude
                # Keep satellites from GGA
                if position.satellites is None and self._position.satellites:
                    position.satellites = self._position.satellites

            self._position = position
            self._last_update = datetime.utcnow()
            self._error = None

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(position)
            except Exception as e:
                logger.error(f"GPS callback error: {e}")


# Global GPS reader instance
_gps_reader: Optional[GPSReader] = None
_gps_lock = threading.Lock()


def get_gps_reader() -> Optional[GPSReader]:
    """Get the global GPS reader instance."""
    with _gps_lock:
        return _gps_reader


def start_gps(device_path: str, baudrate: int = 9600) -> bool:
    """
    Start the global GPS reader.

    Args:
        device_path: Path to the GPS serial device
        baudrate: Serial baudrate (default 9600)

    Returns:
        True if started successfully
    """
    global _gps_reader

    with _gps_lock:
        # Stop existing reader if any
        if _gps_reader:
            _gps_reader.stop()

        _gps_reader = GPSReader(device_path, baudrate)
        return _gps_reader.start()


def stop_gps() -> None:
    """Stop the global GPS reader."""
    global _gps_reader

    with _gps_lock:
        if _gps_reader:
            _gps_reader.stop()
            _gps_reader = None


def get_current_position() -> Optional[GPSPosition]:
    """Get the current GPS position from the global reader."""
    reader = get_gps_reader()
    if reader:
        return reader.position
    return None


def is_serial_available() -> bool:
    """Check if pyserial is available."""
    return SERIAL_AVAILABLE
