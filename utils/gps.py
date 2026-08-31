"""
GPS support for INTERCEPT via gpsd daemon.

Provides GPS location data by connecting to the gpsd daemon.
"""

from __future__ import annotations

import contextlib
import logging
import socket as _socket_mod
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

logger = logging.getLogger("intercept.gps")


@dataclass
class GPSSatellite:
    """Individual satellite data from gpsd SKY message."""

    prn: int
    elevation: float | None = None  # degrees
    azimuth: float | None = None  # degrees
    snr: float | None = None  # dB-Hz
    used: bool = False
    constellation: str = "GPS"  # GPS, GLONASS, Galileo, BeiDou, SBAS, QZSS

    def to_dict(self) -> dict:
        return {
            "prn": self.prn,
            "elevation": self.elevation,
            "azimuth": self.azimuth,
            "snr": self.snr,
            "used": self.used,
            "constellation": self.constellation,
        }


@dataclass
class GPSSkyData:
    """Sky view data from gpsd SKY message."""

    satellites: list[GPSSatellite] = field(default_factory=list)
    hdop: float | None = None
    vdop: float | None = None
    pdop: float | None = None
    tdop: float | None = None
    gdop: float | None = None
    xdop: float | None = None
    ydop: float | None = None
    nsat: int = 0  # total visible
    usat: int = 0  # total used

    def to_dict(self) -> dict:
        return {
            "satellites": [s.to_dict() for s in self.satellites],
            "hdop": self.hdop,
            "vdop": self.vdop,
            "pdop": self.pdop,
            "tdop": self.tdop,
            "gdop": self.gdop,
            "xdop": self.xdop,
            "ydop": self.ydop,
            "nsat": self.nsat,
            "usat": self.usat,
        }


@dataclass
class GPSPosition:
    """GPS position data."""

    latitude: float
    longitude: float
    altitude: float | None = None
    speed: float | None = None  # m/s
    heading: float | None = None  # degrees
    climb: float | None = None  # m/s vertical speed
    satellites: int | None = None
    fix_quality: int = 0  # 0=unknown, 1=no fix, 2=2D fix, 3=3D fix
    timestamp: datetime | None = None
    device: str | None = None
    # Error estimates
    epx: float | None = None  # lon error (m)
    epy: float | None = None  # lat error (m)
    epv: float | None = None  # vertical error (m)
    eps: float | None = None  # speed error (m/s)
    ept: float | None = None  # time error (s)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "speed": self.speed,
            "heading": self.heading,
            "climb": self.climb,
            "satellites": self.satellites,
            "fix_quality": self.fix_quality,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "device": self.device,
            "epx": self.epx,
            "epy": self.epy,
            "epv": self.epv,
            "eps": self.eps,
            "ept": self.ept,
        }


def _classify_constellation(prn: int, gnssid: int | None = None) -> str:
    """Classify satellite constellation from PRN or gnssid."""
    if gnssid is not None:
        mapping = {
            0: "GPS",
            1: "SBAS",
            2: "Galileo",
            3: "BeiDou",
            4: "IMES",
            5: "QZSS",
            6: "GLONASS",
            7: "NavIC",
        }
        return mapping.get(gnssid, "GPS")
    # Fall back to PRN range heuristic
    if 1 <= prn <= 32:
        return "GPS"
    elif 33 <= prn <= 64:
        return "SBAS"
    elif 65 <= prn <= 96:
        return "GLONASS"
    elif 120 <= prn <= 158:
        return "SBAS"
    elif 201 <= prn <= 264:
        return "BeiDou"
    elif 301 <= prn <= 336:
        return "Galileo"
    elif 193 <= prn <= 200:
        return "QZSS"
    return "GPS"


class GPSDClient:
    """
    Connects to gpsd daemon for GPS data.

    gpsd provides a unified interface for GPS devices and handles
    device management, making it ideal when gpsd is already running.
    """

    DEFAULT_HOST = "localhost"
    DEFAULT_PORT = 2947

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._position: GPSPosition | None = None
        self._sky: GPSSkyData | None = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._socket: _socket_mod.socket | None = None
        self._last_update: datetime | None = None
        self._error: str | None = None
        self._callbacks: list[Callable[[GPSPosition], None]] = []
        self._sky_callbacks: list[Callable[[GPSSkyData], None]] = []
        self._device: str | None = None

    @property
    def position(self) -> GPSPosition | None:
        """Get the current GPS position."""
        with self._lock:
            return self._position

    @property
    def sky(self) -> GPSSkyData | None:
        """Get the current sky view data."""
        with self._lock:
            return self._sky

    @property
    def is_running(self) -> bool:
        """Check if the client is running."""
        return self._running

    @property
    def last_update(self) -> datetime | None:
        """Get the time of the last position update."""
        with self._lock:
            return self._last_update

    @property
    def error(self) -> str | None:
        """Get any error message."""
        with self._lock:
            return self._error

    @property
    def device_path(self) -> str:
        """Return gpsd connection info."""
        return f"gpsd://{self.host}:{self.port}"

    def add_callback(self, callback: Callable[[GPSPosition], None]) -> None:
        """Add a callback to be called on position updates."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[GPSPosition], None]) -> None:
        """Remove a position update callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def add_sky_callback(self, callback: Callable[[GPSSkyData], None]) -> None:
        """Add a callback to be called on sky data updates."""
        if callback not in self._sky_callbacks:
            self._sky_callbacks.append(callback)

    def remove_sky_callback(self, callback: Callable[[GPSSkyData], None]) -> None:
        """Remove a sky data update callback."""
        if callback in self._sky_callbacks:
            self._sky_callbacks.remove(callback)

    def start(self) -> bool:
        """Start receiving GPS data from gpsd."""
        import socket

        if self._running:
            return True

        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(5.0)
            self._socket.connect((self.host, self.port))

            # Enable JSON watch mode
            watch_cmd = '?WATCH={"enable":true,"json":true}\n'
            self._socket.send(watch_cmd.encode("ascii"))

            self._running = True
            self._error = None

            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()

            logger.info(f"Connected to gpsd at {self.host}:{self.port}")
            print(f"[GPS] Connected to gpsd at {self.host}:{self.port}", flush=True)
            return True

        except Exception as e:
            self._error = str(e)
            logger.error(f"Failed to connect to gpsd at {self.host}:{self.port}: {e}")
            if self._socket:
                with contextlib.suppress(Exception):
                    self._socket.close()
                self._socket = None
            return False

    def stop(self) -> None:
        """Stop receiving GPS data."""
        self._running = False

        if self._socket:
            try:
                # Disable watch mode
                self._socket.send(b'?WATCH={"enable":false}\n')
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

        logger.info(f"Disconnected from gpsd at {self.host}:{self.port}")

    def _read_loop(self) -> None:
        """Background thread loop for reading gpsd data."""
        import json
        import socket

        buffer = ""
        message_count = 0

        print("[GPS] gpsd read loop started", flush=True)

        while self._running and self._socket:
            try:
                self._socket.settimeout(1.0)
                data = self._socket.recv(4096)

                if not data:
                    logger.warning("gpsd connection closed")
                    with self._lock:
                        self._error = "Connection closed by gpsd"
                    break

                buffer += data.decode("ascii", errors="ignore")

                # Process complete JSON lines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()

                    if not line:
                        continue

                    try:
                        msg = json.loads(line)
                        msg_class = msg.get("class", "")

                        message_count += 1
                        if message_count <= 5 or message_count % 20 == 0:
                            print(f"[GPS] gpsd msg [{message_count}]: {msg_class}", flush=True)

                        if msg_class == "TPV":
                            self._handle_tpv(msg)
                        elif msg_class == "SKY":
                            self._handle_sky(msg)
                        elif msg_class == "DEVICES":
                            # Track connected device
                            devices = msg.get("devices", [])
                            if devices:
                                self._device = devices[0].get("path", "unknown")
                                print(f"[GPS] gpsd device: {self._device}", flush=True)

                    except json.JSONDecodeError:
                        logger.debug(f"Invalid JSON from gpsd: {line[:50]}")
                    except Exception as parse_err:
                        logger.error(f"Error handling gpsd {msg_class} message: {parse_err}")

            except socket.timeout:
                continue
            except Exception as e:
                logger.error(f"gpsd read error: {e}")
                with self._lock:
                    self._error = str(e)
                break

    def _handle_tpv(self, msg: dict) -> None:
        """Handle TPV (Time-Position-Velocity) message from gpsd."""
        # mode: 0=unknown, 1=no fix, 2=2D fix, 3=3D fix
        mode = msg.get("mode", 0)

        if mode < 2:
            # No fix yet
            return

        lat = msg.get("lat")
        lon = msg.get("lon")

        if lat is None or lon is None:
            return

        # Parse timestamp
        timestamp = None
        time_str = msg.get("time")
        if time_str:
            with contextlib.suppress(ValueError, AttributeError):
                # gpsd uses ISO format: 2024-01-01T12:00:00.000Z
                timestamp = datetime.fromisoformat(time_str.replace("Z", "+00:00"))

        position = GPSPosition(
            latitude=lat,
            longitude=lon,
            altitude=msg.get("alt"),
            speed=msg.get("speed"),  # m/s in gpsd
            heading=msg.get("track"),
            climb=msg.get("climb"),
            fix_quality=mode,
            timestamp=timestamp,
            device=self._device or f"gpsd://{self.host}:{self.port}",
            epx=msg.get("epx"),
            epy=msg.get("epy"),
            epv=msg.get("epv"),
            eps=msg.get("eps"),
            ept=msg.get("ept"),
        )

        print(f"[GPS] gpsd FIX: {lat:.6f}, {lon:.6f} (mode: {mode})", flush=True)
        self._update_position(position)

    def _handle_sky(self, msg: dict) -> None:
        """Handle SKY (satellite sky view) message from gpsd.

        gpsd sends multiple SKY messages per cycle: some contain only DOP
        values while others include the full satellites array.  When a
        DOP-only SKY arrives, preserve the most recent satellite list
        instead of overwriting it with an empty one.
        """
        raw_sats = msg.get("satellites", [])
        has_satellites = len(raw_sats) > 0

        if has_satellites:
            sats = []
            for sat in raw_sats:
                prn = sat.get("PRN", 0)
                gnssid = sat.get("gnssid")
                sats.append(
                    GPSSatellite(
                        prn=prn,
                        elevation=sat.get("el"),
                        azimuth=sat.get("az"),
                        snr=sat.get("ss"),
                        used=sat.get("used", False),
                        constellation=_classify_constellation(prn, gnssid),
                    )
                )
        else:
            # DOP-only SKY message — keep existing satellites
            with self._lock:
                sats = list(self._sky.satellites) if self._sky else []

        sky_data = GPSSkyData(
            satellites=sats,
            hdop=msg.get("hdop"),
            vdop=msg.get("vdop"),
            pdop=msg.get("pdop"),
            tdop=msg.get("tdop"),
            gdop=msg.get("gdop"),
            xdop=msg.get("xdop"),
            ydop=msg.get("ydop"),
            nsat=len(sats),
            usat=sum(1 for s in sats if s.used),
        )

        with self._lock:
            self._sky = sky_data

        # Notify sky callbacks
        for callback in self._sky_callbacks:
            try:
                callback(sky_data)
            except Exception as e:
                logger.error(f"GPS sky callback error: {e}")

    def _update_position(self, position: GPSPosition) -> None:
        """Update the current position and notify callbacks."""
        with self._lock:
            self._position = position
            self._last_update = datetime.utcnow()
            self._error = None

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(position)
            except Exception as e:
                logger.error(f"GPS callback error: {e}")


# Global GPS client instance
_gps_client: GPSDClient | None = None
_gps_lock = threading.Lock()

# Fallback position from a non-gpsd source (e.g. a Meshtastic node's onboard
# GPS) — used only when gpsd isn't connected or has no position at all, so it
# never overrides a real gpsd fix.
_external_position: GPSPosition | None = None
_external_lock = threading.Lock()


def set_external_position(position: GPSPosition) -> None:
    """Register a GPS position from a non-gpsd source."""
    global _external_position
    with _external_lock:
        _external_position = position


def get_external_position() -> GPSPosition | None:
    """Return the most recently registered non-gpsd position, if any."""
    with _external_lock:
        return _external_position


def get_gps_reader() -> GPSDClient | None:
    """Get the global GPS client instance."""
    with _gps_lock:
        return _gps_client


def start_gpsd(
    host: str = "localhost",
    port: int = 2947,
    callback: Callable[[GPSPosition], None] | None = None,
    sky_callback: Callable[[GPSSkyData], None] | None = None,
) -> bool:
    """
    Start the global GPS client connected to gpsd.

    Args:
        host: gpsd host (default localhost)
        port: gpsd port (default 2947)
        callback: Optional callback for position updates
        sky_callback: Optional callback for sky data updates

    Returns:
        True if started successfully
    """
    global _gps_client

    with _gps_lock:
        # Stop existing client if any
        if _gps_client:
            _gps_client.stop()

        _gps_client = GPSDClient(host, port)

        # Register callbacks BEFORE starting to avoid race condition
        if callback:
            _gps_client.add_callback(callback)
        if sky_callback:
            _gps_client.add_sky_callback(sky_callback)

        return _gps_client.start()


def stop_gps() -> None:
    """Stop the global GPS client."""
    global _gps_client

    with _gps_lock:
        if _gps_client:
            _gps_client.stop()
            _gps_client = None


def get_current_position() -> GPSPosition | None:
    """Get the current GPS position from the global client, falling back to
    an externally-registered position (e.g. Meshtastic) when gpsd has none."""
    client = get_gps_reader()
    if client and client.position:
        return client.position
    return get_external_position()


# ============================================
# GPS device detection and gpsd auto-start
# ============================================

_gpsd_process: subprocess.Popen | None = None
_gpsd_process_lock = threading.RLock()


def detect_gps_devices() -> list[dict]:
    """
    Detect connected GPS serial devices.

    Returns list of dicts with 'path' and 'description' keys.
    """
    import glob
    import os
    import platform

    devices: list[dict] = []
    system = platform.system()

    if system == "Linux":
        # Common USB GPS device paths
        patterns = ["/dev/ttyUSB*", "/dev/ttyACM*"]
        for pattern in patterns:
            for path in sorted(glob.glob(pattern)):
                desc = _describe_device_linux(path)
                devices.append({"path": path, "description": desc})

        # Also check /dev/serial/by-id for descriptive names
        serial_dir = "/dev/serial/by-id"
        if os.path.isdir(serial_dir):
            for name in sorted(os.listdir(serial_dir)):
                full = os.path.join(serial_dir, name)
                real = os.path.realpath(full)
                # Skip if we already found this device
                if any(d["path"] == real for d in devices):
                    # Update description with the more descriptive name
                    for d in devices:
                        if d["path"] == real:
                            d["description"] = name
                    continue
                devices.append({"path": real, "description": name})

    elif system == "Darwin":
        # macOS: USB serial devices (prefer cu. over tty. for outgoing)
        patterns = ["/dev/cu.usbmodem*", "/dev/cu.usbserial*"]
        for pattern in patterns:
            for path in sorted(glob.glob(pattern)):
                desc = _describe_device_macos(path)
                devices.append({"path": path, "description": desc})

    # Sort: devices with GPS-related descriptions first
    gps_keywords = ("gps", "gnss", "u-blox", "ublox", "nmea", "sirf", "navigation")
    devices.sort(key=lambda d: (0 if any(k in d["description"].lower() for k in gps_keywords) else 1))

    return devices


def _describe_device_linux(path: str) -> str:
    """Get a human-readable description of a Linux serial device."""
    import os

    basename = os.path.basename(path)
    # Try to read from sysfs
    try:
        # /sys/class/tty/ttyUSB0/device/../product
        sysfs = f"/sys/class/tty/{basename}/device/../product"
        if os.path.exists(sysfs):
            with open(sysfs) as f:
                return f.read().strip()
    except Exception:
        pass
    return basename


def _describe_device_macos(path: str) -> str:
    """Get a description of a macOS serial device."""
    import os

    return os.path.basename(path)


def is_gpsd_running(host: str = "localhost", port: int = 2947) -> bool:
    """Check if gpsd is reachable."""
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect((host, port))
        sock.close()
        return True
    except Exception:
        return False


def start_gpsd_daemon(device_path: str, host: str = "localhost", port: int = 2947) -> tuple[bool, str]:
    """
    Start gpsd daemon pointing at the given device.

    Returns (success, message) tuple.
    """
    import shutil
    import subprocess

    global _gpsd_process

    with _gpsd_process_lock:
        # Already running?
        if is_gpsd_running(host, port):
            return True, "gpsd already running"

        gpsd_bin = shutil.which("gpsd")
        if not gpsd_bin:
            return False, "gpsd not installed"

        # Stop any existing managed process
        stop_gpsd_daemon()

        try:
            import os

            if not os.path.exists(device_path):
                return False, f"Device {device_path} not found"

            cmd = [gpsd_bin, "-N", "-n", "-S", str(port), device_path]
            logger.info(f"Starting gpsd: {' '.join(cmd)}")
            print(f"[GPS] Starting gpsd: {' '.join(cmd)}", flush=True)

            _gpsd_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            # Give gpsd a moment to start
            import time

            time.sleep(1.5)

            if _gpsd_process.poll() is not None:
                stderr = ""
                if _gpsd_process.stderr:
                    stderr = _gpsd_process.stderr.read().decode("utf-8", errors="ignore").strip()
                msg = f"gpsd exited with code {_gpsd_process.returncode}"
                if stderr:
                    msg += f": {stderr}"
                return False, msg

            # Verify it's listening
            if is_gpsd_running(host, port):
                return True, f"gpsd started on {device_path}"
            else:
                return False, "gpsd started but not accepting connections"

        except Exception as e:
            logger.error(f"Failed to start gpsd: {e}")
            return False, str(e)


def stop_gpsd_daemon() -> None:
    """Stop the managed gpsd daemon process."""
    global _gpsd_process

    with _gpsd_process_lock:
        if _gpsd_process and _gpsd_process.poll() is None:
            try:
                _gpsd_process.terminate()
                _gpsd_process.wait(timeout=3.0)
            except Exception:
                with contextlib.suppress(Exception):
                    _gpsd_process.kill()
            logger.info("Stopped gpsd daemon")
            print("[GPS] Stopped gpsd daemon", flush=True)
        _gpsd_process = None
