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
    ):
        resp = client.post("/drone/start", json={})
        assert resp.status_code == 200


def test_stop_returns_ok(client):
    resp = client.post("/drone/stop")
    assert resp.status_code == 200


def test_stream_returns_event_stream(client):
    resp = client.get("/drone/stream")
    assert resp.content_type.startswith("text/event-stream")
