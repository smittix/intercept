"""A gpsd with no receiver attached.

systemd's gpsd.socket (enabled by the Debian package, which setup.sh
installs) starts gpsd at boot with no device. Auto-connect used to see
"gpsd is running", connect, and wait for a fix that could never come.
"""

from __future__ import annotations

import json
import socket
import threading
from unittest.mock import patch

import pytest

from utils.gps import gpsd_devices


def _fake_gpsd(devices):
    """A one-shot gpsd answering ?DEVICES with the given paths."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        with conn:
            conn.sendall(b'{"class":"VERSION","release":"3.25"}\n')
            conn.recv(1024)
            reply = {"class": "DEVICES", "devices": [{"path": p} for p in devices]}
            conn.sendall((json.dumps(reply) + "\n").encode())
        server.close()

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1]


def test_gpsd_devices_reports_attached_receivers():
    assert gpsd_devices(port=_fake_gpsd(["/dev/ttyACM0"])) == ["/dev/ttyACM0"]


def test_gpsd_devices_is_empty_for_a_gpsd_with_no_receiver():
    assert gpsd_devices(port=_fake_gpsd([])) == []


def test_gpsd_devices_is_none_when_gpsd_is_not_there():
    unused = socket.socket()
    unused.bind(("127.0.0.1", 0))
    port = unused.getsockname()[1]
    unused.close()
    assert gpsd_devices(port=port) is None


@pytest.fixture
def no_reader():
    with patch("routes.gps.get_gps_reader", return_value=None), patch("routes.gps.is_gpsd_running", return_value=True):
        yield


def test_the_detected_receiver_is_handed_to_gpsd(client, no_reader):
    with (
        patch("routes.gps.gpsd_devices", return_value=[]),
        patch("routes.gps.detect_gps_devices", return_value=[{"path": "/dev/ttyACM0"}]),
        patch("routes.gps.add_device_to_gpsd", return_value=(True, "added")) as add,
        patch("routes.gps.start_gpsd", return_value=True),
    ):
        data = client.post("/gps/auto-connect").get_json()
    add.assert_called_once_with("/dev/ttyACM0", "localhost", 2947)
    assert data["status"] == "connected"


def test_when_it_cannot_be_handed_over_the_fix_is_named(client, no_reader):
    with (
        patch("routes.gps.gpsd_devices", return_value=[]),
        patch("routes.gps.detect_gps_devices", return_value=[{"path": "/dev/ttyACM0"}]),
        patch("routes.gps.add_device_to_gpsd", return_value=(False, "gpsdctl add /dev/ttyACM0 failed (needs root)")),
        patch("routes.gps.start_gpsd") as start,
    ):
        data = client.post("/gps/auto-connect").get_json()
    assert data["status"] == "unavailable"
    assert "sudo gpsdctl add /dev/ttyACM0" in data["message"] and "/etc/default/gpsd" in data["message"]
    start.assert_not_called()


def test_a_gpsd_with_a_receiver_is_used_as_it_is(client, no_reader):
    with (
        patch("routes.gps.gpsd_devices", return_value=["/dev/ttyACM0"]),
        patch("routes.gps.add_device_to_gpsd") as add,
        patch("routes.gps.start_gpsd", return_value=True),
    ):
        assert client.post("/gps/auto-connect").get_json()["status"] == "connected"
    add.assert_not_called()
