"""Tests for Meshtastic integration.

Tests cover:
- MeshtasticClient initialization and state management
- PSK parsing (various formats)
- Message callback handling
- Route endpoints (mocked)
- Graceful degradation when SDK not installed
"""

import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

# =============================================================================
# Utility Module Tests
# =============================================================================


class TestMeshtasticAvailability:
    """Tests for SDK availability checks."""

    def test_is_meshtastic_available_returns_bool(self):
        """is_meshtastic_available should return a boolean."""
        from utils.meshtastic import is_meshtastic_available

        result = is_meshtastic_available()
        assert isinstance(result, bool)


class TestMeshtasticMessage:
    """Tests for MeshtasticMessage dataclass."""

    def test_message_to_dict(self):
        """MeshtasticMessage should convert to dictionary."""
        from utils.meshtastic import MeshtasticMessage

        msg = MeshtasticMessage(
            from_id="!a1b2c3d4",
            to_id="^all",
            message="Hello mesh!",
            portnum="TEXT_MESSAGE_APP",
            channel=0,
            rssi=-95,
            snr=-3.5,
            hop_limit=3,
            timestamp=datetime(2026, 1, 27, 12, 0, 0, tzinfo=timezone.utc),
        )

        d = msg.to_dict()

        assert d["type"] == "meshtastic"
        assert d["from"] == "!a1b2c3d4"
        assert d["to"] == "^all"
        assert d["message"] == "Hello mesh!"
        assert d["portnum"] == "TEXT_MESSAGE_APP"
        assert d["channel"] == 0
        assert d["rssi"] == -95
        assert d["snr"] == -3.5
        assert d["hop_limit"] == 3
        assert isinstance(d["timestamp"], float)
        # 2026-01-27 12:00:00 UTC as Unix epoch
        assert d["timestamp"] == pytest.approx(1769515200.0)

    def test_message_with_none_values(self):
        """MeshtasticMessage should handle None values."""
        from utils.meshtastic import MeshtasticMessage

        msg = MeshtasticMessage(
            from_id="!00000001",
            to_id="!00000002",
            message=None,
            portnum="POSITION_APP",
            channel=1,
            rssi=None,
            snr=None,
            hop_limit=None,
            timestamp=datetime.now(timezone.utc),
        )

        d = msg.to_dict()

        assert d["message"] is None
        assert d["rssi"] is None
        assert d["snr"] is None


class TestChannelConfig:
    """Tests for ChannelConfig dataclass."""

    def test_channel_to_dict_hides_psk(self):
        """ChannelConfig.to_dict should not expose raw PSK."""
        from utils.meshtastic import ChannelConfig

        config = ChannelConfig(
            index=0,
            name="Primary",
            psk=b"\x01\x02\x03\x04" * 8,  # 32-byte key
            role=1,  # PRIMARY
        )

        d = config.to_dict()

        assert "psk" not in d  # Raw PSK should not be in dict
        assert d["index"] == 0
        assert d["name"] == "Primary"
        assert d["role"] == "PRIMARY"
        assert d["encrypted"] is True
        assert d["key_type"] == "AES-256"

    def test_channel_default_key_detection(self):
        """ChannelConfig should detect default key."""
        from utils.meshtastic import ChannelConfig

        # Default key is single byte 0x01
        config = ChannelConfig(index=0, name="Test", psk=b"\x01", role=1)
        d = config.to_dict()

        assert d["is_default_key"] is True
        assert d["key_type"] == "default"

    def test_channel_aes128_detection(self):
        """ChannelConfig should detect AES-128 key."""
        from utils.meshtastic import ChannelConfig

        config = ChannelConfig(index=0, name="Test", psk=b"0" * 16, role=1)
        d = config.to_dict()

        assert d["key_type"] == "AES-128"
        assert d["encrypted"] is True

    def test_channel_no_encryption(self):
        """ChannelConfig should detect no encryption."""
        from utils.meshtastic import ChannelConfig

        config = ChannelConfig(index=0, name="Test", psk=b"", role=1)
        d = config.to_dict()

        assert d["key_type"] == "none"
        assert d["encrypted"] is False


class TestPSKParsing:
    """Tests for PSK format parsing."""

    def test_parse_psk_none(self):
        """Should parse 'none' as empty bytes."""
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        result = client._parse_psk("none")

        assert result == b""

    def test_parse_psk_default(self):
        """Should parse 'default' as single byte."""
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        result = client._parse_psk("default")

        assert result == b"\x01"

    def test_parse_psk_random(self):
        """Should generate 32 random bytes for 'random'."""
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        result = client._parse_psk("random")

        assert len(result) == 32
        # Verify it's actually random (two calls should differ)
        result2 = client._parse_psk("random")
        assert result != result2

    def test_parse_psk_base64(self):
        """Should decode base64 PSK."""
        import base64

        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        # 32-byte key encoded as base64
        key = b"A" * 32
        encoded = "base64:" + base64.b64encode(key).decode()

        result = client._parse_psk(encoded)

        assert result == key

    def test_parse_psk_hex(self):
        """Should decode hex PSK."""
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        # 16-byte key as hex
        result = client._parse_psk("0x" + "41" * 16)

        assert result == b"A" * 16

    def test_parse_psk_simple_passphrase(self):
        """Should hash simple passphrase to 32-byte key."""
        import hashlib

        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        result = client._parse_psk("simple:MySecretPassword")

        expected = hashlib.sha256(b"MySecretPassword").digest()
        assert result == expected
        assert len(result) == 32

    def test_parse_psk_invalid(self):
        """Should return None for invalid PSK format."""
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()

        assert client._parse_psk("base64:!!!invalid!!!") is None
        assert client._parse_psk("0xZZZZ") is None

    def test_parse_psk_raw_base64(self):
        """Should accept raw base64 without prefix."""
        import base64

        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        key = b"B" * 16
        encoded = base64.b64encode(key).decode()

        result = client._parse_psk(encoded)

        assert result == key


class TestNodeIdFormatting:
    """Tests for node ID formatting."""

    def test_format_regular_node(self):
        """Should format regular node as hex."""
        from utils.meshtastic import MeshtasticClient

        result = MeshtasticClient._format_node_id(0xDEADBEEF)

        assert result == "!deadbeef"

    def test_format_broadcast(self):
        """Should format broadcast address."""
        from utils.meshtastic import MeshtasticClient

        result = MeshtasticClient._format_node_id(0xFFFFFFFF)

        assert result == "^all"


# =============================================================================
# Route Tests (Mocked)
# =============================================================================


class TestMeshtasticRoutes:
    """Tests for Flask route endpoints."""

    @pytest.fixture
    def app(self):
        """Create Flask test app."""
        from flask import Flask

        from routes.meshtastic import meshtastic_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(meshtastic_bp)

        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()

    def test_status_sdk_not_installed(self, client):
        """GET /meshtastic/status should report SDK unavailable."""
        with patch("routes.meshtastic.is_meshtastic_available", return_value=False):
            response = client.get("/meshtastic/status")
            data = json.loads(response.data)

            assert response.status_code == 200
            assert data["available"] is False
            assert "not installed" in data["error"]

    def test_status_not_connected(self, client):
        """GET /meshtastic/status should report not running when disconnected."""
        with patch("routes.meshtastic.is_meshtastic_available", return_value=True):
            with patch("routes.meshtastic.get_meshtastic_client", return_value=None):
                response = client.get("/meshtastic/status")
                data = json.loads(response.data)

                assert response.status_code == 200
                assert data["available"] is True
                assert data["running"] is False

    def test_start_sdk_not_installed(self, client):
        """POST /meshtastic/start should fail if SDK not installed."""
        with patch("routes.meshtastic.is_meshtastic_available", return_value=False):
            response = client.post("/meshtastic/start")
            data = json.loads(response.data)

            assert response.status_code == 400
            assert data["status"] == "error"

    def test_stop_always_succeeds(self, client):
        """POST /meshtastic/stop should always succeed."""
        with patch("routes.meshtastic.stop_meshtastic"):
            response = client.post("/meshtastic/stop")
            data = json.loads(response.data)

            assert response.status_code == 200
            assert data["status"] == "stopped"

    def test_channels_not_connected(self, client):
        """GET /meshtastic/channels should fail if not connected."""
        with patch("routes.meshtastic.get_meshtastic_client", return_value=None):
            response = client.get("/meshtastic/channels")
            data = json.loads(response.data)

            assert response.status_code == 400
            assert "Not connected" in data["message"]

    def test_configure_channel_invalid_index(self, client):
        """POST /meshtastic/channels/<id> should reject invalid index."""
        mock_client = Mock()
        mock_client.is_running = True

        with patch("routes.meshtastic.get_meshtastic_client", return_value=mock_client):
            response = client.post("/meshtastic/channels/10", json={"name": "Test"}, content_type="application/json")
            data = json.loads(response.data)

            assert response.status_code == 400
            assert "must be 0-7" in data["message"]

    def test_configure_channel_no_params(self, client):
        """POST /meshtastic/channels/<id> should require name or psk."""
        mock_client = Mock()
        mock_client.is_running = True

        with patch("routes.meshtastic.get_meshtastic_client", return_value=mock_client):
            response = client.post("/meshtastic/channels/0", json={}, content_type="application/json")
            data = json.loads(response.data)

            assert response.status_code == 400
            assert "Must provide" in data["message"]

    def test_messages_empty(self, client):
        """GET /meshtastic/messages should return empty list initially."""
        with patch("routes.meshtastic._recent_messages", []):
            response = client.get("/meshtastic/messages")
            data = json.loads(response.data)

            assert response.status_code == 200
            assert data["status"] == "ok"
            assert data["messages"] == []
            assert data["count"] == 0

    def test_messages_with_limit(self, client):
        """GET /meshtastic/messages should respect limit param."""
        test_messages = [{"id": i} for i in range(10)]

        with patch("routes.meshtastic._recent_messages", test_messages):
            response = client.get("/meshtastic/messages?limit=3")
            data = json.loads(response.data)

            assert response.status_code == 200
            assert len(data["messages"]) == 3
            # Should return last 3 (most recent)
            assert data["messages"][0]["id"] == 7

    def test_messages_filter_by_channel(self, client):
        """GET /meshtastic/messages should filter by channel."""
        test_messages = [
            {"id": 1, "channel": 0},
            {"id": 2, "channel": 1},
            {"id": 3, "channel": 0},
        ]

        with patch("routes.meshtastic._recent_messages", test_messages):
            response = client.get("/meshtastic/messages?channel=0")
            data = json.loads(response.data)

            assert response.status_code == 200
            assert len(data["messages"]) == 2
            assert all(m["channel"] == 0 for m in data["messages"])

    def test_stream_endpoint_exists(self, client):
        """GET /meshtastic/stream should return SSE content type."""
        response = client.get("/meshtastic/stream")

        assert response.content_type.startswith("text/event-stream")

    def test_node_not_connected(self, client):
        """GET /meshtastic/node should fail if not connected."""
        with patch("routes.meshtastic.get_meshtastic_client", return_value=None):
            response = client.get("/meshtastic/node")
            data = json.loads(response.data)

            assert response.status_code == 400
            assert "Not connected" in data["message"]


# =============================================================================
# Integration Tests (Mocked SDK)
# =============================================================================


class TestMeshtasticClientMocked:
    """Tests for MeshtasticClient with mocked SDK."""

    def test_client_init(self):
        """MeshtasticClient should initialize with default state."""
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()

        assert client.is_running is False
        assert client.device_path is None
        assert client.error is None

    def test_client_connect_no_sdk(self):
        """MeshtasticClient.connect should fail gracefully without SDK."""
        from utils.meshtastic import MeshtasticClient

        with patch("utils.meshtastic.HAS_MESHTASTIC", False):
            client = MeshtasticClient()
            result = client.connect()

            assert result is False
            assert "not installed" in client.error

    def test_client_disconnect_idempotent(self):
        """MeshtasticClient.disconnect should be safe to call multiple times."""
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()

        # Should not raise even when not connected
        client.disconnect()
        client.disconnect()

        assert client.is_running is False


class TestMeshtasticOwnNodeGpsFeedsObserverLocation:
    """Tests that only OUR OWN node's position feeds utils.gps, never a mesh peer's."""

    def test_is_local_node_true_when_matches_my_info(self):
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        client._interface = Mock()
        client._interface.myInfo = Mock(my_node_num=0x55890AEB)

        assert client._is_local_node(0x55890AEB) is True
        assert client._is_local_node(0x1234) is False

    def test_is_local_node_false_when_no_interface(self):
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        client._interface = None

        assert client._is_local_node(0x55890AEB) is False

    def test_own_node_position_registers_external_gps_position(self):
        """Our own node's GPS fix should be pushed into utils.gps as an external position."""
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        client._interface = Mock()
        client._interface.myInfo = Mock(my_node_num=0x55890AEB)

        packet = {"from": 0x55890AEB}
        decoded = {"position": {"latitude": -33.9, "longitude": 18.4, "altitude": 15}}

        with patch("utils.gps.set_external_position") as mock_set_pos:
            client._track_node_from_packet(packet, decoded, "POSITION_APP")

        mock_set_pos.assert_called_once()
        (pos,), _ = mock_set_pos.call_args
        assert pos.latitude == -33.9
        assert pos.longitude == 18.4
        assert pos.altitude == 15
        assert pos.device == "meshtastic:!55890aeb"

    def test_peer_node_position_does_not_register_external_gps_position(self):
        """A remote mesh node's position must never overwrite our observer location."""
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        client._interface = Mock()
        client._interface.myInfo = Mock(my_node_num=0x55890AEB)  # our own node

        packet = {"from": 0x336437CC}  # a different, remote node
        decoded = {"position": {"latitude": -34.15, "longitude": 18.32}}

        with patch("utils.gps.set_external_position") as mock_set_pos:
            client._track_node_from_packet(packet, decoded, "POSITION_APP")

        mock_set_pos.assert_not_called()

    def test_gps_registration_failure_does_not_break_tracking(self):
        from utils.meshtastic import MeshtasticClient

        client = MeshtasticClient()
        client._interface = Mock()
        client._interface.myInfo = Mock(my_node_num=0x55890AEB)

        packet = {"from": 0x55890AEB}
        decoded = {"position": {"latitude": -33.9, "longitude": 18.4}}

        with patch("utils.gps.set_external_position", side_effect=RuntimeError("boom")):
            client._track_node_from_packet(packet, decoded, "POSITION_APP")

        node = client._nodes[0x55890AEB]
        assert node.latitude == -33.9
        assert node.longitude == 18.4
