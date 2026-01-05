"""WiFi reconnaissance routes."""

from __future__ import annotations

import fcntl
import json
import os
import platform
import queue
import re
import subprocess
import threading
import time
from typing import Any, Generator

from flask import Blueprint, jsonify, request, Response

import app as app_module
from utils.dependencies import check_tool
from utils.logging import wifi_logger as logger
from utils.process import is_valid_mac, is_valid_channel
from utils.validation import validate_wifi_channel, validate_mac_address
from utils.sse import format_sse
from data.oui import get_manufacturer

wifi_bp = Blueprint('wifi', __name__, url_prefix='/wifi')

# PMKID process state
pmkid_process = None
pmkid_lock = threading.Lock()


def detect_wifi_interfaces():
    """Detect available WiFi interfaces."""
    interfaces = []

    if platform.system() == 'Darwin':  # macOS
        try:
            result = subprocess.run(['networksetup', '-listallhardwareports'],
                                    capture_output=True, text=True, timeout=5)
            lines = result.stdout.split('\n')
            for i, line in enumerate(lines):
                if 'Wi-Fi' in line or 'AirPort' in line:
                    for j in range(i+1, min(i+3, len(lines))):
                        if 'Device:' in lines[j]:
                            device = lines[j].split('Device:')[1].strip()
                            interfaces.append({
                                'name': device,
                                'type': 'internal',
                                'monitor_capable': False,
                                'status': 'up'
                            })
                            break
        except Exception as e:
            logger.error(f"Error detecting macOS interfaces: {e}")

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
            logger.error(f"Error detecting Linux interfaces: {e}")

    return interfaces


def parse_airodump_csv(csv_path):
    """Parse airodump-ng CSV output file."""
    networks = {}
    clients = {}

    try:
        with open(csv_path, 'r', errors='replace') as f:
            content = f.read()

        sections = content.split('\n\n')

        for section in sections:
            lines = section.strip().split('\n')
            if not lines:
                continue

            header = lines[0] if lines else ''

            if 'BSSID' in header and 'ESSID' in header:
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
                for line in lines[1:]:
                    parts = [p.strip() for p in line.split(',')]
                    if len(parts) >= 6:
                        station = parts[0]
                        if station and ':' in station:
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
        logger.error(f"Error parsing CSV: {e}")

    return networks, clients


def stream_airodump_output(process, csv_path):
    """Stream airodump-ng output to queue."""
    try:
        app_module.wifi_queue.put({'type': 'status', 'text': 'started'})
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
                    stderr_text = stderr_data.decode('utf-8', errors='replace').strip()
                    if stderr_text:
                        for line in stderr_text.split('\n'):
                            line = line.strip()
                            if line and not line.startswith('CH') and not line.startswith('Elapsed'):
                                app_module.wifi_queue.put({'type': 'error', 'text': f'airodump-ng: {line}'})
            except Exception:
                pass

            current_time = time.time()
            if current_time - last_parse >= 2:
                csv_file = csv_path + '-01.csv'
                if os.path.exists(csv_file):
                    csv_found = True
                    networks, clients = parse_airodump_csv(csv_file)

                    for bssid, net in networks.items():
                        if bssid not in app_module.wifi_networks:
                            app_module.wifi_queue.put({
                                'type': 'network',
                                'action': 'new',
                                **net
                            })
                        else:
                            app_module.wifi_queue.put({
                                'type': 'network',
                                'action': 'update',
                                **net
                            })

                    for mac, client in clients.items():
                        if mac not in app_module.wifi_clients:
                            app_module.wifi_queue.put({
                                'type': 'client',
                                'action': 'new',
                                **client
                            })

                    app_module.wifi_networks = networks
                    app_module.wifi_clients = clients
                    last_parse = current_time

                if current_time - start_time > 5 and not csv_found:
                    app_module.wifi_queue.put({'type': 'error', 'text': 'No scan data after 5 seconds. Check if monitor mode is properly enabled.'})
                    start_time = current_time + 30

            time.sleep(0.5)

        try:
            remaining_stderr = process.stderr.read()
            if remaining_stderr:
                stderr_text = remaining_stderr.decode('utf-8', errors='replace').strip()
                if stderr_text:
                    app_module.wifi_queue.put({'type': 'error', 'text': f'airodump-ng exited: {stderr_text}'})
        except Exception:
            pass

        exit_code = process.returncode
        if exit_code != 0 and exit_code is not None:
            app_module.wifi_queue.put({'type': 'error', 'text': f'airodump-ng exited with code {exit_code}'})

    except Exception as e:
        app_module.wifi_queue.put({'type': 'error', 'text': str(e)})
    finally:
        process.wait()
        app_module.wifi_queue.put({'type': 'status', 'text': 'stopped'})
        with app_module.wifi_lock:
            app_module.wifi_process = None


@wifi_bp.route('/interfaces')
def get_wifi_interfaces():
    """Get available WiFi interfaces."""
    interfaces = detect_wifi_interfaces()
    tools = {
        'airmon': check_tool('airmon-ng'),
        'airodump': check_tool('airodump-ng'),
        'aireplay': check_tool('aireplay-ng'),
        'iw': check_tool('iw')
    }
    return jsonify({'interfaces': interfaces, 'tools': tools, 'monitor_interface': app_module.wifi_monitor_interface})


@wifi_bp.route('/monitor', methods=['POST'])
def toggle_monitor_mode():
    """Enable or disable monitor mode on an interface."""
    data = request.json
    interface = data.get('interface')
    action = data.get('action', 'start')

    if not interface:
        return jsonify({'status': 'error', 'message': 'No interface specified'})

    if action == 'start':
        if check_tool('airmon-ng'):
            try:
                def get_wireless_interfaces():
                    interfaces = set()
                    try:
                        result = subprocess.run(['iwconfig'], capture_output=True, text=True, timeout=5)
                        for line in result.stdout.split('\n'):
                            if line and not line.startswith(' ') and 'no wireless' not in line.lower():
                                iface = line.split()[0] if line.split() else None
                                if iface:
                                    interfaces.add(iface)
                    except (subprocess.SubprocessError, OSError):
                        pass

                    try:
                        for iface in os.listdir('/sys/class/net'):
                            if os.path.exists(f'/sys/class/net/{iface}/wireless'):
                                interfaces.add(iface)
                    except OSError:
                        pass

                    try:
                        result = subprocess.run(['ip', 'link', 'show'], capture_output=True, text=True, timeout=5)
                        for match in re.finditer(r'^\d+:\s+(\S+):', result.stdout, re.MULTILINE):
                            iface = match.group(1).rstrip(':')
                            if iface.startswith('wl') or 'mon' in iface:
                                interfaces.add(iface)
                    except (subprocess.SubprocessError, OSError):
                        pass

                    return interfaces

                interfaces_before = get_wireless_interfaces()

                kill_processes = data.get('kill_processes', False)
                if kill_processes:
                    subprocess.run(['airmon-ng', 'check', 'kill'], capture_output=True, timeout=10)

                result = subprocess.run(['airmon-ng', 'start', interface],
                                        capture_output=True, text=True, timeout=15)

                output = result.stdout + result.stderr

                time.sleep(1)
                interfaces_after = get_wireless_interfaces()

                new_interfaces = interfaces_after - interfaces_before
                monitor_iface = None

                if new_interfaces:
                    for iface in new_interfaces:
                        if 'mon' in iface:
                            monitor_iface = iface
                            break
                    if not monitor_iface:
                        monitor_iface = list(new_interfaces)[0]

                if not monitor_iface:
                    # Patterns to extract monitor interface name from airmon-ng output
                    # Interface names: start with letter, contain alphanumeric/underscore/dash
                    patterns = [
                        # Look for interface names ending in 'mon' (most reliable)
                        r'\b([a-zA-Z][a-zA-Z0-9_-]*mon)\b',
                        # Airmon-ng format: [phyX]interfacename
                        r'\[phy\d+\]([a-zA-Z][a-zA-Z0-9_-]*mon)',
                        # "enabled for/on [phyX]interface" format
                        r'enabled.*?\[phy\d+\]([a-zA-Z][a-zA-Z0-9_-]*)',
                        # Original interface with 'mon' appended
                        r'\b(' + re.escape(interface) + r'mon)\b',
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, output, re.IGNORECASE)
                        if match:
                            candidate = match.group(1)
                            # Validate it looks like an interface name (not channel info like "10)")
                            if candidate and not candidate[0].isdigit() and ')' not in candidate:
                                monitor_iface = candidate
                                break

                if not monitor_iface:
                    try:
                        result = subprocess.run(['iwconfig', interface], capture_output=True, text=True, timeout=5)
                        if 'Mode:Monitor' in result.stdout:
                            monitor_iface = interface
                    except (subprocess.SubprocessError, OSError):
                        pass

                if not monitor_iface:
                    potential = interface + 'mon'
                    if potential in interfaces_after:
                        monitor_iface = potential

                if not monitor_iface:
                    monitor_iface = interface + 'mon'

                app_module.wifi_monitor_interface = monitor_iface
                app_module.wifi_queue.put({'type': 'info', 'text': f'Monitor mode enabled on {app_module.wifi_monitor_interface}'})
                return jsonify({'status': 'success', 'monitor_interface': app_module.wifi_monitor_interface})

            except Exception as e:
                import traceback
                logger.error(f"Error enabling monitor mode: {e}", exc_info=True)
                return jsonify({'status': 'error', 'message': str(e)})

        elif check_tool('iw'):
            try:
                subprocess.run(['ip', 'link', 'set', interface, 'down'], capture_output=True)
                subprocess.run(['iw', interface, 'set', 'monitor', 'control'], capture_output=True)
                subprocess.run(['ip', 'link', 'set', interface, 'up'], capture_output=True)
                app_module.wifi_monitor_interface = interface
                return jsonify({'status': 'success', 'monitor_interface': interface})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)})
        else:
            return jsonify({'status': 'error', 'message': 'No monitor mode tools available.'})

    else:  # stop
        if check_tool('airmon-ng'):
            try:
                subprocess.run(['airmon-ng', 'stop', app_module.wifi_monitor_interface or interface],
                               capture_output=True, text=True, timeout=15)
                app_module.wifi_monitor_interface = None
                return jsonify({'status': 'success', 'message': 'Monitor mode disabled'})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)})
        elif check_tool('iw'):
            try:
                subprocess.run(['ip', 'link', 'set', interface, 'down'], capture_output=True)
                subprocess.run(['iw', interface, 'set', 'type', 'managed'], capture_output=True)
                subprocess.run(['ip', 'link', 'set', interface, 'up'], capture_output=True)
                app_module.wifi_monitor_interface = None
                return jsonify({'status': 'success', 'message': 'Monitor mode disabled'})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)})

    return jsonify({'status': 'error', 'message': 'Unknown action'})


@wifi_bp.route('/scan/start', methods=['POST'])
def start_wifi_scan():
    """Start WiFi scanning with airodump-ng."""
    with app_module.wifi_lock:
        if app_module.wifi_process:
            return jsonify({'status': 'error', 'message': 'Scan already running'})

        data = request.json
        interface = data.get('interface') or app_module.wifi_monitor_interface
        channel = data.get('channel')
        band = data.get('band', 'abg')

        if not interface:
            return jsonify({'status': 'error', 'message': 'No monitor interface available.'})

        app_module.wifi_networks = {}
        app_module.wifi_clients = {}

        while not app_module.wifi_queue.empty():
            try:
                app_module.wifi_queue.get_nowait()
            except queue.Empty:
                break

        csv_path = '/tmp/intercept_wifi'

        for f in [f'/tmp/intercept_wifi-01.csv', f'/tmp/intercept_wifi-01.cap']:
            try:
                os.remove(f)
            except OSError:
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

        logger.info(f"Running: {' '.join(cmd)}")

        try:
            app_module.wifi_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            time.sleep(0.5)

            if app_module.wifi_process.poll() is not None:
                stderr_output = app_module.wifi_process.stderr.read().decode('utf-8', errors='replace').strip()
                stdout_output = app_module.wifi_process.stdout.read().decode('utf-8', errors='replace').strip()
                exit_code = app_module.wifi_process.returncode
                app_module.wifi_process = None

                error_msg = stderr_output or stdout_output or f'Process exited with code {exit_code}'
                error_msg = re.sub(r'\x1b\[[0-9;]*m', '', error_msg)

                if 'No such device' in error_msg or 'No such interface' in error_msg:
                    error_msg = f'Interface "{interface}" not found.'
                elif 'Operation not permitted' in error_msg:
                    error_msg = 'Permission denied. Try running with sudo.'

                return jsonify({'status': 'error', 'message': error_msg})

            thread = threading.Thread(target=stream_airodump_output, args=(app_module.wifi_process, csv_path))
            thread.daemon = True
            thread.start()

            app_module.wifi_queue.put({'type': 'info', 'text': f'Started scanning on {interface}'})

            return jsonify({'status': 'started', 'interface': interface})

        except FileNotFoundError:
            return jsonify({'status': 'error', 'message': 'airodump-ng not found.'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@wifi_bp.route('/scan/stop', methods=['POST'])
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
            return jsonify({'status': 'stopped'})
        return jsonify({'status': 'not_running'})


@wifi_bp.route('/deauth', methods=['POST'])
def send_deauth():
    """Send deauthentication packets."""
    data = request.json
    target_bssid = data.get('bssid')
    target_client = data.get('client', 'FF:FF:FF:FF:FF:FF')
    count = data.get('count', 5)
    interface = data.get('interface') or app_module.wifi_monitor_interface

    if not target_bssid:
        return jsonify({'status': 'error', 'message': 'Target BSSID required'})

    if not is_valid_mac(target_bssid):
        return jsonify({'status': 'error', 'message': 'Invalid BSSID format'})

    if not is_valid_mac(target_client):
        return jsonify({'status': 'error', 'message': 'Invalid client MAC format'})

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
        cmd = [
            'aireplay-ng',
            '--deauth', str(count),
            '-a', target_bssid,
            '-c', target_client,
            interface
        ]

        app_module.wifi_queue.put({'type': 'info', 'text': f'Sending {count} deauth packets to {target_bssid}'})

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            return jsonify({'status': 'success', 'message': f'Sent {count} deauth packets'})
        else:
            return jsonify({'status': 'error', 'message': result.stderr})

    except subprocess.TimeoutExpired:
        return jsonify({'status': 'success', 'message': 'Deauth sent (timed out)'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@wifi_bp.route('/handshake/capture', methods=['POST'])
def capture_handshake():
    """Start targeted handshake capture."""
    data = request.json
    target_bssid = data.get('bssid')
    channel = data.get('channel')
    interface = data.get('interface') or app_module.wifi_monitor_interface

    if not target_bssid or not channel:
        return jsonify({'status': 'error', 'message': 'BSSID and channel required'})

    if not is_valid_mac(target_bssid):
        return jsonify({'status': 'error', 'message': 'Invalid BSSID format'})

    if not is_valid_channel(channel):
        return jsonify({'status': 'error', 'message': 'Invalid channel'})

    with app_module.wifi_lock:
        if app_module.wifi_process:
            return jsonify({'status': 'error', 'message': 'Scan already running.'})

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
            app_module.wifi_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            app_module.wifi_queue.put({'type': 'info', 'text': f'Capturing handshakes for {target_bssid}'})
            return jsonify({'status': 'started', 'capture_file': capture_path + '-01.cap'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@wifi_bp.route('/handshake/status', methods=['POST'])
def check_handshake_status():
    """Check if a handshake has been captured."""
    data = request.json
    capture_file = data.get('file', '')
    target_bssid = data.get('bssid', '')

    if not capture_file.startswith('/tmp/intercept_handshake_') or '..' in capture_file:
        return jsonify({'status': 'error', 'message': 'Invalid capture file path'})

    if not os.path.exists(capture_file):
        with app_module.wifi_lock:
            if app_module.wifi_process and app_module.wifi_process.poll() is None:
                return jsonify({'status': 'running', 'file_exists': False, 'handshake_found': False})
            else:
                return jsonify({'status': 'stopped', 'file_exists': False, 'handshake_found': False})

    file_size = os.path.getsize(capture_file)
    handshake_found = False

    try:
        if target_bssid and is_valid_mac(target_bssid):
            result = subprocess.run(
                ['aircrack-ng', '-a', '2', '-b', target_bssid, capture_file],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout + result.stderr
            if '1 handshake' in output or ('handshake' in output.lower() and 'wpa' in output.lower()):
                if '0 handshake' not in output:
                    handshake_found = True
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        logger.error(f"Error checking handshake: {e}")

    return jsonify({
        'status': 'running' if app_module.wifi_process and app_module.wifi_process.poll() is None else 'stopped',
        'file_exists': True,
        'file_size': file_size,
        'file': capture_file,
        'handshake_found': handshake_found
    })


@wifi_bp.route('/pmkid/capture', methods=['POST'])
def capture_pmkid():
    """Start PMKID capture using hcxdumptool."""
    global pmkid_process

    data = request.json
    target_bssid = data.get('bssid')
    channel = data.get('channel')
    interface = data.get('interface') or app_module.wifi_monitor_interface

    if not target_bssid:
        return jsonify({'status': 'error', 'message': 'BSSID required'})

    if not is_valid_mac(target_bssid):
        return jsonify({'status': 'error', 'message': 'Invalid BSSID format'})

    with pmkid_lock:
        if pmkid_process and pmkid_process.poll() is None:
            return jsonify({'status': 'error', 'message': 'PMKID capture already running'})

        capture_path = f'/tmp/intercept_pmkid_{target_bssid.replace(":", "")}.pcapng'
        filter_file = f'/tmp/pmkid_filter_{target_bssid.replace(":", "")}'
        with open(filter_file, 'w') as f:
            f.write(target_bssid.replace(':', '').lower())

        cmd = [
            'hcxdumptool',
            '-i', interface,
            '-o', capture_path,
            '--filterlist_ap', filter_file,
            '--filtermode', '2',
            '--enable_status', '1'
        ]

        if channel:
            cmd.extend(['-c', str(channel)])

        try:
            pmkid_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return jsonify({'status': 'started', 'file': capture_path})
        except FileNotFoundError:
            return jsonify({'status': 'error', 'message': 'hcxdumptool not found.'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@wifi_bp.route('/pmkid/status', methods=['POST'])
def check_pmkid_status():
    """Check if PMKID has been captured."""
    data = request.json
    capture_file = data.get('file', '')

    if not capture_file.startswith('/tmp/intercept_pmkid_') or '..' in capture_file:
        return jsonify({'status': 'error', 'message': 'Invalid capture file path'})

    if not os.path.exists(capture_file):
        return jsonify({'pmkid_found': False, 'file_exists': False})

    file_size = os.path.getsize(capture_file)
    pmkid_found = False

    try:
        hash_file = capture_file.replace('.pcapng', '.22000')
        result = subprocess.run(
            ['hcxpcapngtool', '-o', hash_file, capture_file],
            capture_output=True, text=True, timeout=10
        )
        if os.path.exists(hash_file) and os.path.getsize(hash_file) > 0:
            pmkid_found = True
    except FileNotFoundError:
        pmkid_found = file_size > 1000
    except Exception:
        pass

    return jsonify({
        'pmkid_found': pmkid_found,
        'file_exists': True,
        'file_size': file_size,
        'file': capture_file
    })


@wifi_bp.route('/pmkid/stop', methods=['POST'])
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

    return jsonify({'status': 'stopped'})


@wifi_bp.route('/networks')
def get_wifi_networks():
    """Get current list of discovered networks."""
    return jsonify({
        'networks': list(app_module.wifi_networks.values()),
        'clients': list(app_module.wifi_clients.values()),
        'handshakes': app_module.wifi_handshakes,
        'monitor_interface': app_module.wifi_monitor_interface
    })


@wifi_bp.route('/stream')
def stream_wifi():
    """SSE stream for WiFi events."""
    def generate():
        last_keepalive = time.time()
        keepalive_interval = 30.0

        while True:
            try:
                msg = app_module.wifi_queue.get(timeout=1)
                last_keepalive = time.time()
                yield format_sse(msg)
            except queue.Empty:
                now = time.time()
                if now - last_keepalive >= keepalive_interval:
                    yield format_sse({'type': 'keepalive'})
                    last_keepalive = now

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response
