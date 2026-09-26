"""Smoke test: every page and every mode loads without a JavaScript error.

Starts the app on a local port, opens each page in headless Chromium, and
switches the main page through every mode in the registry. It fails on any
uncaught error (what the app shows as an "Unhandled Error" pop-up), and on
programming errors that code caught and only logged (a TypeError or
ReferenceError in console.error), since many modes wrap their start-up in
try/catch. Requests off this machine are refused, so the result does not
depend on CDNs or third-party services.

Each check runs in the dark and light themes, at desktop and phone size.
It also fails on a missing file (a 4xx/5xx for anything under /static/),
and, at phone size, on any screen that scrolls sideways.

Needs Playwright and its Chromium:

    pip install playwright && python -m playwright install --with-deps chromium
    pytest tests/smoke -q

Skipped when Playwright is not installed. CI runs it as its own job.
"""

from __future__ import annotations

import os
import re
import threading

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

DASHBOARDS = [
    "/adsb/dashboard",
    "/ais/dashboard",
    "/satellite/dashboard",
    "/aprs/dashboard",
    "/meshtastic/dashboard",
    "/meshcore/dashboard",
    "/adsb/history",
    "/controller/manage",
    "/controller/monitor",
]

# A logged error that is a bug in the page, not a failed request or a missing tool
CODE_ERROR = re.compile(r"\b(TypeError|ReferenceError|SyntaxError|RangeError)\b|is not (a function|defined)")

# First-run prompts that would cover the page, and the theme under test
INIT_SCRIPT = """
try {
    localStorage.setItem('disclaimerAccepted', 'true');
    localStorage.setItem('intercept.setup.complete.v1', 'true');
    localStorage.setItem('intercept-theme', '%s');
} catch (e) {}
"""

SETUPS = {
    "dark-desktop": ("dark", {"width": 1600, "height": 1000}),
    "light-desktop": ("light", {"width": 1600, "height": 1000}),
    "dark-phone": ("dark", {"width": 390, "height": 844}),
    "light-phone": ("light", {"width": 390, "height": 844}),
}

# Scrolling sideways: the page is wider than the window (content that scrolls
# inside its own box, like the phone nav bar, does not count)
OVERFLOW_JS = "document.documentElement.scrollWidth - window.innerWidth"


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
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(params=list(SETUPS))
def page(request, browser, base_url):
    theme, viewport = SETUPS[request.param]
    context = browser.new_context(viewport=viewport)
    context.add_init_script(INIT_SCRIPT % theme)

    def local_only(route):
        if route.request.url.startswith(base_url):
            route.continue_()
        else:
            route.abort()

    context.route("**/*", local_only)
    page = context.new_page()
    page.errors = []
    page.is_phone = viewport["width"] < 768
    page.on("pageerror", lambda err: page.errors.append(str(err)))
    page.on(
        "response",
        lambda r: (
            page.errors.append(f"{r.status} {r.url}")
            if r.status >= 400 and r.url.startswith(base_url + "/static/")
            else None
        ),
    )
    page.on(
        "console",
        lambda msg: page.errors.append(msg.text) if msg.type == "error" and CODE_ERROR.search(msg.text) else None,
    )
    yield page
    context.close()


def test_main_page_every_mode(page, base_url):
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_function("window.INTERCEPT_MODES && typeof window.switchMode === 'function'")
    modes = page.evaluate("Object.keys(window.INTERCEPT_MODES)")
    assert len(modes) > 20

    failures = {}
    for mode in modes:
        before = len(page.errors)
        page.evaluate("(m) => Promise.resolve(window.switchMode(m)).catch((e) => { throw e; })", mode)
        page.wait_for_timeout(700)  # lazy scripts load and modes run their first render
        problems = page.errors[before:]
        if page.is_phone and page.evaluate(OVERFLOW_JS) > 1:
            problems = problems + [f"scrolls sideways by {page.evaluate(OVERFLOW_JS)} px"]
        if problems:
            failures[mode] = problems

    assert not failures, failures


@pytest.mark.parametrize("path", DASHBOARDS)
def test_dashboard_loads(page, base_url, path):
    response = page.goto(base_url + path, wait_until="domcontentloaded")
    assert response.status == 200
    page.wait_for_timeout(1500)
    problems = list(page.errors)
    if page.is_phone and page.evaluate(OVERFLOW_JS) > 1:
        problems.append(f"scrolls sideways by {page.evaluate(OVERFLOW_JS)} px")
    assert not problems, problems
