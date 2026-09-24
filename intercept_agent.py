#!/usr/bin/env python3
"""
INTERCEPT Agent - Remote node for distributed signal intelligence.

This agent runs on remote nodes and exposes Intercept's capabilities via REST API.
It can push data to a central controller or respond to pull requests.

Usage:
    python intercept_agent.py [--port 8020] [--config intercept_agent.cfg]
"""

from __future__ import annotations

import argparse
import configparser
import contextlib
import json
import logging
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import dependency checking from Intercept utils
try:
    from utils.dependencies import TOOL_DEPENDENCIES, check_all_dependencies

    HAS_DEPENDENCIES_MODULE = True
except ImportError:
    HAS_DEPENDENCIES_MODULE = False

# Shared capability detection (extracted so app and agent cannot drift)
try:
    from utils.capabilities import MODE_DEPENDENCY_MAP, detect_interfaces, detect_mode_availability

    HAS_CAPABILITIES_MODULE = True
except ImportError:
    HAS_CAPABILITIES_MODULE = False

# Share the controller's aircraft TTL so agent and local mode age contacts
# out at the same rate.
try:
    from utils.constants import MAX_AIRCRAFT_AGE_SECONDS
except ImportError:
    MAX_AIRCRAFT_AGE_SECONDS = 300

# Import TSCM modules for consistent analysis (same as local mode)
try:
    from utils.tscm.correlation import CorrelationEngine
    from utils.tscm.detector import ThreatDetector

    HAS_TSCM_MODULES = True
except ImportError:
    HAS_TSCM_MODULES = False
    ThreatDetector = None
    CorrelationEngine = None

# Per-device defaults (name, PPM, gain, bias-T) from this host's settings,
# validated on the same path the controller's routes use
try:
    from utils.sdr.device_config import apply_device_defaults, display_names
    from utils.validation import validate_gain, validate_ppm

    HAS_DEVICE_CONFIG = True
except ImportError:
    HAS_DEVICE_CONFIG = False

# Modes that tune an SDR and so take its PPM, gain and bias-T defaults
SDR_MODES = {"sensor", "adsb", "pager", "ais", "acars", "aprs", "rtlamr", "dsc", "listening_post"}

# Import database functions for baseline support (same as local mode)
try:
    from utils.database import get_active_tscm_baseline, get_tscm_baseline

    HAS_BASELINE_DB = True
except ImportError:
    HAS_BASELINE_DB = False
    get_tscm_baseline = None
    get_active_tscm_baseline = None

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("intercept.agent")

# Version
AGENT_VERSION = "1.0.0"

# =============================================================================
# Configuration
# =============================================================================


class AgentConfig:
    """Agent configuration loaded from INI file or defaults."""

    def __init__(self):
        # Agent settings
        self.name: str = socket.gethostname()
        self.port: int = 8020
        self.allowed_ips: list[str] = []
        self.allow_cors: bool = False

        # Controller settings
        self.controller_url: str = ""
        self.controller_api_key: str = ""
        self.push_enabled: bool = False
        self.push_interval: int = 5

        # Mode settings (all enabled by default)
        self.modes_enabled: dict[str, bool] = {
            "pager": True,
            "sensor": True,
            "adsb": True,
            "ais": True,
            "acars": True,
            "aprs": True,
            "wifi": True,
            "bluetooth": True,
            "dsc": True,
            "rtlamr": True,
            "tscm": True,
            "satellite": True,
            "listening_post": True,
        }

    def load_from_file(self, filepath: str) -> bool:
        """Load configuration from INI file."""
        if not os.path.isfile(filepath):
            logger.warning(f"Config file not found: {filepath}")
            return False

        parser = configparser.ConfigParser()
        try:
            parser.read(filepath)

            # Agent section
            if parser.has_section("agent"):
                if parser.has_option("agent", "name"):
                    self.name = parser.get("agent", "name")
                if parser.has_option("agent", "port"):
                    self.port = parser.getint("agent", "port")
                if parser.has_option("agent", "allowed_ips"):
                    ips = parser.get("agent", "allowed_ips")
                    if ips.strip():
                        self.allowed_ips = [ip.strip() for ip in ips.split(",")]
                if parser.has_option("agent", "allow_cors"):
                    self.allow_cors = parser.getboolean("agent", "allow_cors")

            # Controller section
            if parser.has_section("controller"):
                if parser.has_option("controller", "url"):
                    self.controller_url = parser.get("controller", "url").rstrip("/")
                if parser.has_option("controller", "api_key"):
                    self.controller_api_key = parser.get("controller", "api_key")
                if parser.has_option("controller", "push_enabled"):
                    self.push_enabled = parser.getboolean("controller", "push_enabled")
                if parser.has_option("controller", "push_interval"):
                    self.push_interval = parser.getint("controller", "push_interval")

            # Modes section
            if parser.has_section("modes"):
                for mode in self.modes_enabled:
                    if parser.has_option("modes", mode):
                        self.modes_enabled[mode] = parser.getboolean("modes", mode)

            logger.info(f"Loaded configuration from {filepath}")
            return True

        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return False

    def to_dict(self) -> dict:
        """Convert config to dictionary."""
        return {
            "name": self.name,
            "port": self.port,
            "allowed_ips": self.allowed_ips,
            "allow_cors": self.allow_cors,
            "controller_url": self.controller_url,
            "push_enabled": self.push_enabled,
            "push_interval": self.push_interval,
            "modes_enabled": self.modes_enabled,
        }


# Global config
config = AgentConfig()


# =============================================================================
# GPS Integration
# =============================================================================


class GPSManager:
    """Manages GPS position via gpsd."""

    def __init__(self):
        self._client = None
        self._position = None
        self._lock = threading.Lock()
        self._running = False

    @property
    def position(self) -> dict | None:
        """Get current GPS position."""
        with self._lock:
            if self._position:
                return {
                    "lat": self._position.latitude,
                    "lon": self._position.longitude,
                    "altitude": self._position.altitude,
                    "speed": self._position.speed,
                    "heading": self._position.heading,
                    "fix_quality": self._position.fix_quality,
                }
            return None

    def start(self, host: str = "localhost", port: int = 2947) -> bool:
        """Start GPS client connection to gpsd."""
        try:
            from utils.gps import GPSDClient

            self._client = GPSDClient(host, port)
            self._client.add_callback(self._on_position_update)
            success = self._client.start()
            if success:
                self._running = True
                logger.info(f"GPS connected to gpsd at {host}:{port}")
            return success
        except ImportError:
            logger.warning("GPS module not available")
            return False
        except Exception as e:
            logger.error(f"Failed to start GPS: {e}")
            return False

    def stop(self):
        """Stop GPS client."""
        if self._client:
            self._client.stop()
            self._client = None
        self._running = False

    def _on_position_update(self, position):
        """Callback for GPS position updates."""
        with self._lock:
            self._position = position

    @property
    def is_running(self) -> bool:
        return self._running


# Global GPS manager
gps_manager = GPSManager()


# =============================================================================
# Controller Push Client
# =============================================================================


class ControllerPushClient(threading.Thread):
    """Daemon thread that pushes scan data to the controller."""

    def __init__(self, cfg: AgentConfig):
        super().__init__()
        self.daemon = True
        self.cfg = cfg
        self.queue: queue.Queue = queue.Queue(maxsize=200)
        self.running = False
        self.stop_event = threading.Event()

    def enqueue(self, scan_type: str, payload: dict, interface: str = None):
        """Add data to push queue."""
        if not self.cfg.push_enabled or not self.cfg.controller_url:
            return

        item = {
            "agent_name": self.cfg.name,
            "scan_type": scan_type,
            "interface": interface,
            "payload": payload,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "attempts": 0,
        }

        try:
            self.queue.put_nowait(item)
        except queue.Full:
            logger.warning("Push queue full, dropping payload")

    def run(self):
        """Main push loop."""
        import requests

        self.running = True
        logger.info(f"Push client started, target: {self.cfg.controller_url}")

        while not self.stop_event.is_set():
            try:
                item = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if item is None:
                continue

            endpoint = f"{self.cfg.controller_url}/controller/api/ingest"
            headers = {"Content-Type": "application/json"}
            if self.cfg.controller_api_key:
                headers["X-API-Key"] = self.cfg.controller_api_key

            body = {
                "agent_name": item["agent_name"],
                "scan_type": item["scan_type"],
                "interface": item["interface"],
                "payload": item["payload"],
                "received_at": item["received_at"],
            }

            try:
                response = requests.post(endpoint, json=body, headers=headers, timeout=5)
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}")
                logger.debug(f"Pushed {item['scan_type']} data to controller")
            except Exception as e:
                item["attempts"] += 1
                if item["attempts"] < 3 and not self.stop_event.is_set():
                    with contextlib.suppress(queue.Full):
                        self.queue.put_nowait(item)
                else:
                    logger.warning(f"Failed to push after {item['attempts']} attempts: {e}")
            finally:
                self.queue.task_done()

        self.running = False
        logger.info("Push client stopped")

    def stop(self):
        """Stop the push client."""
        self.stop_event.set()


# Global push client
push_client: ControllerPushClient | None = None


# =============================================================================
# Mode Manager - Uses Intercept's existing utilities and tools
# =============================================================================


class ModeManager:
    """
    Manages mode state using Intercept's existing infrastructure.

    This assumes Intercept (or its utilities) is installed on the agent host.
    The agent imports and uses the existing modules rather than reimplementing
    tool execution logic.
    """

    def __init__(self):
        self.running_modes: dict[str, dict] = {}
        self.data_snapshots: dict[str, list] = {}
        self.locks: dict[str, threading.Lock] = {}
        self._capabilities: dict | None = None
        # Process tracking per mode
        self.processes: dict[str, subprocess.Popen] = {}
        self.output_threads: dict[str, threading.Thread] = {}
        self.stop_events: dict[str, threading.Event] = {}
        # Data queues for each mode (for real-time collection)
        self.data_queues: dict[str, queue.Queue] = {}
        # WiFi-specific state
        self.wifi_networks: dict[str, dict] = {}
        self.wifi_clients: dict[str, dict] = {}
        # ADS-B specific state
        self.adsb_aircraft: dict[str, dict] = {}
        # Bluetooth specific state
        self.bluetooth_devices: dict[str, dict] = {}
        # Lazy-loaded Intercept utilities
        self._sdr_factory = None
        self._dependencies = None

    def _get_sdr_factory(self):
        """Lazy-load SDRFactory from Intercept's utils."""
        if self._sdr_factory is None:
            try:
                from utils.sdr import SDRFactory

                self._sdr_factory = SDRFactory
            except ImportError:
                logger.warning("SDRFactory not available - SDR features disabled")
        return self._sdr_factory

    def _get_dependencies(self):
        """Lazy-load dependencies module from Intercept's utils."""
        if self._dependencies is None:
            try:
                from utils import dependencies

                self._dependencies = dependencies
            except ImportError:
                logger.warning("Dependencies module not available")
        return self._dependencies

    def _get_tool_path(self, tool_name: str) -> str | None:
        """Get tool path using Intercept's dependency module."""
        deps = self._get_dependencies()
        if deps and hasattr(deps, "get_tool_path"):
            return deps.get_tool_path(tool_name)
        return shutil.which(tool_name)

    def detect_capabilities(self) -> dict:
        """Detect available tools and hardware using Intercept's utilities."""
        if self._capabilities is not None:
            return self._capabilities

        capabilities = {
            "modes": {},
            "devices": [],
            "interfaces": {
                "wifi_interfaces": [],
                "bt_adapters": [],
                "sdr_devices": [],
            },
            "agent_version": AGENT_VERSION,
            "gps": gps_manager.is_running,
            "gps_position": gps_manager.position,
            "tool_details": {},  # Detailed tool status
        }

        if not HAS_CAPABILITIES_MODULE:
            # Degrade gracefully on stripped-down hosts where utils.capabilities
            # (or its dependency chain) could not be imported.
            logger.warning("capability detection unavailable: utils.capabilities could not be imported")
        else:
            # Detect interfaces via shared capability detection
            capabilities["interfaces"] = detect_interfaces()

            # Probe dependencies once; reuse for both mode availability and tool_details.
            # Note: utils.capabilities imports check_all_dependencies at module level, so if
            # utils.dependencies were missing, the capabilities import would have failed and
            # HAS_CAPABILITIES_MODULE would be False. Inside this True branch, HAS_DEPENDENCIES_MODULE
            # is therefore necessarily True as well — the inner guard is not needed.
            dep_status: dict | None = None
            try:
                dep_status = check_all_dependencies()
            except Exception as e:
                logger.warning(f"Tool detail collection failed: {e}")

            # Detect mode availability via shared capability detection
            raw_modes = detect_mode_availability(dep_status)
            for mode, ready in raw_modes.items():
                # Apply this agent's per-mode config gating on top of tool readiness
                capabilities["modes"][mode] = ready if config.modes_enabled.get(mode, True) else False

            # Populate detailed tool info for modes tracked in dependencies.py
            if dep_status is not None:
                for dep_mode, cap_mode in MODE_DEPENDENCY_MAP.items():
                    if dep_mode in dep_status:
                        mode_info = dep_status[dep_mode]
                        capabilities["tool_details"][cap_mode] = {
                            "name": mode_info["name"],
                            "ready": mode_info["ready"],
                            "missing_required": mode_info["missing_required"],
                            "tools": mode_info["tools"],
                        }

        # Use Intercept's SDR detection
        sdr_factory = self._get_sdr_factory()
        if sdr_factory:
            try:
                devices = sdr_factory.detect_devices()
                names = display_names(devices) if HAS_DEVICE_CONFIG else [d.name for d in devices]
                sdr_list = []
                for sdr, name in zip(devices, names):
                    sdr_dict = sdr.to_dict()
                    sdr_dict["name"] = name
                    # Create friendly display name
                    display_name = name
                    if name == sdr.name and sdr.serial and sdr.serial not in ("N/A", "Unknown"):
                        display_name = f"{sdr.name} (SN: {sdr.serial[-8:]})"
                    sdr_dict["display_name"] = display_name
                    sdr_list.append(sdr_dict)
                capabilities["devices"] = sdr_list
                capabilities["interfaces"]["sdr_devices"] = sdr_list
            except Exception as e:
                logger.warning(f"SDR device detection failed: {e}")

        self._capabilities = capabilities
        return capabilities

    def get_status(self) -> dict:
        """Get overall agent status."""
        # Build running modes with device info for multi-SDR tracking
        running_modes_detail = {}
        for mode, info in self.running_modes.items():
            params = info.get("params", {})
            running_modes_detail[mode] = {
                "started_at": info.get("started_at"),
                "device": params.get("device", params.get("device_index", 0)),
            }

        status = {
            "running_modes": list(self.running_modes.keys()),
            "running_modes_detail": running_modes_detail,  # Include device info per mode
            "uptime": time.time() - _start_time,
            "push_enabled": config.push_enabled,
            "push_connected": push_client is not None and push_client.running,
            "gps": gps_manager.is_running,
        }
        # Include GPS position if available
        gps_pos = gps_manager.position
        if gps_pos:
            status["gps_position"] = gps_pos
        return status

    # Modes that use RTL-SDR devices
    SDR_MODES = {"adsb", "sensor", "pager", "ais", "acars", "dsc", "rtlamr", "listening_post"}

    def get_sdr_in_use(self, device: int = 0) -> str | None:
        """Check if an SDR device is in use by another mode.

        Returns the mode name using the device, or None if available.
        """
        for mode, info in self.running_modes.items():
            if mode in self.SDR_MODES:
                mode_device = info.get("params", {}).get("device", 0)
                # Normalize to int for comparison
                try:
                    mode_device = int(mode_device)
                except (ValueError, TypeError):
                    mode_device = 0
                if mode_device == device:
                    return mode
        return None

    def start_mode(self, mode: str, params: dict) -> dict:
        """Start a mode with given parameters."""
        if mode in self.running_modes:
            return {"status": "error", "message": f"{mode} already running"}

        caps = self.detect_capabilities()
        if not caps["modes"].get(mode, False):
            return {"status": "error", "message": f"{mode} not available (missing tools)"}

        # Check SDR device conflicts for SDR-based modes
        if mode in self.SDR_MODES:
            device = params.get("device", 0)
            try:
                device = int(device)
            except (ValueError, TypeError):
                device = 0
            in_use_by = self.get_sdr_in_use(device)
            if in_use_by:
                return {
                    "status": "error",
                    "message": f"SDR device {device} is in use by {in_use_by}. Stop {in_use_by} first or use a different device.",
                }

        # Initialize lock if needed
        if mode not in self.locks:
            self.locks[mode] = threading.Lock()

        with self.locks[mode]:
            try:
                # Mode-specific start logic
                result = self._start_mode_internal(mode, params)
                if result.get("status") == "started":
                    self.running_modes[mode] = {
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "params": params,
                    }
                return result
            except Exception as e:
                logger.exception(f"Error starting {mode}")
                return {"status": "error", "message": str(e)}

    def stop_mode(self, mode: str) -> dict:
        """Stop a running mode."""
        if mode not in self.running_modes:
            return {"status": "not_running"}

        if mode not in self.locks:
            self.locks[mode] = threading.Lock()

        with self.locks[mode]:
            try:
                result = self._stop_mode_internal(mode)
                if mode in self.running_modes:
                    del self.running_modes[mode]
                return result
            except Exception as e:
                logger.exception(f"Error stopping {mode}")
                return {"status": "error", "message": str(e)}

    def get_mode_status(self, mode: str) -> dict:
        """Get status of a specific mode."""
        if mode in self.running_modes:
            info = {"running": True, **self.running_modes[mode]}
            # Add mode-specific stats
            if mode == "adsb":
                self._prune_stale_aircraft()
                info["aircraft_count"] = len(self.adsb_aircraft)
            elif mode == "wifi":
                info["network_count"] = len(self.wifi_networks)
                info["client_count"] = len(self.wifi_clients)
            elif mode == "bluetooth":
                info["device_count"] = len(self.bluetooth_devices)
            elif mode == "sensor":
                info["reading_count"] = len(self.data_snapshots.get(mode, []))
            elif mode == "ais":
                info["vessel_count"] = len(getattr(self, "ais_vessels", {}))
            elif mode == "aprs":
                info["station_count"] = len(getattr(self, "aprs_stations", {}))
            elif mode == "pager" or mode == "acars":
                info["message_count"] = len(self.data_snapshots.get(mode, []))
            elif mode == "rtlamr":
                info["reading_count"] = len(self.data_snapshots.get(mode, []))
            elif mode == "tscm":
                info["anomaly_count"] = len(getattr(self, "tscm_anomalies", []))
            elif mode == "satellite":
                info["pass_count"] = len(self.data_snapshots.get(mode, []))
            elif mode == "listening_post":
                info["signal_count"] = len(getattr(self, "listening_post_activity", []))
                info["current_freq"] = getattr(self, "listening_post_current_freq", 0)
                info["freqs_scanned"] = getattr(self, "listening_post_freqs_scanned", 0)
            return info
        return {"running": False}

    def get_mode_data(self, mode: str) -> dict:
        """Get current data snapshot for a mode."""
        data = {
            "mode": mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Add GPS position
        gps_pos = gps_manager.position
        if gps_pos:
            data["agent_gps"] = gps_pos

        # Mode-specific data
        if mode == "adsb":
            self._prune_stale_aircraft()
            data["data"] = list(self.adsb_aircraft.values())
        elif mode == "wifi":
            data["data"] = {
                "networks": list(self.wifi_networks.values()),
                "clients": list(self.wifi_clients.values()),
            }
        elif mode == "bluetooth":
            data["data"] = list(self.bluetooth_devices.values())
        elif mode == "ais":
            data["data"] = list(getattr(self, "ais_vessels", {}).values())
        elif mode == "aprs":
            data["data"] = list(getattr(self, "aprs_stations", {}).values())
        elif mode == "tscm":
            data["data"] = {
                "anomalies": getattr(self, "tscm_anomalies", []),
                "baseline": getattr(self, "tscm_baseline", {}),
                "wifi_devices": list(self.wifi_networks.values()),
                "wifi_clients": list(getattr(self, "tscm_wifi_clients", {}).values()),
                "bt_devices": list(self.bluetooth_devices.values()),
                "rf_signals": getattr(self, "tscm_rf_signals", []),
            }
        elif mode == "listening_post":
            data["data"] = {
                "activity": getattr(self, "listening_post_activity", []),
                "current_freq": getattr(self, "listening_post_current_freq", 0),
                "freqs_scanned": getattr(self, "listening_post_freqs_scanned", 0),
                "signal_count": len(getattr(self, "listening_post_activity", [])),
            }
        elif mode == "pager":
            # Return recent pager messages
            messages = self.data_snapshots.get(mode, [])
            data["data"] = {
                "messages": messages[-50:] if len(messages) > 50 else messages,
                "total_count": len(messages),
            }
        elif mode == "dsc":
            # Return DSC messages
            messages = getattr(self, "dsc_messages", [])
            data["data"] = {
                "messages": messages[-50:] if len(messages) > 50 else messages,
                "total_count": len(messages),
            }
        else:
            data["data"] = self.data_snapshots.get(mode, [])

        return data

    # =========================================================================
    # WiFi Monitor Mode
    # =========================================================================

    def toggle_monitor_mode(self, params: dict) -> dict:
        """Enable or disable monitor mode on a WiFi interface."""
        import re

        action = params.get("action", "start")
        interface = params.get("interface", "")
        kill_processes = params.get("kill_processes", False)

        # Validate interface name (alphanumeric, underscore, dash only)
        if not interface or not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", interface):
            return {"status": "error", "message": "Invalid interface name"}

        airmon_path = self._get_tool_path("airmon-ng")
        iw_path = self._get_tool_path("iw")

        if action == "start":
            if airmon_path:
                try:
                    # Get interfaces before
                    def get_wireless_interfaces():
                        interfaces = set()
                        try:
                            for iface in os.listdir("/sys/class/net"):
                                if os.path.exists(f"/sys/class/net/{iface}/wireless") or "mon" in iface:
                                    interfaces.add(iface)
                        except OSError:
                            pass
                        return interfaces

                    interfaces_before = get_wireless_interfaces()

                    # Kill interfering processes if requested
                    if kill_processes:
                        subprocess.run([airmon_path, "check", "kill"], capture_output=True, timeout=10)

                    # Start monitor mode
                    result = subprocess.run(
                        [airmon_path, "start", interface], capture_output=True, text=True, timeout=15
                    )
                    output = result.stdout + result.stderr

                    time.sleep(1)
                    interfaces_after = get_wireless_interfaces()

                    # Find the new monitor interface
                    new_interfaces = interfaces_after - interfaces_before
                    monitor_iface = None

                    if new_interfaces:
                        for iface in new_interfaces:
                            if "mon" in iface:
                                monitor_iface = iface
                                break
                        if not monitor_iface:
                            monitor_iface = list(new_interfaces)[0]

                    # Try to parse from airmon-ng output
                    if not monitor_iface:
                        patterns = [
                            r"\b([a-zA-Z][a-zA-Z0-9_-]*mon)\b",
                            r"\[phy\d+\]([a-zA-Z][a-zA-Z0-9_-]*mon)",
                            r"enabled.*?\[phy\d+\]([a-zA-Z][a-zA-Z0-9_-]*)",
                        ]
                        for pattern in patterns:
                            match = re.search(pattern, output, re.IGNORECASE)
                            if match:
                                candidate = match.group(1)
                                if candidate and not candidate[0].isdigit():
                                    monitor_iface = candidate
                                    break

                    # Fallback: check if original interface is in monitor mode
                    if not monitor_iface:
                        try:
                            result = subprocess.run(["iwconfig", interface], capture_output=True, text=True, timeout=5)
                            if "Mode:Monitor" in result.stdout:
                                monitor_iface = interface
                        except (subprocess.SubprocessError, OSError):
                            pass

                    # Last resort: try common naming
                    if not monitor_iface:
                        potential = interface + "mon"
                        if os.path.exists(f"/sys/class/net/{potential}"):
                            monitor_iface = potential

                    if not monitor_iface or not os.path.exists(f"/sys/class/net/{monitor_iface}"):
                        all_wireless = list(get_wireless_interfaces())
                        return {
                            "status": "error",
                            "message": f"Monitor interface not created. airmon-ng output: {output[:500]}. Available interfaces: {all_wireless}",
                        }

                    self.wifi_monitor_interface = monitor_iface
                    self._capabilities = None  # Invalidate cache so interfaces refresh
                    logger.info(f"Monitor mode enabled on {monitor_iface}")
                    return {"status": "success", "monitor_interface": monitor_iface}

                except Exception as e:
                    logger.error(f"Error enabling monitor mode: {e}")
                    return {"status": "error", "message": str(e)}

            elif iw_path:
                try:
                    subprocess.run(["ip", "link", "set", interface, "down"], capture_output=True)
                    subprocess.run([iw_path, interface, "set", "monitor", "control"], capture_output=True)
                    subprocess.run(["ip", "link", "set", interface, "up"], capture_output=True)
                    self.wifi_monitor_interface = interface
                    self._capabilities = None  # Invalidate cache
                    return {"status": "success", "monitor_interface": interface}
                except Exception as e:
                    return {"status": "error", "message": str(e)}
            else:
                return {"status": "error", "message": "No monitor mode tools available (airmon-ng or iw)"}

        else:  # stop
            current_iface = getattr(self, "wifi_monitor_interface", None) or interface
            if airmon_path:
                try:
                    subprocess.run([airmon_path, "stop", current_iface], capture_output=True, text=True, timeout=15)
                    self.wifi_monitor_interface = None
                    self._capabilities = None  # Invalidate cache
                    return {"status": "success", "message": "Monitor mode disabled"}
                except Exception as e:
                    return {"status": "error", "message": str(e)}
            elif iw_path:
                try:
                    subprocess.run(["ip", "link", "set", current_iface, "down"], capture_output=True)
                    subprocess.run([iw_path, current_iface, "set", "type", "managed"], capture_output=True)
                    subprocess.run(["ip", "link", "set", current_iface, "up"], capture_output=True)
                    self.wifi_monitor_interface = None
                    self._capabilities = None  # Invalidate cache
                    return {"status": "success", "message": "Monitor mode disabled"}
                except Exception as e:
                    return {"status": "error", "message": str(e)}

        return {"status": "error", "message": "Unknown action"}

    # =========================================================================
    # Mode-specific implementations
    # =========================================================================

    def _start_mode_internal(self, mode: str, params: dict) -> dict:
        """Internal mode start - dispatches to mode-specific handlers."""
        if HAS_DEVICE_CONFIG and mode in SDR_MODES:
            params = apply_device_defaults(params)
            try:
                if "ppm" in params:
                    validate_ppm(params["ppm"])
                if "gain" in params:
                    validate_gain(params["gain"])
            except ValueError as e:
                return {"status": "error", "message": str(e)}

        logger.info(f"Starting mode {mode} with params: {params}")

        # Initialize data structures
        self.data_snapshots[mode] = []
        self.data_queues[mode] = queue.Queue(maxsize=500)
        self.stop_events[mode] = threading.Event()

        # Dispatch to mode-specific handler
        handlers = {
            "sensor": self._start_sensor,
            "adsb": self._start_adsb,
            "wifi": self._start_wifi,
            "bluetooth": self._start_bluetooth,
            "pager": self._start_pager,
            "ais": self._start_ais,
            "acars": self._start_acars,
            "aprs": self._start_aprs,
            "rtlamr": self._start_rtlamr,
            "dsc": self._start_dsc,
            "tscm": self._start_tscm,
            "satellite": self._start_satellite,
            "listening_post": self._start_listening_post,
        }

        handler = handlers.get(mode)
        if handler:
            return handler(params)

        # Unknown mode
        logger.warning(f"Unknown mode: {mode}")
        return {"status": "error", "message": f"Unknown mode: {mode}"}

    def _stop_mode_internal(self, mode: str) -> dict:
        """Internal mode stop - terminates processes and cleans up."""
        logger.info(f"Stopping mode {mode}")

        # Signal stop first - this unblocks any waiting threads
        if mode in self.stop_events:
            self.stop_events[mode].set()

        # Terminate process if running
        if mode in self.processes:
            proc = self.processes[mode]
            try:
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        with contextlib.suppress(Exception):
                            proc.wait(timeout=1)
            except (OSError, ProcessLookupError) as e:
                # Process already dead or inaccessible
                logger.debug(f"Process cleanup for {mode}: {e}")
            del self.processes[mode]

        # Wait for output thread (short timeout since stop event is set)
        if mode in self.output_threads:
            thread = self.output_threads[mode]
            if thread and thread.is_alive():
                thread.join(timeout=1)
            del self.output_threads[mode]

        # Clean up
        if mode in self.stop_events:
            del self.stop_events[mode]
        if mode in self.data_queues:
            del self.data_queues[mode]
        if mode in self.data_snapshots:
            del self.data_snapshots[mode]

        # Mode-specific cleanup
        if mode == "adsb":
            self.adsb_aircraft.clear()
        elif mode == "wifi":
            self.wifi_networks.clear()
            self.wifi_clients.clear()
        elif mode == "bluetooth":
            self.bluetooth_devices.clear()
        elif mode == "tscm":
            # Clean up TSCM sub-threads
            for sub_thread_name in ["tscm_wifi", "tscm_bt", "tscm_rf"]:
                if sub_thread_name in self.output_threads:
                    thread = self.output_threads[sub_thread_name]
                    if thread and thread.is_alive():
                        thread.join(timeout=2)
                    del self.output_threads[sub_thread_name]
            # Clear TSCM data
            self.tscm_anomalies = []
            self.tscm_baseline = {}
            self.tscm_rf_signals = []
            self.tscm_wifi_clients = {}
            # Clear reported threat tracking sets
            if hasattr(self, "_tscm_reported_wifi"):
                self._tscm_reported_wifi.clear()
            if hasattr(self, "_tscm_reported_bt"):
                self._tscm_reported_bt.clear()
        elif mode == "dsc":
            # Clear DSC data
            if hasattr(self, "dsc_messages"):
                self.dsc_messages = []
        elif mode == "pager":
            # Pager uses two processes: multimon-ng (pager) and rtl_fm (pager_rtl)
            # Kill the rtl_fm process as well
            if "pager_rtl" in self.processes:
                rtl_proc = self.processes["pager_rtl"]
                if rtl_proc and rtl_proc.poll() is None:
                    rtl_proc.terminate()
                    try:
                        rtl_proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        rtl_proc.kill()
                del self.processes["pager_rtl"]
            # Clear pager data
            if hasattr(self, "pager_messages"):
                self.pager_messages = []
        elif mode == "aprs":
            # APRS uses two processes: decoder (aprs) and rtl_fm (aprs_rtl)
            if "aprs_rtl" in self.processes:
                rtl_proc = self.processes["aprs_rtl"]
                if rtl_proc and rtl_proc.poll() is None:
                    rtl_proc.terminate()
                    try:
                        rtl_proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        rtl_proc.kill()
                del self.processes["aprs_rtl"]
        elif mode == "rtlamr":
            # RTLAMR uses two processes: rtlamr and rtl_tcp (rtlamr_tcp)
            if "rtlamr_tcp" in self.processes:
                tcp_proc = self.processes["rtlamr_tcp"]
                if tcp_proc and tcp_proc.poll() is None:
                    tcp_proc.terminate()
                    try:
                        tcp_proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        tcp_proc.kill()
                del self.processes["rtlamr_tcp"]

        return {"status": "stopped", "mode": mode}

    # -------------------------------------------------------------------------
    # SENSOR MODE (rtl_433) - Uses Intercept's SDR abstraction
    # -------------------------------------------------------------------------

    def _start_sensor(self, params: dict) -> dict:
        """Start rtl_433 sensor mode using Intercept's SDR utilities."""
        freq = params.get("frequency", "433.92")
        gain = params.get("gain")
        device = params.get("device", "0")
        ppm = params.get("ppm")
        bias_t = params.get("bias_t", False)
        sdr_type_str = params.get("sdr_type", "rtlsdr")
        units = str(params.get("units", "si")).strip().lower()
        if units not in ("si", "customary", "native"):
            return {"status": "error", "message": f"Invalid units: {params.get('units')}"}

        # Try to use Intercept's SDR abstraction layer
        sdr_factory = self._get_sdr_factory()
        if sdr_factory:
            try:
                from utils.sdr import SDRType

                sdr_type = SDRType(sdr_type_str)
                sdr_device = sdr_factory.create_default_device(sdr_type, index=int(device))
                builder = sdr_factory.get_builder(sdr_type)

                # Use the builder to construct the command properly
                cmd = builder.build_ism_command(
                    device=sdr_device,
                    frequency_mhz=float(freq),
                    gain=float(gain) if gain else None,
                    ppm=int(ppm) if ppm else None,
                    bias_t=bias_t,
                )
                logger.info(f"Starting sensor (via SDR abstraction): {' '.join(cmd)}")

            except Exception as e:
                logger.warning(f"SDR abstraction failed, falling back to direct command: {e}")
                cmd = self._build_sensor_command_fallback(freq, gain, device, ppm)
        else:
            # Fallback: build command directly
            cmd = self._build_sensor_command_fallback(freq, gain, device, ppm)

        # Unit conversion, matching the controller's /start_sensor handling
        cmd.extend(["-C", units])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes["sensor"] = proc

            # Wait briefly to verify process started successfully
            time.sleep(0.5)
            if proc.poll() is not None:
                stderr_output = proc.stderr.read().decode("utf-8", errors="replace")
                del self.processes["sensor"]
                return {"status": "error", "message": f"rtl_433 failed to start: {stderr_output[:200]}"}

            # Start output reader thread
            thread = threading.Thread(target=self._sensor_output_reader, args=(proc,), daemon=True)
            thread.start()
            self.output_threads["sensor"] = thread

            return {
                "status": "started",
                "mode": "sensor",
                "command": " ".join(cmd),
                "gps_enabled": gps_manager.is_running,
            }

        except FileNotFoundError:
            return {"status": "error", "message": "rtl_433 not found. Install via: apt install rtl-433"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _build_sensor_command_fallback(self, freq, gain, device, ppm) -> list:
        """Build rtl_433 command without SDR abstraction."""
        cmd = ["rtl_433", "-F", "json"]
        if freq:
            cmd.extend(["-f", f"{freq}M"])
        if gain and str(gain) != "0":
            cmd.extend(["-g", str(gain)])
        if device and str(device) != "0":
            cmd.extend(["-d", str(device)])
        if ppm and str(ppm) != "0":
            cmd.extend(["-p", str(ppm)])
        return cmd

    def _sensor_output_reader(self, proc: subprocess.Popen):
        """Read rtl_433 JSON output and collect data."""
        mode = "sensor"
        stop_event = self.stop_events.get(mode)

        try:
            for line in iter(proc.stdout.readline, b""):
                if stop_event and stop_event.is_set():
                    break

                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    data["type"] = "sensor"
                    data["received_at"] = datetime.now(timezone.utc).isoformat()

                    # Add GPS if available
                    gps_pos = gps_manager.position
                    if gps_pos:
                        data["agent_gps"] = gps_pos

                    # Store in snapshot (keep last 100)
                    snapshots = self.data_snapshots.get(mode, [])
                    snapshots.append(data)
                    if len(snapshots) > 100:
                        snapshots = snapshots[-100:]
                    self.data_snapshots[mode] = snapshots

                    logger.debug(f"Sensor data: {data.get('model', 'Unknown')}")

                except json.JSONDecodeError:
                    pass  # Not JSON, ignore

        except (OSError, ValueError) as e:
            # Bad file descriptor or closed file - process was terminated
            logger.debug(f"Sensor output reader stopped: {e}")
        except Exception as e:
            logger.error(f"Sensor output reader error: {e}")
        finally:
            with contextlib.suppress(Exception):
                proc.wait(timeout=1)
            logger.info("Sensor output reader stopped")

    # -------------------------------------------------------------------------
    # ADS-B MODE (dump1090) - Uses Intercept's SDR abstraction
    # -------------------------------------------------------------------------

    def _start_adsb(self, params: dict) -> dict:
        """Start dump1090 ADS-B mode using Intercept's utilities."""
        gain = params.get("gain", "40")
        device = params.get("device", "0")
        bias_t = params.get("bias_t", False)
        ppm = params.get("ppm")
        sdr_type_str = params.get("sdr_type", "rtlsdr")
        remote_sbs_host = params.get("remote_sbs_host")
        remote_sbs_port = params.get("remote_sbs_port", 30003)

        # If remote SBS host provided, just connect to it
        if remote_sbs_host:
            return self._start_adsb_sbs_connection(remote_sbs_host, remote_sbs_port)

        # Check if dump1090 already running on port 30003
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            result = sock.connect_ex(("localhost", 30003))
            sock.close()
            if result == 0:
                logger.info("dump1090 already running, connecting to SBS port")
                return self._start_adsb_sbs_connection("localhost", 30003)
        except Exception:
            pass

        # Try using Intercept's SDR abstraction for building the command
        sdr_factory = self._get_sdr_factory()
        cmd = None

        if sdr_factory:
            try:
                from utils.sdr import SDRType

                sdr_type = SDRType(sdr_type_str)
                sdr_device = sdr_factory.create_default_device(sdr_type, index=int(device))
                builder = sdr_factory.get_builder(sdr_type)

                # Use the builder to construct dump1090 command
                cmd = builder.build_adsb_command(
                    device=sdr_device,
                    gain=float(gain) if gain else None,
                    bias_t=bias_t,
                    ppm=int(ppm) if ppm else None,
                )
                logger.info(f"Starting ADS-B (via SDR abstraction): {' '.join(cmd)}")

            except Exception as e:
                logger.warning(f"SDR abstraction failed for ADS-B: {e}")

        if not cmd:
            # Fallback: find dump1090 manually and build command
            dump1090_path = self._find_dump1090()
            if not dump1090_path:
                return {"status": "error", "message": "dump1090 not found. Install via: apt install dump1090-fa"}

            cmd = [dump1090_path, "--net", "--quiet"]
            if gain:
                cmd.extend(["--gain", str(gain)])
            if ppm and str(ppm) != "0":
                cmd.extend(["--ppm", str(ppm)])
            if device and str(device) != "0":
                cmd.extend(["--device-index", str(device)])

        logger.info(f"Starting dump1090: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
            self.processes["adsb"] = proc

            # Wait for dump1090 to start
            time.sleep(2)

            if proc.poll() is not None:
                stderr = proc.stderr.read().decode("utf-8", errors="ignore")
                return {"status": "error", "message": f"dump1090 failed to start: {stderr[:200]}"}

            # Connect to SBS port
            return self._start_adsb_sbs_connection("localhost", 30003)

        except FileNotFoundError:
            return {"status": "error", "message": "dump1090 not found"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _find_dump1090(self) -> str | None:
        """Find dump1090 binary using Intercept's dependency module or fallback."""
        # Try Intercept's tool path finder first
        for name in ["dump1090", "dump1090-fa", "dump1090-mutability", "readsb"]:
            path = self._get_tool_path(name)
            if path:
                return path

        # Fallback: check common installation paths
        common_paths = [
            "/opt/homebrew/bin/dump1090",
            "/opt/homebrew/bin/dump1090-fa",
            "/usr/local/bin/dump1090",
            "/usr/local/bin/dump1090-fa",
            "/usr/bin/dump1090",
            "/usr/bin/dump1090-fa",
        ]
        for path in common_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    def _start_adsb_sbs_connection(self, host: str, port: int) -> dict:
        """Connect to SBS port and start parsing."""
        thread = threading.Thread(target=self._adsb_sbs_reader, args=(host, port), daemon=True)
        thread.start()
        self.output_threads["adsb"] = thread

        return {
            "status": "started",
            "mode": "adsb",
            "sbs_source": f"{host}:{port}",
            "gps_enabled": gps_manager.is_running,
        }

    def _adsb_sbs_reader(self, host: str, port: int):
        """Read and parse SBS data from dump1090."""
        mode = "adsb"
        stop_event = self.stop_events.get(mode)
        retry_count = 0
        max_retries = 5

        while not (stop_event and stop_event.is_set()):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((host, port))
                logger.info(f"Connected to SBS at {host}:{port}")
                retry_count = 0

                buffer = ""
                sock.settimeout(1.0)

                while not (stop_event and stop_event.is_set()):
                    try:
                        data = sock.recv(4096).decode("utf-8", errors="ignore")
                        if not data:
                            break
                        buffer += data

                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            self._parse_sbs_line(line.strip())

                    except socket.timeout:
                        continue

                sock.close()

            except Exception as e:
                logger.warning(f"SBS connection error: {e}")
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error("Max SBS retries reached, stopping")
                    break
                time.sleep(2)

        logger.info("ADS-B SBS reader stopped")

    def _parse_sbs_line(self, line: str):
        """Parse SBS format line and update aircraft dict."""
        if not line:
            return

        parts = line.split(",")
        if len(parts) < 11 or parts[0] != "MSG":
            return

        msg_type = parts[1]
        icao = parts[4].upper()
        if not icao:
            return

        aircraft = self.adsb_aircraft.get(icao) or {"icao": icao}
        aircraft["last_seen"] = datetime.now(timezone.utc).isoformat()

        # Add GPS
        gps_pos = gps_manager.position
        if gps_pos:
            aircraft["agent_gps"] = gps_pos

        try:
            if msg_type == "1" and len(parts) > 10:
                callsign = parts[10].strip()
                if callsign:
                    aircraft["callsign"] = callsign

            elif msg_type == "3" and len(parts) > 15:
                if parts[11]:
                    aircraft["altitude"] = int(float(parts[11]))
                if parts[14] and parts[15]:
                    aircraft["lat"] = float(parts[14])
                    aircraft["lon"] = float(parts[15])

            elif msg_type == "4" and len(parts) > 16:
                if parts[12]:
                    aircraft["speed"] = int(float(parts[12]))
                if parts[13]:
                    aircraft["heading"] = int(float(parts[13]))
                if parts[16]:
                    aircraft["vertical_rate"] = int(float(parts[16]))

            elif msg_type == "5" and len(parts) > 11:
                if parts[10]:
                    callsign = parts[10].strip()
                    if callsign:
                        aircraft["callsign"] = callsign
                if parts[11]:
                    aircraft["altitude"] = int(float(parts[11]))

            elif msg_type == "6" and len(parts) > 17:
                if parts[17]:
                    aircraft["squawk"] = parts[17]

        except (ValueError, IndexError):
            pass

        self.adsb_aircraft[icao] = aircraft

    def _prune_stale_aircraft(self) -> None:
        """Drop aircraft not heard from within MAX_AIRCRAFT_AGE_SECONDS.

        The agent serves its whole aircraft dict on every poll, and the
        dashboard stamps each one as freshly seen on arrival. Without
        pruning here, a contact heard once stays on the map until the mode
        is stopped -- the dashboard's own 60s expiry can never fire for it.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - MAX_AIRCRAFT_AGE_SECONDS
        for icao, aircraft in list(self.adsb_aircraft.items()):
            last_seen = aircraft.get("last_seen")
            if not last_seen:
                continue
            try:
                seen_at = datetime.fromisoformat(last_seen).timestamp()
            except (TypeError, ValueError):
                continue
            if seen_at < cutoff:
                del self.adsb_aircraft[icao]

    # -------------------------------------------------------------------------
    # WIFI MODE (airodump-ng) - Uses Intercept's utilities
    # -------------------------------------------------------------------------

    def _start_wifi(self, params: dict) -> dict:
        """Start WiFi scanning using Intercept's UnifiedWiFiScanner."""
        interface = params.get("interface")
        channel = params.get("channel")
        channels = params.get("channels")
        band = params.get("band", "abg")
        scan_type = params.get("scan_type", "deep")

        # Handle quick scan - returns results synchronously
        if scan_type == "quick":
            return self._wifi_quick_scan(interface)

        # Deep scan - use Intercept's UnifiedWiFiScanner
        try:
            from utils.wifi.scanner import get_wifi_scanner

            scanner = get_wifi_scanner(interface)

            # Store scanner reference
            self._wifi_scanner_instance = scanner

            # Check capabilities
            caps = scanner.check_capabilities()
            if not caps.can_deep_scan:
                return {"status": "error", "message": f"Deep scan not available: {', '.join(caps.issues)}"}

            # Convert band parameter
            if band == "abg":
                scan_band = "all"
            elif band == "bg":
                scan_band = "2.4"
            elif band == "a":
                scan_band = "5"
            else:
                scan_band = "all"

            channel_list = None
            if channels:
                if isinstance(channels, str):
                    channel_list = [c.strip() for c in channels.split(",") if c.strip()]
                elif isinstance(channels, (list, tuple, set)):
                    channel_list = list(channels)
                else:
                    channel_list = [channels]
                try:
                    channel_list = [int(c) for c in channel_list]
                except (TypeError, ValueError):
                    return {"status": "error", "message": "Invalid channels"}

            # Start deep scan
            if scanner.start_deep_scan(interface=interface, band=scan_band, channel=channel, channels=channel_list):
                # Start thread to sync data to agent's dictionaries
                thread = threading.Thread(target=self._wifi_data_sync, args=(scanner,), daemon=True)
                thread.start()
                self.output_threads["wifi"] = thread

                return {
                    "status": "started",
                    "mode": "wifi",
                    "interface": interface,
                    "gps_enabled": gps_manager.is_running,
                }
            else:
                return {"status": "error", "message": scanner.get_status().error or "Failed to start deep scan"}

        except ImportError:
            # Fallback to direct airodump-ng
            return self._start_wifi_fallback(interface, channel, band, channels)
        except Exception as e:
            logger.error(f"WiFi scanner error: {e}")
            return {"status": "error", "message": str(e)}

    def _wifi_data_sync(self, scanner):
        """Sync WiFi scanner data to agent's data structures."""
        mode = "wifi"
        stop_event = self.stop_events.get(mode)

        while not (stop_event and stop_event.is_set()):
            try:
                gps_position = gps_manager.position

                # Sync access points
                for ap in scanner.access_points:
                    net = ap.to_dict()
                    if gps_position:
                        net["agent_gps"] = gps_position
                    self.wifi_networks[ap.bssid.upper()] = net

                # Sync clients
                for client in scanner.clients:
                    client_data = client.to_dict()
                    if gps_position:
                        client_data["agent_gps"] = gps_position
                    self.wifi_clients[client.mac.upper()] = client_data

                time.sleep(2)
            except Exception as e:
                logger.debug(f"WiFi sync error: {e}")
                time.sleep(2)

        # Stop scanner when done
        if hasattr(self, "_wifi_scanner_instance") and self._wifi_scanner_instance:
            self._wifi_scanner_instance.stop_deep_scan()

    def _start_wifi_fallback(
        self,
        interface: str | None,
        channel: int | None,
        band: str,
        channels: list[int] | str | None = None,
    ) -> dict:
        """Fallback WiFi deep scan using airodump-ng directly."""
        if not interface:
            return {"status": "error", "message": "WiFi interface required"}

        # Validate interface
        try:
            from utils.validation import validate_network_interface

            interface = validate_network_interface(interface)
        except (ImportError, ValueError):
            if not os.path.exists(f"/sys/class/net/{interface}"):
                return {"status": "error", "message": f"Interface {interface} not found"}

        csv_path = "/tmp/intercept_agent_wifi"
        for f in [f"{csv_path}-01.csv", f"{csv_path}-01.cap", f"{csv_path}-01.gps"]:
            with contextlib.suppress(OSError):
                os.remove(f)

        airodump_path = self._get_tool_path("airodump-ng")
        if not airodump_path:
            return {"status": "error", "message": "airodump-ng not found"}

        output_formats = "csv,gps" if gps_manager.is_running else "csv"
        cmd = [airodump_path, "-w", csv_path, "--output-format", output_formats, "--band", band]
        if gps_manager.is_running:
            cmd.append("--gpsd")
        channel_list = None
        if channels:
            if isinstance(channels, str):
                channel_list = [c.strip() for c in channels.split(",") if c.strip()]
            elif isinstance(channels, (list, tuple, set)):
                channel_list = list(channels)
            else:
                channel_list = [channels]
            try:
                channel_list = [int(c) for c in channel_list]
            except (TypeError, ValueError):
                return {"status": "error", "message": "Invalid channels"}

        if channel_list:
            cmd.extend(["-c", ",".join(str(c) for c in channel_list)])
        elif channel:
            cmd.extend(["-c", str(channel)])
        cmd.append(interface)

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.processes["wifi"] = proc

            time.sleep(0.5)
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode("utf-8", errors="ignore")
                return {"status": "error", "message": f"airodump-ng failed: {stderr[:200]}"}

            thread = threading.Thread(target=self._wifi_csv_reader, args=(csv_path,), daemon=True)
            thread.start()
            self.output_threads["wifi"] = thread

            return {"status": "started", "mode": "wifi", "interface": interface, "gps_enabled": gps_manager.is_running}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _wifi_quick_scan(self, interface: str | None) -> dict:
        """
        Perform a quick one-shot WiFi scan using system tools.

        Uses nmcli, iw, or iwlist (no monitor mode required).
        Returns results synchronously.
        """
        try:
            from utils.wifi.scanner import get_wifi_scanner

            scanner = get_wifi_scanner()
            result = scanner.quick_scan(interface=interface, timeout=15.0)

            if result.error:
                return {"status": "error", "message": result.error, "warnings": result.warnings}

            # Convert access points to dict format
            networks = []
            gps_position = gps_manager.position
            for ap in result.access_points:
                net = ap.to_dict()
                # Add agent GPS if available
                if gps_position:
                    net["agent_gps"] = gps_position
                networks.append(net)

            return {
                "status": "success",
                "scan_type": "quick",
                "access_points": networks,
                "networks": networks,  # Alias for compatibility
                "network_count": len(networks),
                "warnings": result.warnings,
                "gps_enabled": gps_manager.is_running,
                "agent_gps": gps_position,
            }

        except ImportError:
            # Fallback: simple nmcli scan
            return self._wifi_quick_scan_fallback(interface)
        except Exception as e:
            logger.exception("Quick WiFi scan failed")
            return {"status": "error", "message": str(e)}

    def _wifi_quick_scan_fallback(self, interface: str | None) -> dict:
        """Fallback quick scan using nmcli directly."""
        nmcli_path = shutil.which("nmcli")
        if not nmcli_path:
            return {"status": "error", "message": "nmcli not found. Install NetworkManager."}

        try:
            # Trigger rescan
            subprocess.run([nmcli_path, "device", "wifi", "rescan"], capture_output=True, timeout=5)

            # Get results
            cmd = [nmcli_path, "-t", "-f", "BSSID,SSID,CHAN,SIGNAL,SECURITY", "device", "wifi", "list"]
            if interface:
                cmd.extend(["ifname", interface])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

            if result.returncode != 0:
                return {"status": "error", "message": f"nmcli failed: {result.stderr}"}

            networks = []
            gps_position = gps_manager.position
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split(":")
                if len(parts) >= 5:
                    net = {
                        "bssid": parts[0],
                        "essid": parts[1],
                        "channel": int(parts[2]) if parts[2].isdigit() else 0,
                        "signal": int(parts[3]) if parts[3].isdigit() else 0,
                        "rssi_current": int(parts[3]) - 100 if parts[3].isdigit() else -100,  # Convert % to dBm approx
                        "security": parts[4],
                    }
                    if gps_position:
                        net["agent_gps"] = gps_position
                    networks.append(net)

            return {
                "status": "success",
                "scan_type": "quick",
                "access_points": networks,
                "networks": networks,
                "network_count": len(networks),
                "warnings": ["Using fallback nmcli scanner"],
                "gps_enabled": gps_manager.is_running,
                "agent_gps": gps_position,
            }

        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "nmcli scan timed out"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _wifi_csv_reader(self, csv_path: str):
        """Periodically parse airodump-ng CSV and GPS output."""
        mode = "wifi"
        stop_event = self.stop_events.get(mode)
        csv_file = csv_path + "-01.csv"
        gps_file = csv_path + "-01.gps"

        while not (stop_event and stop_event.is_set()):
            if os.path.exists(csv_file):
                try:
                    # Parse GPS file for accurate coordinates (if available)
                    gps_data = self._parse_airodump_gps(gps_file) if os.path.exists(gps_file) else None

                    networks, clients = self._parse_airodump_csv(csv_file, gps_data)
                    self.wifi_networks = networks
                    self.wifi_clients = clients
                except Exception as e:
                    logger.error(f"CSV parse error: {e}")

            time.sleep(2)

        logger.info("WiFi CSV reader stopped")

    def _parse_airodump_gps(self, gps_path: str) -> dict | None:
        """
        Parse airodump-ng GPS file for accurate coordinates.

        Format:
        <?xml version="1.0" encoding="ISO-8859-1"?>
        <!DOCTYPE gps-run SYSTEM "...">
        <gps-run gps-version="1">
        <gps-point lat="LAT" lon="LON" alt="ALT" spd="SPD" time="TIME"/>
        ...
        </gps-run>

        Returns the most recent GPS point.
        """
        try:
            import xml.etree.ElementTree as ET

            tree = ET.parse(gps_path)
            root = tree.getroot()

            # Get the last (most recent) GPS point
            gps_points = root.findall(".//gps-point")
            if gps_points:
                last_point = gps_points[-1]
                lat = last_point.get("lat")
                lon = last_point.get("lon")
                alt = last_point.get("alt")

                if lat and lon:
                    return {
                        "lat": float(lat),
                        "lon": float(lon),
                        "altitude": float(alt) if alt else None,
                        "source": "airodump_gps",
                    }
        except Exception as e:
            logger.debug(f"GPS file parse error: {e}")

        return None

    def _parse_airodump_csv(self, csv_path: str, gps_data: dict | None = None) -> tuple[dict, dict]:
        """Parse airodump-ng CSV file using Intercept's existing parser."""
        networks = {}
        clients = {}

        try:
            # Use Intercept's robust airodump parser (handles edge cases, proper CSV parsing)
            from utils.wifi.parsers.airodump import parse_airodump_csv

            network_obs, client_list = parse_airodump_csv(csv_path)

            # Convert WiFiObservation objects to dicts for agent format
            for obs in network_obs:
                networks[obs.bssid] = {
                    "bssid": obs.bssid,
                    "essid": obs.essid or "Hidden",
                    "channel": obs.channel,
                    "frequency_mhz": obs.frequency_mhz,
                    "signal": obs.rssi,
                    "security": obs.security,
                    "cipher": obs.cipher,
                    "auth": obs.auth,
                    "vendor": obs.vendor,
                    "beacon_count": obs.beacon_count,
                    "data_count": obs.data_count,
                    "band": obs.band,
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                }

            # Convert client dicts (already in dict format from parser)
            for client in client_list:
                mac = client.get("mac")
                if mac:
                    clients[mac] = {
                        "mac": mac,
                        "signal": client.get("rssi"),
                        "bssid": client.get("bssid"),
                        "probes": ",".join(client.get("probed_essids", [])),
                        "packets": client.get("packets", 0),
                        "last_seen": datetime.now(timezone.utc).isoformat(),
                    }

            logger.debug(f"Parsed {len(networks)} networks, {len(clients)} clients")

        except ImportError:
            logger.warning("Intercept WiFi parser not available, using fallback")
            # Fallback: simple parsing if running standalone
            try:
                with open(csv_path, errors="replace") as f:
                    content = f.read()
                for section in content.split("\n\n"):
                    lines = section.strip().split("\n")
                    if not lines:
                        continue
                    header = lines[0]
                    if "BSSID" in header and "ESSID" in header:
                        for line in lines[1:]:
                            parts = [p.strip() for p in line.split(",")]
                            if len(parts) >= 14 and ":" in parts[0]:
                                networks[parts[0]] = {
                                    "bssid": parts[0],
                                    "channel": int(parts[3]) if parts[3].lstrip("-").isdigit() else None,
                                    "signal": int(parts[8]) if parts[8].lstrip("-").isdigit() else None,
                                    "security": parts[5],
                                    "essid": parts[13] or "Hidden",
                                    "last_seen": datetime.now(timezone.utc).isoformat(),
                                }
                    elif "Station MAC" in header:
                        for line in lines[1:]:
                            parts = [p.strip() for p in line.split(",")]
                            if len(parts) >= 6 and ":" in parts[0]:
                                clients[parts[0]] = {
                                    "mac": parts[0],
                                    "signal": int(parts[3]) if parts[3].lstrip("-").isdigit() else None,
                                    "bssid": parts[5] if ":" in parts[5] else None,
                                    "probes": parts[6] if len(parts) > 6 else "",
                                    "last_seen": datetime.now(timezone.utc).isoformat(),
                                }
            except Exception as e:
                logger.error(f"Fallback CSV parse error: {e}")

        except Exception as e:
            logger.error(f"Error parsing CSV: {e}")

        # Add GPS to all entries
        # Prefer GPS from airodump's .gps file (more accurate timestamp)
        # Fall back to GPSManager if no .gps file data
        if gps_data:
            # Use GPS coordinates from airodump's GPS file
            gps_pos = {
                "lat": gps_data["lat"],
                "lon": gps_data["lon"],
                "altitude": gps_data.get("altitude"),
                "source": "airodump_gps",  # Mark as from airodump GPS file
            }
            logger.debug(f"Using airodump GPS: {gps_data['lat']:.6f}, {gps_data['lon']:.6f}")
        else:
            # Fall back to GPSManager position
            gps_pos = gps_manager.position

        if gps_pos:
            for net in networks.values():
                net["agent_gps"] = gps_pos
            for client in clients.values():
                client["agent_gps"] = gps_pos

        return networks, clients

    # -------------------------------------------------------------------------
    # BLUETOOTH MODE
    # -------------------------------------------------------------------------

    def _start_bluetooth(self, params: dict) -> dict:
        """Start Bluetooth scanning using Intercept's BluetoothScanner."""
        adapter = params.get("adapter", "hci0")
        mode_param = params.get("mode", "auto")
        duration = params.get("duration")

        try:
            # Use Intercept's BluetoothScanner
            from utils.bluetooth.scanner import BluetoothScanner

            scanner = BluetoothScanner(adapter_id=adapter)

            # Store scanner reference
            self._bluetooth_scanner_instance = scanner

            # Set callback for device updates
            def on_device_updated(device):
                # Convert to agent's format and store
                self.bluetooth_devices[device.address.upper()] = {
                    "mac": device.address.upper(),
                    "name": device.name,
                    "rssi": device.rssi_current,
                    "protocol": device.protocol,
                    "last_seen": device.last_seen.isoformat() if device.last_seen else None,
                    "first_seen": device.first_seen.isoformat() if device.first_seen else None,
                    "agent_gps": gps_manager.position,
                }

            scanner.add_device_callback(on_device_updated)

            # Start scanning
            if scanner.start_scan(mode=mode_param, duration_s=duration):
                # Start thread to sync device data
                thread = threading.Thread(target=self._bluetooth_data_sync, args=(scanner,), daemon=True)
                thread.start()
                self.output_threads["bluetooth"] = thread

                return {
                    "status": "started",
                    "mode": "bluetooth",
                    "adapter": adapter,
                    "backend": scanner.get_status().backend,
                    "gps_enabled": gps_manager.is_running,
                }
            else:
                return {"status": "error", "message": scanner.get_status().error or "Failed to start scan"}

        except ImportError:
            # Fallback to direct bluetoothctl if scanner not available
            return self._start_bluetooth_fallback(adapter)
        except Exception as e:
            logger.error(f"Bluetooth scanner error: {e}")
            return {"status": "error", "message": str(e)}

    def _bluetooth_data_sync(self, scanner):
        """Sync Bluetooth scanner data to agent's data structures."""
        mode = "bluetooth"
        stop_event = self.stop_events.get(mode)

        while not (stop_event and stop_event.is_set()):
            try:
                # Get devices from scanner
                devices = scanner.get_devices()
                for device in devices:
                    self.bluetooth_devices[device.address.upper()] = {
                        "mac": device.address.upper(),
                        "name": device.name,
                        "rssi": device.rssi_current,
                        "protocol": device.protocol,
                        "last_seen": device.last_seen.isoformat() if device.last_seen else None,
                        "agent_gps": gps_manager.position,
                    }
                time.sleep(1)
            except Exception as e:
                logger.debug(f"Bluetooth sync error: {e}")
                time.sleep(1)

        # Stop scanner when done
        if hasattr(self, "_bluetooth_scanner_instance") and self._bluetooth_scanner_instance:
            self._bluetooth_scanner_instance.stop_scan()

    def _start_bluetooth_fallback(self, adapter: str) -> dict:
        """Fallback Bluetooth scanning using bluetoothctl directly."""
        if not shutil.which("bluetoothctl"):
            return {"status": "error", "message": "bluetoothctl not found"}

        thread = threading.Thread(target=self._bluetooth_scanner_fallback, args=(adapter,), daemon=True)
        thread.start()
        self.output_threads["bluetooth"] = thread

        return {
            "status": "started",
            "mode": "bluetooth",
            "adapter": adapter,
            "backend": "bluetoothctl",
            "gps_enabled": gps_manager.is_running,
        }

    def _bluetooth_scanner_fallback(self, adapter: str):
        """Fallback scan using bluetoothctl directly."""
        mode = "bluetooth"
        stop_event = self.stop_events.get(mode)

        try:
            proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes["bluetooth"] = proc

            proc.stdin.write(b"scan on\n")
            proc.stdin.flush()

            while not (stop_event and stop_event.is_set()):
                line = proc.stdout.readline()
                if not line:
                    break

                line = line.decode("utf-8", errors="replace").strip()
                if "Device" in line:
                    self._parse_bluetooth_line(line)

                time.sleep(0.1)

            proc.stdin.write(b"scan off\n")
            proc.stdin.write(b"exit\n")
            proc.stdin.flush()
            proc.wait(timeout=2)

        except Exception as e:
            logger.error(f"Bluetooth scanner error: {e}")
        finally:
            logger.info("Bluetooth scanner stopped")

    def _parse_bluetooth_line(self, line: str):
        """Parse bluetoothctl output line."""
        import re

        # Match device address (MAC)
        mac_match = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", line)
        if not mac_match:
            return

        mac = mac_match.group(1).upper()
        device = self.bluetooth_devices.get(mac) or {"mac": mac}
        device["last_seen"] = datetime.now(timezone.utc).isoformat()

        # Extract name
        if "[NEW]" in line or "[CHG]" in line and "Name:" not in line:
            # Try to get name after MAC
            parts = line.split(mac)
            if len(parts) > 1:
                name = parts[1].strip()
                if name and not name.startswith("RSSI") and not name.startswith("ManufacturerData"):
                    device["name"] = name

        # Extract RSSI
        rssi_match = re.search(r"RSSI:\s*(-?\d+)", line)
        if rssi_match:
            device["rssi"] = int(rssi_match.group(1))

        # Add GPS
        gps_pos = gps_manager.position
        if gps_pos:
            device["agent_gps"] = gps_pos

        self.bluetooth_devices[mac] = device

    # -------------------------------------------------------------------------
    # PAGER MODE (rtl_fm | multimon-ng)
    # -------------------------------------------------------------------------

    def _start_pager(self, params: dict) -> dict:
        """Start POCSAG/FLEX pager decoding using rtl_fm | multimon-ng."""
        freq = params.get("frequency", "929.6125")
        gain = params.get("gain", "0")
        device = params.get("device", "0")
        ppm = params.get("ppm", "0")
        squelch = params.get("squelch", "0")
        protocols = params.get("protocols", ["POCSAG512", "POCSAG1200", "POCSAG2400", "FLEX"])

        # Validate tools
        rtl_fm_path = self._get_tool_path("rtl_fm")
        multimon_path = self._get_tool_path("multimon-ng")
        if not rtl_fm_path:
            return {"status": "error", "message": "rtl_fm not found. Install rtl-sdr."}
        if not multimon_path:
            return {"status": "error", "message": "multimon-ng not found. Install multimon-ng."}

        # Build rtl_fm command for FM demodulation at 22050 Hz
        rtl_fm_cmd = [
            rtl_fm_path,
            "-f",
            f"{freq}M",
            "-s",
            "22050",
            "-g",
            str(gain),
            "-d",
            str(device),
        ]
        if ppm and str(ppm) != "0":
            rtl_fm_cmd.extend(["-p", str(ppm)])
        if squelch and str(squelch) != "0":
            rtl_fm_cmd.extend(["-l", str(squelch)])

        # Build multimon-ng command
        multimon_cmd = [multimon_path, "-t", "raw", "-a"]
        for proto in protocols:
            if proto in ["POCSAG512", "POCSAG1200", "POCSAG2400", "FLEX"]:
                multimon_cmd.extend(["-a", proto])
        multimon_cmd.append("-")

        logger.info(f"Starting pager: {' '.join(rtl_fm_cmd)} | {' '.join(multimon_cmd)}")

        try:
            # Start rtl_fm process
            rtl_fm_proc = subprocess.Popen(
                rtl_fm_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Pipe to multimon-ng
            multimon_proc = subprocess.Popen(
                multimon_cmd,
                stdin=rtl_fm_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            rtl_fm_proc.stdout.close()  # Allow SIGPIPE

            # Store both processes
            self.processes["pager"] = multimon_proc
            self.processes["pager_rtl"] = rtl_fm_proc

            # Wait briefly to verify processes started successfully
            time.sleep(0.5)
            if rtl_fm_proc.poll() is not None:
                stderr_output = rtl_fm_proc.stderr.read().decode("utf-8", errors="replace")
                multimon_proc.terminate()
                del self.processes["pager"]
                del self.processes["pager_rtl"]
                return {"status": "error", "message": f"rtl_fm failed to start: {stderr_output[:200]}"}

            # Start output reader
            thread = threading.Thread(target=self._pager_output_reader, args=(multimon_proc,), daemon=True)
            thread.start()
            self.output_threads["pager"] = thread

            return {
                "status": "started",
                "mode": "pager",
                "frequency": freq,
                "protocols": protocols,
                "gps_enabled": gps_manager.is_running,
            }

        except FileNotFoundError as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _pager_output_reader(self, proc: subprocess.Popen):
        """Read and parse multimon-ng output for pager messages."""
        mode = "pager"
        stop_event = self.stop_events.get(mode)

        try:
            for line in iter(proc.stdout.readline, b""):
                if stop_event and stop_event.is_set():
                    break

                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                parsed = self._parse_pager_message(line)
                if parsed:
                    parsed["received_at"] = datetime.now(timezone.utc).isoformat()

                    gps_pos = gps_manager.position
                    if gps_pos:
                        parsed["agent_gps"] = gps_pos

                    snapshots = self.data_snapshots.get(mode, [])
                    snapshots.append(parsed)
                    if len(snapshots) > 200:
                        snapshots = snapshots[-200:]
                    self.data_snapshots[mode] = snapshots

                    logger.debug(f"Pager: {parsed.get('protocol')} addr={parsed.get('address')}")

        except (OSError, ValueError) as e:
            # Bad file descriptor or closed file - process was terminated
            logger.debug(f"Pager reader stopped: {e}")
        except Exception as e:
            logger.error(f"Pager reader error: {e}")
        finally:
            with contextlib.suppress(Exception):
                proc.wait(timeout=1)
            if "pager_rtl" in self.processes:
                try:
                    rtl_proc = self.processes["pager_rtl"]
                    if rtl_proc.poll() is None:
                        rtl_proc.terminate()
                    del self.processes["pager_rtl"]
                except Exception:
                    pass
            logger.info("Pager reader stopped")

    def _parse_pager_message(self, line: str) -> dict | None:
        """Parse multimon-ng output line for POCSAG/FLEX using Intercept's parser."""
        try:
            # Use Intercept's existing pager parser
            from routes.pager import parse_multimon_output

            parsed = parse_multimon_output(line)
            if parsed:
                parsed["type"] = "pager"
                return parsed
            return None
        except ImportError:
            # Fallback to inline parsing if import fails
            import re

            # POCSAG with message
            match = re.match(r"(POCSAG\d+):\s*Address:\s*(\d+)\s+Function:\s*(\d+)\s+(Alpha|Numeric):\s*(.*)", line)
            if match:
                return {
                    "type": "pager",
                    "protocol": match.group(1),
                    "address": match.group(2),
                    "function": match.group(3),
                    "msg_type": match.group(4),
                    "message": match.group(5).strip() or "[No Message]",
                }

            # POCSAG address only (tone)
            match = re.match(r"(POCSAG\d+):\s*Address:\s*(\d+)\s+Function:\s*(\d+)\s*$", line)
            if match:
                return {
                    "type": "pager",
                    "protocol": match.group(1),
                    "address": match.group(2),
                    "function": match.group(3),
                    "msg_type": "Tone",
                    "message": "[Tone Only]",
                }

            # FLEX format
            match = re.match(r"FLEX[:\|]\s*(.+)", line)
            if match:
                return {
                    "type": "pager",
                    "protocol": "FLEX",
                    "address": "Unknown",
                    "function": "",
                    "msg_type": "Unknown",
                    "message": match.group(1).strip(),
                }

            return None

    # -------------------------------------------------------------------------
    # AIS MODE (AIS-catcher)
    # -------------------------------------------------------------------------

    def _start_ais(self, params: dict) -> dict:
        """Start AIS vessel tracking using AIS-catcher."""
        gain = params.get("gain", "33")
        device = params.get("device", "0")
        bias_t = params.get("bias_t", False)
        ppm = params.get("ppm")

        # Find AIS-catcher
        ais_catcher = self._find_ais_catcher()
        if not ais_catcher:
            return {
                "status": "error",
                "message": "AIS-catcher not found. Install from https://github.com/jvde-github/AIS-catcher",
            }

        # Initialize vessel dict
        if not hasattr(self, "ais_vessels"):
            self.ais_vessels = {}
        self.ais_vessels.clear()

        # Build command - output JSON on TCP port 1234
        cmd = [
            ais_catcher,
            "-d",
            str(device),
            "-gr",
            f"TUNER={gain}",
            "-o",
            "4",  # JSON format
            "-N",
            "1234",  # TCP output on port 1234
        ]

        if bias_t:
            cmd.extend(["-gr", "BIASTEE=on"])

        if ppm and str(ppm) != "0":
            cmd.extend(["-p", str(ppm)])

        logger.info(f"Starting AIS-catcher: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            self.processes["ais"] = proc

            time.sleep(2)
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode("utf-8", errors="ignore")
                return {"status": "error", "message": f"AIS-catcher failed: {stderr[:200]}"}

            # Start TCP reader thread
            thread = threading.Thread(target=self._ais_tcp_reader, args=(1234,), daemon=True)
            thread.start()
            self.output_threads["ais"] = thread

            return {"status": "started", "mode": "ais", "tcp_port": 1234, "gps_enabled": gps_manager.is_running}

        except FileNotFoundError:
            return {"status": "error", "message": "AIS-catcher not found"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _find_ais_catcher(self) -> str | None:
        """Find AIS-catcher binary."""
        for name in ["AIS-catcher", "aiscatcher"]:
            path = self._get_tool_path(name)
            if path:
                return path
        for path in ["/usr/local/bin/AIS-catcher", "/usr/bin/AIS-catcher", "/opt/homebrew/bin/AIS-catcher"]:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    def _ais_tcp_reader(self, port: int):
        """Read JSON vessel data from AIS-catcher TCP port."""
        mode = "ais"
        stop_event = self.stop_events.get(mode)
        retry_count = 0

        # Initialize vessel dict
        if not hasattr(self, "ais_vessels"):
            self.ais_vessels = {}

        while not (stop_event and stop_event.is_set()):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect(("localhost", port))
                logger.info(f"Connected to AIS-catcher on port {port}")
                retry_count = 0

                buffer = ""
                sock.settimeout(1.0)

                while not (stop_event and stop_event.is_set()):
                    try:
                        data = sock.recv(4096).decode("utf-8", errors="ignore")
                        if not data:
                            break
                        buffer += data

                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            self._parse_ais_json(line.strip())

                    except socket.timeout:
                        continue

                sock.close()

            except Exception:
                retry_count += 1
                if retry_count >= 10:
                    logger.error("Max AIS retries reached")
                    break
                time.sleep(2)

        logger.info("AIS TCP reader stopped")

    def _parse_ais_json(self, line: str):
        """Parse AIS-catcher JSON output."""
        if not line:
            return

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return

        mmsi = msg.get("mmsi")
        if not mmsi:
            return

        mmsi = str(mmsi)
        vessel = self.ais_vessels.get(mmsi) or {"mmsi": mmsi}
        vessel["last_seen"] = datetime.now(timezone.utc).isoformat()

        # Position
        lat = msg.get("latitude") or msg.get("lat")
        lon = msg.get("longitude") or msg.get("lon")
        if lat is not None and lon is not None:
            try:
                lat, lon = float(lat), float(lon)
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    vessel["lat"] = lat
                    vessel["lon"] = lon
            except (ValueError, TypeError):
                pass

        # Speed and course
        for field, max_val in [("speed", 102.3), ("course", 360)]:
            if field in msg:
                try:
                    val = float(msg[field])
                    if val < max_val:
                        vessel[field] = round(val, 1)
                except (ValueError, TypeError):
                    pass

        if "heading" in msg:
            try:
                heading = int(msg["heading"])
                if heading < 360:
                    vessel["heading"] = heading
            except (ValueError, TypeError):
                pass

        # Static data
        for field in ["name", "callsign", "destination", "shiptype", "ship_type"]:
            if field in msg and msg[field]:
                key = "ship_type" if field == "shiptype" else field
                vessel[key] = str(msg[field]).strip()

        gps_pos = gps_manager.position
        if gps_pos:
            vessel["agent_gps"] = gps_pos

        self.ais_vessels[mmsi] = vessel

    # -------------------------------------------------------------------------
    # ACARS MODE (acarsdec)
    # -------------------------------------------------------------------------

    def _detect_acarsdec_fork(self, acarsdec_path: str) -> str:
        """Detect which acarsdec fork is installed.

        Returns:
            '--output' for f00b4r0 fork (DragonOS)
            '-j' for TLeconte v4+
            '-o' for TLeconte v3.x
        """
        try:
            result = subprocess.run([acarsdec_path], capture_output=True, text=True, timeout=5)
            output = result.stdout + result.stderr

            # f00b4r0 fork uses --output instead of -j/-o
            if "--output" in output:
                return "--output"

            # Parse version for TLeconte
            import re

            version_match = re.search(r"acarsdec[^\d]*v?(\d+)\.(\d+)", output, re.IGNORECASE)
            if version_match:
                major = int(version_match.group(1))
                return "-j" if major >= 4 else "-o"
        except Exception:
            pass
        return "-j"  # Default to TLeconte v4+

    def _start_acars(self, params: dict) -> dict:
        """Start ACARS decoding using acarsdec."""
        gain = params.get("gain", "40")
        device = params.get("device", "0")
        ppm = params.get("ppm")
        frequencies = params.get("frequencies", ["131.550", "130.025", "129.125", "131.525", "131.725"])
        # PPM, like gain, must come before the device argument
        ppm_args = ["-p", str(ppm)] if ppm and str(ppm) != "0" else []

        acarsdec_path = self._get_tool_path("acarsdec")
        if not acarsdec_path:
            return {"status": "error", "message": "acarsdec not found. Install acarsdec."}

        # Detect fork and build appropriate command
        fork_type = self._detect_acarsdec_fork(acarsdec_path)
        cmd = [acarsdec_path]

        if fork_type == "--output":
            # f00b4r0 fork (DragonOS): different syntax
            cmd.extend(["--output", "json:file"])  # stdout
            cmd.extend(["-g", str(gain), *ppm_args])
            cmd.extend(["-m", "256"])  # 3.2 MS/s for wider bandwidth
            cmd.extend(["--rtlsdr", str(device)])
        elif fork_type == "-j":
            # TLeconte v4+
            cmd.extend(["-j", "-g", str(gain), *ppm_args, "-r", str(device)])
        else:
            # TLeconte v3.x
            cmd.extend(["-o", "4", "-g", str(gain), *ppm_args, "-r", str(device)])

        cmd.extend(frequencies)

        logger.info(f"Starting acarsdec: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes["acars"] = proc

            thread = threading.Thread(target=self._acars_output_reader, args=(proc,), daemon=True)
            thread.start()
            self.output_threads["acars"] = thread

            # Wait briefly to verify process started successfully
            time.sleep(0.5)
            if proc.poll() is not None:
                # Process already exited - likely SDR busy or other error
                stderr_output = proc.stderr.read().decode("utf-8", errors="replace")
                del self.processes["acars"]
                return {"status": "error", "message": f"acarsdec failed to start: {stderr_output[:200]}"}

            return {
                "status": "started",
                "mode": "acars",
                "frequencies": frequencies,
                "gps_enabled": gps_manager.is_running,
            }

        except FileNotFoundError:
            return {"status": "error", "message": "acarsdec not found"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _acars_output_reader(self, proc: subprocess.Popen):
        """Read acarsdec JSON output."""
        mode = "acars"
        stop_event = self.stop_events.get(mode)

        try:
            for line in iter(proc.stdout.readline, b""):
                if stop_event and stop_event.is_set():
                    break

                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                    msg["type"] = "acars"
                    msg["received_at"] = datetime.now(timezone.utc).isoformat()

                    gps_pos = gps_manager.position
                    if gps_pos:
                        msg["agent_gps"] = gps_pos

                    snapshots = self.data_snapshots.get(mode, [])
                    snapshots.append(msg)
                    if len(snapshots) > 100:
                        snapshots = snapshots[-100:]
                    self.data_snapshots[mode] = snapshots

                    logger.debug(f"ACARS: {msg.get('tail', 'Unknown')}")

                except json.JSONDecodeError:
                    pass

        except (OSError, ValueError) as e:
            logger.debug(f"ACARS reader stopped: {e}")
        except Exception as e:
            logger.error(f"ACARS reader error: {e}")
        finally:
            with contextlib.suppress(Exception):
                proc.wait(timeout=1)
            logger.info("ACARS reader stopped")

    # -------------------------------------------------------------------------
    # APRS MODE (rtl_fm | direwolf)
    # -------------------------------------------------------------------------

    def _start_aprs(self, params: dict) -> dict:
        """Start APRS decoding using rtl_fm | direwolf."""
        freq = params.get("frequency", "144.390")  # North America APRS
        gain = params.get("gain", "40")
        device = params.get("device", "0")
        ppm = params.get("ppm", "0")

        rtl_fm_path = self._get_tool_path("rtl_fm")
        if not rtl_fm_path:
            return {"status": "error", "message": "rtl_fm not found"}

        direwolf_path = self._get_tool_path("direwolf")
        multimon_path = self._get_tool_path("multimon-ng")
        decoder_path = direwolf_path or multimon_path

        if not decoder_path:
            return {"status": "error", "message": "direwolf or multimon-ng not found"}

        # Initialize state
        if not hasattr(self, "aprs_stations"):
            self.aprs_stations = {}
        self.aprs_stations.clear()

        # Build rtl_fm command for APRS (22050 Hz for AFSK 1200 baud)
        rtl_fm_cmd = [
            rtl_fm_path,
            "-f",
            f"{freq}M",
            "-s",
            "22050",
            "-g",
            str(gain),
            "-d",
            str(device),
            "-E",
            "dc",
            "-A",
            "fast",
        ]
        if ppm and str(ppm) != "0":
            rtl_fm_cmd.extend(["-p", str(ppm)])

        # Build decoder command
        if direwolf_path:
            dw_config = "/tmp/intercept_direwolf.conf"
            try:
                with open(dw_config, "w") as f:
                    f.write("ADEVICE stdin null\nARATE 22050\nMODEM 1200\n")
            except Exception as e:
                return {"status": "error", "message": f"Failed to create direwolf config: {e}"}
            decoder_cmd = [direwolf_path, "-c", dw_config, "-r", "22050", "-t", "0", "-"]
        else:
            decoder_cmd = [multimon_path, "-t", "raw", "-a", "AFSK1200", "-"]

        logger.info(f"Starting APRS: {' '.join(rtl_fm_cmd)} | {' '.join(decoder_cmd)}")

        try:
            rtl_fm_proc = subprocess.Popen(
                rtl_fm_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            decoder_proc = subprocess.Popen(
                decoder_cmd,
                stdin=rtl_fm_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            rtl_fm_proc.stdout.close()

            self.processes["aprs"] = decoder_proc
            self.processes["aprs_rtl"] = rtl_fm_proc

            # Wait briefly to verify processes started successfully
            time.sleep(0.5)
            if rtl_fm_proc.poll() is not None:
                stderr_output = rtl_fm_proc.stderr.read().decode("utf-8", errors="replace")
                decoder_proc.terminate()
                del self.processes["aprs"]
                del self.processes["aprs_rtl"]
                return {"status": "error", "message": f"rtl_fm failed to start: {stderr_output[:200]}"}

            thread = threading.Thread(
                target=self._aprs_output_reader, args=(decoder_proc, direwolf_path is not None), daemon=True
            )
            thread.start()
            self.output_threads["aprs"] = thread

            return {
                "status": "started",
                "mode": "aprs",
                "frequency": freq,
                "decoder": "direwolf" if direwolf_path else "multimon-ng",
                "gps_enabled": gps_manager.is_running,
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _aprs_output_reader(self, proc: subprocess.Popen, is_direwolf: bool):
        """Read and parse APRS packets."""
        mode = "aprs"
        stop_event = self.stop_events.get(mode)

        try:
            for line in iter(proc.stdout.readline, b""):
                if stop_event and stop_event.is_set():
                    break

                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                parsed = self._parse_aprs_packet(line)
                if parsed:
                    parsed["received_at"] = datetime.now(timezone.utc).isoformat()

                    gps_pos = gps_manager.position
                    if gps_pos:
                        parsed["agent_gps"] = gps_pos

                    callsign = parsed.get("callsign")
                    if callsign:
                        self.aprs_stations[callsign] = parsed

                    snapshots = self.data_snapshots.get(mode, [])
                    snapshots.append(parsed)
                    if len(snapshots) > 100:
                        snapshots = snapshots[-100:]
                    self.data_snapshots[mode] = snapshots

                    logger.debug(f"APRS: {callsign}")

        except (OSError, ValueError) as e:
            logger.debug(f"APRS reader stopped: {e}")
        except Exception as e:
            logger.error(f"APRS reader error: {e}")
        finally:
            with contextlib.suppress(Exception):
                proc.wait(timeout=1)
            if "aprs_rtl" in self.processes:
                try:
                    rtl_proc = self.processes["aprs_rtl"]
                    if rtl_proc.poll() is None:
                        rtl_proc.terminate()
                    del self.processes["aprs_rtl"]
                except Exception:
                    pass
            logger.info("APRS reader stopped")

    def _parse_aprs_packet(self, line: str) -> dict | None:
        """Parse APRS packet from direwolf or multimon-ng."""
        if not line:
            return None

        # Normalize common decoder prefixes before parsing.
        # multimon-ng: "AFSK1200: ..."
        # direwolf: "[0.4] ...", "[0L] ..."
        line = line.strip()
        if line.startswith("AFSK1200:"):
            line = line[9:].strip()
        line = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", line)

        match = re.match(r"([A-Z0-9-]+)>([^:]+):(.+)", line)
        if not match:
            return None

        callsign = match.group(1)
        path = match.group(2)
        data = match.group(3)

        packet = {
            "type": "aprs",
            "callsign": callsign,
            "path": path,
            "raw": data,
        }

        # Try to extract position
        pos_match = re.search(r"[!=/@](\d{4}\.\d{2})([NS])[/\\](\d{5}\.\d{2})([EW])", data)
        if pos_match:
            lat = float(pos_match.group(1)[:2]) + float(pos_match.group(1)[2:]) / 60
            if pos_match.group(2) == "S":
                lat = -lat
            lon = float(pos_match.group(3)[:3]) + float(pos_match.group(3)[3:]) / 60
            if pos_match.group(4) == "W":
                lon = -lon
            packet["lat"] = round(lat, 6)
            packet["lon"] = round(lon, 6)

        return packet

    # -------------------------------------------------------------------------
    # RTLAMR MODE (rtl_tcp + rtlamr)
    # -------------------------------------------------------------------------

    def _start_rtlamr(self, params: dict) -> dict:
        """Start utility meter reading using rtl_tcp + rtlamr."""
        freq = params.get("frequency", "912.0")
        device = params.get("device", "0")
        gain = params.get("gain", "40")
        ppm = params.get("ppm")
        msg_type = params.get("msgtype", "scm")
        filter_id = params.get("filterid")

        rtl_tcp_path = self._get_tool_path("rtl_tcp")
        rtlamr_path = self._get_tool_path("rtlamr")

        if not rtl_tcp_path:
            return {"status": "error", "message": "rtl_tcp not found. Install rtl-sdr."}
        if not rtlamr_path:
            return {"status": "error", "message": "rtlamr not found. Install from https://github.com/bemasher/rtlamr"}

        # Start rtl_tcp server
        rtl_tcp_cmd = [rtl_tcp_path, "-a", "127.0.0.1", "-p", "1234", "-d", str(device)]
        if gain:
            rtl_tcp_cmd.extend(["-g", str(gain)])
        if ppm and str(ppm) != "0":
            rtl_tcp_cmd.extend(["-P", str(ppm)])  # -p is the listen port

        logger.info(f"Starting rtl_tcp: {' '.join(rtl_tcp_cmd)}")

        try:
            rtl_tcp_proc = subprocess.Popen(
                rtl_tcp_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes["rtlamr_tcp"] = rtl_tcp_proc

            time.sleep(2)
            if rtl_tcp_proc.poll() is not None:
                stderr = rtl_tcp_proc.stderr.read().decode("utf-8", errors="ignore")
                return {"status": "error", "message": f"rtl_tcp failed: {stderr[:200]}"}

            # Build rtlamr command
            rtlamr_cmd = [
                rtlamr_path,
                "-server=127.0.0.1:1234",
                f"-msgtype={msg_type}",
                "-format=json",
                f"-centerfreq={int(float(freq) * 1e6)}",
                "-unique=true",
            ]
            if filter_id:
                rtlamr_cmd.append(f"-filterid={filter_id}")

            logger.info(f"Starting rtlamr: {' '.join(rtlamr_cmd)}")

            rtlamr_proc = subprocess.Popen(
                rtlamr_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes["rtlamr"] = rtlamr_proc

            thread = threading.Thread(target=self._rtlamr_output_reader, args=(rtlamr_proc,), daemon=True)
            thread.start()
            self.output_threads["rtlamr"] = thread

            return {
                "status": "started",
                "mode": "rtlamr",
                "frequency": freq,
                "msgtype": msg_type,
                "gps_enabled": gps_manager.is_running,
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _rtlamr_output_reader(self, proc: subprocess.Popen):
        """Read rtlamr JSON output."""
        mode = "rtlamr"
        stop_event = self.stop_events.get(mode)

        try:
            for line in iter(proc.stdout.readline, b""):
                if stop_event and stop_event.is_set():
                    break

                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                    msg["type"] = "rtlamr"
                    msg["received_at"] = datetime.now(timezone.utc).isoformat()

                    gps_pos = gps_manager.position
                    if gps_pos:
                        msg["agent_gps"] = gps_pos

                    snapshots = self.data_snapshots.get(mode, [])
                    snapshots.append(msg)
                    if len(snapshots) > 100:
                        snapshots = snapshots[-100:]
                    self.data_snapshots[mode] = snapshots

                    logger.debug(f"RTLAMR: meter {msg.get('Message', {}).get('ID', 'Unknown')}")

                except json.JSONDecodeError:
                    pass

        except (OSError, ValueError) as e:
            logger.debug(f"RTLAMR reader stopped: {e}")
        except Exception as e:
            logger.error(f"RTLAMR reader error: {e}")
        finally:
            with contextlib.suppress(Exception):
                proc.wait(timeout=1)
            if "rtlamr_tcp" in self.processes:
                try:
                    tcp_proc = self.processes["rtlamr_tcp"]
                    if tcp_proc.poll() is None:
                        tcp_proc.terminate()
                    del self.processes["rtlamr_tcp"]
                except Exception:
                    pass
            logger.info("RTLAMR reader stopped")

    # -------------------------------------------------------------------------
    # DSC MODE (rtl_fm | dsc-decoder) - Digital Selective Calling
    # -------------------------------------------------------------------------

    def _start_dsc(self, params: dict) -> dict:
        """Start DSC (VHF Channel 70) decoding using Intercept's DSCDecoder."""
        device = params.get("device", "0")
        gain = params.get("gain", "40")
        ppm = params.get("ppm", "0")
        freq = "156.525"  # DSC Channel 70

        rtl_fm_path = self._get_tool_path("rtl_fm")
        if not rtl_fm_path:
            return {"status": "error", "message": "rtl_fm not found"}

        # Initialize DSC messages list
        if not hasattr(self, "dsc_messages"):
            self.dsc_messages = []

        # Build rtl_fm command for DSC (48kHz sample rate)
        rtl_fm_cmd = [
            rtl_fm_path,
            "-f",
            f"{freq}M",
            "-s",
            "48000",
            "-g",
            str(gain),
            "-d",
            str(device),
        ]
        if ppm and str(ppm) != "0":
            rtl_fm_cmd.extend(["-p", str(ppm)])

        logger.info(f"Starting DSC: {' '.join(rtl_fm_cmd)}")

        try:
            rtl_fm_proc = subprocess.Popen(
                rtl_fm_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.processes["dsc"] = rtl_fm_proc

            # Wait briefly to verify process started successfully
            time.sleep(0.5)
            if rtl_fm_proc.poll() is not None:
                stderr_output = rtl_fm_proc.stderr.read().decode("utf-8", errors="replace")
                del self.processes["dsc"]
                return {"status": "error", "message": f"rtl_fm failed to start: {stderr_output[:200]}"}

            # Start output reader thread using Intercept's DSCDecoder
            thread = threading.Thread(target=self._dsc_output_reader, args=(rtl_fm_proc,), daemon=True)
            thread.start()
            self.output_threads["dsc"] = thread

            return {
                "status": "started",
                "mode": "dsc",
                "frequency": freq,
                "channel": 70,
                "gps_enabled": gps_manager.is_running,
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _dsc_output_reader(self, proc: subprocess.Popen):
        """Read rtl_fm audio and decode DSC using Intercept's DSCDecoder."""
        mode = "dsc"
        stop_event = self.stop_events.get(mode)

        try:
            # Use Intercept's DSC decoder
            from utils.dsc.decoder import DSCDecoder

            decoder = DSCDecoder(sample_rate=48000)
            logger.info("Using Intercept's DSCDecoder")

            chunk_size = 9600  # 0.1 seconds at 48kHz, 16-bit

            while not (stop_event and stop_event.is_set()):
                audio_data = proc.stdout.read(chunk_size)
                if not audio_data:
                    break

                for message in decoder.process_audio(audio_data):
                    message["received_at"] = datetime.now(timezone.utc).isoformat()

                    gps_pos = gps_manager.position
                    if gps_pos:
                        message["agent_gps"] = gps_pos

                    # Store message
                    self.dsc_messages.append(message)
                    if len(self.dsc_messages) > 100:
                        self.dsc_messages = self.dsc_messages[-100:]

                    self.data_snapshots[mode] = self.dsc_messages.copy()
                    logger.info(f"DSC message: {message.get('category')} from {message.get('source_mmsi')}")

        except ImportError:
            logger.warning("DSCDecoder not available (missing scipy/numpy)")
        except (OSError, ValueError) as e:
            logger.debug(f"DSC reader stopped: {e}")
        except Exception as e:
            logger.error(f"DSC reader error: {e}")
        finally:
            with contextlib.suppress(Exception):
                proc.wait(timeout=1)
            logger.info("DSC reader stopped")

    # -------------------------------------------------------------------------
    # TSCM MODE (Technical Surveillance Countermeasures)
    # -------------------------------------------------------------------------

    def _start_tscm(self, params: dict) -> dict:
        """Start TSCM scanning - uses existing Intercept scanning functions."""
        # Initialize state
        if not hasattr(self, "tscm_baseline"):
            self.tscm_baseline = {}
        if not hasattr(self, "tscm_anomalies"):
            self.tscm_anomalies = []
        if not hasattr(self, "tscm_rf_signals"):
            self.tscm_rf_signals = []
        if not hasattr(self, "tscm_wifi_clients"):
            self.tscm_wifi_clients = {}
        self.tscm_anomalies.clear()
        self.tscm_wifi_clients.clear()

        # Get params for what to scan
        scan_wifi = params.get("wifi", True)
        scan_bt = params.get("bluetooth", True)
        scan_rf = params.get("rf", True)
        wifi_interface = params.get("wifi_interface") or params.get("interface")
        bt_adapter = params.get("bt_interface") or params.get("adapter", "hci0")
        sdr_device = params.get("sdr_device", params.get("device", 0))
        sweep_type = params.get("sweep_type")

        # Get baseline_id for comparison (same as local mode)
        baseline_id = params.get("baseline_id")

        started_scans = []

        # Start the combined TSCM scanner thread using existing Intercept functions
        thread = threading.Thread(
            target=self._tscm_scanner_thread,
            args=(scan_wifi, scan_bt, scan_rf, wifi_interface, bt_adapter, sdr_device, baseline_id, sweep_type),
            daemon=True,
        )
        thread.start()
        self.output_threads["tscm"] = thread

        if scan_wifi:
            started_scans.append("wifi")
        if scan_bt:
            started_scans.append("bluetooth")
        if scan_rf:
            started_scans.append("rf")

        return {
            "status": "started",
            "mode": "tscm",
            "note": f"TSCM scanning {', '.join(started_scans) if started_scans else 'using existing data'}",
            "gps_enabled": gps_manager.is_running,
            "scanning": started_scans,
        }

    def _tscm_scanner_thread(
        self,
        scan_wifi: bool,
        scan_bt: bool,
        scan_rf: bool,
        wifi_interface: str | None,
        bt_adapter: str,
        sdr_device: int,
        baseline_id: int | None = None,
        sweep_type: str | None = None,
    ):
        """Combined TSCM scanner using existing Intercept functions.

        NOTE: This matches local mode behavior exactly:
        - If baseline_id provided, loads baseline and detects 'new_device' threats
        - If no baseline, only 'anomaly' and 'hidden_camera' threats are detected
        - Each new device seen during sweep is analyzed once
        """
        logger.info("TSCM thread starting...")
        mode = "tscm"
        stop_event = self.stop_events.get(mode)

        # Import existing Intercept TSCM functions
        from routes.tscm import _scan_bluetooth_devices, _scan_rf_signals, _scan_wifi_clients, _scan_wifi_networks

        logger.info("TSCM imports successful")

        sweep_ranges = None
        if sweep_type:
            try:
                from data.tscm_frequencies import SWEEP_PRESETS, get_sweep_preset

                preset = get_sweep_preset(sweep_type) or SWEEP_PRESETS.get("standard")
                sweep_ranges = preset.get("ranges") if preset else None
            except Exception:
                sweep_ranges = None

        # Load baseline if specified (same as local mode)
        baseline = None
        if baseline_id and HAS_BASELINE_DB and get_tscm_baseline:
            baseline = get_tscm_baseline(baseline_id)
            if baseline:
                logger.info(f"TSCM loaded baseline '{baseline.get('name')}' (ID: {baseline_id})")
            else:
                logger.warning(f"TSCM baseline ID {baseline_id} not found")

        # Initialize detector and correlation engine (same as local mode)
        if HAS_TSCM_MODULES and ThreatDetector:
            self._tscm_detector = ThreatDetector(baseline=baseline)
            self._tscm_correlation = CorrelationEngine() if CorrelationEngine else None
            if baseline:
                logger.info("TSCM detector initialized with baseline - will detect 'new_device' threats")
            else:
                logger.info("TSCM detector initialized without baseline - only 'anomaly'/'hidden_camera' threats")
        else:
            self._tscm_detector = None
            self._tscm_correlation = None

        # Track devices seen during this sweep (like local mode's all_wifi/all_bt dicts)
        seen_wifi = {}
        seen_wifi_clients = {}
        seen_bt = {}

        last_rf_scan = 0
        rf_scan_interval = 30

        while not (stop_event and stop_event.is_set()):
            try:
                current_time = time.time()

                # WiFi scan using Intercept's function (same as local mode)
                if scan_wifi:
                    try:
                        wifi_networks = _scan_wifi_networks(wifi_interface or "")
                        for net in wifi_networks:
                            bssid = net.get("bssid", "").upper()
                            if bssid and bssid not in seen_wifi:
                                # First time seeing this device during sweep
                                seen_wifi[bssid] = net

                                # Enrich with classification/scoring
                                enriched = dict(net)
                                # Ensure power/signal is numeric (scanner may return string)
                                if "power" in enriched:
                                    try:
                                        enriched["power"] = int(enriched["power"])
                                    except (ValueError, TypeError):
                                        enriched["power"] = -100
                                if "signal" in enriched and enriched["signal"] is not None:
                                    try:
                                        enriched["signal"] = int(enriched["signal"])
                                    except (ValueError, TypeError):
                                        enriched["signal"] = -100

                                # Analyze for threats (same as local mode)
                                if self._tscm_detector:
                                    threat = self._tscm_detector.analyze_wifi_device(enriched)
                                    if threat:
                                        self.tscm_anomalies.append(threat)
                                        if len(self.tscm_anomalies) > 100:
                                            self.tscm_anomalies = self.tscm_anomalies[-100:]
                                        print(
                                            f"[TSCM] WiFi threat: {threat.get('threat_type')} - {threat.get('name')}",
                                            flush=True,
                                        )

                                    classification = self._tscm_detector.classify_wifi_device(enriched)
                                    enriched["is_new"] = not classification.get("in_baseline", False)
                                    enriched["reasons"] = classification.get("reasons", [])

                                if self._tscm_correlation:
                                    profile = self._tscm_correlation.analyze_wifi_device(enriched)
                                    enriched["classification"] = profile.risk_level.value
                                    enriched["score"] = profile.total_score
                                    enriched["score_modifier"] = profile.score_modifier
                                    enriched["known_device"] = profile.known_device
                                    enriched["known_device_name"] = profile.known_device_name
                                    enriched["indicators"] = [
                                        {"type": i.type.value, "desc": i.description} for i in profile.indicators
                                    ]
                                    enriched["recommended_action"] = profile.recommended_action

                                self.wifi_networks[bssid] = enriched

                        # WiFi clients (monitor mode only)
                        try:
                            wifi_clients = _scan_wifi_clients(wifi_interface or "")
                            for client in wifi_clients:
                                mac = (client.get("mac") or "").upper()
                                if not mac or mac in seen_wifi_clients:
                                    continue
                                seen_wifi_clients[mac] = client

                                rssi_val = client.get("rssi_current")
                                if rssi_val is None:
                                    rssi_val = client.get("rssi_median") or client.get("rssi_ema")

                                client_device = {
                                    "mac": mac,
                                    "vendor": client.get("vendor"),
                                    "name": client.get("vendor") or "WiFi Client",
                                    "rssi": rssi_val,
                                    "associated_bssid": client.get("associated_bssid"),
                                    "probed_ssids": client.get("probed_ssids", []),
                                    "probe_count": client.get("probe_count", len(client.get("probed_ssids", []))),
                                    "is_client": True,
                                }

                                if self._tscm_correlation:
                                    profile = self._tscm_correlation.analyze_wifi_device(client_device)
                                    client_device["classification"] = profile.risk_level.value
                                    client_device["score"] = profile.total_score
                                    client_device["score_modifier"] = profile.score_modifier
                                    client_device["known_device"] = profile.known_device
                                    client_device["known_device_name"] = profile.known_device_name
                                    client_device["indicators"] = [
                                        {"type": i.type.value, "desc": i.description} for i in profile.indicators
                                    ]
                                    client_device["recommended_action"] = profile.recommended_action

                                self.tscm_wifi_clients[mac] = client_device
                        except Exception as e:
                            logger.debug(f"WiFi client scan error: {e}")
                    except Exception as e:
                        logger.debug(f"WiFi scan error: {e}")

                # Bluetooth scan using Intercept's function (same as local mode)
                if scan_bt:
                    try:
                        bt_devices = _scan_bluetooth_devices(bt_adapter, duration=5)
                        for dev in bt_devices:
                            mac = dev.get("mac", "").upper()
                            if mac and mac not in seen_bt:
                                # First time seeing this device during sweep
                                seen_bt[mac] = dev

                                # Enrich with classification/scoring
                                enriched = dict(dev)
                                # Ensure rssi/signal is numeric (scanner may return string)
                                if "rssi" in enriched and enriched["rssi"] is not None:
                                    try:
                                        enriched["rssi"] = int(enriched["rssi"])
                                    except (ValueError, TypeError):
                                        enriched["rssi"] = -100

                                # Analyze for threats (same as local mode)
                                if self._tscm_detector:
                                    threat = self._tscm_detector.analyze_bt_device(enriched)
                                    if threat:
                                        self.tscm_anomalies.append(threat)
                                        if len(self.tscm_anomalies) > 100:
                                            self.tscm_anomalies = self.tscm_anomalies[-100:]
                                        logger.info(
                                            f"TSCM BT threat: {threat.get('threat_type')} - {threat.get('name')}"
                                        )

                                    classification = self._tscm_detector.classify_bt_device(enriched)
                                    enriched["is_new"] = not classification.get("in_baseline", False)
                                    enriched["reasons"] = classification.get("reasons", [])

                                if self._tscm_correlation:
                                    profile = self._tscm_correlation.analyze_bluetooth_device(enriched)
                                    enriched["classification"] = profile.risk_level.value
                                    enriched["score"] = profile.total_score
                                    enriched["score_modifier"] = profile.score_modifier
                                    enriched["known_device"] = profile.known_device
                                    enriched["known_device_name"] = profile.known_device_name
                                    enriched["indicators"] = [
                                        {"type": i.type.value, "desc": i.description} for i in profile.indicators
                                    ]
                                    enriched["recommended_action"] = profile.recommended_action

                                self.bluetooth_devices[mac] = enriched
                    except Exception as e:
                        logger.debug(f"Bluetooth scan error: {e}")

                # RF scan using Intercept's function (less frequently)
                if scan_rf and (current_time - last_rf_scan) >= rf_scan_interval:
                    try:
                        # Pass a stop check that uses our stop_event (not the module's _sweep_running)
                        def agent_stop_check():
                            return stop_event and stop_event.is_set()

                        rf_signals = _scan_rf_signals(
                            sdr_device, stop_check=agent_stop_check, sweep_ranges=sweep_ranges
                        )

                        # Analyze each RF signal like local mode does
                        analyzed_signals = []
                        rf_threats = []
                        for signal in rf_signals:
                            analyzed = dict(signal)
                            is_threat = False

                            # Use detector to analyze for threats (same as local mode)
                            if hasattr(self, "_tscm_detector") and self._tscm_detector:
                                threat = self._tscm_detector.analyze_rf_signal(signal)
                                if threat:
                                    rf_threats.append(threat)
                                    is_threat = True
                                classification = self._tscm_detector.classify_rf_signal(signal)
                                analyzed["is_new"] = not classification.get("in_baseline", False)
                                analyzed["reasons"] = classification.get("reasons", [])

                            # Use correlation engine for scoring (same as local mode)
                            if hasattr(self, "_tscm_correlation") and self._tscm_correlation:
                                profile = self._tscm_correlation.analyze_rf_signal(signal)
                                analyzed["classification"] = profile.risk_level.value
                                analyzed["score"] = profile.total_score
                                analyzed["score_modifier"] = profile.score_modifier
                                analyzed["known_device"] = profile.known_device
                                analyzed["known_device_name"] = profile.known_device_name
                                analyzed["indicators"] = [
                                    {"type": i.type.value, "desc": i.description} for i in profile.indicators
                                ]

                            analyzed["is_threat"] = is_threat
                            analyzed_signals.append(analyzed)

                        # Add RF threats to anomalies list
                        if rf_threats:
                            self.tscm_anomalies.extend(rf_threats)
                            if len(self.tscm_anomalies) > 100:
                                self.tscm_anomalies = self.tscm_anomalies[-100:]
                            for threat in rf_threats:
                                logger.info(f"TSCM RF threat: {threat.get('threat_type')} - {threat.get('identifier')}")

                        self.tscm_rf_signals = analyzed_signals
                        logger.info(f"RF scan found {len(analyzed_signals)} signals")
                        last_rf_scan = current_time
                    except Exception as e:
                        logger.debug(f"RF scan error: {e}")

                # Sleep between scan cycles (same interval as local mode)
                time.sleep(5)

            except Exception as e:
                logger.error(f"TSCM scanner error: {e}")
                time.sleep(5)

        logger.info("TSCM scanner stopped")

    # -------------------------------------------------------------------------
    # SATELLITE MODE (TLE-based pass prediction)
    # -------------------------------------------------------------------------

    def _start_satellite(self, params: dict) -> dict:
        """Start satellite pass prediction - no SDR needed."""
        lat = params.get("lat", params.get("latitude"))
        lon = params.get("lon", params.get("longitude"))
        min_elevation = params.get("min_elevation", 10)

        if lat is None or lon is None:
            gps_pos = gps_manager.position
            if gps_pos:
                lat = gps_pos.get("lat")
                lon = gps_pos.get("lon")

        if lat is None or lon is None:
            return {"status": "error", "message": "Observer location required (lat/lon)"}

        thread = threading.Thread(
            target=self._satellite_predictor, args=(float(lat), float(lon), int(min_elevation)), daemon=True
        )
        thread.start()
        self.output_threads["satellite"] = thread

        return {
            "status": "started",
            "mode": "satellite",
            "observer": {"lat": lat, "lon": lon},
            "min_elevation": min_elevation,
            "note": "Satellite pass prediction - no SDR required",
        }

    def _satellite_predictor(self, lat: float, lon: float, min_elevation: int):
        """Calculate satellite passes using TLE data."""
        mode = "satellite"
        stop_event = self.stop_events.get(mode)

        try:
            from skyfield.api import EarthSatellite, Topos, load

            from utils import tle_store

            ts = load.timescale(builtin=True)
            satellites = [EarthSatellite(l1, l2, name, ts) for name, l1, l2 in tle_store.all_tles().values()]
            observer = Topos(latitude_degrees=lat, longitude_degrees=lon)

            logger.info(f"Satellite predictor: {len(satellites)} satellites loaded")

            while not (stop_event and stop_event.is_set()):
                passes = []
                now = ts.now()
                end = ts.utc(now.utc_datetime().year, now.utc_datetime().month, now.utc_datetime().day + 1)

                for sat in satellites[:20]:
                    try:
                        t, events = sat.find_events(observer, now, end, altitude_degrees=min_elevation)

                        for ti, event in zip(t, events):
                            if event == 0:  # Rise
                                difference = sat - observer
                                topocentric = difference.at(ti)
                                alt, az, _ = topocentric.altaz()
                                passes.append(
                                    {
                                        "satellite": sat.name,
                                        "rise_time": ti.utc_iso(),
                                        "rise_azimuth": round(az.degrees, 1),
                                        "max_elevation": min_elevation,
                                    }
                                )
                    except Exception:
                        continue

                self.data_snapshots[mode] = passes[:50]
                time.sleep(300)

        except ImportError:
            logger.warning("skyfield not installed - satellite prediction unavailable")
            self.data_snapshots[mode] = [{"error": "skyfield not installed"}]
        except Exception as e:
            logger.error(f"Satellite predictor error: {e}")

        logger.info("Satellite predictor stopped")

    # -------------------------------------------------------------------------
    # LISTENING POST MODE (Spectrum scanner - signal detection only)
    # -------------------------------------------------------------------------

    def _start_listening_post(self, params: dict) -> dict:
        """
        Start listening post / spectrum scanner.

        Note: Full FFT streaming isn't practical over HTTP agents.
        Instead provides signal detection events and activity log.
        """
        start_freq = params.get("start_freq", 88.0)
        end_freq = params.get("end_freq", 108.0)
        # Step is sent in kHz from frontend, convert to MHz
        step_khz = params.get("step", 100)
        step = step_khz / 1000.0  # Convert kHz to MHz
        modulation = params.get("modulation", "wfm")
        squelch = params.get("squelch", 20)
        device = params.get("device", "0")
        gain = params.get("gain", "40")
        dwell_time = params.get("dwell_time", 1.0)

        rtl_fm_path = self._get_tool_path("rtl_fm")
        if not rtl_fm_path:
            return {"status": "error", "message": "rtl_fm not found"}

        # Quick SDR availability check - try to run rtl_fm briefly
        test_proc = None
        try:
            test_proc = subprocess.Popen(
                [rtl_fm_path, "-f", f"{start_freq}M", "-d", str(device), "-g", str(gain)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            time.sleep(0.5)
            if test_proc.poll() is not None:
                stderr = test_proc.stderr.read().decode("utf-8", errors="ignore")
                return {"status": "error", "message": f"SDR not available: {stderr[:200]}"}
            # SDR is available - terminate test process
            test_proc.terminate()
            try:
                test_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                test_proc.kill()
                test_proc.wait(timeout=1)
        except Exception as e:
            # Ensure test process is killed on any error
            if test_proc and test_proc.poll() is None:
                test_proc.kill()
                with contextlib.suppress(Exception):
                    test_proc.wait(timeout=1)
            return {"status": "error", "message": f"SDR check failed: {str(e)}"}

        # Initialize state
        if not hasattr(self, "listening_post_activity"):
            self.listening_post_activity = []
        self.listening_post_activity.clear()
        self.listening_post_current_freq = float(start_freq)

        thread = threading.Thread(
            target=self._listening_post_scanner,
            args=(
                float(start_freq),
                float(end_freq),
                float(step),
                modulation,
                int(squelch),
                str(device),
                str(gain),
                float(dwell_time),
            ),
            daemon=True,
        )
        thread.start()
        self.output_threads["listening_post"] = thread

        return {
            "status": "started",
            "mode": "listening_post",
            "start_freq": start_freq,
            "end_freq": end_freq,
            "step": step,
            "modulation": modulation,
            "dwell_time": dwell_time,
            "note": "Provides signal detection events, not full FFT data",
            "gps_enabled": gps_manager.is_running,
        }

    def _listening_post_scanner(
        self,
        start_freq: float,
        end_freq: float,
        step: float,
        modulation: str,
        squelch: int,
        device: str,
        gain: str,
        dwell_time: float = 1.0,
    ):
        """Scan frequency range and report signal detections."""
        import fcntl
        import os
        import select

        mode = "listening_post"
        stop_event = self.stop_events.get(mode)

        rtl_fm_path = self._get_tool_path("rtl_fm")
        current_freq = start_freq
        scan_direction = 1
        self.listening_post_freqs_scanned = 0

        logger.info(f"Listening post scanner starting: {start_freq}-{end_freq} MHz, step {step}, dwell {dwell_time}s")

        while not (stop_event and stop_event.is_set()):
            self.listening_post_current_freq = current_freq

            cmd = [
                rtl_fm_path,
                "-f",
                f"{current_freq}M",
                "-M",
                modulation,
                "-s",
                "22050",
                "-g",
                gain,
                "-d",
                device,
                "-l",
                str(squelch),
            ]

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                # Set stdout to non-blocking
                fd = proc.stdout.fileno()
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

                signal_detected = False
                start_time = time.time()

                while time.time() - start_time < dwell_time:
                    if stop_event and stop_event.is_set():
                        break

                    # Use select for non-blocking read with timeout
                    ready, _, _ = select.select([proc.stdout], [], [], 0.1)
                    if ready:
                        try:
                            data = proc.stdout.read(2205)
                            if data and len(data) > 10:
                                # Simple signal detection via audio level
                                try:
                                    samples = [
                                        int.from_bytes(data[i : i + 2], "little", signed=True)
                                        for i in range(0, min(len(data) - 1, 1000), 2)
                                    ]
                                    if samples:
                                        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
                                        if rms > 500:
                                            signal_detected = True
                                except Exception:
                                    pass
                        except (OSError, BlockingIOError):
                            pass

                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)

                self.listening_post_freqs_scanned += 1

                if signal_detected:
                    event = {
                        "type": "signal_found",
                        "frequency": current_freq,
                        "modulation": modulation,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                    }

                    gps_pos = gps_manager.position
                    if gps_pos:
                        event["agent_gps"] = gps_pos

                    self.listening_post_activity.append(event)
                    if len(self.listening_post_activity) > 500:
                        self.listening_post_activity = self.listening_post_activity[-500:]

                    self.data_snapshots[mode] = self.listening_post_activity.copy()
                    logger.info(f"Listening post: signal at {current_freq} MHz")

            except Exception as e:
                logger.debug(f"Scanner error at {current_freq}: {e}")

            # Move to next frequency
            current_freq += step * scan_direction
            if current_freq >= end_freq:
                current_freq = end_freq
                scan_direction = -1
            elif current_freq <= start_freq:
                current_freq = start_freq
                scan_direction = 1

            time.sleep(0.1)

        logger.info("Listening post scanner stopped")


# Global mode manager
mode_manager = ModeManager()
_start_time = time.time()


# =============================================================================
# Data Push Loop
# =============================================================================


class DataPushLoop(threading.Thread):
    """Background thread that periodically pushes mode data to controller."""

    def __init__(self, interval_seconds: float = 5.0):
        super().__init__()
        self.daemon = True
        self.interval = interval_seconds
        self.stop_event = threading.Event()

    def run(self):
        """Main push loop."""
        logger.info(f"Data push loop started (interval: {self.interval}s)")

        while not self.stop_event.is_set():
            if push_client and push_client.running:
                # Push data for all running modes
                for mode in list(mode_manager.running_modes.keys()):
                    try:
                        data = mode_manager.get_mode_data(mode)
                        if data.get("data"):  # Only push if there's data
                            push_client.enqueue(scan_type=mode, payload=data, interface=None)
                    except Exception as e:
                        logger.warning(f"Failed to push {mode} data: {e}")

            # Wait for next interval
            self.stop_event.wait(self.interval)

        logger.info("Data push loop stopped")

    def stop(self):
        """Stop the push loop."""
        self.stop_event.set()


# Global push loop
data_push_loop: DataPushLoop | None = None


# =============================================================================
# HTTP Request Handler
# =============================================================================


class InterceptAgentHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the agent API."""

    # Disable default logging
    def log_message(self, format, *args):
        logger.debug(f"{self.client_address[0]} - {format % args}")

    def _check_ip_allowed(self) -> bool:
        """Check if client IP is allowed."""
        if not config.allowed_ips:
            return True

        client_ip = self.client_address[0]
        return client_ip in config.allowed_ips

    def _send_json(self, data: dict, status: int = 200):
        """Send JSON response."""
        body = json.dumps(data).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        if config.allow_cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message: str, status: int = 400):
        """Send error response."""
        self._send_json({"error": message}, status)

    def _read_body(self) -> dict:
        """Read and parse JSON body."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}

        body = self.rfile.read(content_length)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _parse_path(self) -> tuple[str, dict]:
        """Parse URL path and query parameters."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)
        # Flatten single-value query params
        params = {k: v[0] if len(v) == 1 else v for k, v in query.items()}
        return path, params

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        if config.allow_cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        if not self._check_ip_allowed():
            self._send_error("Forbidden", 403)
            return

        path, params = self._parse_path()

        # Route handling
        if path == "/capabilities":
            self._send_json(mode_manager.detect_capabilities())

        elif path == "/status":
            self._send_json(mode_manager.get_status())

        elif path == "/health":
            self._send_json({"status": "healthy", "version": AGENT_VERSION})

        elif path == "/gps":
            gps_pos = gps_manager.position
            self._send_json(
                {
                    "available": gps_manager.is_running,
                    "position": gps_pos,
                }
            )

        elif path == "/config":
            # Return non-sensitive config
            cfg = config.to_dict()
            if "controller_api_key" in cfg:
                del cfg["controller_api_key"]
            self._send_json(cfg)

        elif path.startswith("/") and path.count("/") == 2:
            # /{mode}/status or /{mode}/data
            parts = path.split("/")
            mode = parts[1]
            action = parts[2]

            if action == "status":
                self._send_json(mode_manager.get_mode_status(mode))
            elif action == "data":
                self._send_json(mode_manager.get_mode_data(mode))
            else:
                self._send_error("Not found", 404)

        else:
            self._send_error("Not found", 404)

    def do_POST(self):
        """Handle POST requests."""
        if not self._check_ip_allowed():
            self._send_error("Forbidden", 403)
            return

        path, _ = self._parse_path()
        body = self._read_body()

        if path == "/config":
            # Update running config (limited fields)
            if "push_enabled" in body:
                config.push_enabled = bool(body["push_enabled"])
            if "push_interval" in body:
                config.push_interval = int(body["push_interval"])
            self._send_json({"status": "updated", "config": config.to_dict()})

        elif path == "/wifi/monitor":
            # Enable/disable monitor mode on WiFi interface
            result = mode_manager.toggle_monitor_mode(body)
            status = 200 if result.get("status") == "success" else 400
            self._send_json(result, status)

        elif path.startswith("/") and path.count("/") == 2:
            # /{mode}/start or /{mode}/stop
            parts = path.split("/")
            mode = parts[1]
            action = parts[2]

            if action == "start":
                result = mode_manager.start_mode(mode, body)
                # Accept both 'started' and 'success' as valid (quick scans return 'success')
                status = 200 if result.get("status") in ("started", "success") else 400
                self._send_json(result, status)
            elif action == "stop":
                result = mode_manager.stop_mode(mode)
                self._send_json(result)
            else:
                self._send_error("Not found", 404)

        else:
            self._send_error("Not found", 404)


# =============================================================================
# Threaded HTTP Server
# =============================================================================


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server."""

    allow_reuse_address = True
    daemon_threads = True


# =============================================================================
# Main
# =============================================================================


def main():
    global config, push_client, _start_time

    parser = argparse.ArgumentParser(description="INTERCEPT Agent - Remote signal intelligence node")
    parser.add_argument("--port", "-p", type=int, default=8020, help="Port to listen on (default: 8020)")
    parser.add_argument(
        "--config", "-c", default="intercept_agent.cfg", help="Configuration file (default: intercept_agent.cfg)"
    )
    parser.add_argument("--name", "-n", help="Agent name (overrides config file)")
    parser.add_argument("--controller", help="Controller URL for push mode")
    parser.add_argument("--api-key", help="API key for controller authentication")
    parser.add_argument("--allowed-ips", help="Comma-separated list of allowed client IPs")
    parser.add_argument("--cors", action="store_true", help="Enable CORS headers")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load config file
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(os.path.dirname(__file__), config_path)
    config.load_from_file(config_path)

    # Override with command line args
    if args.port:
        config.port = args.port
    if args.name:
        config.name = args.name
    if args.controller:
        config.controller_url = args.controller.rstrip("/")
        config.push_enabled = True
    if args.api_key:
        config.controller_api_key = args.api_key
    if args.allowed_ips:
        config.allowed_ips = [ip.strip() for ip in args.allowed_ips.split(",")]
    if args.cors:
        config.allow_cors = True

    _start_time = time.time()

    print("=" * 60)
    print("  INTERCEPT AGENT")
    print("  Remote Signal Intelligence Node")
    print("=" * 60)
    print()
    print(f"  Agent Name:  {config.name}")
    print(f"  Port:        {config.port}")
    print(f"  CORS:        {'Enabled' if config.allow_cors else 'Disabled'}")

    # Start GPS
    print()
    print("  Initializing GPS...")
    if gps_manager.start():
        print("  GPS:         Connected to gpsd")
    else:
        print("  GPS:         Not available (gpsd not running)")
    if config.allowed_ips:
        print(f"  Allowed IPs: {', '.join(config.allowed_ips)}")
    else:
        print("  Allowed IPs: Any")
    print()

    # Detect capabilities
    caps = mode_manager.detect_capabilities()
    print("  Available Modes:")
    for mode, available in caps["modes"].items():
        status = "OK" if available else "N/A"
        print(f"    - {mode}: {status}")
    print()

    if caps["devices"]:
        print("  Detected SDR Devices:")
        for dev in caps["devices"]:
            print(f"    - [{dev.get('index', '?')}] {dev.get('name', 'Unknown')}")
        print()

    # Start push client if enabled
    global data_push_loop
    if config.push_enabled and config.controller_url:
        print(f"  Push Mode:   Enabled -> {config.controller_url}")
        push_client = ControllerPushClient(config)
        push_client.start()
        # Start data push loop
        data_push_loop = DataPushLoop(interval_seconds=config.push_interval)
        data_push_loop.start()
    else:
        print("  Push Mode:   Disabled")
    print()

    # Start HTTP server
    server_address = ("", config.port)
    httpd = ThreadedHTTPServer(server_address, InterceptAgentHandler)

    print(f"  Listening on http://0.0.0.0:{config.port}")
    print()
    print("  Press Ctrl+C to stop")
    print()

    # Shutdown flag
    shutdown_requested = threading.Event()

    # Handle shutdown - run cleanup in separate thread to avoid blocking
    def signal_handler(sig, frame):
        if shutdown_requested.is_set():
            # Already shutting down, force exit
            print("\nForce exit...")
            os._exit(1)
        shutdown_requested.set()
        print("\nShutting down...")

        def cleanup():
            # Stop all running modes first (they have subprocesses)
            for mode in list(mode_manager.running_modes.keys()):
                try:
                    mode_manager.stop_mode(mode)
                except Exception as e:
                    logger.debug(f"Error stopping {mode}: {e}")

            # Stop push services
            if data_push_loop:
                with contextlib.suppress(Exception):
                    data_push_loop.stop()
            if push_client:
                with contextlib.suppress(Exception):
                    push_client.stop()

            # Stop GPS
            with contextlib.suppress(Exception):
                gps_manager.stop()

            # Shutdown HTTP server
            with contextlib.suppress(Exception):
                httpd.shutdown()

        # Run cleanup in background thread so signal handler returns quickly
        cleanup_thread = threading.Thread(target=cleanup, daemon=True)
        cleanup_thread.start()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        pass

    # Give cleanup thread time to finish
    if shutdown_requested.is_set():
        time.sleep(0.5)

    print("Agent stopped.")


if __name__ == "__main__":
    main()
