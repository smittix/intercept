"""Route tests for the Reticulum blueprint (client mocked)."""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from routes.reticulum import reticulum_bp
from utils.reticulum import ReticulumConfig

HASH = "a" * 32


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(reticulum_bp)
    return app.test_client()


@pytest.fixture(autouse=True)
def rns_client():
    rc = MagicMock()
    rc.get_status.return_value = {"state": "stopped", "message": None, "running": False}
    rc.get_nodes.return_value = [{"dest_hash": HASH, "name": "n", "hops": 1, "first_heard": 1.0, "last_heard": 2.0}]
    rc.get_conversations.return_value = []
    rc.get_messages.return_value = []
    with (
        patch("routes.reticulum.get_reticulum_client", return_value=rc),
        patch("routes.reticulum.is_reticulum_available", return_value=True),
        patch("routes.reticulum.default_config", return_value=ReticulumConfig()),
        patch("routes.reticulum.save_config") as save,
    ):
        rc.save = save
        yield rc


class TestStatus:
    def test_status(self, client):
        d = client.get("/reticulum/status").get_json()
        assert d["available"] is True
        assert d["state"] == "stopped"

    def test_unavailable(self, client):
        with patch("routes.reticulum.is_reticulum_available", return_value=False):
            d = client.get("/reticulum/status").get_json()
        assert d == {"available": False, "state": "unavailable", "message": d["message"]}

    def test_config(self, client):
        d = client.get("/reticulum/config").get_json()
        assert d["mode"] == "shared"
        assert d["port"] == 4242


class TestStart:
    def test_shared_start(self, client, rns_client):
        r = client.post("/reticulum/start", json={"display_name": "Deck"})
        assert r.status_code == 200
        cfg = rns_client.start.call_args[0][0]
        assert cfg.mode == "shared"
        assert cfg.display_name == "Deck"
        rns_client.save.assert_called_once()

    def test_tcp_start(self, client, rns_client):
        r = client.post("/reticulum/start", json={"mode": "tcp", "host": "100.104.5.118", "port": 4242})
        assert r.status_code == 200
        cfg = rns_client.start.call_args[0][0]
        assert (cfg.mode, cfg.host, cfg.port) == ("tcp", "100.104.5.118", 4242)

    @pytest.mark.parametrize(
        "body",
        [
            {"mode": "udp"},
            {"mode": "tcp", "host": ""},
            {"mode": "tcp", "host": "h", "port": 70000},
            {"mode": "tcp", "host": "bad host"},
            {"display_name": ""},
            {"port": "x"},
        ],
    )
    def test_rejects_bad_config(self, client, rns_client, body):
        r = client.post("/reticulum/start", json=body)
        assert r.status_code == 400
        rns_client.start.assert_not_called()
        rns_client.save.assert_not_called()

    def test_changed_connection_is_400(self, client, rns_client):
        rns_client.start.side_effect = ValueError("restart Intercept to change it")
        r = client.post("/reticulum/start", json={})
        assert r.status_code == 400
        assert "restart" in r.get_json()["message"]

    def test_not_installed(self, client):
        with patch("routes.reticulum.is_reticulum_available", return_value=False):
            assert client.post("/reticulum/start", json={}).status_code == 503

    def test_stop(self, client, rns_client):
        assert client.post("/reticulum/stop").get_json()["status"] == "stopped"
        rns_client.stop.assert_called_once()


class TestAnnounce:
    def test_announce(self, client, rns_client):
        assert client.post("/reticulum/announce").status_code == 200

    def test_announce_not_running(self, client, rns_client):
        rns_client.announce.side_effect = RuntimeError("Reticulum is not running")
        assert client.post("/reticulum/announce").status_code == 409


class TestData:
    def test_nodes(self, client):
        assert client.get("/reticulum/nodes").get_json()["nodes"][0]["dest_hash"] == HASH

    def test_conversations(self, client):
        assert client.get("/reticulum/conversations").get_json() == {"conversations": []}

    def test_messages_peer_normalized(self, client, rns_client):
        client.get("/reticulum/messages?peer=<" + "A" * 32 + ">")
        rns_client.get_messages.assert_called_once_with("a" * 32)

    def test_messages_bad_peer(self, client):
        assert client.get("/reticulum/messages?peer=zz").status_code == 400


class TestSend:
    def test_send(self, client, rns_client):
        rns_client.send.return_value = {"msg_hash": "ff", "state": "outbound"}
        r = client.post("/reticulum/send", json={"destination": HASH, "text": " hi "})
        assert r.status_code == 200
        rns_client.send.assert_called_once_with(HASH, "hi")

    def test_empty_text(self, client):
        assert client.post("/reticulum/send", json={"destination": HASH, "text": " "}).status_code == 400

    def test_too_long(self, client):
        assert client.post("/reticulum/send", json={"destination": HASH, "text": "x" * 4001}).status_code == 400

    @pytest.mark.parametrize(
        "exc,code", [(ValueError("bad"), 400), (LookupError("unknown"), 404), (RuntimeError("stopped"), 409)]
    )
    def test_errors(self, client, rns_client, exc, code):
        rns_client.send.side_effect = exc
        assert client.post("/reticulum/send", json={"destination": HASH, "text": "hi"}).status_code == code


def test_dashboard_renders(app):
    """Full app: the dashboard page renders with nav."""
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["role"] = "admin"
    r = c.get("/reticulum/dashboard")
    assert r.status_code == 200
    assert b"js/modes/reticulum.js" in r.data
