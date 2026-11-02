"""Reticulum (LXMF) routes.

Endpoints for starting/stopping the LXMF router, announcing, listing nodes and
conversations, sending messages, and streaming live events.
"""

from __future__ import annotations

import json
import queue

from flask import Blueprint, Response, jsonify, render_template, request

from config import DEFAULT_LATITUDE, DEFAULT_LONGITUDE, SHARED_OBSERVER_LOCATION_ENABLED
from utils.logging import get_logger
from utils.responses import api_error
from utils.reticulum import (
    ReticulumConfig,
    default_config,
    get_reticulum_client,
    is_reticulum_available,
    normalize_hash,
    save_config,
)

logger = get_logger("intercept.reticulum")

reticulum_bp = Blueprint("reticulum", __name__, url_prefix="/reticulum")

MAX_MESSAGE_LENGTH = 4000


def _client():
    return get_reticulum_client()


@reticulum_bp.route("/dashboard")
def reticulum_dashboard() -> Response:
    """Reticulum LXMF messaging dashboard."""
    embedded = request.args.get("embedded", "false") == "true"
    return render_template(
        "reticulum_dashboard.html",
        shared_observer_location=SHARED_OBSERVER_LOCATION_ENABLED,
        default_latitude=DEFAULT_LATITUDE,
        default_longitude=DEFAULT_LONGITUDE,
        embedded=embedded,
    )


# ---------------------------------------------------------------------------
# Status & lifecycle
# ---------------------------------------------------------------------------


@reticulum_bp.route("/status")
def status():
    if not is_reticulum_available():
        return jsonify(
            {
                "available": False,
                "state": "unavailable",
                "message": "rns/lxmf not installed. Run: pip install rns lxmf",
            }
        )
    return jsonify({"available": True, **_client().get_status()})


@reticulum_bp.route("/config")
def get_config():
    cfg = default_config()
    return jsonify({"mode": cfg.mode, "host": cfg.host, "port": cfg.port, "display_name": cfg.display_name})


@reticulum_bp.route("/start", methods=["POST"])
def start():
    if not is_reticulum_available():
        return api_error("rns/lxmf not installed", 503)
    data = request.get_json(silent=True) or {}
    base = default_config()
    try:
        cfg = ReticulumConfig(
            mode=data.get("mode", base.mode),
            host=str(data.get("host", base.host)).strip(),
            port=int(data.get("port", base.port)),
            display_name=str(data.get("display_name", base.display_name)).strip(),
            instance_name=base.instance_name,
            rpc_key=base.rpc_key,
        )
        cfg.validate()
        _client().start(cfg)
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), 400)
    save_config(cfg)
    return jsonify({"status": "starting", "mode": cfg.mode})


@reticulum_bp.route("/stop", methods=["POST"])
def stop():
    if is_reticulum_available():
        _client().stop()
    return jsonify({"status": "stopped"})


@reticulum_bp.route("/announce", methods=["POST"])
def announce():
    try:
        _client().announce()
    except RuntimeError as exc:
        return api_error(str(exc), 409)
    return jsonify({"status": "announced"})


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


@reticulum_bp.route("/stream")
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
# Nodes, conversations, messages
# ---------------------------------------------------------------------------


@reticulum_bp.route("/nodes")
def nodes():
    return jsonify({"nodes": _client().get_nodes()})


@reticulum_bp.route("/conversations")
def conversations():
    return jsonify({"conversations": _client().get_conversations()})


@reticulum_bp.route("/messages")
def messages():
    peer = request.args.get("peer")
    if peer:
        try:
            peer = normalize_hash(peer)
        except ValueError as exc:
            return api_error(str(exc), 400)
    return jsonify({"messages": _client().get_messages(peer)})


@reticulum_bp.route("/send", methods=["POST"])
def send():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    destination = str(data.get("destination", ""))
    if not text:
        return api_error("text is required", 400)
    if len(text) > MAX_MESSAGE_LENGTH:
        return api_error(f"text exceeds {MAX_MESSAGE_LENGTH} characters", 400)
    try:
        msg = _client().send(destination, text)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except LookupError as exc:
        return api_error(str(exc), 404)
    except RuntimeError as exc:
        return api_error(str(exc), 409)
    return jsonify({"status": "queued", "message": msg})
