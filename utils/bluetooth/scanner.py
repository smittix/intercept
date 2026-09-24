"""
Main Bluetooth scanner coordinator.

Coordinates DBus and fallback scanners, manages device aggregation and heuristics.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Generator
from datetime import datetime
from typing import Callable

from .aggregator import DeviceAggregator
from .capability_check import check_capabilities
from .constants import (
    DEVICE_STALE_TIMEOUT,
)
from .dbus_scanner import DBusScanner
from .fallback_scanner import FallbackScanner
from .heuristics import HeuristicsEngine
from .irk_extractor import get_paired_irks
from .models import BTDeviceAggregate, BTObservation, ScanStatus, SystemCapabilities
from .ubertooth_scanner import UbertoothScanner

logger = logging.getLogger(__name__)

# Global scanner instance
_scanner_instance: BluetoothScanner | None = None
_scanner_lock = threading.Lock()


class BluetoothScanner:
    """
    Main Bluetooth scanner coordinating DBus and fallback scanners.

    Provides unified API for scanning, device aggregation, and heuristics.
    """

    def __init__(self, adapter_id: str | None = None):
        """
        Initialize Bluetooth scanner.

        Args:
            adapter_id: Adapter path/name (e.g., '/org/bluez/hci0' or 'hci0').
        """
        self._adapter_id = adapter_id
        self._aggregator = DeviceAggregator()
        self._heuristics = HeuristicsEngine()
        self._status = ScanStatus()
        self._lock = threading.Lock()

        # Scanner backends
        self._dbus_scanner: DBusScanner | None = None
        self._fallback_scanner: FallbackScanner | None = None
        self._ubertooth_scanner: UbertoothScanner | None = None
        self._active_backend: str | None = None

        # Event queue for SSE streaming
        self._event_queue: queue.Queue = queue.Queue(maxsize=1000)

        # Duration-based scanning
        self._scan_timer: threading.Timer | None = None

        # Callbacks
        self._on_device_updated_callbacks: list[Callable[[BTDeviceAggregate], None]] = []

        # Capability check result
        self._capabilities: SystemCapabilities | None = None

    def start_scan(
        self,
        mode: str = "auto",
        duration_s: int | None = None,
        transport: str = "auto",
        rssi_threshold: int = -100,
    ) -> bool:
        """
        Start Bluetooth scanning.

        Args:
            mode: Scanner mode ('dbus', 'bleak', 'hcitool', 'bluetoothctl', 'auto').
            duration_s: Scan duration in seconds (None for indefinite).
            transport: BLE transport filter ('bredr', 'le', 'auto').
            rssi_threshold: Minimum RSSI for device discovery.

        Returns:
            True if scan started successfully.
        """
        with self._lock:
            if self._status.is_scanning:
                return True

            # Check capabilities
            self._capabilities = check_capabilities()

            # Determine adapter
            adapter = self._adapter_id or self._capabilities.default_adapter
            if not adapter and mode == "dbus":
                self._status.error = "No Bluetooth adapter found"
                return False

            # Select and start backend
            started = False
            backend_used = None
            original_mode = mode

            if mode == "auto":
                mode = self._capabilities.recommended_backend or "bleak"

            if mode == "dbus":
                started, backend_used = self._start_dbus(adapter, transport, rssi_threshold)
            elif mode == "ubertooth":
                started, backend_used = self._start_ubertooth()

            # Fallback: try non-DBus methods if DBus failed or wasn't requested
            if not started and (original_mode == "auto" or mode in ("bleak", "hcitool", "bluetoothctl")):
                started, backend_used = self._start_fallback(adapter, original_mode)

            if not started:
                self._status.error = f"Failed to start scanner with mode '{mode}'"
                return False

            # Update status
            self._active_backend = backend_used
            self._status = ScanStatus(
                is_scanning=True,
                mode=mode,
                backend=backend_used,
                adapter_id=adapter,
                started_at=datetime.now(),
                duration_s=duration_s,
            )

            # Queue status event
            self._queue_event(
                {
                    "type": "status",
                    "status": "started",
                    "backend": backend_used,
                    "mode": mode,
                }
            )

            # Set up timer for duration-based scanning
            if duration_s:
                self._scan_timer = threading.Timer(duration_s, self.stop_scan)
                self._scan_timer.daemon = True
                self._scan_timer.start()

            logger.info(f"Bluetooth scan started: mode={mode}, backend={backend_used}")
            return True

    def _start_dbus(self, adapter: str, transport: str, rssi_threshold: int) -> tuple[bool, str | None]:
        """Start DBus scanner."""
        try:
            self._dbus_scanner = DBusScanner(
                adapter_path=adapter,
                on_observation=self._handle_observation,
            )
            if self._dbus_scanner.start(transport=transport, rssi_threshold=rssi_threshold):
                return True, "dbus"
        except Exception as e:
            logger.warning(f"DBus scanner failed: {e}")
        return False, None

    def _start_ubertooth(self) -> tuple[bool, str | None]:
        """Start Ubertooth One scanner."""
        try:
            self._ubertooth_scanner = UbertoothScanner(
                on_observation=self._handle_observation,
            )
            if self._ubertooth_scanner.start():
                return True, "ubertooth"
        except Exception as e:
            logger.warning(f"Ubertooth scanner failed: {e}")
        return False, None

    def _start_fallback(self, adapter: str, preferred: str) -> tuple[bool, str | None]:
        """Start fallback scanner."""
        try:
            # Extract adapter name from path if needed
            adapter_name = adapter.split("/")[-1] if adapter else "hci0"

            self._fallback_scanner = FallbackScanner(
                adapter=adapter_name,
                on_observation=self._handle_observation,
            )
            if self._fallback_scanner.start():
                return True, self._fallback_scanner.backend
        except Exception as e:
            logger.warning(f"Fallback scanner failed: {e}")
        return False, None

    def stop_scan(self) -> None:
        """Stop Bluetooth scanning."""
        with self._lock:
            if not self._status.is_scanning:
                return

            # Cancel timer if running
            if self._scan_timer:
                self._scan_timer.cancel()
                self._scan_timer = None

            # Stop active scanner
            if self._dbus_scanner:
                self._dbus_scanner.stop()
                self._dbus_scanner = None

            if self._fallback_scanner:
                self._fallback_scanner.stop()
                self._fallback_scanner = None

            if self._ubertooth_scanner:
                self._ubertooth_scanner.stop()
                self._ubertooth_scanner = None

            # Update status
            self._status.is_scanning = False
            self._active_backend = None

            # Queue status event
            self._queue_event(
                {
                    "type": "status",
                    "status": "stopped",
                }
            )

            logger.info("Bluetooth scan stopped")

    def _match_irk(self, device: BTDeviceAggregate) -> None:
        """Check if a device address resolves against any paired IRK."""
        if device.irk_hex is not None:
            return  # Already matched

        address = device.address
        if not address or len(address.replace(":", "").replace("-", "")) not in (12, 32):
            return

        # Only attempt RPA resolution on 6-byte addresses
        addr_clean = address.replace(":", "").replace("-", "")
        if len(addr_clean) != 12:
            return

        try:
            paired = get_paired_irks()
        except Exception:
            return

        if not paired:
            return

        try:
            from utils.bt_locate import resolve_rpa
        except ImportError:
            return

        for entry in paired:
            irk_hex = entry.get("irk_hex", "")
            if not irk_hex or len(irk_hex) != 32:
                continue
            try:
                irk = bytes.fromhex(irk_hex)
                if resolve_rpa(irk, address):
                    device.irk_hex = irk_hex
                    device.irk_source_name = entry.get("name")
                    logger.debug(f"IRK match for {address}: {entry.get('name', 'unnamed')}")
                    return
            except Exception:
                continue

    def _handle_observation(self, observation: BTObservation) -> None:
        """Handle incoming observation from scanner backend."""
        try:
            # Ingest into aggregator
            device = self._aggregator.ingest(observation)

            # Evaluate heuristics
            self._heuristics.evaluate(device)

            # Check for IRK match
            self._match_irk(device)

            # Update device count
            with self._lock:
                self._status.devices_found = self._aggregator.device_count

            # Build summary with MAC cluster count
            summary = device.to_summary_dict()
            if device.payload_fingerprint_id:
                summary["mac_cluster_count"] = self._aggregator.get_fingerprint_mac_count(device.payload_fingerprint_id)
            else:
                summary["mac_cluster_count"] = 0

            # Queue event
            self._queue_event(
                {
                    "type": "device",
                    "action": "update",
                    "device": summary,
                }
            )

            # Callbacks
            for cb in self._on_device_updated_callbacks:
                try:
                    cb(device)
                except Exception as cb_err:
                    logger.error(f"Device callback error: {cb_err}")

        except Exception as e:
            logger.error(f"Error handling observation: {e}")

    def _queue_event(self, event: dict) -> None:
        """Add event to queue for SSE streaming, and send it through the
        event pipeline once, whether or not a browser is subscribed."""
        from utils.event_pipeline import submit

        event_name, event_data = sse_event(event)
        submit("bluetooth", event_data, event_name)
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            # Drop oldest event
            try:
                self._event_queue.get_nowait()
                self._event_queue.put_nowait(event)
            except queue.Empty:
                pass

    def get_status(self) -> ScanStatus:
        """Get current scan status."""
        with self._lock:
            self._status.devices_found = self._aggregator.device_count
            return self._status

    def get_devices(
        self,
        sort_by: str = "last_seen",
        sort_desc: bool = True,
        min_rssi: int | None = None,
        protocol: str | None = None,
        max_age_seconds: float = DEVICE_STALE_TIMEOUT,
    ) -> list[BTDeviceAggregate]:
        """
        Get list of discovered devices with optional filtering.

        Args:
            sort_by: Field to sort by ('last_seen', 'rssi_current', 'name', 'seen_count').
            sort_desc: Sort descending if True.
            min_rssi: Minimum RSSI filter.
            protocol: Protocol filter ('ble', 'classic', None for all).
            max_age_seconds: Maximum age for devices.

        Returns:
            List of BTDeviceAggregate instances.
        """
        devices = self._aggregator.get_active_devices(max_age_seconds)

        # Filter by RSSI
        if min_rssi is not None:
            devices = [d for d in devices if d.rssi_current and d.rssi_current >= min_rssi]

        # Filter by protocol
        if protocol:
            devices = [d for d in devices if d.protocol == protocol]

        # Sort
        sort_key = {
            "last_seen": lambda d: d.last_seen,
            "rssi_current": lambda d: d.rssi_current or -999,
            "name": lambda d: (d.name or "").lower(),
            "seen_count": lambda d: d.seen_count,
            "first_seen": lambda d: d.first_seen,
        }.get(sort_by, lambda d: d.last_seen)

        devices.sort(key=sort_key, reverse=sort_desc)

        return devices

    def get_device(self, device_id: str) -> BTDeviceAggregate | None:
        """Get a specific device by ID."""
        return self._aggregator.get_device(device_id)

    def get_snapshot(self) -> list[dict]:
        """Get current device snapshot for TSCM integration."""
        devices = self.get_devices()
        return [d.to_dict() for d in devices]

    def stream_events(self, timeout: float = 1.0) -> Generator[dict, None, None]:
        """
        Generator for SSE event streaming.

        Args:
            timeout: Queue get timeout in seconds.

        Yields:
            Event dictionaries.
        """
        while True:
            try:
                event = self._event_queue.get(timeout=timeout)
                yield event
            except queue.Empty:
                yield {"type": "ping"}

    def set_baseline(self) -> int:
        """Set current devices as baseline."""
        count = self._aggregator.set_baseline()
        self._queue_event(
            {
                "type": "baseline",
                "action": "set",
                "device_count": count,
            }
        )
        return count

    def clear_baseline(self) -> None:
        """Clear the baseline."""
        self._aggregator.clear_baseline()
        self._queue_event(
            {
                "type": "baseline",
                "action": "cleared",
            }
        )

    def clear_devices(self) -> None:
        """Clear all tracked devices."""
        self._aggregator.clear()
        self._queue_event(
            {
                "type": "devices",
                "action": "cleared",
            }
        )

    def prune_stale(self, max_age_seconds: float = DEVICE_STALE_TIMEOUT) -> int:
        """Prune stale devices."""
        return self._aggregator.prune_stale_devices(max_age_seconds)

    def get_capabilities(self) -> SystemCapabilities:
        """Get system capabilities."""
        if not self._capabilities:
            self._capabilities = check_capabilities()
        return self._capabilities

    def set_on_device_updated(self, callback: Callable[[BTDeviceAggregate], None]) -> None:
        """Set callback for device updates (legacy, adds to callback list)."""
        self.add_device_callback(callback)

    def add_device_callback(self, callback: Callable[[BTDeviceAggregate], None]) -> None:
        """Add a callback for device updates."""
        if callback not in self._on_device_updated_callbacks:
            self._on_device_updated_callbacks.append(callback)

    def remove_device_callback(self, callback: Callable[[BTDeviceAggregate], None]) -> None:
        """Remove a device update callback."""
        if callback in self._on_device_updated_callbacks:
            self._on_device_updated_callbacks.remove(callback)

    @property
    def is_scanning(self) -> bool:
        """Check if scanning is active.

        Cross-checks the backend scanner state, since bleak scans can
        expire silently without calling stop_scan().
        """
        if not self._status.is_scanning:
            return False

        # Detect backends that finished on their own (e.g. bleak timeout)
        backend_alive = (
            (self._dbus_scanner and self._dbus_scanner.is_scanning)
            or (self._fallback_scanner and self._fallback_scanner.is_scanning)
            or (self._ubertooth_scanner and self._ubertooth_scanner.is_scanning)
        )
        if not backend_alive:
            self._status.is_scanning = False
            return False

        return True

    @property
    def device_count(self) -> int:
        """Number of tracked devices."""
        return self._aggregator.device_count

    @property
    def has_baseline(self) -> bool:
        """Whether baseline is set."""
        return self._aggregator.has_baseline


def sse_event(event: dict) -> tuple[str, dict]:
    """A scanner event as the SSE stream names and shapes it: (event name, data)."""
    event_type = event.get("type", "unknown")
    if event_type == "device":
        return "device_update", event.get("device", event)
    elif event_type == "status":
        status = event.get("status", "")
        if status == "started":
            return "scan_started", event
        elif status == "stopped":
            return "scan_stopped", event
        return "status", event
    elif event_type == "error":
        return "error", event
    elif event_type == "baseline":
        return "baseline", event
    elif event_type == "ping":
        return "ping", {}
    else:
        return event_type, event


def get_bluetooth_scanner(adapter_id: str | None = None) -> BluetoothScanner:
    """
    Get or create the global Bluetooth scanner instance.

    Args:
        adapter_id: Adapter path/name (only used on first call).

    Returns:
        BluetoothScanner instance.
    """
    global _scanner_instance

    with _scanner_lock:
        if _scanner_instance is None:
            _scanner_instance = BluetoothScanner(adapter_id)
        return _scanner_instance


def reset_bluetooth_scanner() -> None:
    """Reset the global scanner instance (for testing)."""
    global _scanner_instance

    with _scanner_lock:
        if _scanner_instance:
            _scanner_instance.stop_scan()
        _scanner_instance = None
