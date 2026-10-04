"""Shared fixtures for the browser smoke tests: the app on a local port, and Chromium."""

from __future__ import annotations

import os
import threading

import pytest


@pytest.fixture(scope="module")
def base_url(app):
    from werkzeug.serving import make_server

    previous = os.environ.get("INTERCEPT_DISABLE_AUTH")
    os.environ["INTERCEPT_DISABLE_AUTH"] = "1"  # read per request; restored below
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    if previous is None:
        os.environ.pop("INTERCEPT_DISABLE_AUTH", None)
    else:
        os.environ["INTERCEPT_DISABLE_AUTH"] = previous


@pytest.fixture(scope="module")
def browser():
    playwright_api = pytest.importorskip("playwright.sync_api")
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()
