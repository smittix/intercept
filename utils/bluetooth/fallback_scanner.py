"""
Fallback Bluetooth scanners when DBus/BlueZ is unavailable.

Supports:
- bleak (cross-platform, async)
- hcitool lescan (Linux, requires root)
- bluetoothctl (Linux)
- btmgmt (Linux)
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import threading
from datetime import datetime
from typing import Callable

from .constants import (
    ADDRESS_TYPE_PUBLIC,
    ADDRESS_TYPE_RANDOM,
    ADDRESS_TYPE_UUID,
    BLEAK_SCAN_TIMEOUT,
)

# CoreBluetooth UUID pattern: 8-4-4-4-12 hex digits
_CB_UUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
import contextlib

from .models import BTObservation

logger = logging.getLogger(__name__)


class BleakScanner:
    """
    Cross-platform BLE scanner using bleak library.

    Works on Linux, macOS, and Windows.
    """

    def __init__(
        self,
        on_observation: Callable[[BTObservation], None] | None = None,
    ):
        self._on_observation = on_observation
        self._scanner = None
        self._is_scanning = False
        self._scan_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self, duration: float = BLEAK_SCAN_TIMEOUT) -> bool:
        """Start bleak scanning in background thread."""
        try:
            import bleak

            if self._is_scanning:
                return True

            self._stop_event.clear()
            self._scan_thread = threading.Thread(target=self._scan_loop, args=(duration,), daemon=True)
            self._scan_thread.start()
            self._is_scanning = True
            logger.info("Bleak scanner started")
            return True

        except ImportError:
            logger.error("bleak library not installed")
            return False
        except Exception as e:
            logger.error(f"Failed to start bleak scanner: {e}")
            return False

    def stop(self) -> None:
        """Stop bleak scanning."""
        self._stop_event.set()
        if self._scan_thread:
            self._scan_thread.join(timeout=2.0)
        self._is_scanning = False
        logger.info("Bleak scanner stopped")

    @property
    def is_scanning(self) -> bool:
        return self._is_scanning

    def _scan_loop(self, duration: float) -> None:
        """Run scanning in async event loop."""
        try:
            asyncio.run(self._async_scan(duration))
        except Exception as e:
            logger.error(f"Bleak scan error: {e}")
        finally:
            self._is_scanning = False

    async def _async_scan(self, duration: float) -> None:
        """Async scanning coroutine."""
        try:
            from bleak import BleakScanner as BleakLib
            from bleak.backends.device import BLEDevice
            from bleak.backends.scanner import AdvertisementData

            def detection_callback(device: BLEDevice, adv_data: AdvertisementData):
                if self._stop_event.is_set():
                    return

                try:
                    observation = self._convert_bleak_device(device, adv_data)
                    if self._on_observation:
                        self._on_observation(observation)
                except Exception as e:
                    logger.debug(f"Error converting bleak device: {e}")

            scanner = BleakLib(detection_callback=detection_callback)
            await scanner.start()

            # Wait for duration or stop event
            start_time = asyncio.get_event_loop().time()
            while not self._stop_event.is_set():
                await asyncio.sleep(0.1)
                if duration > 0 and (asyncio.get_event_loop().time() - start_time) >= duration:
                    break

            await scanner.stop()

        except Exception as e:
            logger.error(f"Async scan error: {e}")

    def _convert_bleak_device(self, device, adv_data) -> BTObservation:
        """Convert bleak device to BTObservation."""
        # Determine address type from address format
        address_type = ADDRESS_TYPE_PUBLIC
        if device.address and _CB_UUID_RE.match(device.address):
            # macOS CoreBluetooth returns a platform UUID instead of a real MAC
            address_type = ADDRESS_TYPE_UUID
        elif device.address and ":" in device.address:
            # Check if first byte indicates random address
            first_byte = int(device.address.split(":")[0], 16)
            if (first_byte & 0xC0) == 0xC0:  # Random static
                address_type = ADDRESS_TYPE_RANDOM

        # Extract manufacturer data
        manufacturer_id = None
        manufacturer_data = None
        if adv_data.manufacturer_data:
            for mid, mdata in adv_data.manufacturer_data.items():
                manufacturer_id = mid
                # Handle various data types safely
                try:
                    if isinstance(mdata, (bytes, bytearray, list, tuple)):
                        manufacturer_data = bytes(mdata)
                    elif isinstance(mdata, str):
                        manufacturer_data = bytes.fromhex(mdata)
                    else:
                        manufacturer_data = bytes(mdata)
                except (TypeError, ValueError) as e:
                    logger.debug(f"Could not convert manufacturer data: {e}")
                break

        # Extract service data
        service_data = {}
        if adv_data.service_data:
            for uuid, data in adv_data.service_data.items():
                try:
                    if isinstance(data, (bytes, bytearray, list, tuple)):
                        service_data[str(uuid)] = bytes(data)
                    elif isinstance(data, str):
                        service_data[str(uuid)] = bytes.fromhex(data)
                    else:
                        service_data[str(uuid)] = bytes(data)
                except (TypeError, ValueError) as e:
                    logger.debug(f"Could not convert service data for {uuid}: {e}")

        return BTObservation(
            timestamp=datetime.now(),
            address=device.address.upper() if device.address else "",
            address_type=address_type,
            rssi=adv_data.rssi,
            tx_power=adv_data.tx_power,
            name=adv_data.local_name or device.name,
            manufacturer_id=manufacturer_id,
            manufacturer_data=manufacturer_data,
            service_uuids=list(adv_data.service_uuids) if adv_data.service_uuids else [],
            service_data=service_data,
            is_connectable=getattr(adv_data, "connectable", True) if adv_data else True,
        )


class HcitoolScanner:
    """
    Linux hcitool-based scanner for BLE devices.

    Requires root privileges.
    """

    def __init__(
        self,
        adapter: str = "hci0",
        on_observation: Callable[[BTObservation], None] | None = None,
    ):
        self._adapter = adapter
        self._on_observation = on_observation
        self._process: subprocess.Popen | None = None
        self._is_scanning = False
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> bool:
        """Start hcitool lescan."""
        try:
            if self._is_scanning:
                return True

            # Start hcitool lescan with duplicate reporting
            self._process = subprocess.Popen(
                ["hcitool", "-i", self._adapter, "lescan", "--duplicates"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )

            self._stop_event.clear()
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()
            self._is_scanning = True
            logger.info(f"hcitool scanner started on {self._adapter}")
            return True

        except FileNotFoundError:
            logger.error("hcitool not found")
            return False
        except PermissionError:
            logger.error("hcitool requires root privileges")
            return False
        except Exception as e:
            logger.error(f"Failed to start hcitool scanner: {e}")
            return False

    def stop(self) -> None:
        """Stop hcitool scanning."""
        self._stop_event.set()
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except Exception:
                self._process.kill()
            self._process = None

        if self._reader_thread:
            self._reader_thread.join(timeout=2.0)

        self._is_scanning = False
        logger.info("hcitool scanner stopped")

    @property
    def is_scanning(self) -> bool:
        return self._is_scanning

    def _read_output(self) -> None:
        """Read hcitool output and parse devices."""
        try:
            # Also start hcidump in parallel for RSSI values
            dump_process = None
            with contextlib.suppress(Exception):
                dump_process = subprocess.Popen(
                    ["hcidump", "-i", self._adapter, "--raw"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            while not self._stop_event.is_set() and self._process:
                line = self._process.stdout.readline()
                if not line:
                    break

                # Parse hcitool output: "AA:BB:CC:DD:EE:FF DeviceName"
                match = re.match(r"^([0-9A-Fa-f:]{17})\s*(.*)$", line.strip())
                if match:
                    address = match.group(1).upper()
                    name = match.group(2).strip() or None

                    observation = BTObservation(
                        timestamp=datetime.now(),
                        address=address,
                        address_type=ADDRESS_TYPE_PUBLIC,
                        name=name if name and name != "(unknown)" else None,
                    )

                    if self._on_observation:
                        self._on_observation(observation)

            if dump_process:
                dump_process.terminate()

        except Exception as e:
            logger.error(f"hcitool read error: {e}")
        finally:
            self._is_scanning = False


class BluetoothctlScanner:
    """
    Linux bluetoothctl-based scanner.

    Works without root but may have limited data.
    """

    def __init__(
        self,
        on_observation: Callable[[BTObservation], None] | None = None,
    ):
        self._on_observation = on_observation
        self._process: subprocess.Popen | None = None
        self._is_scanning = False
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._devices: dict[str, dict] = {}

    def start(self) -> bool:
        """Start bluetoothctl scanning."""
        try:
            if self._is_scanning:
                return True

            self._process = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )

            self._stop_event.clear()
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()

            # Send scan on command
            self._process.stdin.write("scan on\n")
            self._process.stdin.flush()

            self._is_scanning = True
            logger.info("bluetoothctl scanner started")
            return True

        except FileNotFoundError:
            logger.error("bluetoothctl not found")
            return False
        except Exception as e:
            logger.error(f"Failed to start bluetoothctl scanner: {e}")
            return False

    def stop(self) -> None:
        """Stop bluetoothctl scanning."""
        self._stop_event.set()

        if self._process:
            try:
                self._process.stdin.write("scan off\n")
                self._process.stdin.write("quit\n")
                self._process.stdin.flush()
                self._process.wait(timeout=2.0)
            except Exception:
                with contextlib.suppress(Exception):
                    self._process.terminate()
            self._process = None

        if self._reader_thread:
            self._reader_thread.join(timeout=2.0)

        self._is_scanning = False
        logger.info("bluetoothctl scanner stopped")

    @property
    def is_scanning(self) -> bool:
        return self._is_scanning

    def _read_output(self) -> None:
        """Read bluetoothctl output and parse devices."""
        try:
            while not self._stop_event.is_set() and self._process:
                line = self._process.stdout.readline()
                if not line:
                    break

                line = line.strip()

                # Parse device discovery lines
                # [NEW] Device AA:BB:CC:DD:EE:FF DeviceName
                # [CHG] Device AA:BB:CC:DD:EE:FF RSSI: -65
                # [CHG] Device AA:BB:CC:DD:EE:FF Name: DeviceName

                new_match = re.search(r"\[NEW\]\s+Device\s+([0-9A-Fa-f:]{17})\s*(.*)", line)
                if new_match:
                    address = new_match.group(1).upper()
                    name = new_match.group(2).strip() or None

                    self._devices[address] = {
                        "address": address,
                        "name": name,
                        "rssi": None,
                    }

                    observation = BTObservation(
                        timestamp=datetime.now(),
                        address=address,
                        address_type=ADDRESS_TYPE_PUBLIC,
                        name=name,
                    )

                    if self._on_observation:
                        self._on_observation(observation)
                    continue

                # RSSI change
                rssi_match = re.search(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+RSSI:\s*(-?\d+)", line)
                if rssi_match:
                    address = rssi_match.group(1).upper()
                    rssi = int(rssi_match.group(2))

                    device_data = self._devices.get(address, {"address": address})
                    device_data["rssi"] = rssi
                    self._devices[address] = device_data

                    observation = BTObservation(
                        timestamp=datetime.now(),
                        address=address,
                        address_type=ADDRESS_TYPE_PUBLIC,
                        name=device_data.get("name"),
                        rssi=rssi,
                    )

                    if self._on_observation:
                        self._on_observation(observation)
                    continue

                # Name change
                name_match = re.search(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+Name:\s*(.+)", line)
                if name_match:
                    address = name_match.group(1).upper()
                    name = name_match.group(2).strip()

                    device_data = self._devices.get(address, {"address": address})
                    device_data["name"] = name
                    self._devices[address] = device_data

                    observation = BTObservation(
                        timestamp=datetime.now(),
                        address=address,
                        address_type=ADDRESS_TYPE_PUBLIC,
                        name=name,
                        rssi=device_data.get("rssi"),
                    )

                    if self._on_observation:
                        self._on_observation(observation)

        except Exception as e:
            logger.error(f"bluetoothctl read error: {e}")
        finally:
            self._is_scanning = False


class FallbackScanner:
    """
    Unified fallback scanner that selects the best available backend.
    """

    def __init__(
        self,
        adapter: str = "hci0",
        on_observation: Callable[[BTObservation], None] | None = None,
    ):
        self._adapter = adapter
        self._on_observation = on_observation
        self._active_scanner: object | None = None
        self._backend: str | None = None

    def start(self) -> bool:
        """Start scanning with best available backend."""
        # Try bleak first (cross-platform)
        try:
            import bleak

            self._active_scanner = BleakScanner(on_observation=self._on_observation)
            if self._active_scanner.start():
                self._backend = "bleak"
                return True
        except ImportError:
            pass

        # Try hcitool (requires root)
        try:
            self._active_scanner = HcitoolScanner(adapter=self._adapter, on_observation=self._on_observation)
            if self._active_scanner.start():
                self._backend = "hcitool"
                return True
        except Exception:
            pass

        # Try bluetoothctl
        try:
            self._active_scanner = BluetoothctlScanner(on_observation=self._on_observation)
            if self._active_scanner.start():
                self._backend = "bluetoothctl"
                return True
        except Exception:
            pass

        # Try ubertooth (raw packet capture with Ubertooth One hardware)
        try:
            from .ubertooth_scanner import UbertoothScanner

            self._active_scanner = UbertoothScanner(on_observation=self._on_observation)
            if self._active_scanner.start():
                self._backend = "ubertooth"
                return True
        except Exception:
            pass

        logger.error("No fallback scanner available")
        return False

    def stop(self) -> None:
        """Stop active scanner."""
        if self._active_scanner:
            self._active_scanner.stop()
            self._active_scanner = None
            self._backend = None

    @property
    def is_scanning(self) -> bool:
        return self._active_scanner.is_scanning if self._active_scanner else False

    @property
    def backend(self) -> str | None:
        return self._backend
