#!/usr/bin/env python3
"""
Pager Decoder - POCSAG/FLEX decoder using RTL-SDR and multimon-ng
"""

import subprocess
import shutil
import re
import threading
import queue
import pty
import os
import select
import json
from flask import Flask, render_template_string, jsonify, request, Response, send_file

app = Flask(__name__)


def load_oui_database():
    """Load OUI database from external JSON file, with fallback to built-in."""
    oui_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'oui_database.json')
    try:
        if os.path.exists(oui_file):
            with open(oui_file, 'r') as f:
                data = json.load(f)
                # Remove comment fields
                return {k: v for k, v in data.items() if not k.startswith('_')}
    except Exception as e:
        print(f"[OUI] Error loading oui_database.json: {e}, using built-in database")
    return None  # Will fall back to built-in

# Global process management
current_process = None
sensor_process = None
wifi_process = None
bt_process = None
output_queue = queue.Queue()
sensor_queue = queue.Queue()
wifi_queue = queue.Queue()
bt_queue = queue.Queue()
process_lock = threading.Lock()
sensor_lock = threading.Lock()
wifi_lock = threading.Lock()
bt_lock = threading.Lock()

# Logging settings
logging_enabled = False
log_file_path = 'pager_messages.log'

# WiFi state
wifi_monitor_interface = None
wifi_networks = {}  # BSSID -> network info
wifi_clients = {}   # Client MAC -> client info
wifi_handshakes = []  # Captured handshakes

# Bluetooth state
bt_interface = None
bt_devices = {}      # MAC -> device info
bt_beacons = {}      # MAC -> beacon info (AirTags, Tiles, iBeacons)
bt_services = {}     # MAC -> list of services

# Known beacon prefixes for detection
AIRTAG_PREFIXES = ['4C:00']  # Apple continuity
TILE_PREFIXES = ['C4:E7', 'DC:54', 'E4:B0', 'F8:8A']
SAMSUNG_TRACKER = ['58:4D', 'A0:75']

# Drone detection patterns
DRONE_SSID_PATTERNS = [
    # DJI
    'DJI-', 'DJI_', 'Mavic', 'Phantom', 'Spark-', 'Mini-', 'Air-', 'Inspire',
    'Matrice', 'Avata', 'FPV-', 'Osmo', 'RoboMaster', 'Tello',
    # Parrot
    'Parrot', 'Bebop', 'Anafi', 'Disco-', 'Mambo', 'Swing',
    # Autel
    'Autel', 'EVO-', 'Dragonfish', 'Lite+', 'Nano',
    # Skydio
    'Skydio',
    # Other brands
    'Holy Stone', 'Potensic', 'SYMA', 'Hubsan', 'Eachine', 'FIMI',
    'Xiaomi_FIMI', 'Yuneec', 'Typhoon', 'PowerVision', 'PowerEgg',
    # Generic drone patterns
    'Drone', 'UAV-', 'Quadcopter', 'FPV_', 'RC-Drone'
]

# Drone OUI prefixes (MAC address prefixes for drone manufacturers)
DRONE_OUI_PREFIXES = {
    # DJI
    '60:60:1F': 'DJI', '48:1C:B9': 'DJI', '34:D2:62': 'DJI', 'E0:DB:55': 'DJI',
    'C8:6C:87': 'DJI', 'A0:14:3D': 'DJI', '70:D7:11': 'DJI', '98:3A:56': 'DJI',
    # Parrot
    '90:03:B7': 'Parrot', 'A0:14:3D': 'Parrot', '00:12:1C': 'Parrot', '00:26:7E': 'Parrot',
    # Autel
    '8C:F5:A3': 'Autel', 'D8:E0:E1': 'Autel',
    # Yuneec
    '60:60:1F': 'Yuneec',
    # Skydio
    'F8:0F:6F': 'Skydio',
}

# OUI Database for manufacturer lookup (expanded)
OUI_DATABASE = {
    # Apple (extensive list)
    '00:25:DB': 'Apple', '04:52:F3': 'Apple', '0C:3E:9F': 'Apple', '10:94:BB': 'Apple',
    '14:99:E2': 'Apple', '20:78:F0': 'Apple', '28:6A:BA': 'Apple', '3C:22:FB': 'Apple',
    '40:98:AD': 'Apple', '48:D7:05': 'Apple', '4C:57:CA': 'Apple', '54:4E:90': 'Apple',
    '5C:97:F3': 'Apple', '60:F8:1D': 'Apple', '68:DB:CA': 'Apple', '70:56:81': 'Apple',
    '78:7B:8A': 'Apple', '7C:D1:C3': 'Apple', '84:FC:FE': 'Apple', '8C:2D:AA': 'Apple',
    '90:B0:ED': 'Apple', '98:01:A7': 'Apple', '98:D6:BB': 'Apple', 'A4:D1:D2': 'Apple',
    'AC:BC:32': 'Apple', 'B0:34:95': 'Apple', 'B8:C1:11': 'Apple', 'C8:69:CD': 'Apple',
    'D0:03:4B': 'Apple', 'DC:A9:04': 'Apple', 'E0:C7:67': 'Apple', 'F0:18:98': 'Apple',
    'F4:5C:89': 'Apple', '78:4F:43': 'Apple', '00:CD:FE': 'Apple', '04:4B:ED': 'Apple',
    '04:D3:CF': 'Apple', '08:66:98': 'Apple', '0C:74:C2': 'Apple', '10:DD:B1': 'Apple',
    '14:10:9F': 'Apple', '18:EE:69': 'Apple', '1C:36:BB': 'Apple', '24:A0:74': 'Apple',
    '28:37:37': 'Apple', '2C:BE:08': 'Apple', '34:08:BC': 'Apple', '38:C9:86': 'Apple',
    '3C:06:30': 'Apple', '44:D8:84': 'Apple', '48:A9:1C': 'Apple', '4C:32:75': 'Apple',
    '50:32:37': 'Apple', '54:26:96': 'Apple', '58:B0:35': 'Apple', '5C:F7:E6': 'Apple',
    '64:A3:CB': 'Apple', '68:FE:F7': 'Apple', '6C:4D:73': 'Apple', '70:DE:E2': 'Apple',
    '74:E2:F5': 'Apple', '78:67:D7': 'Apple', '7C:04:D0': 'Apple', '80:E6:50': 'Apple',
    '84:78:8B': 'Apple', '88:66:A5': 'Apple', '8C:85:90': 'Apple', '94:E9:6A': 'Apple',
    '9C:F4:8E': 'Apple', 'A0:99:9B': 'Apple', 'A4:83:E7': 'Apple', 'A8:5C:2C': 'Apple',
    'AC:1F:74': 'Apple', 'B0:19:C6': 'Apple', 'B4:F1:DA': 'Apple', 'BC:52:B7': 'Apple',
    'C0:A5:3E': 'Apple', 'C4:B3:01': 'Apple', 'CC:20:E8': 'Apple', 'D0:C5:F3': 'Apple',
    'D4:61:9D': 'Apple', 'D8:1C:79': 'Apple', 'E0:5F:45': 'Apple', 'E4:C6:3D': 'Apple',
    'F0:B4:79': 'Apple', 'F4:0F:24': 'Apple', 'F8:4D:89': 'Apple', 'FC:D8:48': 'Apple',
    # Samsung
    '00:1B:66': 'Samsung', '00:21:19': 'Samsung', '00:26:37': 'Samsung', '5C:0A:5B': 'Samsung',
    '8C:71:F8': 'Samsung', 'C4:73:1E': 'Samsung', '38:2C:4A': 'Samsung', '00:1E:4C': 'Samsung',
    '00:12:47': 'Samsung', '00:15:99': 'Samsung', '00:17:D5': 'Samsung', '00:1D:F6': 'Samsung',
    '00:21:D1': 'Samsung', '00:24:54': 'Samsung', '00:26:5D': 'Samsung', '08:D4:2B': 'Samsung',
    '10:D5:42': 'Samsung', '14:49:E0': 'Samsung', '18:3A:2D': 'Samsung', '1C:66:AA': 'Samsung',
    '24:4B:81': 'Samsung', '28:98:7B': 'Samsung', '2C:AE:2B': 'Samsung', '30:96:FB': 'Samsung',
    '34:C3:AC': 'Samsung', '38:01:95': 'Samsung', '3C:5A:37': 'Samsung', '40:0E:85': 'Samsung',
    '44:4E:1A': 'Samsung', '4C:BC:A5': 'Samsung', '50:01:BB': 'Samsung', '50:A4:D0': 'Samsung',
    '54:88:0E': 'Samsung', '58:C3:8B': 'Samsung', '5C:2E:59': 'Samsung', '60:D0:A9': 'Samsung',
    '64:B3:10': 'Samsung', '68:48:98': 'Samsung', '6C:2F:2C': 'Samsung', '70:F9:27': 'Samsung',
    '74:45:8A': 'Samsung', '78:47:1D': 'Samsung', '7C:0B:C6': 'Samsung', '84:11:9E': 'Samsung',
    '88:32:9B': 'Samsung', '8C:77:12': 'Samsung', '90:18:7C': 'Samsung', '94:35:0A': 'Samsung',
    '98:52:B1': 'Samsung', '9C:02:98': 'Samsung', 'A0:0B:BA': 'Samsung', 'A4:7B:85': 'Samsung',
    'A8:06:00': 'Samsung', 'AC:5F:3E': 'Samsung', 'B0:72:BF': 'Samsung', 'B4:79:A7': 'Samsung',
    'BC:44:86': 'Samsung', 'C0:97:27': 'Samsung', 'C4:42:02': 'Samsung', 'CC:07:AB': 'Samsung',
    'D0:22:BE': 'Samsung', 'D4:87:D8': 'Samsung', 'D8:90:E8': 'Samsung', 'E4:7C:F9': 'Samsung',
    'E8:50:8B': 'Samsung', 'F0:25:B7': 'Samsung', 'F4:7B:5E': 'Samsung', 'FC:A1:3E': 'Samsung',
    # Google
    '54:60:09': 'Google', '00:1A:11': 'Google', 'F4:F5:D8': 'Google', '94:EB:2C': 'Google',
    '64:B5:C6': 'Google', '3C:5A:B4': 'Google', 'F8:8F:CA': 'Google', '20:DF:B9': 'Google',
    '54:27:1E': 'Google', '58:CB:52': 'Google', 'A4:77:33': 'Google', 'F4:0E:22': 'Google',
    # Sony
    '00:13:A9': 'Sony', '00:1D:28': 'Sony', '00:24:BE': 'Sony', '04:5D:4B': 'Sony',
    '08:A9:5A': 'Sony', '10:4F:A8': 'Sony', '24:21:AB': 'Sony', '30:52:CB': 'Sony',
    '40:B8:37': 'Sony', '58:48:22': 'Sony', '70:9E:29': 'Sony', '84:00:D2': 'Sony',
    'AC:9B:0A': 'Sony', 'B4:52:7D': 'Sony', 'BC:60:A7': 'Sony', 'FC:0F:E6': 'Sony',
    # Bose
    '00:0C:8A': 'Bose', '04:52:C7': 'Bose', '08:DF:1F': 'Bose', '2C:41:A1': 'Bose',
    '4C:87:5D': 'Bose', '60:AB:D2': 'Bose', '88:C9:E8': 'Bose', 'D8:9C:67': 'Bose',
    # JBL/Harman
    '00:1D:DF': 'JBL', '08:AE:D6': 'JBL', '20:3C:AE': 'JBL', '44:5E:F3': 'JBL',
    '50:C9:71': 'JBL', '74:5E:1C': 'JBL', '88:C6:26': 'JBL', 'AC:12:2F': 'JBL',
    # Beats (Apple subsidiary)
    '00:61:71': 'Beats', '48:D6:D5': 'Beats', '9C:64:8B': 'Beats', 'A4:E9:75': 'Beats',
    # Jabra/GN Audio
    '00:13:17': 'Jabra', '1C:48:F9': 'Jabra', '50:C2:ED': 'Jabra', '70:BF:92': 'Jabra',
    '74:5C:4B': 'Jabra', '94:16:25': 'Jabra', 'D0:81:7A': 'Jabra', 'E8:EE:CC': 'Jabra',
    # Sennheiser
    '00:1B:66': 'Sennheiser', '00:22:27': 'Sennheiser', 'B8:AD:3E': 'Sennheiser',
    # Xiaomi
    '04:CF:8C': 'Xiaomi', '0C:1D:AF': 'Xiaomi', '10:2A:B3': 'Xiaomi', '18:59:36': 'Xiaomi',
    '20:47:DA': 'Xiaomi', '28:6C:07': 'Xiaomi', '34:CE:00': 'Xiaomi', '38:A4:ED': 'Xiaomi',
    '44:23:7C': 'Xiaomi', '50:64:2B': 'Xiaomi', '58:44:98': 'Xiaomi', '64:09:80': 'Xiaomi',
    '74:23:44': 'Xiaomi', '78:02:F8': 'Xiaomi', '7C:1C:4E': 'Xiaomi', '84:F3:EB': 'Xiaomi',
    '8C:BE:BE': 'Xiaomi', '98:FA:E3': 'Xiaomi', 'A4:77:58': 'Xiaomi', 'AC:C1:EE': 'Xiaomi',
    'B0:E2:35': 'Xiaomi', 'C4:0B:CB': 'Xiaomi', 'C8:47:8C': 'Xiaomi', 'D4:97:0B': 'Xiaomi',
    'E4:46:DA': 'Xiaomi', 'F0:B4:29': 'Xiaomi', 'FC:64:BA': 'Xiaomi',
    # Huawei
    '00:18:82': 'Huawei', '00:1E:10': 'Huawei', '00:25:68': 'Huawei', '04:B0:E7': 'Huawei',
    '08:63:61': 'Huawei', '10:1B:54': 'Huawei', '18:DE:D7': 'Huawei', '20:A6:80': 'Huawei',
    '28:31:52': 'Huawei', '34:12:98': 'Huawei', '3C:47:11': 'Huawei', '48:00:31': 'Huawei',
    '4C:50:77': 'Huawei', '5C:7D:5E': 'Huawei', '60:DE:44': 'Huawei', '70:72:3C': 'Huawei',
    '78:F5:57': 'Huawei', '80:B6:86': 'Huawei', '88:53:D4': 'Huawei', '94:04:9C': 'Huawei',
    'A4:99:47': 'Huawei', 'B4:15:13': 'Huawei', 'BC:76:70': 'Huawei', 'C8:D1:5E': 'Huawei',
    'DC:D2:FC': 'Huawei', 'E4:68:A3': 'Huawei', 'F4:63:1F': 'Huawei',
    # OnePlus/BBK
    '64:A2:F9': 'OnePlus', 'C0:EE:FB': 'OnePlus', '94:65:2D': 'OnePlus',
    # Fitbit
    '2C:09:4D': 'Fitbit', 'C4:D9:87': 'Fitbit', 'E4:88:6D': 'Fitbit',
    # Garmin
    '00:1C:D1': 'Garmin', 'C4:AC:59': 'Garmin', 'E8:0F:C8': 'Garmin',
    # Microsoft
    '00:50:F2': 'Microsoft', '28:18:78': 'Microsoft', '60:45:BD': 'Microsoft',
    '7C:1E:52': 'Microsoft', '98:5F:D3': 'Microsoft', 'B4:0E:DE': 'Microsoft',
    # Intel
    '00:1B:21': 'Intel', '00:1C:C0': 'Intel', '00:1E:64': 'Intel', '00:21:5C': 'Intel',
    '08:D4:0C': 'Intel', '18:1D:EA': 'Intel', '34:02:86': 'Intel', '40:74:E0': 'Intel',
    '48:51:B7': 'Intel', '58:A0:23': 'Intel', '64:D4:DA': 'Intel', '80:19:34': 'Intel',
    '8C:8D:28': 'Intel', 'A4:4E:31': 'Intel', 'B4:6B:FC': 'Intel', 'C8:D0:83': 'Intel',
    # Qualcomm/Atheros
    '00:03:7F': 'Qualcomm', '00:24:E4': 'Qualcomm', '04:F0:21': 'Qualcomm',
    '1C:4B:D6': 'Qualcomm', '88:71:B1': 'Qualcomm', 'A0:65:18': 'Qualcomm',
    # Broadcom
    '00:10:18': 'Broadcom', '00:1A:2B': 'Broadcom', '20:10:7A': 'Broadcom',
    # Realtek
    '00:0A:EB': 'Realtek', '00:E0:4C': 'Realtek', '48:02:2A': 'Realtek',
    '52:54:00': 'Realtek', '80:EA:96': 'Realtek',
    # Logitech
    '00:1F:20': 'Logitech', '34:88:5D': 'Logitech', '6C:B7:49': 'Logitech',
    # Lenovo
    '00:09:2D': 'Lenovo', '28:D2:44': 'Lenovo', '54:EE:75': 'Lenovo', '98:FA:9B': 'Lenovo',
    # Dell
    '00:14:22': 'Dell', '00:1A:A0': 'Dell', '18:DB:F2': 'Dell', '34:17:EB': 'Dell',
    '78:2B:CB': 'Dell', 'A4:BA:DB': 'Dell', 'E4:B9:7A': 'Dell',
    # HP
    '00:0F:61': 'HP', '00:14:C2': 'HP', '10:1F:74': 'HP', '28:80:23': 'HP',
    '38:63:BB': 'HP', '5C:B9:01': 'HP', '80:CE:62': 'HP', 'A0:D3:C1': 'HP',
    # Tile
    'F8:E4:E3': 'Tile', 'C4:E7:BE': 'Tile', 'DC:54:D7': 'Tile', 'E4:B0:21': 'Tile',
    # Raspberry Pi
    'B8:27:EB': 'Raspberry Pi', 'DC:A6:32': 'Raspberry Pi', 'E4:5F:01': 'Raspberry Pi',
    # Amazon
    '00:FC:8B': 'Amazon', '10:CE:A9': 'Amazon', '34:D2:70': 'Amazon', '40:B4:CD': 'Amazon',
    '44:65:0D': 'Amazon', '68:54:FD': 'Amazon', '74:C2:46': 'Amazon', '84:D6:D0': 'Amazon',
    'A0:02:DC': 'Amazon', 'AC:63:BE': 'Amazon', 'B4:7C:9C': 'Amazon', 'FC:65:DE': 'Amazon',
    # Skullcandy
    '00:01:00': 'Skullcandy', '88:E6:03': 'Skullcandy',
    # Bang & Olufsen
    '00:21:3E': 'Bang & Olufsen', '78:C5:E5': 'Bang & Olufsen',
    # Audio-Technica
    'A0:E9:DB': 'Audio-Technica', 'EC:81:93': 'Audio-Technica',
    # Plantronics/Poly
    '00:1D:DF': 'Plantronics', 'B0:B4:48': 'Plantronics', 'E8:FC:AF': 'Plantronics',
    # Anker
    'AC:89:95': 'Anker', 'E8:AB:FA': 'Anker',
    # Misc/Generic
    '00:00:0A': 'Omron', '00:1A:7D': 'Cyber-Blue', '00:1E:3D': 'Alps Electric',
    '00:0B:57': 'Silicon Wave', '00:02:72': 'CC&C',
}

# Try to load from external file (easier to update)
_external_oui = load_oui_database()
if _external_oui:
    OUI_DATABASE = _external_oui
    print(f"[OUI] Loaded {len(OUI_DATABASE)} entries from oui_database.json")
else:
    print(f"[OUI] Using built-in database with {len(OUI_DATABASE)} entries")


HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>INTERCEPT // Signal Intelligence</title>
    <link rel="icon" type="image/svg+xml" href="/favicon.svg">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Rajdhani:wght@400;500;600;700&display=swap');

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        :root {
            --bg-primary: #000000;
            --bg-secondary: #0a0a0a;
            --bg-tertiary: #111111;
            --bg-card: #0d0d0d;
            --accent-cyan: #00d4ff;
            --accent-cyan-dim: #00d4ff40;
            --accent-green: #00ff88;
            --accent-red: #ff3366;
            --accent-orange: #ff8800;
            --text-primary: #ffffff;
            --text-secondary: #888888;
            --text-dim: #444444;
            --border-color: #1a1a1a;
            --border-glow: #00d4ff33;
        }

        body {
            font-family: 'Rajdhani', 'Segoe UI', sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            background-image:
                radial-gradient(ellipse at top, #001a2c 0%, transparent 50%),
                radial-gradient(ellipse at bottom, #0a0a0a 0%, var(--bg-primary) 100%);
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }

        header {
            background: linear-gradient(180deg, var(--bg-secondary) 0%, transparent 100%);
            padding: 30px 20px;
            text-align: center;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 25px;
            position: relative;
        }

        header::after {
            content: '';
            position: absolute;
            bottom: -1px;
            left: 50%;
            transform: translateX(-50%);
            width: 200px;
            height: 1px;
            background: linear-gradient(90deg, transparent, var(--accent-cyan), transparent);
        }

        header h1 {
            color: var(--text-primary);
            font-size: 2.5em;
            font-weight: 700;
            letter-spacing: 8px;
            text-transform: uppercase;
            margin-bottom: 8px;
            text-shadow: 0 0 30px var(--accent-cyan-dim);
        }

        header p {
            color: var(--text-secondary);
            font-size: 14px;
            letter-spacing: 3px;
            text-transform: uppercase;
        }

        .logo {
            margin-bottom: 15px;
            animation: logo-pulse 3s ease-in-out infinite;
        }

        .logo svg {
            filter: drop-shadow(0 0 10px var(--accent-cyan-dim));
        }

        @keyframes logo-pulse {
            0%, 100% {
                filter: drop-shadow(0 0 5px var(--accent-cyan-dim));
            }
            50% {
                filter: drop-shadow(0 0 20px var(--accent-cyan));
            }
        }

        .main-content {
            display: grid;
            grid-template-columns: 340px 1fr;
            gap: 25px;
        }

        @media (max-width: 900px) {
            .main-content {
                grid-template-columns: 1fr;
            }
        }

        .sidebar {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            padding: 20px;
            position: relative;
        }

        .sidebar::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, var(--accent-cyan), transparent);
        }

        .section {
            margin-bottom: 25px;
        }

        .section h3 {
            color: var(--accent-cyan);
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border-color);
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 3px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .section h3::before {
            content: '//';
            color: var(--text-dim);
        }

        .form-group {
            margin-bottom: 15px;
        }

        .form-group label {
            display: block;
            margin-bottom: 6px;
            color: var(--text-secondary);
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .form-group input,
        .form-group select {
            width: 100%;
            padding: 12px 15px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            font-family: 'JetBrains Mono', monospace;
            font-size: 14px;
            transition: all 0.2s ease;
        }

        .form-group input:focus,
        .form-group select:focus {
            outline: none;
            border-color: var(--accent-cyan);
            box-shadow: 0 0 15px var(--accent-cyan-dim), inset 0 0 15px var(--accent-cyan-dim);
        }

        .checkbox-group {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
        }

        .checkbox-group label {
            display: flex;
            align-items: center;
            gap: 8px;
            color: var(--text-secondary);
            font-size: 12px;
            cursor: pointer;
            padding: 8px 12px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            transition: all 0.2s ease;
        }

        .checkbox-group label:hover {
            border-color: var(--accent-cyan);
        }

        .checkbox-group input[type="checkbox"] {
            width: auto;
            accent-color: var(--accent-cyan);
        }

        .preset-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }

        .preset-btn {
            padding: 10px 16px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            cursor: pointer;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.2s ease;
        }

        .preset-btn:hover {
            background: var(--accent-cyan);
            color: var(--bg-primary);
            border-color: var(--accent-cyan);
            box-shadow: 0 0 20px var(--accent-cyan-dim);
        }

        .run-btn {
            width: 100%;
            padding: 16px;
            background: transparent;
            border: 2px solid var(--accent-green);
            color: var(--accent-green);
            font-family: 'Rajdhani', sans-serif;
            font-size: 14px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 4px;
            cursor: pointer;
            transition: all 0.3s ease;
            margin-top: 15px;
            position: relative;
            overflow: hidden;
        }

        .run-btn::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, var(--accent-green), transparent);
            opacity: 0.3;
            transition: left 0.5s ease;
        }

        .run-btn:hover {
            background: var(--accent-green);
            color: var(--bg-primary);
            box-shadow: 0 0 30px rgba(0, 255, 136, 0.4);
        }

        .run-btn:hover::before {
            left: 100%;
        }

        .stop-btn {
            width: 100%;
            padding: 16px;
            background: transparent;
            border: 2px solid var(--accent-red);
            color: var(--accent-red);
            font-family: 'Rajdhani', sans-serif;
            font-size: 14px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 4px;
            cursor: pointer;
            transition: all 0.3s ease;
            margin-top: 15px;
        }

        .stop-btn:hover {
            background: var(--accent-red);
            color: var(--bg-primary);
            box-shadow: 0 0 30px rgba(255, 51, 102, 0.4);
        }

        .output-panel {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
            position: relative;
        }

        .output-panel::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--accent-cyan), transparent);
        }

        .output-header {
            padding: 18px 25px;
            background: var(--bg-secondary);
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
        }

        .output-header h3 {
            color: var(--text-primary);
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 3px;
        }

        .stats {
            display: flex;
            gap: 25px;
            font-size: 11px;
            color: var(--text-secondary);
            font-family: 'JetBrains Mono', monospace;
        }

        .stats span {
            color: var(--accent-cyan);
            font-weight: 500;
        }

        .output-content {
            flex: 1;
            padding: 15px;
            overflow-y: auto;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            background: var(--bg-primary);
            margin: 15px;
            border: 1px solid var(--border-color);
            min-height: 500px;
            max-height: 600px;
        }

        .output-content::-webkit-scrollbar {
            width: 6px;
        }

        .output-content::-webkit-scrollbar-track {
            background: var(--bg-primary);
        }

        .output-content::-webkit-scrollbar-thumb {
            background: var(--border-color);
        }

        .output-content::-webkit-scrollbar-thumb:hover {
            background: var(--accent-cyan);
        }

        .message {
            padding: 15px;
            margin-bottom: 10px;
            border: 1px solid var(--border-color);
            border-left: 3px solid var(--accent-cyan);
            background: var(--bg-secondary);
            position: relative;
            transition: all 0.2s ease;
        }

        .message:hover {
            border-left-color: var(--accent-cyan);
            box-shadow: 0 0 20px var(--accent-cyan-dim);
        }

        .message.pocsag {
            border-left-color: var(--accent-cyan);
        }

        .message.flex {
            border-left-color: var(--accent-orange);
        }

        .message .header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-size: 10px;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .message .protocol {
            color: var(--accent-cyan);
            font-weight: 600;
        }

        .message.pocsag .protocol {
            color: var(--accent-cyan);
        }

        .message.flex .protocol {
            color: var(--accent-orange);
        }

        .message .address {
            color: var(--accent-green);
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            margin-bottom: 8px;
        }

        .message .content {
            color: var(--text-primary);
            word-wrap: break-word;
            font-size: 13px;
            line-height: 1.5;
        }

        .message .content.numeric {
            font-family: 'JetBrains Mono', monospace;
            font-size: 15px;
            letter-spacing: 2px;
            color: var(--accent-cyan);
        }

        .status-bar {
            padding: 15px 25px;
            background: var(--bg-secondary);
            border-top: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 11px;
        }

        .status-indicator {
            display: flex;
            align-items: center;
            gap: 10px;
            text-transform: uppercase;
            letter-spacing: 2px;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            background: var(--text-dim);
            position: relative;
        }

        .status-dot.running {
            background: var(--accent-green);
            box-shadow: 0 0 10px var(--accent-green);
            animation: pulse-glow 2s infinite;
        }

        @keyframes pulse-glow {
            0%, 100% {
                opacity: 1;
                box-shadow: 0 0 10px var(--accent-green);
            }
            50% {
                opacity: 0.7;
                box-shadow: 0 0 20px var(--accent-green), 0 0 30px var(--accent-green);
            }
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.3; }
        }

        .clear-btn {
            padding: 8px 16px;
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 2px;
            transition: all 0.2s ease;
        }

        .clear-btn:hover {
            border-color: var(--accent-cyan);
            color: var(--accent-cyan);
        }

        .tool-status {
            font-size: 10px;
            padding: 4px 10px;
            margin-left: 8px;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 600;
        }

        .tool-status.ok {
            background: transparent;
            border: 1px solid var(--accent-green);
            color: var(--accent-green);
        }

        .tool-status.missing {
            background: transparent;
            border: 1px solid var(--accent-red);
            color: var(--accent-red);
        }

        .info-text {
            font-size: 10px;
            color: var(--text-dim);
            margin-top: 8px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .header-controls {
            display: flex;
            align-items: center;
            gap: 20px;
        }

        .signal-meter {
            display: flex;
            align-items: flex-end;
            gap: 2px;
            height: 20px;
            padding: 0 10px;
        }

        .signal-bar {
            width: 4px;
            background: var(--border-color);
            transition: all 0.1s ease;
        }

        .signal-bar:nth-child(1) { height: 4px; }
        .signal-bar:nth-child(2) { height: 8px; }
        .signal-bar:nth-child(3) { height: 12px; }
        .signal-bar:nth-child(4) { height: 16px; }
        .signal-bar:nth-child(5) { height: 20px; }

        .signal-bar.active {
            background: var(--accent-cyan);
            box-shadow: 0 0 8px var(--accent-cyan);
        }

        .waterfall-container {
            padding: 0 15px;
            margin-bottom: 10px;
        }

        #waterfallCanvas {
            width: 100%;
            height: 60px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            transition: box-shadow 0.3s ease;
        }

        #waterfallCanvas.active {
            box-shadow: 0 0 15px var(--accent-cyan-dim);
            border-color: var(--accent-cyan);
        }

        .status-controls {
            display: flex;
            gap: 8px;
            align-items: center;
        }

        .control-btn {
            padding: 6px 12px;
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.2s ease;
            font-family: 'Rajdhani', sans-serif;
        }

        .control-btn:hover {
            border-color: var(--accent-cyan);
            color: var(--accent-cyan);
        }

        .control-btn.active {
            border-color: var(--accent-green);
            color: var(--accent-green);
        }

        .control-btn.muted {
            border-color: var(--accent-red);
            color: var(--accent-red);
        }

        /* Mode tabs */
        .mode-tabs {
            display: flex;
            gap: 0;
            margin-bottom: 20px;
            border: 1px solid var(--border-color);
        }

        .mode-tab {
            flex: 1;
            padding: 12px 16px;
            background: var(--bg-primary);
            border: none;
            color: var(--text-secondary);
            cursor: pointer;
            font-family: 'Rajdhani', sans-serif;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 2px;
            transition: all 0.2s ease;
        }

        .mode-tab:not(:last-child) {
            border-right: 1px solid var(--border-color);
        }

        .mode-tab:hover {
            background: var(--bg-secondary);
            color: var(--text-primary);
        }

        .mode-tab.active {
            background: var(--accent-cyan);
            color: var(--bg-primary);
        }

        .mode-content {
            display: none;
        }

        .mode-content.active {
            display: block;
        }

        /* Sensor card styling */
        .sensor-card {
            padding: 15px;
            margin-bottom: 10px;
            border: 1px solid var(--border-color);
            border-left: 3px solid var(--accent-green);
            background: var(--bg-secondary);
        }

        .sensor-card .device-name {
            color: var(--accent-green);
            font-weight: 600;
            font-size: 13px;
            margin-bottom: 8px;
        }

        .sensor-card .sensor-data {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 8px;
        }

        .sensor-card .data-item {
            background: var(--bg-primary);
            padding: 8px 10px;
            border: 1px solid var(--border-color);
        }

        .sensor-card .data-label {
            font-size: 9px;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .sensor-card .data-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 14px;
            color: var(--accent-cyan);
        }

        /* Recon Dashboard - Prominent Device Intelligence */
        .recon-panel {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            margin: 15px;
            margin-bottom: 10px;
            position: relative;
        }

        .recon-panel::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, var(--accent-orange), var(--accent-cyan), transparent);
        }

        .recon-panel.collapsed .recon-content {
            display: none;
        }

        .recon-header {
            padding: 12px 15px;
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .recon-header h4 {
            color: var(--accent-orange);
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 2px;
            margin: 0;
        }

        .recon-stats {
            display: flex;
            gap: 15px;
            font-size: 10px;
            font-family: 'JetBrains Mono', monospace;
        }

        .recon-stats span {
            color: var(--accent-cyan);
        }

        .recon-content {
            max-height: 300px;
            overflow-y: auto;
        }

        .device-row {
            display: grid;
            grid-template-columns: 1fr auto auto auto;
            gap: 10px;
            padding: 10px 15px;
            border-bottom: 1px solid var(--border-color);
            font-size: 11px;
            align-items: center;
            transition: background 0.2s ease;
        }

        .device-row:hover {
            background: var(--bg-secondary);
        }

        .device-row.anomaly {
            border-left: 3px solid var(--accent-red);
            background: rgba(255, 51, 102, 0.05);
        }

        .device-row.new-device {
            border-left: 3px solid var(--accent-green);
            background: rgba(0, 255, 136, 0.05);
        }

        .device-info {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }

        .device-name-row {
            color: var(--text-primary);
            font-weight: 500;
        }

        .device-id {
            color: var(--text-dim);
            font-family: 'JetBrains Mono', monospace;
            font-size: 10px;
        }

        .device-meta {
            text-align: right;
            color: var(--text-secondary);
            font-family: 'JetBrains Mono', monospace;
        }

        .device-meta.encrypted {
            color: var(--accent-green);
        }

        .device-meta.plaintext {
            color: var(--accent-red);
        }

        .transmission-bar {
            width: 60px;
            height: 4px;
            background: var(--border-color);
            position: relative;
        }

        .transmission-bar-fill {
            height: 100%;
            background: var(--accent-cyan);
            transition: width 0.3s ease;
        }

        .badge {
            display: inline-block;
            padding: 2px 6px;
            font-size: 9px;
            text-transform: uppercase;
            letter-spacing: 1px;
            border: 1px solid;
        }

        .badge.proto-pocsag { border-color: var(--accent-cyan); color: var(--accent-cyan); }
        .badge.proto-flex { border-color: var(--accent-orange); color: var(--accent-orange); }
        .badge.proto-433 { border-color: var(--accent-green); color: var(--accent-green); }
        .badge.proto-unknown { border-color: var(--text-dim); color: var(--text-dim); }

        .recon-toggle {
            padding: 4px 8px;
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 9px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .recon-toggle:hover {
            border-color: var(--accent-orange);
            color: var(--accent-orange);
        }

        .recon-toggle.active {
            border-color: var(--accent-orange);
            color: var(--accent-orange);
            background: rgba(255, 136, 0, 0.1);
        }

        .hex-dump {
            font-family: 'JetBrains Mono', monospace;
            font-size: 10px;
            color: var(--text-dim);
            background: var(--bg-primary);
            padding: 8px;
            margin-top: 8px;
            border: 1px solid var(--border-color);
            word-break: break-all;
        }

        .timeline-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--accent-cyan);
            display: inline-block;
            margin-right: 5px;
        }

        .timeline-dot.recent { background: var(--accent-green); }
        .timeline-dot.stale { background: var(--accent-orange); }
        .timeline-dot.old { background: var(--text-dim); }

        /* WiFi Visualizations */
        .wifi-visuals {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            padding: 15px;
            background: var(--bg-secondary);
            margin: 0 15px 10px 15px;
            border: 1px solid var(--border-color);
        }

        @media (max-width: 1200px) {
            .wifi-visuals { grid-template-columns: 1fr; }
        }

        .wifi-visual-panel {
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            padding: 10px;
            position: relative;
        }

        .wifi-visual-panel h5 {
            color: var(--accent-cyan);
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 10px;
            padding-bottom: 5px;
            border-bottom: 1px solid var(--border-color);
        }

        /* Radar Display */
        .radar-container {
            position: relative;
            width: 150px;
            height: 150px;
            margin: 0 auto;
        }

        #radarCanvas, #btRadarCanvas {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            background: radial-gradient(circle, #001515 0%, #000a0a 100%);
            border: 1px solid var(--accent-cyan-dim);
        }

        #btRadarCanvas {
            background: radial-gradient(circle, #150015 0%, #0a000a 100%);
            border: 1px solid rgba(138, 43, 226, 0.3);
        }

        /* Channel Graph */
        .channel-graph {
            display: flex;
            align-items: flex-end;
            justify-content: space-around;
            height: 60px;
            padding: 5px 0;
            border-bottom: 1px solid var(--border-color);
        }

        .channel-bar-wrapper {
            display: flex;
            flex-direction: column;
            align-items: center;
            flex: 1;
        }

        .channel-bar {
            width: 80%;
            background: var(--border-color);
            min-height: 2px;
            transition: height 0.3s ease, background 0.3s ease;
        }

        .channel-bar.active {
            background: var(--accent-cyan);
            box-shadow: 0 0 5px var(--accent-cyan);
        }

        .channel-bar.congested {
            background: var(--accent-orange);
        }

        .channel-bar.very-congested {
            background: var(--accent-red);
        }

        .channel-label {
            font-size: 8px;
            color: #fff;
            margin-top: 3px;
        }

        /* Security Donut */
        .security-container {
            display: flex;
            align-items: center;
            gap: 15px;
        }

        .security-donut {
            width: 80px;
            height: 80px;
            flex-shrink: 0;
        }

        #securityCanvas {
            width: 100%;
            height: 100%;
        }

        .security-legend {
            display: flex;
            flex-direction: column;
            gap: 4px;
            font-size: 10px;
            font-family: 'JetBrains Mono', monospace;
        }

        .security-legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .security-legend-dot {
            width: 10px;
            height: 10px;
            border-radius: 2px;
        }

        .security-legend-dot.wpa3 { background: var(--accent-green); }
        .security-legend-dot.wpa2 { background: var(--accent-orange); }
        .security-legend-dot.wep { background: var(--accent-red); }
        .security-legend-dot.open { background: var(--accent-cyan); }

        /* Signal Strength Meter */
        .signal-strength-display {
            text-align: center;
            padding: 5px;
        }

        .target-ssid {
            font-size: 11px;
            color: var(--text-secondary);
            margin-bottom: 5px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .signal-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 28px;
            color: var(--accent-cyan);
            text-shadow: 0 0 10px var(--accent-cyan-dim);
        }

        .signal-value.weak { color: var(--accent-red); text-shadow: 0 0 10px rgba(255,51,102,0.4); }
        .signal-value.medium { color: var(--accent-orange); text-shadow: 0 0 10px rgba(255,136,0,0.4); }
        .signal-value.strong { color: var(--accent-green); text-shadow: 0 0 10px rgba(0,255,136,0.4); }

        .signal-bars-large {
            display: flex;
            justify-content: center;
            align-items: flex-end;
            gap: 3px;
            height: 30px;
            margin-top: 8px;
        }

        .signal-bar-large {
            width: 8px;
            background: var(--border-color);
            transition: all 0.2s ease;
        }

        .signal-bar-large.active {
            box-shadow: 0 0 5px currentColor;
        }

        .signal-bar-large.weak { background: var(--accent-red); }
        .signal-bar-large.medium { background: var(--accent-orange); }
        .signal-bar-large.strong { background: var(--accent-green); }

        .signal-bar-large:nth-child(1) { height: 20%; }
        .signal-bar-large:nth-child(2) { height: 40%; }
        .signal-bar-large:nth-child(3) { height: 60%; }
        .signal-bar-large:nth-child(4) { height: 80%; }
        .signal-bar-large:nth-child(5) { height: 100%; }

        /* Scanline effect overlay */
        body::before {
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            background: repeating-linear-gradient(
                0deg,
                rgba(0, 0, 0, 0.03),
                rgba(0, 0, 0, 0.03) 1px,
                transparent 1px,
                transparent 2px
            );
            z-index: 1000;
        }

        /* Disclaimer Modal */
        .disclaimer-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.95);
            z-index: 9999;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .disclaimer-modal {
            background: var(--bg-card);
            border: 1px solid var(--accent-cyan);
            max-width: 550px;
            padding: 30px;
            text-align: center;
            box-shadow: 0 0 50px rgba(0, 212, 255, 0.3);
        }

        .disclaimer-modal h2 {
            color: var(--accent-red);
            font-size: 1.5em;
            margin-bottom: 20px;
            letter-spacing: 3px;
        }

        .disclaimer-modal .warning-icon {
            font-size: 48px;
            margin-bottom: 15px;
        }

        .disclaimer-modal p {
            color: var(--text-secondary);
            font-size: 13px;
            line-height: 1.8;
            margin-bottom: 15px;
            text-align: left;
        }

        .disclaimer-modal ul {
            text-align: left;
            color: var(--text-secondary);
            font-size: 12px;
            margin: 15px 0;
            padding-left: 20px;
        }

        .disclaimer-modal ul li {
            margin-bottom: 8px;
        }

        .disclaimer-modal .accept-btn {
            background: var(--accent-cyan);
            color: #000;
            border: none;
            padding: 12px 40px;
            font-family: 'Rajdhani', sans-serif;
            font-size: 14px;
            font-weight: 600;
            letter-spacing: 2px;
            cursor: pointer;
            margin-top: 20px;
            transition: all 0.3s ease;
        }

        .disclaimer-modal .accept-btn:hover {
            background: #fff;
            box-shadow: 0 0 20px rgba(0, 212, 255, 0.5);
        }

        .disclaimer-hidden {
            display: none !important;
        }
    </style>
</head>
<body>
    <!-- Disclaimer Modal -->
    <div class="disclaimer-overlay" id="disclaimerModal">
        <div class="disclaimer-modal">
            <div class="warning-icon">⚠️</div>
            <h2>DISCLAIMER</h2>
            <p>
                <strong>INTERCEPT</strong> is a signal intelligence tool designed for <strong>educational purposes only</strong>.
            </p>
            <p>By using this software, you acknowledge and agree that:</p>
            <ul>
                <li>This tool is intended for use by <strong>cyber security professionals</strong> and researchers only</li>
                <li>You will only use this software in a <strong>controlled environment</strong> with proper authorization</li>
                <li>Intercepting communications without consent may be <strong>illegal</strong> in your jurisdiction</li>
                <li>You are solely responsible for ensuring compliance with all applicable laws and regulations</li>
                <li>The developers assume no liability for misuse of this software</li>
            </ul>
            <p style="color: var(--accent-red); font-weight: bold;">
                Only proceed if you understand and accept these terms.
            </p>
            <div style="display: flex; gap: 15px; justify-content: center; margin-top: 20px;">
                <button class="accept-btn" onclick="acceptDisclaimer()">I UNDERSTAND & ACCEPT</button>
                <button class="accept-btn" onclick="declineDisclaimer()" style="background: transparent; border: 1px solid var(--accent-red); color: var(--accent-red);">DECLINE</button>
            </div>
        </div>
    </div>

    <!-- Rejection Page -->
    <div class="disclaimer-overlay disclaimer-hidden" id="rejectionPage">
        <div class="disclaimer-modal" style="max-width: 600px;">
            <pre style="color: var(--accent-red); font-size: 9px; line-height: 1.1; margin-bottom: 20px; text-align: center;">
 █████╗  ██████╗ ██████╗███████╗███████╗███████╗
██╔══██╗██╔════╝██╔════╝██╔════╝██╔════╝██╔════╝
███████║██║     ██║     █████╗  ███████╗███████╗
██╔══██║██║     ██║     ██╔══╝  ╚════██║╚════██║
██║  ██║╚██████╗╚██████╗███████╗███████║███████║
╚═╝  ╚═╝ ╚═════╝ ╚═════╝╚══════╝╚══════╝╚══════╝
██████╗ ███████╗███╗   ██╗██╗███████╗██████╗
██╔══██╗██╔════╝████╗  ██║██║██╔════╝██╔══██╗
██║  ██║█████╗  ██╔██╗ ██║██║█████╗  ██║  ██║
██║  ██║██╔══╝  ██║╚██╗██║██║██╔══╝  ██║  ██║
██████╔╝███████╗██║ ╚████║██║███████╗██████╔╝
╚═════╝ ╚══════╝╚═╝  ╚═══╝╚═╝╚══════╝╚═════╝</pre>
            <div style="margin: 25px 0; padding: 15px; background: #0a0a0a; border-left: 3px solid var(--accent-red);">
                <p style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #888; text-align: left; margin: 0;">
                    <span style="color: var(--accent-red);">root@intercepted:</span><span style="color: var(--accent-cyan);">~#</span> sudo access --grant-permission<br>
                    <span style="color: #666;">[sudo] password for user: ********</span><br>
                    <span style="color: var(--accent-red);">Error:</span> User is not in the sudoers file.<br>
                    <span style="color: var(--accent-orange);">This incident will be reported.</span>
                </p>
            </div>
            <p style="color: #666; font-size: 11px; text-align: center;">
                "In a world of locked doors, the man with the key is king.<br>
                And you, my friend, just threw away the key."
            </p>
            <button class="accept-btn" onclick="location.reload()" style="margin-top: 20px; background: transparent; border: 1px solid var(--accent-cyan); color: var(--accent-cyan);">
                TRY AGAIN
            </button>
        </div>
    </div>
    <header>
        <div class="logo">
            <svg width="50" height="50" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                <!-- Outer hexagon -->
                <path d="M50 5 L90 27.5 L90 72.5 L50 95 L10 72.5 L10 27.5 Z" stroke="#00d4ff" stroke-width="2" fill="none"/>
                <!-- Inner signal waves -->
                <path d="M30 50 Q40 35, 50 50 Q60 65, 70 50" stroke="#00d4ff" stroke-width="2.5" fill="none" stroke-linecap="round"/>
                <path d="M35 50 Q42 40, 50 50 Q58 60, 65 50" stroke="#00ff88" stroke-width="2" fill="none" stroke-linecap="round"/>
                <path d="M40 50 Q45 45, 50 50 Q55 55, 60 50" stroke="#ffffff" stroke-width="1.5" fill="none" stroke-linecap="round"/>
                <!-- Center dot -->
                <circle cx="50" cy="50" r="3" fill="#00d4ff"/>
                <!-- Corner accents -->
                <path d="M50 12 L55 17 L50 17 Z" fill="#00d4ff"/>
                <path d="M50 88 L45 83 L50 83 Z" fill="#00d4ff"/>
            </svg>
        </div>
        <h1>INTERCEPT</h1>
        <p>Signal Intelligence // by smittix</p>
    </header>

    <div class="container">
        <div class="main-content">
            <div class="sidebar">
                <!-- Mode Tabs -->
                <div class="mode-tabs">
                    <button class="mode-tab active" onclick="switchMode('pager')">Pager</button>
                    <button class="mode-tab" onclick="switchMode('sensor')">433MHz</button>
                    <button class="mode-tab" onclick="switchMode('wifi')">WiFi</button>
                    <button class="mode-tab" onclick="switchMode('bluetooth')">BT</button>
                </div>

                <div class="section" id="rtlDeviceSection">
                    <h3>RTL-SDR Device</h3>
                    <div class="form-group">
                        <select id="deviceSelect">
                            {% if devices %}
                                {% for device in devices %}
                                <option value="{{ device.index }}">{{ device.index }}: {{ device.name }}</option>
                                {% endfor %}
                            {% else %}
                                <option value="0">No devices found</option>
                            {% endif %}
                        </select>
                    </div>
                    <button class="preset-btn" onclick="refreshDevices()" style="width: 100%;">
                        Refresh Devices
                    </button>
                    <div class="info-text" style="display: grid; grid-template-columns: auto auto; gap: 4px 8px; align-items: center;">
                        <span>rtl_fm:</span><span class="tool-status {{ 'ok' if tools.rtl_fm else 'missing' }}">{{ 'OK' if tools.rtl_fm else 'Missing' }}</span>
                        <span>multimon-ng:</span><span class="tool-status {{ 'ok' if tools.multimon else 'missing' }}">{{ 'OK' if tools.multimon else 'Missing' }}</span>
                        <span>rtl_433:</span><span class="tool-status {{ 'ok' if tools.rtl_433 else 'missing' }}">{{ 'OK' if tools.rtl_433 else 'Missing' }}</span>
                    </div>
                </div>

                <!-- PAGER MODE -->
                <div id="pagerMode" class="mode-content active">
                    <div class="section">
                        <h3>Frequency</h3>
                        <div class="form-group">
                            <label>Frequency (MHz)</label>
                            <input type="text" id="frequency" value="153.350" placeholder="e.g., 153.350">
                        </div>
                        <div class="preset-buttons" id="presetButtons">
                            <!-- Populated by JavaScript -->
                        </div>
                        <div style="margin-top: 8px; display: flex; gap: 5px;">
                            <input type="text" id="newPresetFreq" placeholder="New freq (MHz)" style="flex: 1; padding: 6px; background: #0f3460; border: 1px solid #1a1a2e; color: #fff; border-radius: 4px; font-size: 12px;">
                            <button class="preset-btn" onclick="addPreset()" style="background: #2ecc71;">Add</button>
                        </div>
                        <div style="margin-top: 5px;">
                            <button class="preset-btn" onclick="resetPresets()" style="font-size: 11px;">Reset to Defaults</button>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Protocols</h3>
                        <div class="checkbox-group">
                            <label><input type="checkbox" id="proto_pocsag512" checked> POCSAG-512</label>
                            <label><input type="checkbox" id="proto_pocsag1200" checked> POCSAG-1200</label>
                            <label><input type="checkbox" id="proto_pocsag2400" checked> POCSAG-2400</label>
                            <label><input type="checkbox" id="proto_flex" checked> FLEX</label>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Settings</h3>
                        <div class="form-group">
                            <label>Gain (dB, 0 = auto)</label>
                            <input type="text" id="gain" value="0" placeholder="0-49 or 0 for auto">
                        </div>
                        <div class="form-group">
                            <label>Squelch Level</label>
                            <input type="text" id="squelch" value="0" placeholder="0 = off">
                        </div>
                        <div class="form-group">
                            <label>PPM Correction</label>
                            <input type="text" id="ppm" value="0" placeholder="Frequency correction">
                        </div>
                    </div>

                    <div class="section">
                        <h3>Logging</h3>
                        <div class="checkbox-group" style="margin-bottom: 15px;">
                            <label>
                                <input type="checkbox" id="loggingEnabled" onchange="toggleLogging()">
                                Enable Logging
                            </label>
                        </div>
                        <div class="form-group">
                            <label>Log file path</label>
                            <input type="text" id="logFilePath" value="pager_messages.log" placeholder="pager_messages.log">
                        </div>
                    </div>

                    <button class="run-btn" id="startBtn" onclick="startDecoding()">
                        Start Decoding
                    </button>
                    <button class="stop-btn" id="stopBtn" onclick="stopDecoding()" style="display: none;">
                        Stop Decoding
                    </button>
                </div>

                <!-- 433MHz SENSOR MODE -->
                <div id="sensorMode" class="mode-content">
                    <div class="section">
                        <h3>Frequency</h3>
                        <div class="form-group">
                            <label>Frequency (MHz)</label>
                            <input type="text" id="sensorFrequency" value="433.92" placeholder="e.g., 433.92">
                        </div>
                        <div class="preset-buttons">
                            <button class="preset-btn" onclick="setSensorFreq('433.92')">433.92</button>
                            <button class="preset-btn" onclick="setSensorFreq('315.00')">315.00</button>
                            <button class="preset-btn" onclick="setSensorFreq('868.00')">868.00</button>
                            <button class="preset-btn" onclick="setSensorFreq('915.00')">915.00</button>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Settings</h3>
                        <div class="form-group">
                            <label>Gain (dB, 0 = auto)</label>
                            <input type="text" id="sensorGain" value="0" placeholder="0-49 or 0 for auto">
                        </div>
                        <div class="form-group">
                            <label>PPM Correction</label>
                            <input type="text" id="sensorPpm" value="0" placeholder="Frequency correction">
                        </div>
                    </div>

                    <div class="section">
                        <h3>Protocols</h3>
                        <div class="info-text" style="margin-bottom: 10px;">
                            rtl_433 auto-detects 200+ device protocols including weather stations, TPMS, doorbells, and more.
                        </div>
                        <div class="checkbox-group">
                            <label>
                                <input type="checkbox" id="sensorLogging" onchange="toggleSensorLogging()">
                                Enable Logging
                            </label>
                        </div>
                    </div>

                    <button class="run-btn" id="startSensorBtn" onclick="startSensorDecoding()">
                        Start Listening
                    </button>
                    <button class="stop-btn" id="stopSensorBtn" onclick="stopSensorDecoding()" style="display: none;">
                        Stop Listening
                    </button>
                </div>

                <!-- WiFi MODE -->
                <div id="wifiMode" class="mode-content">
                    <div class="section">
                        <h3>WiFi Interface</h3>
                        <div class="form-group">
                            <select id="wifiInterfaceSelect">
                                <option value="">Detecting interfaces...</option>
                            </select>
                        </div>
                        <button class="preset-btn" onclick="refreshWifiInterfaces()" style="width: 100%;">
                            Refresh Interfaces
                        </button>
                        <div class="info-text" style="margin-top: 8px; display: grid; grid-template-columns: auto auto; gap: 4px 8px; align-items: center;" id="wifiToolStatus">
                            <span>airmon-ng:</span><span class="tool-status missing">Checking...</span>
                            <span>airodump-ng:</span><span class="tool-status missing">Checking...</span>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Monitor Mode</h3>
                        <div style="display: flex; gap: 8px;">
                            <button class="preset-btn" id="monitorStartBtn" onclick="enableMonitorMode()" style="flex: 1; background: var(--accent-green); color: #000;">
                                Enable Monitor
                            </button>
                            <button class="preset-btn" id="monitorStopBtn" onclick="disableMonitorMode()" style="flex: 1; display: none;">
                                Disable Monitor
                            </button>
                        </div>
                        <div class="checkbox-group" style="margin-top: 8px;">
                            <label style="font-size: 10px;">
                                <input type="checkbox" id="killProcesses">
                                Kill interfering processes (may drop other connections)
                            </label>
                        </div>
                        <div id="monitorStatus" class="info-text" style="margin-top: 8px;">
                            Monitor mode: <span style="color: var(--accent-red);">Inactive</span>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Scan Settings</h3>
                        <div class="form-group">
                            <label>Band</label>
                            <select id="wifiBand">
                                <option value="abg">All (2.4 + 5 GHz)</option>
                                <option value="bg">2.4 GHz only</option>
                                <option value="a">5 GHz only</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Channel (empty = hop)</label>
                            <input type="text" id="wifiChannel" placeholder="e.g., 6 or 36">
                        </div>
                    </div>

                    <div class="section">
                        <h3>Proximity Alerts</h3>
                        <div class="info-text" style="margin-bottom: 8px;">
                            Alert when specific MAC addresses appear
                        </div>
                        <div class="form-group">
                            <input type="text" id="watchMacInput" placeholder="AA:BB:CC:DD:EE:FF">
                        </div>
                        <button class="preset-btn" onclick="addWatchMac()" style="width: 100%; margin-bottom: 8px;">
                            Add to Watch List
                        </button>
                        <div id="watchList" style="max-height: 80px; overflow-y: auto; font-size: 10px; color: var(--text-dim);"></div>
                    </div>

                    <div class="section">
                        <h3>Attack Options</h3>
                        <div class="info-text" style="color: var(--accent-red); margin-bottom: 10px;">
                            ⚠ Only use on authorized networks
                        </div>
                        <div class="form-group">
                            <label>Target BSSID</label>
                            <input type="text" id="targetBssid" placeholder="AA:BB:CC:DD:EE:FF">
                        </div>
                        <div class="form-group">
                            <label>Target Client (optional)</label>
                            <input type="text" id="targetClient" placeholder="FF:FF:FF:FF:FF:FF (broadcast)">
                        </div>
                        <div class="form-group">
                            <label>Deauth Count</label>
                            <input type="text" id="deauthCount" value="5" placeholder="5">
                        </div>
                        <button class="preset-btn" onclick="sendDeauth()" style="width: 100%; border-color: var(--accent-red); color: var(--accent-red);">
                            Send Deauth
                        </button>
                    </div>

                    <!-- Handshake Capture Status Panel -->
                    <div class="section" id="captureStatusPanel" style="display: none; border: 1px solid var(--accent-orange); border-radius: 4px; padding: 10px; background: rgba(255, 165, 0, 0.1);">
                        <h3 style="color: var(--accent-orange); margin: 0 0 8px 0;">🎯 Handshake Capture</h3>
                        <div style="font-size: 11px;">
                            <div style="margin-bottom: 4px;">
                                <span style="color: var(--text-dim);">Target:</span>
                                <span id="captureTargetBssid" style="font-family: monospace;">--</span>
                            </div>
                            <div style="margin-bottom: 4px;">
                                <span style="color: var(--text-dim);">Channel:</span>
                                <span id="captureTargetChannel">--</span>
                            </div>
                            <div style="margin-bottom: 4px;">
                                <span style="color: var(--text-dim);">File:</span>
                                <span id="captureFilePath" style="font-size: 9px; word-break: break-all;">--</span>
                            </div>
                            <div style="margin-bottom: 8px;">
                                <span style="color: var(--text-dim);">Status:</span>
                                <span id="captureStatus" style="font-weight: bold;">--</span>
                            </div>
                            <div style="display: flex; gap: 8px;">
                                <button class="preset-btn" onclick="checkCaptureStatus()" style="flex: 1; font-size: 10px; padding: 4px;">
                                    Check Status
                                </button>
                                <button class="preset-btn" onclick="stopHandshakeCapture()" style="flex: 1; font-size: 10px; padding: 4px; border-color: var(--accent-red); color: var(--accent-red);">
                                    Stop Capture
                                </button>
                            </div>
                        </div>
                    </div>

                    <button class="run-btn" id="startWifiBtn" onclick="startWifiScan()">
                        Start Scanning
                    </button>
                    <button class="stop-btn" id="stopWifiBtn" onclick="stopWifiScan()" style="display: none;">
                        Stop Scanning
                    </button>
                </div>

                <!-- BLUETOOTH MODE -->
                <div id="bluetoothMode" class="mode-content">
                    <div class="section">
                        <h3>Bluetooth Interface</h3>
                        <div class="form-group">
                            <select id="btInterfaceSelect">
                                <option value="">Detecting interfaces...</option>
                            </select>
                        </div>
                        <button class="preset-btn" onclick="refreshBtInterfaces()" style="width: 100%;">
                            Refresh Interfaces
                        </button>
                        <div class="info-text" style="margin-top: 8px; display: grid; grid-template-columns: auto auto; gap: 4px 8px; align-items: center;" id="btToolStatus">
                            <span>hcitool:</span><span class="tool-status missing">Checking...</span>
                            <span>bluetoothctl:</span><span class="tool-status missing">Checking...</span>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Scan Mode</h3>
                        <div class="checkbox-group" style="margin-bottom: 10px;">
                            <label><input type="radio" name="btScanMode" value="bluetoothctl" checked> bluetoothctl (Recommended)</label>
                            <label><input type="radio" name="btScanMode" value="hcitool"> hcitool (Legacy)</label>
                        </div>
                        <div class="form-group">
                            <label>Scan Duration (sec)</label>
                            <input type="text" id="btScanDuration" value="30" placeholder="30">
                        </div>
                        <div class="checkbox-group">
                            <label>
                                <input type="checkbox" id="btScanBLE" checked>
                                Scan BLE Devices
                            </label>
                            <label>
                                <input type="checkbox" id="btScanClassic" checked>
                                Scan Classic BT
                            </label>
                            <label>
                                <input type="checkbox" id="btDetectBeacons" checked>
                                Detect Trackers (AirTag/Tile)
                            </label>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Device Actions</h3>
                        <div class="form-group">
                            <label>Target MAC</label>
                            <input type="text" id="btTargetMac" placeholder="AA:BB:CC:DD:EE:FF">
                        </div>
                        <button class="preset-btn" onclick="btEnumServices()" style="width: 100%;">
                            Enumerate Services
                        </button>
                    </div>

                    <button class="run-btn" id="startBtBtn" onclick="startBtScan()">
                        Start Scanning
                    </button>
                    <button class="stop-btn" id="stopBtBtn" onclick="stopBtScan()" style="display: none;">
                        Stop Scanning
                    </button>
                    <button class="preset-btn" onclick="resetBtAdapter()" style="margin-top: 5px; width: 100%;">
                        Reset Adapter
                    </button>
                </div>

                <button class="preset-btn" onclick="killAll()" style="width: 100%; margin-top: 10px; border-color: #ff3366; color: #ff3366;">
                    Kill All Processes
                </button>
            </div>

            <div class="output-panel">
                <div class="output-header">
                    <h3>Decoded Messages</h3>
                    <div class="header-controls">
                        <div id="signalMeter" class="signal-meter" title="Signal Activity">
                            <div class="signal-bar"></div>
                            <div class="signal-bar"></div>
                            <div class="signal-bar"></div>
                            <div class="signal-bar"></div>
                            <div class="signal-bar"></div>
                        </div>
                        <div class="stats" id="pagerStats">
                            <div>MSG: <span id="msgCount">0</span></div>
                            <div>POCSAG: <span id="pocsagCount">0</span></div>
                            <div>FLEX: <span id="flexCount">0</span></div>
                        </div>
                        <div class="stats" id="sensorStats" style="display: none;">
                            <div>SENSORS: <span id="sensorCount">0</span></div>
                            <div>DEVICES: <span id="deviceCount">0</span></div>
                        </div>
                        <div class="stats" id="wifiStats" style="display: none;">
                            <div>APs: <span id="apCount">0</span></div>
                            <div>CLIENTS: <span id="clientCount">0</span></div>
                            <div>HANDSHAKES: <span id="handshakeCount" style="color: var(--accent-green);">0</span></div>
                            <div style="color: var(--accent-orange);">DRONES: <span id="droneCount">0</span></div>
                            <div style="color: var(--accent-red); cursor: pointer;" onclick="showRogueApDetails()" title="Click to see rogue AP details">ROGUE: <span id="rogueApCount">0</span></div>
                        </div>
                        <div class="stats" id="btStats" style="display: none;">
                            <div>DEVICES: <span id="btDeviceCount">0</span></div>
                            <div>BEACONS: <span id="btBeaconCount">0</span></div>
                        </div>
                    </div>
                </div>

                <!-- WiFi Visualizations (shown only in WiFi mode) -->
                <div class="wifi-visuals" id="wifiVisuals" style="display: none;">
                    <div class="wifi-visual-panel">
                        <h5>Network Radar</h5>
                        <div class="radar-container">
                            <canvas id="radarCanvas" width="150" height="150"></canvas>
                        </div>
                    </div>
                    <div class="wifi-visual-panel">
                        <h5>Channel Utilization (2.4 GHz)</h5>
                        <div class="channel-graph" id="channelGraph">
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">1</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">2</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">3</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">4</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">5</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">6</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">7</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">8</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">9</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">10</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">11</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">12</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">13</span></div>
                        </div>
                    </div>
                    <div class="wifi-visual-panel">
                        <h5>Channel Utilization (5 GHz)</h5>
                        <div class="channel-graph" id="channelGraph5g" style="font-size: 7px;">
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">36</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">40</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">44</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">48</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">52</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">56</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">60</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">64</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">100</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">149</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">153</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">157</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">161</span></div>
                            <div class="channel-bar-wrapper"><div class="channel-bar" style="height: 2px;"></div><span class="channel-label">165</span></div>
                        </div>
                    </div>
                    <div class="wifi-visual-panel">
                        <h5>Security Overview</h5>
                        <div class="security-container">
                            <div class="security-donut">
                                <canvas id="securityCanvas" width="80" height="80"></canvas>
                            </div>
                            <div class="security-legend">
                                <div class="security-legend-item"><div class="security-legend-dot wpa3"></div>WPA3: <span id="wpa3Count">0</span></div>
                                <div class="security-legend-item"><div class="security-legend-dot wpa2"></div>WPA2: <span id="wpa2Count">0</span></div>
                                <div class="security-legend-item"><div class="security-legend-dot wep"></div>WEP: <span id="wepCount">0</span></div>
                                <div class="security-legend-item"><div class="security-legend-dot open"></div>Open: <span id="openCount">0</span></div>
                            </div>
                        </div>
                    </div>
                    <div class="wifi-visual-panel">
                        <h5>Target Signal</h5>
                        <div class="signal-strength-display">
                            <div class="target-ssid" id="targetSsid">No target selected</div>
                            <div class="signal-value" id="signalValue">-- dBm</div>
                            <div class="signal-bars-large">
                                <div class="signal-bar-large"></div>
                                <div class="signal-bar-large"></div>
                                <div class="signal-bar-large"></div>
                                <div class="signal-bar-large"></div>
                                <div class="signal-bar-large"></div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Bluetooth Visualizations -->
                <div class="wifi-visuals" id="btVisuals" style="display: none;">
                    <div class="wifi-visual-panel">
                        <h5>Bluetooth Proximity Radar</h5>
                        <div class="radar-container">
                            <canvas id="btRadarCanvas" width="150" height="150"></canvas>
                        </div>
                    </div>
                </div>

                <!-- Device Intelligence Dashboard (above waterfall for prominence) -->
                <div class="recon-panel" id="reconPanel">
                    <div class="recon-header" onclick="toggleReconCollapse()" style="cursor: pointer;">
                        <h4><span id="reconCollapseIcon">▼</span> Device Intelligence</h4>
                        <div class="recon-stats">
                            <div>TRACKED: <span id="trackedCount">0</span></div>
                            <div>NEW: <span id="newDeviceCount">0</span></div>
                            <div>ANOMALIES: <span id="anomalyCount">0</span></div>
                        </div>
                    </div>
                    <div class="recon-content" id="reconContent">
                        <div style="color: #444; text-align: center; padding: 20px; font-size: 11px;">
                            Device intelligence data will appear here as signals are intercepted.
                        </div>
                    </div>
                </div>

                <div class="waterfall-container">
                    <canvas id="waterfallCanvas" width="800" height="60"></canvas>
                </div>

                <div class="output-content" id="output">
                    <div class="placeholder" style="color: #888; text-align: center; padding: 50px;">
                        Configure settings and click "Start Decoding" to begin.
                    </div>
                </div>

                <div class="status-bar">
                    <div class="status-indicator">
                        <div class="status-dot" id="statusDot"></div>
                        <span id="statusText">Idle</span>
                    </div>
                    <div class="status-controls">
                        <button id="reconBtn" class="recon-toggle" onclick="toggleRecon()">RECON</button>
                        <button id="muteBtn" class="control-btn" onclick="toggleMute()">🔊 MUTE</button>
                        <button id="autoScrollBtn" class="control-btn" onclick="toggleAutoScroll()">⬇ AUTO-SCROLL ON</button>
                        <button class="control-btn" onclick="exportCSV()">📄 CSV</button>
                        <button class="control-btn" onclick="exportJSON()">📋 JSON</button>
                        <button class="control-btn" onclick="exportDeviceDB()" title="Export Device Intelligence">🔍 INTEL</button>
                        <button class="clear-btn" onclick="clearMessages()">Clear</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Disclaimer handling
        function checkDisclaimer() {
            const accepted = localStorage.getItem('disclaimerAccepted');
            if (accepted === 'true') {
                document.getElementById('disclaimerModal').classList.add('disclaimer-hidden');
            }
        }

        function acceptDisclaimer() {
            localStorage.setItem('disclaimerAccepted', 'true');
            document.getElementById('disclaimerModal').classList.add('disclaimer-hidden');
        }

        function declineDisclaimer() {
            document.getElementById('disclaimerModal').classList.add('disclaimer-hidden');
            document.getElementById('rejectionPage').classList.remove('disclaimer-hidden');
        }

        // Check disclaimer on load
        checkDisclaimer();

        let eventSource = null;
        let isRunning = false;
        let isSensorRunning = false;
        let currentMode = 'pager';
        let msgCount = 0;
        let pocsagCount = 0;
        let flexCount = 0;
        let sensorCount = 0;
        let deviceList = {{ devices | tojson | safe }};

        // Mode switching
        function switchMode(mode) {
            // Stop any running scans when switching modes
            if (isRunning) stopDecoding();
            if (isSensorRunning) stopSensorDecoding();
            if (isWifiRunning) stopWifiScan();
            if (isBtRunning) stopBtScan();

            currentMode = mode;
            document.querySelectorAll('.mode-tab').forEach(tab => {
                const tabText = tab.textContent.toLowerCase();
                const isActive = (mode === 'pager' && tabText.includes('pager')) ||
                                 (mode === 'sensor' && tabText.includes('433')) ||
                                 (mode === 'wifi' && tabText.includes('wifi')) ||
                                 (mode === 'bluetooth' && tabText === 'bt');
                tab.classList.toggle('active', isActive);
            });
            document.getElementById('pagerMode').classList.toggle('active', mode === 'pager');
            document.getElementById('sensorMode').classList.toggle('active', mode === 'sensor');
            document.getElementById('wifiMode').classList.toggle('active', mode === 'wifi');
            document.getElementById('bluetoothMode').classList.toggle('active', mode === 'bluetooth');
            document.getElementById('pagerStats').style.display = mode === 'pager' ? 'flex' : 'none';
            document.getElementById('sensorStats').style.display = mode === 'sensor' ? 'flex' : 'none';
            document.getElementById('wifiStats').style.display = mode === 'wifi' ? 'flex' : 'none';
            document.getElementById('btStats').style.display = mode === 'bluetooth' ? 'flex' : 'none';
            document.getElementById('wifiVisuals').style.display = mode === 'wifi' ? 'grid' : 'none';
            document.getElementById('btVisuals').style.display = mode === 'bluetooth' ? 'grid' : 'none';

            // Show RTL-SDR device section only for modes that use it (pager and sensor/433MHz)
            document.getElementById('rtlDeviceSection').style.display = (mode === 'pager' || mode === 'sensor') ? 'block' : 'none';

            // Load interfaces when switching modes
            if (mode === 'wifi') {
                refreshWifiInterfaces();
                initRadar();
                initWatchList();
            } else if (mode === 'bluetooth') {
                refreshBtInterfaces();
                initBtRadar();
            }
        }

        // Track unique sensor devices
        let uniqueDevices = new Set();

        // Sensor frequency
        function setSensorFreq(freq) {
            document.getElementById('sensorFrequency').value = freq;
            if (isSensorRunning) {
                fetch('/stop_sensor', {method: 'POST'})
                    .then(() => setTimeout(() => startSensorDecoding(), 500));
            }
        }

        // Start sensor decoding
        function startSensorDecoding() {
            const freq = document.getElementById('sensorFrequency').value;
            const gain = document.getElementById('sensorGain').value;
            const ppm = document.getElementById('sensorPpm').value;
            const device = getSelectedDevice();

            const config = {
                frequency: freq,
                gain: gain,
                ppm: ppm,
                device: device
            };

            fetch('/start_sensor', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(config)
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'started') {
                      setSensorRunning(true);
                      startSensorStream();
                  } else {
                      alert('Error: ' + data.message);
                  }
              });
        }

        // Stop sensor decoding
        function stopSensorDecoding() {
            fetch('/stop_sensor', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    setSensorRunning(false);
                    if (eventSource) {
                        eventSource.close();
                        eventSource = null;
                    }
                });
        }

        function setSensorRunning(running) {
            isSensorRunning = running;
            document.getElementById('statusDot').classList.toggle('running', running);
            document.getElementById('statusText').textContent = running ? 'Listening...' : 'Idle';
            document.getElementById('startSensorBtn').style.display = running ? 'none' : 'block';
            document.getElementById('stopSensorBtn').style.display = running ? 'block' : 'none';
        }

        function startSensorStream() {
            if (eventSource) {
                eventSource.close();
            }

            eventSource = new EventSource('/stream_sensor');

            eventSource.onopen = function() {
                showInfo('Sensor stream connected...');
            };

            eventSource.onmessage = function(e) {
                const data = JSON.parse(e.data);
                if (data.type === 'sensor') {
                    addSensorReading(data);
                } else if (data.type === 'status') {
                    if (data.text === 'stopped') {
                        setSensorRunning(false);
                    }
                } else if (data.type === 'info' || data.type === 'raw') {
                    showInfo(data.text);
                }
            };

            eventSource.onerror = function(e) {
                console.error('Sensor stream error');
            };
        }

        function addSensorReading(data) {
            const output = document.getElementById('output');
            const placeholder = output.querySelector('.placeholder');
            if (placeholder) placeholder.remove();

            // Store for export
            allMessages.push(data);
            playAlert();
            pulseSignal();
            addWaterfallPoint(Date.now(), 0.8);

            sensorCount++;
            document.getElementById('sensorCount').textContent = sensorCount;

            // Track unique devices by model + id
            const deviceKey = (data.model || 'Unknown') + '_' + (data.id || data.channel || '0');
            if (!uniqueDevices.has(deviceKey)) {
                uniqueDevices.add(deviceKey);
                document.getElementById('deviceCount').textContent = uniqueDevices.size;
            }

            const card = document.createElement('div');
            card.className = 'sensor-card';

            let dataItems = '';
            const skipKeys = ['type', 'time', 'model', 'raw'];
            for (const [key, value] of Object.entries(data)) {
                if (!skipKeys.includes(key) && value !== null && value !== undefined) {
                    const label = key.replace(/_/g, ' ');
                    let displayValue = value;
                    if (key === 'temperature_C') displayValue = value + ' °C';
                    else if (key === 'temperature_F') displayValue = value + ' °F';
                    else if (key === 'humidity') displayValue = value + ' %';
                    else if (key === 'pressure_hPa') displayValue = value + ' hPa';
                    else if (key === 'wind_avg_km_h') displayValue = value + ' km/h';
                    else if (key === 'rain_mm') displayValue = value + ' mm';
                    else if (key === 'battery_ok') displayValue = value ? 'OK' : 'Low';

                    dataItems += '<div class="data-item"><div class="data-label">' + label + '</div><div class="data-value">' + displayValue + '</div></div>';
                }
            }

            const relTime = data.time ? getRelativeTime(data.time.split(' ')[1] || data.time) : 'now';

            card.innerHTML =
                '<div class="header" style="display: flex; justify-content: space-between; margin-bottom: 8px;">' +
                    '<span class="device-name">' + (data.model || 'Unknown Device') + '</span>' +
                    '<span class="msg-time" data-timestamp="' + (data.time || '') + '" style="color: #444; font-size: 10px;">' + relTime + '</span>' +
                '</div>' +
                '<div class="sensor-data">' + dataItems + '</div>';

            output.insertBefore(card, output.firstChild);

            if (autoScroll) output.scrollTop = 0;
            while (output.children.length > 100) {
                output.removeChild(output.lastChild);
            }
        }

        function toggleSensorLogging() {
            const enabled = document.getElementById('sensorLogging').checked;
            fetch('/logging', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled: enabled, log_file: 'sensor_data.log'})
            });
        }

        // Audio alert settings
        let audioMuted = localStorage.getItem('audioMuted') === 'true';
        let audioContext = null;

        function initAudio() {
            if (!audioContext) {
                audioContext = new (window.AudioContext || window.webkitAudioContext)();
            }
        }

        function playAlert() {
            if (audioMuted || !audioContext) return;
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();
            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);
            oscillator.frequency.value = 880;
            oscillator.type = 'sine';
            gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.2);
            oscillator.start(audioContext.currentTime);
            oscillator.stop(audioContext.currentTime + 0.2);
        }

        function toggleMute() {
            audioMuted = !audioMuted;
            localStorage.setItem('audioMuted', audioMuted);
            updateMuteButton();
        }

        function updateMuteButton() {
            const btn = document.getElementById('muteBtn');
            if (btn) {
                btn.innerHTML = audioMuted ? '🔇 UNMUTE' : '🔊 MUTE';
                btn.classList.toggle('muted', audioMuted);
            }
        }

        // Message storage for export
        let allMessages = [];

        function exportCSV() {
            if (allMessages.length === 0) {
                alert('No messages to export');
                return;
            }
            const headers = ['Timestamp', 'Protocol', 'Address', 'Function', 'Type', 'Message'];
            const csv = [headers.join(',')];
            allMessages.forEach(msg => {
                const row = [
                    msg.timestamp || '',
                    msg.protocol || '',
                    msg.address || '',
                    msg.function || '',
                    msg.msg_type || '',
                    '"' + (msg.message || '').replace(/"/g, '""') + '"'
                ];
                csv.push(row.join(','));
            });
            downloadFile(csv.join('\\n'), 'intercept_messages.csv', 'text/csv');
        }

        function exportJSON() {
            if (allMessages.length === 0) {
                alert('No messages to export');
                return;
            }
            downloadFile(JSON.stringify(allMessages, null, 2), 'intercept_messages.json', 'application/json');
        }

        function downloadFile(content, filename, type) {
            const blob = new Blob([content], { type });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            a.click();
            URL.revokeObjectURL(url);
        }

        // Auto-scroll setting
        let autoScroll = localStorage.getItem('autoScroll') !== 'false';

        function toggleAutoScroll() {
            autoScroll = !autoScroll;
            localStorage.setItem('autoScroll', autoScroll);
            updateAutoScrollButton();
        }

        function updateAutoScrollButton() {
            const btn = document.getElementById('autoScrollBtn');
            if (btn) {
                btn.innerHTML = autoScroll ? '⬇ AUTO-SCROLL ON' : '⬇ AUTO-SCROLL OFF';
                btn.classList.toggle('active', autoScroll);
            }
        }

        // Signal activity meter
        let signalActivity = 0;
        let lastMessageTime = 0;

        function updateSignalMeter() {
            const now = Date.now();
            const timeSinceLastMsg = now - lastMessageTime;

            // Decay signal activity over time
            if (timeSinceLastMsg > 1000) {
                signalActivity = Math.max(0, signalActivity - 0.05);
            }

            const meter = document.getElementById('signalMeter');
            const bars = meter?.querySelectorAll('.signal-bar');
            if (bars) {
                const activeBars = Math.ceil(signalActivity * bars.length);
                bars.forEach((bar, i) => {
                    bar.classList.toggle('active', i < activeBars);
                });
            }
        }

        function pulseSignal() {
            signalActivity = Math.min(1, signalActivity + 0.4);
            lastMessageTime = Date.now();

            // Flash waterfall canvas
            const canvas = document.getElementById('waterfallCanvas');
            if (canvas) {
                canvas.classList.add('active');
                setTimeout(() => canvas.classList.remove('active'), 500);
            }
        }

        // Waterfall display
        const waterfallData = [];
        const maxWaterfallRows = 50;

        function addWaterfallPoint(timestamp, intensity) {
            waterfallData.push({ time: timestamp, intensity });
            if (waterfallData.length > maxWaterfallRows * 100) {
                waterfallData.shift();
            }
            renderWaterfall();
        }

        function renderWaterfall() {
            const canvas = document.getElementById('waterfallCanvas');
            if (!canvas) return;
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            const width = canvas.width;
            const height = canvas.height;

            // Shift existing image down
            const imageData = ctx.getImageData(0, 0, width, height - 2);
            ctx.putImageData(imageData, 0, 2);

            // Draw new row at top
            ctx.fillStyle = '#000';
            ctx.fillRect(0, 0, width, 2);

            // Add activity markers
            const now = Date.now();
            const recentData = waterfallData.filter(d => now - d.time < 100);
            recentData.forEach(d => {
                const x = Math.random() * width;
                const hue = 180 + (d.intensity * 60); // cyan to green
                ctx.fillStyle = `hsla(${hue}, 100%, 50%, ${d.intensity})`;
                ctx.fillRect(x - 2, 0, 4, 2);
            });
        }

        // Relative timestamps
        function getRelativeTime(timestamp) {
            if (!timestamp) return '';
            const now = new Date();
            const parts = timestamp.split(':');
            const msgTime = new Date();
            msgTime.setHours(parseInt(parts[0]), parseInt(parts[1]), parseInt(parts[2]));

            const diff = Math.floor((now - msgTime) / 1000);
            if (diff < 5) return 'just now';
            if (diff < 60) return diff + 's ago';
            if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
            return timestamp;
        }

        function updateRelativeTimes() {
            document.querySelectorAll('.msg-time').forEach(el => {
                const ts = el.dataset.timestamp;
                if (ts) el.textContent = getRelativeTime(ts);
            });
        }

        // Update timers
        setInterval(updateSignalMeter, 100);
        setInterval(updateRelativeTimes, 10000);

        // Default presets (UK frequencies)
        const defaultPresets = ['153.350', '153.025'];

        // Load presets from localStorage or use defaults
        function loadPresets() {
            const saved = localStorage.getItem('pagerPresets');
            return saved ? JSON.parse(saved) : [...defaultPresets];
        }

        function savePresets(presets) {
            localStorage.setItem('pagerPresets', JSON.stringify(presets));
        }

        function renderPresets() {
            const presets = loadPresets();
            const container = document.getElementById('presetButtons');
            container.innerHTML = presets.map(freq =>
                `<button class="preset-btn" onclick="setFreq('${freq}')" oncontextmenu="removePreset('${freq}'); return false;" title="Right-click to remove">${freq}</button>`
            ).join('');
        }

        function addPreset() {
            const input = document.getElementById('newPresetFreq');
            const freq = input.value.trim();
            if (!freq || isNaN(parseFloat(freq))) {
                alert('Please enter a valid frequency');
                return;
            }
            const presets = loadPresets();
            if (!presets.includes(freq)) {
                presets.push(freq);
                savePresets(presets);
                renderPresets();
            }
            input.value = '';
        }

        function removePreset(freq) {
            if (confirm('Remove preset ' + freq + ' MHz?')) {
                let presets = loadPresets();
                presets = presets.filter(p => p !== freq);
                savePresets(presets);
                renderPresets();
            }
        }

        function resetPresets() {
            if (confirm('Reset to default presets?')) {
                savePresets([...defaultPresets]);
                renderPresets();
            }
        }

        // Initialize presets on load
        renderPresets();

        // Initialize button states on load
        updateMuteButton();
        updateAutoScrollButton();

        // Initialize audio context on first user interaction (required by browsers)
        document.addEventListener('click', function initAudioOnClick() {
            initAudio();
            document.removeEventListener('click', initAudioOnClick);
        }, { once: true });

        function setFreq(freq) {
            document.getElementById('frequency').value = freq;
            // Auto-restart decoder with new frequency if currently running
            if (isRunning) {
                fetch('/stop', {method: 'POST'})
                    .then(() => {
                        setTimeout(() => startDecoding(), 500);
                    });
            }
        }

        function refreshDevices() {
            fetch('/devices')
                .then(r => r.json())
                .then(devices => {
                    deviceList = devices;
                    const select = document.getElementById('deviceSelect');
                    if (devices.length === 0) {
                        select.innerHTML = '<option value="0">No devices found</option>';
                    } else {
                        select.innerHTML = devices.map(d =>
                            `<option value="${d.index}">${d.index}: ${d.name}</option>`
                        ).join('');
                    }
                });
        }

        function getSelectedDevice() {
            return document.getElementById('deviceSelect').value;
        }

        function getSelectedProtocols() {
            const protocols = [];
            if (document.getElementById('proto_pocsag512').checked) protocols.push('POCSAG512');
            if (document.getElementById('proto_pocsag1200').checked) protocols.push('POCSAG1200');
            if (document.getElementById('proto_pocsag2400').checked) protocols.push('POCSAG2400');
            if (document.getElementById('proto_flex').checked) protocols.push('FLEX');
            return protocols;
        }

        function startDecoding() {
            const freq = document.getElementById('frequency').value;
            const gain = document.getElementById('gain').value;
            const squelch = document.getElementById('squelch').value;
            const ppm = document.getElementById('ppm').value;
            const device = getSelectedDevice();
            const protocols = getSelectedProtocols();

            if (protocols.length === 0) {
                alert('Please select at least one protocol');
                return;
            }

            const config = {
                frequency: freq,
                gain: gain,
                squelch: squelch,
                ppm: ppm,
                device: device,
                protocols: protocols
            };

            fetch('/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(config)
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'started') {
                      setRunning(true);
                      startStream();
                  } else {
                      alert('Error: ' + data.message);
                  }
              })
              .catch(err => {
                  console.error('Start error:', err);
              });
        }

        function stopDecoding() {
            fetch('/stop', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    setRunning(false);
                    if (eventSource) {
                        eventSource.close();
                        eventSource = null;
                    }
                });
        }

        function killAll() {
            fetch('/killall', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    setRunning(false);
                    if (eventSource) {
                        eventSource.close();
                        eventSource = null;
                    }
                    showInfo('Killed all processes: ' + (data.processes.length ? data.processes.join(', ') : 'none running'));
                });
        }

        function checkStatus() {
            fetch('/status')
                .then(r => r.json())
                .then(data => {
                    if (data.running !== isRunning) {
                        setRunning(data.running);
                        if (data.running && !eventSource) {
                            startStream();
                        }
                    }
                });
        }

        // Periodic status check every 5 seconds
        setInterval(checkStatus, 5000);

        function toggleLogging() {
            const enabled = document.getElementById('loggingEnabled').checked;
            const logFile = document.getElementById('logFilePath').value;
            fetch('/logging', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled: enabled, log_file: logFile})
            }).then(r => r.json())
              .then(data => {
                  showInfo(data.logging ? 'Logging enabled: ' + data.log_file : 'Logging disabled');
              });
        }

        function setRunning(running) {
            isRunning = running;
            document.getElementById('statusDot').classList.toggle('running', running);
            document.getElementById('statusText').textContent = running ? 'Decoding...' : 'Idle';
            document.getElementById('startBtn').style.display = running ? 'none' : 'block';
            document.getElementById('stopBtn').style.display = running ? 'block' : 'none';
        }

        function startStream() {
            if (eventSource) {
                eventSource.close();
            }

            eventSource = new EventSource('/stream');

            eventSource.onopen = function() {
                showInfo('Stream connected...');
            };

            eventSource.onmessage = function(e) {
                const data = JSON.parse(e.data);

                if (data.type === 'message') {
                    addMessage(data);
                } else if (data.type === 'status') {
                    if (data.text === 'stopped') {
                        setRunning(false);
                    } else if (data.text === 'started') {
                        showInfo('Decoder started, waiting for signals...');
                    }
                } else if (data.type === 'info') {
                    showInfo(data.text);
                } else if (data.type === 'raw') {
                    showInfo(data.text);
                }
            };

            eventSource.onerror = function(e) {
                checkStatus();
            };
        }

        function addMessage(msg) {
            const output = document.getElementById('output');

            // Remove placeholder if present
            const placeholder = output.querySelector('.placeholder');
            if (placeholder) {
                placeholder.remove();
            }

            // Store message for export
            allMessages.push(msg);

            // Play audio alert
            playAlert();

            // Update signal meter
            pulseSignal();

            // Add to waterfall
            addWaterfallPoint(Date.now(), 0.8);

            msgCount++;
            document.getElementById('msgCount').textContent = msgCount;

            let protoClass = '';
            if (msg.protocol.includes('POCSAG')) {
                pocsagCount++;
                protoClass = 'pocsag';
                document.getElementById('pocsagCount').textContent = pocsagCount;
            } else if (msg.protocol.includes('FLEX')) {
                flexCount++;
                protoClass = 'flex';
                document.getElementById('flexCount').textContent = flexCount;
            }

            const isNumeric = /^[0-9\s\-\*\#U]+$/.test(msg.message);
            const relativeTime = getRelativeTime(msg.timestamp);

            const msgEl = document.createElement('div');
            msgEl.className = 'message ' + protoClass;
            msgEl.innerHTML = `
                <div class="header">
                    <span class="protocol">${msg.protocol}</span>
                    <span class="msg-time" data-timestamp="${msg.timestamp}" title="${msg.timestamp}">${relativeTime}</span>
                </div>
                <div class="address">Address: ${msg.address}${msg.function ? ' | Func: ' + msg.function : ''}</div>
                <div class="content ${isNumeric ? 'numeric' : ''}">${escapeHtml(msg.message)}</div>
            `;

            output.insertBefore(msgEl, output.firstChild);

            // Auto-scroll to top (newest messages)
            if (autoScroll) {
                output.scrollTop = 0;
            }

            // Limit messages displayed
            while (output.children.length > 100) {
                output.removeChild(output.lastChild);
            }
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function escapeAttr(text) {
            // Escape for use in HTML attributes (especially onclick handlers)
            if (text === null || text === undefined) return '';
            var s = String(text);
            s = s.replace(/&/g, '&amp;');
            s = s.replace(/'/g, '&#39;');
            s = s.replace(/"/g, '&quot;');
            s = s.replace(/</g, '&lt;');
            s = s.replace(/>/g, '&gt;');
            return s;
        }

        function isValidMac(mac) {
            // Validate MAC address format (XX:XX:XX:XX:XX:XX)
            return /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(mac);
        }

        function isValidChannel(ch) {
            // Validate WiFi channel (1-200 covers all bands)
            const num = parseInt(ch, 10);
            return !isNaN(num) && num >= 1 && num <= 200;
        }

        function showInfo(text) {
            const output = document.getElementById('output');

            // Clear placeholder only (has the 'placeholder' class)
            const placeholder = output.querySelector('.placeholder');
            if (placeholder) {
                placeholder.remove();
            }

            const infoEl = document.createElement('div');
            infoEl.className = 'info-msg';
            infoEl.style.cssText = 'padding: 12px 15px; margin-bottom: 8px; background: #0a0a0a; border: 1px solid #1a1a1a; border-left: 2px solid #00d4ff; font-family: "JetBrains Mono", monospace; font-size: 11px; color: #888; word-break: break-all;';
            infoEl.textContent = text;
            output.insertBefore(infoEl, output.firstChild);
        }

        function showError(text) {
            const output = document.getElementById('output');

            // Clear placeholder only (has the 'placeholder' class)
            const placeholder = output.querySelector('.placeholder');
            if (placeholder) {
                placeholder.remove();
            }

            const errorEl = document.createElement('div');
            errorEl.className = 'error-msg';
            errorEl.style.cssText = 'padding: 12px 15px; margin-bottom: 8px; background: #1a0a0a; border: 1px solid #2a1a1a; border-left: 2px solid #ff3366; font-family: "JetBrains Mono", monospace; font-size: 11px; color: #ff6688; word-break: break-all;';
            errorEl.textContent = '⚠ ' + text;
            output.insertBefore(errorEl, output.firstChild);
        }

        function clearMessages() {
            document.getElementById('output').innerHTML = `
                <div class="placeholder" style="color: #888; text-align: center; padding: 50px;">
                    Messages cleared. ${isRunning || isSensorRunning ? 'Waiting for new messages...' : 'Start decoding to receive messages.'}
                </div>
            `;
            msgCount = 0;
            pocsagCount = 0;
            flexCount = 0;
            sensorCount = 0;
            uniqueDevices.clear();
            document.getElementById('msgCount').textContent = '0';
            document.getElementById('pocsagCount').textContent = '0';
            document.getElementById('flexCount').textContent = '0';
            document.getElementById('sensorCount').textContent = '0';
            document.getElementById('deviceCount').textContent = '0';

            // Reset recon data
            deviceDatabase.clear();
            newDeviceAlerts = 0;
            anomalyAlerts = 0;
            document.getElementById('trackedCount').textContent = '0';
            document.getElementById('newDeviceCount').textContent = '0';
            document.getElementById('anomalyCount').textContent = '0';
            document.getElementById('reconContent').innerHTML = '<div style="color: #444; text-align: center; padding: 30px; font-size: 11px;">Device intelligence data will appear here as signals are intercepted.</div>';
        }

        // ============== DEVICE INTELLIGENCE & RECONNAISSANCE ==============

        // Device tracking database
        const deviceDatabase = new Map(); // key: deviceId, value: device profile
        // Default to true if not set, so device intelligence works by default
        let reconEnabled = localStorage.getItem('reconEnabled') !== 'false';
        let newDeviceAlerts = 0;
        let anomalyAlerts = 0;

        // Device profile structure
        function createDeviceProfile(deviceId, protocol, firstSeen) {
            return {
                id: deviceId,
                protocol: protocol,
                firstSeen: firstSeen,
                lastSeen: firstSeen,
                transmissionCount: 1,
                transmissions: [firstSeen], // timestamps of recent transmissions
                avgInterval: null, // average time between transmissions
                addresses: new Set(),
                models: new Set(),
                messages: [],
                isNew: true,
                anomalies: [],
                signalStrength: [],
                encrypted: null // null = unknown, true/false
            };
        }

        // Analyze transmission patterns for anomalies
        function analyzeTransmissions(profile) {
            const anomalies = [];
            const now = Date.now();

            // Need at least 3 transmissions to analyze patterns
            if (profile.transmissions.length < 3) {
                return anomalies;
            }

            // Calculate intervals between transmissions
            const intervals = [];
            for (let i = 1; i < profile.transmissions.length; i++) {
                intervals.push(profile.transmissions[i] - profile.transmissions[i-1]);
            }

            // Calculate average and standard deviation
            const avg = intervals.reduce((a, b) => a + b, 0) / intervals.length;
            profile.avgInterval = avg;

            const variance = intervals.reduce((a, b) => a + Math.pow(b - avg, 2), 0) / intervals.length;
            const stdDev = Math.sqrt(variance);

            // Check for burst transmission (sudden increase in frequency)
            const lastInterval = intervals[intervals.length - 1];
            if (avg > 0 && lastInterval < avg * 0.2) {
                anomalies.push({
                    type: 'burst',
                    severity: 'medium',
                    message: 'Burst transmission detected - interval ' + Math.round(lastInterval/1000) + 's vs avg ' + Math.round(avg/1000) + 's'
                });
            }

            // Check for silence break (device was quiet, now transmitting again)
            if (avg > 0 && lastInterval > avg * 5) {
                anomalies.push({
                    type: 'silence_break',
                    severity: 'low',
                    message: 'Device resumed after ' + Math.round(lastInterval/60000) + ' min silence'
                });
            }

            return anomalies;
        }

        // Check for encryption indicators
        function detectEncryption(message) {
            if (!message || message === '[No Message]' || message === '[Tone Only]') {
                return null; // Can't determine
            }

            // Check for high entropy (random-looking data)
            const printableRatio = (message.match(/[a-zA-Z0-9\s.,!?-]/g) || []).length / message.length;

            // Check for common encrypted patterns (hex strings, base64-like)
            const hexPattern = /^[0-9A-Fa-f\s]+$/;
            const hasNonPrintable = /[^\x20-\x7E]/.test(message);

            if (printableRatio > 0.8 && !hasNonPrintable) {
                return false; // Likely plaintext
            } else if (hexPattern.test(message.replace(/\s/g, '')) || hasNonPrintable) {
                return true; // Likely encrypted or encoded
            }

            return null; // Unknown
        }

        // Generate device fingerprint
        function generateDeviceId(data) {
            if (data.protocol && data.protocol.includes('POCSAG')) {
                return 'PAGER_' + (data.address || 'UNK');
            } else if (data.protocol === 'FLEX') {
                return 'FLEX_' + (data.address || 'UNK');
            } else if (data.protocol === 'WiFi-AP') {
                return 'WIFI_AP_' + (data.address || 'UNK').replace(/:/g, '');
            } else if (data.protocol === 'WiFi-Client') {
                return 'WIFI_CLIENT_' + (data.address || 'UNK').replace(/:/g, '');
            } else if (data.protocol === 'Bluetooth' || data.protocol === 'BLE') {
                return 'BT_' + (data.address || 'UNK').replace(/:/g, '');
            } else if (data.model) {
                // 433MHz sensor
                const id = data.id || data.channel || data.unit || '0';
                return 'SENSOR_' + data.model.replace(/\s+/g, '_') + '_' + id;
            }
            return 'UNKNOWN_' + Date.now();
        }

        // Track a device transmission
        function trackDevice(data) {
            const now = Date.now();
            const deviceId = generateDeviceId(data);
            const protocol = data.protocol || data.model || 'Unknown';

            let profile = deviceDatabase.get(deviceId);
            let isNewDevice = false;

            if (!profile) {
                // New device discovered
                profile = createDeviceProfile(deviceId, protocol, now);
                isNewDevice = true;
                newDeviceAlerts++;
                document.getElementById('newDeviceCount').textContent = newDeviceAlerts;
            } else {
                // Update existing profile
                profile.lastSeen = now;
                profile.transmissionCount++;
                profile.transmissions.push(now);
                profile.isNew = false;

                // Keep only last 100 transmissions for analysis
                if (profile.transmissions.length > 100) {
                    profile.transmissions = profile.transmissions.slice(-100);
                }
            }

            // Track addresses
            if (data.address) profile.addresses.add(data.address);
            if (data.model) profile.models.add(data.model);

            // Store recent messages (keep last 10)
            if (data.message) {
                profile.messages.unshift({
                    text: data.message,
                    time: now
                });
                if (profile.messages.length > 10) profile.messages.pop();

                // Detect encryption
                const encrypted = detectEncryption(data.message);
                if (encrypted !== null) profile.encrypted = encrypted;
            }

            // Analyze for anomalies
            const newAnomalies = analyzeTransmissions(profile);
            if (newAnomalies.length > 0) {
                profile.anomalies = profile.anomalies.concat(newAnomalies);
                anomalyAlerts += newAnomalies.length;
                document.getElementById('anomalyCount').textContent = anomalyAlerts;
            }

            deviceDatabase.set(deviceId, profile);
            document.getElementById('trackedCount').textContent = deviceDatabase.size;

            // Update recon display
            if (reconEnabled) {
                updateReconDisplay(deviceId, profile, isNewDevice, newAnomalies);
            }

            return { deviceId, profile, isNewDevice, anomalies: newAnomalies };
        }

        // Update reconnaissance display
        function updateReconDisplay(deviceId, profile, isNewDevice, anomalies) {
            const content = document.getElementById('reconContent');

            // Remove placeholder if present
            const placeholder = content.querySelector('div[style*="text-align: center"]');
            if (placeholder) placeholder.remove();

            // Check if device row already exists
            let row = document.getElementById('device_' + deviceId.replace(/[^a-zA-Z0-9]/g, '_'));

            if (!row) {
                // Create new row
                row = document.createElement('div');
                row.id = 'device_' + deviceId.replace(/[^a-zA-Z0-9]/g, '_');
                row.className = 'device-row' + (isNewDevice ? ' new-device' : '');
                content.insertBefore(row, content.firstChild);
            }

            // Determine protocol badge class
            let badgeClass = 'proto-unknown';
            if (profile.protocol.includes('POCSAG')) badgeClass = 'proto-pocsag';
            else if (profile.protocol === 'FLEX') badgeClass = 'proto-flex';
            else if (profile.protocol.includes('SENSOR') || profile.models.size > 0) badgeClass = 'proto-433';

            // Calculate transmission rate bar width
            const maxRate = 100; // Max expected transmissions
            const rateWidth = Math.min(100, (profile.transmissionCount / maxRate) * 100);

            // Determine timeline status
            const timeSinceLast = Date.now() - profile.lastSeen;
            let timelineDot = 'recent';
            if (timeSinceLast > 300000) timelineDot = 'old'; // > 5 min
            else if (timeSinceLast > 60000) timelineDot = 'stale'; // > 1 min

            // Build encryption indicator
            let encStatus = 'Unknown';
            let encClass = '';
            if (profile.encrypted === true) { encStatus = 'Encrypted'; encClass = 'encrypted'; }
            else if (profile.encrypted === false) { encStatus = 'Plaintext'; encClass = 'plaintext'; }

            // Format time
            const lastSeenStr = getRelativeTime(new Date(profile.lastSeen).toTimeString().split(' ')[0]);
            const firstSeenStr = new Date(profile.firstSeen).toLocaleTimeString();

            // Update row content
            row.className = 'device-row' + (isNewDevice ? ' new-device' : '') + (anomalies.length > 0 ? ' anomaly' : '');
            row.innerHTML = `
                <div class="device-info">
                    <div class="device-name-row">
                        <span class="timeline-dot ${timelineDot}"></span>
                        <span class="badge ${badgeClass}">${profile.protocol.substring(0, 8)}</span>
                        ${deviceId.substring(0, 30)}
                    </div>
                    <div class="device-id">
                        First: ${firstSeenStr} | Last: ${lastSeenStr} | TX: ${profile.transmissionCount}
                        ${profile.avgInterval ? ' | Interval: ' + Math.round(profile.avgInterval/1000) + 's' : ''}
                    </div>
                </div>
                <div class="device-meta ${encClass}">${encStatus}</div>
                <div>
                    <div class="transmission-bar">
                        <div class="transmission-bar-fill" style="width: ${rateWidth}%"></div>
                    </div>
                </div>
                <div class="device-meta">${Array.from(profile.addresses).slice(0, 2).join(', ')}</div>
            `;

            // Show anomaly alerts
            if (anomalies.length > 0) {
                anomalies.forEach(a => {
                    const alertEl = document.createElement('div');
                    alertEl.style.cssText = 'padding: 5px 15px; background: rgba(255,51,102,0.1); border-left: 2px solid var(--accent-red); font-size: 10px; color: var(--accent-red);';
                    alertEl.textContent = '⚠ ' + a.message;
                    row.appendChild(alertEl);
                });
            }

            // Limit displayed devices
            while (content.children.length > 50) {
                content.removeChild(content.lastChild);
            }
        }

        // Toggle recon panel visibility
        function toggleRecon() {
            reconEnabled = !reconEnabled;
            localStorage.setItem('reconEnabled', reconEnabled);
            document.getElementById('reconPanel').style.display = reconEnabled ? 'block' : 'none';
            document.getElementById('reconBtn').classList.toggle('active', reconEnabled);

            // Populate recon display if enabled and we have data
            if (reconEnabled && deviceDatabase.size > 0) {
                deviceDatabase.forEach((profile, deviceId) => {
                    updateReconDisplay(deviceId, profile, false, []);
                });
            }
        }

        // Initialize recon state
        if (reconEnabled) {
            document.getElementById('reconPanel').style.display = 'block';
            document.getElementById('reconBtn').classList.add('active');
        } else {
            document.getElementById('reconPanel').style.display = 'none';
        }

        // Hook into existing message handlers to track devices
        const originalAddMessage = addMessage;
        addMessage = function(msg) {
            originalAddMessage(msg);
            trackDevice(msg);
        };

        const originalAddSensorReading = addSensorReading;
        addSensorReading = function(data) {
            originalAddSensorReading(data);
            trackDevice(data);
        };

        // Export device database
        function exportDeviceDB() {
            const data = [];
            deviceDatabase.forEach((profile, id) => {
                data.push({
                    id: id,
                    protocol: profile.protocol,
                    firstSeen: new Date(profile.firstSeen).toISOString(),
                    lastSeen: new Date(profile.lastSeen).toISOString(),
                    transmissionCount: profile.transmissionCount,
                    avgIntervalSeconds: profile.avgInterval ? Math.round(profile.avgInterval / 1000) : null,
                    addresses: Array.from(profile.addresses),
                    models: Array.from(profile.models),
                    encrypted: profile.encrypted,
                    anomalyCount: profile.anomalies.length,
                    recentMessages: profile.messages.slice(0, 5).map(m => m.text)
                });
            });
            downloadFile(JSON.stringify(data, null, 2), 'intercept_device_intelligence.json', 'application/json');
        }

        // Toggle recon panel collapse
        function toggleReconCollapse() {
            const panel = document.getElementById('reconPanel');
            const icon = document.getElementById('reconCollapseIcon');
            panel.classList.toggle('collapsed');
            icon.textContent = panel.classList.contains('collapsed') ? '▶' : '▼';
        }

        // ============== WIFI RECONNAISSANCE ==============

        let wifiEventSource = null;
        let isWifiRunning = false;
        let monitorInterface = null;
        let wifiNetworks = {};
        let wifiClients = {};
        let apCount = 0;
        let clientCount = 0;
        let handshakeCount = 0;
        let rogueApCount = 0;
        let droneCount = 0;
        let detectedDrones = {};  // Track detected drones by BSSID
        let ssidToBssids = {};  // Track SSIDs to their BSSIDs for rogue AP detection
        let rogueApDetails = {};  // Store details about rogue APs: {ssid: [{bssid, signal, channel, firstSeen}]}
        let activeCapture = null;  // {bssid, channel, file, startTime, pollInterval}
        let watchMacs = JSON.parse(localStorage.getItem('watchMacs') || '[]');
        let alertedMacs = new Set();  // Prevent duplicate alerts per session

        // 5GHz channel mapping for the graph
        const channels5g = ['36', '40', '44', '48', '52', '56', '60', '64', '100', '149', '153', '157', '161', '165'];

        // Drone SSID patterns for detection
        const dronePatterns = [
            /^DJI[-_]/i, /Mavic/i, /Phantom/i, /^Spark[-_]/i, /^Mini[-_]/i, /^Air[-_]/i,
            /Inspire/i, /Matrice/i, /Avata/i, /^FPV[-_]/i, /Osmo/i, /RoboMaster/i, /Tello/i,
            /Parrot/i, /Bebop/i, /Anafi/i, /^Disco[-_]/i, /Mambo/i, /Swing/i,
            /Autel/i, /^EVO[-_]/i, /Dragonfish/i, /Skydio/i,
            /Holy.?Stone/i, /Potensic/i, /SYMA/i, /Hubsan/i, /Eachine/i, /FIMI/i,
            /Yuneec/i, /Typhoon/i, /PowerVision/i, /PowerEgg/i,
            /Drone/i, /^UAV[-_]/i, /Quadcopter/i, /^RC[-_]Drone/i
        ];

        // Drone OUI prefixes
        const droneOuiPrefixes = {
            '60:60:1F': 'DJI', '48:1C:B9': 'DJI', '34:D2:62': 'DJI', 'E0:DB:55': 'DJI',
            'C8:6C:87': 'DJI', 'A0:14:3D': 'DJI', '70:D7:11': 'DJI', '98:3A:56': 'DJI',
            '90:03:B7': 'Parrot', '00:12:1C': 'Parrot', '00:26:7E': 'Parrot',
            '8C:F5:A3': 'Autel', 'D8:E0:E1': 'Autel', 'F8:0F:6F': 'Skydio'
        };

        // Check if network is a drone
        function isDrone(ssid, bssid) {
            // Check SSID patterns
            if (ssid) {
                for (const pattern of dronePatterns) {
                    if (pattern.test(ssid)) {
                        return { isDrone: true, method: 'SSID', brand: ssid.split(/[-_\s]/)[0] };
                    }
                }
            }
            // Check OUI prefix
            if (bssid) {
                const prefix = bssid.substring(0, 8).toUpperCase();
                if (droneOuiPrefixes[prefix]) {
                    return { isDrone: true, method: 'OUI', brand: droneOuiPrefixes[prefix] };
                }
            }
            return { isDrone: false };
        }

        // Handle drone detection
        function handleDroneDetection(net, droneInfo) {
            if (detectedDrones[net.bssid]) return; // Already detected

            detectedDrones[net.bssid] = {
                ssid: net.essid,
                bssid: net.bssid,
                brand: droneInfo.brand,
                method: droneInfo.method,
                signal: net.power,
                channel: net.channel,
                firstSeen: new Date().toISOString()
            };

            droneCount++;
            document.getElementById('droneCount').textContent = droneCount;

            // Calculate approximate distance from signal strength
            const rssi = parseInt(net.power) || -70;
            const distance = estimateDroneDistance(rssi);

            // Triple alert for drones
            playAlert();
            setTimeout(playAlert, 200);
            setTimeout(playAlert, 400);

            // Show drone alert
            showDroneAlert(net.essid, net.bssid, droneInfo.brand, distance, rssi);
        }

        // Estimate distance from RSSI (rough approximation)
        function estimateDroneDistance(rssi) {
            // Using free-space path loss model (very approximate)
            // Reference: -30 dBm at 1 meter
            const txPower = -30;
            const n = 2.5; // Path loss exponent (2-4, higher for obstacles)
            const distance = Math.pow(10, (txPower - rssi) / (10 * n));
            return Math.round(distance);
        }

        // Show drone alert popup
        function showDroneAlert(ssid, bssid, brand, distance, rssi) {
            const alertDiv = document.createElement('div');
            alertDiv.className = 'drone-alert';
            alertDiv.innerHTML = `
                <div style="font-weight: bold; color: var(--accent-orange); font-size: 16px;">🚁 DRONE DETECTED</div>
                <div style="margin: 10px 0;">
                    <div><strong>SSID:</strong> ${escapeHtml(ssid || 'Unknown')}</div>
                    <div><strong>BSSID:</strong> ${bssid}</div>
                    <div><strong>Brand:</strong> ${brand || 'Unknown'}</div>
                    <div><strong>Signal:</strong> ${rssi} dBm</div>
                    <div><strong>Est. Distance:</strong> ~${distance}m</div>
                </div>
                <button onclick="this.parentElement.remove()" style="padding: 6px 16px; cursor: pointer; background: var(--accent-orange); border: none; color: #000; border-radius: 4px;">Dismiss</button>
            `;
            alertDiv.style.cssText = 'position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: #1a1a2e; border: 2px solid var(--accent-orange); padding: 20px; border-radius: 8px; z-index: 10000; text-align: center; box-shadow: 0 0 30px rgba(255,165,0,0.5); min-width: 280px;';
            document.body.appendChild(alertDiv);
            setTimeout(() => { if (alertDiv.parentElement) alertDiv.remove(); }, 15000);
        }

        // Initialize watch list display
        function initWatchList() {
            updateWatchListDisplay();
        }

        // Add MAC to watch list
        function addWatchMac() {
            const input = document.getElementById('watchMacInput');
            const mac = input.value.trim().toUpperCase();
            if (!mac || !/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(mac)) {
                alert('Please enter a valid MAC address (AA:BB:CC:DD:EE:FF)');
                return;
            }
            if (!watchMacs.includes(mac)) {
                watchMacs.push(mac);
                localStorage.setItem('watchMacs', JSON.stringify(watchMacs));
                updateWatchListDisplay();
            }
            input.value = '';
        }

        // Remove MAC from watch list
        function removeWatchMac(mac) {
            watchMacs = watchMacs.filter(m => m !== mac);
            localStorage.setItem('watchMacs', JSON.stringify(watchMacs));
            alertedMacs.delete(mac);
            updateWatchListDisplay();
        }

        // Update watch list display
        function updateWatchListDisplay() {
            const container = document.getElementById('watchList');
            if (!container) return;
            if (watchMacs.length === 0) {
                container.innerHTML = '<div style="color: #555;">No MACs in watch list</div>';
            } else {
                container.innerHTML = watchMacs.map(mac =>
                    `<div style="display: flex; justify-content: space-between; align-items: center; padding: 2px 0;">
                        <span>${mac}</span>
                        <button onclick="removeWatchMac('${mac}')" style="background: none; border: none; color: var(--accent-red); cursor: pointer; font-size: 10px;">✕</button>
                    </div>`
                ).join('');
            }
        }

        // Check if MAC is in watch list and alert
        function checkWatchList(mac, type) {
            const upperMac = mac.toUpperCase();
            if (watchMacs.includes(upperMac) && !alertedMacs.has(upperMac)) {
                alertedMacs.add(upperMac);
                // Play alert sound multiple times for urgency
                playAlert();
                setTimeout(playAlert, 300);
                setTimeout(playAlert, 600);
                // Show prominent alert
                showProximityAlert(mac, type);
            }
        }

        // Show proximity alert popup
        function showProximityAlert(mac, type) {
            const alertDiv = document.createElement('div');
            alertDiv.className = 'proximity-alert';
            alertDiv.innerHTML = `
                <div style="font-weight: bold; color: var(--accent-red);">⚠ PROXIMITY ALERT</div>
                <div>Watched ${type} detected:</div>
                <div style="font-family: monospace; font-size: 14px;">${mac}</div>
                <button onclick="this.parentElement.remove()" style="margin-top: 8px; padding: 4px 12px; cursor: pointer;">Dismiss</button>
            `;
            alertDiv.style.cssText = 'position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: #1a1a2e; border: 2px solid var(--accent-red); padding: 20px; border-radius: 8px; z-index: 10000; text-align: center; box-shadow: 0 0 30px rgba(255,0,0,0.5);';
            document.body.appendChild(alertDiv);
            // Auto-dismiss after 10 seconds
            setTimeout(() => alertDiv.remove(), 10000);
        }

        // Check for rogue APs (same SSID, different BSSID)
        function checkRogueAP(ssid, bssid, channel, signal) {
            if (!ssid || ssid === 'Hidden' || ssid === '[Hidden]') return false;

            if (!ssidToBssids[ssid]) {
                ssidToBssids[ssid] = new Set();
            }

            // Store details for this BSSID
            if (!rogueApDetails[ssid]) {
                rogueApDetails[ssid] = [];
            }

            // Check if we already have this BSSID stored
            const existingEntry = rogueApDetails[ssid].find(e => e.bssid === bssid);
            if (!existingEntry) {
                rogueApDetails[ssid].push({
                    bssid: bssid,
                    channel: channel || '?',
                    signal: signal || '?',
                    firstSeen: new Date().toLocaleTimeString()
                });
            }

            const isNewBssid = !ssidToBssids[ssid].has(bssid);
            ssidToBssids[ssid].add(bssid);

            // If we have more than one BSSID for this SSID, it could be rogue (or just multiple APs)
            if (ssidToBssids[ssid].size > 1 && isNewBssid) {
                rogueApCount++;
                document.getElementById('rogueApCount').textContent = rogueApCount;
                playAlert();

                // Get the BSSIDs to show in alert
                const bssidList = rogueApDetails[ssid].map(e => e.bssid).join(', ');
                showInfo(`⚠ Rogue AP: "${ssid}" has ${ssidToBssids[ssid].size} BSSIDs: ${bssidList}`);
                return true;
            }
            return false;
        }

        // Show rogue AP details popup
        function showRogueApDetails() {
            const rogueSSIDs = Object.keys(rogueApDetails).filter(ssid =>
                rogueApDetails[ssid].length > 1
            );

            if (rogueSSIDs.length === 0) {
                showInfo('No rogue APs detected. Rogue AP = same SSID on multiple BSSIDs.');
                return;
            }

            // Remove existing popup if any
            const existing = document.getElementById('rogueApPopup');
            if (existing) existing.remove();

            // Build details HTML
            let html = '<div style="max-height: 300px; overflow-y: auto;">';
            rogueSSIDs.forEach(ssid => {
                const aps = rogueApDetails[ssid];
                html += `<div style="margin-bottom: 12px;">
                    <div style="color: var(--accent-red); font-weight: bold; margin-bottom: 4px;">
                        📡 "${ssid}" (${aps.length} BSSIDs)
                    </div>
                    <table style="width: 100%; font-size: 10px; border-collapse: collapse;">
                        <tr style="color: var(--text-dim);">
                            <th style="text-align: left; padding: 2px 8px;">BSSID</th>
                            <th style="text-align: left; padding: 2px 8px;">CH</th>
                            <th style="text-align: left; padding: 2px 8px;">Signal</th>
                            <th style="text-align: left; padding: 2px 8px;">First Seen</th>
                        </tr>`;
                aps.forEach((ap, idx) => {
                    const bgColor = idx % 2 === 0 ? 'rgba(255,255,255,0.05)' : 'transparent';
                    html += `<tr style="background: ${bgColor};">
                        <td style="padding: 2px 8px; font-family: monospace;">${ap.bssid}</td>
                        <td style="padding: 2px 8px;">${ap.channel}</td>
                        <td style="padding: 2px 8px;">${ap.signal} dBm</td>
                        <td style="padding: 2px 8px;">${ap.firstSeen}</td>
                    </tr>`;
                });
                html += '</table></div>';
            });
            html += '</div>';
            html += '<div style="margin-top: 8px; font-size: 9px; color: var(--text-dim);">⚠ Multiple BSSIDs for same SSID may indicate rogue AP or legitimate multi-AP setup</div>';

            // Create popup
            const popup = document.createElement('div');
            popup.id = 'rogueApPopup';
            popup.style.cssText = `
                position: fixed;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                background: var(--bg-primary);
                border: 1px solid var(--accent-red);
                border-radius: 8px;
                padding: 16px;
                z-index: 10000;
                min-width: 400px;
                max-width: 600px;
                box-shadow: 0 4px 20px rgba(0,0,0,0.5);
            `;
            popup.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <span style="font-weight: bold; color: var(--accent-red);">🚨 Rogue AP Details</span>
                    <button onclick="this.parentElement.parentElement.remove()"
                            style="background: none; border: none; color: var(--text-dim); cursor: pointer; font-size: 16px;">✕</button>
                </div>
                ${html}
            `;

            document.body.appendChild(popup);
        }

        // Update 5GHz channel graph
        function updateChannel5gGraph() {
            const bars = document.querySelectorAll('#channelGraph5g .channel-bar');
            const labels = document.querySelectorAll('#channelGraph5g .channel-label');

            // Count networks per 5GHz channel
            const channelCounts = {};
            channels5g.forEach(ch => channelCounts[ch] = 0);

            Object.values(wifiNetworks).forEach(net => {
                const ch = net.channel?.toString().trim();
                if (channels5g.includes(ch)) {
                    channelCounts[ch]++;
                }
            });

            const maxCount = Math.max(1, ...Object.values(channelCounts));

            bars.forEach((bar, i) => {
                const ch = channels5g[i];
                const count = channelCounts[ch] || 0;
                const height = Math.max(2, (count / maxCount) * 50);
                bar.style.height = height + 'px';
                bar.className = 'channel-bar' + (count > 0 ? ' active' : '') + (count > 3 ? ' congested' : '') + (count > 5 ? ' very-congested' : '');
            });
        }

        // Refresh WiFi interfaces
        function refreshWifiInterfaces() {
            fetch('/wifi/interfaces')
                .then(r => r.json())
                .then(data => {
                    const select = document.getElementById('wifiInterfaceSelect');
                    if (data.interfaces.length === 0) {
                        select.innerHTML = '<option value="">No WiFi interfaces found</option>';
                    } else {
                        select.innerHTML = data.interfaces.map(i =>
                            `<option value="${i.name}">${i.name} (${i.type})${i.monitor_capable ? ' [Monitor OK]' : ''}</option>`
                        ).join('');
                    }

                    // Update tool status
                    const statusDiv = document.getElementById('wifiToolStatus');
                    statusDiv.innerHTML = `
                        <span>airmon-ng:</span><span class="tool-status ${data.tools.airmon ? 'ok' : 'missing'}">${data.tools.airmon ? 'OK' : 'Missing'}</span>
                        <span>airodump-ng:</span><span class="tool-status ${data.tools.airodump ? 'ok' : 'missing'}">${data.tools.airodump ? 'OK' : 'Missing'}</span>
                    `;

                    // Update monitor status
                    if (data.monitor_interface) {
                        monitorInterface = data.monitor_interface;
                        updateMonitorStatus(true);
                    }
                });
        }

        // Enable monitor mode
        function enableMonitorMode() {
            const iface = document.getElementById('wifiInterfaceSelect').value;
            if (!iface) {
                alert('Please select an interface');
                return;
            }

            const killProcesses = document.getElementById('killProcesses').checked;

            // Show loading state
            const btn = document.getElementById('monitorStartBtn');
            const originalText = btn.textContent;
            btn.textContent = 'Enabling...';
            btn.disabled = true;

            fetch('/wifi/monitor', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({interface: iface, action: 'start', kill_processes: killProcesses})
            }).then(r => r.json())
              .then(data => {
                  btn.textContent = originalText;
                  btn.disabled = false;

                  if (data.status === 'success') {
                      monitorInterface = data.monitor_interface;
                      updateMonitorStatus(true);
                      showInfo('Monitor mode enabled on ' + monitorInterface + ' - Ready to scan!');

                      // Refresh interface list and auto-select the monitor interface
                      fetch('/wifi/interfaces')
                          .then(r => r.json())
                          .then(ifaceData => {
                              const select = document.getElementById('wifiInterfaceSelect');
                              if (ifaceData.interfaces.length > 0) {
                                  select.innerHTML = ifaceData.interfaces.map(i =>
                                      `<option value="${i.name}" ${i.name === monitorInterface ? 'selected' : ''}>${i.name} (${i.type})${i.monitor_capable ? ' [Monitor OK]' : ''}</option>`
                                  ).join('');
                              }
                          });
                  } else {
                      alert('Error: ' + data.message);
                  }
              })
              .catch(err => {
                  btn.textContent = originalText;
                  btn.disabled = false;
                  alert('Error: ' + err.message);
              });
        }

        // Disable monitor mode
        function disableMonitorMode() {
            const iface = monitorInterface || document.getElementById('wifiInterfaceSelect').value;

            fetch('/wifi/monitor', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({interface: iface, action: 'stop'})
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'success') {
                      monitorInterface = null;
                      updateMonitorStatus(false);
                      showInfo('Monitor mode disabled');
                  } else {
                      alert('Error: ' + data.message);
                  }
              });
        }

        function updateMonitorStatus(enabled) {
            document.getElementById('monitorStartBtn').style.display = enabled ? 'none' : 'block';
            document.getElementById('monitorStopBtn').style.display = enabled ? 'block' : 'none';
            document.getElementById('monitorStatus').innerHTML = enabled
                ? 'Monitor mode: <span style="color: var(--accent-green);">Active (' + monitorInterface + ')</span>'
                : 'Monitor mode: <span style="color: var(--accent-red);">Inactive</span>';
        }

        // Start WiFi scan
        function startWifiScan() {
            const band = document.getElementById('wifiBand').value;
            const channel = document.getElementById('wifiChannel').value;

            if (!monitorInterface) {
                alert('Enable monitor mode first');
                return;
            }

            fetch('/wifi/scan/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    interface: monitorInterface,
                    band: band,
                    channel: channel || null
                })
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'started') {
                      setWifiRunning(true);
                      startWifiStream();
                  } else {
                      alert('Error: ' + data.message);
                  }
              });
        }

        // Stop WiFi scan
        function stopWifiScan() {
            fetch('/wifi/scan/stop', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    setWifiRunning(false);
                    if (wifiEventSource) {
                        wifiEventSource.close();
                        wifiEventSource = null;
                    }
                });
        }

        function setWifiRunning(running) {
            isWifiRunning = running;
            document.getElementById('statusDot').classList.toggle('running', running);
            document.getElementById('statusText').textContent = running ? 'Scanning...' : 'Idle';
            document.getElementById('startWifiBtn').style.display = running ? 'none' : 'block';
            document.getElementById('stopWifiBtn').style.display = running ? 'block' : 'none';
        }

        // Start WiFi event stream
        function startWifiStream() {
            if (wifiEventSource) {
                wifiEventSource.close();
            }

            wifiEventSource = new EventSource('/wifi/stream');

            wifiEventSource.onmessage = function(e) {
                const data = JSON.parse(e.data);

                if (data.type === 'network') {
                    handleWifiNetwork(data);
                } else if (data.type === 'client') {
                    handleWifiClient(data);
                } else if (data.type === 'info' || data.type === 'raw') {
                    showInfo(data.text);
                } else if (data.type === 'error') {
                    showError(data.text);
                } else if (data.type === 'status') {
                    if (data.text === 'stopped') {
                        setWifiRunning(false);
                    }
                }
            };

            wifiEventSource.onerror = function() {
                console.error('WiFi stream error');
            };
        }

        // Handle discovered WiFi network
        function handleWifiNetwork(net) {
            const isNew = !wifiNetworks[net.bssid];
            wifiNetworks[net.bssid] = net;

            if (isNew) {
                apCount++;
                document.getElementById('apCount').textContent = apCount;
                playAlert();
                pulseSignal();

                // Check for rogue AP (same SSID, different BSSID)
                checkRogueAP(net.essid, net.bssid, net.channel, net.power);

                // Check proximity watch list
                checkWatchList(net.bssid, 'AP');

                // Check for drone
                const droneCheck = isDrone(net.essid, net.bssid);
                if (droneCheck.isDrone) {
                    handleDroneDetection(net, droneCheck);
                }
            }

            // Update recon display
            const droneInfo = isDrone(net.essid, net.bssid);
            trackDevice({
                protocol: droneInfo.isDrone ? 'DRONE' : 'WiFi-AP',
                address: net.bssid,
                message: net.essid || '[Hidden SSID]',
                model: net.essid,
                channel: net.channel,
                privacy: net.privacy,
                isDrone: droneInfo.isDrone,
                droneBrand: droneInfo.brand
            });

            // Add to output
            addWifiNetworkCard(net, isNew);

            // Update both channel graphs
            updateChannelGraph();
            updateChannel5gGraph();
        }

        // Handle discovered WiFi client
        function handleWifiClient(client) {
            const isNew = !wifiClients[client.mac];
            wifiClients[client.mac] = client;

            if (isNew) {
                clientCount++;
                document.getElementById('clientCount').textContent = clientCount;

                // Check proximity watch list
                checkWatchList(client.mac, 'Client');
            }

            // Track in device intelligence with vendor info
            const vendorInfo = client.vendor && client.vendor !== 'Unknown' ? ` [${client.vendor}]` : '';
            trackDevice({
                protocol: 'WiFi-Client',
                address: client.mac,
                message: (client.probes || '[No probes]') + vendorInfo,
                bssid: client.bssid,
                vendor: client.vendor
            });
        }

        // Add WiFi network card to output
        function addWifiNetworkCard(net, isNew) {
            const output = document.getElementById('output');
            const placeholder = output.querySelector('.placeholder');
            if (placeholder) placeholder.remove();

            // Check if card already exists
            let card = document.getElementById('wifi_' + net.bssid.replace(/:/g, ''));

            if (!card) {
                card = document.createElement('div');
                card.id = 'wifi_' + net.bssid.replace(/:/g, '');
                card.className = 'sensor-card';
                card.style.borderLeftColor = net.privacy.includes('WPA') ? 'var(--accent-orange)' :
                                             net.privacy.includes('WEP') ? 'var(--accent-red)' :
                                             'var(--accent-green)';
                output.insertBefore(card, output.firstChild);
            }

            const signalStrength = parseInt(net.power) || -100;
            const signalBars = Math.max(0, Math.min(5, Math.floor((signalStrength + 100) / 15)));

            card.innerHTML = `
                <div class="header" style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                    <span class="device-name">${escapeHtml(net.essid || '[Hidden]')}</span>
                    <span style="color: #444; font-size: 10px;">CH ${net.channel}</span>
                </div>
                <div class="sensor-data">
                    <div class="data-item">
                        <div class="data-label">BSSID</div>
                        <div class="data-value" style="font-size: 11px;">${escapeHtml(net.bssid)}</div>
                    </div>
                    <div class="data-item">
                        <div class="data-label">Security</div>
                        <div class="data-value" style="color: ${(net.privacy || '').includes('WPA') ? 'var(--accent-orange)' : net.privacy === 'OPN' ? 'var(--accent-green)' : 'var(--accent-red)'}">${escapeHtml(net.privacy || '')}</div>
                    </div>
                    <div class="data-item">
                        <div class="data-label">Signal</div>
                        <div class="data-value">${net.power} dBm ${'█'.repeat(signalBars)}${'░'.repeat(5-signalBars)}</div>
                    </div>
                    <div class="data-item">
                        <div class="data-label">Beacons</div>
                        <div class="data-value">${net.beacons}</div>
                    </div>
                </div>
                <div style="margin-top: 8px; display: flex; gap: 5px;">
                    <button class="preset-btn" onclick="targetNetwork('${escapeAttr(net.bssid)}', '${escapeAttr(net.channel)}')" style="font-size: 10px; padding: 4px 8px;">Target</button>
                    <button class="preset-btn" onclick="captureHandshake('${escapeAttr(net.bssid)}', '${escapeAttr(net.channel)}')" style="font-size: 10px; padding: 4px 8px; border-color: var(--accent-orange); color: var(--accent-orange);">Capture</button>
                </div>
            `;

            if (autoScroll) output.scrollTop = 0;
        }

        // Target a network for attack
        function targetNetwork(bssid, channel) {
            document.getElementById('targetBssid').value = bssid;
            document.getElementById('wifiChannel').value = channel;
            showInfo('Targeted: ' + bssid + ' on channel ' + channel);
        }

        // Start handshake capture
        function captureHandshake(bssid, channel) {
            if (!confirm('Start handshake capture for ' + bssid + '? This will stop the current scan.')) {
                return;
            }

            fetch('/wifi/handshake/capture', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({bssid: bssid, channel: channel})
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'started') {
                      showInfo('🎯 Capturing handshakes for ' + bssid);
                      setWifiRunning(true);

                      // Update handshake indicator to show active capture
                      const hsSpan = document.getElementById('handshakeCount');
                      hsSpan.style.animation = 'pulse 1s infinite';
                      hsSpan.title = 'Capturing: ' + bssid;

                      // Show capture status panel
                      const panel = document.getElementById('captureStatusPanel');
                      panel.style.display = 'block';
                      document.getElementById('captureTargetBssid').textContent = bssid;
                      document.getElementById('captureTargetChannel').textContent = channel;
                      document.getElementById('captureFilePath').textContent = data.capture_file;
                      document.getElementById('captureStatus').textContent = 'Waiting for handshake...';
                      document.getElementById('captureStatus').style.color = 'var(--accent-orange)';

                      // Store active capture info and start polling
                      activeCapture = {
                          bssid: bssid,
                          channel: channel,
                          file: data.capture_file,
                          startTime: Date.now(),
                          pollInterval: setInterval(checkCaptureStatus, 5000)  // Check every 5 seconds
                      };
                  } else {
                      alert('Error: ' + data.message);
                  }
              });
        }

        // Check handshake capture status
        function checkCaptureStatus() {
            if (!activeCapture) {
                showInfo('No active handshake capture');
                return;
            }

            fetch('/wifi/handshake/status', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({file: activeCapture.file, bssid: activeCapture.bssid})
            }).then(r => r.json())
              .then(data => {
                  const statusSpan = document.getElementById('captureStatus');
                  const elapsed = Math.round((Date.now() - activeCapture.startTime) / 1000);
                  const elapsedStr = elapsed < 60 ? elapsed + 's' : Math.floor(elapsed/60) + 'm ' + (elapsed%60) + 's';

                  if (data.handshake_found) {
                      // Handshake captured!
                      statusSpan.textContent = '✓ HANDSHAKE CAPTURED!';
                      statusSpan.style.color = 'var(--accent-green)';
                      handshakeCount++;
                      document.getElementById('handshakeCount').textContent = handshakeCount;
                      playAlert();
                      showInfo('🎉 Handshake captured for ' + activeCapture.bssid + '! File: ' + data.file);

                      // Stop polling
                      if (activeCapture.pollInterval) {
                          clearInterval(activeCapture.pollInterval);
                      }
                      document.getElementById('handshakeCount').style.animation = '';
                  } else if (data.file_exists) {
                      const sizeKB = (data.file_size / 1024).toFixed(1);
                      statusSpan.textContent = 'Capturing... (' + sizeKB + ' KB, ' + elapsedStr + ')';
                      statusSpan.style.color = 'var(--accent-orange)';
                  } else if (data.status === 'stopped') {
                      statusSpan.textContent = 'Capture stopped';
                      statusSpan.style.color = 'var(--text-dim)';
                      if (activeCapture.pollInterval) {
                          clearInterval(activeCapture.pollInterval);
                      }
                  } else {
                      statusSpan.textContent = 'Waiting for data... (' + elapsedStr + ')';
                      statusSpan.style.color = 'var(--accent-orange)';
                  }
              })
              .catch(err => {
                  console.error('Capture status check failed:', err);
              });
        }

        // Stop handshake capture
        function stopHandshakeCapture() {
            if (activeCapture && activeCapture.pollInterval) {
                clearInterval(activeCapture.pollInterval);
            }

            // Stop the WiFi scan (which stops airodump-ng)
            stopWifiScan();

            document.getElementById('captureStatus').textContent = 'Stopped';
            document.getElementById('captureStatus').style.color = 'var(--text-dim)';
            document.getElementById('handshakeCount').style.animation = '';

            // Keep the panel visible so user can see the file path
            showInfo('Handshake capture stopped. Check ' + (activeCapture ? activeCapture.file : 'capture file'));

            activeCapture = null;
        }

        // Send deauth
        function sendDeauth() {
            const bssid = document.getElementById('targetBssid').value;
            const client = document.getElementById('targetClient').value || 'FF:FF:FF:FF:FF:FF';
            const count = document.getElementById('deauthCount').value || '5';

            if (!bssid) {
                alert('Enter target BSSID');
                return;
            }

            if (!confirm('Send ' + count + ' deauth packets to ' + bssid + '?\\n\\n⚠ Only use on networks you own or have authorization to test!')) {
                return;
            }

            fetch('/wifi/deauth', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({bssid: bssid, client: client, count: parseInt(count)})
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'success') {
                      showInfo(data.message);
                  } else {
                      alert('Error: ' + data.message);
                  }
              });
        }

        // ============== WIFI VISUALIZATIONS ==============

        let radarCtx = null;
        let radarAngle = 0;
        let radarAnimFrame = null;
        let radarNetworks = [];  // {x, y, strength, ssid, bssid}
        let targetBssidForSignal = null;

        // Initialize radar canvas
        function initRadar() {
            const canvas = document.getElementById('radarCanvas');
            if (!canvas) return;

            radarCtx = canvas.getContext('2d');
            canvas.width = 150;
            canvas.height = 150;

            // Start animation
            if (!radarAnimFrame) {
                animateRadar();
            }
        }

        // Animate radar sweep
        function animateRadar() {
            if (!radarCtx) {
                radarAnimFrame = null;
                return;
            }

            const canvas = radarCtx.canvas;
            const cx = canvas.width / 2;
            const cy = canvas.height / 2;
            const radius = Math.min(cx, cy) - 5;

            // Clear canvas
            radarCtx.fillStyle = 'rgba(0, 10, 10, 0.1)';
            radarCtx.fillRect(0, 0, canvas.width, canvas.height);

            // Draw grid circles
            radarCtx.strokeStyle = 'rgba(0, 212, 255, 0.2)';
            radarCtx.lineWidth = 1;
            for (let r = radius / 4; r <= radius; r += radius / 4) {
                radarCtx.beginPath();
                radarCtx.arc(cx, cy, r, 0, Math.PI * 2);
                radarCtx.stroke();
            }

            // Draw crosshairs
            radarCtx.beginPath();
            radarCtx.moveTo(cx, cy - radius);
            radarCtx.lineTo(cx, cy + radius);
            radarCtx.moveTo(cx - radius, cy);
            radarCtx.lineTo(cx + radius, cy);
            radarCtx.stroke();

            // Draw sweep line
            radarCtx.strokeStyle = 'rgba(0, 255, 136, 0.8)';
            radarCtx.lineWidth = 2;
            radarCtx.beginPath();
            radarCtx.moveTo(cx, cy);
            radarCtx.lineTo(
                cx + Math.cos(radarAngle) * radius,
                cy + Math.sin(radarAngle) * radius
            );
            radarCtx.stroke();

            // Draw sweep gradient
            const gradient = radarCtx.createConicalGradient ?
                null : // Not supported in all browsers
                radarCtx.createRadialGradient(cx, cy, 0, cx, cy, radius);

            radarCtx.fillStyle = 'rgba(0, 255, 136, 0.05)';
            radarCtx.beginPath();
            radarCtx.moveTo(cx, cy);
            radarCtx.arc(cx, cy, radius, radarAngle - 0.5, radarAngle);
            radarCtx.closePath();
            radarCtx.fill();

            // Draw network blips
            radarNetworks.forEach(net => {
                const age = Date.now() - net.timestamp;
                const alpha = Math.max(0.1, 1 - age / 10000);

                radarCtx.fillStyle = `rgba(0, 255, 136, ${alpha})`;
                radarCtx.beginPath();
                radarCtx.arc(net.x, net.y, 4 + (1 - alpha) * 3, 0, Math.PI * 2);
                radarCtx.fill();

                // Glow effect
                radarCtx.fillStyle = `rgba(0, 255, 136, ${alpha * 0.3})`;
                radarCtx.beginPath();
                radarCtx.arc(net.x, net.y, 8 + (1 - alpha) * 5, 0, Math.PI * 2);
                radarCtx.fill();
            });

            // Update angle
            radarAngle += 0.03;
            if (radarAngle > Math.PI * 2) radarAngle = 0;

            radarAnimFrame = requestAnimationFrame(animateRadar);
        }

        // Add network to radar
        function addNetworkToRadar(net) {
            const canvas = document.getElementById('radarCanvas');
            if (!canvas) return;

            const cx = canvas.width / 2;
            const cy = canvas.height / 2;
            const radius = Math.min(cx, cy) - 10;

            // Convert signal strength to distance (stronger = closer)
            const power = parseInt(net.power) || -80;
            const distance = Math.max(0.1, Math.min(1, (power + 100) / 60));
            const r = radius * (1 - distance);

            // Random angle based on BSSID hash
            let angle = 0;
            for (let i = 0; i < net.bssid.length; i++) {
                angle += net.bssid.charCodeAt(i);
            }
            angle = (angle % 360) * Math.PI / 180;

            const x = cx + Math.cos(angle) * r;
            const y = cy + Math.sin(angle) * r;

            // Update or add
            const existing = radarNetworks.find(n => n.bssid === net.bssid);
            if (existing) {
                existing.x = x;
                existing.y = y;
                existing.timestamp = Date.now();
            } else {
                radarNetworks.push({
                    x, y,
                    bssid: net.bssid,
                    ssid: net.essid,
                    timestamp: Date.now()
                });
            }

            // Limit to 50 networks
            if (radarNetworks.length > 50) {
                radarNetworks.shift();
            }
        }

        // Update channel graph
        function updateChannelGraph() {
            const channels = {};
            for (let i = 1; i <= 13; i++) channels[i] = 0;

            // Count networks per channel
            Object.values(wifiNetworks).forEach(net => {
                const ch = parseInt(net.channel);
                if (ch >= 1 && ch <= 13) {
                    channels[ch]++;
                }
            });

            // Find max for scaling
            const maxCount = Math.max(1, ...Object.values(channels));

            // Update bars
            const bars = document.querySelectorAll('#channelGraph .channel-bar');
            bars.forEach((bar, i) => {
                const ch = i + 1;
                const count = channels[ch] || 0;
                const height = Math.max(2, (count / maxCount) * 55);
                bar.style.height = height + 'px';

                bar.classList.remove('active', 'congested', 'very-congested');
                if (count > 0) bar.classList.add('active');
                if (count >= 3) bar.classList.add('congested');
                if (count >= 5) bar.classList.add('very-congested');
            });
        }

        // Update security donut chart
        function updateSecurityDonut() {
            const canvas = document.getElementById('securityCanvas');
            if (!canvas) return;

            const ctx = canvas.getContext('2d');
            const cx = canvas.width / 2;
            const cy = canvas.height / 2;
            const radius = Math.min(cx, cy) - 2;
            const innerRadius = radius * 0.6;

            // Count security types
            let wpa3 = 0, wpa2 = 0, wep = 0, open = 0;
            Object.values(wifiNetworks).forEach(net => {
                const priv = (net.privacy || '').toUpperCase();
                if (priv.includes('WPA3')) wpa3++;
                else if (priv.includes('WPA')) wpa2++;
                else if (priv.includes('WEP')) wep++;
                else if (priv === 'OPN' || priv === '' || priv === 'OPEN') open++;
                else wpa2++; // Default to WPA2
            });

            const total = wpa3 + wpa2 + wep + open;

            // Update legend
            document.getElementById('wpa3Count').textContent = wpa3;
            document.getElementById('wpa2Count').textContent = wpa2;
            document.getElementById('wepCount').textContent = wep;
            document.getElementById('openCount').textContent = open;

            // Clear canvas
            ctx.clearRect(0, 0, canvas.width, canvas.height);

            if (total === 0) {
                // Draw empty circle
                ctx.strokeStyle = '#1a1a1a';
                ctx.lineWidth = radius - innerRadius;
                ctx.beginPath();
                ctx.arc(cx, cy, (radius + innerRadius) / 2, 0, Math.PI * 2);
                ctx.stroke();
                return;
            }

            // Draw segments
            const colors = {
                wpa3: '#00ff88',
                wpa2: '#ff8800',
                wep: '#ff3366',
                open: '#00d4ff'
            };

            const data = [
                { value: wpa3, color: colors.wpa3 },
                { value: wpa2, color: colors.wpa2 },
                { value: wep, color: colors.wep },
                { value: open, color: colors.open }
            ];

            let startAngle = -Math.PI / 2;

            data.forEach(segment => {
                if (segment.value === 0) return;

                const sliceAngle = (segment.value / total) * Math.PI * 2;

                ctx.fillStyle = segment.color;
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.arc(cx, cy, radius, startAngle, startAngle + sliceAngle);
                ctx.closePath();
                ctx.fill();

                startAngle += sliceAngle;
            });

            // Draw inner circle (donut hole)
            ctx.fillStyle = '#000';
            ctx.beginPath();
            ctx.arc(cx, cy, innerRadius, 0, Math.PI * 2);
            ctx.fill();

            // Draw total in center
            ctx.fillStyle = '#fff';
            ctx.font = 'bold 16px JetBrains Mono';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(total, cx, cy);
        }

        // Update signal strength meter for targeted network
        function updateSignalMeter(net) {
            if (!net) return;

            targetBssidForSignal = net.bssid;

            const ssidEl = document.getElementById('targetSsid');
            const valueEl = document.getElementById('signalValue');
            const barsEl = document.querySelectorAll('.signal-bar-large');

            ssidEl.textContent = net.essid || net.bssid;

            const power = parseInt(net.power) || -100;
            valueEl.textContent = power + ' dBm';

            // Determine signal quality
            let quality = 'weak';
            let activeBars = 1;

            if (power >= -50) { quality = 'strong'; activeBars = 5; }
            else if (power >= -60) { quality = 'strong'; activeBars = 4; }
            else if (power >= -70) { quality = 'medium'; activeBars = 3; }
            else if (power >= -80) { quality = 'medium'; activeBars = 2; }
            else { quality = 'weak'; activeBars = 1; }

            valueEl.className = 'signal-value ' + quality;

            barsEl.forEach((bar, i) => {
                bar.className = 'signal-bar-large';
                if (i < activeBars) {
                    bar.classList.add('active', quality);
                }
            });
        }

        // Hook into handleWifiNetwork to update visualizations
        const originalHandleWifiNetwork = handleWifiNetwork;
        handleWifiNetwork = function(net) {
            originalHandleWifiNetwork(net);

            // Update radar
            addNetworkToRadar(net);

            // Update channel graph
            updateChannelGraph();

            // Update security donut
            updateSecurityDonut();

            // Update signal meter if this is the targeted network
            if (targetBssidForSignal === net.bssid) {
                updateSignalMeter(net);
            }
        };

        // Update targetNetwork to also set signal meter
        const originalTargetNetwork = targetNetwork;
        targetNetwork = function(bssid, channel) {
            originalTargetNetwork(bssid, channel);

            const net = wifiNetworks[bssid];
            if (net) {
                updateSignalMeter(net);
            }
        };

        // ============== BLUETOOTH RECONNAISSANCE ==============

        let btEventSource = null;
        let isBtRunning = false;
        let btDevices = {};
        let btDeviceCount = 0;
        let btBeaconCount = 0;
        let btRadarCtx = null;
        let btRadarAngle = 0;
        let btRadarAnimFrame = null;
        let btRadarDevices = [];

        // Refresh Bluetooth interfaces
        function refreshBtInterfaces() {
            fetch('/bt/interfaces')
                .then(r => r.json())
                .then(data => {
                    const select = document.getElementById('btInterfaceSelect');
                    if (data.interfaces.length === 0) {
                        select.innerHTML = '<option value="">No BT interfaces found</option>';
                    } else {
                        select.innerHTML = data.interfaces.map(i =>
                            `<option value="${i.name}">${i.name} (${i.type}) [${i.status}]</option>`
                        ).join('');
                    }

                    // Update tool status
                    const statusDiv = document.getElementById('btToolStatus');
                    statusDiv.innerHTML = `
                        <span>hcitool:</span><span class="tool-status ${data.tools.hcitool ? 'ok' : 'missing'}">${data.tools.hcitool ? 'OK' : 'Missing'}</span>
                        <span>bluetoothctl:</span><span class="tool-status ${data.tools.bluetoothctl ? 'ok' : 'missing'}">${data.tools.bluetoothctl ? 'OK' : 'Missing'}</span>
                    `;
                });
        }

        // Start Bluetooth scan
        function startBtScan() {
            const scanMode = document.querySelector('input[name="btScanMode"]:checked').value;
            const iface = document.getElementById('btInterfaceSelect').value;
            const duration = document.getElementById('btScanDuration').value;
            const scanBLE = document.getElementById('btScanBLE').checked;
            const scanClassic = document.getElementById('btScanClassic').checked;

            fetch('/bt/scan/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    mode: scanMode,
                    interface: iface,
                    duration: parseInt(duration),
                    scan_ble: scanBLE,
                    scan_classic: scanClassic
                })
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'started') {
                      setBtRunning(true);
                      startBtStream();
                  } else {
                      alert('Error: ' + data.message);
                  }
              });
        }

        // Stop Bluetooth scan
        function stopBtScan() {
            fetch('/bt/scan/stop', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    setBtRunning(false);
                    if (btEventSource) {
                        btEventSource.close();
                        btEventSource = null;
                    }
                });
        }

        function resetBtAdapter() {
            const iface = document.getElementById('btInterfaceSelect')?.value || 'hci0';
            fetch('/bt/reset', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({interface: iface})
            }).then(r => r.json())
              .then(data => {
                  setBtRunning(false);
                  if (btEventSource) {
                      btEventSource.close();
                      btEventSource = null;
                  }
                  if (data.status === 'success') {
                      showInfo('Bluetooth adapter reset. Status: ' + (data.is_up ? 'UP' : 'DOWN'));
                      // Refresh interface list
                      if (typeof refreshBtInterfaces === 'function') refreshBtInterfaces();
                  } else {
                      showError('Reset failed: ' + data.message);
                  }
              });
        }

        function setBtRunning(running) {
            isBtRunning = running;
            document.getElementById('statusDot').classList.toggle('running', running);
            document.getElementById('statusText').textContent = running ? 'Scanning...' : 'Idle';
            document.getElementById('startBtBtn').style.display = running ? 'none' : 'block';
            document.getElementById('stopBtBtn').style.display = running ? 'block' : 'none';
        }

        // Start Bluetooth event stream
        function startBtStream() {
            if (btEventSource) btEventSource.close();

            btEventSource = new EventSource('/bt/stream');

            btEventSource.onmessage = function(e) {
                const data = JSON.parse(e.data);

                if (data.type === 'device') {
                    handleBtDevice(data);
                } else if (data.type === 'info' || data.type === 'raw') {
                    showInfo(data.text);
                } else if (data.type === 'error') {
                    showError(data.text);
                } else if (data.type === 'status') {
                    if (data.text === 'stopped') {
                        setBtRunning(false);
                    }
                }
            };

            btEventSource.onerror = function() {
                console.error('BT stream error');
            };
        }

        // Handle discovered Bluetooth device
        function handleBtDevice(device) {
            const isNew = !btDevices[device.mac];
            btDevices[device.mac] = device;

            if (isNew) {
                btDeviceCount++;
                document.getElementById('btDeviceCount').textContent = btDeviceCount;
                playAlert();
                pulseSignal();
            }

            // Track in device intelligence
            trackDevice({
                protocol: 'Bluetooth',
                address: device.mac,
                message: device.name,
                model: device.manufacturer,
                device_type: device.device_type || device.type || 'other'
            });

            // Update visualizations
            addBtDeviceToRadar(device);

            // Add device card
            addBtDeviceCard(device, isNew);
        }

        // Add Bluetooth device card to output
        function addBtDeviceCard(device, isNew) {
            const output = document.getElementById('output');
            const placeholder = output.querySelector('.placeholder');
            if (placeholder) placeholder.remove();

            let card = document.getElementById('bt_' + device.mac.replace(/:/g, ''));

            if (!card) {
                card = document.createElement('div');
                card.id = 'bt_' + device.mac.replace(/:/g, '');
                card.className = 'sensor-card';
                const devType = device.device_type || device.type || 'other';
                card.style.borderLeftColor = device.tracker ? 'var(--accent-red)' :
                                             devType === 'phone' ? 'var(--accent-cyan)' :
                                             devType === 'audio' ? 'var(--accent-green)' :
                                             'var(--accent-orange)';
                output.insertBefore(card, output.firstChild);
            }

            const devType = device.device_type || device.type || 'other';
            const typeIcon = {
                'phone': '📱', 'audio': '🎧', 'wearable': '⌚', 'tracker': '📍',
                'computer': '💻', 'input': '⌨️', 'other': '📶'
            }[devType] || '📶';

            card.innerHTML = `
                <div class="header" style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                    <span class="device-name">${typeIcon} ${escapeHtml(device.name)}</span>
                    <span style="color: #444; font-size: 10px;">${escapeHtml(devType.toUpperCase())}</span>
                </div>
                <div class="sensor-data">
                    <div class="data-item">
                        <div class="data-label">MAC</div>
                        <div class="data-value" style="font-size: 11px;">${escapeHtml(device.mac)}</div>
                    </div>
                    <div class="data-item">
                        <div class="data-label">Manufacturer</div>
                        <div class="data-value">${escapeHtml(device.manufacturer)}</div>
                    </div>
                    ${device.tracker ? `
                    <div class="data-item">
                        <div class="data-label">Tracker</div>
                        <div class="data-value" style="color: var(--accent-red);">${escapeHtml(device.tracker.name)}</div>
                    </div>` : ''}
                </div>
                <div style="margin-top: 8px; display: flex; gap: 5px;">
                    <button class="preset-btn" onclick="btTargetDevice('${escapeAttr(device.mac)}')" style="font-size: 10px; padding: 4px 8px;">Target</button>
                    <button class="preset-btn" onclick="btEnumServicesFor('${escapeAttr(device.mac)}')" style="font-size: 10px; padding: 4px 8px;">Services</button>
                </div>
            `;

            if (autoScroll) output.scrollTop = 0;
        }

        // Target a Bluetooth device
        function btTargetDevice(mac) {
            document.getElementById('btTargetMac').value = mac;
            showInfo('Targeted: ' + mac);
        }

        // Enumerate services for a device
        function btEnumServicesFor(mac) {
            document.getElementById('btTargetMac').value = mac;
            btEnumServices();
        }

        // Enumerate services
        function btEnumServices() {
            const mac = document.getElementById('btTargetMac').value;
            if (!mac) { alert('Enter target MAC'); return; }

            showInfo('Enumerating services for ' + mac + '...');

            fetch('/bt/enum', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mac: mac})
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'success') {
                      let msg = 'Services for ' + mac + ': ';
                      if (data.services.length === 0) {
                          msg += 'None found';
                      } else {
                          msg += data.services.map(s => s.name).join(', ');
                      }
                      showInfo(msg);
                  } else {
                      showInfo('Error: ' + data.message);
                  }
              });
        }

        // Initialize Bluetooth radar
        function initBtRadar() {
            const canvas = document.getElementById('btRadarCanvas');
            if (!canvas) return;

            btRadarCtx = canvas.getContext('2d');
            canvas.width = 150;
            canvas.height = 150;

            if (!btRadarAnimFrame) {
                animateBtRadar();
            }
        }

        // Animate Bluetooth radar
        function animateBtRadar() {
            if (!btRadarCtx) { btRadarAnimFrame = null; return; }

            const canvas = btRadarCtx.canvas;
            const cx = canvas.width / 2;
            const cy = canvas.height / 2;
            const radius = Math.min(cx, cy) - 5;

            btRadarCtx.fillStyle = 'rgba(0, 10, 20, 0.1)';
            btRadarCtx.fillRect(0, 0, canvas.width, canvas.height);

            // Grid circles
            btRadarCtx.strokeStyle = 'rgba(138, 43, 226, 0.2)';
            btRadarCtx.lineWidth = 1;
            for (let r = radius / 4; r <= radius; r += radius / 4) {
                btRadarCtx.beginPath();
                btRadarCtx.arc(cx, cy, r, 0, Math.PI * 2);
                btRadarCtx.stroke();
            }

            // Sweep line (purple for BT)
            btRadarCtx.strokeStyle = 'rgba(138, 43, 226, 0.8)';
            btRadarCtx.lineWidth = 2;
            btRadarCtx.beginPath();
            btRadarCtx.moveTo(cx, cy);
            btRadarCtx.lineTo(cx + Math.cos(btRadarAngle) * radius, cy + Math.sin(btRadarAngle) * radius);
            btRadarCtx.stroke();

            // Device blips
            btRadarDevices.forEach(dev => {
                const age = Date.now() - dev.timestamp;
                const alpha = Math.max(0.1, 1 - age / 15000);
                const color = dev.isTracker ? '255, 51, 102' : '138, 43, 226';

                btRadarCtx.fillStyle = `rgba(${color}, ${alpha})`;
                btRadarCtx.beginPath();
                btRadarCtx.arc(dev.x, dev.y, dev.isTracker ? 6 : 4, 0, Math.PI * 2);
                btRadarCtx.fill();
            });

            btRadarAngle += 0.025;
            if (btRadarAngle > Math.PI * 2) btRadarAngle = 0;

            btRadarAnimFrame = requestAnimationFrame(animateBtRadar);
        }

        // Add device to BT radar
        function addBtDeviceToRadar(device) {
            const canvas = document.getElementById('btRadarCanvas');
            if (!canvas) return;

            const cx = canvas.width / 2;
            const cy = canvas.height / 2;
            const radius = Math.min(cx, cy) - 10;

            // Random position based on MAC hash
            let angle = 0;
            for (let i = 0; i < device.mac.length; i++) {
                angle += device.mac.charCodeAt(i);
            }
            angle = (angle % 360) * Math.PI / 180;
            const r = radius * (0.3 + Math.random() * 0.6);

            const x = cx + Math.cos(angle) * r;
            const y = cy + Math.sin(angle) * r;

            const existing = btRadarDevices.find(d => d.mac === device.mac);
            if (existing) {
                existing.timestamp = Date.now();
            } else {
                btRadarDevices.push({
                    x, y,
                    mac: device.mac,
                    isTracker: !!device.tracker,
                    timestamp: Date.now()
                });
            }

            if (btRadarDevices.length > 50) btRadarDevices.shift();
        }
    </script>
</body>
</html>
'''


def check_tool(name):
    """Check if a tool is installed."""
    return shutil.which(name) is not None


def is_valid_mac(mac):
    """Validate MAC address format."""
    import re
    if not mac:
        return False
    return bool(re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', mac))


def is_valid_channel(channel):
    """Validate WiFi channel number."""
    try:
        ch = int(channel)
        return 1 <= ch <= 200
    except (ValueError, TypeError):
        return False


def detect_devices():
    """Detect RTL-SDR devices."""
    devices = []

    if not check_tool('rtl_test'):
        return devices

    try:
        result = subprocess.run(
            ['rtl_test', '-t'],
            capture_output=True,
            text=True,
            timeout=5
        )
        output = result.stderr + result.stdout

        # Parse device info
        device_pattern = r'(\d+):\s+(.+?)(?:,\s*SN:\s*(\S+))?$'

        for line in output.split('\n'):
            line = line.strip()
            match = re.match(device_pattern, line)
            if match:
                devices.append({
                    'index': int(match.group(1)),
                    'name': match.group(2).strip().rstrip(','),
                    'serial': match.group(3) or 'N/A'
                })

        if not devices:
            found_match = re.search(r'Found (\d+) device', output)
            if found_match:
                count = int(found_match.group(1))
                for i in range(count):
                    devices.append({
                        'index': i,
                        'name': f'RTL-SDR Device {i}',
                        'serial': 'Unknown'
                    })

    except Exception:
        pass

    return devices


def parse_multimon_output(line):
    """Parse multimon-ng output line."""
    # POCSAG formats:
    # POCSAG512: Address: 1234567  Function: 0  Alpha:   Message here
    # POCSAG1200: Address: 1234567  Function: 0  Numeric: 123-456-7890
    # POCSAG2400: Address: 1234567  Function: 0  (no message)
    # FLEX formats:
    # FLEX: NNNN-NN-NN NN:NN:NN NNNN/NN/C NN.NNN [NNNNNNN] ALN Message here
    # FLEX|NNNN-NN-NN|NN:NN:NN|NNNN/NN/C|NN.NNN|NNNNNNN|ALN|Message

    line = line.strip()

    # POCSAG parsing - with message content
    pocsag_match = re.match(
        r'(POCSAG\d+):\s*Address:\s*(\d+)\s+Function:\s*(\d+)\s+(Alpha|Numeric):\s*(.*)',
        line
    )
    if pocsag_match:
        return {
            'protocol': pocsag_match.group(1),
            'address': pocsag_match.group(2),
            'function': pocsag_match.group(3),
            'msg_type': pocsag_match.group(4),
            'message': pocsag_match.group(5).strip() or '[No Message]'
        }

    # POCSAG parsing - address only (no message content)
    pocsag_addr_match = re.match(
        r'(POCSAG\d+):\s*Address:\s*(\d+)\s+Function:\s*(\d+)\s*$',
        line
    )
    if pocsag_addr_match:
        return {
            'protocol': pocsag_addr_match.group(1),
            'address': pocsag_addr_match.group(2),
            'function': pocsag_addr_match.group(3),
            'msg_type': 'Tone',
            'message': '[Tone Only]'
        }

    # FLEX parsing (standard format)
    flex_match = re.match(
        r'FLEX[:\|]\s*[\d\-]+[\s\|]+[\d:]+[\s\|]+([\d/A-Z]+)[\s\|]+([\d.]+)[\s\|]+\[?(\d+)\]?[\s\|]+(\w+)[\s\|]+(.*)',
        line
    )
    if flex_match:
        return {
            'protocol': 'FLEX',
            'address': flex_match.group(3),
            'function': flex_match.group(1),
            'msg_type': flex_match.group(4),
            'message': flex_match.group(5).strip() or '[No Message]'
        }

    # Simple FLEX format
    flex_simple = re.match(r'FLEX:\s*(.+)', line)
    if flex_simple:
        return {
            'protocol': 'FLEX',
            'address': 'Unknown',
            'function': '',
            'msg_type': 'Unknown',
            'message': flex_simple.group(1).strip()
        }

    return None


def stream_decoder(master_fd, process):
    """Stream decoder output to queue using PTY for unbuffered output."""
    global current_process

    try:
        output_queue.put({'type': 'status', 'text': 'started'})

        buffer = ""
        while True:
            try:
                ready, _, _ = select.select([master_fd], [], [], 1.0)
            except Exception:
                break

            if ready:
                try:
                    data = os.read(master_fd, 1024)
                    if not data:
                        break
                    buffer += data.decode('utf-8', errors='replace')

                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue

                        parsed = parse_multimon_output(line)
                        if parsed:
                            from datetime import datetime
                            parsed['timestamp'] = datetime.now().strftime('%H:%M:%S')
                            output_queue.put({'type': 'message', **parsed})
                            log_message(parsed)
                        else:
                            output_queue.put({'type': 'raw', 'text': line})
                except OSError:
                    break

            if process.poll() is not None:
                break

    except Exception as e:
        output_queue.put({'type': 'error', 'text': str(e)})
    finally:
        try:
            os.close(master_fd)
        except:
            pass
        process.wait()
        output_queue.put({'type': 'status', 'text': 'stopped'})
        with process_lock:
            current_process = None


@app.route('/')
def index():
    tools = {
        'rtl_fm': check_tool('rtl_fm'),
        'multimon': check_tool('multimon-ng'),
        'rtl_433': check_tool('rtl_433')
    }
    devices = detect_devices()
    return render_template_string(HTML_TEMPLATE, tools=tools, devices=devices)


@app.route('/favicon.svg')
def favicon():
    return send_file('favicon.svg', mimetype='image/svg+xml')


@app.route('/devices')
def get_devices():
    return jsonify(detect_devices())


@app.route('/start', methods=['POST'])
def start_decoding():
    global current_process

    with process_lock:
        if current_process:
            return jsonify({'status': 'error', 'message': 'Already running'})

        data = request.json
        freq = data.get('frequency', '929.6125')
        gain = data.get('gain', '0')
        squelch = data.get('squelch', '0')
        ppm = data.get('ppm', '0')
        device = data.get('device', '0')
        protocols = data.get('protocols', ['POCSAG512', 'POCSAG1200', 'POCSAG2400', 'FLEX'])

        # Clear queue
        while not output_queue.empty():
            try:
                output_queue.get_nowait()
            except:
                break

        # Build multimon-ng decoder arguments
        decoders = []
        for proto in protocols:
            if proto == 'POCSAG512':
                decoders.extend(['-a', 'POCSAG512'])
            elif proto == 'POCSAG1200':
                decoders.extend(['-a', 'POCSAG1200'])
            elif proto == 'POCSAG2400':
                decoders.extend(['-a', 'POCSAG2400'])
            elif proto == 'FLEX':
                decoders.extend(['-a', 'FLEX'])

        # Build rtl_fm command
        # rtl_fm -d <device> -f <freq>M -M fm -s 22050 -g <gain> -p <ppm> -l <squelch> - | multimon-ng -t raw -a POCSAG512 -a POCSAG1200 -a FLEX -f alpha -
        rtl_cmd = [
            'rtl_fm',
            '-d', str(device),
            '-f', f'{freq}M',
            '-M', 'fm',
            '-s', '22050',
        ]

        if gain and gain != '0':
            rtl_cmd.extend(['-g', str(gain)])

        if ppm and ppm != '0':
            rtl_cmd.extend(['-p', str(ppm)])

        if squelch and squelch != '0':
            rtl_cmd.extend(['-l', str(squelch)])

        rtl_cmd.append('-')

        multimon_cmd = ['multimon-ng', '-t', 'raw'] + decoders + ['-f', 'alpha', '-']

        # Log the command being run
        full_cmd = ' '.join(rtl_cmd) + ' | ' + ' '.join(multimon_cmd)
        print(f"Running: {full_cmd}")

        try:
            # Create pipe: rtl_fm | multimon-ng
            # Use PTY for multimon-ng to get unbuffered output
            rtl_process = subprocess.Popen(
                rtl_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Start a thread to monitor rtl_fm stderr for errors
            def monitor_rtl_stderr():
                for line in rtl_process.stderr:
                    err_text = line.decode('utf-8', errors='replace').strip()
                    if err_text:
                        print(f"[RTL_FM] {err_text}", flush=True)
                        output_queue.put({'type': 'raw', 'text': f'[rtl_fm] {err_text}'})

            rtl_stderr_thread = threading.Thread(target=monitor_rtl_stderr)
            rtl_stderr_thread.daemon = True
            rtl_stderr_thread.start()

            # Create a pseudo-terminal for multimon-ng output
            # This tricks it into thinking it's connected to a terminal,
            # which disables output buffering
            master_fd, slave_fd = pty.openpty()

            multimon_process = subprocess.Popen(
                multimon_cmd,
                stdin=rtl_process.stdout,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True
            )

            os.close(slave_fd)  # Close slave fd in parent process
            rtl_process.stdout.close()  # Allow rtl_process to receive SIGPIPE

            current_process = multimon_process
            current_process._rtl_process = rtl_process  # Store reference to kill later
            current_process._master_fd = master_fd  # Store for cleanup

            # Start output thread with PTY master fd
            thread = threading.Thread(target=stream_decoder, args=(master_fd, multimon_process))
            thread.daemon = True
            thread.start()

            # Send the command info to the client
            output_queue.put({'type': 'info', 'text': f'Command: {full_cmd}'})

            return jsonify({'status': 'started', 'command': full_cmd})

        except FileNotFoundError as e:
            return jsonify({'status': 'error', 'message': f'Tool not found: {e.filename}'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@app.route('/stop', methods=['POST'])
def stop_decoding():
    global current_process

    with process_lock:
        if current_process:
            # Kill rtl_fm process first
            if hasattr(current_process, '_rtl_process'):
                try:
                    current_process._rtl_process.terminate()
                    current_process._rtl_process.wait(timeout=2)
                except:
                    try:
                        current_process._rtl_process.kill()
                    except:
                        pass

            # Close PTY master fd
            if hasattr(current_process, '_master_fd'):
                try:
                    os.close(current_process._master_fd)
                except:
                    pass

            # Kill multimon-ng
            current_process.terminate()
            try:
                current_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                current_process.kill()

            current_process = None
            return jsonify({'status': 'stopped'})

        return jsonify({'status': 'not_running'})


@app.route('/status')
def get_status():
    """Check if decoder is currently running."""
    with process_lock:
        if current_process and current_process.poll() is None:
            return jsonify({'running': True, 'logging': logging_enabled, 'log_file': log_file_path})
        return jsonify({'running': False, 'logging': logging_enabled, 'log_file': log_file_path})


@app.route('/logging', methods=['POST'])
def toggle_logging():
    """Toggle message logging."""
    global logging_enabled, log_file_path
    data = request.json
    if 'enabled' in data:
        logging_enabled = data['enabled']
    if 'log_file' in data and data['log_file']:
        log_file_path = data['log_file']
    return jsonify({'logging': logging_enabled, 'log_file': log_file_path})


def log_message(msg):
    """Log a message to file if logging is enabled."""
    if not logging_enabled:
        return
    try:
        with open(log_file_path, 'a') as f:
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            f.write(f"{timestamp} | {msg.get('protocol', 'UNKNOWN')} | {msg.get('address', '')} | {msg.get('message', '')}\n")
    except Exception as e:
        print(f"[ERROR] Failed to log message: {e}", flush=True)


@app.route('/killall', methods=['POST'])
def kill_all():
    """Kill all decoder and WiFi processes."""
    global current_process, sensor_process, wifi_process

    killed = []
    processes_to_kill = [
        'rtl_fm', 'multimon-ng', 'rtl_433',
        'airodump-ng', 'aireplay-ng', 'airmon-ng'
    ]

    for proc in processes_to_kill:
        try:
            result = subprocess.run(['pkill', '-f', proc], capture_output=True)
            if result.returncode == 0:
                killed.append(proc)
        except:
            pass

    with process_lock:
        current_process = None

    with sensor_lock:
        sensor_process = None

    with wifi_lock:
        wifi_process = None

    return jsonify({'status': 'killed', 'processes': killed})


@app.route('/stream')
def stream():
    def generate():
        import json
        while True:
            try:
                msg = output_queue.get(timeout=1)
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


# ============== RTL_433 SENSOR ROUTES ==============

def stream_sensor_output(process):
    """Stream rtl_433 JSON output to queue."""
    global sensor_process
    import json as json_module

    try:
        sensor_queue.put({'type': 'status', 'text': 'started'})

        for line in iter(process.stdout.readline, b''):
            line = line.decode('utf-8', errors='replace').strip()
            if not line:
                continue

            try:
                # rtl_433 outputs JSON objects, one per line
                data = json_module.loads(line)
                data['type'] = 'sensor'
                sensor_queue.put(data)

                # Log if enabled
                if logging_enabled:
                    try:
                        with open(log_file_path, 'a') as f:
                            from datetime import datetime
                            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            f.write(f"{timestamp} | {data.get('model', 'Unknown')} | {json_module.dumps(data)}\n")
                    except Exception:
                        pass
            except json_module.JSONDecodeError:
                # Not JSON, send as raw
                sensor_queue.put({'type': 'raw', 'text': line})

    except Exception as e:
        sensor_queue.put({'type': 'error', 'text': str(e)})
    finally:
        process.wait()
        sensor_queue.put({'type': 'status', 'text': 'stopped'})
        with sensor_lock:
            sensor_process = None


@app.route('/start_sensor', methods=['POST'])
def start_sensor():
    global sensor_process

    with sensor_lock:
        if sensor_process:
            return jsonify({'status': 'error', 'message': 'Sensor already running'})

        data = request.json
        freq = data.get('frequency', '433.92')
        gain = data.get('gain', '0')
        ppm = data.get('ppm', '0')
        device = data.get('device', '0')

        # Clear queue
        while not sensor_queue.empty():
            try:
                sensor_queue.get_nowait()
            except:
                break

        # Build rtl_433 command
        # rtl_433 -d <device> -f <freq>M -g <gain> -p <ppm> -F json
        cmd = [
            'rtl_433',
            '-d', str(device),
            '-f', f'{freq}M',
            '-F', 'json'
        ]

        if gain and gain != '0':
            cmd.extend(['-g', str(gain)])

        if ppm and ppm != '0':
            cmd.extend(['-p', str(ppm)])

        full_cmd = ' '.join(cmd)
        print(f"Running: {full_cmd}")

        try:
            sensor_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1
            )

            # Start output thread
            thread = threading.Thread(target=stream_sensor_output, args=(sensor_process,))
            thread.daemon = True
            thread.start()

            # Monitor stderr
            def monitor_stderr():
                for line in sensor_process.stderr:
                    err = line.decode('utf-8', errors='replace').strip()
                    if err:
                        print(f"[rtl_433] {err}")
                        sensor_queue.put({'type': 'info', 'text': f'[rtl_433] {err}'})

            stderr_thread = threading.Thread(target=monitor_stderr)
            stderr_thread.daemon = True
            stderr_thread.start()

            sensor_queue.put({'type': 'info', 'text': f'Command: {full_cmd}'})

            return jsonify({'status': 'started', 'command': full_cmd})

        except FileNotFoundError:
            return jsonify({'status': 'error', 'message': 'rtl_433 not found. Install with: brew install rtl_433'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@app.route('/stop_sensor', methods=['POST'])
def stop_sensor():
    global sensor_process

    with sensor_lock:
        if sensor_process:
            sensor_process.terminate()
            try:
                sensor_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                sensor_process.kill()
            sensor_process = None
            return jsonify({'status': 'stopped'})

        return jsonify({'status': 'not_running'})


@app.route('/stream_sensor')
def stream_sensor():
    def generate():
        import json
        while True:
            try:
                msg = sensor_queue.get(timeout=1)
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


# ============== WIFI RECONNAISSANCE ROUTES ==============

def detect_wifi_interfaces():
    """Detect available WiFi interfaces."""
    interfaces = []
    import platform

    if platform.system() == 'Darwin':  # macOS
        try:
            # Get list of network interfaces
            result = subprocess.run(['networksetup', '-listallhardwareports'],
                                    capture_output=True, text=True, timeout=5)
            lines = result.stdout.split('\n')
            current_device = None
            for i, line in enumerate(lines):
                if 'Wi-Fi' in line or 'AirPort' in line:
                    # Next line should have the device
                    for j in range(i+1, min(i+3, len(lines))):
                        if 'Device:' in lines[j]:
                            device = lines[j].split('Device:')[1].strip()
                            interfaces.append({
                                'name': device,
                                'type': 'internal',
                                'monitor_capable': False,  # macOS internal usually can't
                                'status': 'up'
                            })
                            break
        except Exception as e:
            print(f"[WiFi] Error detecting macOS interfaces: {e}")

        # Check for USB WiFi adapters
        try:
            result = subprocess.run(['system_profiler', 'SPUSBDataType'],
                                    capture_output=True, text=True, timeout=10)
            if 'Wireless' in result.stdout or 'WLAN' in result.stdout or '802.11' in result.stdout:
                interfaces.append({
                    'name': 'USB WiFi Adapter',
                    'type': 'usb',
                    'monitor_capable': True,
                    'status': 'detected'
                })
        except Exception:
            pass

    else:  # Linux
        try:
            # Use iw to list wireless interfaces
            result = subprocess.run(['iw', 'dev'], capture_output=True, text=True, timeout=5)
            current_iface = None
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith('Interface'):
                    current_iface = line.split()[1]
                elif current_iface and 'type' in line:
                    iface_type = line.split()[-1]
                    interfaces.append({
                        'name': current_iface,
                        'type': iface_type,
                        'monitor_capable': True,
                        'status': 'up'
                    })
                    current_iface = None
        except FileNotFoundError:
            # Try iwconfig instead
            try:
                result = subprocess.run(['iwconfig'], capture_output=True, text=True, timeout=5)
                for line in result.stdout.split('\n'):
                    if 'IEEE 802.11' in line:
                        iface = line.split()[0]
                        interfaces.append({
                            'name': iface,
                            'type': 'managed',
                            'monitor_capable': True,
                            'status': 'up'
                        })
            except Exception:
                pass
        except Exception as e:
            print(f"[WiFi] Error detecting Linux interfaces: {e}")

    return interfaces


@app.route('/wifi/interfaces')
def get_wifi_interfaces():
    """Get available WiFi interfaces."""
    interfaces = detect_wifi_interfaces()
    tools = {
        'airmon': check_tool('airmon-ng'),
        'airodump': check_tool('airodump-ng'),
        'aireplay': check_tool('aireplay-ng'),
        'iw': check_tool('iw')
    }
    return jsonify({'interfaces': interfaces, 'tools': tools, 'monitor_interface': wifi_monitor_interface})


@app.route('/wifi/monitor', methods=['POST'])
def toggle_monitor_mode():
    """Enable or disable monitor mode on an interface."""
    global wifi_monitor_interface

    data = request.json
    interface = data.get('interface')
    action = data.get('action', 'start')  # 'start' or 'stop'

    if not interface:
        return jsonify({'status': 'error', 'message': 'No interface specified'})

    if action == 'start':
        # Try airmon-ng first
        if check_tool('airmon-ng'):
            try:
                import re

                # Get list of wireless interfaces BEFORE enabling monitor mode
                def get_wireless_interfaces():
                    """Get all wireless interface names."""
                    interfaces = set()
                    try:
                        # Try iwconfig first (shows wireless interfaces)
                        result = subprocess.run(['iwconfig'], capture_output=True, text=True, timeout=5)
                        for line in result.stdout.split('\n'):
                            if line and not line.startswith(' ') and 'no wireless' not in line.lower():
                                iface = line.split()[0] if line.split() else None
                                if iface:
                                    interfaces.add(iface)
                    except:
                        pass

                    try:
                        # Also check /sys/class/net for interfaces with wireless dir
                        import os
                        for iface in os.listdir('/sys/class/net'):
                            if os.path.exists(f'/sys/class/net/{iface}/wireless'):
                                interfaces.add(iface)
                    except:
                        pass

                    try:
                        # Also try ip link to find any interface
                        result = subprocess.run(['ip', 'link', 'show'], capture_output=True, text=True, timeout=5)
                        for match in re.finditer(r'^\d+:\s+(\S+):', result.stdout, re.MULTILINE):
                            iface = match.group(1).rstrip(':')
                            # Include interfaces that look like wireless (wl*, wlan*, etc)
                            if iface.startswith('wl') or 'mon' in iface:
                                interfaces.add(iface)
                    except:
                        pass

                    return interfaces

                interfaces_before = get_wireless_interfaces()
                print(f"[WiFi] Interfaces before monitor mode: {interfaces_before}", flush=True)

                # Optionally kill interfering processes (can drop other connections)
                kill_processes = data.get('kill_processes', False)
                if kill_processes:
                    print("[WiFi] Killing interfering processes...", flush=True)
                    subprocess.run(['airmon-ng', 'check', 'kill'], capture_output=True, timeout=10)
                else:
                    print("[WiFi] Skipping process kill (other connections preserved)", flush=True)

                # Start monitor mode
                result = subprocess.run(['airmon-ng', 'start', interface],
                                        capture_output=True, text=True, timeout=15)

                output = result.stdout + result.stderr
                print(f"[WiFi] airmon-ng output:\n{output}", flush=True)

                # Get interfaces AFTER enabling monitor mode
                import time
                time.sleep(1)  # Give system time to register new interface
                interfaces_after = get_wireless_interfaces()
                print(f"[WiFi] Interfaces after monitor mode: {interfaces_after}", flush=True)

                # Find the new interface (the monitor mode one)
                new_interfaces = interfaces_after - interfaces_before
                print(f"[WiFi] New interfaces detected: {new_interfaces}", flush=True)

                # Determine monitor interface
                monitor_iface = None

                # Method 1: New interface appeared
                if new_interfaces:
                    # Prefer interface with 'mon' in name
                    for iface in new_interfaces:
                        if 'mon' in iface:
                            monitor_iface = iface
                            break
                    if not monitor_iface:
                        monitor_iface = list(new_interfaces)[0]

                # Method 2: Parse airmon-ng output
                if not monitor_iface:
                    # Look for various patterns in airmon-ng output
                    patterns = [
                        r'monitor mode.*enabled.*on\s+(\S+)',  # "monitor mode enabled on wlan0mon"
                        r'\(monitor mode.*enabled.*?(\S+mon)\)',  # "(monitor mode enabled on wlan0mon)"
                        r'created\s+(\S+mon)',  # "created wlan0mon"
                        r'\bon\s+(\S+mon)\b',  # "on wlan0mon"
                        r'\b(\S+mon)\b.*monitor',  # "wlan0mon in monitor"
                        r'\b(' + re.escape(interface) + r'mon)\b',  # exact match: interfacemon
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, output, re.IGNORECASE)
                        if match:
                            monitor_iface = match.group(1)
                            print(f"[WiFi] Found monitor interface via pattern '{pattern}': {monitor_iface}", flush=True)
                            break

                # Method 3: Check if original interface now in monitor mode
                if not monitor_iface:
                    # Check if original interface is now in monitor mode
                    try:
                        result = subprocess.run(['iwconfig', interface], capture_output=True, text=True, timeout=5)
                        if 'Mode:Monitor' in result.stdout:
                            monitor_iface = interface
                            print(f"[WiFi] Original interface {interface} is now in monitor mode", flush=True)
                    except:
                        pass

                # Method 4: Check interface + 'mon'
                if not monitor_iface:
                    potential = interface + 'mon'
                    if potential in interfaces_after:
                        monitor_iface = potential

                # Method 5: Last resort - assume interface + 'mon'
                if not monitor_iface:
                    monitor_iface = interface + 'mon'
                    print(f"[WiFi] Assuming monitor interface: {monitor_iface}", flush=True)

                # Verify the interface actually exists
                try:
                    result = subprocess.run(['ip', 'link', 'show', monitor_iface], capture_output=True, text=True, timeout=5)
                    if result.returncode != 0:
                        # Interface doesn't exist - try to find any mon interface
                        for iface in interfaces_after:
                            if 'mon' in iface or iface.startswith('wl'):
                                # Check if it's in monitor mode
                                try:
                                    check = subprocess.run(['iwconfig', iface], capture_output=True, text=True, timeout=5)
                                    if 'Mode:Monitor' in check.stdout:
                                        monitor_iface = iface
                                        print(f"[WiFi] Found working monitor interface: {monitor_iface}", flush=True)
                                        break
                                except:
                                    pass
                except:
                    pass

                wifi_monitor_interface = monitor_iface
                wifi_queue.put({'type': 'info', 'text': f'Monitor mode enabled on {wifi_monitor_interface}'})
                return jsonify({'status': 'success', 'monitor_interface': wifi_monitor_interface})

            except Exception as e:
                import traceback
                print(f"[WiFi] Error enabling monitor mode: {e}\n{traceback.format_exc()}", flush=True)
                return jsonify({'status': 'error', 'message': str(e)})

        # Fallback to iw (Linux)
        elif check_tool('iw'):
            try:
                subprocess.run(['ip', 'link', 'set', interface, 'down'], capture_output=True)
                subprocess.run(['iw', interface, 'set', 'monitor', 'control'], capture_output=True)
                subprocess.run(['ip', 'link', 'set', interface, 'up'], capture_output=True)
                wifi_monitor_interface = interface
                return jsonify({'status': 'success', 'monitor_interface': interface})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)})
        else:
            return jsonify({'status': 'error', 'message': 'No monitor mode tools available. Install aircrack-ng (brew install aircrack-ng) or iw.'})

    else:  # stop
        if check_tool('airmon-ng'):
            try:
                result = subprocess.run(['airmon-ng', 'stop', wifi_monitor_interface or interface],
                                        capture_output=True, text=True, timeout=15)
                wifi_monitor_interface = None
                return jsonify({'status': 'success', 'message': 'Monitor mode disabled'})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)})
        elif check_tool('iw'):
            try:
                subprocess.run(['ip', 'link', 'set', interface, 'down'], capture_output=True)
                subprocess.run(['iw', interface, 'set', 'type', 'managed'], capture_output=True)
                subprocess.run(['ip', 'link', 'set', interface, 'up'], capture_output=True)
                wifi_monitor_interface = None
                return jsonify({'status': 'success', 'message': 'Monitor mode disabled'})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)})

    return jsonify({'status': 'error', 'message': 'Unknown action'})


def parse_airodump_csv(csv_path):
    """Parse airodump-ng CSV output file."""
    networks = {}
    clients = {}

    try:
        with open(csv_path, 'r', errors='replace') as f:
            content = f.read()

        # Split into networks and clients sections
        sections = content.split('\n\n')

        for section in sections:
            lines = section.strip().split('\n')
            if not lines:
                continue

            header = lines[0] if lines else ''

            if 'BSSID' in header and 'ESSID' in header:
                # Networks section
                for line in lines[1:]:
                    parts = [p.strip() for p in line.split(',')]
                    if len(parts) >= 14:
                        bssid = parts[0]
                        if bssid and ':' in bssid:
                            networks[bssid] = {
                                'bssid': bssid,
                                'first_seen': parts[1],
                                'last_seen': parts[2],
                                'channel': parts[3],
                                'speed': parts[4],
                                'privacy': parts[5],
                                'cipher': parts[6],
                                'auth': parts[7],
                                'power': parts[8],
                                'beacons': parts[9],
                                'ivs': parts[10],
                                'lan_ip': parts[11],
                                'essid': parts[13] or 'Hidden'
                            }

            elif 'Station MAC' in header:
                # Clients section
                for line in lines[1:]:
                    parts = [p.strip() for p in line.split(',')]
                    if len(parts) >= 6:
                        station = parts[0]
                        if station and ':' in station:
                            # Lookup vendor from OUI database
                            vendor = get_manufacturer(station)
                            clients[station] = {
                                'mac': station,
                                'first_seen': parts[1],
                                'last_seen': parts[2],
                                'power': parts[3],
                                'packets': parts[4],
                                'bssid': parts[5],
                                'probes': parts[6] if len(parts) > 6 else '',
                                'vendor': vendor
                            }
    except Exception as e:
        print(f"[WiFi] Error parsing CSV: {e}")

    return networks, clients


def stream_airodump_output(process, csv_path):
    """Stream airodump-ng output to queue."""
    global wifi_process, wifi_networks, wifi_clients
    import time
    import select

    try:
        wifi_queue.put({'type': 'status', 'text': 'started'})
        last_parse = 0
        start_time = time.time()
        csv_found = False

        while process.poll() is None:
            # Check for stderr output (non-blocking)
            try:
                import fcntl
                # Make stderr non-blocking
                fd = process.stderr.fileno()
                fl = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

                stderr_data = process.stderr.read()
                if stderr_data:
                    stderr_text = stderr_data.decode('utf-8', errors='replace').strip()
                    if stderr_text:
                        # Filter out progress updates, report actual errors
                        for line in stderr_text.split('\n'):
                            line = line.strip()
                            if line and not line.startswith('CH') and not line.startswith('Elapsed'):
                                wifi_queue.put({'type': 'error', 'text': f'airodump-ng: {line}'})
            except Exception:
                pass

            # Parse CSV file periodically
            current_time = time.time()
            if current_time - last_parse >= 2:  # Parse every 2 seconds
                csv_file = csv_path + '-01.csv'
                if os.path.exists(csv_file):
                    csv_found = True
                    networks, clients = parse_airodump_csv(csv_file)

                    # Detect new networks
                    for bssid, net in networks.items():
                        if bssid not in wifi_networks:
                            wifi_queue.put({
                                'type': 'network',
                                'action': 'new',
                                **net
                            })
                        else:
                            # Update existing
                            wifi_queue.put({
                                'type': 'network',
                                'action': 'update',
                                **net
                            })

                    # Detect new clients
                    for mac, client in clients.items():
                        if mac not in wifi_clients:
                            wifi_queue.put({
                                'type': 'client',
                                'action': 'new',
                                **client
                            })

                    wifi_networks = networks
                    wifi_clients = clients
                    last_parse = current_time

                if current_time - start_time > 5 and not csv_found:
                    # No CSV after 5 seconds - likely a problem
                    wifi_queue.put({'type': 'error', 'text': 'No scan data after 5 seconds. Check if monitor mode is properly enabled.'})
                    start_time = current_time + 30  # Don't spam this message

            time.sleep(0.5)

        # Process exited - capture any remaining stderr
        try:
            remaining_stderr = process.stderr.read()
            if remaining_stderr:
                stderr_text = remaining_stderr.decode('utf-8', errors='replace').strip()
                if stderr_text:
                    wifi_queue.put({'type': 'error', 'text': f'airodump-ng exited: {stderr_text}'})
        except Exception:
            pass

        # Check exit code
        exit_code = process.returncode
        if exit_code != 0 and exit_code is not None:
            wifi_queue.put({'type': 'error', 'text': f'airodump-ng exited with code {exit_code}'})

    except Exception as e:
        wifi_queue.put({'type': 'error', 'text': str(e)})
    finally:
        process.wait()
        wifi_queue.put({'type': 'status', 'text': 'stopped'})
        with wifi_lock:
            wifi_process = None


@app.route('/wifi/scan/start', methods=['POST'])
def start_wifi_scan():
    """Start WiFi scanning with airodump-ng."""
    global wifi_process, wifi_networks, wifi_clients

    with wifi_lock:
        if wifi_process:
            return jsonify({'status': 'error', 'message': 'Scan already running'})

        data = request.json
        interface = data.get('interface') or wifi_monitor_interface
        channel = data.get('channel')  # None = channel hopping
        band = data.get('band', 'abg')  # 'a' = 5GHz, 'bg' = 2.4GHz, 'abg' = both

        if not interface:
            return jsonify({'status': 'error', 'message': 'No monitor interface available. Enable monitor mode first.'})

        # Clear previous data
        wifi_networks = {}
        wifi_clients = {}

        # Clear queue
        while not wifi_queue.empty():
            try:
                wifi_queue.get_nowait()
            except:
                break

        # Build airodump-ng command
        csv_path = '/tmp/intercept_wifi'

        # Remove old files
        for f in [f'/tmp/intercept_wifi-01.csv', f'/tmp/intercept_wifi-01.cap']:
            try:
                os.remove(f)
            except:
                pass

        cmd = [
            'airodump-ng',
            '-w', csv_path,
            '--output-format', 'csv,pcap',
            '--band', band,
            interface
        ]

        if channel:
            cmd.extend(['-c', str(channel)])

        print(f"[WiFi] Running: {' '.join(cmd)}")

        try:
            wifi_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Wait briefly to check if process fails immediately
            import time
            time.sleep(0.5)

            if wifi_process.poll() is not None:
                # Process already exited - capture error
                stderr_output = wifi_process.stderr.read().decode('utf-8', errors='replace').strip()
                stdout_output = wifi_process.stdout.read().decode('utf-8', errors='replace').strip()
                exit_code = wifi_process.returncode
                wifi_process = None

                error_msg = stderr_output or stdout_output or f'Process exited with code {exit_code}'

                # Strip ANSI escape codes
                import re
                error_msg = re.sub(r'\x1b\[[0-9;]*m', '', error_msg)

                # Common error explanations
                if 'No such device' in error_msg or 'No such interface' in error_msg:
                    error_msg = f'Interface "{interface}" not found. Make sure monitor mode is enabled.'
                elif 'Operation not permitted' in error_msg:
                    error_msg = 'Permission denied. Try running with sudo.'
                elif 'monitor mode' in error_msg.lower():
                    error_msg = f'Interface "{interface}" is not in monitor mode. Enable monitor mode first.'
                elif 'Failed initialising' in error_msg:
                    error_msg = f'Failed to initialize "{interface}". The adapter may have been disconnected or monitor mode is not active. Try disabling and re-enabling monitor mode.'

                return jsonify({'status': 'error', 'message': error_msg})

            # Start parsing thread
            thread = threading.Thread(target=stream_airodump_output, args=(wifi_process, csv_path))
            thread.daemon = True
            thread.start()

            wifi_queue.put({'type': 'info', 'text': f'Started scanning on {interface}'})

            return jsonify({'status': 'started', 'interface': interface})

        except FileNotFoundError:
            return jsonify({'status': 'error', 'message': 'airodump-ng not found. Install aircrack-ng suite (brew install aircrack-ng).'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@app.route('/wifi/scan/stop', methods=['POST'])
def stop_wifi_scan():
    """Stop WiFi scanning."""
    global wifi_process

    with wifi_lock:
        if wifi_process:
            wifi_process.terminate()
            try:
                wifi_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                wifi_process.kill()
            wifi_process = None
            return jsonify({'status': 'stopped'})
        return jsonify({'status': 'not_running'})


@app.route('/wifi/deauth', methods=['POST'])
def send_deauth():
    """Send deauthentication packets to force handshake capture."""
    data = request.json
    target_bssid = data.get('bssid')
    target_client = data.get('client', 'FF:FF:FF:FF:FF:FF')  # Broadcast by default
    count = data.get('count', 5)
    interface = data.get('interface') or wifi_monitor_interface

    if not target_bssid:
        return jsonify({'status': 'error', 'message': 'Target BSSID required'})

    # Validate MAC addresses to prevent command injection
    if not is_valid_mac(target_bssid):
        return jsonify({'status': 'error', 'message': 'Invalid BSSID format'})

    if not is_valid_mac(target_client):
        return jsonify({'status': 'error', 'message': 'Invalid client MAC format'})

    # Validate count to prevent abuse
    try:
        count = int(count)
        if count < 1 or count > 100:
            count = 5
    except (ValueError, TypeError):
        count = 5

    if not interface:
        return jsonify({'status': 'error', 'message': 'No monitor interface'})

    if not check_tool('aireplay-ng'):
        return jsonify({'status': 'error', 'message': 'aireplay-ng not found'})

    try:
        # aireplay-ng --deauth <count> -a <AP BSSID> -c <client> <interface>
        cmd = [
            'aireplay-ng',
            '--deauth', str(count),
            '-a', target_bssid,
            '-c', target_client,
            interface
        ]

        wifi_queue.put({'type': 'info', 'text': f'Sending {count} deauth packets to {target_bssid}'})

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            return jsonify({'status': 'success', 'message': f'Sent {count} deauth packets'})
        else:
            return jsonify({'status': 'error', 'message': result.stderr})

    except subprocess.TimeoutExpired:
        return jsonify({'status': 'success', 'message': 'Deauth sent (timed out waiting for completion)'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/wifi/handshake/capture', methods=['POST'])
def capture_handshake():
    """Start targeted handshake capture."""
    global wifi_process

    data = request.json
    target_bssid = data.get('bssid')
    channel = data.get('channel')
    interface = data.get('interface') or wifi_monitor_interface

    if not target_bssid or not channel:
        return jsonify({'status': 'error', 'message': 'BSSID and channel required'})

    # Validate inputs to prevent command injection
    if not is_valid_mac(target_bssid):
        return jsonify({'status': 'error', 'message': 'Invalid BSSID format'})

    if not is_valid_channel(channel):
        return jsonify({'status': 'error', 'message': 'Invalid channel'})

    with wifi_lock:
        if wifi_process:
            return jsonify({'status': 'error', 'message': 'Scan already running. Stop it first.'})

        # Safe to use in path after validation
        capture_path = f'/tmp/intercept_handshake_{target_bssid.replace(":", "")}'

        cmd = [
            'airodump-ng',
            '-c', str(channel),
            '--bssid', target_bssid,
            '-w', capture_path,
            '--output-format', 'pcap',
            interface
        ]

        try:
            wifi_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            wifi_queue.put({'type': 'info', 'text': f'Capturing handshakes for {target_bssid} on channel {channel}'})
            return jsonify({'status': 'started', 'capture_file': capture_path + '-01.cap'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@app.route('/wifi/handshake/status', methods=['POST'])
def check_handshake_status():
    """Check if a handshake has been captured in the specified file."""
    import os

    data = request.json
    capture_file = data.get('file', '')
    target_bssid = data.get('bssid', '')

    # Security: ensure the file path is in /tmp and looks like our capture files
    if not capture_file.startswith('/tmp/intercept_handshake_') or '..' in capture_file:
        return jsonify({'status': 'error', 'message': 'Invalid capture file path'})

    # Check if file exists
    if not os.path.exists(capture_file):
        # Check if capture is still running
        with wifi_lock:
            if wifi_process and wifi_process.poll() is None:
                return jsonify({
                    'status': 'running',
                    'file_exists': False,
                    'handshake_found': False
                })
            else:
                return jsonify({
                    'status': 'stopped',
                    'file_exists': False,
                    'handshake_found': False
                })

    # File exists - get size
    file_size = os.path.getsize(capture_file)

    # Use aircrack-ng to check if handshake is present
    # aircrack-ng -a 2 -b <BSSID> <capture_file> will show if EAPOL handshake exists
    handshake_found = False
    try:
        if target_bssid and is_valid_mac(target_bssid):
            result = subprocess.run(
                ['aircrack-ng', '-a', '2', '-b', target_bssid, capture_file],
                capture_output=True,
                text=True,
                timeout=10
            )
            # Check output for handshake indicators
            # aircrack-ng shows "1 handshake" if found, or "0 handshake" if not
            output = result.stdout + result.stderr
            if '1 handshake' in output or 'handshake' in output.lower() and 'wpa' in output.lower():
                # Also check it's not "0 handshake"
                if '0 handshake' not in output:
                    handshake_found = True
    except subprocess.TimeoutExpired:
        pass  # aircrack-ng timed out, assume no handshake yet
    except Exception as e:
        print(f"[WiFi] Error checking handshake: {e}", flush=True)

    return jsonify({
        'status': 'running' if wifi_process and wifi_process.poll() is None else 'stopped',
        'file_exists': True,
        'file_size': file_size,
        'file': capture_file,
        'handshake_found': handshake_found
    })


@app.route('/wifi/networks')
def get_wifi_networks():
    """Get current list of discovered networks."""
    return jsonify({
        'networks': list(wifi_networks.values()),
        'clients': list(wifi_clients.values()),
        'handshakes': wifi_handshakes,
        'monitor_interface': wifi_monitor_interface
    })


@app.route('/wifi/stream')
def stream_wifi():
    """SSE stream for WiFi events."""
    def generate():
        import json
        while True:
            try:
                msg = wifi_queue.get(timeout=1)
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


# ============== BLUETOOTH RECONNAISSANCE ROUTES ==============

def get_manufacturer(mac):
    """Look up manufacturer from MAC address OUI."""
    prefix = mac[:8].upper()
    result = OUI_DATABASE.get(prefix, 'Unknown')
    return result


def classify_bt_device(name, device_class, services, manufacturer=None):
    """Classify Bluetooth device type based on available info."""
    name_lower = (name or '').lower()
    mfr_lower = (manufacturer or '').lower()

    # Audio devices - extensive patterns
    audio_patterns = [
        'airpod', 'earbud', 'headphone', 'headset', 'speaker', 'audio', 'beats', 'bose',
        'jbl', 'sony wh', 'sony wf', 'sennheiser', 'jabra', 'soundcore', 'anker', 'buds',
        'earphone', 'pod', 'soundbar', 'subwoofer', 'amp', 'dac', 'hifi', 'stereo',
        'skullcandy', 'marshall', 'b&o', 'bang', 'olufsen', 'harman', 'akg', 'shure',
        'audio-technica', 'plantronics', 'poly', 'soundlink', 'soundsport', 'quietcomfort',
        'freebuds', 'galaxy buds', 'wf-', 'wh-', 'linkbuds', 'momentum', 'px7', 'px8',
        'liberty', 'life', 'enco', 'oppo enco', 'nothing ear', 'ear (', 'studio buds',
        'powerbeats', 'solo', 'flex', 'tour', 'tune', 'reflect', 'endurance', 'soundpeats'
    ]
    if any(x in name_lower for x in audio_patterns):
        return 'audio'

    # Wearables - watches, bands, fitness
    wearable_patterns = [
        'watch', 'band', 'fitbit', 'garmin', 'mi band', 'miband', 'amazfit', 'huawei band',
        'galaxy watch', 'gear', 'versa', 'sense', 'charge', 'inspire', 'vivosmart',
        'vivoactive', 'venu', 'forerunner', 'fenix', 'instinct', 'polar', 'suunto',
        'whoop', 'oura', 'ring', 'wristband', 'fitness', 'tracker', 'activity',
        'apple watch', 'iwatch', 'samsung watch', 'ticwatch', 'fossil', 'withings'
    ]
    if any(x in name_lower for x in wearable_patterns):
        return 'wearable'

    # Phones - mobile devices
    phone_patterns = [
        'iphone', 'galaxy', 'pixel', 'phone', 'android', 'oneplus', 'huawei', 'xiaomi',
        'redmi', 'poco', 'realme', 'oppo', 'vivo', 'motorola', 'moto', 'nokia', 'lg',
        'sony xperia', 'xperia', 'asus', 'rog phone', 'zenfone', 'nothing phone',
        'samsung sm-', 'sm-g', 'sm-a', 'sm-s', 'sm-n', 'sm-f'
    ]
    if any(x in name_lower for x in phone_patterns):
        return 'phone'

    # Trackers - location devices
    tracker_patterns = [
        'airtag', 'tile', 'smarttag', 'chipolo', 'find my', 'findmy', 'locator',
        'gps', 'pet tracker', 'key finder', 'nut', 'trackr', 'pebblebee', 'cube'
    ]
    if any(x in name_lower for x in tracker_patterns):
        return 'tracker'

    # Input devices - keyboards, mice, controllers
    input_patterns = [
        'keyboard', 'mouse', 'controller', 'gamepad', 'joystick', 'remote', 'trackpad',
        'magic mouse', 'magic keyboard', 'mx master', 'mx keys', 'logitech', 'razer',
        'dualshock', 'dualsense', 'xbox', 'switch pro', 'joycon', 'joy-con', '8bitdo',
        'steelseries', 'corsair', 'hyperx'
    ]
    if any(x in name_lower for x in input_patterns):
        return 'input'

    # Media devices - TVs, streaming
    media_patterns = [
        'tv', 'roku', 'chromecast', 'firestick', 'fire tv', 'appletv', 'apple tv',
        'nvidia shield', 'android tv', 'smart tv', 'lg tv', 'samsung tv', 'sony tv',
        'tcl', 'hisense', 'vizio', 'projector', 'beam', 'soundbase'
    ]
    if any(x in name_lower for x in media_patterns):
        return 'media'

    # Computers - laptops, desktops
    computer_patterns = [
        'macbook', 'imac', 'mac mini', 'mac pro', 'thinkpad', 'latitude', 'xps',
        'pavilion', 'envy', 'spectre', 'surface', 'chromebook', 'ideapad', 'legion',
        'predator', 'rog', 'alienware', 'desktop', 'laptop', 'notebook', 'pc'
    ]
    if any(x in name_lower for x in computer_patterns):
        return 'computer'

    # Use manufacturer to infer type
    if mfr_lower in ['bose', 'jbl', 'sony', 'sennheiser', 'jabra', 'beats', 'bang & olufsen', 'audio-technica', 'plantronics', 'skullcandy', 'anker']:
        return 'audio'
    if mfr_lower in ['fitbit', 'garmin']:
        return 'wearable'
    if mfr_lower == 'tile':
        return 'tracker'
    if mfr_lower == 'logitech':
        return 'input'

    # Check device class if available
    if device_class:
        major_class = (device_class >> 8) & 0x1F
        if major_class == 1:  # Computer
            return 'computer'
        elif major_class == 2:  # Phone
            return 'phone'
        elif major_class == 4:  # Audio/Video
            return 'audio'
        elif major_class == 5:  # Peripheral
            return 'input'
        elif major_class == 6:  # Imaging
            return 'imaging'
        elif major_class == 7:  # Wearable
            return 'wearable'

    return 'other'


def detect_tracker(mac, name, manufacturer_data=None):
    """Detect if device is a known tracker (AirTag, Tile, etc)."""
    mac_prefix = mac[:5].upper()

    # AirTag detection (Apple Find My)
    if any(mac_prefix.startswith(p) for p in AIRTAG_PREFIXES):
        if manufacturer_data and b'\\x4c\\x00' in manufacturer_data:
            return {'type': 'airtag', 'name': 'Apple AirTag', 'risk': 'high'}

    # Tile detection
    if any(mac_prefix.startswith(p) for p in TILE_PREFIXES):
        return {'type': 'tile', 'name': 'Tile Tracker', 'risk': 'medium'}

    # Samsung SmartTag
    if any(mac_prefix.startswith(p) for p in SAMSUNG_TRACKER):
        return {'type': 'smarttag', 'name': 'Samsung SmartTag', 'risk': 'medium'}

    # Name-based detection
    name_lower = (name or '').lower()
    if 'airtag' in name_lower:
        return {'type': 'airtag', 'name': 'Apple AirTag', 'risk': 'high'}
    if 'tile' in name_lower:
        return {'type': 'tile', 'name': 'Tile Tracker', 'risk': 'medium'}
    if 'smarttag' in name_lower:
        return {'type': 'smarttag', 'name': 'Samsung SmartTag', 'risk': 'medium'}
    if 'chipolo' in name_lower:
        return {'type': 'chipolo', 'name': 'Chipolo Tracker', 'risk': 'medium'}

    return None


def detect_bt_interfaces():
    """Detect available Bluetooth interfaces."""
    interfaces = []
    import platform

    if platform.system() == 'Linux':
        try:
            # Use hciconfig to list interfaces
            result = subprocess.run(['hciconfig'], capture_output=True, text=True, timeout=5)
            output = result.stdout

            # Parse hciconfig output - "UP RUNNING" appears on a separate line
            import re
            # Split by interface blocks
            blocks = re.split(r'(?=^hci\d+:)', output, flags=re.MULTILINE)
            for block in blocks:
                if block.strip():
                    # Get interface name from first line
                    first_line = block.split('\n')[0]
                    match = re.match(r'(hci\d+):', first_line)
                    if match:
                        iface_name = match.group(1)
                        # Check if UP appears anywhere in the block
                        is_up = 'UP RUNNING' in block or '\tUP ' in block
                        interfaces.append({
                            'name': iface_name,
                            'type': 'hci',
                            'status': 'up' if is_up else 'down'
                        })
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[BT] Error detecting interfaces: {e}")

    elif platform.system() == 'Darwin':  # macOS
        # macOS uses different Bluetooth stack
        interfaces.append({
            'name': 'default',
            'type': 'macos',
            'status': 'available'
        })

    return interfaces


@app.route('/bt/reload-oui', methods=['POST'])
def reload_oui_database():
    """Reload OUI database from external file."""
    global OUI_DATABASE
    new_db = load_oui_database()
    if new_db:
        OUI_DATABASE = new_db
        return jsonify({'status': 'success', 'entries': len(OUI_DATABASE)})
    return jsonify({'status': 'error', 'message': 'Could not load oui_database.json'})


@app.route('/bt/interfaces')
def get_bt_interfaces():
    """Get available Bluetooth interfaces and tools."""
    interfaces = detect_bt_interfaces()
    tools = {
        'hcitool': check_tool('hcitool'),
        'bluetoothctl': check_tool('bluetoothctl'),
        'hciconfig': check_tool('hciconfig'),
        'l2ping': check_tool('l2ping'),
        'sdptool': check_tool('sdptool')
    }
    return jsonify({
        'interfaces': interfaces,
        'tools': tools,
        'current_interface': bt_interface
    })


def parse_hcitool_output(line):
    """Parse hcitool scan output line."""
    # Format: "AA:BB:CC:DD:EE:FF    Device Name"
    parts = line.strip().split('\t')
    if len(parts) >= 2:
        mac = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else ''
        if ':' in mac and len(mac) == 17:
            return {'mac': mac, 'name': name}
    return None


def stream_bt_scan(process, scan_mode):
    """Stream Bluetooth scan output to queue."""
    global bt_process, bt_devices
    import time

    try:
        bt_queue.put({'type': 'status', 'text': 'started'})
        start_time = time.time()
        device_found = False

        # Set up non-blocking stderr reading
        try:
            import fcntl
            fd = process.stderr.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        except Exception:
            pass

        if scan_mode == 'hcitool':
            # hcitool lescan output
            for line in iter(process.stdout.readline, b''):
                line = line.decode('utf-8', errors='replace').strip()
                if not line or 'LE Scan' in line:
                    continue

                # Parse BLE device
                parts = line.split()
                if len(parts) >= 1 and ':' in parts[0]:
                    mac = parts[0]
                    name = ' '.join(parts[1:]) if len(parts) > 1 else ''

                    manufacturer = get_manufacturer(mac)
                    device = {
                        'mac': mac,
                        'name': name or '[Unknown]',
                        'manufacturer': manufacturer,
                        'type': classify_bt_device(name, None, None, manufacturer),
                        'rssi': None,
                        'last_seen': time.time()
                    }

                    # Check for tracker
                    tracker = detect_tracker(mac, name)
                    if tracker:
                        device['tracker'] = tracker

                    is_new = mac not in bt_devices
                    bt_devices[mac] = device

                    queue_data = {
                        **device,
                        'type': 'device',  # Must come after **device to not be overwritten
                        'device_type': device.get('type', 'other'),
                        'action': 'new' if is_new else 'update',
                    }
                    bt_queue.put(queue_data)

        elif scan_mode == 'bluetoothctl':
            # bluetoothctl scan output - read from pty
            import os
            import select
            import time
            import re

            master_fd = getattr(process, '_master_fd', None)
            if not master_fd:
                bt_queue.put({'type': 'error', 'text': 'bluetoothctl pty not available'})
                return

            buffer = ''
            while process.poll() is None:
                # Check if data available
                readable, _, _ = select.select([master_fd], [], [], 1.0)
                if readable:
                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        buffer += data.decode('utf-8', errors='replace')

                        # Process complete lines
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.strip()

                            # Remove ANSI escape codes
                            line = re.sub(r'\x1b\[[0-9;]*m', '', line)
                            line = re.sub(r'\x1b\[\?.*?[a-zA-Z]', '', line)
                            line = re.sub(r'\x1b\[K', '', line)  # Clear line escape
                            line = re.sub(r'\r', '', line)  # Remove carriage returns

                            # Debug: print what we're receiving
                            if line and 'Device' in line:
                                print(f"[BT] bluetoothctl: {line}")

                            # Parse [NEW] Device or [CHG] Device lines
                            # Format: [NEW] Device AA:BB:CC:DD:EE:FF DeviceName
                            if 'Device' in line:
                                match = re.search(r'([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})\s*(.*)', line)
                                if match:
                                    mac = match.group(1).upper()
                                    name = match.group(2).strip()

                                    manufacturer = get_manufacturer(mac)
                                    device = {
                                        'mac': mac,
                                        'name': name or '[Unknown]',
                                        'manufacturer': manufacturer,
                                        'type': classify_bt_device(name, None, None, manufacturer),
                                        'rssi': None,
                                        'last_seen': time.time()
                                    }

                                    tracker = detect_tracker(mac, name)
                                    if tracker:
                                        device['tracker'] = tracker

                                    is_new = mac not in bt_devices
                                    bt_devices[mac] = device

                                    queue_data = {
                                        **device,
                                        'type': 'device',  # Must come after **device to not be overwritten
                                        'device_type': device.get('type', 'other'),
                                        'action': 'new' if is_new else 'update',
                                    }
                                    print(f"[BT] Queuing device: {mac} - {name}")
                                    bt_queue.put(queue_data)
                    except OSError:
                        break

            # Close master_fd
            try:
                os.close(master_fd)
            except:
                pass

    except Exception as e:
        bt_queue.put({'type': 'error', 'text': str(e)})
    finally:
        # Capture any remaining stderr
        try:
            remaining_stderr = process.stderr.read()
            if remaining_stderr:
                stderr_text = remaining_stderr.decode('utf-8', errors='replace').strip()
                if stderr_text:
                    bt_queue.put({'type': 'error', 'text': f'Bluetooth scan: {stderr_text}'})
        except Exception:
            pass

        # Check exit code
        process.wait()
        exit_code = process.returncode
        if exit_code != 0 and exit_code is not None:
            bt_queue.put({'type': 'error', 'text': f'Bluetooth scan exited with code {exit_code}'})

        bt_queue.put({'type': 'status', 'text': 'stopped'})
        with bt_lock:
            bt_process = None


@app.route('/bt/scan/start', methods=['POST'])
def start_bt_scan():
    """Start Bluetooth scanning."""
    global bt_process, bt_devices, bt_interface

    with bt_lock:
        # Check if process is actually still running (not just set)
        if bt_process:
            if bt_process.poll() is None:
                # Process is actually running
                return jsonify({'status': 'error', 'message': 'Scan already running'})
            else:
                # Process died, clear the state
                bt_process = None

        data = request.json
        scan_mode = data.get('mode', 'hcitool')
        interface = data.get('interface', 'hci0')
        duration = data.get('duration', 30)
        scan_ble = data.get('scan_ble', True)
        scan_classic = data.get('scan_classic', True)

        bt_interface = interface
        bt_devices = {}

        # Clear queue
        while not bt_queue.empty():
            try:
                bt_queue.get_nowait()
            except:
                break

        try:
            if scan_mode == 'hcitool':
                if scan_ble:
                    cmd = ['hcitool', '-i', interface, 'lescan', '--duplicates']
                else:
                    cmd = ['hcitool', '-i', interface, 'scan']

                bt_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )

            elif scan_mode == 'bluetoothctl':
                # Use bluetoothctl for BLE scanning with pty for proper output
                import pty
                import os
                import time

                master_fd, slave_fd = pty.openpty()
                bt_process = subprocess.Popen(
                    ['bluetoothctl'],
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    close_fds=True
                )
                os.close(slave_fd)

                # Store master_fd for reading
                bt_process._master_fd = master_fd

                # Wait for bluetoothctl to initialize
                time.sleep(0.5)

                # Power on and start LE scan (compatible with Bluetooth 5.x)
                os.write(master_fd, b'power on\n')
                time.sleep(0.3)
                os.write(master_fd, b'scan on\n')

            else:
                return jsonify({'status': 'error', 'message': f'Unknown scan mode: {scan_mode}'})

            # Wait briefly to check if process fails immediately
            import time
            time.sleep(0.5)

            if bt_process.poll() is not None:
                # Process already exited - capture error
                stderr_output = bt_process.stderr.read().decode('utf-8', errors='replace').strip()
                stdout_output = bt_process.stdout.read().decode('utf-8', errors='replace').strip()
                exit_code = bt_process.returncode
                bt_process = None

                error_msg = stderr_output or stdout_output or f'Process exited with code {exit_code}'

                # Common error explanations and auto-recovery
                if 'No such device' in error_msg or 'hci0' in error_msg.lower():
                    error_msg = f'Bluetooth interface "{interface}" not found or not available.'
                elif 'Operation not permitted' in error_msg or 'Permission denied' in error_msg:
                    error_msg = 'Permission denied. Try running with sudo or add user to bluetooth group.'
                elif 'busy' in error_msg.lower():
                    error_msg = f'Bluetooth interface "{interface}" is busy. Stop other Bluetooth operations first.'
                elif 'set scan parameters failed' in error_msg.lower() or 'input/output error' in error_msg.lower():
                    # Try to auto-reset the adapter
                    try:
                        subprocess.run(['hciconfig', interface, 'down'], capture_output=True, timeout=5)
                        subprocess.run(['hciconfig', interface, 'up'], capture_output=True, timeout=5)
                        error_msg = f'Adapter error - attempted auto-reset. Click "Reset Adapter" and try again.'
                    except:
                        error_msg = 'Bluetooth adapter I/O error. Click "Reset Adapter" to reset the adapter and try again.'

                return jsonify({'status': 'error', 'message': error_msg})

            # Start streaming thread
            thread = threading.Thread(target=stream_bt_scan, args=(bt_process, scan_mode))
            thread.daemon = True
            thread.start()

            bt_queue.put({'type': 'info', 'text': f'Started {scan_mode} scan on {interface}'})
            return jsonify({'status': 'started', 'mode': scan_mode, 'interface': interface})

        except FileNotFoundError as e:
            tool_name = e.filename or scan_mode
            return jsonify({'status': 'error', 'message': f'Tool "{tool_name}" not found. Install required Bluetooth tools.'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@app.route('/bt/scan/stop', methods=['POST'])
def stop_bt_scan():
    """Stop Bluetooth scanning."""
    global bt_process

    with bt_lock:
        if bt_process:
            bt_process.terminate()
            try:
                bt_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                bt_process.kill()
            bt_process = None
            return jsonify({'status': 'stopped'})
        return jsonify({'status': 'not_running'})


@app.route('/bt/reset', methods=['POST'])
def reset_bt_adapter():
    """Reset Bluetooth adapter and clear scan state."""
    global bt_process

    data = request.json
    interface = data.get('interface', 'hci0')

    with bt_lock:
        # Force clear the process state
        if bt_process:
            try:
                bt_process.terminate()
                bt_process.wait(timeout=2)
            except:
                try:
                    bt_process.kill()
                except:
                    pass
            bt_process = None

    # Reset the adapter
    try:
        import time
        import os

        # Kill any processes that might be using the adapter
        subprocess.run(['pkill', '-f', 'hcitool'], capture_output=True, timeout=2)
        subprocess.run(['pkill', '-f', 'bluetoothctl'], capture_output=True, timeout=2)
        time.sleep(0.5)

        # Check if running as root
        is_root = os.geteuid() == 0

        # Try rfkill unblock first
        subprocess.run(['rfkill', 'unblock', 'bluetooth'], capture_output=True, timeout=5)

        # Reset the adapter with a delay between down and up
        if is_root:
            down_result = subprocess.run(['hciconfig', interface, 'down'], capture_output=True, text=True, timeout=5)
            time.sleep(1)
            up_result = subprocess.run(['hciconfig', interface, 'up'], capture_output=True, text=True, timeout=5)
        else:
            # Try with sudo
            down_result = subprocess.run(['sudo', '-n', 'hciconfig', interface, 'down'], capture_output=True, text=True, timeout=5)
            time.sleep(1)
            up_result = subprocess.run(['sudo', '-n', 'hciconfig', interface, 'up'], capture_output=True, text=True, timeout=5)

        time.sleep(0.5)

        # Check if adapter is up
        result = subprocess.run(['hciconfig', interface], capture_output=True, text=True, timeout=5)
        is_up = 'UP RUNNING' in result.stdout

        # If still not up, try bluetoothctl
        if not is_up:
            subprocess.run(['bluetoothctl', 'power', 'off'], capture_output=True, timeout=5)
            time.sleep(1)
            subprocess.run(['bluetoothctl', 'power', 'on'], capture_output=True, timeout=5)
            time.sleep(0.5)
            result = subprocess.run(['hciconfig', interface], capture_output=True, text=True, timeout=5)
            is_up = 'UP RUNNING' in result.stdout

        if is_up:
            bt_queue.put({'type': 'info', 'text': f'Bluetooth adapter {interface} reset successfully'})
        else:
            bt_queue.put({'type': 'error', 'text': f'Adapter {interface} may need manual reset. Try: sudo hciconfig {interface} up'})

        return jsonify({
            'status': 'success' if is_up else 'warning',
            'message': f'Adapter {interface} reset' if is_up else f'Reset attempted but adapter still down. Run: sudo hciconfig {interface} up',
            'is_up': is_up
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/bt/enum', methods=['POST'])
def enum_bt_services():
    """Enumerate services on a Bluetooth device."""
    data = request.json
    target_mac = data.get('mac')

    if not target_mac:
        return jsonify({'status': 'error', 'message': 'Target MAC required'})

    try:
        # Try sdptool for classic BT
        result = subprocess.run(
            ['sdptool', 'browse', target_mac],
            capture_output=True, text=True, timeout=30
        )

        services = []
        current_service = {}

        for line in result.stdout.split('\n'):
            line = line.strip()
            if line.startswith('Service Name:'):
                if current_service:
                    services.append(current_service)
                current_service = {'name': line.split(':', 1)[1].strip()}
            elif line.startswith('Service Description:'):
                current_service['description'] = line.split(':', 1)[1].strip()
            elif line.startswith('Service Provider:'):
                current_service['provider'] = line.split(':', 1)[1].strip()
            elif 'Protocol Descriptor' in line:
                current_service['protocol'] = line

        if current_service:
            services.append(current_service)

        bt_services[target_mac] = services

        return jsonify({
            'status': 'success',
            'mac': target_mac,
            'services': services
        })

    except subprocess.TimeoutExpired:
        return jsonify({'status': 'error', 'message': 'Connection timed out'})
    except FileNotFoundError:
        return jsonify({'status': 'error', 'message': 'sdptool not found'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/bt/devices')
def get_bt_devices():
    """Get current list of discovered Bluetooth devices."""
    return jsonify({
        'devices': list(bt_devices.values()),
        'beacons': list(bt_beacons.values()),
        'interface': bt_interface
    })


@app.route('/bt/stream')
def stream_bt():
    """SSE stream for Bluetooth events."""
    print("[BT Stream] Client connected")
    def generate():
        import json
        print("[BT Stream] Generator started, waiting for queue...")
        while True:
            try:
                msg = bt_queue.get(timeout=1)
                print(f"[BT Stream] Got from queue: {msg.get('type')}")
                if msg.get('type') == 'device':
                    print(f"[BT Stream] Sending device: {msg.get('mac')}")
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


def main():
    print("=" * 50)
    print("  INTERCEPT // Signal Intelligence")
    print("  POCSAG / FLEX / 433MHz / WiFi / Bluetooth")
    print("=" * 50)
    print()
    print("Open http://localhost:5050 in your browser")
    print()
    print("Press Ctrl+C to stop")
    print()

    app.run(host='0.0.0.0', port=5050, debug=False, threaded=True)


if __name__ == '__main__':
    main()
