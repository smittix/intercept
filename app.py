"""
INTERCEPT - Signal Intelligence Platform

Flask application and shared state.
"""

from __future__ import annotations

import sys
import site

# Ensure user site-packages is available (may be disabled when running as root/sudo)
if not site.ENABLE_USER_SITE:
    user_site = site.getusersitepackages()
    if user_site and user_site not in sys.path:
        sys.path.insert(0, user_site)

import os
import queue
import threading
import platform
import subprocess

from typing import Any

from flask import Flask, render_template, jsonify, send_file, Response, request

from config import VERSION
from utils.dependencies import check_tool, check_all_dependencies, TOOL_DEPENDENCIES
from utils.process import cleanup_stale_processes
from utils.sdr import SDRFactory


# Create Flask app
app = Flask(__name__)

# ============================================
# GLOBAL PROCESS MANAGEMENT
# ============================================

# Pager decoder
current_process = None
output_queue = queue.Queue()
process_lock = threading.Lock()

# RTL_433 sensor
sensor_process = None
sensor_queue = queue.Queue()
sensor_lock = threading.Lock()

# WiFi
wifi_process = None
wifi_queue = queue.Queue()
wifi_lock = threading.Lock()

# Bluetooth
bt_process = None
bt_queue = queue.Queue()
bt_lock = threading.Lock()

# ADS-B aircraft
adsb_process = None
adsb_queue = queue.Queue()
adsb_lock = threading.Lock()

# Satellite/Iridium
satellite_process = None
satellite_queue = queue.Queue()
satellite_lock = threading.Lock()

# ============================================
# GLOBAL STATE DICTIONARIES
# ============================================

# Logging settings
logging_enabled = False
log_file_path = "pager_messages.log"

# WiFi state
wifi_monitor_interface = None
wifi_networks = {}  # BSSID -> network info
wifi_clients = {}  # Client MAC -> client info
wifi_handshakes = []  # Captured handshakes

# Bluetooth state
bt_interface = None
bt_devices = {}  # MAC -> device info
bt_beacons = {}  # MAC -> beacon info (AirTags, Tiles, iBeacons)
bt_services = {}  # MAC -> list of services

# Aircraft (ADS-B) state
adsb_aircraft = {}  # ICAO hex -> aircraft info

# Satellite state
satellite_passes = [] # Predicted satellite passes


# ============================================
# MAIN ROUTES
# ============================================


@app.route("/")
def index() -> str:
    tools = {"rtl_fm": check_tool("rtl_fm"), "multimon": check_tool("multimon-ng"), "rtl_433": check_tool("rtl_433")}
    devices = [d.to_dict() for d in SDRFactory.detect_devices()]
    return render_template('index.html', tools=tools, devices=devices, version=VERSION)


@app.route("/favicon.svg")
def favicon() -> Response:
    return send_file("favicon.svg", mimetype="image/svg+xml")


@app.route("/devices")
def get_devices() -> Response:
    """Get all detected SDR devices with hardware type info."""
    devices = SDRFactory.detect_devices()
    return jsonify([d.to_dict() for d in devices])


@app.route("/dependencies")
def get_dependencies() -> Response:
    """Get status of all tool dependencies."""
    results = check_all_dependencies()

    # Determine OS for install instructions
    system = platform.system().lower()
    if system == "darwin":
        install_method = "brew"
    elif system == "linux":
        install_method = "apt"
    else:
        install_method = "manual"

    return jsonify({"os": system, "install_method": install_method, "modes": results})


@app.route("/export/aircraft", methods=["GET"])
def export_aircraft() -> Response:
    """Export aircraft data as JSON or CSV."""
    import csv
    import io

    format_type = request.args.get("format", "json").lower()

    if format_type == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["icao", "callsign", "altitude", "speed", "heading", "lat", "lon", "squawk", "last_seen"])

        for icao, ac in adsb_aircraft.items():
            writer.writerow(
                [
                    icao,
                    ac.get("callsign", ""),
                    ac.get("altitude", ""),
                    ac.get("speed", ""),
                    ac.get("heading", ""),
                    ac.get("lat", ""),
                    ac.get("lon", ""),
                    ac.get("squawk", ""),
                    ac.get("lastSeen", ""),
                ]
            )

        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=aircraft.csv"
        return response
    else:
        return jsonify(
            {
                "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
                "aircraft": list(adsb_aircraft.values()),
            }
        )


@app.route("/export/wifi", methods=["GET"])
def export_wifi() -> Response:
    """Export WiFi networks as JSON or CSV."""
    import csv
    import io

    format_type = request.args.get("format", "json").lower()

    if format_type == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["bssid", "ssid", "channel", "signal", "encryption", "clients"])

        for bssid, net in wifi_networks.items():
            writer.writerow(
                [
                    bssid,
                    net.get("ssid", ""),
                    net.get("channel", ""),
                    net.get("signal", ""),
                    net.get("encryption", ""),
                    net.get("clients", 0),
                ]
            )

        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=wifi_networks.csv"
        return response
    else:
        return jsonify(
            {
                "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
                "networks": list(wifi_networks.values()),
                "clients": list(wifi_clients.values()),
            }
        )


@app.route("/export/bluetooth", methods=["GET"])
def export_bluetooth() -> Response:
    """Export Bluetooth devices as JSON or CSV."""
    import csv
    import io

    format_type = request.args.get("format", "json").lower()

    if format_type == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["mac", "name", "rssi", "type", "manufacturer", "last_seen"])

        for mac, dev in bt_devices.items():
            writer.writerow(
                [
                    mac,
                    dev.get("name", ""),
                    dev.get("rssi", ""),
                    dev.get("type", ""),
                    dev.get("manufacturer", ""),
                    dev.get("lastSeen", ""),
                ]
            )

        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=bluetooth_devices.csv"
        return response
    else:
        return jsonify(
            {
                "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
                "devices": list(bt_devices.values()),
                "beacons": list(bt_beacons.values()),
            }
        )


@app.route("/killall", methods=["POST"])
def kill_all() -> Response:
    """Kill all decoder and WiFi processes."""
    global current_process, sensor_process, wifi_process, adsb_process

    # Import adsb module to reset its state
    from routes import adsb as adsb_module

    killed = []
    processes_to_kill = ["rtl_fm", "multimon-ng", "rtl_433", "airodump-ng", "aireplay-ng", "airmon-ng", "dump1090"]

    for proc in processes_to_kill:
        try:
            result = subprocess.run(["pkill", "-f", proc], capture_output=True)
            if result.returncode == 0:
                killed.append(proc)
        except (subprocess.SubprocessError, OSError):
            pass

    with process_lock:
        current_process = None

    with sensor_lock:
        sensor_process = None

    with wifi_lock:
        wifi_process = None

    # Reset ADS-B state
    with adsb_lock:
        adsb_process = None
        adsb_module.adsb_using_service = False

    return jsonify({"status": "killed", "processes": killed})


def main() -> None:
    """Main entry point."""
    import argparse
    import config

    parser = argparse.ArgumentParser(
        description="INTERCEPT - Signal Intelligence Platform",
        epilog="Environment variables: INTERCEPT_HOST, INTERCEPT_PORT, INTERCEPT_DEBUG, INTERCEPT_LOG_LEVEL",
    )
    parser.add_argument(
        "-p", "--port", type=int, default=config.PORT, help=f"Port to run server on (default: {config.PORT})"
    )
    parser.add_argument("-H", "--host", default=config.HOST, help=f"Host to bind to (default: {config.HOST})")
    parser.add_argument("-d", "--debug", action="store_true", default=config.DEBUG, help="Enable debug mode")
    parser.add_argument("--check-deps", action="store_true", help="Check dependencies and exit")
    args = parser.parse_args()

    # Check dependencies only
    if args.check_deps:
        results = check_all_dependencies()
        print("Dependency Status:")
        print("-" * 40)
        for mode, info in results.items():
            status = "✓" if info["ready"] else "✗"
            print(f"\n{status} {info['name']}:")
            for tool, tool_info in info["tools"].items():
                tool_status = "✓" if tool_info["installed"] else "✗"
                req = " (required)" if tool_info["required"] else ""
                print(f"    {tool_status} {tool}{req}")
        sys.exit(0)

    print("=" * 50)
    print("  INTERCEPT // Signal Intelligence")
    print("  Pager / 433MHz / Aircraft / Satellite / WiFi / BT")
    print("=" * 50)
    print()

    # Clean up any stale processes from previous runs
    cleanup_stale_processes()

    # Register blueprints
    from routes import register_blueprints

    register_blueprints(app)

    print(f"Open http://localhost:{args.port} in your browser")
    print()
    print("Press Ctrl+C to stop")
    print()

    # Avoid loading a global ~/.env when running the script directly.
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True,
        load_dotenv=False,
    )
