"""
BLE Scanner for TSCM

Cross-platform BLE scanning with manufacturer data detection.
Supports macOS and Linux using the bleak library with fallback to system tools.

Detects:
- Apple AirTags (company ID 0x004C)
- Tile trackers
- Samsung SmartTags
- ESP32/ESP8266 devices (Espressif, company ID 0x02E5)
- Generic BLE devices with suspicious characteristics
"""

import asyncio
import logging
import platform
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger("intercept.tscm.ble")

# Manufacturer company IDs (Bluetooth SIG assigned)
COMPANY_IDS = {
    0x004C: "Apple",
    0x02E5: "Espressif",
    0x0059: "Nordic Semiconductor",
    0x000D: "Texas Instruments",
    0x0075: "Samsung",
    0x00E0: "Google",
    0x0006: "Microsoft",
    0x01DA: "Tile",
}

# Known tracker signatures
TRACKER_SIGNATURES = {
    # Apple AirTag detection patterns
    "airtag": {
        "company_id": 0x004C,
        "data_patterns": [
            b"\x12\x19",  # AirTag/Find My advertisement prefix
            b"\x07\x19",  # Offline Finding
        ],
        "name_patterns": ["airtag", "findmy", "find my"],
    },
    # Tile tracker
    "tile": {
        "company_id": 0x01DA,
        "name_patterns": ["tile"],
    },
    # Samsung SmartTag
    "smarttag": {
        "company_id": 0x0075,
        "name_patterns": ["smarttag", "smart tag", "galaxy smart"],
    },
    # ESP32/ESP8266
    "espressif": {
        "company_id": 0x02E5,
        "name_patterns": ["esp32", "esp8266", "espressif"],
    },
}


@dataclass
class BLEDevice:
    """Represents a detected BLE device with full advertisement data."""

    mac: str
    name: Optional[str] = None
    rssi: Optional[int] = None
    manufacturer_id: Optional[int] = None
    manufacturer_name: Optional[str] = None
    manufacturer_data: bytes = field(default_factory=bytes)
    service_uuids: list = field(default_factory=list)
    tx_power: Optional[int] = None
    is_connectable: bool = True

    # Detection flags
    is_airtag: bool = False
    is_tile: bool = False
    is_smarttag: bool = False
    is_espressif: bool = False
    is_tracker: bool = False
    tracker_type: Optional[str] = None

    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    detection_count: int = 1

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "mac": self.mac,
            "name": self.name or "Unknown",
            "rssi": self.rssi,
            "manufacturer_id": self.manufacturer_id,
            "manufacturer_name": self.manufacturer_name,
            "service_uuids": self.service_uuids,
            "tx_power": self.tx_power,
            "is_connectable": self.is_connectable,
            "is_airtag": self.is_airtag,
            "is_tile": self.is_tile,
            "is_smarttag": self.is_smarttag,
            "is_espressif": self.is_espressif,
            "is_tracker": self.is_tracker,
            "tracker_type": self.tracker_type,
            "detection_count": self.detection_count,
            "type": "ble",
        }


class BLEScanner:
    """
    Cross-platform BLE scanner with manufacturer data detection.

    Uses bleak library for proper BLE scanning, with fallback to
    system tools (hcitool/btmgmt on Linux, system_profiler on macOS).
    """

    def __init__(self):
        self.devices: dict[str, BLEDevice] = {}
        self._bleak_available = self._check_bleak()
        self._scanning = False

    def _check_bleak(self) -> bool:
        """Check if bleak library is available."""
        try:
            import bleak

            return True
        except ImportError:
            logger.warning("bleak library not available - using fallback scanning")
            return False

    async def scan_async(self, duration: int = 10) -> list[BLEDevice]:
        """
        Perform async BLE scan using bleak.

        Args:
            duration: Scan duration in seconds

        Returns:
            List of detected BLE devices
        """
        if not self._bleak_available:
            # Use synchronous fallback
            return self._scan_fallback(duration)

        try:
            from bleak import BleakScanner
            from bleak.backends.device import BLEDevice as BleakDevice
            from bleak.backends.scanner import AdvertisementData

            detected = {}

            def detection_callback(device: BleakDevice, adv_data: AdvertisementData):
                """Callback for each detected device."""
                mac = device.address.upper()

                if mac in detected:
                    # Update existing device
                    detected[mac].rssi = adv_data.rssi
                    detected[mac].last_seen = datetime.now()
                    detected[mac].detection_count += 1
                else:
                    # Create new device entry
                    ble_device = BLEDevice(
                        mac=mac,
                        name=adv_data.local_name or device.name,
                        rssi=adv_data.rssi,
                        service_uuids=list(adv_data.service_uuids) if adv_data.service_uuids else [],
                        tx_power=adv_data.tx_power,
                    )

                    # Parse manufacturer data
                    if adv_data.manufacturer_data:
                        for company_id, data in adv_data.manufacturer_data.items():
                            ble_device.manufacturer_id = company_id
                            ble_device.manufacturer_name = COMPANY_IDS.get(company_id, f"Unknown ({hex(company_id)})")
                            # Handle various data types safely
                            try:
                                if isinstance(data, (bytes, bytearray, list, tuple)):
                                    ble_device.manufacturer_data = bytes(data)
                                elif isinstance(data, str):
                                    ble_device.manufacturer_data = bytes.fromhex(data)
                                else:
                                    ble_device.manufacturer_data = bytes(data)
                            except (TypeError, ValueError) as e:
                                logger.debug(f"Could not convert manufacturer data: {e}")
                                ble_device.manufacturer_data = None

                            # Check for known trackers
                            self._identify_tracker(ble_device, company_id, data)

                    # Also check name patterns
                    self._check_name_patterns(ble_device)

                    detected[mac] = ble_device

            logger.info(f"Starting BLE scan with bleak (duration={duration}s)")

            scanner = BleakScanner(detection_callback=detection_callback)
            await scanner.start()
            await asyncio.sleep(duration)
            await scanner.stop()

            # Update internal device list
            for mac, device in detected.items():
                if mac in self.devices:
                    self.devices[mac].rssi = device.rssi
                    self.devices[mac].last_seen = device.last_seen
                    self.devices[mac].detection_count += 1
                else:
                    self.devices[mac] = device

            logger.info(f"BLE scan complete: {len(detected)} devices found")
            return list(detected.values())

        except Exception as e:
            logger.error(f"Bleak scan failed: {e}")
            return self._scan_fallback(duration)

    def scan(self, duration: int = 10) -> list[BLEDevice]:
        """
        Synchronous wrapper for BLE scanning.

        Args:
            duration: Scan duration in seconds

        Returns:
            List of detected BLE devices
        """
        if self._bleak_available:
            try:
                # Try to get existing event loop
                try:
                    asyncio.get_running_loop()
                    # We're in an async context, can't use run()
                    future = asyncio.ensure_future(self.scan_async(duration))
                    return asyncio.get_event_loop().run_until_complete(future)
                except RuntimeError:
                    # No running loop, create one
                    return asyncio.run(self.scan_async(duration))
            except Exception as e:
                logger.error(f"Async scan failed: {e}")
                return self._scan_fallback(duration)
        else:
            return self._scan_fallback(duration)

    def _identify_tracker(self, device: BLEDevice, company_id: int, data: bytes):
        """Identify if device is a known tracker type."""

        # Apple AirTag detection
        if company_id == 0x004C:  # Apple
            # Check for Find My / AirTag advertisement patterns
            if len(data) >= 2:
                # AirTag advertisements have specific byte patterns
                if data[0] == 0x12 and data[1] == 0x19:
                    device.is_airtag = True
                    device.is_tracker = True
                    device.tracker_type = "AirTag"
                    logger.info(f"AirTag detected: {device.mac}")
                elif data[0] == 0x07:  # Offline Finding
                    device.is_airtag = True
                    device.is_tracker = True
                    device.tracker_type = "AirTag (Offline)"
                    logger.info(f"AirTag (offline mode) detected: {device.mac}")

        # Tile tracker
        elif company_id == 0x01DA:  # Tile
            device.is_tile = True
            device.is_tracker = True
            device.tracker_type = "Tile"
            logger.info(f"Tile tracker detected: {device.mac}")

        # Samsung SmartTag
        elif company_id == 0x0075:  # Samsung
            # Check if it's specifically a SmartTag
            device.is_smarttag = True
            device.is_tracker = True
            device.tracker_type = "SmartTag"
            logger.info(f"Samsung SmartTag detected: {device.mac}")

        # Espressif (ESP32/ESP8266)
        elif company_id == 0x02E5:  # Espressif
            device.is_espressif = True
            device.tracker_type = "ESP32/ESP8266"
            logger.info(f"ESP32/ESP8266 device detected: {device.mac}")

    def _check_name_patterns(self, device: BLEDevice):
        """Check device name for tracker patterns."""
        if not device.name:
            return

        name_lower = device.name.lower()

        # Check each tracker type
        for tracker_type, sig in TRACKER_SIGNATURES.items():
            patterns = sig.get("name_patterns", [])
            for pattern in patterns:
                if pattern in name_lower:
                    if tracker_type == "airtag":
                        device.is_airtag = True
                        device.is_tracker = True
                        device.tracker_type = "AirTag"
                    elif tracker_type == "tile":
                        device.is_tile = True
                        device.is_tracker = True
                        device.tracker_type = "Tile"
                    elif tracker_type == "smarttag":
                        device.is_smarttag = True
                        device.is_tracker = True
                        device.tracker_type = "SmartTag"
                    elif tracker_type == "espressif":
                        device.is_espressif = True
                        device.tracker_type = "ESP32/ESP8266"

                    logger.info(f"Tracker identified by name: {device.name} -> {tracker_type}")
                    return

    def _scan_fallback(self, duration: int = 10) -> list[BLEDevice]:
        """
        Fallback scanning using system tools when bleak is unavailable.
        Works on both macOS and Linux.
        """
        system = platform.system()

        if system == "Darwin":
            return self._scan_macos(duration)
        else:
            return self._scan_linux(duration)

    def _scan_macos(self, duration: int = 10) -> list[BLEDevice]:
        """Fallback BLE scanning on macOS using system_profiler."""
        devices = []

        try:
            import json

            result = subprocess.run(
                ["system_profiler", "SPBluetoothDataType", "-json"], capture_output=True, text=True, timeout=15
            )
            data = json.loads(result.stdout)
            bt_data = data.get("SPBluetoothDataType", [{}])[0]

            # Get connected/paired devices
            for section in ["device_connected", "device_title"]:
                section_data = bt_data.get(section, {})
                if isinstance(section_data, dict):
                    for name, info in section_data.items():
                        if isinstance(info, dict):
                            mac = info.get("device_address", "").upper()
                            if mac:
                                device = BLEDevice(
                                    mac=mac,
                                    name=name,
                                )
                                # Check name patterns
                                self._check_name_patterns(device)
                                devices.append(device)

            logger.info(f"macOS fallback scan found {len(devices)} devices")
        except Exception as e:
            logger.error(f"macOS fallback scan failed: {e}")

        return devices

    def _scan_linux(self, duration: int = 10) -> list[BLEDevice]:
        """Fallback BLE scanning on Linux using bluetoothctl/btmgmt."""
        import shutil

        devices = []
        seen_macs = set()

        # Method 1: Try btmgmt for BLE devices
        if shutil.which("btmgmt"):
            try:
                logger.info("Trying btmgmt find...")
                result = subprocess.run(["btmgmt", "find"], capture_output=True, text=True, timeout=duration + 5)

                for line in result.stdout.split("\n"):
                    if "dev_found" in line.lower() or ("type" in line.lower() and ":" in line):
                        mac_match = re.search(
                            r"([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:"
                            r"[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})",
                            line,
                        )
                        if mac_match:
                            mac = mac_match.group(1).upper()
                            if mac not in seen_macs:
                                seen_macs.add(mac)
                                name_match = re.search(r"name\s+(.+?)(?:\s|$)", line, re.I)
                                name = name_match.group(1) if name_match else None

                                device = BLEDevice(mac=mac, name=name)
                                self._check_name_patterns(device)
                                devices.append(device)

                logger.info(f"btmgmt found {len(devices)} devices")
            except Exception as e:
                logger.warning(f"btmgmt failed: {e}")

        # Method 2: Try hcitool lescan
        if not devices and shutil.which("hcitool"):
            try:
                logger.info("Trying hcitool lescan...")
                # Start lescan in background
                process = subprocess.Popen(
                    ["hcitool", "lescan", "--duplicates"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    errors="replace",
                )

                import time

                time.sleep(duration)
                process.terminate()

                stdout, _ = process.communicate(timeout=2)

                for line in stdout.split("\n"):
                    mac_match = re.search(
                        r"([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:"
                        r"[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})",
                        line,
                    )
                    if mac_match:
                        mac = mac_match.group(1).upper()
                        if mac not in seen_macs:
                            seen_macs.add(mac)
                            # Extract name (comes after MAC)
                            parts = line.strip().split()
                            name = " ".join(parts[1:]) if len(parts) > 1 else None

                            device = BLEDevice(mac=mac, name=name if name != "(unknown)" else None)
                            self._check_name_patterns(device)
                            devices.append(device)

                logger.info(f"hcitool lescan found {len(devices)} devices")
            except Exception as e:
                logger.warning(f"hcitool lescan failed: {e}")

        return devices

    def get_trackers(self) -> list[BLEDevice]:
        """Get all detected tracker devices."""
        return [d for d in self.devices.values() if d.is_tracker]

    def get_espressif_devices(self) -> list[BLEDevice]:
        """Get all detected ESP32/ESP8266 devices."""
        return [d for d in self.devices.values() if d.is_espressif]

    def clear(self):
        """Clear all detected devices."""
        self.devices.clear()


# Singleton instance
_scanner: Optional[BLEScanner] = None


def get_ble_scanner() -> BLEScanner:
    """Get the global BLE scanner instance."""
    global _scanner
    if _scanner is None:
        _scanner = BLEScanner()
    return _scanner


def scan_ble_devices(duration: int = 10) -> list[dict]:
    """
    Convenience function to scan for BLE devices.

    Args:
        duration: Scan duration in seconds

    Returns:
        List of device dictionaries
    """
    scanner = get_ble_scanner()
    devices = scanner.scan(duration)
    return [d.to_dict() for d in devices]
