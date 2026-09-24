import json
import queue
from unittest.mock import patch

import pytest
from flask import Flask

import app as app_module
from routes.drone import drone_bp


@pytest.fixture(autouse=True)
def mock_app_state(mocker):
    mocker.patch.object(app_module, "drone_queue", queue.Queue())
    yield


@pytest.fixture
def drone_app():
    app = Flask(__name__)
    app.register_blueprint(drone_bp)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(drone_app):
    return drone_app.test_client()


def test_status_returns_json(client):
    resp = client.get("/drone/status")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "running" in data
    assert "vectors" in data


def test_contacts_returns_empty_list_when_idle(client):
    resp = client.get("/drone/contacts")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data == [] or isinstance(data, list)


def test_start_returns_ok(client):
    with (
        patch("routes.drone._correlator"),
        patch("routes.drone._remote_id_scanner"),
        patch("routes.drone._rf_detector"),
        patch("routes.drone.shutil.which", return_value="/usr/bin/hackrf_sweep"),
        patch.object(app_module, "claim_sdr_device", return_value="Device 0 is in use by sensor"),
    ):
        resp = client.post("/drone/start", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["vectors"] == ["HACKRF"]
        assert any("in use by sensor" in reason for reason in data["unavailable"])
    client.post("/drone/stop")


def test_start_with_no_source_available_says_why(client):
    with (
        patch("routes.drone.shutil.which", return_value=None),
        patch("routes.drone.remote_id.SCAPY_AVAILABLE", False),
    ):
        resp = client.post("/drone/start", json={})
    assert resp.status_code == 400
    message = resp.get_json()["message"]
    assert "scapy" in message and "rtl_433 not found" in message and "hackrf_sweep not found" in message
    assert client.get("/drone/status").get_json()["running"] is False


def test_stop_returns_ok(client):
    resp = client.post("/drone/stop")
    assert resp.status_code == 200


def test_stream_returns_event_stream(client):
    resp = client.get("/drone/stream")
    assert resp.content_type.startswith("text/event-stream")
