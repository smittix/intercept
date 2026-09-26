"""Consistency checks between the mode registry and the template/assets."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "static" / "js" / "mode-registry.js"
INDEX = ROOT / "templates" / "index.html"


def _registry_modes() -> set[str]:
    src = REGISTRY.read_text()
    return set(re.findall(r"^\s{4}([a-z_]+):\s*\{", src, re.M))


def test_registry_has_all_modes():
    """The registry must declare a sane number of modes (28 at creation)."""
    modes = _registry_modes()
    assert len(modes) >= 28, f"registry lost modes: {sorted(modes)}"


def test_registry_modes_have_partials():
    """Every partial included by index.html must exist on disk."""
    html = INDEX.read_text()
    partials = set(re.findall(r"partials/modes/([\w.-]+)\.html", html))
    for partial in partials:
        assert (ROOT / "templates" / "partials" / "modes" / f"{partial}.html").exists(), (
            f"index.html includes missing partial: {partial}"
        )


def test_no_orphan_mode_assets():
    """Every modes/*.js and modes/*.css file is referenced somewhere."""
    referenced = INDEX.read_text() + REGISTRY.read_text()
    # ground_station_waterfall.js belongs to the satellite dashboard
    referenced += (ROOT / "templates" / "satellite_dashboard.html").read_text()
    # aprs.css / markers belong to the APRS dashboard (migrated out of the SPA)
    referenced += (ROOT / "templates" / "aprs_dashboard.html").read_text()
    # meshtastic.css / module belong to the Meshtastic dashboard (migrated out of the SPA)
    referenced += (ROOT / "templates" / "meshtastic_dashboard.html").read_text()
    for asset_dir, ext in [("static/js/modes", ".js"), ("static/css/modes", ".css")]:
        for f in (ROOT / asset_dir).glob(f"*{ext}"):
            assert f.name in referenced, f"orphaned mode asset: {f}"
