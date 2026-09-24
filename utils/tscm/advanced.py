"""
TSCM Advanced Features Module

Implements:
1. Capability & Coverage Reality Panel
2. Baseline Diff & Baseline Health
3. Per-Device Timelines
4. Meeting-Window Summary Enhancements
5. WiFi Advanced Indicators (Evil Twin, Probes, Deauth)
6. Bluetooth Risk Explainability & Proximity Heuristics
7. Operator Playbooks

DISCLAIMER: This system performs wireless and RF surveillance screening.
Findings indicate anomalies and indicators, not confirmed surveillance devices.
All claims are probabilistic pattern matches requiring professional verification.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger("intercept.tscm.advanced")


# =============================================================================
# 1. Capability & Coverage Reality Panel
# =============================================================================


class WifiMode(Enum):
    """WiFi adapter operating modes."""

    MONITOR = "monitor"
    MANAGED = "managed"
    UNAVAILABLE = "unavailable"


class BluetoothMode(Enum):
    """Bluetooth adapter capabilities."""

    BLE_CLASSIC = "ble_classic"
    BLE_ONLY = "ble_only"
    LIMITED = "limited"
    UNAVAILABLE = "unavailable"


@dataclass
class RFCapability:
    """RF/SDR device capabilities."""

    device_type: str = "none"
    driver: str = ""
    min_frequency_mhz: float = 0.0
    max_frequency_mhz: float = 0.0
    sample_rate_max: int = 0
    available: bool = False
    limitations: list[str] = field(default_factory=list)


@dataclass
class SweepCapabilities:
    """
    Complete capabilities snapshot for a TSCM sweep.

    Exposes what the current sweep CAN and CANNOT detect based on
    OS, privileges, adapters, and SDR hardware limits.
    """

    # System info
    os_name: str = ""
    os_version: str = ""
    is_root: bool = False

    # WiFi capabilities
    wifi_mode: WifiMode = WifiMode.UNAVAILABLE
    wifi_interface: str = ""
    wifi_driver: str = ""
    wifi_monitor_capable: bool = False
    wifi_limitations: list[str] = field(default_factory=list)

    # Bluetooth capabilities
    bt_mode: BluetoothMode = BluetoothMode.UNAVAILABLE
    bt_adapter: str = ""
    bt_version: str = ""
    bt_limitations: list[str] = field(default_factory=list)

    # RF/SDR capabilities
    rf_capability: RFCapability = field(default_factory=RFCapability)

    # Overall limitations
    all_limitations: list[str] = field(default_factory=list)

    # Timestamp
    captured_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "system": {
                "os": self.os_name,
                "os_version": self.os_version,
                "is_root": self.is_root,
            },
            "wifi": {
                "mode": self.wifi_mode.value,
                "interface": self.wifi_interface,
                "driver": self.wifi_driver,
                "monitor_capable": self.wifi_monitor_capable,
                "limitations": self.wifi_limitations,
            },
            "bluetooth": {
                "mode": self.bt_mode.value,
                "adapter": self.bt_adapter,
                "version": self.bt_version,
                "limitations": self.bt_limitations,
            },
            "rf": {
                "device_type": self.rf_capability.device_type,
                "driver": self.rf_capability.driver,
                "frequency_range_mhz": {
                    "min": self.rf_capability.min_frequency_mhz,
                    "max": self.rf_capability.max_frequency_mhz,
                },
                "sample_rate_max": self.rf_capability.sample_rate_max,
                "available": self.rf_capability.available,
                "limitations": self.rf_capability.limitations,
            },
            "all_limitations": self.all_limitations,
            "captured_at": self.captured_at.isoformat(),
            "disclaimer": (
                "Capabilities are detected at sweep start time and may change. "
                "Limitations listed affect what this sweep can reliably detect."
            ),
        }


def detect_sweep_capabilities(
    wifi_interface: str = "", bt_adapter: str = "", sdr_device: Any = None
) -> SweepCapabilities:
    """
    Detect current system capabilities for TSCM sweeping.

    Args:
        wifi_interface: Specific WiFi interface to check
        bt_adapter: Specific BT adapter to check
        sdr_device: SDR device object if available

    Returns:
        SweepCapabilities object with complete capability assessment
    """
    caps = SweepCapabilities()

    # System info
    caps.os_name = platform.system()
    caps.os_version = platform.release()
    caps.is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False

    # Detect WiFi capabilities
    _detect_wifi_capabilities(caps, wifi_interface)

    # Detect Bluetooth capabilities
    _detect_bluetooth_capabilities(caps, bt_adapter)

    # Detect RF/SDR capabilities
    _detect_rf_capabilities(caps, sdr_device)

    # Compile all limitations
    caps.all_limitations = caps.wifi_limitations + caps.bt_limitations + caps.rf_capability.limitations

    # Add privilege-based limitations
    if not caps.is_root:
        caps.all_limitations.append("Running without root privileges - some features may be limited")

    return caps


def _detect_wifi_capabilities(caps: SweepCapabilities, interface: str) -> None:
    """Detect WiFi adapter capabilities."""
    caps.wifi_interface = interface

    if platform.system() == "Darwin":
        # macOS: Check for WiFi capability using multiple methods
        wifi_available = False

        # Method 1: Check airport utility (older macOS)
        airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
        if os.path.exists(airport_path):
            wifi_available = True

        # Method 2: Check for WiFi interface using networksetup (works on all macOS)
        if not wifi_available:
            try:
                result = subprocess.run(
                    ["networksetup", "-listallhardwareports"], capture_output=True, text=True, timeout=5
                )
                if "Wi-Fi" in result.stdout or "AirPort" in result.stdout:
                    wifi_available = True
            except Exception:
                pass

        # Method 3: Check if en0 exists (common WiFi interface on macOS)
        if not wifi_available:
            try:
                result = subprocess.run(["ifconfig", "en0"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    wifi_available = True
            except Exception:
                pass

        if wifi_available:
            caps.wifi_mode = WifiMode.MANAGED
            caps.wifi_driver = "apple80211"
            caps.wifi_monitor_capable = False
            caps.wifi_limitations = [
                "macOS WiFi operates in managed mode only.",
                "Cannot capture probe requests or deauthentication frames.",
                "Evil twin detection limited to SSID/BSSID comparison only.",
            ]
        else:
            caps.wifi_mode = WifiMode.UNAVAILABLE
            caps.wifi_limitations = ["WiFi scanning unavailable - no interface found"]

    else:
        # Linux: Check for monitor mode capability
        try:
            # Check if interface supports monitor mode
            result = subprocess.run(["iw", "list"], capture_output=True, text=True, timeout=5)

            if "monitor" in result.stdout.lower():
                # Check current mode
                if interface:
                    mode_result = subprocess.run(
                        ["iw", "dev", interface, "info"], capture_output=True, text=True, timeout=5
                    )
                    if "type monitor" in mode_result.stdout.lower():
                        caps.wifi_mode = WifiMode.MONITOR
                        caps.wifi_monitor_capable = True
                    else:
                        caps.wifi_mode = WifiMode.MANAGED
                        caps.wifi_monitor_capable = True
                        caps.wifi_limitations.append(
                            "WiFi interface in managed mode. Probe requests and deauth detection require monitor mode."
                        )
                else:
                    caps.wifi_mode = WifiMode.MANAGED
                    caps.wifi_monitor_capable = True
            else:
                caps.wifi_mode = WifiMode.MANAGED
                caps.wifi_monitor_capable = False
                caps.wifi_limitations = [
                    "Passive WiFi frame analysis is not available in this sweep.",
                    "WiFi adapter does not support monitor mode.",
                    "Probe request and deauthentication detection unavailable.",
                ]

            # Get driver info
            if interface:
                try:
                    driver_path = f"/sys/class/net/{interface}/device/driver"
                    if os.path.exists(driver_path):
                        caps.wifi_driver = os.path.basename(os.readlink(driver_path))
                except Exception:
                    pass

        except (subprocess.TimeoutExpired, FileNotFoundError):
            caps.wifi_mode = WifiMode.UNAVAILABLE
            caps.wifi_limitations = ["WiFi scanning tools not available"]


def _detect_bluetooth_capabilities(caps: SweepCapabilities, adapter: str) -> None:
    """Detect Bluetooth adapter capabilities."""
    caps.bt_adapter = adapter

    if platform.system() == "Darwin":
        # macOS: Use system_profiler
        try:
            result = subprocess.run(
                ["system_profiler", "SPBluetoothDataType", "-json"], capture_output=True, text=True, timeout=10
            )
            if "Bluetooth" in result.stdout:
                caps.bt_mode = BluetoothMode.BLE_CLASSIC
                caps.bt_version = "macOS CoreBluetooth"
                caps.bt_limitations = [
                    "BLE scanning limited to advertising devices only.",
                    "Classic Bluetooth discovery may be incomplete.",
                    "Manufacturer data parsing depends on device advertising.",
                ]
            else:
                caps.bt_mode = BluetoothMode.UNAVAILABLE
                caps.bt_limitations = ["Bluetooth not available"]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            caps.bt_mode = BluetoothMode.UNAVAILABLE
            caps.bt_limitations = ["Bluetooth detection failed"]

    else:
        # Linux: Check bluetoothctl/hciconfig
        try:
            result = subprocess.run(["hciconfig", "-a"], capture_output=True, text=True, timeout=5)

            if "hci" in result.stdout.lower():
                # Check for BLE support
                if "le" in result.stdout.lower():
                    caps.bt_mode = BluetoothMode.BLE_CLASSIC
                    caps.bt_limitations = [
                        "BLE scanning range depends on adapter sensitivity.",
                        "Some devices may not be detected if not advertising.",
                    ]
                else:
                    caps.bt_mode = BluetoothMode.LIMITED
                    caps.bt_limitations = [
                        "Adapter may not support BLE scanning.",
                        "Limited to classic Bluetooth discovery.",
                    ]

                # Extract version
                for line in result.stdout.split("\n"):
                    if "hci version" in line.lower():
                        caps.bt_version = line.strip()
                        break
            else:
                caps.bt_mode = BluetoothMode.UNAVAILABLE
                caps.bt_limitations = ["No Bluetooth adapter found"]

        except (subprocess.TimeoutExpired, FileNotFoundError):
            caps.bt_mode = BluetoothMode.UNAVAILABLE
            caps.bt_limitations = ["Bluetooth tools not available"]


def _detect_rf_capabilities(caps: SweepCapabilities, sdr_device: Any) -> None:
    """Detect RF/SDR device capabilities."""
    rf_cap = RFCapability()

    try:
        from utils.sdr import SDRFactory

        devices = SDRFactory.detect_devices()

        if devices:
            device = devices[0]  # Use first device
            rf_cap.available = True
            rf_cap.device_type = getattr(device, "sdr_type", "unknown")
            if hasattr(rf_cap.device_type, "value"):
                rf_cap.device_type = rf_cap.device_type.value
            rf_cap.driver = getattr(device, "driver", "")

            # Set frequency ranges based on device type
            if "rtl" in rf_cap.device_type.lower():
                rf_cap.min_frequency_mhz = 24.0
                rf_cap.max_frequency_mhz = 1766.0
                rf_cap.sample_rate_max = 3200000
                rf_cap.limitations = [
                    "RTL-SDR frequency range: 24-1766 MHz typical.",
                    "Cannot reliably cover frequencies below 24 MHz.",
                    "Cannot cover microwave bands (>1.8 GHz) without upconverter.",
                    "Signal detection limited by SDR noise floor and dynamic range.",
                ]
            elif "hackrf" in rf_cap.device_type.lower():
                rf_cap.min_frequency_mhz = 1.0
                rf_cap.max_frequency_mhz = 6000.0
                rf_cap.sample_rate_max = 20000000
                rf_cap.limitations = [
                    "HackRF frequency range: 1 MHz - 6 GHz.",
                    "8-bit ADC limits dynamic range for weak signal detection.",
                ]
            else:
                rf_cap.limitations = [
                    f"Unknown SDR type: {rf_cap.device_type}",
                    "Frequency coverage and capabilities uncertain.",
                ]
        else:
            rf_cap.available = False
            rf_cap.device_type = "none"
            rf_cap.limitations = [
                "No SDR device detected.",
                "RF spectrum analysis is not available in this sweep.",
                "Cannot scan for wireless microphones, bugs, or RF transmitters.",
            ]

    except ImportError:
        rf_cap.available = False
        rf_cap.limitations = [
            "SDR support not installed.",
            "RF spectrum analysis unavailable.",
        ]
    except Exception as e:
        rf_cap.available = False
        rf_cap.limitations = [f"SDR detection failed: {str(e)}"]

    caps.rf_capability = rf_cap


# =============================================================================
# 2. Baseline Diff & Baseline Health
# =============================================================================


class BaselineHealth(Enum):
    """Baseline health status."""

    HEALTHY = "healthy"
    NOISY = "noisy"
    STALE = "stale"


@dataclass
class DeviceChange:
    """Represents a change detected compared to baseline."""

    identifier: str
    protocol: str
    change_type: str  # 'new', 'missing', 'rssi_drift', 'channel_change', 'security_change'
    description: str
    expected: bool = False  # True if this is an expected/normal change
    details: dict = field(default_factory=dict)


@dataclass
class BaselineDiff:
    """
    Complete diff between a baseline and a sweep.

    Shows what changed, whether baseline is reliable,
    and separates expected vs unexpected changes.
    """

    baseline_id: int
    sweep_id: int

    # Health assessment
    health: BaselineHealth = BaselineHealth.HEALTHY
    health_score: float = 1.0  # 0-1, higher is healthier
    health_reasons: list[str] = field(default_factory=list)

    # Age metrics
    baseline_age_hours: float = 0.0
    is_stale: bool = False

    # Device changes
    new_devices: list[DeviceChange] = field(default_factory=list)
    missing_devices: list[DeviceChange] = field(default_factory=list)
    changed_devices: list[DeviceChange] = field(default_factory=list)

    # Summary counts
    total_new: int = 0
    total_missing: int = 0
    total_changed: int = 0

    # Expected vs unexpected
    expected_changes: list[DeviceChange] = field(default_factory=list)
    unexpected_changes: list[DeviceChange] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "baseline_id": self.baseline_id,
            "sweep_id": self.sweep_id,
            "health": {
                "status": self.health.value,
                "score": round(self.health_score, 2),
                "reasons": self.health_reasons,
            },
            "age": {
                "hours": round(self.baseline_age_hours, 1),
                "is_stale": self.is_stale,
            },
            "summary": {
                "new_devices": self.total_new,
                "missing_devices": self.total_missing,
                "changed_devices": self.total_changed,
                "expected_changes": len(self.expected_changes),
                "unexpected_changes": len(self.unexpected_changes),
            },
            "new_devices": [
                {"identifier": d.identifier, "protocol": d.protocol, "description": d.description, "details": d.details}
                for d in self.new_devices
            ],
            "missing_devices": [
                {"identifier": d.identifier, "protocol": d.protocol, "description": d.description, "details": d.details}
                for d in self.missing_devices
            ],
            "changed_devices": [
                {
                    "identifier": d.identifier,
                    "protocol": d.protocol,
                    "change_type": d.change_type,
                    "description": d.description,
                    "expected": d.expected,
                    "details": d.details,
                }
                for d in self.changed_devices
            ],
            "disclaimer": (
                "Baseline comparison shows differences, not confirmed threats. "
                "New devices may be legitimate. Missing devices may have been powered off."
            ),
        }


def baseline_age_hours(created_at) -> float:
    """Hours since a baseline was recorded.

    A stored created_at is SQLite's CURRENT_TIMESTAMP: UTC with no offset.
    It has to be compared with the UTC clock; against local time a baseline
    recorded a moment ago reads as hours old outside UTC.
    """
    if isinstance(created_at, datetime):
        return (datetime.now() - created_at).total_seconds() / 3600
    if not isinstance(created_at, str) or not created_at:
        return 0.0
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() / 3600


def diff_sweep_against_baseline(baseline: dict, sweep: dict) -> BaselineDiff | None:
    """A completed sweep's results compared with a baseline, or None while the
    sweep has no results (diffing nothing would report every device missing)."""
    results = sweep.get("results")
    if results is None:
        return None
    if isinstance(results, str):
        results = json.loads(results)
    return calculate_baseline_diff(
        baseline=baseline,
        current_wifi=results.get("wifi_devices", []),
        current_wifi_clients=results.get("wifi_clients", []),
        current_bt=results.get("bt_devices", []),
        current_rf=results.get("rf_signals", []),
        sweep_id=sweep["id"],
    )


def calculate_baseline_diff(
    baseline: dict,
    current_wifi: list[dict],
    current_wifi_clients: list[dict],
    current_bt: list[dict],
    current_rf: list[dict],
    sweep_id: int,
) -> BaselineDiff:
    """
    Calculate comprehensive diff between baseline and current scan.

    Args:
        baseline: Baseline dict from database
        current_wifi: Current WiFi devices
        current_wifi_clients: Current WiFi clients
        current_bt: Current Bluetooth devices
        current_rf: Current RF signals
        sweep_id: Current sweep ID

    Returns:
        BaselineDiff with complete comparison results
    """
    diff = BaselineDiff(baseline_id=baseline.get("id", 0), sweep_id=sweep_id)

    # Calculate baseline age
    diff.baseline_age_hours = baseline_age_hours(baseline.get("created_at"))

    # Check if baseline is stale (>72 hours old)
    diff.is_stale = diff.baseline_age_hours > 72

    # Build baseline lookup dicts
    baseline_wifi = {
        d.get("bssid", d.get("mac", "")).upper(): d
        for d in baseline.get("wifi_networks", [])
        if d.get("bssid") or d.get("mac")
    }
    baseline_wifi_clients = {
        d.get("mac", d.get("address", "")).upper(): d
        for d in baseline.get("wifi_clients", [])
        if d.get("mac") or d.get("address")
    }
    baseline_bt = {
        d.get("mac", d.get("address", "")).upper(): d
        for d in baseline.get("bt_devices", [])
        if d.get("mac") or d.get("address")
    }
    baseline_rf = {round(d.get("frequency", 0), 1): d for d in baseline.get("rf_frequencies", []) if d.get("frequency")}

    # Compare WiFi
    _compare_wifi(diff, baseline_wifi, current_wifi)

    # Compare WiFi clients
    _compare_wifi_clients(diff, baseline_wifi_clients, current_wifi_clients)

    # Compare Bluetooth
    _compare_bluetooth(diff, baseline_bt, current_bt)

    # Compare RF
    _compare_rf(diff, baseline_rf, current_rf)

    # Calculate totals
    diff.total_new = len(diff.new_devices)
    diff.total_missing = len(diff.missing_devices)
    diff.total_changed = len(diff.changed_devices)

    # Separate expected vs unexpected changes
    for change in diff.new_devices + diff.missing_devices + diff.changed_devices:
        if change.expected:
            diff.expected_changes.append(change)
        else:
            diff.unexpected_changes.append(change)

    # Calculate health
    _calculate_baseline_health(diff, baseline)

    return diff


def _compare_wifi(diff: BaselineDiff, baseline: dict, current: list[dict]) -> None:
    """Compare WiFi devices between baseline and current."""
    current_macs = {d.get("bssid", d.get("mac", "")).upper(): d for d in current if d.get("bssid") or d.get("mac")}

    # Find new devices
    for mac, device in current_macs.items():
        if mac not in baseline:
            ssid = device.get("essid", device.get("ssid", "Hidden"))
            diff.new_devices.append(
                DeviceChange(
                    identifier=mac,
                    protocol="wifi",
                    change_type="new",
                    description=f"New WiFi AP: {ssid}",
                    expected=False,
                    details={
                        "ssid": ssid,
                        "channel": device.get("channel"),
                        "rssi": device.get("power", device.get("signal")),
                    },
                )
            )


def _compare_wifi_clients(diff: BaselineDiff, baseline: dict, current: list[dict]) -> None:
    """Compare WiFi clients between baseline and current."""
    current_macs = {d.get("mac", d.get("address", "")).upper(): d for d in current if d.get("mac") or d.get("address")}

    # Find new clients
    for mac, device in current_macs.items():
        if mac not in baseline:
            name = device.get("vendor", "WiFi Client")
            diff.new_devices.append(
                DeviceChange(
                    identifier=mac,
                    protocol="wifi_client",
                    change_type="new",
                    description=f"New WiFi client: {name}",
                    expected=False,
                    details={
                        "vendor": name,
                        "rssi": device.get("rssi"),
                        "associated_bssid": device.get("associated_bssid"),
                    },
                )
            )

    # Find missing clients
    for mac, device in baseline.items():
        if mac not in current_macs:
            name = device.get("vendor", "WiFi Client")
            diff.missing_devices.append(
                DeviceChange(
                    identifier=mac,
                    protocol="wifi_client",
                    change_type="missing",
                    description=f"Missing WiFi client: {name}",
                    expected=True,
                    details={
                        "vendor": name,
                    },
                )
            )
        else:
            # Check for changes
            baseline_dev = baseline[mac]
            changes = []

            # RSSI drift
            curr_rssi = device.get("power", device.get("signal"))
            base_rssi = baseline_dev.get("power", baseline_dev.get("signal"))
            if curr_rssi and base_rssi:
                rssi_diff = abs(int(curr_rssi) - int(base_rssi))
                if rssi_diff > 15:
                    changes.append(("rssi_drift", f"RSSI changed by {rssi_diff} dBm"))

            # Channel change
            curr_chan = device.get("channel")
            base_chan = baseline_dev.get("channel")
            if curr_chan and base_chan and curr_chan != base_chan:
                changes.append(("channel_change", f"Channel changed from {base_chan} to {curr_chan}"))

            # Security change
            curr_sec = device.get("encryption", device.get("privacy", ""))
            base_sec = baseline_dev.get("encryption", baseline_dev.get("privacy", ""))
            if curr_sec and base_sec and curr_sec != base_sec:
                changes.append(("security_change", f"Security changed from {base_sec} to {curr_sec}"))

            for change_type, desc in changes:
                diff.changed_devices.append(
                    DeviceChange(
                        identifier=mac,
                        protocol="wifi",
                        change_type=change_type,
                        description=desc,
                        expected=change_type == "rssi_drift",  # RSSI drift is often expected
                        details={
                            "ssid": device.get("essid", device.get("ssid")),
                            "baseline": baseline_dev,
                            "current": device,
                        },
                    )
                )

    # Find missing devices
    for mac, device in baseline.items():
        if mac not in current_macs:
            ssid = device.get("essid", device.get("ssid", "Hidden"))
            diff.missing_devices.append(
                DeviceChange(
                    identifier=mac,
                    protocol="wifi",
                    change_type="missing",
                    description=f"Missing WiFi AP: {ssid}",
                    expected=False,  # Could be powered off
                    details={
                        "ssid": ssid,
                        "last_channel": device.get("channel"),
                    },
                )
            )


def _compare_bluetooth(diff: BaselineDiff, baseline: dict, current: list[dict]) -> None:
    """Compare Bluetooth devices between baseline and current."""
    current_macs = {d.get("mac", d.get("address", "")).upper(): d for d in current if d.get("mac") or d.get("address")}

    # Find new devices
    for mac, device in current_macs.items():
        if mac not in baseline:
            name = device.get("name", "Unknown")
            diff.new_devices.append(
                DeviceChange(
                    identifier=mac,
                    protocol="bluetooth",
                    change_type="new",
                    description=f"New BLE device: {name}",
                    expected=False,
                    details={
                        "name": name,
                        "rssi": device.get("rssi"),
                        "manufacturer": device.get("manufacturer"),
                    },
                )
            )
        else:
            # Check for changes
            baseline_dev = baseline[mac]

            # Name change (device renamed)
            curr_name = device.get("name", "")
            base_name = baseline_dev.get("name", "")
            if curr_name and base_name and curr_name != base_name:
                diff.changed_devices.append(
                    DeviceChange(
                        identifier=mac,
                        protocol="bluetooth",
                        change_type="name_change",
                        description=f"Device renamed: {base_name} -> {curr_name}",
                        expected=True,
                        details={"old_name": base_name, "new_name": curr_name},
                    )
                )

    # Find missing devices
    for mac, device in baseline.items():
        if mac not in current_macs:
            name = device.get("name", "Unknown")
            diff.missing_devices.append(
                DeviceChange(
                    identifier=mac,
                    protocol="bluetooth",
                    change_type="missing",
                    description=f"Missing BLE device: {name}",
                    expected=True,  # BLE devices often go to sleep
                    details={"name": name},
                )
            )


def _compare_rf(diff: BaselineDiff, baseline: dict, current: list[dict]) -> None:
    """Compare RF signals between baseline and current."""
    current_freqs = {round(s.get("frequency", 0), 1): s for s in current if s.get("frequency")}

    # Find new signals
    for freq, signal in current_freqs.items():
        if freq not in baseline:
            diff.new_devices.append(
                DeviceChange(
                    identifier=f"{freq:.1f} MHz",
                    protocol="rf",
                    change_type="new",
                    description=f"New RF signal at {freq:.3f} MHz",
                    expected=False,
                    details={
                        "frequency": freq,
                        "power": signal.get("power", signal.get("level")),
                        "modulation": signal.get("modulation"),
                    },
                )
            )

    # Find missing signals
    for freq, signal in baseline.items():
        if freq not in current_freqs:
            diff.missing_devices.append(
                DeviceChange(
                    identifier=f"{freq:.1f} MHz",
                    protocol="rf",
                    change_type="missing",
                    description=f"Missing RF signal at {freq:.1f} MHz",
                    expected=True,  # RF signals can be intermittent
                    details={"frequency": freq},
                )
            )


def _calculate_baseline_health(diff: BaselineDiff, baseline: dict) -> None:
    """Calculate baseline health score and status."""
    score = 1.0
    reasons = []

    # Age penalty
    if diff.baseline_age_hours > 168:  # > 1 week
        score -= 0.4
        reasons.append(f"Baseline is {diff.baseline_age_hours:.0f} hours old (>1 week)")
    elif diff.baseline_age_hours > 72:  # > 3 days
        score -= 0.2
        reasons.append(f"Baseline is {diff.baseline_age_hours:.0f} hours old (>3 days)")
    elif diff.baseline_age_hours > 24:
        score -= 0.1
        reasons.append(f"Baseline is {diff.baseline_age_hours:.0f} hours old")

    # Device churn penalty
    total_baseline = (
        len(baseline.get("wifi_networks", []))
        + len(baseline.get("wifi_clients", []))
        + len(baseline.get("bt_devices", []))
        + len(baseline.get("rf_frequencies", []))
    )

    if total_baseline > 0:
        churn_rate = (diff.total_new + diff.total_missing) / total_baseline
        if churn_rate > 0.5:
            score -= 0.3
            reasons.append(f"High device churn rate: {churn_rate:.0%}")
        elif churn_rate > 0.25:
            score -= 0.15
            reasons.append(f"Moderate device churn rate: {churn_rate:.0%}")

    # Small baseline penalty
    if total_baseline < 3:
        score -= 0.2
        reasons.append(f"Baseline has few devices ({total_baseline}) - may be incomplete")

    # Set health status
    diff.health_score = max(0, min(1, score))

    if diff.health_score >= 0.7:
        diff.health = BaselineHealth.HEALTHY
    elif diff.health_score >= 0.4:
        diff.health = BaselineHealth.NOISY
        if not reasons:
            reasons.append("Baseline showing moderate variability")
    else:
        diff.health = BaselineHealth.STALE
        if not reasons:
            reasons.append("Baseline requires refresh")

    diff.health_reasons = reasons


# =============================================================================
# 3. Per-Device Timelines
# =============================================================================


@dataclass
class DeviceObservation:
    """A single observation of a device."""

    timestamp: datetime
    rssi: int | None = None
    present: bool = True
    channel: int | None = None
    frequency: float | None = None
    attributes: dict = field(default_factory=dict)


@dataclass
class DeviceTimeline:
    """
    Complete timeline for a device showing behavior over time.

    Used to assess signal stability, movement patterns, and
    meeting window correlation.
    """

    identifier: str
    protocol: str
    name: str | None = None

    # Observation history (time-bucketed)
    observations: list[DeviceObservation] = field(default_factory=list)

    # Computed metrics
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    total_observations: int = 0
    presence_ratio: float = 0.0  # % of time device was present

    # Signal metrics
    rssi_min: int | None = None
    rssi_max: int | None = None
    rssi_mean: float | None = None
    rssi_stability: float = 0.0  # 0-1, higher = more stable

    # Movement assessment
    appears_stationary: bool = True
    movement_pattern: str = "unknown"  # 'stationary', 'mobile', 'intermittent'

    # Meeting correlation
    meeting_correlated: bool = False
    meeting_observations: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "identifier": self.identifier,
            "protocol": self.protocol,
            "name": self.name,
            "observations": [
                {
                    "timestamp": obs.timestamp.isoformat(),
                    "rssi": obs.rssi,
                    "present": obs.present,
                    "channel": obs.channel,
                    "frequency": obs.frequency,
                }
                for obs in self.observations[-50:]  # Limit to last 50
            ],
            "metrics": {
                "first_seen": self.first_seen.isoformat() if self.first_seen else None,
                "last_seen": self.last_seen.isoformat() if self.last_seen else None,
                "total_observations": self.total_observations,
                "presence_ratio": round(self.presence_ratio, 2),
            },
            "signal": {
                "rssi_min": self.rssi_min,
                "rssi_max": self.rssi_max,
                "rssi_mean": round(self.rssi_mean, 1) if self.rssi_mean else None,
                "stability": round(self.rssi_stability, 2),
            },
            "movement": {
                "appears_stationary": self.appears_stationary,
                "pattern": self.movement_pattern,
            },
            "meeting_correlation": {
                "correlated": self.meeting_correlated,
                "observations_during_meeting": self.meeting_observations,
            },
        }


class TimelineManager:
    """
    Manages per-device timelines with time-bucketing.

    Buckets observations to keep memory bounded while preserving
    useful behavioral patterns.
    """

    def __init__(self, bucket_seconds: int = 30, max_observations: int = 200):
        """
        Args:
            bucket_seconds: Time bucket size in seconds
            max_observations: Maximum observations to keep per device
        """
        self.bucket_seconds = bucket_seconds
        self.max_observations = max_observations
        self.timelines: dict[str, DeviceTimeline] = {}
        self._meeting_windows: list[tuple[datetime, datetime | None]] = []

    def add_observation(
        self,
        identifier: str,
        protocol: str,
        rssi: int | None = None,
        channel: int | None = None,
        frequency: float | None = None,
        name: str | None = None,
        attributes: dict | None = None,
    ) -> None:
        """Add an observation for a device."""
        key = f"{protocol}:{identifier.upper()}"
        now = datetime.now()

        if key not in self.timelines:
            self.timelines[key] = DeviceTimeline(
                identifier=identifier.upper(),
                protocol=protocol,
                name=name,
                first_seen=now,
            )

        timeline = self.timelines[key]

        # Update name if provided
        if name:
            timeline.name = name

        # Check if we should bucket with previous observation
        if timeline.observations:
            last_obs = timeline.observations[-1]
            time_diff = (now - last_obs.timestamp).total_seconds()

            if time_diff < self.bucket_seconds:
                # Update existing bucket
                if rssi is not None:
                    # Average RSSI
                    if last_obs.rssi is not None:
                        last_obs.rssi = (last_obs.rssi + rssi) // 2
                    else:
                        last_obs.rssi = rssi
                return

        # Add new observation
        obs = DeviceObservation(
            timestamp=now,
            rssi=rssi,
            present=True,
            channel=channel,
            frequency=frequency,
            attributes=attributes or {},
        )
        timeline.observations.append(obs)

        # Enforce max observations
        if len(timeline.observations) > self.max_observations:
            timeline.observations = timeline.observations[-self.max_observations :]

        # Update metrics
        timeline.last_seen = now
        timeline.total_observations = len(timeline.observations)

        # Check meeting correlation
        if self._is_during_meeting(now):
            timeline.meeting_observations += 1
            timeline.meeting_correlated = True

    def start_meeting_window(self) -> None:
        """Mark the start of a meeting window."""
        self._meeting_windows.append((datetime.now(), None))

    def end_meeting_window(self) -> None:
        """Mark the end of a meeting window."""
        if self._meeting_windows and self._meeting_windows[-1][1] is None:
            start = self._meeting_windows[-1][0]
            self._meeting_windows[-1] = (start, datetime.now())

    def _is_during_meeting(self, timestamp: datetime) -> bool:
        """Check if timestamp falls within a meeting window."""
        for start, end in self._meeting_windows:
            if end is None:
                if timestamp >= start:
                    return True
            elif start <= timestamp <= end:
                return True
        return False

    def compute_metrics(self, identifier: str, protocol: str) -> DeviceTimeline | None:
        """Compute all metrics for a device timeline."""
        key = f"{protocol}:{identifier.upper()}"
        if key not in self.timelines:
            return None

        timeline = self.timelines[key]

        if not timeline.observations:
            return timeline

        # RSSI metrics
        rssi_values = [obs.rssi for obs in timeline.observations if obs.rssi is not None]
        if rssi_values:
            timeline.rssi_min = min(rssi_values)
            timeline.rssi_max = max(rssi_values)
            timeline.rssi_mean = sum(rssi_values) / len(rssi_values)

            # Calculate stability (0-1)
            if len(rssi_values) >= 3:
                variance = sum((r - timeline.rssi_mean) ** 2 for r in rssi_values) / len(rssi_values)
                timeline.rssi_stability = max(0, 1 - (variance / 100))

            # Movement assessment based on RSSI variance
            rssi_range = timeline.rssi_max - timeline.rssi_min
            if rssi_range < 10:
                timeline.appears_stationary = True
                timeline.movement_pattern = "stationary"
            elif rssi_range < 25:
                timeline.appears_stationary = False
                timeline.movement_pattern = "mobile"
            else:
                timeline.appears_stationary = False
                timeline.movement_pattern = "intermittent"

        # Presence ratio
        if timeline.first_seen and timeline.last_seen:
            total_duration = (timeline.last_seen - timeline.first_seen).total_seconds()
            if total_duration > 0:
                # Estimate presence based on observation count and bucket size
                estimated_present_time = timeline.total_observations * self.bucket_seconds
                timeline.presence_ratio = min(1.0, estimated_present_time / total_duration)

        return timeline

    def get_timeline(self, identifier: str, protocol: str) -> DeviceTimeline | None:
        """Get computed timeline for a device."""
        return self.compute_metrics(identifier, protocol)

    def get_all_timelines(self) -> list[DeviceTimeline]:
        """Get all device timelines with computed metrics."""
        for key in self.timelines:
            protocol, identifier = key.split(":", 1)
            self.compute_metrics(identifier, protocol)
        return list(self.timelines.values())


# =============================================================================
# 5. Meeting-Window Summary Enhancements
# =============================================================================


@dataclass
class MeetingWindowSummary:
    """
    Summary of device activity during a meeting window.

    Tracks devices first seen during meeting, behavior changes,
    and applies meeting-window scoring modifiers.
    """

    meeting_id: int
    name: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_minutes: float = 0.0

    # Devices first seen during meeting (high interest)
    devices_first_seen: list[dict] = field(default_factory=list)

    # Devices with behavior change during meeting
    devices_behavior_change: list[dict] = field(default_factory=list)

    # All active devices during meeting
    active_devices: list[dict] = field(default_factory=list)

    # Summary metrics
    total_devices_active: int = 0
    new_devices_count: int = 0
    behavior_changes_count: int = 0
    high_interest_count: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "meeting_id": self.meeting_id,
            "name": self.name,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_minutes": round(self.duration_minutes, 1),
            "summary": {
                "total_devices_active": self.total_devices_active,
                "new_devices": self.new_devices_count,
                "behavior_changes": self.behavior_changes_count,
                "high_interest": self.high_interest_count,
            },
            "devices_first_seen": self.devices_first_seen,
            "devices_behavior_change": self.devices_behavior_change,
            "disclaimer": (
                "Meeting-correlated activity indicates temporal correlation only, "
                "not confirmed surveillance. Devices may have legitimate reasons "
                "for appearing during meetings."
            ),
        }


def generate_meeting_summary(
    meeting_window: dict, device_timelines: list[DeviceTimeline], device_profiles: list[dict]
) -> MeetingWindowSummary:
    """
    Generate summary of device activity during a meeting window.

    Args:
        meeting_window: Meeting window dict from database
        device_timelines: List of device timelines
        device_profiles: List of device profiles from correlation engine

    Returns:
        MeetingWindowSummary with analysis
    """
    summary = MeetingWindowSummary(
        meeting_id=meeting_window.get("id", 0),
        name=meeting_window.get("name"),
    )

    # Parse times
    start_str = meeting_window.get("start_time")
    end_str = meeting_window.get("end_time")

    if start_str:
        if isinstance(start_str, str):
            summary.start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00")).replace(tzinfo=None)
        else:
            summary.start_time = start_str

    if end_str:
        if isinstance(end_str, str):
            summary.end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00")).replace(tzinfo=None)
        else:
            summary.end_time = end_str

    if summary.start_time and summary.end_time:
        summary.duration_minutes = (summary.end_time - summary.start_time).total_seconds() / 60

    if not summary.start_time:
        return summary

    # Analyze device timelines
    for timeline in device_timelines:
        if not timeline.first_seen:
            continue

        # Check if device was active during meeting
        was_active = False
        first_seen_during = False

        for obs in timeline.observations:
            if summary.end_time:
                if summary.start_time <= obs.timestamp <= summary.end_time:
                    was_active = True
                    if timeline.first_seen and abs((obs.timestamp - timeline.first_seen).total_seconds()) < 60:
                        first_seen_during = True
                    break
            else:
                # Meeting still ongoing
                if obs.timestamp >= summary.start_time:
                    was_active = True
                    if timeline.first_seen and abs((obs.timestamp - timeline.first_seen).total_seconds()) < 60:
                        first_seen_during = True
                    break

        if was_active:
            device_info = {
                "identifier": timeline.identifier,
                "protocol": timeline.protocol,
                "name": timeline.name,
                "meeting_correlated": True,
            }
            summary.active_devices.append(device_info)

            if first_seen_during:
                device_info["first_seen_during_meeting"] = True
                summary.devices_first_seen.append(
                    {
                        **device_info,
                        "description": "Device first seen during meeting window",
                        "risk_modifier": "+2 (meeting-correlated activity)",
                    }
                )

    # Update counts
    summary.total_devices_active = len(summary.active_devices)
    summary.new_devices_count = len(summary.devices_first_seen)
    summary.behavior_changes_count = len(summary.devices_behavior_change)

    # Count high interest from profiles
    for profile in device_profiles:
        if profile.get("risk_level") == "high_interest":
            indicators = profile.get("indicators", [])
            if any(i.get("type") == "meeting_correlated" for i in indicators):
                summary.high_interest_count += 1

    return summary


# =============================================================================
# 7. WiFi Advanced Indicators (LIMITED SCOPE)
# =============================================================================


@dataclass
class WiFiAdvancedIndicator:
    """An advanced WiFi indicator detection."""

    indicator_type: str  # 'evil_twin', 'probe_request', 'deauth_burst'
    severity: str  # 'high', 'medium', 'low'
    description: str
    details: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    requires_monitor_mode: bool = False

    def to_dict(self) -> dict:
        return {
            "type": self.indicator_type,
            "severity": self.severity,
            "description": self.description,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
            "requires_monitor_mode": self.requires_monitor_mode,
            "disclaimer": (
                "Pattern detected - this is an indicator, not confirmation of an attack. "
                "Further investigation required."
            ),
        }


class WiFiAdvancedDetector:
    """
    Detects advanced WiFi indicators.

    LIMITED SCOPE - Only implements:
    1. Evil Twin patterns (same SSID, different BSSID/security/abnormal signal)
    2. Probe requests for sensitive SSIDs (requires monitor mode)
    3. Deauthentication bursts (requires monitor mode)

    All findings labeled as "pattern detected", never called attacks.
    """

    def __init__(self, monitor_mode_available: bool = False):
        self.monitor_mode = monitor_mode_available
        self.known_networks: dict[str, dict] = {}  # SSID -> expected BSSID/security
        self.probe_requests: list[dict] = []
        self.deauth_frames: list[dict] = []
        self.indicators: list[WiFiAdvancedIndicator] = []

    def set_known_networks(self, networks: list[dict]) -> None:
        """Set known/expected networks from baseline."""
        for net in networks:
            ssid = net.get("essid", net.get("ssid", ""))
            if ssid:
                self.known_networks[ssid] = {
                    "bssid": net.get("bssid", net.get("mac", "")).upper(),
                    "security": net.get("encryption", net.get("privacy", "")),
                    "channel": net.get("channel"),
                    "rssi": net.get("power", net.get("signal")),
                }

    def analyze_network(self, network: dict) -> list[WiFiAdvancedIndicator]:
        """
        Analyze a network for evil twin patterns.

        Detects: Same SSID with different BSSID, security, or abnormal signal.
        """
        indicators = []
        ssid = network.get("essid", network.get("ssid", ""))
        bssid = network.get("bssid", network.get("mac", "")).upper()
        security = network.get("encryption", network.get("privacy", ""))
        rssi = network.get("power", network.get("signal"))

        if not ssid or ssid in ["", "Hidden", "[Hidden]"]:
            return indicators

        if ssid in self.known_networks:
            known = self.known_networks[ssid]

            # Different BSSID for same SSID
            if known["bssid"] and known["bssid"] != bssid:
                # Check security mismatch
                security_mismatch = known["security"] and security and known["security"] != security

                # Check signal anomaly (significantly stronger than expected)
                signal_anomaly = False
                if rssi and known.get("rssi"):
                    try:
                        rssi_diff = int(rssi) - int(known["rssi"])
                        signal_anomaly = rssi_diff > 20  # Much stronger than expected
                    except (ValueError, TypeError):
                        pass

                if security_mismatch:
                    indicators.append(
                        WiFiAdvancedIndicator(
                            indicator_type="evil_twin",
                            severity="high",
                            description=f'Evil twin pattern detected for SSID "{ssid}"',
                            details={
                                "ssid": ssid,
                                "detected_bssid": bssid,
                                "expected_bssid": known["bssid"],
                                "detected_security": security,
                                "expected_security": known["security"],
                                "pattern": "Different BSSID with security downgrade",
                            },
                            requires_monitor_mode=False,
                        )
                    )
                elif signal_anomaly:
                    indicators.append(
                        WiFiAdvancedIndicator(
                            indicator_type="evil_twin",
                            severity="medium",
                            description=f'Possible evil twin pattern for SSID "{ssid}"',
                            details={
                                "ssid": ssid,
                                "detected_bssid": bssid,
                                "expected_bssid": known["bssid"],
                                "signal_difference": f"+{rssi_diff} dBm stronger than expected",
                                "pattern": "Different BSSID with abnormally strong signal",
                            },
                            requires_monitor_mode=False,
                        )
                    )
                else:
                    indicators.append(
                        WiFiAdvancedIndicator(
                            indicator_type="evil_twin",
                            severity="low",
                            description=f'Duplicate SSID detected: "{ssid}"',
                            details={
                                "ssid": ssid,
                                "detected_bssid": bssid,
                                "expected_bssid": known["bssid"],
                                "pattern": "Multiple APs with same SSID (may be legitimate)",
                            },
                            requires_monitor_mode=False,
                        )
                    )

        self.indicators.extend(indicators)
        return indicators

    def add_probe_request(self, frame: dict) -> WiFiAdvancedIndicator | None:
        """
        Record a probe request frame (requires monitor mode).

        Detects repeated probing for sensitive SSIDs.
        """
        if not self.monitor_mode:
            return None

        self.probe_requests.append(
            {
                "timestamp": datetime.now(),
                "src_mac": frame.get("src_mac", "").upper(),
                "probed_ssid": frame.get("ssid", ""),
            }
        )

        # Keep last 1000 probe requests
        if len(self.probe_requests) > 1000:
            self.probe_requests = self.probe_requests[-1000:]

        # Check for sensitive SSID probing
        ssid = frame.get("ssid", "")
        sensitive_patterns = [
            "corp",
            "internal",
            "private",
            "secure",
            "vpn",
            "admin",
            "management",
            "executive",
            "board",
        ]

        is_sensitive = any(p in ssid.lower() for p in sensitive_patterns) if ssid else False

        if is_sensitive:
            # Count recent probes for this SSID
            recent_cutoff = datetime.now() - timedelta(minutes=5)
            recent_probes = [
                p for p in self.probe_requests if p["probed_ssid"] == ssid and p["timestamp"] > recent_cutoff
            ]

            if len(recent_probes) >= 3:
                indicator = WiFiAdvancedIndicator(
                    indicator_type="probe_request",
                    severity="medium",
                    description=f'Repeated probing for sensitive SSID "{ssid}"',
                    details={
                        "ssid": ssid,
                        "probe_count": len(recent_probes),
                        "source_macs": list({p["src_mac"] for p in recent_probes}),
                        "pattern": "Multiple probe requests for potentially sensitive network",
                    },
                    requires_monitor_mode=True,
                )
                self.indicators.append(indicator)
                return indicator

        return None

    def add_deauth_frame(self, frame: dict) -> WiFiAdvancedIndicator | None:
        """
        Record a deauthentication frame (requires monitor mode).

        Detects abnormal deauth volume potentially indicating attack.
        """
        if not self.monitor_mode:
            return None

        self.deauth_frames.append(
            {
                "timestamp": datetime.now(),
                "src_mac": frame.get("src_mac", "").upper(),
                "dst_mac": frame.get("dst_mac", "").upper(),
                "bssid": frame.get("bssid", "").upper(),
                "reason": frame.get("reason_code"),
            }
        )

        # Keep last 500 deauth frames
        if len(self.deauth_frames) > 500:
            self.deauth_frames = self.deauth_frames[-500:]

        # Check for deauth burst (>10 deauths in 10 seconds)
        recent_cutoff = datetime.now() - timedelta(seconds=10)
        recent_deauths = [d for d in self.deauth_frames if d["timestamp"] > recent_cutoff]

        if len(recent_deauths) >= 10:
            # Check if targeting specific BSSID
            bssid = frame.get("bssid", "").upper()
            targeting_bssid = len([d for d in recent_deauths if d["bssid"] == bssid]) >= 5

            indicator = WiFiAdvancedIndicator(
                indicator_type="deauth_burst",
                severity="high" if targeting_bssid else "medium",
                description="Deauthentication burst pattern detected",
                details={
                    "deauth_count": len(recent_deauths),
                    "time_window_seconds": 10,
                    "targeted_bssid": bssid if targeting_bssid else None,
                    "unique_sources": len({d["src_mac"] for d in recent_deauths}),
                    "pattern": "Abnormal deauthentication frame volume",
                },
                requires_monitor_mode=True,
            )
            self.indicators.append(indicator)

            # Clear recent to avoid repeated alerts
            self.deauth_frames = [d for d in self.deauth_frames if d["timestamp"] <= recent_cutoff]

            return indicator

        return None

    def get_all_indicators(self) -> list[dict]:
        """Get all detected indicators."""
        return [i.to_dict() for i in self.indicators]

    def get_unavailable_features(self) -> list[str]:
        """Get list of features unavailable without monitor mode."""
        if self.monitor_mode:
            return []
        return [
            "Probe request analysis: Requires monitor mode to capture probe frames.",
            "Deauthentication detection: Requires monitor mode to capture management frames.",
            "Raw 802.11 frame analysis: Not available in managed mode.",
        ]


# =============================================================================
# 8. Bluetooth Risk Explainability & Proximity Heuristics
# =============================================================================


class BLEProximity(Enum):
    """RSSI-based proximity estimation."""

    VERY_CLOSE = "very_close"  # Within ~1m
    CLOSE = "close"  # Within ~3m
    MODERATE = "moderate"  # Within ~10m
    FAR = "far"  # Beyond ~10m
    UNKNOWN = "unknown"


@dataclass
class BLERiskExplanation:
    """
    Explainable risk assessment for a BLE device.

    Provides human-readable explanations, proximity estimates,
    and recommended actions.
    """

    identifier: str
    name: str | None = None

    # Risk assessment
    risk_level: str = "informational"
    risk_score: int = 0
    risk_explanation: str = ""

    # Proximity
    proximity: BLEProximity = BLEProximity.UNKNOWN
    proximity_explanation: str = ""
    estimated_distance: str = ""

    # Tracker detection
    is_tracker: bool = False
    tracker_type: str | None = None
    tracker_explanation: str = ""

    # Meeting correlation
    meeting_correlated: bool = False
    meeting_explanation: str = ""

    # Recommended action
    recommended_action: str = ""
    action_rationale: str = ""

    # All indicators with explanations
    indicators: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "name": self.name,
            "risk": {
                "level": self.risk_level,
                "score": self.risk_score,
                "explanation": self.risk_explanation,
            },
            "proximity": {
                "estimate": self.proximity.value,
                "explanation": self.proximity_explanation,
                "estimated_distance": self.estimated_distance,
            },
            "tracker": {
                "is_tracker": self.is_tracker,
                "type": self.tracker_type,
                "explanation": self.tracker_explanation,
            },
            "meeting_correlation": {
                "correlated": self.meeting_correlated,
                "explanation": self.meeting_explanation,
            },
            "recommended_action": {
                "action": self.recommended_action,
                "rationale": self.action_rationale,
            },
            "indicators": self.indicators,
            "disclaimer": (
                "Risk assessment is based on observable indicators and heuristics. "
                "Proximity estimates are approximate based on RSSI and may vary with environment. "
                "Tracker detection indicates brand presence, not confirmed threat."
            ),
        }


def estimate_ble_proximity(rssi: int) -> tuple[BLEProximity, str, str]:
    """
    Estimate BLE device proximity from RSSI.

    Note: RSSI-based distance is highly variable due to:
    - TX power differences between devices
    - Environmental factors (walls, interference)
    - Antenna characteristics

    Returns:
        Tuple of (proximity enum, explanation, estimated distance string)
    """
    if rssi is None:
        return (BLEProximity.UNKNOWN, "RSSI not available - cannot estimate proximity", "Unknown")

    # These thresholds are heuristic approximations
    if rssi >= -50:
        return (
            BLEProximity.VERY_CLOSE,
            f"Very strong signal ({rssi} dBm) suggests device is very close",
            "< 1 meter (approximate)",
        )
    elif rssi >= -65:
        return (BLEProximity.CLOSE, f"Strong signal ({rssi} dBm) suggests device is nearby", "1-3 meters (approximate)")
    elif rssi >= -80:
        return (
            BLEProximity.MODERATE,
            f"Moderate signal ({rssi} dBm) suggests device is in the area",
            "3-10 meters (approximate)",
        )
    else:
        return (BLEProximity.FAR, f"Weak signal ({rssi} dBm) suggests device is distant", "> 10 meters (approximate)")


def generate_ble_risk_explanation(
    device: dict, profile: dict | None = None, is_during_meeting: bool = False
) -> BLERiskExplanation:
    """
    Generate human-readable risk explanation for a BLE device.

    Args:
        device: BLE device dict with mac, name, rssi, etc.
        profile: DeviceProfile dict from correlation engine
        is_during_meeting: Whether device was detected during meeting

    Returns:
        BLERiskExplanation with complete assessment
    """
    mac = device.get("mac", device.get("address", "")).upper()
    name = device.get("name", "")
    rssi = device.get("rssi", device.get("signal"))

    explanation = BLERiskExplanation(
        identifier=mac,
        name=name if name else None,
    )

    # Proximity estimation
    if rssi:
        try:
            rssi_int = int(rssi)
            prox, prox_exp, dist = estimate_ble_proximity(rssi_int)
            explanation.proximity = prox
            explanation.proximity_explanation = prox_exp
            explanation.estimated_distance = dist
        except (ValueError, TypeError):
            explanation.proximity = BLEProximity.UNKNOWN
            explanation.proximity_explanation = "Could not parse RSSI value"

    # Tracker detection with explanation
    device.get("tracker_type") or device.get("is_tracker")
    if device.get("is_airtag"):
        explanation.is_tracker = True
        explanation.tracker_type = "Apple AirTag"
        explanation.tracker_explanation = (
            "Apple AirTag detected via manufacturer data. AirTags are legitimate "
            "tracking devices but may indicate unwanted tracking if not recognized. "
            "Apple's Find My network will alert iPhone users to unknown AirTags."
        )
    elif device.get("is_tile"):
        explanation.is_tracker = True
        explanation.tracker_type = "Tile"
        explanation.tracker_explanation = (
            "Tile tracker detected. Tile trackers are common consumer devices "
            "for finding lost items. Presence does not indicate surveillance."
        )
    elif device.get("is_smarttag"):
        explanation.is_tracker = True
        explanation.tracker_type = "Samsung SmartTag"
        explanation.tracker_explanation = (
            "Samsung SmartTag detected. SmartTags are consumer tracking devices "
            "similar to AirTags. Samsung phones can detect unknown SmartTags."
        )
    elif device.get("is_espressif"):
        explanation.tracker_type = "ESP32/ESP8266"
        explanation.tracker_explanation = (
            "Espressif chipset (ESP32/ESP8266) detected. These are programmable "
            "development boards commonly used in IoT projects. They can be configured "
            "for various purposes including custom tracking devices."
        )

    # Meeting correlation explanation
    if is_during_meeting or device.get("meeting_correlated"):
        explanation.meeting_correlated = True
        explanation.meeting_explanation = (
            "Device detected during a marked meeting window. This temporal correlation "
            "is noted but does not confirm malicious intent - many legitimate devices "
            "are active during meetings (phones, laptops, wearables)."
        )

    # Build risk explanation from profile
    if profile:
        explanation.risk_level = profile.get("risk_level", "informational")
        explanation.risk_score = profile.get("total_score", 0)

        # Convert indicators to explanations
        for ind in profile.get("indicators", []):
            ind_type = ind.get("type", "")
            ind_desc = ind.get("description", "")

            explanation.indicators.append(
                {
                    "type": ind_type,
                    "description": ind_desc,
                    "explanation": _get_indicator_explanation(ind_type),
                }
            )

        # Build overall risk explanation
        if explanation.risk_level == "high_interest":
            explanation.risk_explanation = (
                f"This device has accumulated {explanation.risk_score} risk points "
                "across multiple indicators, warranting closer investigation. "
                "High interest does not confirm surveillance - manual verification required."
            )
        elif explanation.risk_level == "review":
            explanation.risk_explanation = (
                f"This device shows {explanation.risk_score} risk points indicating "
                "it should be reviewed but is not immediately concerning."
            )
        else:
            explanation.risk_explanation = (
                "This device shows typical characteristics and does not raise "
                "significant concerns based on observable indicators."
            )
    else:
        explanation.risk_explanation = "No detailed profile available for risk assessment."

    # Recommended action
    _set_recommended_action(explanation)

    return explanation


def _get_indicator_explanation(indicator_type: str) -> str:
    """Get human-readable explanation for an indicator type."""
    explanations = {
        "unknown_device": (
            "Device manufacturer is unknown or uses a generic chipset. "
            "This is common in DIY/hobbyist devices and some surveillance equipment."
        ),
        "audio_capable": (
            "Device advertises audio services (headphones, speakers, etc.). "
            "Audio-capable devices could theoretically transmit captured audio."
        ),
        "persistent": (
            "Device has been detected repeatedly across multiple scans. "
            "Persistence suggests a fixed or regularly present device."
        ),
        "meeting_correlated": (
            "Device activity correlates with marked meeting windows. "
            "This is a temporal pattern that warrants attention."
        ),
        "hidden_identity": (
            "Device does not broadcast a name or uses minimal advertising. "
            "Some legitimate devices minimize advertising for battery life."
        ),
        "stable_rssi": (
            "Signal strength is very stable, suggesting a stationary device. "
            "Fixed placement could indicate a planted device."
        ),
        "mac_rotation": (
            "Device appears to use MAC address randomization. "
            "This is a privacy feature in modern devices, also used to evade detection."
        ),
        "known_tracker": (
            "Device matches known tracking device signatures. "
            "May be a legitimate item tracker or unwanted surveillance."
        ),
        "airtag_detected": ("Apple AirTag identified. Check if this belongs to someone present."),
        "tile_detected": ("Tile tracker identified. Common consumer tracking device."),
        "smarttag_detected": ("Samsung SmartTag identified. Consumer tracking device."),
        "esp32_device": (
            "Espressif development board detected. Highly programmable, "
            "could be configured for custom surveillance applications."
        ),
    }
    return explanations.get(indicator_type, "Indicator detected requiring review.")


def _set_recommended_action(explanation: BLERiskExplanation) -> None:
    """Set recommended action based on risk assessment."""
    if explanation.risk_level == "high_interest":
        if explanation.is_tracker and explanation.proximity == BLEProximity.VERY_CLOSE:
            explanation.recommended_action = "Investigate immediately"
            explanation.action_rationale = (
                "Unknown tracker in very close proximity warrants immediate "
                "physical search of the area and personal belongings."
            )
        elif explanation.is_tracker:
            explanation.recommended_action = "Investigate location"
            explanation.action_rationale = (
                "Tracker detected - recommend searching the area to locate "
                "the physical device and determine if it belongs to someone present."
            )
        else:
            explanation.recommended_action = "Review and document"
            explanation.action_rationale = (
                "Multiple risk indicators present. Document the finding, "
                "attempt to identify the device, and consider physical search "
                "if other indicators suggest surveillance."
            )
    elif explanation.risk_level == "review":
        explanation.recommended_action = "Monitor and document"
        explanation.action_rationale = (
            "Device shows some indicators worth noting. Add to monitoring list "
            "and compare against future sweeps to identify patterns."
        )
    else:
        explanation.recommended_action = "Continue monitoring"
        explanation.action_rationale = (
            "No immediate action required. Device will be tracked in subsequent sweeps for pattern analysis."
        )


# =============================================================================
# 9. Operator Playbooks ("What To Do Next")
# =============================================================================


@dataclass
class PlaybookStep:
    """A single step in an operator playbook."""

    step_number: int
    action: str
    details: str
    safety_note: str | None = None


@dataclass
class OperatorPlaybook:
    """
    Procedural guidance for TSCM operators based on findings.

    Playbooks are procedural (what to do), not prescriptive (how to decide).
    All guidance is legally safe and professional.
    """

    playbook_id: str
    title: str
    risk_level: str
    description: str
    steps: list[PlaybookStep] = field(default_factory=list)
    when_to_escalate: str = ""
    documentation_required: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "playbook_id": self.playbook_id,
            "title": self.title,
            "risk_level": self.risk_level,
            "description": self.description,
            "steps": [
                {
                    "step": s.step_number,
                    "action": s.action,
                    "details": s.details,
                    "safety_note": s.safety_note,
                }
                for s in self.steps
            ],
            "when_to_escalate": self.when_to_escalate,
            "documentation_required": self.documentation_required,
            "disclaimer": (
                "This playbook provides procedural guidance only. Actions should be "
                "adapted to local laws, organizational policies, and professional judgment. "
                "Do not disassemble, interfere with, or remove suspected devices without "
                "proper authorization and legal guidance."
            ),
        }


# Predefined playbooks by risk level
PLAYBOOKS = {
    "high_interest_tracker": OperatorPlaybook(
        playbook_id="PB-001",
        title="High Interest: Unknown Tracker Detection",
        risk_level="high_interest",
        description="Guidance for responding to unknown tracking device detection",
        steps=[
            PlaybookStep(
                step_number=1,
                action="Document the finding",
                details="Record device identifier, signal strength, location, and timestamp. Take screenshots of the detection.",
            ),
            PlaybookStep(
                step_number=2,
                action="Estimate device location",
                details="Use signal strength variations while moving to triangulate approximate device position. Note areas of strongest signal.",
                safety_note="Do not touch or disturb any physical device found.",
            ),
            PlaybookStep(
                step_number=3,
                action="Physical search (if authorized)",
                details="Systematically search the high-signal area. Check common hiding spots: under furniture, in plants, behind fixtures, in bags/belongings.",
                safety_note="Only conduct physical searches with proper authorization.",
            ),
            PlaybookStep(
                step_number=4,
                action="Identify device owner",
                details="If device is located, determine if it belongs to someone legitimately present. Apple/Samsung/Tile devices can be scanned by their respective apps.",
            ),
            PlaybookStep(
                step_number=5,
                action="Escalate if unidentified",
                details="If device owner cannot be determined and device is in sensitive location, escalate to security management.",
            ),
        ],
        when_to_escalate="Escalate immediately if: device is concealed in sensitive area, owner cannot be identified, or multiple unknown trackers are found.",
        documentation_required=[
            "Device identifier (MAC address)",
            "Signal strength readings at multiple locations",
            "Physical location description",
            "Photos of any located devices",
            "Names of individuals present during search",
        ],
    ),
    "high_interest_generic": OperatorPlaybook(
        playbook_id="PB-002",
        title="High Interest: Suspicious Device Pattern",
        risk_level="high_interest",
        description="Guidance for devices with multiple high-risk indicators",
        steps=[
            PlaybookStep(
                step_number=1,
                action="Review all indicators",
                details="Examine each risk indicator in the device profile. Understand why the device scored high interest.",
            ),
            PlaybookStep(
                step_number=2,
                action="Cross-reference with baseline",
                details="Check if device appears in baseline. New devices warrant more scrutiny than known devices.",
            ),
            PlaybookStep(
                step_number=3,
                action="Monitor for pattern",
                details="Continue sweep and note if device persists, moves, or correlates with sensitive activities.",
            ),
            PlaybookStep(
                step_number=4,
                action="Attempt identification",
                details="Research manufacturer OUI, check for matching devices in the environment, ask occupants about devices.",
            ),
            PlaybookStep(
                step_number=5,
                action="Document and report",
                details="Add finding to sweep report with full details. Include in meeting/client debrief.",
            ),
        ],
        when_to_escalate="Escalate if: device cannot be identified, shows surveillance-consistent behavior, or correlates strongly with sensitive activities.",
        documentation_required=[
            "Complete device profile",
            "All risk indicators with scores",
            "Timeline of observations",
            "Correlation with meeting windows",
            "Any identification attempts and results",
        ],
    ),
    "needs_review": OperatorPlaybook(
        playbook_id="PB-003",
        title="Needs Review: Unknown Device",
        risk_level="needs_review",
        description="Guidance for devices requiring investigation but not immediately concerning",
        steps=[
            PlaybookStep(
                step_number=1,
                action="Note the device",
                details="Add device to monitoring list. Record basic details: identifier, type, signal strength.",
            ),
            PlaybookStep(
                step_number=2,
                action="Check against known devices",
                details="Verify device is not a known infrastructure device or personal device of authorized personnel.",
            ),
            PlaybookStep(
                step_number=3,
                action="Continue sweep",
                details="Complete the sweep. Review device in context of all findings.",
            ),
            PlaybookStep(
                step_number=4,
                action="Assess in final review",
                details="During sweep wrap-up, decide if device warrants further investigation or can be added to baseline.",
            ),
        ],
        when_to_escalate='Escalate if: multiple "needs review" devices appear together, or device shows high-interest indicators in subsequent sweeps.',
        documentation_required=[
            "Device identifier and type",
            "Brief description of why flagged",
            "Decision made (investigate further / add to baseline / monitor)",
        ],
    ),
    "informational": OperatorPlaybook(
        playbook_id="PB-004",
        title="Informational: Known/Expected Device",
        risk_level="informational",
        description="Guidance for devices that appear normal and expected",
        steps=[
            PlaybookStep(
                step_number=1,
                action="Verify against baseline",
                details="Confirm device matches baseline entry. Note any changes (signal strength, channel, etc.).",
            ),
            PlaybookStep(
                step_number=2,
                action="Log observation",
                details="Record observation for timeline tracking. Even known devices should be logged.",
            ),
            PlaybookStep(
                step_number=3,
                action="Continue sweep",
                details="No further action required. Proceed with sweep.",
            ),
        ],
        when_to_escalate="Only escalate if device shows unexpected behavior changes or additional risk indicators.",
        documentation_required=[
            "Device identifier (for timeline)",
            "Observation timestamp",
        ],
    ),
    "wifi_evil_twin": OperatorPlaybook(
        playbook_id="PB-005",
        title="High Interest: Evil Twin Pattern Detected",
        risk_level="high_interest",
        description="Guidance when duplicate SSID with security mismatch is detected",
        steps=[
            PlaybookStep(
                step_number=1,
                action="Document both access points",
                details="Record details of legitimate AP and suspected rogue: BSSID, security, signal strength, channel.",
            ),
            PlaybookStep(
                step_number=2,
                action="Verify legitimate AP",
                details="Confirm which AP is the authorized infrastructure. Check with IT/facilities if needed.",
            ),
            PlaybookStep(
                step_number=3,
                action="Locate rogue AP",
                details="Use signal strength to estimate rogue AP location. Walk the area noting signal variations.",
                safety_note="Do not connect to or interact with the suspected rogue AP.",
            ),
            PlaybookStep(
                step_number=4,
                action="Physical search",
                details="Search suspected area for unauthorized access point. Check for hidden devices, suspicious equipment.",
            ),
            PlaybookStep(
                step_number=5,
                action="Report to IT Security",
                details="Even if device not found, report the finding to IT Security for network monitoring.",
            ),
        ],
        when_to_escalate="Escalate immediately. Evil twin attacks can capture credentials and traffic.",
        documentation_required=[
            "Both AP details (BSSID, SSID, security, channel, signal)",
            "Location where detected",
            "Signal strength map if created",
            "Physical search results",
        ],
    ),
}


def get_playbook_for_finding(
    risk_level: str, finding_type: str | None = None, indicators: list[dict] | None = None
) -> OperatorPlaybook:
    """
    Get appropriate playbook for a finding.

    Args:
        risk_level: Risk level string
        finding_type: Optional specific finding type
        indicators: Optional list of indicators

    Returns:
        Appropriate OperatorPlaybook
    """
    # Check for specific finding types
    if finding_type == "evil_twin":
        return PLAYBOOKS["wifi_evil_twin"]

    # Check indicators for tracker
    if indicators:
        tracker_types = ["airtag_detected", "tile_detected", "smarttag_detected", "known_tracker"]
        if any(i.get("type") in tracker_types for i in indicators) and risk_level == "high_interest":
            return PLAYBOOKS["high_interest_tracker"]

    # Return based on risk level
    if risk_level == "high_interest":
        return PLAYBOOKS["high_interest_generic"]
    elif risk_level in ["review", "needs_review"]:
        return PLAYBOOKS["needs_review"]
    else:
        return PLAYBOOKS["informational"]


def attach_playbook_to_finding(finding: dict) -> dict:
    """
    Attach appropriate playbook to a finding dict.

    Args:
        finding: Finding dict with risk_level, indicators, etc.

    Returns:
        Finding dict with playbook attached
    """
    risk_level = finding.get("risk_level", "informational")
    finding_type = finding.get("finding_type")
    indicators = finding.get("indicators", [])

    playbook = get_playbook_for_finding(risk_level, finding_type, indicators)
    finding["suggested_playbook"] = playbook.to_dict()
    finding["suggested_next_steps"] = [
        f"Step {s.step_number}: {s.action}"
        for s in playbook.steps[:3]  # First 3 steps as quick reference
    ]

    return finding


# =============================================================================
# Global Instance Management
# =============================================================================

_timeline_manager: TimelineManager | None = None
_wifi_detector: WiFiAdvancedDetector | None = None


def get_timeline_manager() -> TimelineManager:
    """Get or create global timeline manager."""
    global _timeline_manager
    if _timeline_manager is None:
        _timeline_manager = TimelineManager()
    return _timeline_manager


def reset_timeline_manager() -> None:
    """Reset global timeline manager."""
    global _timeline_manager
    _timeline_manager = TimelineManager()


def get_wifi_detector(monitor_mode: bool = False) -> WiFiAdvancedDetector:
    """Get or create global WiFi detector."""
    global _wifi_detector
    if _wifi_detector is None:
        _wifi_detector = WiFiAdvancedDetector(monitor_mode)
    return _wifi_detector


def reset_wifi_detector(monitor_mode: bool = False) -> None:
    """Reset global WiFi detector."""
    global _wifi_detector
    _wifi_detector = WiFiAdvancedDetector(monitor_mode)
