"""The login gate on the shared app.

These use the conftest app with authentication enabled, so they fail if
require_login stops protecting routes or the suite starts bypassing it.
"""

import pytest

PROTECTED = ["/", "/settings", "/adsb/aircraft", "/status"]


@pytest.mark.parametrize("path", PROTECTED)
def test_anonymous_request_is_sent_to_login(anon_client, path):
    resp = anon_client.get(path, follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_anonymous_websocket_upgrade_is_refused(anon_client):
    assert anon_client.get("/ws/waterfall").status_code == 401


@pytest.mark.parametrize("path", ["/login", "/health"])
def test_public_routes_stay_public(anon_client, path):
    assert anon_client.get(path, follow_redirects=False).status_code == 200


def test_logged_in_client_passes_the_gate(client):
    assert client.get("/", follow_redirects=False).status_code == 200
