"""433MHz: the packets strip shows only in 433MHz mode while the sensor runs.

The page-load status sync used to show it in whatever mode was open.
"""


def _visible(page):
    return page.evaluate("() => getComputedStyle(document.getElementById('sensorScopePanel')).display !== 'none'")


def test_sensor_strip_only_in_sensor_mode(browser, base_url):
    context = browser.new_context()
    context.add_init_script(
        "localStorage.setItem('disclaimerAccepted', 'true');"
        "localStorage.setItem('intercept.setup.complete.v1', 'true');"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/sensor/status", lambda r: r.fulfill(json={"running": True}))

    page.goto(base_url + "/?mode=spaceweather", wait_until="domcontentloaded")
    page.wait_for_function("window.INTERCEPT_MODES && typeof window.switchMode === 'function'")
    page.wait_for_function("typeof isSensorRunning !== 'undefined' && isSensorRunning === true", timeout=15000)
    page.wait_for_timeout(500)
    assert not _visible(page), "packets strip shown outside 433MHz mode"

    page.evaluate("() => Promise.resolve(window.switchMode('sensor'))")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('sensorScopePanel')).display !== 'none'", timeout=15000
    )

    page.evaluate("() => Promise.resolve(window.switchMode('spaceweather'))")
    page.wait_for_timeout(1500)
    assert not _visible(page), "packets strip shown after leaving 433MHz mode"

    assert errors == [], errors
    context.close()
