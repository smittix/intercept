"""
WiFi v2 API routes.

New unified WiFi scanning API with Quick Scan and Deep Scan modes,
channel analysis, hidden SSID correlation, and SSE streaming.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from collections.abc import Generator
from datetime import datetime

from flask import Blueprint, Response, jsonify, request

from utils.responses import api_error
from utils.sse import format_sse
from utils.validation import validate_wifi_channel
from utils.wifi import (
    SCAN_MODE_DEEP,
    analyze_channels,
    get_hidden_correlator,
    get_wifi_scanner,
)

logger = logging.getLogger(__name__)

wifi_v2_bp = Blueprint("wifi_v2", __name__, url_prefix="/wifi/v2")


# =============================================================================
# Capabilities
# =============================================================================


@wifi_v2_bp.route("/capabilities", methods=["GET"])
def get_capabilities():
    """
    Get WiFi scanning capabilities.

    Returns available tools, interfaces, and scan mode support.
    """
    scanner = get_wifi_scanner()
    caps = scanner.check_capabilities()
    return jsonify(caps.to_dict())


# =============================================================================
# Quick Scan
# =============================================================================


@wifi_v2_bp.route("/scan/quick", methods=["POST"])
def quick_scan():
    """
    Perform a quick one-shot WiFi scan.

    Uses system tools (nmcli, iw, iwlist, airport) without monitor mode.

    Request body:
        interface: Optional interface name
        timeout: Optional scan timeout in seconds (default 15)

    Returns:
        WiFiScanResult with discovered networks and channel analysis.
    """
    data = request.get_json() or {}
    interface = data.get("interface")
    timeout = float(data.get("timeout", 15))

    scanner = get_wifi_scanner()
    result = scanner.quick_scan(interface=interface, timeout=timeout)

    return jsonify(result.to_dict())


# =============================================================================
# Deep Scan (Monitor Mode)
# =============================================================================


@wifi_v2_bp.route("/scan/start", methods=["POST"])
def start_deep_scan():
    """
    Start a deep scan using airodump-ng.

    Requires monitor mode interface and root privileges.

    Request body:
        interface: Monitor mode interface (e.g., 'wlan0mon')
        band: Band to scan ('2.4', '5', 'all')
        channel: Optional specific channel to monitor
        channels: Optional list or comma-separated channels to monitor
    """
    data = request.get_json() or {}
    interface = data.get("interface")
    band = data.get("band", "all")
    channel = data.get("channel")
    channels = data.get("channels")

    channel_list = None
    if channels:
        if isinstance(channels, str):
            channel_list = [c.strip() for c in channels.split(",") if c.strip()]
        elif isinstance(channels, (list, tuple, set)):
            channel_list = list(channels)
        else:
            channel_list = [channels]
        try:
            channel_list = [validate_wifi_channel(c) for c in channel_list]
        except (TypeError, ValueError):
            return api_error("Invalid channels", 400)

    if channel:
        try:
            channel = validate_wifi_channel(channel)
        except ValueError:
            return api_error("Invalid channel", 400)

    scanner = get_wifi_scanner()
    success = scanner.start_deep_scan(
        interface=interface,
        band=band,
        channel=channel,
        channels=channel_list,
    )

    if success:
        return jsonify(
            {
                "status": "started",
                "mode": SCAN_MODE_DEEP,
                "interface": interface or scanner._capabilities.monitor_interface,
            }
        )
    else:
        return api_error(scanner._status.error or "Scan failed", 400)


@wifi_v2_bp.route("/scan/stop", methods=["POST"])
def stop_deep_scan():
    """Stop the deep scan."""
    scanner = get_wifi_scanner()
    scanner.stop_deep_scan()

    return jsonify(
        {
            "status": "stopped",
        }
    )


@wifi_v2_bp.route("/scan/status", methods=["GET"])
def get_scan_status():
    """Get current scan status."""
    scanner = get_wifi_scanner()
    status = scanner.get_status()
    return jsonify(status.to_dict())


# =============================================================================
# Data Endpoints
# =============================================================================


@wifi_v2_bp.route("/networks", methods=["GET"])
def get_networks():
    """
    Get all discovered networks.

    Query params:
        band: Filter by band ('2.4GHz', '5GHz', '6GHz')
        security: Filter by security type ('Open', 'WEP', 'WPA', 'WPA2', 'WPA3')
        hidden: Filter hidden networks only (true/false)
        min_rssi: Minimum RSSI threshold
        sort: Sort field ('rssi', 'channel', 'essid', 'last_seen')
        order: Sort order ('asc', 'desc')
        format: Response format ('full', 'summary')
    """
    scanner = get_wifi_scanner()
    networks = scanner.access_points

    # Apply filters — single pass over the network list
    band = request.args.get("band")
    security = request.args.get("security")
    hidden = request.args.get("hidden")
    min_rssi_val: int | None = None
    raw_min_rssi = request.args.get("min_rssi")
    if raw_min_rssi:
        try:
            min_rssi_val = int(raw_min_rssi)
        except ValueError:
            pass

    if band or security or hidden or min_rssi_val is not None:

        def _matches(n: object) -> bool:
            if band and n.band != band:
                return False
            if security and n.security != security:
                return False
            if hidden == "true" and not n.is_hidden:
                return False
            if hidden == "false" and n.is_hidden:
                return False
            if min_rssi_val is not None and (not n.rssi_current or n.rssi_current < min_rssi_val):  # noqa: SIM103
                return False
            return True

        networks = [n for n in networks if _matches(n)]

    # Apply sorting
    sort_field = request.args.get("sort", "rssi")
    order = request.args.get("order", "desc")
    reverse = order == "desc"

    sort_key_map = {
        "rssi": lambda n: n.rssi_current or -100,
        "channel": lambda n: n.channel or 0,
        "essid": lambda n: (n.essid or "").lower(),
        "last_seen": lambda n: n.last_seen,
        "clients": lambda n: n.client_count,
    }

    if sort_field in sort_key_map:
        networks.sort(key=sort_key_map[sort_field], reverse=reverse)

    # Format output
    output_format = request.args.get("format", "summary")
    if output_format == "full":
        return jsonify([n.to_dict() for n in networks])
    else:
        return jsonify([n.to_summary_dict() for n in networks])


@wifi_v2_bp.route("/networks/<bssid>", methods=["GET"])
def get_network(bssid):
    """Get a specific network by BSSID."""
    scanner = get_wifi_scanner()
    network = scanner.get_network(bssid)

    if network:
        return jsonify(network.to_dict())
    else:
        return api_error("Network not found", 404)


@wifi_v2_bp.route("/clients", methods=["GET"])
def get_clients():
    """
    Get all discovered clients.

    Query params:
        associated: Filter by association status (true/false)
        bssid: Filter by associated BSSID
        min_rssi: Minimum RSSI threshold
    """
    scanner = get_wifi_scanner()
    clients = scanner.clients

    # Apply filters
    associated = request.args.get("associated")
    if associated == "true":
        clients = [c for c in clients if c.is_associated]
    elif associated == "false":
        clients = [c for c in clients if not c.is_associated]

    bssid = request.args.get("bssid")
    if bssid:
        clients = [c for c in clients if c.associated_bssid == bssid.upper()]

    min_rssi = request.args.get("min_rssi")
    if min_rssi:
        try:
            min_rssi = int(min_rssi)
            clients = [c for c in clients if c.rssi_current and c.rssi_current >= min_rssi]
        except ValueError:
            pass

    return jsonify([c.to_dict() for c in clients])


@wifi_v2_bp.route("/clients/<mac>", methods=["GET"])
def get_client(mac):
    """Get a specific client by MAC address."""
    scanner = get_wifi_scanner()
    client = scanner.get_client(mac)

    if client:
        return jsonify(client.to_dict())
    else:
        return api_error("Client not found", 404)


@wifi_v2_bp.route("/probes", methods=["GET"])
def get_probes():
    """
    Get captured probe requests.

    Query params:
        client_mac: Filter by client MAC
        ssid: Filter by probed SSID
        limit: Maximum number of results
    """
    scanner = get_wifi_scanner()
    probes = scanner.probe_requests

    # Apply filters
    client_mac = request.args.get("client_mac")
    if client_mac:
        probes = [p for p in probes if p.client_mac == client_mac.upper()]

    ssid = request.args.get("ssid")
    if ssid:
        probes = [p for p in probes if p.probed_ssid == ssid]

    # Apply limit
    limit = request.args.get("limit")
    if limit:
        try:
            limit = int(limit)
            probes = probes[-limit:]  # Most recent
        except ValueError:
            pass

    return jsonify([p.to_dict() for p in probes])


# =============================================================================
# Channel Analysis
# =============================================================================


@wifi_v2_bp.route("/channels", methods=["GET"])
def get_channel_stats():
    """
    Get channel utilization statistics and recommendations.

    Query params:
        include_dfs: Include DFS channels in recommendations (true/false)
    """
    scanner = get_wifi_scanner()
    include_dfs = request.args.get("include_dfs", "false") == "true"

    stats, recommendations = analyze_channels(
        scanner.access_points,
        include_dfs=include_dfs,
    )

    return jsonify(
        {
            "stats": [s.to_dict() for s in stats],
            "recommendations": [r.to_dict() for r in recommendations],
        }
    )


# =============================================================================
# Hidden SSID Correlation
# =============================================================================


@wifi_v2_bp.route("/hidden", methods=["GET"])
def get_hidden_correlations():
    """
    Get revealed hidden SSIDs from correlation.

    Returns mapping of BSSID -> revealed SSID.
    """
    correlator = get_hidden_correlator()
    return jsonify(correlator.get_all_revealed())


# =============================================================================
# Baseline Management
# =============================================================================


@wifi_v2_bp.route("/baseline/set", methods=["POST"])
def set_baseline():
    """Mark current networks as baseline (known networks)."""
    scanner = get_wifi_scanner()
    scanner.set_baseline()

    return jsonify(
        {
            "status": "baseline_set",
            "network_count": len(scanner._baseline_networks),
            "set_at": datetime.now().isoformat(),
        }
    )


@wifi_v2_bp.route("/baseline/clear", methods=["POST"])
def clear_baseline():
    """Clear the baseline."""
    scanner = get_wifi_scanner()
    scanner.clear_baseline()

    return jsonify(
        {
            "status": "baseline_cleared",
        }
    )


# =============================================================================
# SSE Streaming
# =============================================================================


@wifi_v2_bp.route("/stream", methods=["GET"])
def event_stream():
    """
    Server-Sent Events stream for real-time updates.

    Events:
        - network_update: Network discovered/updated
        - client_update: Client discovered/updated
        - probe_request: Probe request detected
        - hidden_revealed: Hidden SSID revealed
        - scan_started, scan_stopped, scan_error
        - keepalive: Periodic keepalive
    """

    def generate() -> Generator[str, None, None]:
        scanner = get_wifi_scanner()

        for event in scanner.get_event_stream():
            yield format_sse(event)

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


# =============================================================================
# Data Management
# =============================================================================


@wifi_v2_bp.route("/clear", methods=["POST"])
def clear_data():
    """Clear all discovered data."""
    scanner = get_wifi_scanner()
    scanner.clear_data()

    return jsonify(
        {
            "status": "cleared",
        }
    )


# =============================================================================
# Export
# =============================================================================


@wifi_v2_bp.route("/export", methods=["GET"])
def export_data():
    """
    Export scan data.

    Query params:
        format: 'json' or 'csv' (default: json)
        type: 'networks', 'clients', 'probes', 'all' (default: all)
    """
    scanner = get_wifi_scanner()
    export_format = request.args.get("format", "json")
    export_type = request.args.get("type", "all")

    if export_format == "csv":
        return _export_csv(scanner, export_type)
    else:
        return _export_json(scanner, export_type)


def _export_json(scanner, export_type: str) -> Response:
    """Export data as JSON."""
    data = {}

    if export_type in ("networks", "all"):
        data["networks"] = [n.to_dict() for n in scanner.access_points]

    if export_type in ("clients", "all"):
        data["clients"] = [c.to_dict() for c in scanner.clients]

    if export_type in ("probes", "all"):
        data["probes"] = [p.to_dict() for p in scanner.probe_requests]

    data["exported_at"] = datetime.now().isoformat()
    data["network_count"] = len(scanner.access_points)
    data["client_count"] = len(scanner.clients)

    response = Response(
        json.dumps(data, indent=2),
        mimetype="application/json",
    )
    response.headers["Content-Disposition"] = (
        f"attachment; filename=wifi_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    return response


def _export_csv(scanner, export_type: str) -> Response:
    """Export data as CSV."""
    output = io.StringIO()

    if export_type in ("networks", "all"):
        writer = csv.writer(output)
        writer.writerow(
            [
                "BSSID",
                "ESSID",
                "Channel",
                "Band",
                "RSSI",
                "Security",
                "Cipher",
                "Auth",
                "Vendor",
                "Clients",
                "First Seen",
                "Last Seen",
            ]
        )

        for n in scanner.access_points:
            writer.writerow(
                [
                    n.bssid,
                    n.essid or "[Hidden]",
                    n.channel,
                    n.band,
                    n.rssi_current,
                    n.security,
                    n.cipher,
                    n.auth,
                    n.vendor or "",
                    n.client_count,
                    n.first_seen.isoformat(),
                    n.last_seen.isoformat(),
                ]
            )

        if export_type == "all":
            writer.writerow([])  # Blank line separator

    if export_type in ("clients", "all"):
        writer = csv.writer(output)
        if export_type == "clients":
            writer.writerow(["MAC", "Vendor", "RSSI", "Associated BSSID", "Probed SSIDs", "First Seen", "Last Seen"])

        for c in scanner.clients:
            writer.writerow(
                [
                    c.mac,
                    c.vendor or "",
                    c.rssi_current,
                    c.associated_bssid or "",
                    ", ".join(c.probed_ssids),
                    c.first_seen.isoformat(),
                    c.last_seen.isoformat(),
                ]
            )

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = (
        f"attachment; filename=wifi_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    return response
