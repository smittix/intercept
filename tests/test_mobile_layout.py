"""Phone and offline UI stays usable with no internet and no desktop viewport."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_login_does_not_request_google_fonts(anon_client):
    resp = anon_client.get("/login")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "fonts.googleapis.com" not in body
    assert "fonts-local.css" in body


def test_agents_page_uses_local_fonts_and_shared_mobile_scroll(client):
    resp = client.get("/controller/manage")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "fonts.googleapis.com" not in body
    assert "fonts-local.css" in body
    assert "css/mobile.css" in body
    assert "js/core/mobile-scroll.js" in body
    # The hamburger drawer exists only on the main page.
    assert "js/core/mobile-nav.js" not in body


def test_main_page_ships_the_drawer_and_does_not_block_on_fonts(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "fonts.googleapis.com" not in body
    assert "js/core/mobile-nav.js" in body
    assert "css/mobile.css" in body
    assert "js/core/mobile-scroll.js" in body
    assert 'id="hamburgerBtn"' in body
    assert 'id="mainSidebar"' in body
    assert 'id="drawerOverlay"' in body


def test_mobile_css_only_applies_below_desktop():
    text = (ROOT / "static/css/mobile.css").read_text(encoding="utf-8")
    stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    parts = re.split(r"@media", stripped)
    assert "{" not in parts[0]
    assert len(parts) > 1
    for part in parts[1:]:
        head = part.split("{", 1)[0]
        assert "max-width:" in head
        assert "min-width" not in head
        # 768px is the narrower phone query; 1023px is the tablet cutoff.
        # Nothing in this file may apply at a desktop width.
        assert "1023px" in head or "768px" in head

    # 100vh is the fallback. Declaring it after 100dvh makes the dynamic
    # viewport lose, which is the drawer bug this file exists to avoid.
    vh = "height: calc(100vh - var(--intercept-drawer-top"
    dvh = "height: calc(100dvh - var(--intercept-drawer-top"
    assert text.index(vh) < text.index(dvh)
    assert text.index("max-height: calc(100vh - 24px)") < text.index("max-height: calc(100dvh - 24px)")


def test_waterfall_follows_the_phone_canvas_box():
    text = (ROOT / "static/js/modes/waterfall.js").read_text(encoding="utf-8")
    assert "function _watchMobileCanvasBox()" in text
    assert "orientationchange" in text
    assert "ResizeObserver" in text
