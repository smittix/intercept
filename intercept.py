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
from flask import Flask, render_template_string, jsonify, request, Response, send_file

app = Flask(__name__)

# Global process management
current_process = None
sensor_process = None
wifi_process = None
kismet_process = None
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

# OUI Database for manufacturer lookup (common ones)
OUI_DATABASE = {
    '00:00:0A': 'Omron',
    '00:1A:7D': 'Cyber-Blue',
    '00:1E:3D': 'Alps Electric',
    '00:1F:20': 'Logitech',
    '00:25:DB': 'Apple',
    '04:52:F3': 'Apple',
    '0C:3E:9F': 'Apple',
    '10:94:BB': 'Apple',
    '14:99:E2': 'Apple',
    '20:78:F0': 'Apple',
    '28:6A:BA': 'Apple',
    '3C:22:FB': 'Apple',
    '40:98:AD': 'Apple',
    '48:D7:05': 'Apple',
    '4C:57:CA': 'Apple',
    '54:4E:90': 'Apple',
    '5C:97:F3': 'Apple',
    '60:F8:1D': 'Apple',
    '68:DB:CA': 'Apple',
    '70:56:81': 'Apple',
    '78:7B:8A': 'Apple',
    '7C:D1:C3': 'Apple',
    '84:FC:FE': 'Apple',
    '8C:2D:AA': 'Apple',
    '90:B0:ED': 'Apple',
    '98:01:A7': 'Apple',
    '98:D6:BB': 'Apple',
    'A4:D1:D2': 'Apple',
    'AC:BC:32': 'Apple',
    'B0:34:95': 'Apple',
    'B8:C1:11': 'Apple',
    'C8:69:CD': 'Apple',
    'D0:03:4B': 'Apple',
    'DC:A9:04': 'Apple',
    'E0:C7:67': 'Apple',
    'F0:18:98': 'Apple',
    'F4:5C:89': 'Apple',
    '00:1B:66': 'Samsung',
    '00:21:19': 'Samsung',
    '00:26:37': 'Samsung',
    '5C:0A:5B': 'Samsung',
    '8C:71:F8': 'Samsung',
    'C4:73:1E': 'Samsung',
    '38:2C:4A': 'Samsung',
    '00:1E:4C': 'Samsung',
    '64:B5:C6': 'Liteon/Google',
    '54:60:09': 'Google',
    '00:1A:11': 'Google',
    'F4:F5:D8': 'Google',
    '94:EB:2C': 'Google',
    '78:4F:43': 'Apple',
    'F8:E4:E3': 'Tile',
    'C4:E7:BE': 'Tile',
    'E0:E5:CF': 'Raspberry Pi',
    'B8:27:EB': 'Raspberry Pi',
    'DC:A6:32': 'Raspberry Pi',
    '00:0B:57': 'Silicon Wave',  # BT Chips
    '00:02:72': 'CC&C',  # BT dongles
}


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
            color: var(--text-dim);
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
                            <span>kismet:</span><span class="tool-status missing">Checking...</span>
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
                        <div id="monitorStatus" class="info-text" style="margin-top: 8px;">
                            Monitor mode: <span style="color: var(--accent-red);">Inactive</span>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Scan Mode</h3>
                        <div class="checkbox-group" style="margin-bottom: 10px;">
                            <label><input type="radio" name="wifiScanMode" value="airodump" checked> Aircrack-ng</label>
                            <label><input type="radio" name="wifiScanMode" value="kismet"> Kismet</label>
                        </div>
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
                            <span>ubertooth:</span><span class="tool-status missing">Checking...</span>
                            <span>bettercap:</span><span class="tool-status missing">Checking...</span>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Scan Mode</h3>
                        <div class="checkbox-group" style="margin-bottom: 10px;">
                            <label><input type="radio" name="btScanMode" value="hcitool" checked> hcitool (Classic)</label>
                            <label><input type="radio" name="btScanMode" value="bluetoothctl"> bluetoothctl (BLE)</label>
                            <label><input type="radio" name="btScanMode" value="ubertooth"> Ubertooth</label>
                            <label><input type="radio" name="btScanMode" value="bettercap"> Bettercap</label>
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
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 5px; margin-bottom: 10px;">
                            <button class="preset-btn" onclick="btEnumServices()">Enum Services</button>
                            <button class="preset-btn" onclick="btPing()">L2CAP Ping</button>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Attack Options</h3>
                        <div class="info-text" style="color: var(--accent-red); margin-bottom: 10px;">
                            ⚠ Authorized testing only
                        </div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 5px;">
                            <button class="preset-btn" onclick="btReplayAttack()" style="border-color: var(--accent-orange); color: var(--accent-orange);">Replay</button>
                            <button class="preset-btn" onclick="btDosAttack()" style="border-color: var(--accent-red); color: var(--accent-red);">DoS Ping</button>
                            <button class="preset-btn" onclick="btSpoofMac()" style="border-color: var(--accent-orange); color: var(--accent-orange);">Spoof MAC</button>
                            <button class="preset-btn" onclick="btScanVulns()" style="border-color: var(--accent-red); color: var(--accent-red);">Vuln Scan</button>
                        </div>
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
                            <div>HANDSHAKES: <span id="handshakeCount">0</span></div>
                        </div>
                        <div class="stats" id="btStats" style="display: none;">
                            <div>DEVICES: <span id="btDeviceCount">0</span></div>
                            <div>BEACONS: <span id="btBeaconCount">0</span></div>
                            <div>TRACKERS: <span id="btTrackerCount">0</span></div>
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
                    <div class="wifi-visual-panel">
                        <h5>Device Types</h5>
                        <div class="security-container">
                            <div class="security-donut">
                                <canvas id="btTypeCanvas" width="80" height="80"></canvas>
                            </div>
                            <div class="security-legend">
                                <div class="security-legend-item"><div class="security-legend-dot" style="background: #00d4ff;"></div>Phones: <span id="btPhoneCount">0</span></div>
                                <div class="security-legend-item"><div class="security-legend-dot" style="background: #00ff88;"></div>Audio: <span id="btAudioCount">0</span></div>
                                <div class="security-legend-item"><div class="security-legend-dot" style="background: #ff8800;"></div>Wearables: <span id="btWearableCount">0</span></div>
                                <div class="security-legend-item"><div class="security-legend-dot" style="background: #ff3366;"></div>Trackers: <span id="btTrackerTypeCount">0</span></div>
                                <div class="security-legend-item"><div class="security-legend-dot" style="background: #888;"></div>Other: <span id="btOtherCount">0</span></div>
                            </div>
                        </div>
                    </div>
                    <div class="wifi-visual-panel">
                        <h5>Manufacturer Breakdown</h5>
                        <div id="btManufacturerList" style="font-size: 10px; font-family: 'JetBrains Mono', monospace;">
                            <div style="color: #444;">Scanning for devices...</div>
                        </div>
                    </div>
                    <div class="wifi-visual-panel">
                        <h5>Tracker Detection</h5>
                        <div id="btTrackerList" style="font-size: 10px; max-height: 120px; overflow-y: auto;">
                            <div style="color: #444; padding: 10px; text-align: center;">
                                Monitoring for AirTags, Tiles, and other trackers...
                            </div>
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
        let reconEnabled = localStorage.getItem('reconEnabled') === 'true';
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
                        <span>kismet:</span><span class="tool-status ${data.tools.kismet ? 'ok' : 'missing'}">${data.tools.kismet ? 'OK' : 'Missing'}</span>
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

            fetch('/wifi/monitor', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({interface: iface, action: 'start'})
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'success') {
                      monitorInterface = data.monitor_interface;
                      updateMonitorStatus(true);
                      showInfo('Monitor mode enabled on ' + monitorInterface);
                  } else {
                      alert('Error: ' + data.message);
                  }
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
            const scanMode = document.querySelector('input[name="wifiScanMode"]:checked').value;
            const band = document.getElementById('wifiBand').value;
            const channel = document.getElementById('wifiChannel').value;

            if (!monitorInterface) {
                alert('Enable monitor mode first');
                return;
            }

            const endpoint = scanMode === 'kismet' ? '/wifi/kismet/start' : '/wifi/scan/start';

            fetch(endpoint, {
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
            const scanMode = document.querySelector('input[name="wifiScanMode"]:checked').value;
            const endpoint = scanMode === 'kismet' ? '/wifi/kismet/stop' : '/wifi/scan/stop';

            fetch(endpoint, {method: 'POST'})
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
            }

            // Update recon display
            trackDevice({
                protocol: 'WiFi-AP',
                address: net.bssid,
                message: net.essid || '[Hidden SSID]',
                model: net.essid,
                channel: net.channel,
                privacy: net.privacy
            });

            // Add to output
            addWifiNetworkCard(net, isNew);
        }

        // Handle discovered WiFi client
        function handleWifiClient(client) {
            const isNew = !wifiClients[client.mac];
            wifiClients[client.mac] = client;

            if (isNew) {
                clientCount++;
                document.getElementById('clientCount').textContent = clientCount;
            }

            // Track in device intelligence
            trackDevice({
                protocol: 'WiFi-Client',
                address: client.mac,
                message: client.probes || '[No probes]',
                bssid: client.bssid
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
                      showInfo('Capturing handshakes for ' + bssid + '. File: ' + data.capture_file);
                      setWifiRunning(true);
                  } else {
                      alert('Error: ' + data.message);
                  }
              });
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
        let btTrackerCount = 0;
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
                        <span>ubertooth:</span><span class="tool-status ${data.tools.ubertooth ? 'ok' : 'missing'}">${data.tools.ubertooth ? 'OK' : 'Missing'}</span>
                        <span>bettercap:</span><span class="tool-status ${data.tools.bettercap ? 'ok' : 'missing'}">${data.tools.bettercap ? 'OK' : 'Missing'}</span>
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

                if (device.tracker) {
                    btTrackerCount++;
                    document.getElementById('btTrackerCount').textContent = btTrackerCount;
                    addTrackerAlert(device);
                }
            }

            // Track in device intelligence
            trackDevice({
                protocol: 'Bluetooth',
                address: device.mac,
                message: device.name,
                model: device.manufacturer,
                type: device.type
            });

            // Update visualizations
            addBtDeviceToRadar(device);
            updateBtTypeChart();
            updateBtManufacturerList();

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
                card.style.borderLeftColor = device.tracker ? 'var(--accent-red)' :
                                             device.type === 'phone' ? 'var(--accent-cyan)' :
                                             device.type === 'audio' ? 'var(--accent-green)' :
                                             'var(--accent-orange)';
                output.insertBefore(card, output.firstChild);
            }

            const typeIcon = {
                'phone': '📱', 'audio': '🎧', 'wearable': '⌚', 'tracker': '📍',
                'computer': '💻', 'input': '⌨️', 'other': '📶'
            }[device.type] || '📶';

            card.innerHTML = `
                <div class="header" style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                    <span class="device-name">${typeIcon} ${escapeHtml(device.name)}</span>
                    <span style="color: #444; font-size: 10px;">${escapeHtml(device.type.toUpperCase())}</span>
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

        // Add tracker alert to visualization
        function addTrackerAlert(device) {
            const list = document.getElementById('btTrackerList');
            const placeholder = list.querySelector('div[style*="text-align: center"]');
            if (placeholder) placeholder.remove();

            const alert = document.createElement('div');
            alert.style.cssText = 'padding: 8px; margin-bottom: 5px; background: rgba(255,51,102,0.1); border-left: 2px solid var(--accent-red); font-family: JetBrains Mono, monospace;';
            alert.innerHTML = `
                <div style="color: var(--accent-red); font-weight: bold;">⚠ ${escapeHtml(device.tracker.name)} Detected</div>
                <div style="color: #888; font-size: 9px;">${escapeHtml(device.mac)}</div>
            `;
            list.insertBefore(alert, list.firstChild);
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

        // L2CAP Ping
        function btPing() {
            const mac = document.getElementById('btTargetMac').value;
            if (!mac) { alert('Enter target MAC'); return; }

            fetch('/bt/ping', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mac: mac, count: 5})
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'success') {
                      showInfo('Ping ' + mac + ': ' + (data.reachable ? 'Reachable' : 'Unreachable'));
                  } else {
                      showInfo('Ping error: ' + data.message);
                  }
              });
        }

        // DoS attack
        function btDosAttack() {
            const mac = document.getElementById('btTargetMac').value;
            if (!mac) { alert('Enter target MAC'); return; }

            if (!confirm('Send DoS ping flood to ' + mac + '?\\n\\n⚠ Only test on devices you own!')) return;

            showInfo('Starting DoS test on ' + mac + '...');

            fetch('/bt/dos', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mac: mac, count: 100, size: 600})
            }).then(r => r.json())
              .then(data => {
                  showInfo('DoS test complete: ' + (data.message || 'Done'));
              });
        }

        // Stub functions for other attacks
        function btReplayAttack() { alert('Replay attack requires captured packets'); }
        function btSpoofMac() { alert('MAC spoofing requires root privileges'); }
        function btScanVulns() { alert('Vulnerability scanning not yet implemented'); }

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

        // Update device type chart
        function updateBtTypeChart() {
            const canvas = document.getElementById('btTypeCanvas');
            if (!canvas) return;

            let phones = 0, audio = 0, wearables = 0, trackers = 0, other = 0;

            Object.values(btDevices).forEach(d => {
                if (d.tracker) trackers++;
                else if (d.type === 'phone') phones++;
                else if (d.type === 'audio') audio++;
                else if (d.type === 'wearable') wearables++;
                else other++;
            });

            document.getElementById('btPhoneCount').textContent = phones;
            document.getElementById('btAudioCount').textContent = audio;
            document.getElementById('btWearableCount').textContent = wearables;
            document.getElementById('btTrackerTypeCount').textContent = trackers;
            document.getElementById('btOtherCount').textContent = other;

            // Draw donut
            const ctx = canvas.getContext('2d');
            const cx = canvas.width / 2;
            const cy = canvas.height / 2;
            const r = Math.min(cx, cy) - 2;
            const inner = r * 0.6;

            ctx.clearRect(0, 0, canvas.width, canvas.height);

            const total = phones + audio + wearables + trackers + other;
            if (total === 0) return;

            const data = [
                { value: phones, color: '#00d4ff' },
                { value: audio, color: '#00ff88' },
                { value: wearables, color: '#ff8800' },
                { value: trackers, color: '#ff3366' },
                { value: other, color: '#888' }
            ];

            let start = -Math.PI / 2;
            data.forEach(seg => {
                if (seg.value === 0) return;
                const angle = (seg.value / total) * Math.PI * 2;
                ctx.fillStyle = seg.color;
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.arc(cx, cy, r, start, start + angle);
                ctx.closePath();
                ctx.fill();
                start += angle;
            });

            ctx.fillStyle = '#000';
            ctx.beginPath();
            ctx.arc(cx, cy, inner, 0, Math.PI * 2);
            ctx.fill();

            ctx.fillStyle = '#fff';
            ctx.font = 'bold 14px JetBrains Mono';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(total, cx, cy);
        }

        // Update manufacturer list
        function updateBtManufacturerList() {
            const manufacturers = {};
            Object.values(btDevices).forEach(d => {
                const m = d.manufacturer || 'Unknown';
                manufacturers[m] = (manufacturers[m] || 0) + 1;
            });

            const sorted = Object.entries(manufacturers).sort((a, b) => b[1] - a[1]).slice(0, 6);

            const list = document.getElementById('btManufacturerList');
            if (sorted.length === 0) {
                list.innerHTML = '<div style="color: #444;">Scanning for devices...</div>';
            } else {
                list.innerHTML = sorted.map(([name, count]) =>
                    `<div style="display: flex; justify-content: space-between; padding: 3px 0; border-bottom: 1px solid #1a1a1a;"><span>${name}</span><span style="color: var(--accent-cyan);">${count}</span></div>`
                ).join('');
            }
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
    global current_process, sensor_process, wifi_process, kismet_process

    killed = []
    processes_to_kill = [
        'rtl_fm', 'multimon-ng', 'rtl_433',
        'airodump-ng', 'aireplay-ng', 'airmon-ng', 'kismet'
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
        kismet_process = None

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
        'kismet': check_tool('kismet'),
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
                # Kill interfering processes
                subprocess.run(['airmon-ng', 'check', 'kill'], capture_output=True, timeout=10)

                # Start monitor mode
                result = subprocess.run(['airmon-ng', 'start', interface],
                                        capture_output=True, text=True, timeout=15)

                # Parse output to find monitor interface name
                output = result.stdout + result.stderr
                # Common patterns: wlan0mon, wlp3s0mon, etc.
                import re
                # Look for "on <interface>mon" pattern first (most reliable)
                match = re.search(r'\bon\s+(\w+mon)\b', output, re.IGNORECASE)
                if not match:
                    # Fallback: look for interface pattern like wlan0mon, wlp3s0mon (must have a digit)
                    match = re.search(r'\b(\w*\d+\w*mon)\b', output)
                if not match:
                    # Second fallback: look for the original interface + mon in output
                    iface_pattern = re.escape(interface) + r'mon'
                    match = re.search(r'\b(' + iface_pattern + r')\b', output)
                if match:
                    wifi_monitor_interface = match.group(1)
                else:
                    # Assume it's interface + 'mon'
                    wifi_monitor_interface = interface + 'mon'

                wifi_queue.put({'type': 'info', 'text': f'Monitor mode enabled on {wifi_monitor_interface}'})
                return jsonify({'status': 'success', 'monitor_interface': wifi_monitor_interface})

            except Exception as e:
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
                            clients[station] = {
                                'mac': station,
                                'first_seen': parts[1],
                                'last_seen': parts[2],
                                'power': parts[3],
                                'packets': parts[4],
                                'bssid': parts[5],
                                'probes': parts[6] if len(parts) > 6 else ''
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
        for f in [f'/tmp/intercept_wifi-01.csv', f'/tmp/intercept_wifi-01.cap',
                  f'/tmp/intercept_wifi-01.kismet.csv', f'/tmp/intercept_wifi-01.kismet.netxml']:
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

                # Common error explanations
                if 'No such device' in error_msg or 'No such interface' in error_msg:
                    error_msg = f'Interface "{interface}" not found. Make sure monitor mode is enabled.'
                elif 'Operation not permitted' in error_msg:
                    error_msg = 'Permission denied. Try running with sudo.'
                elif 'monitor mode' in error_msg.lower():
                    error_msg = f'Interface "{interface}" is not in monitor mode. Enable monitor mode first.'

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


@app.route('/wifi/kismet/start', methods=['POST'])
def start_kismet():
    """Start Kismet for passive reconnaissance."""
    global kismet_process

    data = request.json
    interface = data.get('interface') or wifi_monitor_interface

    if not interface:
        return jsonify({'status': 'error', 'message': 'No interface specified'})

    if not check_tool('kismet'):
        return jsonify({'status': 'error', 'message': 'Kismet not found. Install with: brew install kismet'})

    with wifi_lock:
        if kismet_process:
            return jsonify({'status': 'error', 'message': 'Kismet already running'})

        try:
            # Start Kismet with REST API enabled
            cmd = [
                'kismet',
                '-c', interface,
                '--no-ncurses',
                '--override', 'httpd_bind_address=127.0.0.1',
                '--override', 'httpd_port=2501'
            ]

            kismet_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            wifi_queue.put({'type': 'info', 'text': 'Kismet started. API available at http://127.0.0.1:2501'})
            return jsonify({'status': 'started', 'api_url': 'http://127.0.0.1:2501'})

        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@app.route('/wifi/kismet/stop', methods=['POST'])
def stop_kismet():
    """Stop Kismet."""
    global kismet_process

    with wifi_lock:
        if kismet_process:
            kismet_process.terminate()
            try:
                kismet_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                kismet_process.kill()
            kismet_process = None
            return jsonify({'status': 'stopped'})
        return jsonify({'status': 'not_running'})


@app.route('/wifi/kismet/devices')
def get_kismet_devices():
    """Get devices from Kismet REST API."""
    import urllib.request
    import json as json_module

    try:
        # Kismet REST API endpoint for devices
        url = 'http://127.0.0.1:2501/devices/views/all/devices.json'
        req = urllib.request.Request(url)
        req.add_header('KISMET', 'admin:admin')  # Default credentials

        with urllib.request.urlopen(req, timeout=5) as response:
            data = json_module.loads(response.read().decode())
            return jsonify({'devices': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


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
    return OUI_DATABASE.get(prefix, 'Unknown')


def classify_bt_device(name, device_class, services):
    """Classify Bluetooth device type based on available info."""
    name_lower = (name or '').lower()

    # Check name for common patterns
    if any(x in name_lower for x in ['airpod', 'earbud', 'headphone', 'speaker', 'audio', 'beats', 'bose', 'jbl', 'sony wh', 'sony wf']):
        return 'audio'
    if any(x in name_lower for x in ['watch', 'band', 'fitbit', 'garmin', 'mi band']):
        return 'wearable'
    if any(x in name_lower for x in ['iphone', 'galaxy', 'pixel', 'phone', 'android']):
        return 'phone'
    if any(x in name_lower for x in ['airtag', 'tile', 'smarttag', 'tracker', 'chipolo']):
        return 'tracker'
    if any(x in name_lower for x in ['keyboard', 'mouse', 'controller', 'gamepad']):
        return 'input'
    if any(x in name_lower for x in ['tv', 'roku', 'chromecast', 'firestick']):
        return 'media'

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

    # Check for Ubertooth
    try:
        result = subprocess.run(['ubertooth-util', '-v'], capture_output=True, timeout=5)
        if result.returncode == 0:
            interfaces.append({
                'name': 'ubertooth0',
                'type': 'ubertooth',
                'status': 'connected'
            })
    except:
        pass

    return interfaces


@app.route('/bt/interfaces')
def get_bt_interfaces():
    """Get available Bluetooth interfaces and tools."""
    interfaces = detect_bt_interfaces()
    tools = {
        'hcitool': check_tool('hcitool'),
        'bluetoothctl': check_tool('bluetoothctl'),
        'ubertooth': check_tool('ubertooth-scan'),
        'bettercap': check_tool('bettercap'),
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

                    device = {
                        'mac': mac,
                        'name': name or '[Unknown]',
                        'manufacturer': get_manufacturer(mac),
                        'type': classify_bt_device(name, None, None),
                        'rssi': None,
                        'last_seen': time.time()
                    }

                    # Check for tracker
                    tracker = detect_tracker(mac, name)
                    if tracker:
                        device['tracker'] = tracker

                    is_new = mac not in bt_devices
                    bt_devices[mac] = device

                    bt_queue.put({
                        'type': 'device',
                        'action': 'new' if is_new else 'update',
                        **device
                    })

        elif scan_mode == 'bluetoothctl':
            # bluetoothctl scan output - read from pty
            import os
            import select

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
                            import re
                            line = re.sub(r'\x1b\[[0-9;]*m', '', line)
                            line = re.sub(r'\x1b\[\?.*?[a-zA-Z]', '', line)

                            # Parse [NEW] Device or [CHG] Device lines
                            if 'Device' in line and ':' in line:
                                match = re.search(r'([0-9A-Fa-f:]{17})\s*(.*)', line)
                                if match:
                                    mac = match.group(1)
                                    name = match.group(2).strip()

                                    device = {
                                        'mac': mac,
                                        'name': name or '[Unknown]',
                                        'manufacturer': get_manufacturer(mac),
                                        'type': classify_bt_device(name, None, None),
                                        'rssi': None,
                                        'last_seen': time.time()
                                    }

                                    tracker = detect_tracker(mac, name)
                                    if tracker:
                                        device['tracker'] = tracker

                                    is_new = mac not in bt_devices
                                    bt_devices[mac] = device

                                    bt_queue.put({
                                        'type': 'device',
                                        'action': 'new' if is_new else 'update',
                                        **device
                                    })
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
                # Use bluetoothctl for scanning with stdbuf to unbuffer output
                # Or use script command to provide a pty
                import pty
                import os

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

                # Send scan on command
                os.write(master_fd, b'scan on\n')

            elif scan_mode == 'ubertooth':
                cmd = ['ubertooth-scan', '-t', str(duration)]
                bt_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )

            elif scan_mode == 'bettercap':
                cmd = ['bettercap', '-eval', 'ble.recon on', '-silent']
                bt_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )

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

        # Kill any processes that might be using the adapter
        subprocess.run(['pkill', '-f', 'hcitool'], capture_output=True, timeout=2)
        subprocess.run(['pkill', '-f', 'bluetoothctl'], capture_output=True, timeout=2)
        time.sleep(0.5)

        # Reset the adapter with a delay between down and up
        subprocess.run(['hciconfig', interface, 'down'], capture_output=True, timeout=5)
        time.sleep(1)
        subprocess.run(['hciconfig', interface, 'up'], capture_output=True, timeout=5)
        time.sleep(0.5)

        # Check if adapter is up
        result = subprocess.run(['hciconfig', interface], capture_output=True, text=True, timeout=5)
        is_up = 'UP RUNNING' in result.stdout

        bt_queue.put({'type': 'info', 'text': f'Bluetooth adapter {interface} reset'})

        return jsonify({
            'status': 'success',
            'message': f'Adapter {interface} reset',
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


@app.route('/bt/ping', methods=['POST'])
def ping_bt_device():
    """Ping a Bluetooth device using l2ping."""
    data = request.json
    target_mac = data.get('mac')
    count = data.get('count', 5)

    if not target_mac:
        return jsonify({'status': 'error', 'message': 'Target MAC required'})

    # Validate MAC address
    if not is_valid_mac(target_mac):
        return jsonify({'status': 'error', 'message': 'Invalid MAC address format'})

    # Validate count
    try:
        count = int(count)
        if count < 1 or count > 50:
            count = 5
    except (ValueError, TypeError):
        count = 5

    try:
        result = subprocess.run(
            ['l2ping', '-c', str(count), target_mac],
            capture_output=True, text=True, timeout=30
        )

        return jsonify({
            'status': 'success',
            'output': result.stdout,
            'reachable': result.returncode == 0
        })

    except subprocess.TimeoutExpired:
        return jsonify({'status': 'error', 'message': 'Ping timed out'})
    except FileNotFoundError:
        return jsonify({'status': 'error', 'message': 'l2ping not found'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/bt/dos', methods=['POST'])
def dos_bt_device():
    """Flood ping a Bluetooth device (DoS test)."""
    data = request.json
    target_mac = data.get('mac')
    count = data.get('count', 100)
    size = data.get('size', 600)

    if not target_mac:
        return jsonify({'status': 'error', 'message': 'Target MAC required'})

    # Validate MAC address
    if not is_valid_mac(target_mac):
        return jsonify({'status': 'error', 'message': 'Invalid MAC address format'})

    # Validate count and size to prevent abuse
    try:
        count = int(count)
        if count < 1 or count > 1000:
            count = 100
    except (ValueError, TypeError):
        count = 100

    try:
        size = int(size)
        if size < 1 or size > 1500:
            size = 600
    except (ValueError, TypeError):
        size = 600

    try:
        # l2ping flood with large packets
        result = subprocess.run(
            ['l2ping', '-c', str(count), '-s', str(size), '-f', target_mac],
            capture_output=True, text=True, timeout=60
        )

        bt_queue.put({'type': 'info', 'text': f'DoS test complete on {target_mac}'})

        return jsonify({
            'status': 'success',
            'output': result.stdout
        })

    except subprocess.TimeoutExpired:
        return jsonify({'status': 'success', 'message': 'DoS test timed out (expected)'})
    except FileNotFoundError:
        return jsonify({'status': 'error', 'message': 'l2ping not found'})
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
    def generate():
        import json
        while True:
            try:
                msg = bt_queue.get(timeout=1)
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
