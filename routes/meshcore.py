"""Meshcore device routes.

Endpoints for connecting to Meshcore devices (serial, TCP, BLE),
streaming live events, and managing messages, contacts, and nodes.
"""

from __future__ import annotations

import json
import queue

from flask import Blueprint, Response, jsonify, render_template, request

from config import DEFAULT_LATITUDE, DEFAULT_LONGITUDE, SHARED_OBSERVER_LOCATION_ENABLED
from utils.logging import get_logger
from utils.meshcore import (
    BLEConfig,
    MeshcoreContact,
    SerialConfig,
    TCPConfig,
    get_meshcore_client,
    is_meshcore_available,
    list_serial_ports,
)
from utils.responses import api_error

logger = get_logger("intercept.meshcore")

meshcore_bp = Blueprint("meshcore", __name__, url_prefix="/meshcore")


def _client():
    return get_meshcore_client()


@meshcore_bp.route("/dashboard")
def meshcore_dashboard() -> Response:
    """Popout Meshcore mesh console dashboard."""
    embedded = request.args.get("embedded", "false") == "true"
    return render_template(
        "meshcore_dashboard.html",
        shared_observer_location=SHARED_OBSERVER_LOCATION_ENABLED,
        default_latitude=DEFAULT_LATITUDE,
        default_longitude=DEFAULT_LONGITUDE,
        embedded=embedded,
    )


# ---------------------------------------------------------------------------
# Status & connection management
# ---------------------------------------------------------------------------


@meshcore_bp.route("/status")
def status():
    if not is_meshcore_available():
        return jsonify(
            {
                "available": False,
                "state": "unavailable",
                "message": "meshcore package not installed. Run: pip install meshcore",
            }
        )
    c = _client()
    state, message = c.get_state()
    payload = {"available": True, "state": state.value}
    if message:
        payload["message"] = message
    return jsonify(payload)


@meshcore_bp.route("/connect", methods=["POST"])
def connect():
    if not is_meshcore_available():
        return api_error("meshcore not installed", 503)
    data = request.get_json(silent=True) or {}
    transport = data.get("transport", "serial")

    if transport == "serial":
        config = SerialConfig(port=data.get("port"), baud=int(data.get("baud", 115200)))
    elif transport == "tcp":
        host = data.get("host", "localhost")
        port = int(data.get("port", 5000))
        config = TCPConfig(host=host, port=port)
    elif transport == "ble":
        config = BLEConfig(device_address=data.get("address"))
    else:
        return api_error(f"Unknown transport: {transport}", 400)

    _client().connect(config)
    return jsonify({"status": "connecting", "transport": transport})


@meshcore_bp.route("/disconnect", methods=["POST"])
def disconnect():
    _client().disconnect()
    return jsonify({"status": "disconnected"})


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@meshcore_bp.route("/ports")
def ports():
    return jsonify({"ports": list_serial_ports()})


@meshcore_bp.route("/ble/scan")
def ble_scan():
    if not is_meshcore_available():
        return api_error("meshcore not installed", 503)
    devices = _client().scan_ble()
    return jsonify({"devices": devices})


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


@meshcore_bp.route("/stream")
def stream():
    def _gen():
        q = _client().get_queue()
        while True:
            try:
                event = q.get(timeout=30)
                yield f"data: {json.dumps(event)}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"

    return Response(
        _gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@meshcore_bp.route("/messages")
def messages():
    return jsonify({"messages": _client().get_messages()})


@meshcore_bp.route("/send", methods=["POST"])
def send():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    recipient_id = data.get("recipient_id", "BROADCAST")
    if not text:
        return api_error("text is required", 400)
    if len(text) > 237:
        return api_error("text exceeds 237-character Meshcore limit", 400)
    _client().send_text(recipient_id, text)
    return jsonify({"status": "queued"})


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


@meshcore_bp.route("/nodes")
def nodes():
    return jsonify({"nodes": _client().get_nodes()})


@meshcore_bp.route("/repeaters")
def repeaters():
    return jsonify({"repeaters": _client().get_repeaters()})


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


@meshcore_bp.route("/contacts", methods=["GET"])
def list_contacts():
    return jsonify({"contacts": _client().get_contacts()})


@meshcore_bp.route("/contacts", methods=["POST"])
def add_contact():
    data = request.get_json(silent=True) or {}
    node_id = data.get("node_id", "").strip()
    name = data.get("name", "").strip()
    public_key = data.get("public_key", "").strip()
    if not node_id or not name or not public_key:
        return api_error("node_id, name, and public_key are required", 400)
    contact = MeshcoreContact(node_id=node_id, name=name, public_key=public_key, last_msg=None)
    _client().add_contact(contact)
    return jsonify({"status": "added", "contact": contact.to_dict()})


@meshcore_bp.route("/contacts/<node_id>", methods=["DELETE"])
def delete_contact(node_id: str):
    removed = _client().remove_contact(node_id)
    if not removed:
        return api_error("contact not found", 404)
    return jsonify({"status": "removed"})


# ---------------------------------------------------------------------------
# Telemetry & traceroute
# ---------------------------------------------------------------------------


@meshcore_bp.route("/telemetry/<node_id>")
def telemetry(node_id: str):
    return jsonify({"node_id": node_id, "telemetry": _client().get_telemetry(node_id)})


@meshcore_bp.route("/traceroute", methods=["POST"])
def traceroute():
    data = request.get_json(silent=True) or {}
    node_id = data.get("node_id", "").strip()
    if not node_id:
        return api_error("node_id is required", 400)
    _client().request_traceroute(node_id)
    return jsonify({"status": "requested", "node_id": node_id})
