"""
Controller routes for managing remote Intercept agents.

This blueprint provides:
- Agent CRUD operations
- Proxy endpoints to forward requests to agents
- Push data ingestion endpoint
- Multi-agent SSE stream
"""

from __future__ import annotations

import hmac
import logging
import posixpath
import queue
import threading
import time
from collections.abc import Generator
from datetime import datetime, timezone

import requests
from flask import Blueprint, Response, jsonify, redirect, request, session, url_for

from utils.agent_client import AgentClient, AgentConnectionError, AgentHTTPError, create_client_from_agent
from utils.database import (
    create_agent,
    delete_agent,
    get_agent,
    get_agent_api_key,
    get_agent_by_name,
    get_recent_payloads,
    list_agents,
    store_push_payload,
    update_agent,
)
from utils.responses import api_error
from utils.sse import format_sse
from utils.trilateration import (
    DeviceLocationTracker,
    PathLossModel,
    Trilateration,
    estimate_location_from_observations,
)

logger = logging.getLogger("intercept.controller")

controller_bp = Blueprint("controller", __name__, url_prefix="/controller")
AGENT_HEALTH_TIMEOUT_SECONDS = 2.0
AGENT_STATUS_TIMEOUT_SECONDS = 2.5

# Multi-agent SSE fanout state (per-client queues).
_agent_stream_subscribers: set[queue.Queue] = set()
_agent_stream_subscribers_lock = threading.Lock()
_AGENT_STREAM_CLIENT_QUEUE_SIZE = 500

# Endpoints remote agents call inbound. These carry their own X-API-Key
# check rather than a session, so the session gate below lets them through
# to the handler that validates the key against the named agent.
_AGENT_PUSH_ENDPOINTS = frozenset({"controller.ingest_push_data"})

# Browser pages in this blueprint. An unauthenticated request for these gets
# the same redirect the rest of the app gives, rather than a bare 401.
_BROWSER_PAGE_ENDPOINTS = frozenset({"controller.agent_management_page", "controller.network_monitor_page"})


@controller_bp.before_request
def require_controller_auth():
    """Authenticate every controller route.

    app.py's global gate deliberately skips /controller/* because remote
    agents authenticate with an API key rather than a session. That left
    every route here open, including agent registration and the proxy that
    starts and stops SDR modes on remote nodes. This is that gate.
    """
    if session.get("logged_in"):
        return None

    if request.endpoint in _AGENT_PUSH_ENDPOINTS:
        # The handler validates X-API-Key against the named agent.
        return None

    if request.endpoint in _BROWSER_PAGE_ENDPOINTS:
        # Match the rest of the app: send a browser to the login page rather
        # than a bare 401. Falls back to 401 when this blueprint is mounted
        # without the main app (no login endpoint to build a URL for).
        try:
            return redirect(url_for("login"))
        except Exception:
            return api_error("Authentication required", 401)

    return api_error("Authentication required", 401)


def _broadcast_agent_data(payload: dict) -> None:
    """Fan out an ingested payload to all active /controller/stream/all clients."""
    with _agent_stream_subscribers_lock:
        subscribers = tuple(_agent_stream_subscribers)

    for subscriber in subscribers:
        try:
            subscriber.put_nowait(payload)
        except queue.Full:
            try:
                subscriber.get_nowait()
                subscriber.put_nowait(payload)
            except (queue.Empty, queue.Full):
                continue


# =============================================================================
# Agent CRUD
# =============================================================================


@controller_bp.route("/agents", methods=["GET"])
def get_agents():
    """List all registered agents."""
    active_only = request.args.get("active_only", "true").lower() == "true"
    agents = list_agents(active_only=active_only)

    # Optionally refresh status for each agent
    refresh = request.args.get("refresh", "false").lower() == "true"
    if refresh:
        for agent in agents:
            try:
                client = AgentClient(
                    agent["base_url"],
                    api_key=get_agent_api_key(agent["id"]),
                    timeout=AGENT_HEALTH_TIMEOUT_SECONDS,
                )
                agent["healthy"] = client.health_check()
            except Exception:
                agent["healthy"] = False

    return jsonify({"status": "success", "agents": agents, "count": len(agents)})


@controller_bp.route("/agents", methods=["POST"])
def register_agent():
    """
    Register a new remote agent.

    Expected JSON body:
    {
        "name": "sensor-node-1",
        "base_url": "http://192.168.1.50:8020",
        "api_key": "optional-shared-secret",
        "description": "Optional description"
    }
    """
    data = request.json or {}

    # Validate required fields
    name = data.get("name", "").strip()
    base_url = data.get("base_url", "").strip()

    if not name:
        return api_error("Agent name is required", 400)
    if not base_url:
        return api_error("Base URL is required", 400)

    # Validate URL format
    from urllib.parse import urlparse

    try:
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https"):
            return api_error("URL must start with http:// or https://", 400)
        if not parsed.netloc:
            return api_error("Invalid URL format", 400)
    except Exception:
        return api_error("Invalid URL format", 400)

    # Check if agent already exists
    existing = get_agent_by_name(name)
    if existing:
        return api_error(f'Agent with name "{name}" already exists', 409)

    # Try to connect and get capabilities
    api_key = data.get("api_key", "").strip() or None
    client = AgentClient(base_url, api_key=api_key)

    capabilities = None
    interfaces = None
    try:
        caps = client.get_capabilities()
        capabilities = caps.get("modes", {})
        interfaces = {"devices": caps.get("devices", [])}
    except (AgentHTTPError, AgentConnectionError) as e:
        logger.warning(f"Could not fetch capabilities from {base_url}: {e}")

    # Create agent
    try:
        agent_id = create_agent(
            name=name,
            base_url=base_url,
            api_key=api_key,
            description=data.get("description"),
            capabilities=capabilities,
            interfaces=interfaces,
        )

        # Update last_seen since we just connected
        if capabilities is not None:
            update_agent(agent_id, update_last_seen=True)

        agent = get_agent(agent_id)
        message = "Agent registered successfully"
        if capabilities is None:
            message += " (could not connect - agent may be offline)"
        return jsonify({"status": "success", "message": message, "agent": agent}), 201

    except Exception as e:
        logger.exception("Failed to create agent")
        return api_error(str(e), 500)


@controller_bp.route("/agents/<int:agent_id>", methods=["GET"])
def get_agent_detail(agent_id: int):
    """Get details of a specific agent."""
    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    # Optionally refresh from agent
    refresh = request.args.get("refresh", "false").lower() == "true"
    if refresh:
        try:
            client = create_client_from_agent(agent)
            metadata = client.refresh_metadata()
            if metadata["healthy"]:
                caps = metadata["capabilities"] or {}
                # Store full interfaces structure (wifi, bt, sdr)
                agent_interfaces = caps.get("interfaces", {})
                # Fallback: also include top-level devices for backwards compatibility
                if not agent_interfaces.get("sdr_devices") and caps.get("devices"):
                    agent_interfaces["sdr_devices"] = caps.get("devices", [])
                update_agent(
                    agent_id, capabilities=caps.get("modes"), interfaces=agent_interfaces, update_last_seen=True
                )
                agent = get_agent(agent_id)
                agent["healthy"] = True
            else:
                agent["healthy"] = False
        except Exception:
            agent["healthy"] = False

    return jsonify({"status": "success", "agent": agent})


@controller_bp.route("/agents/<int:agent_id>", methods=["PUT", "PATCH"])
def update_agent_detail(agent_id: int):
    """Update an agent's details."""
    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    data = request.json or {}

    # Update allowed fields
    update_agent(
        agent_id,
        base_url=data.get("base_url"),
        description=data.get("description"),
        api_key=data.get("api_key"),
        is_active=data.get("is_active"),
    )

    agent = get_agent(agent_id)
    return jsonify({"status": "success", "agent": agent})


@controller_bp.route("/agents/<int:agent_id>", methods=["DELETE"])
def remove_agent(agent_id: int):
    """Delete an agent."""
    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    delete_agent(agent_id)
    return jsonify({"status": "success", "message": "Agent deleted"})


@controller_bp.route("/agents/<int:agent_id>/refresh", methods=["POST"])
def refresh_agent_metadata(agent_id: int):
    """Refresh an agent's capabilities and status."""
    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    try:
        client = create_client_from_agent(agent)
        metadata = client.refresh_metadata()

        if metadata["healthy"]:
            caps = metadata["capabilities"] or {}
            # Store full interfaces structure (wifi, bt, sdr)
            agent_interfaces = caps.get("interfaces", {})
            # Fallback: also include top-level devices for backwards compatibility
            if not agent_interfaces.get("sdr_devices") and caps.get("devices"):
                agent_interfaces["sdr_devices"] = caps.get("devices", [])
            update_agent(agent_id, capabilities=caps.get("modes"), interfaces=agent_interfaces, update_last_seen=True)
            agent = get_agent(agent_id)
            return jsonify({"status": "success", "agent": agent, "metadata": metadata})
        else:
            return api_error("Agent is not reachable", 503)

    except (AgentHTTPError, AgentConnectionError) as e:
        return api_error(f"Failed to reach agent: {e}", 503)


# =============================================================================
# Agent Status - Get running state
# =============================================================================


@controller_bp.route("/agents/<int:agent_id>/status", methods=["GET"])
def get_agent_status(agent_id: int):
    """Get an agent's current status including running modes."""
    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    try:
        client = create_client_from_agent(agent)
        status = client.get_status()
        return jsonify({"status": "success", "agent_id": agent_id, "agent_name": agent["name"], "agent_status": status})
    except (AgentHTTPError, AgentConnectionError) as e:
        return api_error(f"Failed to reach agent: {e}", 503)


@controller_bp.route("/agents/health", methods=["GET"])
def check_all_agents_health():
    """
    Check health of all registered agents in one call.

    More efficient than checking each agent individually.
    Returns health status, response time, and running modes for each agent.
    """
    agents_list = list_agents(active_only=True)
    results = []

    for agent in agents_list:
        result = {
            "id": agent["id"],
            "name": agent["name"],
            "healthy": False,
            "response_time_ms": None,
            "running_modes": [],
            "error": None,
        }

        try:
            client = AgentClient(
                agent["base_url"],
                api_key=get_agent_api_key(agent["id"]),
                timeout=AGENT_HEALTH_TIMEOUT_SECONDS,
            )

            # Time the health check
            start_time = time.time()
            is_healthy = client.health_check()
            response_time = (time.time() - start_time) * 1000

            result["healthy"] = is_healthy
            result["response_time_ms"] = round(response_time, 1)

            if is_healthy:
                # Update last_seen in database
                update_agent(agent["id"], update_last_seen=True)

                # Also fetch running modes
                try:
                    status_client = AgentClient(
                        agent["base_url"],
                        api_key=get_agent_api_key(agent["id"]),
                        timeout=AGENT_STATUS_TIMEOUT_SECONDS,
                    )
                    status = status_client.get_status()
                    result["running_modes"] = status.get("running_modes", [])
                    result["running_modes_detail"] = status.get("running_modes_detail", {})
                except Exception:
                    pass  # Status fetch is optional

        except AgentConnectionError as e:
            result["error"] = f"Connection failed: {str(e)}"
        except AgentHTTPError as e:
            result["error"] = f"HTTP error: {str(e)}"
        except Exception as e:
            result["error"] = str(e)

        results.append(result)

    return jsonify(
        {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agents": results,
            "total": len(results),
            "healthy_count": sum(1 for r in results if r["healthy"]),
        }
    )


# =============================================================================
# Proxy Operations - Forward requests to agents
# =============================================================================


@controller_bp.route("/agents/<int:agent_id>/<mode>/start", methods=["POST"])
def proxy_start_mode(agent_id: int, mode: str):
    """Start a mode on a remote agent."""
    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    params = request.json or {}

    try:
        client = create_client_from_agent(agent)
        result = client.start_mode(mode, params)

        # Update last_seen
        update_agent(agent_id, update_last_seen=True)

        return jsonify({"status": "success", "agent_id": agent_id, "mode": mode, "result": result})

    except AgentConnectionError as e:
        return api_error(f"Cannot connect to agent: {e}", 503)
    except AgentHTTPError as e:
        return api_error(f"Agent error: {e}", 502)


@controller_bp.route("/agents/<int:agent_id>/<mode>/stop", methods=["POST"])
def proxy_stop_mode(agent_id: int, mode: str):
    """Stop a mode on a remote agent."""
    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    try:
        client = create_client_from_agent(agent)
        result = client.stop_mode(mode)

        update_agent(agent_id, update_last_seen=True)

        return jsonify({"status": "success", "agent_id": agent_id, "mode": mode, "result": result})

    except AgentConnectionError as e:
        return api_error(f"Cannot connect to agent: {e}", 503)
    except AgentHTTPError as e:
        return api_error(f"Agent error: {e}", 502)


@controller_bp.route("/agents/<int:agent_id>/<mode>/status", methods=["GET"])
def proxy_mode_status(agent_id: int, mode: str):
    """Get mode status from a remote agent."""
    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    try:
        client = create_client_from_agent(agent)
        result = client.get_mode_status(mode)

        return jsonify({"status": "success", "agent_id": agent_id, "mode": mode, "result": result})

    except (AgentHTTPError, AgentConnectionError) as e:
        return api_error(f"Agent error: {e}", 502)


@controller_bp.route("/agents/<int:agent_id>/<mode>/data", methods=["GET"])
def proxy_mode_data(agent_id: int, mode: str):
    """Get current data from a remote agent."""
    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    try:
        client = create_client_from_agent(agent)
        result = client.get_mode_data(mode)

        # Tag data with agent info
        result["agent_id"] = agent_id
        result["agent_name"] = agent["name"]

        return jsonify(
            {"status": "success", "agent_id": agent_id, "agent_name": agent["name"], "mode": mode, "data": result}
        )

    except (AgentHTTPError, AgentConnectionError) as e:
        return api_error(f"Agent error: {e}", 502)


@controller_bp.route("/agents/<int:agent_id>/<mode>/stream")
def proxy_mode_stream(agent_id: int, mode: str):
    """Proxy SSE stream from a remote agent."""
    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    client = create_client_from_agent(agent)
    query = request.query_string.decode("utf-8")
    url = f"{client.base_url}/{mode}/stream"
    if query:
        url = f"{url}?{query}"

    headers = {"Accept": "text/event-stream"}
    _stream_key = get_agent_api_key(agent["id"])
    if _stream_key:
        headers["X-API-Key"] = _stream_key

    def generate() -> Generator[str, None, None]:
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(5, 3600)) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=1024):
                    if not chunk:
                        continue
                    yield chunk.decode("utf-8", errors="ignore")
        except Exception as e:
            logger.error(f"SSE proxy error for agent {agent_id}/{mode}: {e}")
            yield format_sse(
                {
                    "type": "error",
                    "message": str(e),
                    "agent_id": agent_id,
                    "mode": mode,
                }
            )

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


# Endpoint prefixes that may be reached through the generic proxy. Extend this
# list instead of writing new per-endpoint proxy routes.
PROXY_ALLOWED_PREFIXES = ("wifi/v2/",)


@controller_bp.route("/agents/<int:agent_id>/proxy/<path:subpath>", methods=["GET"])
def proxy_passthrough(agent_id: int, subpath: str):
    """Forward an allowlisted GET to a remote agent.

    Keeps remote agents at feature parity with local mode without a
    hand-written proxy per endpoint.
    """
    # requests/urllib3 collapse dot segments before sending, so a subpath
    # like "wifi/v2/../../x" would escape the allowlist — accept only
    # canonical paths
    if posixpath.normpath("/" + subpath).lstrip("/") != subpath:
        return api_error("Endpoint not allowed through agent proxy", 403)

    if not subpath.startswith(PROXY_ALLOWED_PREFIXES):
        return api_error("Endpoint not allowed through agent proxy", 403)

    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    try:
        client = create_client_from_agent(agent)
        result = client.get(f"/{subpath}", params=request.args.to_dict())
        return jsonify(
            {
                "status": "success",
                "agent_id": agent_id,
                "result": result,
            }
        )
    except (AgentHTTPError, AgentConnectionError) as e:
        return api_error(f"Agent error: {e}", 502)


@controller_bp.route("/agents/<int:agent_id>/wifi/monitor", methods=["POST"])
def proxy_wifi_monitor(agent_id: int):
    """Toggle monitor mode on a remote agent's WiFi interface."""
    agent = get_agent(agent_id)
    if not agent:
        return api_error("Agent not found", 404)

    data = request.json or {}

    try:
        client = create_client_from_agent(agent)
        result = client.post("/wifi/monitor", data)

        # Refresh agent capabilities after monitor mode toggle so UI stays in sync
        if result.get("status") == "success":
            try:
                metadata = client.refresh_metadata()
                if metadata.get("healthy"):
                    caps = metadata.get("capabilities") or {}
                    agent_interfaces = caps.get("interfaces", {})
                    if not agent_interfaces.get("sdr_devices") and caps.get("devices"):
                        agent_interfaces["sdr_devices"] = caps.get("devices", [])
                    update_agent(
                        agent_id, capabilities=caps.get("modes"), interfaces=agent_interfaces, update_last_seen=True
                    )
            except Exception:
                pass  # Non-fatal if refresh fails

        return jsonify(
            {
                "status": result.get("status", "error"),
                "agent_id": agent_id,
                "agent_name": agent["name"],
                "monitor_interface": result.get("monitor_interface"),
                "message": result.get("message"),
            }
        )

    except AgentConnectionError as e:
        return api_error(f"Cannot connect to agent: {e}", 503)
    except AgentHTTPError as e:
        return api_error(f"Agent error: {e}", 502)


# =============================================================================
# Push Data Ingestion
# =============================================================================


@controller_bp.route("/api/ingest", methods=["POST"])
def ingest_push_data():
    """
    Receive pushed data from remote agents.

    Expected JSON body:
    {
        "agent_name": "sensor-node-1",
        "scan_type": "adsb",
        "interface": "rtlsdr0",
        "payload": {...},
        "received_at": "2024-01-15T10:30:00Z"
    }

    Expected header:
        X-API-Key: shared-secret (if agent has api_key configured)
    """
    data = request.json
    if not data:
        return api_error("No data provided", 400)

    agent_name = data.get("agent_name")
    if not agent_name:
        return api_error("agent_name required", 400)

    # Find agent
    agent = get_agent_by_name(agent_name)
    if not agent:
        return api_error("Unknown agent", 401)

    # Validate API key. This endpoint is reachable without a session so that
    # agents can push, so the key is the only thing authenticating the caller.
    # An agent registered without one previously accepted a push from anybody.
    expected_key = get_agent_api_key(agent["id"])
    if not expected_key:
        logger.warning(f"Rejected push from agent {agent_name}: no API key configured")
        return api_error("Agent has no API key configured; set one to enable push", 401)

    provided_key = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(provided_key, expected_key):
        logger.warning(f"Invalid API key from agent {agent_name}")
        return api_error("Invalid API key", 401)

    # Store payload
    try:
        payload_id = store_push_payload(
            agent_id=agent["id"],
            scan_type=data.get("scan_type", "unknown"),
            payload=data.get("payload", {}),
            interface=data.get("interface"),
            received_at=data.get("received_at"),
        )

        # Emit to SSE stream (fanout to all connected clients)
        _broadcast_agent_data(
            {
                "type": "agent_data",
                "agent_id": agent["id"],
                "agent_name": agent_name,
                "scan_type": data.get("scan_type"),
                "interface": data.get("interface"),
                "payload": data.get("payload"),
                "received_at": data.get("received_at") or datetime.now(timezone.utc).isoformat(),
            }
        )

        return jsonify({"status": "accepted", "payload_id": payload_id}), 202

    except Exception as e:
        logger.exception("Failed to store push payload")
        return api_error(str(e), 500)


@controller_bp.route("/api/payloads", methods=["GET"])
def get_payloads():
    """Get recent push payloads."""
    agent_id = request.args.get("agent_id", type=int)
    scan_type = request.args.get("scan_type")
    limit = request.args.get("limit", 100, type=int)

    payloads = get_recent_payloads(agent_id=agent_id, scan_type=scan_type, limit=min(limit, 1000))

    return jsonify({"status": "success", "payloads": payloads, "count": len(payloads)})


# =============================================================================
# Multi-Agent SSE Stream
# =============================================================================


@controller_bp.route("/stream/all")
def stream_all_agents():
    """
    Combined SSE stream for data from all agents.

    This endpoint streams push data as it arrives from agents.
    Each message is tagged with agent_id and agent_name.
    """
    client_queue: queue.Queue = queue.Queue(maxsize=_AGENT_STREAM_CLIENT_QUEUE_SIZE)
    with _agent_stream_subscribers_lock:
        _agent_stream_subscribers.add(client_queue)

    def generate() -> Generator[str, None, None]:
        last_keepalive = time.time()
        keepalive_interval = 30.0
        yield format_sse({"type": "keepalive"})

        try:
            while True:
                try:
                    msg = client_queue.get(timeout=1.0)
                    last_keepalive = time.time()
                    yield format_sse(msg)
                except queue.Empty:
                    now = time.time()
                    if now - last_keepalive >= keepalive_interval:
                        yield format_sse({"type": "keepalive"})
                        last_keepalive = now
        finally:
            with _agent_stream_subscribers_lock:
                _agent_stream_subscribers.discard(client_queue)

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


# =============================================================================
# Agent Management Page
# =============================================================================


@controller_bp.route("/manage")
def agent_management_page():
    """Render the agent management page."""
    from flask import render_template

    from config import VERSION

    return render_template("agents.html", version=VERSION)


@controller_bp.route("/monitor")
def network_monitor_page():
    """Render the network monitor page for multi-agent aggregated view."""
    from flask import render_template

    from config import VERSION

    return render_template("network_monitor.html", version=VERSION)


# =============================================================================
# Device Location Estimation (Trilateration)
# =============================================================================

# Global device location tracker
device_tracker = DeviceLocationTracker(
    trilateration=Trilateration(path_loss_model=PathLossModel("outdoor"), min_observations=2),
    observation_window_seconds=120.0,  # 2 minute window
    min_observations=2,
)


@controller_bp.route("/api/location/observe", methods=["POST"])
def add_location_observation():
    """
    Add an observation for device location estimation.

    Expected JSON body:
    {
        "device_id": "AA:BB:CC:DD:EE:FF",
        "agent_name": "sensor-node-1",
        "agent_lat": 40.7128,
        "agent_lon": -74.0060,
        "rssi": -55,
        "frequency_mhz": 2400  (optional)
    }

    Returns location estimate if enough data, null otherwise.
    """
    data = request.json or {}

    required = ["device_id", "agent_name", "agent_lat", "agent_lon", "rssi"]
    for field in required:
        if field not in data:
            return api_error(f"Missing required field: {field}", 400)

    # Look up agent GPS from database if not provided
    agent_lat = data.get("agent_lat")
    agent_lon = data.get("agent_lon")

    if agent_lat is None or agent_lon is None:
        agent = get_agent_by_name(data["agent_name"])
        if agent and agent.get("gps_coords"):
            coords = agent["gps_coords"]
            agent_lat = coords.get("lat") or coords.get("latitude")
            agent_lon = coords.get("lon") or coords.get("longitude")

    if agent_lat is None or agent_lon is None:
        return api_error("Agent GPS coordinates required", 400)

    estimate = device_tracker.add_observation(
        device_id=data["device_id"],
        agent_name=data["agent_name"],
        agent_lat=float(agent_lat),
        agent_lon=float(agent_lon),
        rssi=float(data["rssi"]),
        frequency_mhz=data.get("frequency_mhz"),
    )

    return jsonify(
        {"status": "success", "device_id": data["device_id"], "location": estimate.to_dict() if estimate else None}
    )


@controller_bp.route("/api/location/estimate", methods=["POST"])
def estimate_location():
    """
    Estimate device location from provided observations.

    Expected JSON body:
    {
        "observations": [
            {"agent_lat": 40.7128, "agent_lon": -74.0060, "rssi": -55, "agent_name": "node-1"},
            {"agent_lat": 40.7135, "agent_lon": -74.0055, "rssi": -70, "agent_name": "node-2"},
            {"agent_lat": 40.7120, "agent_lon": -74.0050, "rssi": -62, "agent_name": "node-3"}
        ],
        "environment": "outdoor"  (optional: outdoor, indoor, free_space)
    }
    """
    data = request.json or {}

    observations = data.get("observations", [])
    if len(observations) < 2:
        return api_error("At least 2 observations required", 400)

    environment = data.get("environment", "outdoor")

    try:
        result = estimate_location_from_observations(observations, environment)
        return jsonify({"status": "success" if result else "insufficient_data", "location": result})
    except Exception as e:
        logger.exception("Location estimation failed")
        return api_error(str(e), 500)


@controller_bp.route("/api/location/<device_id>", methods=["GET"])
def get_device_location(device_id: str):
    """Get the latest location estimate for a device."""
    estimate = device_tracker.get_location(device_id)

    if not estimate:
        return jsonify({"status": "not_found", "device_id": device_id, "location": None})

    return jsonify({"status": "success", "device_id": device_id, "location": estimate.to_dict()})


@controller_bp.route("/api/location/all", methods=["GET"])
def get_all_locations():
    """Get all current device location estimates."""
    locations = device_tracker.get_all_locations()

    return jsonify(
        {
            "status": "success",
            "count": len(locations),
            "devices": {device_id: estimate.to_dict() for device_id, estimate in locations.items()},
        }
    )


@controller_bp.route("/api/location/near", methods=["GET"])
def get_devices_near():
    """
    Find devices near a location.

    Query params:
        lat: latitude
        lon: longitude
        radius: radius in meters (default 100)
    """
    try:
        lat = float(request.args.get("lat", 0))
        lon = float(request.args.get("lon", 0))
        radius = float(request.args.get("radius", 100))
    except (ValueError, TypeError):
        return api_error("Invalid coordinates", 400)

    results = device_tracker.get_devices_near(lat, lon, radius)

    return jsonify(
        {
            "status": "success",
            "center": {"lat": lat, "lon": lon},
            "radius_meters": radius,
            "count": len(results),
            "devices": [{"device_id": device_id, "location": estimate.to_dict()} for device_id, estimate in results],
        }
    )
