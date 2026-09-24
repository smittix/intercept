"""
Bluetooth API v2 - Unified scanning with DBus/BlueZ and fallbacks.

Provides REST endpoints and SSE streaming for Bluetooth device discovery,
aggregation, and heuristics.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import queue
import threading
import time
from collections.abc import Generator
from datetime import datetime

from flask import Blueprint, Response, jsonify, request

from utils.bluetooth import (
    BTDeviceAggregate,
    check_capabilities,
    get_bluetooth_scanner,
)
from utils.bluetooth.scanner import sse_event
from utils.database import get_db
from utils.responses import api_error
from utils.sse import format_sse, subscribe_fanout_queue

logger = logging.getLogger("intercept.bluetooth_v2")

# Blueprint
bluetooth_v2_bp = Blueprint("bluetooth_v2", __name__, url_prefix="/api/bluetooth")

# Seen-before tracking
_bt_seen_cache: set[str] = set()
_bt_session_seen: set[str] = set()
_bt_seen_lock = threading.Lock()

# =============================================================================
# DATABASE FUNCTIONS
# =============================================================================


def init_bt_tables() -> None:
    """Initialize Bluetooth-specific database tables."""
    with get_db() as conn:
        # Bluetooth baselines
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bt_baselines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                device_count INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT 0
            )
        """)

        # Baseline device snapshots
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bt_baseline_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                baseline_id INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                address TEXT NOT NULL,
                address_type TEXT,
                name TEXT,
                manufacturer_id INTEGER,
                manufacturer_name TEXT,
                protocol TEXT,
                FOREIGN KEY (baseline_id) REFERENCES bt_baselines(id) ON DELETE CASCADE,
                UNIQUE(baseline_id, device_id)
            )
        """)

        # Observation history for long-term tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bt_observation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                rssi INTEGER,
                seen_count INTEGER
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bt_obs_device_time
            ON bt_observation_history(device_id, timestamp)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bt_baseline_devices_baseline
            ON bt_baseline_devices(baseline_id)
        """)


def get_active_baseline_id() -> int | None:
    """Get the ID of the active baseline."""
    with get_db() as conn:
        cursor = conn.execute("SELECT id FROM bt_baselines WHERE is_active = 1 LIMIT 1")
        row = cursor.fetchone()
        return row["id"] if row else None


def get_baseline_device_ids(baseline_id: int) -> set[str]:
    """Get device IDs from a baseline."""
    with get_db() as conn:
        cursor = conn.execute("SELECT device_id FROM bt_baseline_devices WHERE baseline_id = ?", (baseline_id,))
        return {row["device_id"] for row in cursor}


def save_baseline(name: str, devices: list[BTDeviceAggregate]) -> int:
    """Save current devices as a new baseline."""
    with get_db() as conn:
        # Deactivate existing baselines
        conn.execute("UPDATE bt_baselines SET is_active = 0")

        # Create new baseline
        cursor = conn.execute(
            "INSERT INTO bt_baselines (name, device_count, is_active) VALUES (?, ?, 1)", (name, len(devices))
        )
        baseline_id = cursor.lastrowid

        # Save device snapshots
        for device in devices:
            conn.execute(
                """
                INSERT INTO bt_baseline_devices
                (baseline_id, device_id, address, address_type, name,
                 manufacturer_id, manufacturer_name, protocol)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    baseline_id,
                    device.device_id,
                    device.address,
                    device.address_type,
                    device.name,
                    device.manufacturer_id,
                    device.manufacturer_name,
                    device.protocol,
                ),
            )

        return baseline_id


def clear_active_baseline() -> bool:
    """Clear the active baseline."""
    with get_db() as conn:
        cursor = conn.execute("UPDATE bt_baselines SET is_active = 0 WHERE is_active = 1")
        return cursor.rowcount > 0


def get_all_baselines() -> list[dict]:
    """Get all baselines."""
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT id, name, created_at, device_count, is_active
            FROM bt_baselines
            ORDER BY created_at DESC
        """)
        return [dict(row) for row in cursor]


def save_observation_history(device: BTDeviceAggregate) -> None:
    """Save device observation to history."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO bt_observation_history (device_id, rssi, seen_count)
            VALUES (?, ?, ?)
        """,
            (device.device_id, device.rssi_current, device.seen_count),
        )


def load_seen_device_ids() -> set[str]:
    """Load distinct device IDs from history for seen-before tracking."""
    with get_db() as conn:
        cursor = conn.execute("SELECT DISTINCT device_id FROM bt_observation_history")
        return {row["device_id"] for row in cursor}


# =============================================================================
# HELPERS
# =============================================================================


def device_to_dict(device: BTDeviceAggregate) -> dict:
    """Serialize a BTDeviceAggregate to a JSON-safe dict with heuristics flattened to top level."""
    d = device.to_dict()
    heuristics = d.pop("heuristics", {})
    d.update(heuristics)
    return d


# =============================================================================
# API ENDPOINTS
# =============================================================================


@bluetooth_v2_bp.route("/capabilities", methods=["GET"])
def get_capabilities():
    """
    Get Bluetooth system capabilities.

    Returns:
        JSON with capability information including adapters, backends, and issues.
    """
    caps = check_capabilities()
    return jsonify(caps.to_dict())


@bluetooth_v2_bp.route("/scan/start", methods=["POST"])
def start_scan():
    """
    Start Bluetooth scanning.

    Request JSON:
        - mode: Scanner mode ('auto', 'dbus', 'bleak', 'hcitool', 'bluetoothctl')
        - duration_s: Scan duration in seconds (optional, None for indefinite)
        - adapter_id: Adapter path/name (optional)
        - transport: BLE transport ('auto', 'bredr', 'le')
        - rssi_threshold: Minimum RSSI for discovery

    Returns:
        JSON with scan status.
    """
    data = request.get_json() or {}

    mode = data.get("mode", "auto")
    duration_s = data.get("duration_s")
    adapter_id = data.get("adapter_id")
    transport = data.get("transport", "auto")
    rssi_threshold = data.get("rssi_threshold", -100)

    # Validate mode
    valid_modes = ("auto", "dbus", "bleak", "hcitool", "bluetoothctl", "ubertooth")
    if mode not in valid_modes:
        return api_error(f"Invalid mode. Must be one of: {valid_modes}", 400)

    # Get scanner instance
    scanner = get_bluetooth_scanner(adapter_id)

    # Initialize database tables if needed
    init_bt_tables()

    def _handle_seen_before(device: BTDeviceAggregate) -> None:
        try:
            with _bt_seen_lock:
                device.seen_before = device.device_id in _bt_seen_cache
                if device.device_id not in _bt_session_seen:
                    save_observation_history(device)
                    _bt_session_seen.add(device.device_id)
        except Exception as e:
            logger.debug(f"BT seen-before update failed: {e}")

    # Setup seen-before callback
    if _handle_seen_before not in scanner._on_device_updated_callbacks:
        scanner.add_device_callback(_handle_seen_before)

    # Ensure cache is initialized
    with _bt_seen_lock:
        if not _bt_seen_cache:
            _bt_seen_cache.update(load_seen_device_ids())

    # Check if already scanning
    if scanner.is_scanning:
        return jsonify({"status": "already_scanning", "scan_status": scanner.get_status().to_dict()})

    # Refresh seen-before cache and reset session set for a new scan
    with _bt_seen_lock:
        _bt_seen_cache.clear()
        _bt_seen_cache.update(load_seen_device_ids())
        _bt_session_seen.clear()

    # Load active baseline if exists
    baseline_id = get_active_baseline_id()
    if baseline_id:
        device_ids = get_baseline_device_ids(baseline_id)
        if device_ids:
            scanner._aggregator.load_baseline(device_ids, datetime.now())

    # Start scan
    success = scanner.start_scan(
        mode=mode,
        duration_s=duration_s,
        transport=transport,
        rssi_threshold=rssi_threshold,
    )

    if success:
        status = scanner.get_status()
        return jsonify(
            {
                "status": "started",
                "mode": status.mode,
                "backend": status.backend,
                "adapter_id": status.adapter_id,
            }
        )
    else:
        status = scanner.get_status()
        return jsonify(
            {
                "status": "failed",
                "error": status.error or "Failed to start scan",
            }
        ), 500


@bluetooth_v2_bp.route("/scan/stop", methods=["POST"])
def stop_scan():
    """
    Stop Bluetooth scanning.

    Returns:
        JSON with status.
    """
    scanner = get_bluetooth_scanner()
    scanner.stop_scan()

    return jsonify({"status": "stopped"})


@bluetooth_v2_bp.route("/scan/status", methods=["GET"])
def get_scan_status():
    """
    Get current scan status.

    Returns:
        JSON with scan status including elapsed time and device count.
    """
    scanner = get_bluetooth_scanner()
    status = scanner.get_status()
    return jsonify(status.to_dict())


@bluetooth_v2_bp.route("/devices", methods=["GET"])
def list_devices():
    """
    List discovered Bluetooth devices.

    Query parameters:
        - sort: Sort field ('last_seen', 'rssi_current', 'name', 'seen_count')
        - order: Sort order ('asc', 'desc')
        - min_rssi: Minimum RSSI filter
        - protocol: Protocol filter ('ble', 'classic')
        - max_age: Maximum age in seconds
        - heuristic: Filter by heuristic flag ('new', 'persistent', etc.)

    Returns:
        JSON array of device summaries.
    """
    scanner = get_bluetooth_scanner()

    # Parse query parameters
    sort_by = request.args.get("sort", "last_seen")
    sort_desc = request.args.get("order", "desc").lower() != "asc"
    min_rssi = request.args.get("min_rssi", type=int)
    protocol = request.args.get("protocol")
    max_age = request.args.get("max_age", 300, type=float)
    heuristic_filter = request.args.get("heuristic")

    # Get devices
    devices = scanner.get_devices(
        sort_by=sort_by,
        sort_desc=sort_desc,
        min_rssi=min_rssi,
        protocol=protocol,
        max_age_seconds=max_age,
    )

    # Apply heuristic filter if specified
    if heuristic_filter:
        devices = [d for d in devices if heuristic_filter in d.heuristic_flags]

    return jsonify(
        {
            "count": len(devices),
            "devices": [d.to_summary_dict() for d in devices],
        }
    )


@bluetooth_v2_bp.route("/devices/<device_id>", methods=["GET"])
def get_device(device_id: str):
    """
    Get detailed information about a specific device.

    Path parameters:
        - device_id: Device identifier (address:address_type)

    Returns:
        JSON with full device details including RSSI history.
    """
    scanner = get_bluetooth_scanner()
    device = scanner.get_device(device_id)

    if not device:
        return api_error("Device not found", 404)

    return jsonify(device.to_dict())


# =============================================================================
# TRACKER DETECTION ENDPOINTS (v2)
# =============================================================================


@bluetooth_v2_bp.route("/trackers", methods=["GET"])
def list_trackers():
    """
    List detected tracker devices with enriched tracker data.

    This is the v2 tracker endpoint that provides comprehensive
    tracker detection results including confidence scores and evidence.

    Query parameters:
        - min_confidence: Minimum confidence ('high', 'medium', 'low')
        - max_age: Maximum age in seconds (default: 300)
        - include_risk: Include risk analysis (default: true)

    Returns:
        JSON with detected trackers and their analysis.
    """
    scanner = get_bluetooth_scanner()

    # Parse query parameters
    min_confidence = request.args.get("min_confidence", "low")
    max_age = request.args.get("max_age", 300, type=float)
    include_risk = request.args.get("include_risk", "true").lower() == "true"

    # Get all devices
    devices = scanner.get_devices(max_age_seconds=max_age)

    # Filter to only trackers
    trackers = [d for d in devices if d.is_tracker]

    # Filter by confidence level if specified
    confidence_order = {"high": 3, "medium": 2, "low": 1, "none": 0}
    min_conf_level = confidence_order.get(min_confidence.lower(), 1)
    trackers = [t for t in trackers if confidence_order.get(t.tracker_confidence, 0) >= min_conf_level]

    # Build response
    tracker_list = []
    for device in trackers:
        tracker_info = {
            "device_id": device.device_id,
            "device_key": device.device_key,
            "address": device.address,
            "address_type": device.address_type,
            "name": device.name,
            # Tracker detection details
            "tracker": {
                "type": device.tracker_type,
                "name": device.tracker_name,
                "confidence": device.tracker_confidence,
                "confidence_score": round(device.tracker_confidence_score, 2),
                "evidence": device.tracker_evidence,
            },
            # Location/proximity
            "rssi_current": device.rssi_current,
            "rssi_ema": round(device.rssi_ema, 1) if device.rssi_ema else None,
            "proximity_band": device.proximity_band,
            "estimated_distance_m": round(device.estimated_distance_m, 2) if device.estimated_distance_m else None,
            # Timing
            "first_seen": device.first_seen.isoformat(),
            "last_seen": device.last_seen.isoformat(),
            "age_seconds": round(device.age_seconds, 1),
            "seen_count": device.seen_count,
            "duration_seconds": round(device.duration_seconds, 1),
            # Status
            "is_new": device.is_new,
            "in_baseline": device.in_baseline,
            # Fingerprint for cross-MAC tracking
            "fingerprint_id": device.payload_fingerprint_id,
        }

        # Include risk analysis if requested
        if include_risk:
            tracker_info["risk_analysis"] = {
                "risk_score": round(device.risk_score, 2),
                "risk_factors": device.risk_factors,
            }

        tracker_list.append(tracker_info)

    # Sort by risk score (highest first), then confidence
    tracker_list.sort(
        key=lambda t: (
            t.get("risk_analysis", {}).get("risk_score", 0),
            confidence_order.get(t["tracker"]["confidence"], 0),
        ),
        reverse=True,
    )

    return jsonify(
        {
            "count": len(tracker_list),
            "scan_active": scanner.is_scanning,
            "trackers": tracker_list,
            "summary": {
                "high_confidence": sum(1 for t in tracker_list if t["tracker"]["confidence"] == "high"),
                "medium_confidence": sum(1 for t in tracker_list if t["tracker"]["confidence"] == "medium"),
                "low_confidence": sum(1 for t in tracker_list if t["tracker"]["confidence"] == "low"),
                "high_risk": sum(1 for t in tracker_list if t.get("risk_analysis", {}).get("risk_score", 0) >= 0.5),
            },
        }
    )


@bluetooth_v2_bp.route("/trackers/<device_id>", methods=["GET"])
def get_tracker_detail(device_id: str):
    """
    Get detailed tracker information for investigation.

    Provides comprehensive data about a specific tracker including:
    - Full tracker detection analysis
    - Risk assessment with factors
    - RSSI history and timeline
    - Raw advertising payload data
    - Fingerprint information

    Path parameters:
        - device_id: Device identifier (address:address_type)

    Returns:
        JSON with full tracker investigation data.
    """
    scanner = get_bluetooth_scanner()
    device = scanner.get_device(device_id)

    if not device:
        return api_error("Device not found", 404)

    # Get RSSI history for timeline
    rssi_history = device.get_rssi_history(max_points=100)

    # Build comprehensive response
    return jsonify(
        {
            "device_id": device.device_id,
            "device_key": device.device_key,
            "address": device.address,
            "address_type": device.address_type,
            "name": device.name,
            "manufacturer_name": device.manufacturer_name,
            "manufacturer_id": device.manufacturer_id,
            # Tracker detection
            "tracker": {
                "is_tracker": device.is_tracker,
                "type": device.tracker_type,
                "name": device.tracker_name,
                "confidence": device.tracker_confidence,
                "confidence_score": round(device.tracker_confidence_score, 2),
                "evidence": device.tracker_evidence,
            },
            # Risk analysis
            "risk_analysis": {
                "risk_score": round(device.risk_score, 2),
                "risk_factors": device.risk_factors,
                "warning": "Risk scores are heuristic indicators only. They do NOT prove malicious intent.",
            },
            # Fingerprint (for MAC randomization tracking)
            "fingerprint": {
                "id": device.payload_fingerprint_id,
                "stability": round(device.payload_fingerprint_stability, 2),
                "note": "Fingerprints help track devices across MAC address changes but are probabilistic.",
            },
            # Signal data
            "signal": {
                "rssi_current": device.rssi_current,
                "rssi_median": round(device.rssi_median, 1) if device.rssi_median else None,
                "rssi_ema": round(device.rssi_ema, 1) if device.rssi_ema else None,
                "rssi_min": device.rssi_min,
                "rssi_max": device.rssi_max,
                "rssi_variance": round(device.rssi_variance, 2) if device.rssi_variance else None,
                "tx_power": device.tx_power,
            },
            # Proximity
            "proximity": {
                "band": device.proximity_band,
                "estimated_distance_m": round(device.estimated_distance_m, 2) if device.estimated_distance_m else None,
                "confidence": round(device.distance_confidence, 2),
            },
            # Timeline / sightings
            "timeline": {
                "first_seen": device.first_seen.isoformat(),
                "last_seen": device.last_seen.isoformat(),
                "age_seconds": round(device.age_seconds, 1),
                "duration_seconds": round(device.duration_seconds, 1),
                "seen_count": device.seen_count,
                "seen_rate": round(device.seen_rate, 2),
                "rssi_history": rssi_history,
            },
            # Raw advertisement data for investigation
            "raw_data": {
                "manufacturer_id_hex": f"0x{device.manufacturer_id:04X}" if device.manufacturer_id else None,
                "manufacturer_data_hex": device.manufacturer_bytes.hex() if device.manufacturer_bytes else None,
                "service_uuids": device.service_uuids,
                "service_data": {k: v.hex() for k, v in device.service_data.items()},
                "appearance": device.appearance,
            },
            # Heuristics
            "heuristics": {
                "is_new": device.is_new,
                "is_persistent": device.is_persistent,
                "is_beacon_like": device.is_beacon_like,
                "is_strong_stable": device.is_strong_stable,
                "has_random_address": device.has_random_address,
                "is_randomized_mac": device.is_randomized_mac,
            },
            # Baseline status
            "baseline": {
                "in_baseline": device.in_baseline,
                "baseline_id": device.baseline_id,
            },
        }
    )


@bluetooth_v2_bp.route("/diagnostics", methods=["GET"])
def get_diagnostics():
    """
    Get Bluetooth system diagnostics for troubleshooting.

    Returns detailed information about:
    - Adapter status and capabilities
    - BlueZ version and DBus access
    - Permissions and access issues
    - Available scan backends
    - Recent errors

    Returns:
        JSON with diagnostic information.
    """
    import os
    import subprocess

    caps = check_capabilities()

    diagnostics = {
        "system": {
            "is_root": os.geteuid() == 0 if hasattr(os, "geteuid") else False,
            "platform": os.uname().sysname if hasattr(os, "uname") else "unknown",
        },
        "bluez": {
            "has_bluez": caps.has_bluez,
            "version": caps.bluez_version,
            "has_dbus": caps.has_dbus,
        },
        "adapters": {
            "count": len(caps.adapters),
            "default": caps.default_adapter,
            "list": caps.adapters,
        },
        "permissions": {
            "has_bluetooth_permission": caps.has_bluetooth_permission,
            "is_soft_blocked": caps.is_soft_blocked,
            "is_hard_blocked": caps.is_hard_blocked,
        },
        "backends": {
            "recommended": caps.recommended_backend,
            "available": {
                "dbus": caps.has_dbus and caps.has_bluez,
                "bleak": caps.has_bleak,
                "hcitool": caps.has_hcitool,
                "bluetoothctl": caps.has_bluetoothctl,
                "btmgmt": caps.has_btmgmt,
            },
        },
        "can_scan": caps.can_scan,
        "issues": caps.issues,
        "recommendations": [],
    }

    # Add recommendations based on issues
    if not caps.can_scan:
        diagnostics["recommendations"].append(
            "No scanning backends available. Install BlueZ or ensure Bluetooth adapter is present."
        )

    if caps.is_soft_blocked:
        diagnostics["recommendations"].append("Bluetooth is soft-blocked. Run: sudo rfkill unblock bluetooth")

    if caps.is_hard_blocked:
        diagnostics["recommendations"].append(
            "Bluetooth is hard-blocked (hardware switch). Enable Bluetooth on your device."
        )

    if not caps.has_bluetooth_permission and not diagnostics["system"]["is_root"]:
        diagnostics["recommendations"].append(
            "May need elevated permissions for BLE scanning. Try running with sudo or add user to bluetooth group."
        )

    if caps.has_dbus and caps.has_bluez and len(caps.adapters) == 0:
        diagnostics["recommendations"].append(
            "BlueZ is available but no adapters found. Check if Bluetooth adapter is connected and enabled."
        )

    # Check for btmon availability (useful for debugging)
    try:
        result = subprocess.run(["which", "btmon"], capture_output=True, timeout=2)
        diagnostics["backends"]["available"]["btmon"] = result.returncode == 0
    except Exception:
        diagnostics["backends"]["available"]["btmon"] = False

    return jsonify(diagnostics)


@bluetooth_v2_bp.route("/baseline/set", methods=["POST"])
def set_baseline():
    """
    Set current devices as baseline.

    Request JSON:
        - name: Baseline name (optional)

    Returns:
        JSON with baseline info.
    """
    data = request.get_json() or {}
    name = data.get("name", f"Baseline {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    scanner = get_bluetooth_scanner()

    # Initialize tables if needed
    init_bt_tables()

    # Get current devices and save to database
    devices = scanner.get_devices()
    baseline_id = save_baseline(name, devices)

    # Update scanner's in-memory baseline
    device_count = scanner.set_baseline()

    return jsonify(
        {
            "status": "success",
            "baseline_id": baseline_id,
            "name": name,
            "device_count": device_count,
        }
    )


@bluetooth_v2_bp.route("/baseline/clear", methods=["POST"])
def clear_baseline():
    """
    Clear the active baseline.

    Returns:
        JSON with status.
    """
    scanner = get_bluetooth_scanner()

    # Clear in database
    init_bt_tables()
    cleared = clear_active_baseline()

    # Clear in scanner
    scanner.clear_baseline()

    return jsonify(
        {
            "status": "cleared" if cleared else "no_baseline",
        }
    )


@bluetooth_v2_bp.route("/baseline/list", methods=["GET"])
def list_baselines():
    """
    List all saved baselines.

    Returns:
        JSON array of baselines.
    """
    init_bt_tables()
    baselines = get_all_baselines()
    return jsonify(
        {
            "count": len(baselines),
            "baselines": baselines,
        }
    )


@bluetooth_v2_bp.route("/export", methods=["GET"])
def export_devices():
    """
    Export devices in CSV or JSON format.

    Query parameters:
        - format: Export format ('csv', 'json')

    Returns:
        CSV or JSON file download.
    """
    export_format = request.args.get("format", "json").lower()
    scanner = get_bluetooth_scanner()
    devices = scanner.get_devices()

    if export_format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow(
            [
                "device_id",
                "address",
                "address_type",
                "protocol",
                "name",
                "manufacturer_name",
                "rssi_current",
                "rssi_median",
                "range_band",
                "first_seen",
                "last_seen",
                "seen_count",
                "heuristic_flags",
                "in_baseline",
            ]
        )

        # Data rows
        for device in devices:
            writer.writerow(
                [
                    device.device_id,
                    device.address,
                    device.address_type,
                    device.protocol,
                    device.name or "",
                    device.manufacturer_name or "",
                    device.rssi_current or "",
                    round(device.rssi_median, 1) if device.rssi_median else "",
                    device.range_band,
                    device.first_seen.isoformat(),
                    device.last_seen.isoformat(),
                    device.seen_count,
                    ",".join(device.heuristic_flags),
                    "yes" if device.in_baseline else "no",
                ]
            )

        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=bluetooth_devices_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            },
        )

    else:  # JSON
        data = {
            "exported_at": datetime.now().isoformat(),
            "device_count": len(devices),
            "devices": [d.to_dict() for d in devices],
        }
        return Response(
            json.dumps(data, indent=2),
            mimetype="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=bluetooth_devices_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            },
        )


@bluetooth_v2_bp.route("/stream", methods=["GET"])
def stream_events():
    """SSE event stream for real-time device updates."""
    scanner = get_bluetooth_scanner()

    subscriber, unsubscribe = subscribe_fanout_queue(
        source_queue=scanner._event_queue,
        channel_key="bluetooth_v2",
        source_timeout=1.0,
    )

    def event_generator() -> Generator[str, None, None]:
        last_keepalive = time.time()
        yield format_sse({"type": "keepalive"})
        try:
            while True:
                try:
                    event = subscriber.get(timeout=1.0)
                    last_keepalive = time.time()
                    event_name, event_data = sse_event(event)
                    yield format_sse(event_data, event=event_name)
                except queue.Empty:
                    now = time.time()
                    if now - last_keepalive >= 30.0:
                        yield format_sse({"type": "keepalive"})
                        last_keepalive = now
        finally:
            unsubscribe()

    return Response(
        event_generator(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@bluetooth_v2_bp.route("/clear", methods=["POST"])
def clear_devices():
    """
    Clear all tracked devices (does not affect baseline).

    Returns:
        JSON with status.
    """
    scanner = get_bluetooth_scanner()
    scanner.clear_devices()

    return jsonify({"status": "cleared"})


@bluetooth_v2_bp.route("/prune", methods=["POST"])
def prune_stale():
    """
    Prune stale devices.

    Request JSON:
        - max_age: Maximum age in seconds (default: 300)

    Returns:
        JSON with count of pruned devices.
    """
    data = request.get_json() or {}
    max_age = data.get("max_age", 300)

    scanner = get_bluetooth_scanner()
    pruned = scanner.prune_stale(max_age_seconds=max_age)

    return jsonify(
        {
            "status": "success",
            "pruned_count": pruned,
        }
    )


# =============================================================================
# TSCM INTEGRATION HELPER
# =============================================================================


def get_tscm_bluetooth_snapshot(duration: int = 8) -> list[dict]:
    """
    Get Bluetooth snapshot for TSCM integration.

    This is called from routes/tscm.py to get unified Bluetooth data.

    Args:
        duration: Scan duration in seconds.

    Returns:
        List of device dictionaries in TSCM format.
    """
    import logging

    logger = logging.getLogger("intercept.bluetooth_v2")

    scanner = get_bluetooth_scanner()

    # Start scan if not running
    if not scanner.is_scanning:
        logger.info(f"TSCM snapshot: Scanner not running, starting scan for {duration}s")
        scanner.start_scan(mode="auto", duration_s=duration)
        time.sleep(duration + 1)
    else:
        logger.info("TSCM snapshot: Scanner already running, getting current devices")

    devices = scanner.get_devices()
    logger.info(f"TSCM snapshot: get_devices() returned {len(devices)} devices")

    # Convert to TSCM format with tracker detection data
    tscm_devices = []
    for device in devices:
        manufacturer_name = device.manufacturer_name
        if (not manufacturer_name) or str(manufacturer_name).lower().startswith("unknown"):
            if device.address and not device.is_randomized_mac:
                try:
                    from data.oui import get_manufacturer

                    oui_vendor = get_manufacturer(device.address)
                    if oui_vendor and oui_vendor != "Unknown":
                        manufacturer_name = oui_vendor
                except Exception:
                    pass

        device_data = {
            "mac": device.address,
            "address_type": device.address_type,
            "device_key": device.device_key,
            "name": device.name or "Unknown",
            "rssi": device.rssi_current or -100,
            "rssi_median": device.rssi_median,
            "rssi_ema": round(device.rssi_ema, 1) if device.rssi_ema else None,
            "type": _classify_device_type(device),
            "manufacturer": manufacturer_name,
            "manufacturer_id": device.manufacturer_id,
            "manufacturer_data": device.manufacturer_bytes.hex() if device.manufacturer_bytes else None,
            "protocol": device.protocol,
            "first_seen": device.first_seen.isoformat(),
            "last_seen": device.last_seen.isoformat(),
            "seen_count": device.seen_count,
            "range_band": device.range_band,
            "proximity_band": device.proximity_band,
            "estimated_distance_m": round(device.estimated_distance_m, 2) if device.estimated_distance_m else None,
            "distance_confidence": round(device.distance_confidence, 2),
            "is_randomized_mac": device.is_randomized_mac,
            "threat_tags": device.threat_tags,
            "heuristics": {
                "is_new": device.is_new,
                "is_persistent": device.is_persistent,
                "is_beacon_like": device.is_beacon_like,
                "is_strong_stable": device.is_strong_stable,
                "has_random_address": device.has_random_address,
            },
            "in_baseline": device.in_baseline,
            # Tracker detection data (v2)
            "tracker": {
                "is_tracker": device.is_tracker,
                "type": device.tracker_type,
                "name": device.tracker_name,
                "confidence": device.tracker_confidence,
                "confidence_score": round(device.tracker_confidence_score, 2),
                "evidence": device.tracker_evidence,
            },
            # Risk analysis (v2)
            "risk_analysis": {
                "risk_score": round(device.risk_score, 2),
                "risk_factors": device.risk_factors,
            },
            # Fingerprint for cross-MAC tracking (v2)
            "fingerprint": {
                "id": device.payload_fingerprint_id,
                "stability": round(device.payload_fingerprint_stability, 2),
            },
            # Service UUIDs for analysis
            "service_uuids": device.service_uuids,
        }

        tscm_devices.append(device_data)

    return tscm_devices


# =============================================================================
# PROXIMITY & HEATMAP ENDPOINTS
# =============================================================================


@bluetooth_v2_bp.route("/proximity/snapshot", methods=["GET"])
def get_proximity_snapshot():
    """
    Get proximity snapshot for radar visualization.

    All active devices with proximity data including estimated distance,
    proximity band, and confidence scores.

    Query parameters:
        - max_age: Maximum age in seconds (default: 60)
        - min_confidence: Minimum distance confidence (default: 0)

    Returns:
        JSON with proximity data for all active devices.
    """
    scanner = get_bluetooth_scanner()
    max_age = request.args.get("max_age", 60, type=float)
    min_confidence = request.args.get("min_confidence", 0.0, type=float)

    devices = scanner.get_devices(max_age_seconds=max_age)

    # Filter by confidence if specified
    if min_confidence > 0:
        devices = [d for d in devices if d.distance_confidence >= min_confidence]

    # Build proximity snapshot
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "device_count": len(devices),
        "zone_counts": {
            "immediate": 0,
            "near": 0,
            "far": 0,
            "unknown": 0,
        },
        "devices": [],
    }

    for device in devices:
        # Count by zone
        band = device.proximity_band or "unknown"
        if band in snapshot["zone_counts"]:
            snapshot["zone_counts"][band] += 1
        else:
            snapshot["zone_counts"]["unknown"] += 1

        snapshot["devices"].append(
            {
                "device_key": device.device_key,
                "device_id": device.device_id,
                "name": device.name,
                "address": device.address,
                "rssi_current": device.rssi_current,
                "rssi_ema": round(device.rssi_ema, 1) if device.rssi_ema else None,
                "estimated_distance_m": round(device.estimated_distance_m, 2) if device.estimated_distance_m else None,
                "proximity_band": device.proximity_band,
                "distance_confidence": round(device.distance_confidence, 2),
                "is_new": device.is_new,
                "is_randomized_mac": device.is_randomized_mac,
                "in_baseline": device.in_baseline,
                "heuristic_flags": device.heuristic_flags,
                "last_seen": device.last_seen.isoformat(),
                "age_seconds": round(device.age_seconds, 1),
            }
        )

    return jsonify(snapshot)


@bluetooth_v2_bp.route("/heatmap/data", methods=["GET"])
def get_heatmap_data():
    """
    Get heatmap data for timeline visualization.

    Returns top N devices with downsampled RSSI timeseries.

    Query parameters:
        - top_n: Number of devices (default: 20)
        - window_minutes: Time window (default: 10)
        - bucket_seconds: Bucket size for downsampling (default: 10)
        - sort_by: Sort method - 'recency', 'strength', 'activity' (default: 'recency')

    Returns:
        JSON with device timeseries data for heatmap.
    """
    scanner = get_bluetooth_scanner()

    top_n = request.args.get("top_n", 20, type=int)
    window_minutes = request.args.get("window_minutes", 10, type=int)
    bucket_seconds = request.args.get("bucket_seconds", 10, type=int)
    sort_by = request.args.get("sort_by", "recency")

    # Validate sort_by
    if sort_by not in ("recency", "strength", "activity"):
        sort_by = "recency"

    # Get heatmap data from aggregator
    heatmap_data = scanner._aggregator.get_heatmap_data(
        top_n=top_n,
        window_minutes=window_minutes,
        bucket_seconds=bucket_seconds,
        sort_by=sort_by,
    )

    return jsonify(heatmap_data)


@bluetooth_v2_bp.route("/devices/<path:device_key>/timeseries", methods=["GET"])
def get_device_timeseries(device_key: str):
    """
    Get timeseries data for a specific device.

    Path parameters:
        - device_key: Stable device identifier

    Query parameters:
        - window_minutes: Time window (default: 30)
        - bucket_seconds: Bucket size for downsampling (default: 10)

    Returns:
        JSON with device timeseries data.
    """
    scanner = get_bluetooth_scanner()

    window_minutes = request.args.get("window_minutes", 30, type=int)
    bucket_seconds = request.args.get("bucket_seconds", 10, type=int)

    # URL decode device key
    from urllib.parse import unquote

    device_key = unquote(device_key)

    # Get device info
    device = scanner._aggregator.get_device_by_key(device_key)

    # Get timeseries data
    timeseries = scanner._aggregator.get_timeseries(
        device_key=device_key,
        window_minutes=window_minutes,
        downsample_seconds=bucket_seconds,
    )

    result = {
        "device_key": device_key,
        "window_minutes": window_minutes,
        "bucket_seconds": bucket_seconds,
        "observation_count": len(timeseries),
        "timeseries": timeseries,
    }

    if device:
        result.update(
            {
                "name": device.name,
                "address": device.address,
                "rssi_current": device.rssi_current,
                "rssi_ema": round(device.rssi_ema, 1) if device.rssi_ema else None,
                "proximity_band": device.proximity_band,
                "estimated_distance_m": round(device.estimated_distance_m, 2) if device.estimated_distance_m else None,
            }
        )

    return jsonify(result)


def _classify_device_type(device: BTDeviceAggregate) -> str:
    """Classify device type from available data."""
    name_lower = (device.name or "").lower()
    manufacturer_lower = (device.manufacturer_name or "").lower()
    service_uuids = device.service_uuids or []

    if (not manufacturer_lower) or manufacturer_lower.startswith("unknown"):
        if device.address and not device.is_randomized_mac:
            try:
                from data.oui import get_manufacturer

                oui_vendor = get_manufacturer(device.address)
                if oui_vendor and oui_vendor != "Unknown":
                    manufacturer_lower = oui_vendor.lower()
            except Exception:
                pass

    def normalize_uuid(uuid: str) -> str:
        if not uuid:
            return ""
        value = str(uuid).lower().strip()
        if value.startswith("0x"):
            value = value[2:]
        # Bluetooth Base UUID normalization (16-bit UUIDs)
        if value.endswith("-0000-1000-8000-00805f9b34fb") and len(value) >= 8:
            return value[4:8]
        if len(value) == 4:
            return value
        return value

    # Check by name patterns
    if any(x in name_lower for x in ["airpods", "headphone", "earbuds", "buds", "beats"]):
        return "audio"
    if any(x in name_lower for x in ["watch", "band", "fitbit", "garmin"]):
        return "wearable"
    if any(x in name_lower for x in ["iphone", "pixel", "galaxy", "phone"]):
        return "phone"
    if any(x in name_lower for x in ["macbook", "laptop", "thinkpad", "surface"]):
        return "computer"
    if any(x in name_lower for x in ["mouse", "keyboard", "trackpad"]):
        return "peripheral"
    if any(x in name_lower for x in ["tile", "airtag", "smarttag", "chipolo"]):
        return "tracker"
    if any(x in name_lower for x in ["speaker", "sonos", "echo", "home"]):
        return "speaker"
    if any(x in name_lower for x in ["tv", "chromecast", "roku", "firestick"]):
        return "media"

    # Tracker signals (metadata or Find My service)
    if getattr(device, "is_tracker", False) or getattr(device, "tracker_type", None):
        return "tracker"

    normalized_uuids = {normalize_uuid(u) for u in service_uuids if u}
    if "fd6f" in normalized_uuids:
        return "tracker"

    # Service UUIDs (GATT / classic)
    audio_uuids = {"110b", "110a", "111e", "111f", "1108", "1203"}
    wearable_uuids = {"180d", "1814", "1816"}
    hid_uuids = {"1812"}
    beacon_uuids = {"feaa", "feab", "feb1", "febe"}

    if normalized_uuids & audio_uuids:
        return "audio"
    if normalized_uuids & hid_uuids:
        return "peripheral"
    if normalized_uuids & wearable_uuids:
        return "wearable"
    if normalized_uuids & beacon_uuids:
        return "beacon"

    # Check by manufacturer
    if "apple" in manufacturer_lower:
        return "apple_device"
    if "samsung" in manufacturer_lower:
        return "samsung_device"

    # Check by class of device
    if device.major_class:
        major = device.major_class.lower()
        if "audio" in major:
            return "audio"
        if "phone" in major:
            return "phone"
        if "computer" in major:
            return "computer"
        if "peripheral" in major:
            return "peripheral"
        if "wearable" in major:
            return "wearable"

    return "unknown"
