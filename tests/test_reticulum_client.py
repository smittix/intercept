"""Tests for utils.reticulum with RNS/LXMF mocked at the module boundary."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import utils.reticulum as ret
from utils.database import get_db, get_reticulum_conversations, get_reticulum_messages, get_reticulum_nodes
from utils.reticulum import ReticulumClient, ReticulumConfig

PEER = bytes.fromhex("ab" * 16)
LOCAL = bytes.fromhex("cd" * 16)


@pytest.fixture(autouse=True)
def clean_tables():
    with get_db() as conn:
        conn.execute("DELETE FROM reticulum_messages")
        conn.execute("DELETE FROM reticulum_nodes")


@pytest.fixture()
def fake_rns(tmp_path):
    """Mocked RNS + LXMF; no real Reticulum stack is ever started."""
    rns = MagicMock()
    rns.Transport.interfaces = [SimpleNamespace(online=True)]
    rns.Transport.destinations = []
    rns.Transport.announce_handlers = []
    rns.Transport.PATHFINDER_M = 128
    rns.Transport.hops_to.return_value = 2
    rns.Transport.register_destination.side_effect = rns.Transport.destinations.append
    rns.Transport.register_announce_handler.side_effect = rns.Transport.announce_handlers.append
    rns.Transport.deregister_destination.side_effect = rns.Transport.destinations.remove
    lxmf = MagicMock()
    local = SimpleNamespace(hash=LOCAL, display_name=None)
    lxmf.LXMRouter.return_value.register_delivery_identity.return_value = local
    lxmf.display_name_from_app_data.side_effect = lambda d: d.decode()
    with (
        patch.object(ret, "RNS", rns),
        patch.object(ret, "LXMF", lxmf),
        patch.object(ret, "data_dir", return_value=tmp_path),
        patch.object(ret, "shared_instance_available", return_value=True),
    ):
        yield SimpleNamespace(rns=rns, lxmf=lxmf, local=local, dir=tmp_path)


def _started(fake, cfg=None):
    client = ReticulumClient()
    client.start(cfg or ReticulumConfig(display_name="Deck"))
    client._worker.join(5)
    return client


class TestConfig:
    def test_shared_config_has_no_interfaces(self):
        text = ret.build_rns_config(ReticulumConfig(instance_name="default"))
        assert "share_instance = Yes" in text
        assert "instance_name = default" in text
        assert "type =" not in text

    def test_tcp_config(self):
        text = ret.build_rns_config(ReticulumConfig(mode="tcp", host="100.104.5.118", port=4242))
        assert "share_instance = No" in text
        assert "type = TCPClientInterface" in text
        assert "target_host = 100.104.5.118" in text
        assert "target_port = 4242" in text

    def test_rpc_key_written(self):
        assert "rpc_key = abcd" in ret.build_rns_config(ReticulumConfig(rpc_key="abcd"))

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"mode": "x"},
            {"mode": "tcp", "host": ""},
            {"mode": "tcp", "host": "h", "port": 0},
            {"display_name": ""},
            {"display_name": "a\nb"},
            {"instance_name": "../x"},
            {"rpc_key": "zz"},
        ],
    )
    def test_validate_rejects(self, kwargs):
        with pytest.raises(ValueError):
            ReticulumConfig(**kwargs).validate()

    def test_normalize_hash(self):
        assert ret.normalize_hash("<" + "AB" * 16 + ">") == "ab" * 16
        assert ret.normalize_hash(":".join(["ab"] * 16)) == "ab" * 16
        with pytest.raises(ValueError):
            ret.normalize_hash("abc")

    def test_data_dir_never_home_reticulum(self):
        assert ".reticulum" not in str(ret.data_dir())

    def test_saved_settings_override_env(self):
        ret.save_config(ReticulumConfig(mode="tcp", host="10.0.0.1", port=4000, display_name="X"))
        cfg = ret.default_config()
        assert (cfg.mode, cfg.host, cfg.port, cfg.display_name) == ("tcp", "10.0.0.1", 4000, "X")


class TestLifecycle:
    def test_start_builds_stack_in_own_dir(self, fake_rns):
        client = _started(fake_rns)
        kwargs = fake_rns.rns.Reticulum.call_args.kwargs
        assert kwargs["configdir"] == str(fake_rns.dir / "rns")
        assert kwargs["require_shared_instance"] is True
        assert (fake_rns.dir / "rns" / "config").exists()
        status = client.get_status()
        assert status["state"] == "online"
        assert status["address"] == LOCAL.hex()
        assert status["display_name"] == "Deck"
        fake_rns.lxmf.LXMRouter.return_value.announce.assert_called_once_with(LOCAL)

    def test_signal_handlers_not_installed(self, fake_rns):
        seen = []

        def build(**_kw):
            import signal

            seen.append(signal.signal(signal.SIGTERM, lambda *a: None))
            return MagicMock()

        fake_rns.rns.Reticulum.side_effect = build
        _started(fake_rns)
        assert seen == [None]  # the patched no-op, not the real signal.signal

    def test_offline_link_is_waiting_not_error(self, fake_rns):
        fake_rns.rns.Transport.interfaces = [SimpleNamespace(online=False)]
        client = _started(fake_rns, ReticulumConfig(mode="tcp", host="100.104.5.118"))
        status = client.get_status()
        assert status["state"] == "waiting"
        assert "100.104.5.118:4242" in status["message"]

    def test_no_shared_instance_waits_without_init(self, fake_rns):
        with patch.object(ret, "shared_instance_available", return_value=False):
            client = ReticulumClient()
            client.start(ReticulumConfig())
            for _ in range(50):
                if client.get_status()["state"] == "waiting":
                    break
                threading.Event().wait(0.02)
            assert client.get_status()["state"] == "waiting"
            client.stop()
            client._worker.join(5)
        fake_rns.rns.Reticulum.assert_not_called()
        assert client.get_status()["state"] == "stopped"

    def test_stop_and_restart_reuse_stack(self, fake_rns):
        client = _started(fake_rns)
        client.stop()
        assert fake_rns.rns.Transport.destinations == []
        assert client.get_status()["state"] == "stopped"
        client.start(ReticulumConfig(display_name="Again"))
        client._worker.join(5)
        assert fake_rns.rns.Reticulum.call_count == 1
        assert fake_rns.local in fake_rns.rns.Transport.destinations
        assert client.get_status()["display_name"] == "Again"

    def test_changing_connection_needs_restart(self, fake_rns):
        client = _started(fake_rns)
        client.stop()
        with pytest.raises(ValueError, match="restart"):
            client.start(ReticulumConfig(mode="tcp", host="h"))

    def test_shared_rpc_key_mismatch_is_reported(self, fake_rns):
        fake_rns.rns.Reticulum.return_value.get_interface_stats.side_effect = Exception("digest sent was rejected")
        status = _started(fake_rns).get_status()
        assert status["state"] == "error"
        assert "INTERCEPT_RETICULUM_RPC_KEY" in status["message"]
        assert status["running"] is True

    def test_tcp_mode_skips_rpc_check(self, fake_rns):
        _started(fake_rns, ReticulumConfig(mode="tcp", host="h"))
        fake_rns.rns.Reticulum.return_value.get_interface_stats.assert_not_called()

    def test_start_failure_is_error_state(self, fake_rns):
        fake_rns.rns.Reticulum.side_effect = OSError("boom")
        client = _started(fake_rns)
        assert client.get_status()["state"] == "error"
        assert "boom" in client.get_status()["message"]


class TestMessaging:
    def test_send_stores_and_tracks_delivery(self, fake_rns):
        client = _started(fake_rns)
        lxm = fake_rns.lxmf.LXMessage.return_value
        lxm.hash = b"\x01" * 32
        msg = client.send(PEER.hex(), "hello")
        assert msg["state"] == "outbound"
        assert get_reticulum_messages(PEER.hex())[0]["content"] == "hello"
        on_delivered = lxm.register_delivery_callback.call_args[0][0]
        on_delivered(lxm)
        assert get_reticulum_messages(PEER.hex())[0]["state"] == "delivered"
        events = [client.get_queue().get_nowait()["type"] for _ in range(client.get_queue().qsize())]
        assert "message" in events and "message_state" in events

    def test_send_unknown_identity_requests_path(self, fake_rns):
        client = _started(fake_rns)
        fake_rns.rns.Identity.recall.return_value = None
        with pytest.raises(LookupError):
            client.send(PEER.hex(), "hello")
        fake_rns.rns.Transport.request_path.assert_called_once_with(PEER)

    def test_send_when_stopped(self):
        with pytest.raises(RuntimeError):
            ReticulumClient().send(PEER.hex(), "x")

    def test_incoming_message_persisted_once(self, fake_rns):
        client = _started(fake_rns)
        message = MagicMock(hash=b"\x02" * 32, source_hash=PEER, timestamp=1700000000.0)
        message.title_as_string.return_value = ""
        message.content_as_string.return_value = "hi there"
        client._on_lxmf_message(message)
        client._on_lxmf_message(message)  # duplicate delivery
        msgs = get_reticulum_messages()
        assert [(m["direction"], m["content"]) for m in msgs] == [("in", "hi there")]

    def test_announce_records_node_and_conversation_name(self, fake_rns):
        client = _started(fake_rns)
        client.on_announce(PEER, b"Cyberboy")
        node = get_reticulum_nodes()[0]
        assert (node["dest_hash"], node["name"], node["hops"]) == (PEER.hex(), "Cyberboy", 2)
        client.on_announce(PEER, None)  # announce without a name keeps the old one
        assert get_reticulum_nodes()[0]["name"] == "Cyberboy"
        message = MagicMock(hash=b"\x03" * 32, source_hash=PEER, timestamp=1.0)
        message.title_as_string.return_value = ""
        message.content_as_string.return_value = "yo"
        client._on_lxmf_message(message)
        conv = get_reticulum_conversations()[0]
        assert (conv["peer_hash"], conv["name"], conv["count"], conv["last_content"]) == (
            PEER.hex(),
            "Cyberboy",
            1,
            "yo",
        )
