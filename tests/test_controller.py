"""
Tests for Controller routes (multi-agent management).

Tests cover:
- Agent CRUD operations via HTTP
- Proxy operations to agents
- Push data ingestion
- SSE streaming
- Location estimation
"""

import json
import os
import sys
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def setup_db(tmp_path):
    """Set up a temporary database."""
    import utils.database as db_module
    from utils.database import init_db

    test_db_path = tmp_path / "test.db"
    original_db_path = db_module.DB_PATH
    db_module.DB_PATH = test_db_path
    db_module.DB_DIR = tmp_path

    if hasattr(db_module._local, "connection") and db_module._local.connection:
        db_module._local.connection.close()
        db_module._local.connection = None

    init_db()

    yield

    if hasattr(db_module._local, "connection") and db_module._local.connection:
        db_module._local.connection.close()
        db_module._local.connection = None
    db_module.DB_PATH = original_db_path


@pytest.fixture
def app(setup_db):
    """Create Flask app with controller blueprint."""
    from flask import Flask

    from routes.controller import controller_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test-secret-for-session-auth"
    app.register_blueprint(controller_bp)

    return app


@pytest.fixture
def client(app):
    """Create an AUTHENTICATED test client.

    Controller routes require a session (or an agent API key). These tests
    exercise the behaviour of the routes, not the auth gate, so they log in.
    Anonymous access is covered separately in TestControllerAuth.
    """
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["logged_in"] = True
    return c


@pytest.fixture
def anon_client(app):
    """An unauthenticated client, for testing the auth gate itself."""
    return app.test_client()


@pytest.fixture
def sample_agent(setup_db):
    """Create a sample agent in database."""
    from utils.database import create_agent

    agent_id = create_agent(
        name="test-sensor",
        base_url="http://192.168.1.50:8020",
        api_key="test-key",
        description="Test sensor node",
        capabilities={"adsb": True, "wifi": True},
        gps_coords={"lat": 40.7128, "lon": -74.0060},
    )
    return agent_id


# =============================================================================
# Agent CRUD Tests
# =============================================================================


class TestAgentCRUD:
    """Tests for agent CRUD operations."""

    def test_list_agents_empty(self, client):
        """GET /controller/agents should return empty list initially."""
        response = client.get("/controller/agents")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"
        assert data["agents"] == []
        assert data["count"] == 0

    def test_register_agent_success(self, client):
        """POST /controller/agents should register new agent."""
        with patch("routes.controller.AgentClient") as MockClient:
            # Mock successful capability fetch
            mock_instance = Mock()
            mock_instance.get_capabilities.return_value = {
                "modes": {"adsb": True, "wifi": True},
                "devices": [{"name": "RTL-SDR"}],
            }
            MockClient.return_value = mock_instance

            response = client.post(
                "/controller/agents",
                json={
                    "name": "new-sensor",
                    "base_url": "http://192.168.1.51:8020",
                    "api_key": "secret123",
                    "description": "New sensor node",
                },
                content_type="application/json",
            )

            assert response.status_code == 201
            data = json.loads(response.data)
            assert data["status"] == "success"
            assert data["agent"]["name"] == "new-sensor"

    def test_register_agent_missing_name(self, client):
        """POST /controller/agents should reject missing name."""
        response = client.post(
            "/controller/agents", json={"base_url": "http://localhost:8020"}, content_type="application/json"
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "name is required" in data["message"]

    def test_register_agent_missing_url(self, client):
        """POST /controller/agents should reject missing URL."""
        response = client.post("/controller/agents", json={"name": "test-sensor"}, content_type="application/json")

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Base URL is required" in data["message"]

    def test_register_agent_duplicate_name(self, client, sample_agent):
        """POST /controller/agents should reject duplicate name."""
        response = client.post(
            "/controller/agents",
            json={
                "name": "test-sensor",  # Same as sample_agent
                "base_url": "http://192.168.1.60:8020",
            },
            content_type="application/json",
        )

        assert response.status_code == 409
        data = json.loads(response.data)
        assert "already exists" in data["message"]

    def test_list_agents_with_agents(self, client, sample_agent):
        """GET /controller/agents should return registered agents."""
        response = client.get("/controller/agents")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["count"] >= 1

        names = [a["name"] for a in data["agents"]]
        assert "test-sensor" in names

    def test_get_agent_detail(self, client, sample_agent):
        """GET /controller/agents/<id> should return agent details."""
        response = client.get(f"/controller/agents/{sample_agent}")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"
        assert data["agent"]["name"] == "test-sensor"
        assert data["agent"]["capabilities"]["adsb"] is True

    def test_get_agent_not_found(self, client):
        """GET /controller/agents/<id> should return 404 for missing agent."""
        response = client.get("/controller/agents/99999")
        assert response.status_code == 404

    def test_update_agent(self, client, sample_agent):
        """PATCH /controller/agents/<id> should update agent."""
        response = client.patch(
            f"/controller/agents/{sample_agent}",
            json={"description": "Updated description"},
            content_type="application/json",
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["agent"]["description"] == "Updated description"

    def test_delete_agent(self, client, sample_agent):
        """DELETE /controller/agents/<id> should remove agent."""
        response = client.delete(f"/controller/agents/{sample_agent}")
        assert response.status_code == 200

        # Verify deleted
        response = client.get(f"/controller/agents/{sample_agent}")
        assert response.status_code == 404


# =============================================================================
# Proxy Operation Tests
# =============================================================================


class TestProxyOperations:
    """Tests for proxying operations to agents."""

    def test_proxy_start_mode(self, client, sample_agent):
        """POST /controller/agents/<id>/<mode>/start should proxy to agent."""
        with patch("routes.controller.create_client_from_agent") as mock_create:
            mock_client = Mock()
            mock_client.start_mode.return_value = {"status": "started", "mode": "adsb"}
            mock_create.return_value = mock_client

            response = client.post(
                f"/controller/agents/{sample_agent}/adsb/start",
                json={"device_index": 0},
                content_type="application/json",
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "success"
            assert data["mode"] == "adsb"

            mock_client.start_mode.assert_called_once_with("adsb", {"device_index": 0})

    def test_proxy_stop_mode(self, client, sample_agent):
        """POST /controller/agents/<id>/<mode>/stop should proxy to agent."""
        with patch("routes.controller.create_client_from_agent") as mock_create:
            mock_client = Mock()
            mock_client.stop_mode.return_value = {"status": "stopped"}
            mock_create.return_value = mock_client

            response = client.post(f"/controller/agents/{sample_agent}/wifi/stop", content_type="application/json")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "success"

    def test_proxy_get_mode_data(self, client, sample_agent):
        """GET /controller/agents/<id>/<mode>/data should return data."""
        with patch("routes.controller.create_client_from_agent") as mock_create:
            mock_client = Mock()
            mock_client.get_mode_data.return_value = {"mode": "adsb", "data": [{"icao": "ABC123"}]}
            mock_create.return_value = mock_client

            response = client.get(f"/controller/agents/{sample_agent}/adsb/data")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "success"
            assert "agent_name" in data
            assert data["agent_name"] == "test-sensor"

    def test_proxy_agent_not_found(self, client):
        """Proxy operations should return 404 for missing agent."""
        response = client.post("/controller/agents/99999/adsb/start")
        assert response.status_code == 404

    def test_proxy_connection_error(self, client, sample_agent):
        """Proxy should return 503 when agent unreachable."""
        from utils.agent_client import AgentConnectionError

        with patch("routes.controller.create_client_from_agent") as mock_create:
            mock_client = Mock()
            mock_client.start_mode.side_effect = AgentConnectionError("Connection refused")
            mock_create.return_value = mock_client

            response = client.post(
                f"/controller/agents/{sample_agent}/adsb/start", json={}, content_type="application/json"
            )

            assert response.status_code == 503
            data = json.loads(response.data)
            assert "Cannot connect" in data["message"]


# =============================================================================
# Push Data Ingestion Tests
# =============================================================================


class TestPushIngestion:
    """Tests for push data ingestion endpoint."""

    def test_ingest_success(self, client, sample_agent):
        """POST /controller/api/ingest should store payload."""
        payload = {
            "agent_name": "test-sensor",
            "scan_type": "adsb",
            "interface": "rtlsdr0",
            "payload": {"aircraft": [{"icao": "ABC123", "altitude": 35000}]},
        }

        response = client.post(
            "/controller/api/ingest", json=payload, headers={"X-API-Key": "test-key"}, content_type="application/json"
        )

        assert response.status_code == 202
        data = json.loads(response.data)
        assert data["status"] == "accepted"
        assert "payload_id" in data

    def test_ingest_unknown_agent(self, client):
        """POST /controller/api/ingest should reject unknown agent."""
        payload = {"agent_name": "nonexistent-sensor", "scan_type": "adsb", "payload": {}}

        response = client.post("/controller/api/ingest", json=payload, content_type="application/json")

        assert response.status_code == 401
        data = json.loads(response.data)
        assert "Unknown agent" in data["message"]

    def test_ingest_invalid_api_key(self, client, sample_agent):
        """POST /controller/api/ingest should reject invalid API key."""
        payload = {"agent_name": "test-sensor", "scan_type": "adsb", "payload": {}}

        response = client.post(
            "/controller/api/ingest", json=payload, headers={"X-API-Key": "wrong-key"}, content_type="application/json"
        )

        assert response.status_code == 401
        data = json.loads(response.data)
        assert "Invalid API key" in data["message"]

    def test_ingest_missing_agent_name(self, client):
        """POST /controller/api/ingest should require agent_name."""
        response = client.post(
            "/controller/api/ingest", json={"scan_type": "adsb", "payload": {}}, content_type="application/json"
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "agent_name required" in data["message"]

    def test_get_payloads(self, client, sample_agent):
        """GET /controller/api/payloads should return stored payloads."""
        # First ingest some data
        for i in range(3):
            client.post(
                "/controller/api/ingest",
                json={
                    "agent_name": "test-sensor",
                    "scan_type": "adsb",
                    "payload": {"aircraft": [{"icao": f"TEST{i}"}]},
                },
                headers={"X-API-Key": "test-key"},
                content_type="application/json",
            )

        response = client.get(f"/controller/api/payloads?agent_id={sample_agent}")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["count"] == 3

    def test_get_payloads_filter_by_type(self, client, sample_agent):
        """GET /controller/api/payloads should filter by scan_type."""
        # Ingest mixed data
        client.post(
            "/controller/api/ingest",
            json={"agent_name": "test-sensor", "scan_type": "adsb", "payload": {}},
            headers={"X-API-Key": "test-key"},
            content_type="application/json",
        )
        client.post(
            "/controller/api/ingest",
            json={"agent_name": "test-sensor", "scan_type": "wifi", "payload": {}},
            headers={"X-API-Key": "test-key"},
            content_type="application/json",
        )

        response = client.get("/controller/api/payloads?scan_type=adsb")
        data = json.loads(response.data)

        assert all(p["scan_type"] == "adsb" for p in data["payloads"])


# =============================================================================
# Location Estimation Tests
# =============================================================================


class TestLocationEstimation:
    """Tests for device location estimation (trilateration)."""

    def test_add_observation(self, client):
        """POST /controller/api/location/observe should accept observation."""
        response = client.post(
            "/controller/api/location/observe",
            json={
                "device_id": "AA:BB:CC:DD:EE:FF",
                "agent_name": "sensor-1",
                "agent_lat": 40.7128,
                "agent_lon": -74.0060,
                "rssi": -55,
            },
            content_type="application/json",
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "success"
        assert data["device_id"] == "AA:BB:CC:DD:EE:FF"

    def test_add_observation_missing_fields(self, client):
        """POST /controller/api/location/observe should require all fields."""
        response = client.post(
            "/controller/api/location/observe",
            json={
                "device_id": "AA:BB:CC:DD:EE:FF",
                "rssi": -55,
                # Missing agent_name, agent_lat, agent_lon
            },
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_estimate_location(self, client):
        """POST /controller/api/location/estimate should compute location."""
        response = client.post(
            "/controller/api/location/estimate",
            json={
                "observations": [
                    {"agent_lat": 40.7128, "agent_lon": -74.0060, "rssi": -55, "agent_name": "node-1"},
                    {"agent_lat": 40.7135, "agent_lon": -74.0055, "rssi": -70, "agent_name": "node-2"},
                    {"agent_lat": 40.7120, "agent_lon": -74.0050, "rssi": -62, "agent_name": "node-3"},
                ],
                "environment": "outdoor",
            },
            content_type="application/json",
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        # Should have computed a location
        if data["location"]:
            assert "latitude" in data["location"]
            assert "longitude" in data["location"]

    def test_estimate_location_insufficient_data(self, client):
        """Estimation should require at least 2 observations."""
        response = client.post(
            "/controller/api/location/estimate",
            json={"observations": [{"agent_lat": 40.7128, "agent_lon": -74.0060, "rssi": -55, "agent_name": "node-1"}]},
            content_type="application/json",
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "At least 2" in data["message"]

    def test_get_device_location_not_found(self, client):
        """GET /controller/api/location/<device_id> returns not_found for unknown device."""
        response = client.get("/controller/api/location/unknown-device")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "not_found"
        assert data["location"] is None

    def test_get_all_locations(self, client):
        """GET /controller/api/location/all should return all estimates."""
        response = client.get("/controller/api/location/all")
        assert response.status_code == 200

        data = json.loads(response.data)
        assert data["status"] == "success"
        assert "devices" in data

    def test_get_devices_near(self, client):
        """GET /controller/api/location/near should find nearby devices."""
        response = client.get(
            "/controller/api/location/near", query_string={"lat": 40.7128, "lon": -74.0060, "radius": 100}
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "success"
        assert data["center"]["lat"] == 40.7128


# =============================================================================
# Agent Refresh Tests
# =============================================================================


class TestAgentRefresh:
    """Tests for agent refresh operations."""

    def test_refresh_agent_success(self, client, sample_agent):
        """POST /controller/agents/<id>/refresh should update metadata."""
        with patch("routes.controller.create_client_from_agent") as mock_create:
            mock_client = Mock()
            mock_client.refresh_metadata.return_value = {
                "healthy": True,
                "capabilities": {
                    "modes": {"adsb": True, "wifi": True, "bluetooth": True},
                    "devices": [{"name": "RTL-SDR V3"}],
                },
                "status": {"running_modes": ["adsb"]},
                "config": {},
            }
            mock_create.return_value = mock_client

            response = client.post(f"/controller/agents/{sample_agent}/refresh")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "success"
            assert data["metadata"]["healthy"] is True

    def test_refresh_agent_unreachable(self, client, sample_agent):
        """POST /controller/agents/<id>/refresh should return 503 if unreachable."""
        with patch("routes.controller.create_client_from_agent") as mock_create:
            mock_client = Mock()
            mock_client.refresh_metadata.return_value = {"healthy": False}
            mock_create.return_value = mock_client

            response = client.post(f"/controller/agents/{sample_agent}/refresh")

            assert response.status_code == 503


# =============================================================================
# SSE Stream Tests
# =============================================================================


class TestSSEStream:
    """Tests for SSE streaming endpoint."""

    def test_stream_all_endpoint_exists(self, client):
        """GET /controller/stream/all should exist and return SSE."""
        # Just verify the endpoint is accessible
        # Full SSE testing requires more complex setup
        response = client.get("/controller/stream/all")
        assert response.mimetype == "text/event-stream"


# =============================================================================
# Generic Proxy Tests
# =============================================================================


class TestGenericProxy:
    """Tests for the allowlisted agent passthrough proxy."""

    def _mock_agent(self):
        return {"id": 1, "name": "node-1", "base_url": "http://10.0.0.2:5000", "api_key": None}

    def test_proxies_allowlisted_get(self, client):
        with (
            patch("routes.controller.get_agent", return_value=self._mock_agent()),
            patch("routes.controller.create_client_from_agent") as mock_create,
        ):
            mock_create.return_value.get.return_value = [{"mac": "AA:BB"}]
            resp = client.get("/controller/agents/1/proxy/wifi/v2/clients?bssid=AA:BB")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert data["result"] == [{"mac": "AA:BB"}]
        mock_create.return_value.get.assert_called_once_with("/wifi/v2/clients", params={"bssid": "AA:BB"})

    def test_rejects_non_allowlisted_path(self, client):
        with patch("routes.controller.get_agent", return_value=self._mock_agent()):
            resp = client.get("/controller/agents/1/proxy/settings/secrets")
        assert resp.status_code == 403

    def test_unknown_agent_404(self, client):
        with patch("routes.controller.get_agent", return_value=None):
            resp = client.get("/controller/agents/99/proxy/wifi/v2/clients")
        assert resp.status_code == 404

    def test_rejects_dot_segment_traversal(self, client):
        # Werkzeug may normalize or reject ".." segments before the view runs,
        # so the view might never be reached (404). What matters is that the
        # request does NOT succeed (200) and the agent is never contacted.
        with (
            patch("routes.controller.get_agent", return_value=self._mock_agent()),
            patch("routes.controller.create_client_from_agent") as mock_create,
        ):
            resp = client.get("/controller/agents/1/proxy/wifi/v2/../../settings/secrets")
        # Any status except 200/502 is safe; the agent must not have been called.
        assert resp.status_code not in (200, 502)
        mock_create.return_value.get.assert_not_called()

    def test_rejects_encoded_traversal(self, client):
        # Percent-encoded dots (%2e%2e) — Werkzeug or the test client may
        # decode and normalize these before routing (404), or our canonicality
        # check catches them (403). Either way the agent must not be contacted.
        with (
            patch("routes.controller.get_agent", return_value=self._mock_agent()),
            patch("routes.controller.create_client_from_agent") as mock_create,
        ):
            resp = client.get("/controller/agents/1/proxy/wifi/v2/%2e%2e/%2e%2e/settings/secrets")
        assert resp.status_code in (403, 404)
        mock_create.return_value.get.assert_not_called()


# =============================================================================
# Authentication and secret handling
# =============================================================================


class TestControllerAuth:
    """The controller blueprint must authenticate every route.

    app.py's global gate deliberately skips /controller/* so that remote
    agents can authenticate with an API key instead of a session. That left
    every route here reachable anonymously, including agent registration and
    the proxy that starts and stops SDR modes on remote nodes.
    """

    def test_anonymous_cannot_list_agents(self, anon_client, sample_agent):
        assert anon_client.get("/controller/agents").status_code == 401

    def test_anonymous_cannot_register_agent(self, anon_client):
        resp = anon_client.post(
            "/controller/agents",
            json={"name": "rogue", "base_url": "http://attacker.example"},
        )
        assert resp.status_code == 401

    def test_anonymous_cannot_delete_agent(self, anon_client, sample_agent):
        assert anon_client.delete(f"/controller/agents/{sample_agent}").status_code == 401

    def test_anonymous_cannot_proxy_mode_start(self, anon_client, sample_agent):
        """The most serious case: starting SDR hardware on a remote node."""
        resp = anon_client.post(f"/controller/agents/{sample_agent}/adsb/start", json={})
        assert resp.status_code == 401

    def test_anonymous_cannot_proxy_mode_stop(self, anon_client, sample_agent):
        resp = anon_client.post(f"/controller/agents/{sample_agent}/adsb/stop", json={})
        assert resp.status_code == 401

    def test_anonymous_cannot_read_payloads(self, anon_client):
        assert anon_client.get("/controller/api/payloads").status_code == 401

    def test_authenticated_session_is_allowed(self, client, sample_agent):
        assert client.get("/controller/agents").status_code == 200


class TestAgentApiKeyNotDisclosed:
    """Agent records are returned to browsers; the API key must not be."""

    def test_api_key_absent_from_agent_list(self, client, sample_agent):
        resp = client.get("/controller/agents")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "test-key" not in body, "agent API key leaked in list response"
        for agent in resp.get_json()["agents"]:
            assert "api_key" not in agent
            assert agent["has_api_key"] is True

    def test_api_key_absent_from_agent_detail(self, client, sample_agent):
        resp = client.get(f"/controller/agents/{sample_agent}")
        assert "test-key" not in resp.get_data(as_text=True)

    def test_key_is_still_retrievable_internally(self, sample_agent):
        """Internal callers must still be able to reach the real key."""
        from utils.database import get_agent_api_key

        assert get_agent_api_key(sample_agent) == "test-key"


class TestAgentPushAuth:
    """The ingest endpoint is session-exempt, so its API key is the only auth."""

    def test_push_without_key_is_refused(self, anon_client, sample_agent):
        resp = anon_client.post(
            "/controller/api/ingest",
            json={"agent_name": "test-sensor", "scan_type": "adsb", "payload": {}},
        )
        assert resp.status_code == 401

    def test_push_with_wrong_key_is_refused(self, anon_client, sample_agent):
        resp = anon_client.post(
            "/controller/api/ingest",
            json={"agent_name": "test-sensor", "scan_type": "adsb", "payload": {}},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_push_with_correct_key_is_accepted(self, anon_client, sample_agent):
        resp = anon_client.post(
            "/controller/api/ingest",
            json={"agent_name": "test-sensor", "scan_type": "adsb", "payload": {"a": 1}},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code in (200, 202)
