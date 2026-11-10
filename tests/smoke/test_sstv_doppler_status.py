"""ISS SSTV: the Doppler reading stays in the status line between 'Listening...' updates."""

import json

LISTENING = {
    "type": "sstv_progress",
    "status": "detecting",
    "progress": 0,
    "message": "Listening...",
    "signal_level": 5,
}
DOPPLER = {
    "type": "sstv_progress",
    "status": "detecting",
    "progress": 0,
    "message": "Doppler: +120 Hz, elevation: 23.4°",
    "doppler": {"shift_hz": 120.0, "elevation": 23.4},
}


def test_doppler_reading_stays_visible(browser, base_url):
    context = browser.new_context()
    context.add_init_script(
        "localStorage.setItem('disclaimerAccepted', 'true');"
        "localStorage.setItem('observerLat', '51.5'); localStorage.setItem('observerLon', '-0.12');"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.route(
        "**/sstv/start",
        lambda r: r.fulfill(
            json={"status": "started", "frequency": 145.8, "modulation": "fm", "device": 0, "doppler_enabled": True}
        ),
    )
    events = "".join(f"data: {json.dumps(e)}\n\n" for e in (LISTENING, DOPPLER, LISTENING, LISTENING))
    page.route(
        "**/sstv/stream",
        lambda r: r.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=events),
    )

    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_function("window.INTERCEPT_MODES && typeof window.switchMode === 'function'")
    page.evaluate("() => Promise.resolve(window.switchMode('sstv'))")
    page.wait_for_selector("#sstvStartBtn", timeout=15000)
    page.wait_for_timeout(1500)
    page.evaluate(
        "() => document.querySelectorAll('.setup-overlay, .disclaimer-overlay, .welcome-overlay').forEach(e => e.remove())"
    )

    page.click("#sstvStartBtn")
    page.wait_for_function("document.getElementById('sstvStripStatus').textContent.includes('Doppler')")
    page.wait_for_timeout(500)

    assert page.text_content("#sstvStripStatus") == "Listening... · Doppler +120 Hz, el 23.4°"
    assert errors == [], errors
    context.close()
