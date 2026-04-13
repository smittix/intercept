"""
TSCM Sweep Routes

Handles /sweep/*, /status, /devices, /presets/*, /feed/*,
/capabilities, and /sweep/<id>/capabilities endpoints.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
from typing import Any

from flask import Response, jsonify, request

from data.tscm_frequencies import get_all_sweep_presets, get_sweep_preset
from routes.tscm import (
    _baseline_recorder,
    _emit_event,
    _start_sweep_internal,
    tscm_bp,
)
from utils.database import get_tscm_sweep, update_tscm_sweep
from utils.event_pipeline import process_event
from utils.sse import sse_stream_fanout

logger = logging.getLogger('intercept.tscm')


@tscm_bp.route('/status')
def tscm_status():
    """Check if any TSCM operation is currently running."""
    import routes.tscm as _tscm_pkg
    return jsonify({'running': _tscm_pkg._sweep_running})


@tscm_bp.route('/sweep/start', methods=['POST'])
def start_sweep():
    """Start a TSCM sweep."""
    data = request.get_json() or {}
    sweep_type = data.get('sweep_type', 'standard')
    baseline_id = data.get('baseline_id')
    if baseline_id in ('', None):
        baseline_id = None
    wifi_enabled = data.get('wifi', True)
    bt_enabled = data.get('bluetooth', True)
    rf_enabled = data.get('rf', True)
    verbose_results = bool(data.get('verbose_results', False))

    # Get interface selections
    wifi_interface = data.get('wifi_interface', '')
    bt_interface = data.get('bt_interface', '')
    sdr_device = data.get('sdr_device')

    # Validate custom frequency ranges if provided
    custom_ranges = None
    if sweep_type == 'custom':
        raw_ranges = data.get('custom_ranges') or []
        validated = []
        for rng in raw_ranges:
            try:
                start = float(rng.get('start', 0))
                end = float(rng.get('end', 0))
                step = float(rng.get('step', 0.1))
                if 0 < start < end <= 6000:
                    validated.append({'start': start, 'end': end, 'step': step,
                                      'name': rng.get('name') or f'{start:.0f}–{end:.0f} MHz'})
            except (TypeError, ValueError):
                pass
        if not validated:
            return jsonify({'status': 'error', 'message': 'custom sweep requires valid start/end MHz'}), 400
        custom_ranges = validated

    result = _start_sweep_internal(
        sweep_type=sweep_type,
        baseline_id=baseline_id,
        wifi_enabled=wifi_enabled,
        bt_enabled=bt_enabled,
        rf_enabled=rf_enabled,
        wifi_interface=wifi_interface,
        bt_interface=bt_interface,
        sdr_device=sdr_device,
        verbose_results=verbose_results,
        custom_ranges=custom_ranges,
    )
    http_status = result.pop('http_status', 200)
    return jsonify(result), http_status


@tscm_bp.route('/sweep/stop', methods=['POST'])
def stop_sweep():
    """Stop the current TSCM sweep."""
    import routes.tscm as _tscm_pkg

    if not _tscm_pkg._sweep_running:
        return jsonify({'status': 'error', 'message': 'No sweep running'})

    _tscm_pkg._sweep_running = False

    if _tscm_pkg._current_sweep_id:
        update_tscm_sweep(_tscm_pkg._current_sweep_id, status='aborted', completed=True)

    _emit_event('sweep_stopped', {'reason': 'user_requested'})

    logger.info("TSCM sweep stopped by user")

    return jsonify({'status': 'success', 'message': 'Sweep stopped'})


@tscm_bp.route('/sweep/status')
def sweep_status():
    """Get current sweep status."""
    import routes.tscm as _tscm_pkg

    status = {
        'running': _tscm_pkg._sweep_running,
        'sweep_id': _tscm_pkg._current_sweep_id,
    }

    if _tscm_pkg._current_sweep_id:
        sweep = get_tscm_sweep(_tscm_pkg._current_sweep_id)
        if sweep:
            status['sweep'] = sweep

    return jsonify(status)


@tscm_bp.route('/sweep/stream')
def sweep_stream():
    """SSE stream for real-time sweep updates."""

    import routes.tscm as _tscm_pkg

    def _on_msg(msg: dict[str, Any]) -> None:
        process_event('tscm', msg, msg.get('type'))

    return Response(
        sse_stream_fanout(
            source_queue=_tscm_pkg.tscm_queue,
            channel_key='tscm',
            timeout=1.0,
            keepalive_interval=30.0,
            on_message=_on_msg,
        ),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@tscm_bp.route('/devices')
def get_tscm_devices():
    """Get available scanning devices for TSCM sweeps."""
    devices = {
        'wifi_interfaces': [],
        'bt_adapters': [],
        'sdr_devices': []
    }

    # Detect WiFi interfaces
    if platform.system() == 'Darwin':  # macOS
        try:
            result = subprocess.run(
                ['networksetup', '-listallhardwareports'],
                capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.split('\n')
            for i, line in enumerate(lines):
                if 'Wi-Fi' in line or 'AirPort' in line:
                    # Get the hardware port name (e.g., "Wi-Fi")
                    port_name = line.replace('Hardware Port:', '').strip()
                    for j in range(i + 1, min(i + 3, len(lines))):
                        if 'Device:' in lines[j]:
                            device = lines[j].split('Device:')[1].strip()
                            devices['wifi_interfaces'].append({
                                'name': device,
                                'display_name': f'{port_name} ({device})',
                                'type': 'internal',
                                'monitor_capable': False
                            })
                            break
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
    else:  # Linux
        try:
            result = subprocess.run(
                ['iw', 'dev'],
                capture_output=True, text=True, timeout=5
            )
            current_iface = None
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith('Interface'):
                    current_iface = line.split()[1]
                elif current_iface and 'type' in line:
                    iface_type = line.split()[-1]
                    devices['wifi_interfaces'].append({
                        'name': current_iface,
                        'display_name': f'Wireless ({current_iface}) - {iface_type}',
                        'type': iface_type,
                        'monitor_capable': True
                    })
                    current_iface = None
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            # Fall back to iwconfig
            try:
                result = subprocess.run(
                    ['iwconfig'],
                    capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.split('\n'):
                    if 'IEEE 802.11' in line:
                        iface = line.split()[0]
                        devices['wifi_interfaces'].append({
                            'name': iface,
                            'display_name': f'Wireless ({iface})',
                            'type': 'managed',
                            'monitor_capable': True
                        })
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass

    # Detect Bluetooth adapters
    if platform.system() == 'Linux':
        try:
            result = subprocess.run(
                ['hciconfig'],
                capture_output=True, text=True, timeout=5
            )
            blocks = re.split(r'(?=^hci\d+:)', result.stdout, flags=re.MULTILINE)
            for _idx, block in enumerate(blocks):
                if block.strip():
                    first_line = block.split('\n')[0]
                    match = re.match(r'(hci\d+):', first_line)
                    if match:
                        iface_name = match.group(1)
                        is_up = 'UP RUNNING' in block or '\tUP ' in block
                        devices['bt_adapters'].append({
                            'name': iface_name,
                            'display_name': f'Bluetooth Adapter ({iface_name})',
                            'type': 'hci',
                            'status': 'up' if is_up else 'down'
                        })
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            # Try bluetoothctl as fallback
            try:
                result = subprocess.run(
                    ['bluetoothctl', 'list'],
                    capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.split('\n'):
                    if 'Controller' in line:
                        # Format: Controller XX:XX:XX:XX:XX:XX Name
                        parts = line.split()
                        if len(parts) >= 3:
                            addr = parts[1]
                            name = ' '.join(parts[2:]) if len(parts) > 2 else 'Bluetooth'
                            devices['bt_adapters'].append({
                                'name': addr,
                                'display_name': f'{name} ({addr[-8:]})',
                                'type': 'controller',
                                'status': 'available'
                            })
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass
    elif platform.system() == 'Darwin':
        # macOS has built-in Bluetooth - get more info via system_profiler
        try:
            result = subprocess.run(
                ['system_profiler', 'SPBluetoothDataType'],
                capture_output=True, text=True, timeout=10
            )
            # Extract controller info
            bt_name = 'Built-in Bluetooth'
            bt_addr = ''
            for line in result.stdout.split('\n'):
                if 'Address:' in line:
                    bt_addr = line.split('Address:')[1].strip()
                    break
            devices['bt_adapters'].append({
                'name': 'default',
                'display_name': f'{bt_name}' + (f' ({bt_addr[-8:]})' if bt_addr else ''),
                'type': 'macos',
                'status': 'available'
            })
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            devices['bt_adapters'].append({
                'name': 'default',
                'display_name': 'Built-in Bluetooth',
                'type': 'macos',
                'status': 'available'
            })

    # Detect SDR devices
    try:
        from utils.sdr import SDRFactory
        sdr_list = SDRFactory.detect_devices()
        for sdr in sdr_list:
            # SDRDevice is a dataclass with attributes, not a dict
            sdr_type_name = sdr.sdr_type.value if hasattr(sdr.sdr_type, 'value') else str(sdr.sdr_type)
            # Create a friendly display name
            display_name = sdr.name
            if sdr.serial and sdr.serial not in ('N/A', 'Unknown'):
                display_name = f'{sdr.name} (SN: {sdr.serial[-8:]})'
            devices['sdr_devices'].append({
                'index': sdr.index,
                'name': sdr.name,
                'display_name': display_name,
                'type': sdr_type_name,
                'serial': sdr.serial,
                'driver': sdr.driver
            })
    except ImportError:
        logger.debug("SDR module not available")
    except Exception as e:
        logger.warning(f"Error detecting SDR devices: {e}")

    # Check if running as root
    from flask import current_app
    running_as_root = current_app.config.get('RUNNING_AS_ROOT', os.geteuid() == 0)

    warnings = []
    if not running_as_root:
        warnings.append({
            'type': 'privileges',
            'message': 'Not running as root. WiFi monitor mode and some Bluetooth features require sudo.',
            'action': 'Run with: sudo -E venv/bin/python intercept.py'
        })

    return jsonify({
        'status': 'success',
        'devices': devices,
        'running_as_root': running_as_root,
        'warnings': warnings
    })


# =============================================================================
# Preset Endpoints
# =============================================================================

@tscm_bp.route('/presets')
def list_presets():
    """List available sweep presets."""
    presets = get_all_sweep_presets()
    return jsonify({'status': 'success', 'presets': presets})


@tscm_bp.route('/presets/<preset_name>')
def get_preset(preset_name: str):
    """Get details for a specific preset."""
    preset = get_sweep_preset(preset_name)
    if not preset:
        return jsonify({'status': 'error', 'message': 'Preset not found'}), 404

    return jsonify({'status': 'success', 'preset': preset})


# =============================================================================
# Data Feed Endpoints (for adding data during sweeps/baselines)
# =============================================================================

@tscm_bp.route('/feed/wifi', methods=['POST'])
def feed_wifi():
    """Feed WiFi device data for baseline recording."""

    data = request.get_json()
    if data:
        if data.get('is_client'):
            _baseline_recorder.add_wifi_client(data)
        else:
            _baseline_recorder.add_wifi_device(data)
    return jsonify({'status': 'success'})


@tscm_bp.route('/feed/bluetooth', methods=['POST'])
def feed_bluetooth():
    """Feed Bluetooth device data for baseline recording."""

    data = request.get_json()
    if data:
        _baseline_recorder.add_bt_device(data)
    return jsonify({'status': 'success'})


@tscm_bp.route('/feed/rf', methods=['POST'])
def feed_rf():
    """Feed RF signal data for baseline recording."""

    data = request.get_json()
    if data:
        _baseline_recorder.add_rf_signal(data)
    return jsonify({'status': 'success'})


# =============================================================================
# Capabilities & Coverage Endpoints
# =============================================================================

@tscm_bp.route('/capabilities')
def get_capabilities():
    """
    Get current system capabilities for TSCM sweeping.

    Returns what the system CAN and CANNOT detect based on OS,
    privileges, adapters, and SDR hardware.
    """
    try:
        from utils.tscm.advanced import detect_sweep_capabilities

        wifi_interface = request.args.get('wifi_interface', '')
        bt_adapter = request.args.get('bt_adapter', '')

        caps = detect_sweep_capabilities(
            wifi_interface=wifi_interface,
            bt_adapter=bt_adapter
        )

        return jsonify({
            'status': 'success',
            'capabilities': caps.to_dict()
        })

    except Exception as e:
        logger.error(f"Get capabilities error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@tscm_bp.route('/sweep/<int:sweep_id>/capabilities')
def get_sweep_stored_capabilities(sweep_id: int):
    """Get stored capabilities for a specific sweep."""
    from utils.database import get_sweep_capabilities

    caps = get_sweep_capabilities(sweep_id)
    if not caps:
        return jsonify({'status': 'error', 'message': 'No capabilities stored for this sweep'}), 404

    return jsonify({
        'status': 'success',
        'capabilities': caps
    })
