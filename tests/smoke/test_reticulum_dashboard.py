"""Reticulum dashboard: unverified senders, the message total, and live updates
without polling or re-fetching."""

from __future__ import annotations

import json

import pytest

from utils.reticulum import get_reticulum_client

playwright_api = pytest.importorskip("playwright.sync_api")

PEER = "ab" * 16


def test_reticulum_dashboard_messages(base_url, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    calls = []
    page.on("request", lambda r: calls.append(r.url.split("/reticulum/", 1)[-1]) if "/reticulum/" in r.url else None)

    def reply(route, body):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    messages = [
        {"msg_hash": "01" * 16, "direction": "in", "peer_hash": PEER, "title": "", "content": "real",
         "timestamp": 1.0, "state": "received", "verified": True},
        {"msg_hash": "02" * 16, "direction": "in", "peer_hash": PEER, "title": "", "content": "forged",
         "timestamp": 2.0, "state": "received", "verified": False},
    ]
    page.route("**/reticulum/status", lambda r: reply(r, {"available": True, "state": "online", "running": True,
                                                          "message": "Connected", "address": "cd" * 16}))
    page.route("**/reticulum/config", lambda r: reply(r, {"mode": "shared", "host": "", "port": 4242,
                                                          "display_name": "Deck"}))
    page.route("**/reticulum/nodes", lambda r: reply(r, {"nodes": []}))
    page.route("**/reticulum/conversations", lambda r: reply(r, {"conversations": [
        {"peer_hash": PEER, "name": "Cyberboy", "count": 1200, "last_timestamp": 2.0, "last_content": "forged"}]}))
    page.route("**/reticulum/messages*", lambda r: reply(r, {"messages": messages}))
    event = {"type": "message", "data": {"msg_hash": "03" * 16, "direction": "in", "peer_hash": PEER, "title": "",
                                         "content": "live one", "timestamp": 3.0, "state": "received",
                                         "verified": True}}
    page.goto(f"{base_url}/reticulum/dashboard", wait_until="load")
    page.wait_for_selector(f"#rns-msg-{'02' * 16}")
    page.wait_for_timeout(1500)  # the real stream is open and subscribed
    get_reticulum_client()._push(event)  # the live stream is the app's own
    page.wait_for_selector(f"#rns-msg-{'03' * 16}")

    forged = page.locator(f"#rns-msg-{'02' * 16}")
    assert "UNVERIFIED" in forged.inner_text() and "unverified" in forged.get_attribute("class")
    assert "UNVERIFIED" not in page.inner_text(f"#rns-msg-{'01' * 16}")
    # The total comes from the conversations (1200), not the capped message list; +1 live
    assert page.inner_text("#rnsMsgCount") == "1201"
    conv = page.inner_text("#rnsConversationList")
    assert "(1201)" in conv and "live one" in conv
    assert calls.count("conversations") == 1  # updated in place, not re-fetched per message

    page.wait_for_timeout(5500)  # longer than the old 5 s status poll
    # Status is fetched on load and when the stream opens, never on a timer
    assert calls.count("status") == 2 and calls.count("stream") == 1
    assert page.evaluate("typeof L") == "undefined"  # no map, so no Leaflet
    assert not errors
    page.close()
