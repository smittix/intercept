"""Meshtastic mesh network routes.

Provides endpoints for connecting to Meshtastic devices, configuring
channels with encryption keys, and streaming received messages.

Supports multiple connection types:
- USB/Serial: Physical device connected via USB
- TCP: WiFi-enabled devices accessible via IP address
"""

from __future__ import annotations

import queue

from flask import Blueprint, Response, jsonify, render_template, request

from config import DEFAULT_LATITUDE, DEFAULT_LONGITUDE, SHARED_OBSERVER_LOCATION_ENABLED
from utils.logging import get_logger
from utils.meshtastic import (
    MeshtasticMessage,
    get_meshtastic_client,
    is_meshtastic_available,
    start_meshtastic,
    stop_meshtastic,
)
from utils.responses import api_error
from utils.sse import sse_stream_fanout

logger = get_logger("intercept.meshtastic")

meshtastic_bp = Blueprint("meshtastic", __name__, url_prefix="/meshtastic")

# Queue for SSE message streaming
_mesh_queue: queue.Queue = queue.Queue(maxsize=500)

# Store recent messages for history
_recent_messages: list[dict] = []
MAX_HISTORY = 500


def _message_callback(msg: MeshtasticMessage) -> None:
    """Callback to queue messages for SSE stream."""
    msg_dict = msg.to_dict()

    # Add to history
    _recent_messages.append(msg_dict)
    if len(_recent_messages) > MAX_HISTORY:
        _recent_messages.pop(0)

    # Queue for SSE
    try:
        _mesh_queue.put_nowait(msg_dict)
    except queue.Full:
        try:
            _mesh_queue.get_nowait()
            _mesh_queue.put_nowait(msg_dict)
        except queue.Empty:
            pass


@meshtastic_bp.route("/dashboard")
def meshtastic_dashboard() -> Response:
    """Popout Meshtastic mesh console dashboard."""
    embedded = request.args.get("embedded", "false") == "true"
    return render_template(
        "meshtastic_dashboard.html",
        shared_observer_location=SHARED_OBSERVER_LOCATION_ENABLED,
        default_latitude=DEFAULT_LATITUDE,
        default_longitude=DEFAULT_LONGITUDE,
        embedded=embedded,
    )


@meshtastic_bp.route("/ports")
def list_ports():
    """
    List available serial ports that may have Meshtastic devices.

    Returns:
        JSON with list of available serial ports.
    """
    if not is_meshtastic_available():
        return jsonify({"status": "error", "ports": [], "message": "Meshtastic SDK not installed"})

    try:
        from meshtastic.util import findPorts

        ports = findPorts()
        return jsonify({"status": "ok", "ports": ports, "count": len(ports)})
    except Exception as e:
        logger.error(f"Error listing ports: {e}")
        return jsonify({"status": "error", "ports": [], "message": str(e)})


@meshtastic_bp.route("/status")
def get_status():
    """
    Get Meshtastic connection status.

    Returns:
        JSON with connection status, device info, connection type, and node information.
    """
    if not is_meshtastic_available():
        return jsonify(
            {
                "available": False,
                "running": False,
                "error": "Meshtastic SDK not installed. Install with: pip install meshtastic",
            }
        )

    client = get_meshtastic_client()

    if not client:
        return jsonify(
            {
                "available": True,
                "running": False,
                "device": None,
                "connection_type": None,
                "node_info": None,
            }
        )

    node_info = client.get_node_info() if client.is_running else None

    return jsonify(
        {
            "available": True,
            "running": client.is_running,
            "device": client.device_path,
            "connection_type": client.connection_type,
            "error": client.error,
            "node_info": node_info.to_dict() if node_info else None,
        }
    )


@meshtastic_bp.route("/start", methods=["POST"])
def start_mesh():
    """
    Start Meshtastic listener.

    Connects to a Meshtastic device and begins receiving messages.
    Supports both USB/Serial and TCP connections.

    JSON body (optional):
        {
            "connection_type": "serial",   // 'serial' (default) or 'tcp'
            "device": "/dev/ttyUSB0",      // Serial port path. Auto-discovers if not provided.
            "hostname": "192.168.1.100"    // IP address or hostname for TCP connections
        }

    Examples:
        Serial (auto-discover): {}
        Serial (specific port): {"device": "/dev/ttyUSB0"}
        TCP: {"connection_type": "tcp", "hostname": "192.168.1.100"}

    Returns:
        JSON with connection status.
    """
    if not is_meshtastic_available():
        return jsonify(
            {"status": "error", "message": "Meshtastic SDK not installed. Install with: pip install meshtastic"}
        ), 400

    client = get_meshtastic_client()
    if client and client.is_running:
        return jsonify(
            {"status": "already_running", "device": client.device_path, "connection_type": client.connection_type}
        )

    # Clear queue and history
    while not _mesh_queue.empty():
        try:
            _mesh_queue.get_nowait()
        except queue.Empty:
            break
    _recent_messages.clear()

    # Parse connection parameters
    data = request.get_json(silent=True) or {}
    connection_type = data.get("connection_type", "serial").lower().strip()
    device = data.get("device")
    hostname = data.get("hostname")

    # Validate connection type
    if connection_type not in ("serial", "tcp"):
        return jsonify(
            {"status": "error", "message": f"Invalid connection_type: {connection_type}. Must be 'serial' or 'tcp'"}
        ), 400

    # Validate TCP parameters
    if connection_type == "tcp":
        if not hostname:
            return jsonify({"status": "error", "message": "hostname is required for TCP connections"}), 400
        hostname = str(hostname).strip()
        if not hostname:
            return jsonify({"status": "error", "message": "hostname cannot be empty"}), 400

    # Validate serial device path if provided
    if device:
        device = str(device).strip()
        if not device:
            device = None

    # Start client
    success = start_meshtastic(
        device=device, callback=_message_callback, connection_type=connection_type, hostname=hostname
    )

    if success:
        client = get_meshtastic_client()
        node_info = client.get_node_info() if client else None
        return jsonify(
            {
                "status": "started",
                "device": client.device_path if client else None,
                "connection_type": client.connection_type if client else None,
                "node_info": node_info.to_dict() if node_info else None,
            }
        )
    else:
        client = get_meshtastic_client()
        return jsonify(
            {"status": "error", "message": client.error if client else "Failed to connect to Meshtastic device"}
        ), 503


@meshtastic_bp.route("/stop", methods=["POST"])
def stop_mesh():
    """
    Stop Meshtastic listener.

    Disconnects from the Meshtastic device and stops receiving messages.

    Returns:
        JSON confirmation.
    """
    stop_meshtastic()
    return jsonify({"status": "stopped"})


@meshtastic_bp.route("/channels")
def get_channels():
    """
    Get configured channels on the connected device.

    Returns:
        JSON with list of channel configurations.
        Note: PSK values are not returned for security - only encryption status.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device"}), 400

    channels = client.get_channels()
    return jsonify({"status": "ok", "channels": [ch.to_dict() for ch in channels], "count": len(channels)})


@meshtastic_bp.route("/channels/<int:index>", methods=["POST"])
def configure_channel(index: int):
    """
    Configure a channel with name and/or encryption key.

    This allows joining encrypted channels by providing the PSK.
    The configuration is written to the connected Meshtastic device.

    Args:
        index: Channel index (0-7). Channel 0 is typically the primary channel.

    JSON body:
        {
            "name": "MyChannel",        // Optional: Channel name
            "psk": "base64:ABC123..."   // Optional: Encryption key
        }

    PSK formats:
        - "none"              : Disable encryption
        - "default"           : Use default public key (NOT SECURE - known key)
        - "random"            : Generate new random AES-256 key
        - "base64:..."        : Base64-encoded 16-byte (AES-128) or 32-byte (AES-256) key
        - "0x..."             : Hex-encoded key
        - "simple:passphrase" : Derive AES-256 key from passphrase using SHA-256

    Returns:
        JSON with configuration result.

    Security note:
        The "default" key is publicly known (shipped in source code).
        Use "random" or provide your own key for secure communications.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device"}), 400

    if not 0 <= index <= 7:
        return jsonify({"status": "error", "message": "Channel index must be 0-7"}), 400

    data = request.get_json(silent=True) or {}
    name = data.get("name")
    psk = data.get("psk")

    if not name and not psk:
        return jsonify({"status": "error", "message": "Must provide name and/or psk"}), 400

    # Sanitize name if provided
    if name:
        name = str(name).strip()[:12]  # Meshtastic channel names max 12 chars

    # Validate PSK format if provided
    if psk:
        psk = str(psk).strip()

    success, message = client.set_channel(index, name=name, psk=psk)

    if success:
        # Return updated channel info
        channels = client.get_channels()
        updated = next((ch for ch in channels if ch.index == index), None)
        return jsonify({"status": "ok", "message": message, "channel": updated.to_dict() if updated else None})
    else:
        return jsonify({"status": "error", "message": message}), 500


@meshtastic_bp.route("/send", methods=["POST"])
def send_message():
    """
    Send a text message to the mesh network.

    JSON body:
        {
            "text": "Hello mesh!",      // Required: message text (max 237 chars)
            "channel": 0,               // Optional: channel index (default 0)
            "to": "!a1b2c3d4"          // Optional: destination node (default broadcast)
        }

    Returns:
        JSON with send status.
    """
    if not is_meshtastic_available():
        return jsonify({"status": "error", "message": "Meshtastic SDK not installed"}), 400

    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device"}), 400

    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"status": "error", "message": "Message text is required"}), 400

    if len(text) > 237:
        return jsonify({"status": "error", "message": "Message too long (max 237 characters)"}), 400

    channel = data.get("channel", 0)
    if not isinstance(channel, int) or not 0 <= channel <= 7:
        return jsonify({"status": "error", "message": "Channel must be 0-7"}), 400

    destination = data.get("to")

    logger.info(f"Sending message: text='{text[:50]}...', channel={channel}, to={destination}")
    success, error = client.send_text(text, channel=channel, destination=destination)
    logger.info(f"Send result: success={success}, error={error}")

    if success:
        return jsonify({"status": "sent"})
    else:
        return jsonify({"status": "error", "message": error or "Failed to send message"}), 500


@meshtastic_bp.route("/messages")
def get_messages():
    """
    Get recent message history.

    Returns the most recent messages received since the listener was started.
    Limited to the last 500 messages.

    Query parameters:
        limit: Maximum number of messages to return (default: all)
        channel: Filter by channel index (optional)

    Returns:
        JSON with message list.
    """
    limit = request.args.get("limit", type=int)
    channel = request.args.get("channel", type=int)

    messages = _recent_messages.copy()

    # Filter by channel if specified
    if channel is not None:
        messages = [m for m in messages if m.get("channel") == channel]

    # Apply limit
    if limit and limit > 0:
        messages = messages[-limit:]

    return jsonify({"status": "ok", "messages": messages, "count": len(messages)})


@meshtastic_bp.route("/stream")
def stream_messages():
    """
    SSE stream of Meshtastic messages.

    Provides real-time Server-Sent Events stream of incoming messages.
    Connect to this endpoint with EventSource to receive live updates.

    Event format:
        data: {"type": "meshtastic", "from": "!a1b2c3d4", "message": "Hello", ...}

    Keepalive events are sent every 30 seconds to maintain the connection.

    Returns:
        SSE stream (text/event-stream)
    """
    response = Response(
        sse_stream_fanout(
            source_queue=_mesh_queue,
            channel_key="meshtastic",
            timeout=1.0,
            keepalive_interval=30.0,
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@meshtastic_bp.route("/node")
def get_node():
    """
    Get local node information.

    Returns information about the connected Meshtastic device including
    its ID, name, hardware model, and current position (if available).

    Returns:
        JSON with node information.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device"}), 400

    node_info = client.get_node_info()

    if node_info:
        return jsonify({"status": "ok", "node": node_info.to_dict()})
    else:
        return jsonify({"status": "error", "message": "Failed to get node information"}), 500


@meshtastic_bp.route("/nodes")
def get_nodes():
    """
    Get all tracked mesh nodes with their positions.

    Returns all nodes that have been seen on the mesh network,
    including their positions (if reported), battery levels, and signal info.

    Query parameters:
        with_position: If 'true', only return nodes with valid positions

    Returns:
        JSON with list of nodes.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device", "nodes": []}), 400

    nodes = client.get_nodes()
    nodes_list = [n.to_dict() for n in nodes]

    # Filter to only nodes with positions if requested
    with_position = request.args.get("with_position", "").lower() == "true"
    if with_position:
        nodes_list = [n for n in nodes_list if n.get("has_position")]

    return jsonify(
        {
            "status": "ok",
            "nodes": nodes_list,
            "count": len(nodes_list),
            "with_position_count": sum(1 for n in nodes_list if n.get("has_position")),
        }
    )


@meshtastic_bp.route("/traceroute", methods=["POST"])
def send_traceroute():
    """
    Send a traceroute request to a mesh node.

    JSON body:
        {
            "destination": "!a1b2c3d4",  // Required: target node ID
            "hop_limit": 7                // Optional: max hops (1-7, default 7)
        }

    Returns:
        JSON with traceroute request status.
    """
    if not is_meshtastic_available():
        return jsonify({"status": "error", "message": "Meshtastic SDK not installed"}), 400

    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device"}), 400

    data = request.get_json(silent=True) or {}
    destination = data.get("destination")

    if not destination:
        return jsonify({"status": "error", "message": "Destination node ID is required"}), 400

    hop_limit = data.get("hop_limit", 7)
    if not isinstance(hop_limit, int) or not 1 <= hop_limit <= 7:
        hop_limit = 7

    success, error = client.send_traceroute(destination, hop_limit=hop_limit)

    if success:
        return jsonify({"status": "sent", "destination": destination, "hop_limit": hop_limit})
    else:
        return jsonify({"status": "error", "message": error or "Failed to send traceroute"}), 500


@meshtastic_bp.route("/traceroute/results")
def get_traceroute_results():
    """
    Get recent traceroute results.

    Query parameters:
        limit: Maximum number of results to return (default: 10)

    Returns:
        JSON with list of traceroute results.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device", "results": []}), 400

    limit = request.args.get("limit", 10, type=int)
    results = client.get_traceroute_results(limit=limit)

    return jsonify({"status": "ok", "results": [r.to_dict() for r in results], "count": len(results)})


@meshtastic_bp.route("/position/request", methods=["POST"])
def request_position():
    """
    Request position from a specific node.

    JSON body:
        {
            "node_id": "!a1b2c3d4"  // Required: target node ID
        }

    Returns:
        JSON with request status.
    """
    if not is_meshtastic_available():
        return jsonify({"status": "error", "message": "Meshtastic SDK not installed"}), 400

    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device"}), 400

    data = request.get_json(silent=True) or {}
    node_id = data.get("node_id")

    if not node_id:
        return jsonify({"status": "error", "message": "Node ID is required"}), 400

    success, error = client.request_position(node_id)

    if success:
        return jsonify({"status": "sent", "node_id": node_id})
    else:
        return jsonify({"status": "error", "message": error or "Failed to request position"}), 500


@meshtastic_bp.route("/firmware/check")
def check_firmware():
    """
    Check current firmware version and compare to latest release.

    Returns:
        JSON with current_version, latest_version, update_available, release_url.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device"}), 400

    result = client.check_firmware()
    result["status"] = "ok"
    return jsonify(result)


@meshtastic_bp.route("/channels/<int:index>/qr")
def get_channel_qr(index: int):
    """
    Generate QR code for a channel configuration.

    Args:
        index: Channel index (0-7)

    Returns:
        PNG image of QR code.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device"}), 400

    if not 0 <= index <= 7:
        return jsonify({"status": "error", "message": "Channel index must be 0-7"}), 400

    png_data = client.generate_channel_qr(index)

    if png_data:
        return Response(png_data, mimetype="image/png")
    else:
        return jsonify(
            {"status": "error", "message": "Failed to generate QR code. Make sure qrcode library is installed."}
        ), 500


@meshtastic_bp.route("/telemetry/history")
def get_telemetry_history():
    """
    Get telemetry history for a node.

    Query parameters:
        node_id: Node ID or number (required)
        hours: Number of hours of history (default: 24)

    Returns:
        JSON with telemetry data points.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device", "data": []}), 400

    node_id = request.args.get("node_id")
    hours = request.args.get("hours", 24, type=int)

    if not node_id:
        return jsonify({"status": "error", "message": "node_id is required", "data": []}), 400

    # Parse node ID to number
    try:
        if node_id.startswith("!"):
            node_num = int(node_id[1:], 16)
        else:
            node_num = int(node_id)
    except ValueError:
        return jsonify({"status": "error", "message": f"Invalid node_id: {node_id}", "data": []}), 400

    history = client.get_telemetry_history(node_num, hours=hours)

    return jsonify(
        {
            "status": "ok",
            "node_id": node_id,
            "hours": hours,
            "data": [p.to_dict() for p in history],
            "count": len(history),
        }
    )


@meshtastic_bp.route("/neighbors")
def get_neighbors():
    """
    Get neighbor information for mesh topology visualization.

    Query parameters:
        node_id: Specific node ID (optional, returns all if not provided)

    Returns:
        JSON with neighbor relationships.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device", "neighbors": {}}), 400

    node_id = request.args.get("node_id")
    node_num = None

    if node_id:
        try:
            if node_id.startswith("!"):
                node_num = int(node_id[1:], 16)
            else:
                node_num = int(node_id)
        except ValueError:
            return jsonify({"status": "error", "message": f"Invalid node_id: {node_id}", "neighbors": {}}), 400

    neighbors = client.get_neighbors(node_num)

    # Convert to JSON-serializable format
    result = {}
    for num, neighbor_list in neighbors.items():
        node_key = f"!{num:08x}"
        result[node_key] = [n.to_dict() for n in neighbor_list]

    return jsonify({"status": "ok", "neighbors": result, "node_count": len(result)})


@meshtastic_bp.route("/pending")
def get_pending_messages():
    """
    Get messages waiting for ACK.

    Returns:
        JSON with pending messages and their status.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device", "messages": []}), 400

    pending = client.get_pending_messages()

    return jsonify({"status": "ok", "messages": [m.to_dict() for m in pending.values()], "count": len(pending)})


@meshtastic_bp.route("/range-test/start", methods=["POST"])
def start_range_test():
    """
    Start a range test.

    JSON body:
        {
            "count": 10,     // Number of packets to send (default 10)
            "interval": 5    // Seconds between packets (default 5)
        }

    Returns:
        JSON with start status.
    """
    if not is_meshtastic_available():
        return jsonify({"status": "error", "message": "Meshtastic SDK not installed"}), 400

    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device"}), 400

    data = request.get_json(silent=True) or {}
    count = data.get("count", 10)
    interval = data.get("interval", 5)

    # Validate
    if not isinstance(count, int) or count < 1 or count > 100:
        count = 10
    if not isinstance(interval, int) or interval < 1 or interval > 60:
        interval = 5

    success, error = client.start_range_test(count=count, interval=interval)

    if success:
        return jsonify({"status": "started", "count": count, "interval": interval})
    else:
        return jsonify({"status": "error", "message": error or "Failed to start range test"}), 500


@meshtastic_bp.route("/range-test/stop", methods=["POST"])
def stop_range_test():
    """
    Stop an ongoing range test.

    Returns:
        JSON confirmation.
    """
    client = get_meshtastic_client()

    if client:
        client.stop_range_test()

    return jsonify({"status": "stopped"})


@meshtastic_bp.route("/range-test/status")
def get_range_test_status():
    """
    Get range test status and results.

    Returns:
        JSON with running status and results.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify(
            {"status": "error", "message": "Not connected to Meshtastic device", "running": False, "results": []}
        ), 400

    status = client.get_range_test_status()
    return jsonify({"status": "ok", **status})


@meshtastic_bp.route("/store-forward/status")
def get_store_forward_status():
    """
    Check if Store & Forward router is available.

    Returns:
        JSON with availability status and router info.
    """
    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device", "available": False}), 400

    sf_status = client.check_store_forward_available()
    return jsonify({"status": "ok", **sf_status})


@meshtastic_bp.route("/store-forward/request", methods=["POST"])
def request_store_forward():
    """
    Request missed messages from Store & Forward router.

    JSON body:
        {
            "window_minutes": 60  // Minutes of history to request (default 60)
        }

    Returns:
        JSON with request status.
    """
    if not is_meshtastic_available():
        return jsonify({"status": "error", "message": "Meshtastic SDK not installed"}), 400

    client = get_meshtastic_client()

    if not client or not client.is_running:
        return jsonify({"status": "error", "message": "Not connected to Meshtastic device"}), 400

    data = request.get_json(silent=True) or {}
    window_minutes = data.get("window_minutes", 60)

    if not isinstance(window_minutes, int) or window_minutes < 1 or window_minutes > 1440:
        window_minutes = 60

    success, error = client.request_store_forward(window_minutes=window_minutes)

    if success:
        return jsonify({"status": "sent", "window_minutes": window_minutes})
    else:
        return jsonify({"status": "error", "message": error or "Failed to request S&F history"}), 500


@meshtastic_bp.route("/topology")
def mesh_topology():
    """Return mesh network topology graph."""
    if not is_meshtastic_available():
        return api_error("Meshtastic SDK not installed", 400)

    client = get_meshtastic_client()
    if not client or not client.is_running:
        return api_error("Not connected", 400)

    return jsonify(
        {
            "status": "success",
            "topology": client.get_topology(),
        }
    )
