"""WiFi reconnaissance routes."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import platform
import queue
import re
import subprocess
import threading
import time
from typing import Any

from flask import Blueprint, Response, jsonify, request

import app as app_module
from data.oui import get_manufacturer
from utils.constants import (
    SSE_KEEPALIVE_INTERVAL,
    SSE_QUEUE_TIMEOUT,
    SUBPROCESS_TIMEOUT_MEDIUM,
    SUBPROCESS_TIMEOUT_SHORT,
)
from utils.dependencies import check_tool, get_tool_path
from utils.logging import wifi_logger as logger
from utils.process import is_valid_channel, is_valid_mac
from utils.responses import api_error, api_success
from utils.sse import format_sse, sse_stream_fanout
from utils.validation import validate_network_interface, validate_wifi_channel

wifi_bp = Blueprint("wifi", __name__, url_prefix="/wifi")

# --- v1 deprecation ---
# These endpoints are deprecated in favor of /wifi/v2/*.
# Frontend still uses v1, so they remain active.
# Migration: switch frontend to v2 endpoints, then remove this file.
_v1_deprecation_logged = set()


@wifi_bp.after_request
def _add_deprecation_header(response):
    """Add X-Deprecated header to all v1 WiFi responses."""
    response.headers["X-Deprecated"] = "Use /wifi/v2/* endpoints instead"
    endpoint = request.endpoint or ""
    if endpoint not in _v1_deprecation_logged:
        _v1_deprecation_logged.add(endpoint)
        logger.warning(f"Deprecated v1 WiFi endpoint called: {request.path} — migrate to /wifi/v2/*")
    return response


# PMKID process state
pmkid_process = None
pmkid_lock = threading.Lock()


def _parse_channel_list(raw_channels: Any) -> list[int] | None:
    """Parse a channel list from string/list input."""
    if raw_channels in (None, "", []):
        return None

    if isinstance(raw_channels, str):
        parts = [p.strip() for p in re.split(r"[\s,]+", raw_channels) if p.strip()]
    elif isinstance(raw_channels, (list, tuple, set)):
        parts = list(raw_channels)
    else:
        parts = [raw_channels]

    channels: list[int] = []
    seen = set()
    for part in parts:
        if part in (None, ""):
            continue
        ch = validate_wifi_channel(part)
        if ch not in seen:
            channels.append(ch)
            seen.add(ch)

    return channels or None


def detect_wifi_interfaces():
    """Detect available WiFi interfaces."""
    interfaces = []

    if platform.system() == "Darwin":  # macOS
        try:
            result = subprocess.run(
                ["networksetup", "-listallhardwareports"],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_SHORT,
            )
            lines = result.stdout.split("\n")
            for i, line in enumerate(lines):
                if "Wi-Fi" in line or "AirPort" in line:
                    for j in range(i + 1, min(i + 3, len(lines))):
                        if "Device:" in lines[j]:
                            device = lines[j].split("Device:")[1].strip()
                            interfaces.append(
                                {"name": device, "type": "internal", "monitor_capable": False, "status": "up"}
                            )
                            break
        except FileNotFoundError:
            logger.debug("networksetup not found")
        except subprocess.TimeoutExpired:
            logger.warning("networksetup timed out")
        except subprocess.SubprocessError as e:
            logger.error(f"Error detecting macOS interfaces: {e}")

        try:
            result = subprocess.run(
                ["system_profiler", "SPUSBDataType"], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_MEDIUM
            )
            if "Wireless" in result.stdout or "WLAN" in result.stdout or "802.11" in result.stdout:
                interfaces.append(
                    {"name": "USB WiFi Adapter", "type": "usb", "monitor_capable": True, "status": "detected"}
                )
        except FileNotFoundError:
            logger.debug("system_profiler not found")
        except subprocess.TimeoutExpired:
            logger.debug("system_profiler timed out")
        except subprocess.SubprocessError as e:
            logger.debug(f"Error running system_profiler: {e}")

    else:  # Linux
        try:
            result = subprocess.run(["iw", "dev"], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SHORT)
            current_iface = None
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line.startswith("Interface"):
                    current_iface = line.split()[1]
                elif current_iface and "type" in line:
                    iface_type = line.split()[-1]
                    iface_info = {
                        "name": current_iface,
                        "type": iface_type,
                        "monitor_capable": True,
                        "status": "up",
                        "driver": "",
                        "chipset": "",
                        "mac": "",
                    }
                    # Get additional interface details
                    iface_info.update(_get_interface_details(current_iface))
                    interfaces.append(iface_info)
                    current_iface = None
        except FileNotFoundError:
            # Fall back to iwconfig if iw is not available
            try:
                result = subprocess.run(["iwconfig"], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SHORT)
                for line in result.stdout.split("\n"):
                    if "IEEE 802.11" in line:
                        iface = line.split()[0]
                        iface_info = {
                            "name": iface,
                            "type": "managed",
                            "monitor_capable": True,
                            "status": "up",
                            "driver": "",
                            "chipset": "",
                            "mac": "",
                        }
                        iface_info.update(_get_interface_details(iface))
                        interfaces.append(iface_info)
            except FileNotFoundError:
                logger.debug("Neither iw nor iwconfig found")
            except subprocess.SubprocessError as e:
                logger.debug(f"Error running iwconfig: {e}")
        except subprocess.TimeoutExpired:
            logger.warning("iw command timed out")
        except subprocess.SubprocessError as e:
            logger.error(f"Error detecting Linux interfaces: {e}")

    return interfaces


def _get_interface_details(iface_name):
    """Get additional details about a WiFi interface (driver, chipset, MAC)."""
    import os

    details = {"driver": "", "chipset": "", "mac": ""}

    # Get MAC address
    try:
        mac_path = f"/sys/class/net/{iface_name}/address"
        with open(mac_path) as f:
            details["mac"] = f.read().strip().upper()
    except (OSError, FileNotFoundError):
        pass

    # Get driver name
    try:
        driver_link = f"/sys/class/net/{iface_name}/device/driver"
        if os.path.islink(driver_link):
            driver_path = os.readlink(driver_link)
            details["driver"] = os.path.basename(driver_path)
    except (FileNotFoundError, OSError):
        pass

    # Try airmon-ng first for chipset info (most reliable for WiFi adapters)
    try:
        result = subprocess.run(["airmon-ng"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split("\n"):
            # airmon-ng output format: PHY  Interface  Driver  Chipset
            parts = line.split("\t")
            if len(parts) >= 4:
                if parts[1].strip() == iface_name or parts[1].strip().startswith(iface_name):
                    if parts[2].strip():
                        details["driver"] = parts[2].strip()
                    if parts[3].strip():
                        details["chipset"] = parts[3].strip()
                    break
            # Also try space-separated format
            parts = line.split()
            if len(parts) >= 4 and (parts[1] == iface_name or parts[1].startswith(iface_name)):
                details["driver"] = parts[2]
                details["chipset"] = " ".join(parts[3:])
                break
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    # Fallback: Get chipset info from USB or PCI sysfs
    if not details["chipset"]:
        try:
            device_path = f"/sys/class/net/{iface_name}/device"
            if os.path.exists(device_path):
                # Try to get USB product name
                for usb_path in [f"{device_path}/product", f"{device_path}/../product"]:
                    try:
                        with open(usb_path) as f:
                            details["chipset"] = f.read().strip()
                            break
                    except (OSError, FileNotFoundError):
                        pass

                # If no USB product, try lsusb for USB devices
                if not details["chipset"]:
                    try:
                        # Get USB bus/device info
                        uevent_path = f"{device_path}/uevent"
                        with open(uevent_path) as f:
                            for line in f:
                                if line.startswith("PRODUCT="):
                                    # PRODUCT format: vendor/product/bcdDevice
                                    product = line.split("=")[1].strip()
                                    parts = product.split("/")
                                    if len(parts) >= 2:
                                        vid = parts[0].zfill(4)
                                        pid = parts[1].zfill(4)
                                        # Try lsusb to get device name
                                        try:
                                            lsusb = subprocess.run(
                                                ["lsusb", "-d", f"{vid}:{pid}"],
                                                capture_output=True,
                                                text=True,
                                                timeout=5,
                                            )
                                            if lsusb.stdout:
                                                # Format: Bus XXX Device YYY: ID vid:pid Name
                                                usb_parts = lsusb.stdout.split(f"{vid}:{pid}")
                                                if len(usb_parts) > 1:
                                                    details["chipset"] = usb_parts[1].strip()
                                        except (FileNotFoundError, subprocess.TimeoutExpired):
                                            pass
                                    break
                    except (OSError, FileNotFoundError):
                        pass
        except (FileNotFoundError, OSError):
            pass

    return details


def parse_airodump_csv(csv_path):
    """Parse airodump-ng CSV output file."""
    networks = {}
    clients = {}

    try:
        with open(csv_path, errors="replace") as f:
            content = f.read()

        sections = content.split("\n\n")

        for section in sections:
            lines = section.strip().split("\n")
            if not lines:
                continue

            header = lines[0] if lines else ""

            if "BSSID" in header and "ESSID" in header:
                for line in lines[1:]:
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 14:
                        bssid = parts[0]
                        if bssid and ":" in bssid:
                            networks[bssid] = {
                                "bssid": bssid,
                                "first_seen": parts[1],
                                "last_seen": parts[2],
                                "channel": parts[3],
                                "speed": parts[4],
                                "privacy": parts[5],
                                "cipher": parts[6],
                                "auth": parts[7],
                                "power": parts[8],
                                "beacons": parts[9],
                                "ivs": parts[10],
                                "lan_ip": parts[11],
                                "essid": parts[13] or "Hidden",
                            }

            elif "Station MAC" in header:
                for line in lines[1:]:
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 6:
                        station = parts[0]
                        if station and ":" in station:
                            vendor = get_manufacturer(station)
                            clients[station] = {
                                "mac": station,
                                "first_seen": parts[1],
                                "last_seen": parts[2],
                                "power": parts[3],
                                "packets": parts[4],
                                "bssid": parts[5],
                                "probes": parts[6] if len(parts) > 6 else "",
                                "vendor": vendor,
                            }
    except Exception as e:
        logger.error(f"Error parsing CSV: {e}")

    return networks, clients


def stream_airodump_output(process, csv_path):
    """Stream airodump-ng output to queue."""
    try:
        app_module.wifi_queue.put({"type": "status", "text": "started"})
        last_parse = 0
        start_time = time.time()
        csv_found = False

        while process.poll() is None:
            try:
                fd = process.stderr.fileno()
                fl = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

                stderr_data = process.stderr.read()
                if stderr_data:
                    stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
                    if stderr_text:
                        for line in stderr_text.split("\n"):
                            line = line.strip()
                            if line and not line.startswith("CH") and not line.startswith("Elapsed"):
                                app_module.wifi_queue.put({"type": "error", "text": f"airodump-ng: {line}"})
            except Exception:
                pass

            current_time = time.time()
            if current_time - last_parse >= 2:
                csv_file = csv_path + "-01.csv"
                if os.path.exists(csv_file):
                    csv_found = True
                    networks, clients = parse_airodump_csv(csv_file)

                    for bssid, net in networks.items():
                        if bssid not in app_module.wifi_networks:
                            app_module.wifi_queue.put({"type": "network", "action": "new", **net})
                        else:
                            app_module.wifi_queue.put({"type": "network", "action": "update", **net})

                    for mac, client in clients.items():
                        if mac not in app_module.wifi_clients:
                            app_module.wifi_queue.put({"type": "client", "action": "new", **client})
                        else:
                            # Send update if probes changed or signal changed significantly
                            old_client = app_module.wifi_clients[mac]
                            old_probes = old_client.get("probes", "")
                            new_probes = client.get("probes", "")
                            old_power = int(old_client.get("power", -100) or -100)
                            new_power = int(client.get("power", -100) or -100)

                            if new_probes != old_probes or abs(new_power - old_power) >= 5:
                                app_module.wifi_queue.put({"type": "client", "action": "update", **client})

                    app_module.wifi_networks = networks
                    app_module.wifi_clients = clients
                    last_parse = current_time

                if current_time - start_time > 5 and not csv_found:
                    app_module.wifi_queue.put(
                        {
                            "type": "error",
                            "text": "No scan data after 5 seconds. Check if monitor mode is properly enabled.",
                        }
                    )
                    start_time = current_time + 30

            time.sleep(0.5)

        try:
            remaining_stderr = process.stderr.read()
            if remaining_stderr:
                stderr_text = remaining_stderr.decode("utf-8", errors="replace").strip()
                if stderr_text:
                    app_module.wifi_queue.put({"type": "error", "text": f"airodump-ng exited: {stderr_text}"})
        except Exception:
            pass

        exit_code = process.returncode
        if exit_code != 0 and exit_code is not None:
            app_module.wifi_queue.put({"type": "error", "text": f"airodump-ng exited with code {exit_code}"})

    except Exception as e:
        app_module.wifi_queue.put({"type": "error", "text": str(e)})
    finally:
        process.wait()
        app_module.wifi_queue.put({"type": "status", "text": "stopped"})
        with app_module.wifi_lock:
            app_module.wifi_process = None


@wifi_bp.route("/interfaces")
def get_wifi_interfaces():
    """Get available WiFi interfaces."""
    interfaces = detect_wifi_interfaces()
    tools = {
        "airmon": check_tool("airmon-ng"),
        "airodump": check_tool("airodump-ng"),
        "aireplay": check_tool("aireplay-ng"),
        "iw": check_tool("iw"),
    }
    return jsonify({"interfaces": interfaces, "tools": tools, "monitor_interface": app_module.wifi_monitor_interface})


@wifi_bp.route("/monitor", methods=["POST"])
def toggle_monitor_mode():
    """Enable or disable monitor mode on an interface."""
    data = request.json
    action = data.get("action", "start")

    # Validate interface name to prevent command injection
    try:
        interface = validate_network_interface(data.get("interface"))
    except ValueError as e:
        return api_error(str(e), 400)

    if action == "start":
        if check_tool("airmon-ng"):
            try:

                def get_wireless_interfaces():
                    interfaces = set()
                    try:
                        result = subprocess.run(["iwconfig"], capture_output=True, text=True, timeout=5)
                        for line in result.stdout.split("\n"):
                            if line and not line.startswith(" ") and "no wireless" not in line.lower():
                                iface = line.split()[0] if line.split() else None
                                if iface:
                                    interfaces.add(iface)
                    except (subprocess.SubprocessError, OSError):
                        pass

                    try:
                        for iface in os.listdir("/sys/class/net"):
                            if os.path.exists(f"/sys/class/net/{iface}/wireless"):
                                interfaces.add(iface)
                    except OSError:
                        pass

                    try:
                        result = subprocess.run(["ip", "link", "show"], capture_output=True, text=True, timeout=5)
                        for match in re.finditer(r"^\d+:\s+(\S+):", result.stdout, re.MULTILINE):
                            iface = match.group(1).rstrip(":")
                            if iface.startswith("wl") or "mon" in iface:
                                interfaces.add(iface)
                    except (subprocess.SubprocessError, OSError):
                        pass

                    return interfaces

                interfaces_before = get_wireless_interfaces()

                kill_processes = data.get("kill_processes", False)
                airmon_path = get_tool_path("airmon-ng")
                if kill_processes:
                    subprocess.run([airmon_path, "check", "kill"], capture_output=True, timeout=10)

                result = subprocess.run([airmon_path, "start", interface], capture_output=True, text=True, timeout=15)

                output = result.stdout + result.stderr

                time.sleep(1)
                interfaces_after = get_wireless_interfaces()

                new_interfaces = interfaces_after - interfaces_before
                monitor_iface = None

                if new_interfaces:
                    for iface in new_interfaces:
                        if "mon" in iface:
                            monitor_iface = iface
                            break
                    if not monitor_iface:
                        monitor_iface = list(new_interfaces)[0]

                if not monitor_iface:
                    # Patterns to extract monitor interface name from airmon-ng output
                    # Interface names: start with letter, contain alphanumeric/underscore/dash
                    patterns = [
                        # Look for interface names ending in 'mon' (most reliable)
                        r"\b([a-zA-Z][a-zA-Z0-9_-]*mon)\b",
                        # Airmon-ng format: [phyX]interfacename
                        r"\[phy\d+\]([a-zA-Z][a-zA-Z0-9_-]*mon)",
                        # "enabled for/on [phyX]interface" format
                        r"enabled.*?\[phy\d+\]([a-zA-Z][a-zA-Z0-9_-]*)",
                        # Original interface with 'mon' appended
                        r"\b(" + re.escape(interface) + r"mon)\b",
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, output, re.IGNORECASE)
                        if match:
                            candidate = match.group(1)
                            # Validate it looks like an interface name (not channel info like "10)")
                            if candidate and not candidate[0].isdigit() and ")" not in candidate:
                                monitor_iface = candidate
                                break

                if not monitor_iface:
                    try:
                        result = subprocess.run(["iwconfig", interface], capture_output=True, text=True, timeout=5)
                        if "Mode:Monitor" in result.stdout:
                            monitor_iface = interface
                    except (subprocess.SubprocessError, OSError):
                        pass

                if not monitor_iface:
                    potential = interface + "mon"
                    if potential in interfaces_after:
                        monitor_iface = potential

                if not monitor_iface:
                    monitor_iface = interface + "mon"

                # Verify the interface actually exists
                def interface_exists(iface_name):
                    return os.path.exists(f"/sys/class/net/{iface_name}")

                if not interface_exists(monitor_iface):
                    # Try common naming patterns
                    candidates = [
                        interface + "mon",
                        interface.replace("wlan", "wlan") + "mon",
                        "wlan0mon",
                        "wlan1mon",
                        interface,  # Maybe it stayed the same but in monitor mode
                    ]
                    for candidate in candidates:
                        if interface_exists(candidate):
                            monitor_iface = candidate
                            break
                    else:
                        # List all wireless interfaces to help debug
                        all_wireless = [
                            f
                            for f in os.listdir("/sys/class/net")
                            if os.path.exists(f"/sys/class/net/{f}/wireless") or "mon" in f or f.startswith("wl")
                        ]
                        logger.error(f"Monitor interface not found. Tried: {monitor_iface}. Available: {all_wireless}")
                        return api_error(
                            f"Monitor interface not created. airmon-ng output: {output[:500]}. Available interfaces: {all_wireless}"
                        )

                app_module.wifi_monitor_interface = monitor_iface
                app_module.wifi_queue.put(
                    {"type": "info", "text": f"Monitor mode enabled on {app_module.wifi_monitor_interface}"}
                )
                logger.info(f"Monitor mode enabled on {monitor_iface}")
                return api_success(data={"monitor_interface": app_module.wifi_monitor_interface})

            except Exception as e:
                logger.error(f"Error enabling monitor mode: {e}", exc_info=True)
                return api_error(str(e))

        elif check_tool("iw"):
            try:
                subprocess.run(["ip", "link", "set", interface, "down"], capture_output=True)
                subprocess.run(["iw", interface, "set", "monitor", "control"], capture_output=True)
                subprocess.run(["ip", "link", "set", interface, "up"], capture_output=True)
                app_module.wifi_monitor_interface = interface
                return api_success(data={"monitor_interface": interface})
            except Exception as e:
                return api_error(str(e))
        else:
            return api_error("No monitor mode tools available.")

    else:  # stop
        if check_tool("airmon-ng"):
            try:
                airmon_path = get_tool_path("airmon-ng")
                subprocess.run(
                    [airmon_path, "stop", app_module.wifi_monitor_interface or interface],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                app_module.wifi_monitor_interface = None
                return api_success(message="Monitor mode disabled")
            except Exception as e:
                return api_error(str(e))
        elif check_tool("iw"):
            try:
                subprocess.run(["ip", "link", "set", interface, "down"], capture_output=True)
                subprocess.run(["iw", interface, "set", "type", "managed"], capture_output=True)
                subprocess.run(["ip", "link", "set", interface, "up"], capture_output=True)
                app_module.wifi_monitor_interface = None
                return api_success(message="Monitor mode disabled")
            except Exception as e:
                return api_error(str(e))

    return api_error("Unknown action")


@wifi_bp.route("/scan/start", methods=["POST"])
def start_wifi_scan():
    """Start WiFi scanning with airodump-ng."""
    with app_module.wifi_lock:
        if app_module.wifi_process:
            return api_error("Scan already running")

        data = request.json
        channel = data.get("channel")
        channels = data.get("channels")
        band = data.get("band", "abg")

        # Use provided interface or fall back to stored monitor interface
        interface = data.get("interface")
        if interface:
            try:
                interface = validate_network_interface(interface)
            except ValueError as e:
                return api_error(str(e), 400)
        else:
            interface = app_module.wifi_monitor_interface

        if not interface:
            return api_error("No monitor interface available.")

        # Verify interface exists
        if not os.path.exists(f"/sys/class/net/{interface}"):
            all_wireless = [
                f
                for f in os.listdir("/sys/class/net")
                if os.path.exists(f"/sys/class/net/{f}/wireless") or "mon" in f or f.startswith("wl")
            ]
            return api_error(f'Interface "{interface}" does not exist. Available: {all_wireless}')

        app_module.wifi_networks = {}
        app_module.wifi_clients = {}

        while not app_module.wifi_queue.empty():
            try:
                app_module.wifi_queue.get_nowait()
            except queue.Empty:
                break

        csv_path = "/tmp/intercept_wifi"

        for f in ["/tmp/intercept_wifi-01.csv", "/tmp/intercept_wifi-01.cap"]:
            with contextlib.suppress(OSError):
                os.remove(f)

        airodump_path = get_tool_path("airodump-ng")

        channel_list = None
        if channels:
            try:
                channel_list = _parse_channel_list(channels)
            except ValueError as e:
                return api_error(str(e), 400)

        cmd = [
            airodump_path,
            "-w",
            csv_path,
            "--output-format",
            "csv,pcap",
        ]

        # --band and -c are mutually exclusive: only add --band when not
        # locking to specific channels, and always place the interface last.
        if channel_list:
            cmd.extend(["-c", ",".join(str(c) for c in channel_list)])
        elif channel:
            cmd.extend(["-c", str(channel)])
        else:
            cmd.extend(["--band", band])

        cmd.append(interface)

        logger.info(f"Running: {' '.join(cmd)}")

        try:
            app_module.wifi_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            time.sleep(0.5)

            if app_module.wifi_process.poll() is not None:
                stderr_output = app_module.wifi_process.stderr.read().decode("utf-8", errors="replace").strip()
                stdout_output = app_module.wifi_process.stdout.read().decode("utf-8", errors="replace").strip()
                exit_code = app_module.wifi_process.returncode
                app_module.wifi_process = None

                error_msg = stderr_output or stdout_output or f"Process exited with code {exit_code}"
                error_msg = re.sub(r"\x1b\[[0-9;]*m", "", error_msg)

                if "No such device" in error_msg or "No such interface" in error_msg:
                    error_msg = f'Interface "{interface}" not found. Make sure monitor mode is enabled.'
                elif "Operation not permitted" in error_msg:
                    error_msg = "Permission denied. Try running with sudo."

                logger.error(f"airodump-ng failed for interface '{interface}': {error_msg}")
                return api_error(error_msg)

            thread = threading.Thread(target=stream_airodump_output, args=(app_module.wifi_process, csv_path))
            thread.daemon = True
            thread.start()

            app_module.wifi_queue.put({"type": "info", "text": f"Started scanning on {interface}"})

            return jsonify({"status": "started", "interface": interface})

        except FileNotFoundError:
            return api_error("airodump-ng not found.")
        except Exception as e:
            return api_error(str(e))


@wifi_bp.route("/scan/stop", methods=["POST"])
def stop_wifi_scan():
    """Stop WiFi scanning."""
    with app_module.wifi_lock:
        if app_module.wifi_process:
            app_module.wifi_process.terminate()
            try:
                app_module.wifi_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                app_module.wifi_process.kill()
            app_module.wifi_process = None
            return jsonify({"status": "stopped"})
        return jsonify({"status": "not_running"})


@wifi_bp.route("/deauth", methods=["POST"])
def send_deauth():
    """Send deauthentication packets."""
    data = request.json
    target_bssid = data.get("bssid")
    target_client = data.get("client", "FF:FF:FF:FF:FF:FF")
    count = data.get("count", 5)

    # Validate interface
    interface = data.get("interface")
    if interface:
        try:
            interface = validate_network_interface(interface)
        except ValueError as e:
            return api_error(str(e), 400)
    else:
        interface = app_module.wifi_monitor_interface

    if not target_bssid:
        return api_error("Target BSSID required")

    if not is_valid_mac(target_bssid):
        return api_error("Invalid BSSID format")

    if not is_valid_mac(target_client):
        return api_error("Invalid client MAC format")

    try:
        count = int(count)
        if count < 1 or count > 100:
            count = 5
    except (ValueError, TypeError):
        count = 5

    if not interface:
        return api_error("No monitor interface")

    if not check_tool("aireplay-ng"):
        return api_error("aireplay-ng not found")

    try:
        aireplay_path = get_tool_path("aireplay-ng")
        cmd = [aireplay_path, "--deauth", str(count), "-a", target_bssid, "-c", target_client, interface]

        app_module.wifi_queue.put({"type": "info", "text": f"Sending {count} deauth packets to {target_bssid}"})

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            return api_success(message=f"Sent {count} deauth packets")
        else:
            return api_error(result.stderr)

    except subprocess.TimeoutExpired:
        return api_success(message="Deauth sent (timed out)")
    except Exception as e:
        return api_error(str(e))


@wifi_bp.route("/handshake/capture", methods=["POST"])
def capture_handshake():
    """Start targeted handshake capture."""
    data = request.json
    target_bssid = data.get("bssid")
    channel = data.get("channel")

    # Validate interface
    interface = data.get("interface")
    if interface:
        try:
            interface = validate_network_interface(interface)
        except ValueError as e:
            return api_error(str(e), 400)
    else:
        interface = app_module.wifi_monitor_interface

    if not target_bssid or not channel:
        return api_error("BSSID and channel required")

    if not is_valid_mac(target_bssid):
        return api_error("Invalid BSSID format")

    if not is_valid_channel(channel):
        return api_error("Invalid channel")

    with app_module.wifi_lock:
        if app_module.wifi_process:
            return api_error("Scan already running.")

        capture_path = f"/tmp/intercept_handshake_{target_bssid.replace(':', '')}"

        airodump_path = get_tool_path("airodump-ng")
        cmd = [
            airodump_path,
            "-c",
            str(channel),
            "--bssid",
            target_bssid,
            "-w",
            capture_path,
            "--output-format",
            "pcap",
            interface,
        ]

        try:
            app_module.wifi_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            app_module.wifi_queue.put({"type": "info", "text": f"Capturing handshakes for {target_bssid}"})
            return jsonify({"status": "started", "capture_file": capture_path + "-01.cap"})
        except Exception as e:
            return api_error(str(e))


@wifi_bp.route("/handshake/status", methods=["POST"])
def check_handshake_status():
    """Check if a handshake has been captured."""
    data = request.json
    capture_file = data.get("file", "")
    target_bssid = data.get("bssid", "")

    if not capture_file.startswith("/tmp/intercept_handshake_") or ".." in capture_file:
        return api_error("Invalid capture file path")

    if not os.path.exists(capture_file):
        with app_module.wifi_lock:
            if app_module.wifi_process and app_module.wifi_process.poll() is None:
                return jsonify({"status": "running", "file_exists": False, "handshake_found": False})
            else:
                return jsonify({"status": "stopped", "file_exists": False, "handshake_found": False})

    file_size = os.path.getsize(capture_file)
    handshake_found = False
    handshake_valid: bool | None = None
    handshake_checked = False
    handshake_reason: str | None = None

    try:
        if target_bssid and is_valid_mac(target_bssid):
            aircrack_path = get_tool_path("aircrack-ng")
            if aircrack_path:
                result = subprocess.run(
                    [aircrack_path, "-a", "2", "-b", target_bssid, capture_file],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                output = result.stdout + result.stderr
                output_lower = output.lower()
                handshake_checked = True

                if "no valid wpa handshakes found" in output_lower:
                    handshake_valid = False
                    handshake_reason = "No valid WPA handshake found"
                elif "0 handshake" in output_lower:
                    handshake_valid = False
                elif "1 handshake" in output_lower or ("handshake" in output_lower and "wpa" in output_lower):
                    handshake_valid = True
                else:
                    handshake_valid = False
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        logger.error(f"Error checking handshake: {e}")

    if handshake_valid:
        handshake_found = True
        normalized_bssid = target_bssid.upper() if target_bssid else None
        if normalized_bssid and normalized_bssid not in app_module.wifi_handshakes:
            app_module.wifi_handshakes.append(normalized_bssid)

    return jsonify(
        {
            "status": "running" if app_module.wifi_process and app_module.wifi_process.poll() is None else "stopped",
            "file_exists": True,
            "file_size": file_size,
            "file": capture_file,
            "handshake_found": handshake_found,
            "handshake_valid": handshake_valid,
            "handshake_checked": handshake_checked,
            "handshake_reason": handshake_reason,
        }
    )


@wifi_bp.route("/pmkid/capture", methods=["POST"])
def capture_pmkid():
    """Start PMKID capture using hcxdumptool."""
    global pmkid_process

    data = request.json
    target_bssid = data.get("bssid")
    channel = data.get("channel")

    # Validate interface
    interface = data.get("interface")
    if interface:
        try:
            interface = validate_network_interface(interface)
        except ValueError as e:
            return api_error(str(e), 400)
    else:
        interface = app_module.wifi_monitor_interface

    if not target_bssid:
        return api_error("BSSID required")

    if not is_valid_mac(target_bssid):
        return api_error("Invalid BSSID format")

    with pmkid_lock:
        if pmkid_process and pmkid_process.poll() is None:
            return api_error("PMKID capture already running")

        capture_path = f"/tmp/intercept_pmkid_{target_bssid.replace(':', '')}.pcapng"
        filter_file = f"/tmp/pmkid_filter_{target_bssid.replace(':', '')}"
        with open(filter_file, "w") as f:
            f.write(target_bssid.replace(":", "").lower())

        cmd = [
            "hcxdumptool",
            "-i",
            interface,
            "-o",
            capture_path,
            "--filterlist_ap",
            filter_file,
            "--filtermode",
            "2",
            "--enable_status",
            "1",
        ]

        if channel:
            cmd.extend(["-c", str(channel)])

        try:
            pmkid_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return jsonify({"status": "started", "file": capture_path})
        except FileNotFoundError:
            return api_error("hcxdumptool not found.")
        except Exception as e:
            return api_error(str(e))


@wifi_bp.route("/pmkid/status", methods=["POST"])
def check_pmkid_status():
    """Check if PMKID has been captured."""
    data = request.json
    capture_file = data.get("file", "")

    if not capture_file.startswith("/tmp/intercept_pmkid_") or ".." in capture_file:
        return api_error("Invalid capture file path")

    if not os.path.exists(capture_file):
        return jsonify({"pmkid_found": False, "file_exists": False})

    file_size = os.path.getsize(capture_file)
    pmkid_found = False

    try:
        hash_file = capture_file.replace(".pcapng", ".22000")
        subprocess.run(["hcxpcapngtool", "-o", hash_file, capture_file], capture_output=True, text=True, timeout=10)
        if os.path.exists(hash_file) and os.path.getsize(hash_file) > 0:
            pmkid_found = True
    except FileNotFoundError:
        pmkid_found = file_size > 1000
    except Exception:
        pass

    return jsonify({"pmkid_found": pmkid_found, "file_exists": True, "file_size": file_size, "file": capture_file})


@wifi_bp.route("/pmkid/stop", methods=["POST"])
def stop_pmkid():
    """Stop PMKID capture."""
    global pmkid_process

    with pmkid_lock:
        if pmkid_process:
            pmkid_process.terminate()
            try:
                pmkid_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pmkid_process.kill()
            pmkid_process = None

    return jsonify({"status": "stopped"})


@wifi_bp.route("/handshake/crack", methods=["POST"])
def crack_handshake():
    """Crack a captured handshake using aircrack-ng."""
    data = request.json
    capture_file = data.get("capture_file", "")
    target_bssid = data.get("bssid", "")
    wordlist = data.get("wordlist", "")

    # Validate paths to prevent path traversal
    if not capture_file.startswith("/tmp/intercept_handshake_") or ".." in capture_file:
        return api_error("Invalid capture file path", 400)

    if ".." in wordlist:
        return api_error("Invalid wordlist path", 400)

    if not os.path.exists(capture_file):
        return api_error("Capture file not found", 404)

    if not os.path.exists(wordlist):
        return api_error("Wordlist file not found", 404)

    if target_bssid and not is_valid_mac(target_bssid):
        return api_error("Invalid BSSID format", 400)

    aircrack_path = get_tool_path("aircrack-ng")
    if not aircrack_path:
        return api_error("aircrack-ng not found", 500)

    try:
        cmd = [aircrack_path, "-a", "2", "-w", wordlist]
        if target_bssid:
            cmd.extend(["-b", target_bssid])
        cmd.append(capture_file)

        logger.info(f"Starting aircrack-ng: {' '.join(cmd)}")

        # Run aircrack-ng with a timeout (this could take a while)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        output = result.stdout + result.stderr

        # Check if password was found
        # Aircrack-ng outputs "KEY FOUND! [ password ]" when successful
        if "KEY FOUND!" in output:
            # Extract the password
            import re

            match = re.search(r"KEY FOUND!\s*\[\s*(.+?)\s*\]", output)
            if match:
                password = match.group(1)
                logger.info(f"Password cracked for {target_bssid}: {password}")
                return api_success(data={"password": password, "bssid": target_bssid})

        # Password not found
        return jsonify({"status": "not_found", "message": "Password not in wordlist"})

    except subprocess.TimeoutExpired:
        return jsonify(
            {
                "status": "timeout",
                "message": "Cracking timed out after 5 minutes. Try a smaller wordlist or use hashcat.",
            }
        )
    except Exception as e:
        logger.error(f"Crack error: {e}")
        return api_error(str(e), 500)


@wifi_bp.route("/networks")
def get_wifi_networks():
    """Get current list of discovered networks."""
    return jsonify(
        {
            "networks": list(app_module.wifi_networks.values()),
            "clients": list(app_module.wifi_clients.values()),
            "handshakes": app_module.wifi_handshakes,
            "monitor_interface": app_module.wifi_monitor_interface,
        }
    )


@wifi_bp.route("/stream")
def stream_wifi():
    """SSE stream for WiFi events."""

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.wifi_queue,
            channel_key="wifi",
            timeout=1.0,
            keepalive_interval=30.0,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


# =============================================================================
# V2 API Endpoints - Using unified WiFi scanner
# =============================================================================

from utils.wifi.scanner import get_wifi_scanner


@wifi_bp.route("/v2/capabilities")
def get_v2_capabilities():
    """Get WiFi scanning capabilities on this system."""
    try:
        scanner = get_wifi_scanner()
        caps = scanner.check_capabilities()
        return jsonify(
            {
                "platform": caps.platform,
                "is_root": caps.is_root,
                "can_quick_scan": caps.can_quick_scan,
                "can_deep_scan": caps.can_deep_scan,
                "preferred_quick_tool": caps.preferred_quick_tool,
                "interfaces": caps.interfaces,
                "default_interface": caps.default_interface,
                "has_monitor_capable_interface": caps.has_monitor_capable_interface,
                "monitor_interface": caps.monitor_interface,
                "issues": caps.issues,
                "tools": {
                    "nmcli": caps.has_nmcli,
                    "iw": caps.has_iw,
                    "iwlist": caps.has_iwlist,
                    "airport": caps.has_airport,
                    "airmon_ng": caps.has_airmon_ng,
                    "airodump_ng": caps.has_airodump_ng,
                },
            }
        )
    except Exception as e:
        logger.exception("Error checking capabilities")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/scan/quick", methods=["POST"])
def v2_quick_scan():
    """Perform a quick one-shot WiFi scan using system tools."""
    try:
        data = request.json or {}
        interface = data.get("interface")
        timeout = data.get("timeout", 10.0)

        scanner = get_wifi_scanner()
        result = scanner.quick_scan(interface=interface, timeout=timeout)

        if result.error:
            return jsonify(
                {
                    "error": result.error,
                    "access_points": [],
                    "channel_stats": [],
                    "recommendations": [],
                }
            ), 200  # Return 200 with error in body for cleaner handling

        return jsonify(
            {
                "access_points": [ap.to_summary_dict() for ap in result.access_points],
                "channel_stats": [s.to_dict() for s in result.channel_stats],
                "recommendations": [r.to_dict() for r in result.recommendations],
                "duration_seconds": result.duration_seconds,
                "warnings": result.warnings,
            }
        )
    except Exception as e:
        logger.exception("Error in quick scan")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/scan/start", methods=["POST"])
def v2_start_scan():
    """Start continuous deep scan with airodump-ng."""
    try:
        data = request.json or {}
        interface = data.get("interface")
        band = data.get("band", "all")
        channel = data.get("channel")

        scanner = get_wifi_scanner()
        success = scanner.start_deep_scan(interface=interface, band=band, channel=channel)

        if success:
            return jsonify({"status": "started"})
        else:
            status = scanner.get_status()
            return api_error(status.error or "Failed to start scan", 400)
    except Exception as e:
        logger.exception("Error starting deep scan")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/scan/stop", methods=["POST"])
def v2_stop_scan():
    """Stop the current scan."""
    try:
        scanner = get_wifi_scanner()
        scanner.stop_deep_scan()
        return jsonify({"status": "stopped"})
    except Exception as e:
        logger.exception("Error stopping scan")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/scan/status")
def v2_scan_status():
    """Get current scan status."""
    try:
        scanner = get_wifi_scanner()
        status = scanner.get_status()
        return jsonify(
            {
                "is_scanning": status.is_scanning,
                "scan_mode": status.scan_mode,
                "interface": status.interface,
                "started_at": status.started_at.isoformat() if status.started_at else None,
                "networks_found": status.networks_found,
                "clients_found": status.clients_found,
                "error": status.error,
            }
        )
    except Exception as e:
        logger.exception("Error getting scan status")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/networks")
def v2_get_networks():
    """Get all discovered networks."""
    try:
        scanner = get_wifi_scanner()
        networks = scanner.access_points
        return jsonify(
            {
                "networks": [ap.to_summary_dict() for ap in networks],
                "total": len(networks),
            }
        )
    except Exception as e:
        logger.exception("Error getting networks")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/clients")
def v2_get_clients():
    """Get discovered clients with optional filtering."""
    try:
        scanner = get_wifi_scanner()
        clients = scanner.clients

        # Filter by association status
        associated = request.args.get("associated")
        if associated == "true":
            clients = [c for c in clients if c.is_associated]
        elif associated == "false":
            clients = [c for c in clients if not c.is_associated]

        # Filter by associated BSSID
        bssid = request.args.get("bssid")
        if bssid:
            clients = [c for c in clients if c.associated_bssid == bssid.upper()]

        # Filter by minimum RSSI
        min_rssi = request.args.get("min_rssi")
        if min_rssi:
            try:
                min_rssi = int(min_rssi)
                clients = [c for c in clients if c.rssi_current and c.rssi_current >= min_rssi]
            except ValueError:
                pass

        return jsonify(
            {
                "clients": [c.to_dict() for c in clients],
                "total": len(clients),
            }
        )
    except Exception as e:
        logger.exception("Error getting clients")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/probes")
def v2_get_probes():
    """Get probe requests."""
    try:
        scanner = get_wifi_scanner()
        probes = scanner.probe_requests
        return jsonify(
            {
                "probes": [p.to_dict() for p in probes[-100:]],  # Last 100
                "total": len(probes),
            }
        )
    except Exception as e:
        logger.exception("Error getting probes")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/channels")
def v2_get_channels():
    """Get channel statistics and recommendations."""
    try:
        scanner = get_wifi_scanner()
        stats = scanner._calculate_channel_stats()
        recommendations = scanner._generate_recommendations(stats)
        return jsonify(
            {
                "channel_stats": [s.to_dict() for s in stats],
                "recommendations": [r.to_dict() for r in recommendations],
            }
        )
    except Exception as e:
        logger.exception("Error getting channel stats")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/stream")
def v2_stream():
    """SSE stream for real-time WiFi events."""

    def generate():
        scanner = get_wifi_scanner()
        for event in scanner.get_event_stream():
            yield format_sse(event)

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@wifi_bp.route("/v2/export")
def v2_export():
    """Export scan data as CSV or JSON."""
    try:
        format_type = request.args.get("format", "json")
        data_type = request.args.get("type", "all")

        scanner = get_wifi_scanner()

        if format_type == "json":
            data = {}
            if data_type in ("all", "networks"):
                data["networks"] = [ap.to_summary_dict() for ap in scanner.access_points]
            if data_type in ("all", "clients"):
                data["clients"] = [c.to_dict() for c in scanner.clients]
            if data_type in ("all", "probes"):
                data["probes"] = [p.to_dict() for p in scanner.probe_requests]

            response = Response(
                json.dumps(data, indent=2, default=str),
                mimetype="application/json",
            )
            response.headers["Content-Disposition"] = "attachment; filename=wifi_scan.json"
            return response

        elif format_type == "csv":
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)

            # Write networks
            writer.writerow(["Networks"])
            writer.writerow(
                [
                    "BSSID",
                    "ESSID",
                    "Channel",
                    "Band",
                    "RSSI",
                    "Security",
                    "Vendor",
                    "Clients",
                    "First Seen",
                    "Last Seen",
                ]
            )
            for ap in scanner.access_points:
                writer.writerow(
                    [
                        ap.bssid,
                        ap.essid or "[Hidden]",
                        ap.channel,
                        ap.band,
                        ap.rssi_current,
                        ap.security,
                        ap.vendor,
                        ap.client_count,
                        ap.first_seen.isoformat() if ap.first_seen else "",
                        ap.last_seen.isoformat() if ap.last_seen else "",
                    ]
                )

            writer.writerow([])

            # Write clients
            writer.writerow(["Clients"])
            writer.writerow(["MAC", "BSSID", "Vendor", "RSSI", "Probed SSIDs", "First Seen", "Last Seen"])
            for c in scanner.clients:
                writer.writerow(
                    [
                        c.mac,
                        c.associated_bssid or "",
                        c.vendor,
                        c.rssi_current,
                        ", ".join(c.probed_ssids),
                        c.first_seen.isoformat() if c.first_seen else "",
                        c.last_seen.isoformat() if c.last_seen else "",
                    ]
                )

            response = Response(
                output.getvalue(),
                mimetype="text/csv",
            )
            response.headers["Content-Disposition"] = "attachment; filename=wifi_scan.csv"
            return response

        else:
            return api_error(f"Unknown format: {format_type}", 400)

    except Exception as e:
        logger.exception("Error exporting data")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/baseline/set", methods=["POST"])
def v2_set_baseline():
    """Set current networks as baseline."""
    try:
        scanner = get_wifi_scanner()
        scanner.set_baseline()
        return jsonify({"status": "baseline_set", "count": len(scanner._baseline_networks)})
    except Exception as e:
        logger.exception("Error setting baseline")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/baseline/clear", methods=["POST"])
def v2_clear_baseline():
    """Clear the baseline."""
    try:
        scanner = get_wifi_scanner()
        scanner.clear_baseline()
        return jsonify({"status": "baseline_cleared"})
    except Exception as e:
        logger.exception("Error clearing baseline")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/clear", methods=["POST"])
def v2_clear_data():
    """Clear all discovered data."""
    try:
        scanner = get_wifi_scanner()
        scanner.clear_data()
        return jsonify({"status": "cleared"})
    except Exception as e:
        logger.exception("Error clearing data")
        return api_error(str(e), 500)


# =============================================================================
# V2 Deauth Detection Endpoints
# =============================================================================


@wifi_bp.route("/v2/deauth/status")
def v2_deauth_status():
    """
    Get deauth detection status and recent alerts.

    Returns:
        - is_running: Whether deauth detector is active
        - interface: Monitor interface being used
        - stats: Detection statistics
        - recent_alerts: Recent deauth alerts
    """
    try:
        scanner = get_wifi_scanner()
        detector = scanner.deauth_detector

        if detector:
            stats = detector.stats
            alerts = detector.get_alerts(limit=50)
        else:
            stats = {
                "is_running": False,
                "interface": None,
                "packets_captured": 0,
                "alerts_generated": 0,
            }
            alerts = []

        return jsonify(
            {
                "is_running": stats.get("is_running", False),
                "interface": stats.get("interface"),
                "started_at": stats.get("started_at"),
                "stats": {
                    "packets_captured": stats.get("packets_captured", 0),
                    "alerts_generated": stats.get("alerts_generated", 0),
                    "active_trackers": stats.get("active_trackers", 0),
                },
                "recent_alerts": alerts,
            }
        )
    except Exception as e:
        logger.exception("Error getting deauth status")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/deauth/stream")
def v2_deauth_stream():
    """
    SSE stream for real-time deauth alerts.

    Events:
        - deauth_alert: A deauth attack was detected
        - deauth_detector_started: Detector started
        - deauth_detector_stopped: Detector stopped
        - deauth_error: An error occurred
        - keepalive: Periodic keepalive
    """
    response = Response(
        sse_stream_fanout(
            source_queue=app_module.deauth_detector_queue,
            channel_key="wifi_deauth",
            timeout=SSE_QUEUE_TIMEOUT,
            keepalive_interval=SSE_KEEPALIVE_INTERVAL,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@wifi_bp.route("/v2/deauth/alerts")
def v2_deauth_alerts():
    """
    Get historical deauth alerts.

    Query params:
        - limit: Maximum number of alerts to return (default 100)
    """
    try:
        limit = request.args.get("limit", 100, type=int)
        limit = max(1, min(limit, 1000))  # Clamp between 1 and 1000

        scanner = get_wifi_scanner()
        alerts = scanner.get_deauth_alerts(limit=limit)

        # Also include alerts from DataStore that might have been persisted
        try:
            stored_alerts = list(app_module.deauth_alerts.values())
            # Merge and deduplicate by ID
            alert_ids = {a.get("id") for a in alerts}
            for alert in stored_alerts:
                if alert.get("id") not in alert_ids:
                    alerts.append(alert)
            # Sort by timestamp descending
            alerts.sort(key=lambda a: a.get("timestamp", 0), reverse=True)
            alerts = alerts[:limit]
        except Exception:
            pass

        return jsonify(
            {
                "alerts": alerts,
                "count": len(alerts),
            }
        )
    except Exception as e:
        logger.exception("Error getting deauth alerts")
        return api_error(str(e), 500)


@wifi_bp.route("/v2/deauth/clear", methods=["POST"])
def v2_deauth_clear():
    """Clear deauth alert history."""
    try:
        scanner = get_wifi_scanner()
        scanner.clear_deauth_alerts()

        # Clear the queue
        while not app_module.deauth_detector_queue.empty():
            try:
                app_module.deauth_detector_queue.get_nowait()
            except queue.Empty:
                break

        return jsonify({"status": "cleared"})
    except Exception as e:
        logger.exception("Error clearing deauth alerts")
        return api_error(str(e), 500)
