"""ADS-B dashboard: ACARS on one source while ADS-B runs on another (#264).

ADS-B, ACARS and VDL2 used to share the header's agent choice, which locks
while ADS-B tracks, so a local-SDR mode and an agent-hosted one couldn't run
from the same page. The agent and the decoders are mocked at the HTTP layer;
what's checked is which endpoint each start/stop goes to and that the panels
keep their state.

Skipped when Playwright is not installed, like the rest of tests/smoke.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

AGENT = {"id": 7, "name": "Pi Agent", "healthy": True, "base_url": "http://192.0.2.7:8020"}
LOCAL_SDRS = [
    {"index": 0, "sdr_type": "rtlsdr", "name": "Local RTL A"},
    {"index": 1, "sdr_type": "rtlsdr", "name": "Local RTL B"},
]
AGENT_SDRS = [{"index": 0, "sdr_type": "rtlsdr", "name": "Agent RTL"}]

RESPONSES = {
    ("GET", "/controller/agents"): {"agents": [AGENT]},
    ("GET", "/controller/agents/7"): {"agent": {**AGENT, "interfaces": {"sdr_devices": AGENT_SDRS}}},
    ("GET", "/controller/agents/7/status"): {"status": "success", "agent_status": {"running_modes": []}},
    ("POST", "/controller/agents/7/adsb/start"): {"status": "started"},
    ("POST", "/controller/agents/7/acars/start"): {"status": "success", "result": {"status": "started"}},
    ("POST", "/controller/agents/7/acars/stop"): {"status": "success", "result": {"status": "stopped"}},
    ("GET", "/devices"): LOCAL_SDRS,
    ("GET", "/devices/status"): [],
    ("POST", "/adsb/start"): {"status": "started"},
    ("POST", "/acars/start"): {"status": "started"},
    ("POST", "/acars/stop"): {"status": "stopped"},
}


@pytest.fixture
def page(browser, base_url):
    context = browser.new_context(viewport={"width": 1600, "height": 1000})
    context.add_init_script(
        "try { localStorage.setItem('disclaimerAccepted', 'true');"
        " localStorage.setItem('intercept.setup.complete.v1', 'true');"
        " localStorage.setItem('acarsSidebarCollapsed', 'false'); } catch (e) {}"
    )
    page = context.new_page()
    page.calls = []
    page.errors = []

    def handle(route):
        request = route.request
        if not request.url.startswith(base_url):
            return route.abort()
        path = request.url[len(base_url) :].split("?")[0]
        body = RESPONSES.get((request.method, path))
        if body is None:
            return route.continue_()
        page.calls.append(f"{request.method} {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    context.route("**/*", handle)
    page.on("pageerror", lambda err: page.errors.append(str(err)))
    page.goto(base_url + "/adsb/dashboard", wait_until="domcontentloaded")
    page.wait_for_function("typeof agents !== 'undefined' && agents.length === 1")
    yield page
    context.close()


def _choose(page, select_id, value):
    page.focus(f"#{select_id}")  # the panel source list is filled on focus
    page.select_option(f"#{select_id}", value)


def test_acars_on_agent_while_adsb_tracks_locally(page):
    page.click("#startBtn")
    page.wait_for_function("isTracking === true")
    assert page.is_disabled("#agentSelect")  # header locked to Local while ADS-B tracks

    _choose(page, "acarsSourceSelect", "7")
    page.wait_for_function("document.getElementById('acarsDeviceSelect').textContent.includes('Agent RTL')")
    page.click("#acarsToggleBtn")
    page.wait_for_function("isAcarsRunning === true")

    assert "POST /controller/agents/7/acars/start" in page.calls
    assert "POST /acars/start" not in page.calls
    assert page.is_disabled("#acarsSourceSelect")
    assert page.evaluate("isTracking && adsbTrackingSource === 'local'")

    page.click("#acarsToggleBtn")
    page.wait_for_function("isAcarsRunning === false")
    assert "POST /controller/agents/7/acars/stop" in page.calls
    assert not page.errors, page.errors


def test_local_acars_survives_switching_header_to_agent(page):
    page.click("#acarsToggleBtn")
    page.wait_for_function("isAcarsRunning === true")
    assert "POST /acars/start" in page.calls

    # The agent isn't running ACARS; that must not mark the local ACARS stopped
    page.select_option("#agentSelect", "7")
    page.wait_for_function("adsbCurrentAgent === '7'")
    page.wait_for_timeout(500)
    assert "GET /controller/agents/7/status" in page.calls
    assert page.evaluate("isAcarsRunning && acarsCurrentAgent === null")
    assert "STOP ACARS" in page.inner_text("#acarsToggleBtn")

    page.click("#startBtn")
    page.wait_for_function("isTracking === true")
    assert "POST /controller/agents/7/adsb/start" in page.calls

    page.click("#acarsToggleBtn")
    page.wait_for_function("isAcarsRunning === false")
    assert "POST /acars/stop" in page.calls
    assert "POST /controller/agents/7/acars/stop" not in page.calls
    assert not page.errors, page.errors
