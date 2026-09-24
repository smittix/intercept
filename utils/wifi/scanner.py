"""
Unified WiFi scanner coordinator.

Provides dual-mode scanning:
- Quick Scan: Uses system tools (nmcli, iw, iwlist, airport) without monitor mode
- Deep Scan: Uses airodump-ng with monitor mode for clients and probes
"""

from __future__ import annotations

import logging
import os
import platform
import queue
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .deauth_detector import DeauthDetector

import contextlib

from .constants import (
    DEFAULT_QUICK_SCAN_TIMEOUT,
    MAX_RSSI_SAMPLES,
    SCAN_MODE_DEEP,
    SCAN_MODE_QUICK,
    TOOL_TIMEOUT_DETECT,
    WIFI_EMA_ALPHA,
    get_band_from_channel,
    get_proximity_band,
    get_signal_band,
    get_vendor_from_mac,
)
from .models import (
    ChannelRecommendation,
    ChannelStats,
    WiFiAccessPoint,
    WiFiCapabilities,
    WiFiClient,
    WiFiObservation,
    WiFiProbeRequest,
    WiFiScanResult,
    WiFiScanStatus,
)

logger = logging.getLogger(__name__)

# Global scanner instance
_scanner_instance: UnifiedWiFiScanner | None = None
_scanner_lock = threading.Lock()


class UnifiedWiFiScanner:
    """
    Unified WiFi scanner with Quick Scan and Deep Scan modes.

    Quick Scan: One-shot scan using system tools
    Deep Scan: Continuous monitoring with airodump-ng
    """

    def __init__(self, interface: str | None = None):
        """
        Initialize WiFi scanner.

        Args:
            interface: WiFi interface name (e.g., 'wlan0', 'en0').
        """
        self._interface = interface
        self._lock = threading.Lock()

        # State
        self._status = WiFiScanStatus()
        self._capabilities: WiFiCapabilities | None = None

        # Discovered entities
        self._access_points: dict[str, WiFiAccessPoint] = {}  # bssid -> AP
        self._clients: dict[str, WiFiClient] = {}  # mac -> Client
        self._probe_requests: list[WiFiProbeRequest] = []

        # Deep scan process
        self._deep_scan_process: subprocess.Popen | None = None
        self._deep_scan_thread: threading.Thread | None = None
        self._deep_scan_stop_event = threading.Event()

        # Deauth detector
        self._deauth_detector: DeauthDetector | None = None

        # Event queue for SSE streaming
        self._event_queue: queue.Queue = queue.Queue(maxsize=1000)

        # Callbacks
        self._on_network_updated: Callable[[WiFiAccessPoint], None] | None = None
        self._on_client_updated: Callable[[WiFiClient], None] | None = None
        self._on_probe_request: Callable[[WiFiProbeRequest], None] | None = None

        # Baseline tracking
        self._baseline_networks: set[str] = set()  # BSSIDs in baseline
        self._baseline_set_at: datetime | None = None

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def is_scanning(self) -> bool:
        """Check if currently scanning."""
        return self._status.is_scanning

    @property
    def access_points(self) -> list[WiFiAccessPoint]:
        """Get all discovered access points."""
        with self._lock:
            return list(self._access_points.values())

    @property
    def clients(self) -> list[WiFiClient]:
        """Get all discovered clients."""
        with self._lock:
            return list(self._clients.values())

    @property
    def probe_requests(self) -> list[WiFiProbeRequest]:
        """Get all captured probe requests."""
        with self._lock:
            return list(self._probe_requests)

    # =========================================================================
    # Capability Detection
    # =========================================================================

    def check_capabilities(self) -> WiFiCapabilities:
        """
        Check WiFi scanning capabilities on this system.

        Returns:
            WiFiCapabilities with available tools and interfaces.
        """
        caps = WiFiCapabilities()
        caps.platform = platform.system().lower()
        caps.is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False

        # Detect tools
        caps.has_nmcli = shutil.which("nmcli") is not None
        caps.has_iw = shutil.which("iw") is not None
        caps.has_iwlist = shutil.which("iwlist") is not None
        caps.has_airmon_ng = shutil.which("airmon-ng") is not None
        caps.has_airodump_ng = shutil.which("airodump-ng") is not None

        # macOS airport tool
        airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
        caps.has_airport = os.path.exists(airport_path)

        # Determine preferred quick scan tool
        if caps.platform == "darwin":
            if caps.has_airport:
                caps.preferred_quick_tool = "airport"
        else:  # Linux
            if caps.has_nmcli:
                caps.preferred_quick_tool = "nmcli"
            elif caps.has_iw:
                caps.preferred_quick_tool = "iw"
            elif caps.has_iwlist:
                caps.preferred_quick_tool = "iwlist"

        # Detect interfaces
        caps.interfaces = self._detect_interfaces()
        if caps.interfaces:
            caps.default_interface = caps.interfaces[0].get("name")

        # Check for monitor-capable interface
        for iface in caps.interfaces:
            if iface.get("supports_monitor", False):
                caps.has_monitor_capable_interface = True
                caps.monitor_interface = iface.get("name")
                break

        # Build issues list
        if not caps.interfaces:
            caps.issues.append("No WiFi interfaces detected")
        if not caps.can_quick_scan:
            caps.issues.append("No quick scan tools available")
        if not caps.can_deep_scan:
            if not caps.has_airodump_ng:
                caps.issues.append("airodump-ng not installed (install aircrack-ng)")
            if not caps.is_root:
                caps.issues.append("Root privileges required for deep scan")
            if not caps.has_monitor_capable_interface:
                caps.issues.append("No monitor mode capable interface")

        self._capabilities = caps
        return caps

    def _detect_interfaces(self) -> list[dict]:
        """Detect available WiFi interfaces."""
        interfaces = []

        if platform.system() == "Darwin":
            # macOS: Use networksetup
            try:
                result = subprocess.run(
                    ["networksetup", "-listallhardwareports"],
                    capture_output=True,
                    text=True,
                    timeout=TOOL_TIMEOUT_DETECT,
                )
                current_port = None
                for line in result.stdout.splitlines():
                    if line.startswith("Hardware Port:"):
                        current_port = line.split(":", 1)[1].strip()
                    elif line.startswith("Device:") and current_port:
                        device = line.split(":", 1)[1].strip()
                        if "Wi-Fi" in current_port or "wi-fi" in current_port.lower():
                            interfaces.append(
                                {
                                    "name": device,
                                    "description": current_port,
                                    "supports_monitor": False,  # macOS generally doesn't support monitor mode
                                }
                            )
                        current_port = None
            except Exception as e:
                logger.debug(f"Error detecting macOS interfaces: {e}")
        else:
            # Linux: Use /sys/class/net or iw
            try:
                net_path = Path("/sys/class/net")
                if net_path.exists():
                    for iface_path in net_path.iterdir():
                        wireless_path = iface_path / "wireless"
                        if wireless_path.exists():
                            iface_name = iface_path.name
                            supports_monitor = self._check_monitor_support(iface_name)
                            interfaces.append(
                                {
                                    "name": iface_name,
                                    "description": f"Wireless interface {iface_name}",
                                    "supports_monitor": supports_monitor,
                                }
                            )
            except Exception as e:
                logger.debug(f"Error detecting Linux interfaces: {e}")

        return interfaces

    def _check_monitor_support(self, interface: str) -> bool:
        """Check if interface supports monitor mode."""
        try:
            result = subprocess.run(
                ["iw", interface, "info"],
                capture_output=True,
                text=True,
                timeout=TOOL_TIMEOUT_DETECT,
            )
            # Get phy name
            phy_match = re.search(r"wiphy (\d+)", result.stdout)
            if phy_match:
                phy = f"phy{phy_match.group(1)}"
                # Check supported modes
                result = subprocess.run(
                    ["iw", phy, "info"],
                    capture_output=True,
                    text=True,
                    timeout=TOOL_TIMEOUT_DETECT,
                )
                return "monitor" in result.stdout.lower()
        except Exception:
            pass
        return False

    def _is_monitor_mode_interface(self, interface: str) -> bool:
        """
        Check if interface is currently in monitor mode.

        Returns True if:
        - Interface name ends with 'mon' (common convention)
        - iw reports type as 'monitor'
        """
        # Quick check by name convention
        if interface.endswith("mon"):
            return True

        # Check actual mode via iw
        if shutil.which("iw"):
            try:
                result = subprocess.run(
                    ["iw", interface, "info"],
                    capture_output=True,
                    text=True,
                    timeout=TOOL_TIMEOUT_DETECT,
                )
                if result.returncode == 0:
                    # Look for "type monitor" in output
                    if re.search(r"type\s+monitor", result.stdout, re.IGNORECASE):
                        return True
            except Exception:
                pass

        return False

    def _ensure_interface_up(self, interface: str) -> bool:
        """
        Ensure a WiFi interface is up before scanning.

        Attempts to bring the interface up using 'ip link set <iface> up',
        falling back to 'ifconfig <iface> up'.

        Args:
            interface: Network interface name.

        Returns:
            True if the interface was brought up (or was already up),
            False if we failed to bring it up.
        """
        # Check current state via /sys/class/net
        operstate_path = f"/sys/class/net/{interface}/operstate"
        try:
            with open(operstate_path) as f:
                state = f.read().strip()
            if state == "up":
                return True
            logger.info(f"Interface {interface} is '{state}', attempting to bring up")
        except FileNotFoundError:
            # Interface might not exist or /sys not available (non-Linux)
            return True
        except Exception:
            pass

        # Try ip link set up
        if shutil.which("ip"):
            try:
                result = subprocess.run(
                    ["ip", "link", "set", interface, "up"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    logger.info(f"Brought interface {interface} up via ip link")
                    time.sleep(1)  # Brief settle time
                    return True
                else:
                    logger.warning(f"ip link set {interface} up failed: {result.stderr.strip()}")
            except Exception as e:
                logger.warning(f"Failed to run ip link: {e}")

        # Fallback to ifconfig
        if shutil.which("ifconfig"):
            try:
                result = subprocess.run(
                    ["ifconfig", interface, "up"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    logger.info(f"Brought interface {interface} up via ifconfig")
                    time.sleep(1)
                    return True
                else:
                    logger.warning(f"ifconfig {interface} up failed: {result.stderr.strip()}")
            except Exception as e:
                logger.warning(f"Failed to run ifconfig: {e}")

        logger.error(f"Could not bring interface {interface} up")
        return False

    # =========================================================================
    # Quick Scan
    # =========================================================================

    def quick_scan(
        self,
        interface: str | None = None,
        timeout: float = DEFAULT_QUICK_SCAN_TIMEOUT,
    ) -> WiFiScanResult:
        """
        Perform a quick one-shot WiFi scan using system tools.

        Args:
            interface: Interface to scan on (uses default if None).
            timeout: Scan timeout in seconds.

        Returns:
            WiFiScanResult with discovered networks.
        """
        result = WiFiScanResult(scan_mode=SCAN_MODE_QUICK, started_at=datetime.now())

        # Get capabilities if not cached
        if not self._capabilities:
            self.check_capabilities()

        # Determine interface
        iface = interface or self._interface or self._capabilities.default_interface
        if not iface:
            result.error = "No WiFi interface available"
            result.is_complete = True
            return result

        result.interface = iface

        # Check if interface is in monitor mode (can't use quick scan tools on monitor interfaces)
        if self._is_monitor_mode_interface(iface):
            result.error = (
                f"Interface '{iface}' appears to be in monitor mode. "
                "Quick scan requires a managed mode interface. "
                "Either use a different interface, disable monitor mode, or use deep_scan() with airodump-ng."
            )
            result.is_complete = True
            result.warnings.append("Monitor mode interfaces don't support standard WiFi scanning")
            return result

        # Select and run parser based on platform/tools
        # Try multiple tools with fallback on Linux
        observations = []
        tool_used = None
        errors_encountered = []

        try:
            if self._capabilities.platform == "darwin":
                if self._capabilities.has_airport:
                    observations = self._scan_with_airport(iface, timeout)
                    tool_used = "airport"
                else:
                    result.error = "No WiFi scanning tool available on macOS (airport not found)"
                    result.is_complete = True
                    return result
            else:  # Linux - try tools in order with fallback
                # Ensure interface is up before scanning
                self._ensure_interface_up(iface)

                tools_to_try = []
                if self._capabilities.has_nmcli:
                    tools_to_try.append(("nmcli", self._scan_with_nmcli))
                if self._capabilities.has_iw:
                    tools_to_try.append(("iw", self._scan_with_iw))
                if self._capabilities.has_iwlist:
                    tools_to_try.append(("iwlist", self._scan_with_iwlist))

                if not tools_to_try:
                    result.error = "No WiFi scanning tools available. Install NetworkManager (nmcli) or wireless-tools (iw/iwlist)."
                    result.is_complete = True
                    return result

                interface_was_down = False
                for tool_name, scan_func in tools_to_try:
                    try:
                        logger.info(f"Attempting quick scan with {tool_name} on {iface}")
                        observations = scan_func(iface, timeout)
                        tool_used = tool_name
                        logger.info(f"Quick scan with {tool_name} found {len(observations)} networks")
                        break  # Success, stop trying other tools
                    except Exception as e:
                        error_msg = f"{tool_name}: {str(e)}"
                        errors_encountered.append(error_msg)
                        logger.warning(f"Quick scan with {tool_name} failed: {e}")
                        if "is down" in str(e):
                            interface_was_down = True
                        continue  # Try next tool

                # If all tools failed because interface was down, try bringing it up and retry
                if not tool_used and interface_was_down:
                    logger.info(f"Interface {iface} appears down, attempting to bring up and retry scan")
                    if self._ensure_interface_up(iface):
                        errors_encountered.clear()
                        for tool_name, scan_func in tools_to_try:
                            try:
                                logger.info(f"Retrying scan with {tool_name} on {iface} after bringing interface up")
                                observations = scan_func(iface, timeout)
                                tool_used = tool_name
                                logger.info(f"Retry scan with {tool_name} found {len(observations)} networks")
                                break
                            except Exception as e:
                                error_msg = f"{tool_name}: {str(e)}"
                                errors_encountered.append(error_msg)
                                logger.warning(f"Retry scan with {tool_name} failed: {e}")
                                continue

                if not tool_used:
                    # All tools failed
                    result.error = "All scan tools failed. " + "; ".join(errors_encountered)
                    if not self._capabilities.is_root:
                        result.error += " (Note: iw/iwlist require root privileges)"
                    result.is_complete = True
                    return result

            # Process observations into access points
            for obs in observations:
                self._process_observation(obs)

            # Build result
            with self._lock:
                result.access_points = list(self._access_points.values())

            # Add warnings for tools that failed before one succeeded
            for err in errors_encountered:
                result.warnings.append(err)

            # Generate channel stats
            result.channel_stats = self._calculate_channel_stats()
            result.recommendations = self._generate_recommendations(result.channel_stats)

            logger.info(f"Quick scan complete: {len(result.access_points)} networks found using {tool_used}")

        except subprocess.TimeoutExpired:
            result.error = f"Scan timed out after {timeout}s"
            result.warnings.append(f"Tool '{tool_used}' timed out")
        except Exception as e:
            result.error = str(e)
            logger.exception("Quick scan failed")

        result.completed_at = datetime.now()
        result.duration_seconds = (result.completed_at - result.started_at).total_seconds()
        result.is_complete = True

        return result

    def _scan_with_airport(self, interface: str, timeout: float) -> list[WiFiObservation]:
        """Scan using macOS airport utility."""
        from .parsers.airport import parse_airport_scan

        airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"

        try:
            result = subprocess.run(
                [airport_path, "-s"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() or f"airport returned code {result.returncode}"
                logger.warning(f"airport scan failed: {error_msg}")
                raise RuntimeError(f"airport scan failed: {error_msg}")

            if not result.stdout.strip():
                logger.warning("airport returned empty output")
                return []

            return parse_airport_scan(result.stdout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"airport scan timed out after {timeout}s")
        except FileNotFoundError:
            raise RuntimeError("airport utility not found")

    def _scan_with_nmcli(self, interface: str, timeout: float) -> list[WiFiObservation]:
        """Scan using NetworkManager nmcli."""
        from .parsers.nmcli import parse_nmcli_scan

        try:
            # Try to trigger a rescan first (might fail if interface not managed by NM)
            rescan_result = subprocess.run(
                ["nmcli", "device", "wifi", "rescan", "ifname", interface],
                capture_output=True,
                timeout=timeout / 2,
            )
            if rescan_result.returncode != 0:
                # Try without interface specification
                subprocess.run(
                    ["nmcli", "device", "wifi", "rescan"],
                    capture_output=True,
                    timeout=timeout / 2,
                )

            # Get results - try with interface first, then without
            result = subprocess.run(
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "BSSID,SSID,MODE,CHAN,FREQ,RATE,SIGNAL,SECURITY",
                    "device",
                    "wifi",
                    "list",
                    "ifname",
                    interface,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            # If interface-specific scan failed, try general scan
            if result.returncode != 0 or not result.stdout.strip():
                logger.debug(f"nmcli scan with interface {interface} failed, trying general scan")
                result = subprocess.run(
                    ["nmcli", "-t", "-f", "BSSID,SSID,MODE,CHAN,FREQ,RATE,SIGNAL,SECURITY", "device", "wifi", "list"],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )

            if result.returncode != 0:
                error_msg = result.stderr.strip() or f"nmcli returned code {result.returncode}"
                # Check for common issues
                if "not running" in error_msg.lower():
                    raise RuntimeError("NetworkManager is not running")
                elif "not found" in error_msg.lower() or "no such" in error_msg.lower():
                    raise RuntimeError(f"Interface {interface} not found or not managed by NetworkManager")
                else:
                    raise RuntimeError(f"nmcli scan failed: {error_msg}")

            if not result.stdout.strip():
                raise RuntimeError("nmcli returned no results (WiFi might be disabled or no networks in range)")

            return parse_nmcli_scan(result.stdout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"nmcli scan timed out after {timeout}s")
        except FileNotFoundError:
            raise RuntimeError("nmcli not found (NetworkManager not installed)")

    def _scan_with_iw(self, interface: str, timeout: float) -> list[WiFiObservation]:
        """Scan using iw."""
        from .parsers.iw import parse_iw_scan

        try:
            result = subprocess.run(
                ["iw", interface, "scan"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() or f"iw returned code {result.returncode}"
                # Check for common errors
                if "Operation not permitted" in error_msg or "Permission denied" in error_msg:
                    raise RuntimeError(f"iw scan requires root privileges: {error_msg}")
                elif "Network is down" in error_msg:
                    raise RuntimeError(f"Interface {interface} is down: {error_msg}")
                else:
                    raise RuntimeError(f"iw scan failed: {error_msg}")

            return parse_iw_scan(result.stdout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"iw scan timed out after {timeout}s")
        except FileNotFoundError:
            raise RuntimeError("iw not found (wireless-tools not installed)")

    def _scan_with_iwlist(self, interface: str, timeout: float) -> list[WiFiObservation]:
        """Scan using iwlist."""
        from .parsers.iwlist import parse_iwlist_scan

        try:
            result = subprocess.run(
                ["iwlist", interface, "scan"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() or f"iwlist returned code {result.returncode}"
                if "Operation not permitted" in error_msg or "Permission denied" in error_msg:
                    raise RuntimeError(f"iwlist scan requires root privileges: {error_msg}")
                elif "Network is down" in error_msg:
                    raise RuntimeError(f"Interface {interface} is down: {error_msg}")
                else:
                    raise RuntimeError(f"iwlist scan failed: {error_msg}")

            return parse_iwlist_scan(result.stdout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"iwlist scan timed out after {timeout}s")
        except FileNotFoundError:
            raise RuntimeError("iwlist not found (wireless-tools not installed)")

    # =========================================================================
    # Deep Scan (airodump-ng)
    # =========================================================================

    def start_deep_scan(
        self,
        interface: str | None = None,
        band: str = "all",
        channel: int | None = None,
        channels: list[int] | None = None,
    ) -> bool:
        """
        Start continuous deep scan with airodump-ng.

        Requires monitor mode interface and root privileges.

        Args:
            interface: Monitor mode interface (e.g., 'wlan0mon').
            band: Band to scan ('2.4', '5', 'all').
            channel: Specific channel to monitor (None for hopping).

        Returns:
            True if scan started successfully.
        """
        with self._lock:
            if self._status.is_scanning:
                return True

            # Get capabilities if not cached
            if not self._capabilities:
                self.check_capabilities()

            if not self._capabilities.can_deep_scan:
                self._status.error = "Deep scan not available: " + ", ".join(self._capabilities.issues)
                return False

            iface = interface or self._capabilities.monitor_interface
            if not iface:
                self._status.error = "No monitor mode interface available"
                return False

            # Start airodump-ng in background thread
            self._deep_scan_stop_event.clear()
            self._deep_scan_thread = threading.Thread(
                target=self._run_deep_scan,
                args=(iface, band, channel, channels),
                daemon=True,
            )
            self._deep_scan_thread.start()

            self._status = WiFiScanStatus(
                is_scanning=True,
                scan_mode=SCAN_MODE_DEEP,
                interface=iface,
                started_at=datetime.now(),
            )

            self._queue_event(
                {
                    "type": "scan_started",
                    "mode": SCAN_MODE_DEEP,
                    "interface": iface,
                }
            )

            # Auto-start deauth detector
            self._start_deauth_detector(iface)

            return True

    def stop_deep_scan(self) -> bool:
        """
        Stop the deep scan.

        Returns:
            True if scan was stopped.
        """
        cleanup_process: subprocess.Popen | None = None
        cleanup_thread: threading.Thread | None = None
        cleanup_detector = None

        with self._lock:
            if not self._status.is_scanning:
                return True

            self._deep_scan_stop_event.set()
            cleanup_process = self._deep_scan_process
            cleanup_thread = self._deep_scan_thread
            cleanup_detector = self._deauth_detector
            self._deauth_detector = None
            self._deep_scan_process = None
            self._deep_scan_thread = None

            self._status.is_scanning = False
            self._status.error = None

            self._queue_event(
                {
                    "type": "scan_stopped",
                    "mode": SCAN_MODE_DEEP,
                }
            )

        cleanup_start = time.perf_counter()

        def _finalize_stop(
            process: subprocess.Popen | None,
            scan_thread: threading.Thread | None,
            detector,
        ) -> None:
            if detector:
                try:
                    detector.stop()
                    logger.info("Deauth detector stopped")
                    self._queue_event({"type": "deauth_detector_stopped"})
                except Exception as exc:
                    logger.error(f"Error stopping deauth detector: {exc}")

            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=1.5)
                except Exception:
                    with contextlib.suppress(Exception):
                        process.kill()

            if scan_thread and scan_thread.is_alive():
                scan_thread.join(timeout=1.5)

            elapsed_ms = (time.perf_counter() - cleanup_start) * 1000.0
            logger.info(f"Deep scan stop finalized in {elapsed_ms:.1f}ms")

        threading.Thread(
            target=_finalize_stop,
            args=(cleanup_process, cleanup_thread, cleanup_detector),
            daemon=True,
            name="wifi-deep-stop",
        ).start()

        return True

    def _run_deep_scan(
        self,
        interface: str,
        band: str,
        channel: int | None,
        channels: list[int] | None,
    ):
        """Background thread for running airodump-ng."""
        import tempfile

        from .parsers.airodump import parse_airodump_csv

        # Create temp directory for output files
        with tempfile.TemporaryDirectory(prefix="wifi_scan_") as tmpdir:
            output_prefix = os.path.join(tmpdir, "scan")

            # Build command
            cmd = ["airodump-ng", "-w", output_prefix, "--output-format", "csv"]

            if channels:
                cmd.extend(["-c", ",".join(str(c) for c in channels)])
            elif channel:
                cmd.extend(["-c", str(channel)])
            elif band == "2.4":
                cmd.extend(["--band", "bg"])
            elif band == "5":
                cmd.extend(["--band", "a"])
            else:
                cmd.extend(["--band", "abg"])

            cmd.append(interface)

            logger.info(f"Starting airodump-ng: {' '.join(cmd)}")

            process: subprocess.Popen | None = None
            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                should_track_process = False
                with self._lock:
                    # Only expose the process handle if this run has not been
                    # replaced by a newer deep scan session.
                    if self._status.is_scanning and not self._deep_scan_stop_event.is_set():
                        should_track_process = True
                        self._deep_scan_process = process
                if not should_track_process:
                    try:
                        process.terminate()
                        process.wait(timeout=1.0)
                    except Exception:
                        with contextlib.suppress(Exception):
                            process.kill()
                    return

                csv_file = f"{output_prefix}-01.csv"

                # Poll CSV file for updates
                while not self._deep_scan_stop_event.is_set():
                    time.sleep(1.0)

                    if os.path.exists(csv_file):
                        try:
                            networks, clients = parse_airodump_csv(csv_file)

                            for obs in networks:
                                self._process_observation(obs)

                            for client_data in clients:
                                self._process_client(client_data)

                            # Update status
                            with self._lock:
                                self._status.networks_found = len(self._access_points)
                                self._status.clients_found = len(self._clients)

                        except Exception as e:
                            logger.debug(f"Error parsing airodump CSV: {e}")

            except Exception as e:
                logger.exception(f"Deep scan error: {e}")
                self._queue_event(
                    {
                        "type": "scan_error",
                        "error": str(e),
                    }
                )
            finally:
                with self._lock:
                    if process is not None and self._deep_scan_process is process:
                        self._deep_scan_process = None

    # =========================================================================
    # Observation Processing
    # =========================================================================

    def _process_observation(self, obs: WiFiObservation):
        """Process a WiFi observation and update access point data."""
        with self._lock:
            bssid = obs.bssid.upper()

            if bssid in self._access_points:
                ap = self._access_points[bssid]
                self._update_access_point(ap, obs)
            else:
                ap = self._create_access_point(obs)
                self._access_points[bssid] = ap

            # Check if new (not in baseline)
            if self._baseline_networks and bssid not in self._baseline_networks:
                ap.is_new = True

            # Queue update event
            self._queue_event(
                {
                    "type": "network_update",
                    "network": ap.to_summary_dict(),
                }
            )

            # Callback
            if self._on_network_updated:
                try:
                    self._on_network_updated(ap)
                except Exception as e:
                    logger.debug(f"Network callback error: {e}")

        # Activity feed, at ingestion: every v2 scan backend passes through here.
        # Not direct: the legacy /wifi stream reaches the feed only through
        # process_event(), and the SSE copy of this sighting has the same BSSID,
        # so the per-identifier interval absorbs it.
        try:
            from utils.observations import emit_observation

            emit_observation(
                "wifi",
                bssid,
                rssi=obs.rssi,
                summary=obs.essid or "(hidden)",
                raw={"bssid": bssid, "essid": obs.essid, "channel": obs.channel, "rssi": obs.rssi},
            )
        except Exception as e:
            logger.debug(f"Observation emit failed: {e}")

    def _create_access_point(self, obs: WiFiObservation) -> WiFiAccessPoint:
        """Create new access point from observation."""
        now = datetime.now()
        ap = WiFiAccessPoint(
            bssid=obs.bssid.upper(),
            essid=obs.essid,
            is_hidden=obs.is_hidden,
            channel=obs.channel,
            frequency_mhz=obs.frequency_mhz,
            band=obs.band,
            width=obs.width,
            security=obs.security,
            cipher=obs.cipher,
            auth=obs.auth,
            first_seen=now,
            last_seen=now,
            seen_count=1,
            vendor=get_vendor_from_mac(obs.bssid),
        )

        if obs.rssi is not None:
            ap.rssi_current = obs.rssi
            ap.rssi_samples = [(now, obs.rssi)]
            ap.rssi_min = obs.rssi
            ap.rssi_max = obs.rssi
            ap.rssi_median = float(obs.rssi)
            ap.rssi_ema = float(obs.rssi)
            ap.signal_band = get_signal_band(obs.rssi)
            ap.proximity_band = get_proximity_band(obs.rssi)

        ap.beacon_count = obs.beacon_count
        ap.data_count = obs.data_count

        return ap

    def _update_access_point(self, ap: WiFiAccessPoint, obs: WiFiObservation):
        """Update existing access point with new observation."""
        now = datetime.now()
        ap.last_seen = now
        ap.seen_count += 1

        # Update channel/band if now known (airodump-ng may report -1 or 0 before resolving)
        if obs.channel and not ap.channel:
            ap.channel = obs.channel
            ap.frequency_mhz = obs.frequency_mhz
            ap.band = get_band_from_channel(obs.channel)

        # Update ESSID if revealed
        if obs.essid and ap.is_hidden:
            ap.revealed_essid = obs.essid
            self._queue_event(
                {
                    "type": "hidden_revealed",
                    "bssid": ap.bssid,
                    "revealed_essid": obs.essid,
                }
            )

        # Update RSSI stats
        if obs.rssi is not None:
            ap.rssi_current = obs.rssi
            ap.rssi_samples.append((now, obs.rssi))

            # Trim samples
            if len(ap.rssi_samples) > MAX_RSSI_SAMPLES:
                ap.rssi_samples = ap.rssi_samples[-MAX_RSSI_SAMPLES:]

            # Update stats
            rssi_values = [r for _, r in ap.rssi_samples]
            ap.rssi_min = min(rssi_values)
            ap.rssi_max = max(rssi_values)
            ap.rssi_median = float(sorted(rssi_values)[len(rssi_values) // 2])

            # Update EMA
            if ap.rssi_ema is None:
                ap.rssi_ema = float(obs.rssi)
            else:
                ap.rssi_ema = WIFI_EMA_ALPHA * obs.rssi + (1 - WIFI_EMA_ALPHA) * ap.rssi_ema

            # Calculate variance
            if len(rssi_values) >= 2:
                mean = sum(rssi_values) / len(rssi_values)
                ap.rssi_variance = sum((r - mean) ** 2 for r in rssi_values) / len(rssi_values)

            ap.signal_band = get_signal_band(obs.rssi)
            ap.proximity_band = get_proximity_band(obs.rssi)

        # Update traffic counters
        if obs.beacon_count:
            ap.beacon_count = obs.beacon_count
        if obs.data_count:
            ap.data_count = obs.data_count

        # Calculate seen rate
        duration = (now - ap.first_seen).total_seconds()
        if duration > 0:
            ap.seen_rate = (ap.seen_count / duration) * 60  # per minute

    def _process_client(self, client_data: dict):
        """Process client data from airodump-ng."""
        mac = client_data.get("mac", "").upper()
        if not mac or mac == "(not associated)":
            return

        with self._lock:
            if mac in self._clients:
                client = self._clients[mac]
                self._update_client(client, client_data)
            else:
                client = self._create_client(client_data)
                self._clients[mac] = client

            # Queue update event
            self._queue_event(
                {
                    "type": "client_update",
                    "client": client.to_dict(),
                }
            )

            # Process probe requests
            probed = client_data.get("probed_essids", [])
            for ssid in probed:
                if ssid and ssid not in client.probed_ssids:
                    client.probed_ssids.append(ssid)
                    client.probe_timestamps[ssid] = datetime.now()

                    probe = WiFiProbeRequest(
                        timestamp=datetime.now(),
                        client_mac=mac,
                        probed_ssid=ssid,
                        rssi=client.rssi_current,
                        client_vendor=client.vendor,
                    )
                    self._probe_requests.append(probe)

                    self._queue_event(
                        {
                            "type": "probe_request",
                            "probe": probe.to_dict(),
                        }
                    )

            # Callback
            if self._on_client_updated:
                try:
                    self._on_client_updated(client)
                except Exception as e:
                    logger.debug(f"Client callback error: {e}")

    def _create_client(self, data: dict) -> WiFiClient:
        """Create new client from data."""
        now = datetime.now()
        mac = data.get("mac", "").upper()

        client = WiFiClient(
            mac=mac,
            vendor=get_vendor_from_mac(mac),
            first_seen=now,
            last_seen=now,
            seen_count=1,
        )

        rssi = data.get("rssi")
        if rssi is not None:
            client.rssi_current = rssi
            client.rssi_samples = [(now, rssi)]
            client.rssi_min = rssi
            client.rssi_max = rssi
            client.rssi_median = float(rssi)
            client.rssi_ema = float(rssi)
            client.signal_band = get_signal_band(rssi)
            client.proximity_band = get_proximity_band(rssi)

        bssid = data.get("bssid")
        if bssid and bssid != "(not associated)":
            client.associated_bssid = bssid.upper()
            client.is_associated = True

            # Update AP client count
            if client.associated_bssid in self._access_points:
                self._access_points[client.associated_bssid].client_count += 1

        return client

    def _update_client(self, client: WiFiClient, data: dict):
        """Update existing client with new data."""
        now = datetime.now()
        client.last_seen = now
        client.seen_count += 1

        rssi = data.get("rssi")
        if rssi is not None:
            client.rssi_current = rssi
            client.rssi_samples.append((now, rssi))

            if len(client.rssi_samples) > MAX_RSSI_SAMPLES:
                client.rssi_samples = client.rssi_samples[-MAX_RSSI_SAMPLES:]

            rssi_values = [r for _, r in client.rssi_samples]
            client.rssi_min = min(rssi_values)
            client.rssi_max = max(rssi_values)
            client.rssi_median = float(sorted(rssi_values)[len(rssi_values) // 2])

            if client.rssi_ema is None:
                client.rssi_ema = float(rssi)
            else:
                client.rssi_ema = WIFI_EMA_ALPHA * rssi + (1 - WIFI_EMA_ALPHA) * client.rssi_ema

            client.signal_band = get_signal_band(rssi)
            client.proximity_band = get_proximity_band(rssi)

    # =========================================================================
    # Channel Analysis
    # =========================================================================

    def _calculate_channel_stats(self) -> list[ChannelStats]:
        """Calculate statistics for each channel."""
        from .constants import (
            CHANNEL_FREQUENCIES,
            get_band_from_channel,
        )

        stats_map: dict[int, ChannelStats] = {}

        with self._lock:
            for ap in self._access_points.values():
                if ap.channel is None:
                    continue

                if ap.channel not in stats_map:
                    stats_map[ap.channel] = ChannelStats(
                        channel=ap.channel,
                        band=get_band_from_channel(ap.channel),
                        frequency_mhz=CHANNEL_FREQUENCIES.get(ap.channel),
                    )

                stats = stats_map[ap.channel]
                stats.ap_count += 1
                stats.client_count += ap.client_count

                if ap.rssi_current is not None:
                    if stats.rssi_min is None or ap.rssi_current < stats.rssi_min:
                        stats.rssi_min = ap.rssi_current
                    if stats.rssi_max is None or ap.rssi_current > stats.rssi_max:
                        stats.rssi_max = ap.rssi_current

        # Calculate averages and utilization scores
        for stats in stats_map.values():
            if stats.ap_count > 0:
                # Simple utilization score based on AP and client density
                from .constants import CHANNEL_WEIGHT_AP_COUNT, CHANNEL_WEIGHT_CLIENT_COUNT

                stats.utilization_score = (
                    (stats.ap_count * CHANNEL_WEIGHT_AP_COUNT) + (stats.client_count * CHANNEL_WEIGHT_CLIENT_COUNT)
                ) / 10.0  # Normalize
                stats.utilization_score = min(1.0, stats.utilization_score)

        return sorted(stats_map.values(), key=lambda s: s.channel)

    def _generate_recommendations(self, stats: list[ChannelStats]) -> list[ChannelRecommendation]:
        """Generate channel recommendations."""
        from .constants import (
            BAND_2_4_GHZ,
            BAND_5_GHZ,
            NON_OVERLAPPING_2_4_GHZ,
            NON_OVERLAPPING_5_GHZ,
        )

        recommendations = []

        # Create lookup for existing stats
        stats_map = {s.channel: s for s in stats}

        # Score non-overlapping channels
        for channel in NON_OVERLAPPING_2_4_GHZ:
            s = stats_map.get(channel)
            score = s.utilization_score if s else 0.0
            recommendations.append(
                ChannelRecommendation(
                    channel=channel,
                    band=BAND_2_4_GHZ,
                    score=score,
                    reason=f"{s.ap_count if s else 0} APs on channel" if s else "No APs detected",
                    is_dfs=False,
                )
            )

        for channel in NON_OVERLAPPING_5_GHZ:
            s = stats_map.get(channel)
            score = s.utilization_score if s else 0.0
            is_dfs = 52 <= channel <= 144
            recommendations.append(
                ChannelRecommendation(
                    channel=channel,
                    band=BAND_5_GHZ,
                    score=score,
                    reason=f"{s.ap_count if s else 0} APs on channel" + (" (DFS)" if is_dfs else ""),
                    is_dfs=is_dfs,
                )
            )

        # Sort by score (lower is better)
        recommendations.sort(key=lambda r: (r.score, r.is_dfs))

        # Add rank
        for i, rec in enumerate(recommendations):
            rec.recommendation_rank = i + 1

        return recommendations

    # =========================================================================
    # Event Streaming
    # =========================================================================

    def _queue_event(self, event: dict):
        """Add event to the SSE queue, and send it through the event
        pipeline once, whether or not a browser is subscribed."""
        from utils.event_pipeline import submit

        submit("wifi", event, event.get("type"))
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            # Drop oldest event
            try:
                self._event_queue.get_nowait()
                self._event_queue.put_nowait(event)
            except Exception:
                pass

    def get_event_stream(self) -> Generator[dict, None, None]:
        """Generate events for SSE streaming."""
        while True:
            try:
                event = self._event_queue.get(timeout=1.0)
                yield event
            except queue.Empty:
                yield {"type": "keepalive"}
            except Exception:
                break

    # =========================================================================
    # Baseline Management
    # =========================================================================

    def set_baseline(self):
        """Mark current networks as baseline (known networks)."""
        with self._lock:
            self._baseline_networks = set(self._access_points.keys())
            self._baseline_set_at = datetime.now()

            for ap in self._access_points.values():
                ap.in_baseline = True
                ap.is_new = False

    def clear_baseline(self):
        """Clear the baseline."""
        with self._lock:
            self._baseline_networks.clear()
            self._baseline_set_at = None

            for ap in self._access_points.values():
                ap.in_baseline = False

    # =========================================================================
    # Data Access
    # =========================================================================

    def get_network(self, bssid: str) -> WiFiAccessPoint | None:
        """Get a specific network by BSSID."""
        with self._lock:
            return self._access_points.get(bssid.upper())

    def get_client(self, mac: str) -> WiFiClient | None:
        """Get a specific client by MAC."""
        with self._lock:
            return self._clients.get(mac.upper())

    def get_status(self) -> WiFiScanStatus:
        """Get current scan status."""
        with self._lock:
            self._status.networks_found = len(self._access_points)
            self._status.clients_found = len(self._clients)
            return self._status

    def clear_data(self):
        """Clear all discovered data."""
        with self._lock:
            self._access_points.clear()
            self._clients.clear()
            self._probe_requests.clear()

    # =========================================================================
    # TSCM Compatibility
    # =========================================================================

    def get_networks_legacy_format(self) -> list[dict]:
        """
        Get networks in legacy format for TSCM compatibility.

        Returns list of dicts with: bssid, essid, power, channel, privacy
        """
        with self._lock:
            return [ap.to_legacy_dict() for ap in self._access_points.values()]

    # =========================================================================
    # Deauth Detection Integration
    # =========================================================================

    def _start_deauth_detector(self, interface: str):
        """Start deauth detector on the given interface."""
        try:
            from .deauth_detector import DeauthDetector
        except ImportError as e:
            logger.warning(f"Could not import DeauthDetector (scapy not installed?): {e}")
            return

        if self._deauth_detector and self._deauth_detector.is_running:
            logger.debug("Deauth detector already running")
            return

        def event_callback(event: dict):
            """Handle deauth events and forward to queue."""
            self._queue_event(event)
            # Also store in app-level DataStore if available
            try:
                import app as app_module

                if hasattr(app_module, "deauth_alerts") and event.get("type") == "deauth_alert":
                    alert_id = event.get("id", str(time.time()))
                    app_module.deauth_alerts[alert_id] = event
                if hasattr(app_module, "deauth_detector_queue"):
                    with contextlib.suppress(queue.Full):
                        app_module.deauth_detector_queue.put_nowait(event)
            except Exception as e:
                logger.debug(f"Error storing deauth alert: {e}")

        def get_networks() -> dict:
            """Get current networks for cross-reference."""
            with self._lock:
                return {bssid: ap.to_summary_dict() for bssid, ap in self._access_points.items()}

        def get_clients() -> dict:
            """Get current clients for cross-reference."""
            with self._lock:
                return {mac: client.to_dict() for mac, client in self._clients.items()}

        try:
            self._deauth_detector = DeauthDetector(
                interface=interface,
                event_callback=event_callback,
                get_networks=get_networks,
                get_clients=get_clients,
            )
            self._deauth_detector.start()
            logger.info(f"Deauth detector started on {interface}")

            self._queue_event(
                {
                    "type": "deauth_detector_started",
                    "interface": interface,
                }
            )
        except Exception as e:
            logger.error(f"Failed to start deauth detector: {e}")
            self._queue_event(
                {
                    "type": "deauth_error",
                    "error": f"Failed to start deauth detector: {e}",
                }
            )

    def _stop_deauth_detector(self):
        """Stop the deauth detector."""
        if self._deauth_detector:
            try:
                self._deauth_detector.stop()
                logger.info("Deauth detector stopped")
                self._queue_event(
                    {
                        "type": "deauth_detector_stopped",
                    }
                )
            except Exception as e:
                logger.error(f"Error stopping deauth detector: {e}")
            finally:
                self._deauth_detector = None

    @property
    def deauth_detector(self) -> DeauthDetector | None:
        """Get the deauth detector instance."""
        return self._deauth_detector

    def get_deauth_alerts(self, limit: int = 100) -> list[dict]:
        """Get recent deauth alerts."""
        if self._deauth_detector:
            return self._deauth_detector.get_alerts(limit)
        return []

    def clear_deauth_alerts(self):
        """Clear deauth alert history."""
        if self._deauth_detector:
            self._deauth_detector.clear_alerts()
        # Also clear from app-level store
        try:
            import app as app_module

            if hasattr(app_module, "deauth_alerts"):
                app_module.deauth_alerts.clear()
        except Exception:
            pass


# =============================================================================
# Module-level functions
# =============================================================================


def get_wifi_scanner(interface: str | None = None) -> UnifiedWiFiScanner:
    """
    Get or create the global WiFi scanner instance.

    Args:
        interface: WiFi interface name.

    Returns:
        UnifiedWiFiScanner instance.
    """
    global _scanner_instance

    with _scanner_lock:
        if _scanner_instance is None:
            _scanner_instance = UnifiedWiFiScanner(interface)
        return _scanner_instance


def reset_wifi_scanner():
    """Reset the global scanner instance."""
    global _scanner_instance

    with _scanner_lock:
        if _scanner_instance:
            _scanner_instance.stop_deep_scan()
            _scanner_instance.clear_data()
        _scanner_instance = None
