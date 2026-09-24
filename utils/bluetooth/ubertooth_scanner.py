"""
Ubertooth One BLE scanner backend.

Uses ubertooth-btle for passive BLE packet capture across all 40 channels.
Provides enhanced sniffing capabilities compared to standard Bluetooth adapters.
"""

from __future__ import annotations

import contextlib
import logging
import re
import shutil
import subprocess
import threading
from datetime import datetime
from typing import Callable

from .constants import (
    ADDRESS_TYPE_PUBLIC,
    ADDRESS_TYPE_RANDOM,
)
from .models import BTObservation

logger = logging.getLogger(__name__)

# Ubertooth-specific timeout for subprocess operations
UBERTOOTH_STARTUP_TIMEOUT = 5.0


class UbertoothScanner:
    """
    BLE scanner using Ubertooth One hardware via ubertooth-btle.

    Captures raw BLE advertisements passively across all 40 BLE channels.
    Provides richer data than standard adapters including raw advertising payloads.
    """

    def __init__(
        self,
        device_index: int = 0,
        on_observation: Callable[[BTObservation], None] | None = None,
    ):
        """
        Initialize Ubertooth scanner.

        Args:
            device_index: Ubertooth device index (for systems with multiple Ubertooths)
            on_observation: Callback for each BLE observation
        """
        self._device_index = device_index
        self._on_observation = on_observation
        self._process: subprocess.Popen | None = None
        self._is_scanning = False
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @staticmethod
    def is_available() -> bool:
        """Check if ubertooth-btle is available on the system."""
        return shutil.which("ubertooth-btle") is not None

    def start(self) -> bool:
        """
        Start Ubertooth BLE scanning.

        Spawns ubertooth-btle in advertisement-only mode (-n flag).

        Returns:
            True if scanning started successfully, False otherwise.
        """
        if not self.is_available():
            logger.error("ubertooth-btle not found in PATH")
            return False

        if self._is_scanning:
            logger.warning("Ubertooth scanner already running")
            return True

        try:
            # Build command: ubertooth-btle -n -U <device_index>
            # -n = advertisements only (no follow mode)
            # -U = device index for multiple Ubertooths
            cmd = ["ubertooth-btle", "-n"]
            if self._device_index > 0:
                cmd.extend(["-U", str(self._device_index)])

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                bufsize=1,  # Line buffered
            )

            self._stop_event.clear()
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True, name="ubertooth-reader")
            self._reader_thread.start()
            self._is_scanning = True
            logger.info(f"Ubertooth scanner started (device index: {self._device_index})")
            return True

        except FileNotFoundError:
            logger.error("ubertooth-btle not found")
            return False
        except PermissionError:
            logger.error("ubertooth-btle requires appropriate permissions (try running as root)")
            return False
        except Exception as e:
            logger.error(f"Failed to start Ubertooth scanner: {e}")
            return False

    def stop(self) -> None:
        """Stop Ubertooth scanning and clean up resources."""
        self._stop_event.set()

        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                logger.warning("Ubertooth process did not terminate, killing")
                self._process.kill()
                self._process.wait(timeout=1.0)
            except Exception as e:
                logger.error(f"Error stopping Ubertooth process: {e}")
            finally:
                self._process = None

        if self._reader_thread:
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None

        self._is_scanning = False
        logger.info("Ubertooth scanner stopped")

    @property
    def is_scanning(self) -> bool:
        """Return whether the scanner is currently active."""
        return self._is_scanning

    def _read_output(self) -> None:
        """
        Background thread to read and parse ubertooth-btle output.

        Output format example:
        systime=1349412883 freq=2402 addr=8e89bed6 delta_t=38.441 ms 00 17 ab cd ef 01 22 ...
        """
        try:
            while not self._stop_event.is_set() and self._process:
                line = self._process.stdout.readline()
                if not line:
                    # Process ended
                    break

                line = line.strip()
                if not line:
                    continue

                # Skip non-packet lines (errors, status messages)
                if not line.startswith("systime="):
                    # Log errors from stderr would go here if needed
                    continue

                try:
                    observation = self._parse_advertisement(line)
                    if observation and self._on_observation:
                        self._on_observation(observation)
                except Exception as e:
                    logger.debug(f"Error parsing Ubertooth output: {e}")

        except Exception as e:
            logger.error(f"Ubertooth reader thread error: {e}")
        finally:
            self._is_scanning = False

    def _parse_advertisement(self, line: str) -> BTObservation | None:
        """
        Parse a single ubertooth-btle output line into a BTObservation.

        Format: systime=<epoch> freq=<mhz> addr=<access_addr> delta_t=<ms> ms <hex bytes...>

        The hex bytes contain the BLE PDU:
        - Byte 0: PDU type and header flags
        - Byte 1: Length
        - Bytes 2-7: Advertiser MAC address (reversed byte order)
        - Remaining: Advertising data payload

        Args:
            line: Raw output line from ubertooth-btle

        Returns:
            BTObservation if successfully parsed, None otherwise.
        """
        # Parse the structured prefix
        # Example: systime=1349412883 freq=2402 addr=8e89bed6 delta_t=38.441 ms 00 17 ab cd ef ...
        match = re.match(r"systime=(\d+)\s+freq=(\d+)\s+addr=([0-9a-fA-F]+)\s+delta_t=[\d.]+\s+ms\s+(.+)", line)
        if not match:
            return None

        # Parse hex bytes
        hex_data = match.group(4).strip()
        try:
            raw_bytes = bytes.fromhex(hex_data.replace(" ", ""))
        except ValueError:
            return None

        if len(raw_bytes) < 8:
            # Need at least PDU header + MAC address
            return None

        # Parse PDU header
        pdu_type = raw_bytes[0] & 0x0F
        # tx_add = (raw_bytes[0] >> 6) & 0x01  # TxAdd: 1 = random address
        length = raw_bytes[1]

        # Validate length
        if len(raw_bytes) < 2 + length:
            return None

        # Extract advertiser address (bytes 2-7, reversed)
        # BLE addresses are transmitted LSB first
        addr_bytes = raw_bytes[2:8]
        address = ":".join(f"{b:02X}" for b in reversed(addr_bytes))

        # Determine address type from PDU type and TxAdd flag
        tx_add = (raw_bytes[0] >> 6) & 0x01
        address_type = ADDRESS_TYPE_RANDOM if tx_add else ADDRESS_TYPE_PUBLIC

        # Parse advertising data payload (after MAC address)
        adv_data = raw_bytes[8 : 2 + length] if length > 6 else b""

        # Parse advertising data structures
        name = None
        manufacturer_id = None
        manufacturer_data = None
        service_uuids = []
        service_data = {}
        tx_power = None

        # Parse AD structures: each is [length][type][data...]
        i = 0
        while i < len(adv_data):
            if i >= len(adv_data):
                break
            ad_len = adv_data[i]
            if ad_len == 0 or i + 1 + ad_len > len(adv_data):
                break

            ad_type = adv_data[i + 1]
            ad_payload = adv_data[i + 2 : i + 1 + ad_len]

            # 0x01 = Flags
            # 0x02/0x03 = Incomplete/Complete list of 16-bit UUIDs
            if ad_type in (0x02, 0x03) and len(ad_payload) >= 2:
                for j in range(0, len(ad_payload), 2):
                    if j + 2 <= len(ad_payload):
                        uuid16 = int.from_bytes(ad_payload[j : j + 2], "little")
                        service_uuids.append(f"{uuid16:04X}")

            # 0x06/0x07 = Incomplete/Complete list of 128-bit UUIDs
            elif ad_type in (0x06, 0x07) and len(ad_payload) >= 16:
                for j in range(0, len(ad_payload), 16):
                    if j + 16 <= len(ad_payload):
                        uuid_bytes = ad_payload[j : j + 16]
                        uuid128 = "-".join(
                            [
                                uuid_bytes[15:11:-1].hex(),
                                uuid_bytes[11:9:-1].hex(),
                                uuid_bytes[9:7:-1].hex(),
                                uuid_bytes[7:5:-1].hex(),
                                uuid_bytes[5::-1].hex(),
                            ]
                        )
                        service_uuids.append(uuid128.upper())

            # 0x08/0x09 = Shortened/Complete Local Name
            elif ad_type in (0x08, 0x09):
                with contextlib.suppress(Exception):
                    name = ad_payload.decode("utf-8", errors="replace")

            # 0x0A = TX Power Level
            elif ad_type == 0x0A and len(ad_payload) >= 1:
                # Signed 8-bit value
                tx_power = ad_payload[0] if ad_payload[0] < 128 else ad_payload[0] - 256

            # 0xFF = Manufacturer Specific Data
            elif ad_type == 0xFF and len(ad_payload) >= 2:
                manufacturer_id = int.from_bytes(ad_payload[0:2], "little")
                manufacturer_data = bytes(ad_payload[2:])

            # 0x16 = Service Data (16-bit UUID)
            elif ad_type == 0x16 and len(ad_payload) >= 2:
                svc_uuid = f"{int.from_bytes(ad_payload[0:2], 'little'):04X}"
                service_data[svc_uuid] = bytes(ad_payload[2:])

            # 0x20 = Service Data (32-bit UUID)
            elif ad_type == 0x20 and len(ad_payload) >= 4:
                svc_uuid = f"{int.from_bytes(ad_payload[0:4], 'little'):08X}"
                service_data[svc_uuid] = bytes(ad_payload[4:])

            # 0x21 = Service Data (128-bit UUID)
            elif ad_type == 0x21 and len(ad_payload) >= 16:
                uuid_bytes = ad_payload[0:16]
                svc_uuid = "-".join(
                    [
                        uuid_bytes[15:11:-1].hex(),
                        uuid_bytes[11:9:-1].hex(),
                        uuid_bytes[9:7:-1].hex(),
                        uuid_bytes[7:5:-1].hex(),
                        uuid_bytes[5::-1].hex(),
                    ]
                ).upper()
                service_data[svc_uuid] = bytes(ad_payload[16:])

            i += 1 + ad_len

        # Determine if connectable from PDU type
        # ADV_IND (0x00) and ADV_DIRECT_IND (0x01) are connectable
        is_connectable = pdu_type in (0x00, 0x01)

        return BTObservation(
            timestamp=datetime.now(),
            address=address,
            address_type=address_type,
            rssi=None,  # Ubertooth doesn't provide RSSI in standard mode
            tx_power=tx_power,
            name=name,
            manufacturer_id=manufacturer_id,
            manufacturer_data=manufacturer_data,
            service_uuids=service_uuids,
            service_data=service_data,
            is_connectable=is_connectable,
        )
