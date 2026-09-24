"""Bluetooth reconnaissance routes."""

from __future__ import annotations

import contextlib
import os
import platform
import pty
import queue
import re
import select
import subprocess
import threading
import time

from flask import Blueprint, Response, jsonify, request

import app as app_module
from data.oui import OUI_DATABASE, get_manufacturer, load_oui_database
from data.patterns import AIRTAG_PREFIXES, SAMSUNG_TRACKER, TILE_PREFIXES
from utils.constants import (
    SUBPROCESS_TIMEOUT_SHORT,
)
from utils.dependencies import check_tool
from utils.logging import bluetooth_logger as logger
from utils.responses import api_error, api_success
from utils.sse import sse_stream_fanout
from utils.validation import validate_bluetooth_interface

bluetooth_bp = Blueprint("bluetooth", __name__, url_prefix="/bt")

# --- v1 deprecation ---
# These endpoints are deprecated in favor of /api/bluetooth/*.
# Frontend still uses v1, so they remain active.
# Migration: switch frontend to v2 endpoints, then remove this file.
_v1_deprecation_logged = set()


@bluetooth_bp.after_request
def _add_deprecation_header(response):
    """Add X-Deprecated header to all v1 Bluetooth responses."""
    response.headers["X-Deprecated"] = "Use /api/bluetooth/* endpoints instead"
    endpoint = request.endpoint or ""
    if endpoint not in _v1_deprecation_logged:
        _v1_deprecation_logged.add(endpoint)
        logger.warning(f"Deprecated v1 Bluetooth endpoint called: {request.path} — migrate to /api/bluetooth/*")
    return response


def classify_bt_device(name, device_class, services, manufacturer=None):
    """Classify Bluetooth device type based on available info."""
    name_lower = (name or "").lower()
    mfr_lower = (manufacturer or "").lower()

    # Audio devices - check name patterns first
    audio_patterns = [
        "airpod",
        "earbud",
        "headphone",
        "headset",
        "speaker",
        "audio",
        "beats",
        "bose",
        "jbl",
        "sony wh",
        "sony wf",
        "sennheiser",
        "jabra",
        "soundcore",
        "anker",
        "buds",
        "earphone",
        "pod",
        "soundbar",
        "skullcandy",
        "marshall",
        "b&o",
        "bang",
        "olufsen",
        "powerbeats",
        "soundlink",
        "soundsport",
        "quietcomfort",
        "qc35",
        "qc45",
        "nc700",
        "wh-1000",
        "wf-1000",
        "linkbuds",
        "freebuds",
        "galaxy buds",
        "pixel buds",
        "echo dot",
        "homepod",
        "sonos",
        "ue boom",
        "flip",
        "charge",
        "xtreme",
        "pulse",
    ]
    if any(x in name_lower for x in audio_patterns):
        return "audio"

    # Wearables
    wearable_patterns = [
        "watch",
        "band",
        "fitbit",
        "garmin",
        "mi band",
        "miband",
        "amazfit",
        "galaxy watch",
        "gear",
        "versa",
        "sense",
        "charge",
        "inspire",
        "fenix",
        "forerunner",
        "venu",
        "vivoactive",
        "instinct",
        "apple watch",
        "gt 2",
        "gt2",
    ]
    if any(x in name_lower for x in wearable_patterns):
        return "wearable"

    # Phones - check name patterns
    phone_patterns = [
        "iphone",
        "galaxy",
        "pixel",
        "phone",
        "android",
        "oneplus",
        "huawei",
        "xiaomi",
        "redmi",
        "poco",
        "realme",
        "oppo",
        "vivo",
        "motorola",
        "nokia",
        "lg-",
        "sm-",
        "moto g",
        "moto e",
        "note",
        "ultra",
        "pro max",
        "s21",
        "s22",
        "s23",
        "s24",
    ]
    if any(x in name_lower for x in phone_patterns):
        return "phone"

    # Trackers
    tracker_patterns = ["airtag", "tile", "smarttag", "chipolo", "find my", "findmy"]
    if any(x in name_lower for x in tracker_patterns):
        return "tracker"

    # Input devices
    input_patterns = [
        "keyboard",
        "mouse",
        "controller",
        "gamepad",
        "remote",
        "trackpad",
        "magic keyboard",
        "magic mouse",
        "magic trackpad",
        "mx master",
        "mx keys",
        "logitech k",
        "logitech m",
        "razer",
        "dualshock",
        "dualsense",
        "xbox",
    ]
    if any(x in name_lower for x in input_patterns):
        return "input"

    # Computers/laptops
    computer_patterns = [
        "macbook",
        "imac",
        "mac pro",
        "mac mini",
        "dell",
        "hp ",
        "lenovo",
        "thinkpad",
        "surface",
        "chromebook",
        "laptop",
        "desktop",
        "pc",
    ]
    if any(x in name_lower for x in computer_patterns):
        return "computer"

    # Check manufacturer for device type inference
    audio_manufacturers = [
        "bose",
        "jbl",
        "sony",
        "sennheiser",
        "jabra",
        "beats",
        "bang & olufsen",
        "audio-technica",
        "skullcandy",
        "anker",
        "plantronics",
    ]
    if mfr_lower in audio_manufacturers:
        return "audio"

    wearable_manufacturers = ["fitbit", "garmin"]
    if mfr_lower in wearable_manufacturers:
        return "wearable"

    if mfr_lower == "tile":
        return "tracker"

    phone_manufacturers = ["samsung", "xiaomi", "huawei", "oneplus", "google", "oppo", "vivo", "realme"]
    if mfr_lower in phone_manufacturers:
        return "phone"

    computer_manufacturers = ["dell", "hp", "lenovo", "microsoft", "intel"]
    if mfr_lower in computer_manufacturers:
        return "computer"

    # Check device class if available
    if device_class:
        major_class = (device_class >> 8) & 0x1F
        if major_class == 1:
            return "computer"
        elif major_class == 2:
            return "phone"
        elif major_class == 4:
            return "audio"
        elif major_class == 5:
            return "input"
        elif major_class == 7:
            return "wearable"

    return "other"


def detect_tracker(mac, name, manufacturer_data=None):
    """Detect if device is a known tracker."""
    mac_prefix = mac[:5].upper()

    if any(mac_prefix.startswith(p) for p in AIRTAG_PREFIXES):
        if manufacturer_data and b"\\x4c\\x00" in manufacturer_data:
            return {"type": "airtag", "name": "Apple AirTag", "risk": "high"}

    if any(mac_prefix.startswith(p) for p in TILE_PREFIXES):
        return {"type": "tile", "name": "Tile Tracker", "risk": "medium"}

    if any(mac_prefix.startswith(p) for p in SAMSUNG_TRACKER):
        return {"type": "smarttag", "name": "Samsung SmartTag", "risk": "medium"}

    name_lower = (name or "").lower()
    if "airtag" in name_lower:
        return {"type": "airtag", "name": "Apple AirTag", "risk": "high"}
    if "tile" in name_lower:
        return {"type": "tile", "name": "Tile Tracker", "risk": "medium"}

    return None


def detect_bt_interfaces():
    """Detect available Bluetooth interfaces."""
    interfaces = []

    if platform.system() == "Linux":
        try:
            result = subprocess.run(["hciconfig"], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SHORT)
            blocks = re.split(r"(?=^hci\d+:)", result.stdout, flags=re.MULTILINE)
            for block in blocks:
                if block.strip():
                    first_line = block.split("\n")[0]
                    match = re.match(r"(hci\d+):", first_line)
                    if match:
                        iface_name = match.group(1)
                        is_up = "UP RUNNING" in block or "\tUP " in block
                        interfaces.append({"name": iface_name, "type": "hci", "status": "up" if is_up else "down"})
        except FileNotFoundError:
            logger.debug("hciconfig not found")
        except subprocess.TimeoutExpired:
            logger.warning("hciconfig timed out")
        except subprocess.SubprocessError as e:
            logger.warning(f"Error running hciconfig: {e}")

    elif platform.system() == "Darwin":
        interfaces.append({"name": "default", "type": "macos", "status": "available"})

    return interfaces


def stream_bt_scan(process, scan_mode):
    """Stream Bluetooth scan output to queue."""
    try:
        app_module.bt_queue.put({"type": "status", "text": "started"})

        if scan_mode == "hcitool":
            for line in iter(process.stdout.readline, b""):
                line = line.decode("utf-8", errors="replace").strip()
                if not line or "LE Scan" in line:
                    continue

                parts = line.split()
                if len(parts) >= 1 and ":" in parts[0]:
                    mac = parts[0]
                    name = " ".join(parts[1:]) if len(parts) > 1 else ""

                    manufacturer = get_manufacturer(mac)
                    device = {
                        "mac": mac,
                        "name": name or "[Unknown]",
                        "manufacturer": manufacturer,
                        "type": classify_bt_device(name, None, None, manufacturer),
                        "rssi": None,
                        "last_seen": time.time(),
                    }

                    tracker = detect_tracker(mac, name)
                    if tracker:
                        device["tracker"] = tracker

                    is_new = mac not in app_module.bt_devices
                    app_module.bt_devices[mac] = device

                    app_module.bt_queue.put(
                        {
                            **device,
                            "type": "device",
                            "device_type": device.get("type", "other"),
                            "action": "new" if is_new else "update",
                        }
                    )

        elif scan_mode == "bluetoothctl":
            master_fd = getattr(process, "_master_fd", None)
            if not master_fd:
                app_module.bt_queue.put({"type": "error", "text": "bluetoothctl pty not available"})
                return

            buffer = ""
            while process.poll() is None:
                readable, _, _ = select.select([master_fd], [], [], 1.0)
                if readable:
                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        buffer += data.decode("utf-8", errors="replace")

                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            line = re.sub(r"\x1b\[[0-9;]*m", "", line)
                            line = re.sub(r"\r", "", line)

                            if "Device" in line:
                                # Check for RSSI update: [CHG] Device XX:XX:XX RSSI: -65
                                rssi_match = re.search(
                                    r"([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}).*RSSI:\s*(-?\d+)",
                                    line,
                                )
                                if rssi_match:
                                    mac = rssi_match.group(1).upper()
                                    rssi = int(rssi_match.group(2))
                                    if mac in app_module.bt_devices:
                                        app_module.bt_devices[mac]["rssi"] = rssi
                                        app_module.bt_devices[mac]["last_seen"] = time.time()
                                        # Send RSSI update
                                        app_module.bt_queue.put(
                                            {
                                                **app_module.bt_devices[mac],
                                                "type": "device",
                                                "device_type": app_module.bt_devices[mac].get("type", "other"),
                                                "action": "update",
                                            }
                                        )
                                    continue

                                # Check for new device: [NEW] Device XX:XX:XX Name
                                match = re.search(
                                    r"([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})\s*(.*)",
                                    line,
                                )
                                if match:
                                    mac = match.group(1).upper()
                                    name = match.group(2).strip()

                                    # Extract RSSI from name if present
                                    rssi_in_name = re.search(r"RSSI:\s*(-?\d+)", name)
                                    initial_rssi = int(rssi_in_name.group(1)) if rssi_in_name else None

                                    # Remove "RSSI: -XX" from name
                                    name = re.sub(r"\s*RSSI:\s*-?\d+\s*", "", name).strip()

                                    manufacturer = get_manufacturer(mac)
                                    device = {
                                        "mac": mac,
                                        "name": name or "[Unknown]",
                                        "manufacturer": manufacturer,
                                        "type": classify_bt_device(name, None, None, manufacturer),
                                        "rssi": initial_rssi,
                                        "last_seen": time.time(),
                                    }

                                    tracker = detect_tracker(mac, name)
                                    if tracker:
                                        device["tracker"] = tracker

                                    is_new = mac not in app_module.bt_devices
                                    app_module.bt_devices[mac] = device

                                    app_module.bt_queue.put(
                                        {
                                            **device,
                                            "type": "device",
                                            "device_type": device.get("type", "other"),
                                            "action": "new" if is_new else "update",
                                        }
                                    )
                    except OSError:
                        break

            with contextlib.suppress(OSError):
                os.close(master_fd)

    except Exception as e:
        app_module.bt_queue.put({"type": "error", "text": str(e)})
    finally:
        process.wait()
        app_module.bt_queue.put({"type": "status", "text": "stopped"})
        with app_module.bt_lock:
            app_module.bt_process = None


@bluetooth_bp.route("/reload-oui", methods=["POST"])
def reload_oui_database_route():
    """Reload OUI database from external file."""
    new_db = load_oui_database()
    if new_db:
        OUI_DATABASE.clear()
        OUI_DATABASE.update(new_db)
        return api_success(data={"entries": len(OUI_DATABASE)})
    return api_error("Could not load oui_database.json")


@bluetooth_bp.route("/interfaces")
def get_bt_interfaces():
    """Get available Bluetooth interfaces and tools."""
    interfaces = detect_bt_interfaces()
    tools = {
        "hcitool": check_tool("hcitool"),
        "bluetoothctl": check_tool("bluetoothctl"),
        "hciconfig": check_tool("hciconfig"),
        "l2ping": check_tool("l2ping"),
        "sdptool": check_tool("sdptool"),
    }
    return jsonify({"interfaces": interfaces, "tools": tools, "current_interface": app_module.bt_interface})


@bluetooth_bp.route("/scan/start", methods=["POST"])
def start_bt_scan():
    """Start Bluetooth scanning."""
    with app_module.bt_lock:
        if app_module.bt_process:
            if app_module.bt_process.poll() is None:
                return api_error("Scan already running")
            else:
                app_module.bt_process = None

        data = request.json
        scan_mode = data.get("mode", "hcitool")
        scan_ble = data.get("scan_ble", True)

        # Validate Bluetooth interface name
        try:
            interface = validate_bluetooth_interface(data.get("interface", "hci0"))
        except ValueError as e:
            return api_error(str(e), 400)

        app_module.bt_interface = interface
        app_module.bt_devices = {}

        while not app_module.bt_queue.empty():
            try:
                app_module.bt_queue.get_nowait()
            except queue.Empty:
                break

        try:
            if scan_mode == "hcitool":
                if scan_ble:
                    cmd = ["hcitool", "-i", interface, "lescan", "--duplicates"]
                else:
                    cmd = ["hcitool", "-i", interface, "scan"]

                app_module.bt_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            elif scan_mode == "bluetoothctl":
                master_fd, slave_fd = pty.openpty()
                app_module.bt_process = subprocess.Popen(
                    ["bluetoothctl"], stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True
                )
                os.close(slave_fd)
                app_module.bt_process._master_fd = master_fd

                time.sleep(0.5)
                os.write(master_fd, b"power on\n")
                time.sleep(0.3)
                os.write(master_fd, b"scan on\n")

            else:
                return api_error(f"Unknown scan mode: {scan_mode}")

            time.sleep(0.5)

            if app_module.bt_process.poll() is not None:
                stderr_output = app_module.bt_process.stderr.read().decode("utf-8", errors="replace").strip()
                app_module.bt_process = None
                return api_error(stderr_output or "Process failed to start")

            thread = threading.Thread(target=stream_bt_scan, args=(app_module.bt_process, scan_mode))
            thread.daemon = True
            thread.start()

            app_module.bt_queue.put({"type": "info", "text": f"Started {scan_mode} scan on {interface}"})
            return jsonify({"status": "started", "mode": scan_mode, "interface": interface})

        except FileNotFoundError as e:
            return api_error(f"Tool not found: {e.filename}")
        except Exception as e:
            return api_error(str(e))


@bluetooth_bp.route("/scan/stop", methods=["POST"])
def stop_bt_scan():
    """Stop Bluetooth scanning."""
    with app_module.bt_lock:
        if app_module.bt_process:
            app_module.bt_process.terminate()
            try:
                app_module.bt_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                app_module.bt_process.kill()
            app_module.bt_process = None
            return jsonify({"status": "stopped"})
        return jsonify({"status": "not_running"})


@bluetooth_bp.route("/reset", methods=["POST"])
def reset_bt_adapter():
    """Reset Bluetooth adapter."""
    data = request.json

    # Validate Bluetooth interface name
    try:
        interface = validate_bluetooth_interface(data.get("interface", "hci0"))
    except ValueError as e:
        return api_error(str(e), 400)

    with app_module.bt_lock:
        if app_module.bt_process:
            try:
                app_module.bt_process.terminate()
                app_module.bt_process.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                with contextlib.suppress(OSError):
                    app_module.bt_process.kill()
            app_module.bt_process = None

    try:
        subprocess.run(["pkill", "-f", "hcitool"], capture_output=True, timeout=2)
        subprocess.run(["pkill", "-f", "bluetoothctl"], capture_output=True, timeout=2)
        time.sleep(0.5)

        subprocess.run(["rfkill", "unblock", "bluetooth"], capture_output=True, timeout=5)
        subprocess.run(["hciconfig", interface, "down"], capture_output=True, timeout=5)
        time.sleep(1)
        subprocess.run(["hciconfig", interface, "up"], capture_output=True, timeout=5)
        time.sleep(0.5)

        result = subprocess.run(["hciconfig", interface], capture_output=True, text=True, timeout=5)
        is_up = "UP RUNNING" in result.stdout

        return jsonify(
            {
                "status": "success" if is_up else "warning",
                "message": f"Adapter {interface} reset" if is_up else "Reset attempted but adapter may still be down",
                "is_up": is_up,
            }
        )

    except Exception as e:
        return api_error(str(e))


@bluetooth_bp.route("/enum", methods=["POST"])
def enum_bt_services():
    """Enumerate services on a Bluetooth device."""
    data = request.json
    target_mac = data.get("mac")

    if not target_mac:
        return api_error("Target MAC required")

    try:
        result = subprocess.run(["sdptool", "browse", target_mac], capture_output=True, text=True, timeout=30)

        services = []
        current_service = {}

        for line in result.stdout.split("\n"):
            line = line.strip()
            if line.startswith("Service Name:"):
                if current_service:
                    services.append(current_service)
                current_service = {"name": line.split(":", 1)[1].strip()}
            elif line.startswith("Service Description:"):
                current_service["description"] = line.split(":", 1)[1].strip()

        if current_service:
            services.append(current_service)

        app_module.bt_services[target_mac] = services

        return api_success(data={"mac": target_mac, "services": services})

    except subprocess.TimeoutExpired:
        return api_error("Connection timed out")
    except FileNotFoundError:
        return api_error("sdptool not found")
    except Exception as e:
        return api_error(str(e))


@bluetooth_bp.route("/devices")
def get_bt_devices():
    """Get current list of discovered Bluetooth devices."""
    return jsonify(
        {
            "devices": list(app_module.bt_devices.values()),
            "beacons": list(app_module.bt_beacons.values()),
            "interface": app_module.bt_interface,
        }
    )


@bluetooth_bp.route("/stream")
def stream_bt():
    """SSE stream for Bluetooth events."""

    response = Response(
        sse_stream_fanout(
            source_queue=app_module.bt_queue,
            channel_key="bluetooth",
            timeout=1.0,
            keepalive_interval=30.0,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response
