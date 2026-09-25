"""Static asset URLs carry the app version, so an upgrade is a new URL."""

import re

from flask import url_for

from config import VERSION


def test_url_for_static_carries_version(app):
    with app.test_request_context():
        assert url_for("static", filename="js/modes/gps.js").endswith(f"?v={VERSION}")


def test_pages_version_every_app_asset(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    html = client.get("/").get_data(as_text=True)
    assets = re.findall(r'["\'](/static/[^"\'?]+\.(?:js|css)(?:\?[^"\']*)?)["\']', html)
    assert assets, "expected static assets on the main page"
    unversioned = [a for a in assets if f"v={VERSION}" not in a]
    assert not unversioned, unversioned
    # The lazily loaded mode scripts too (the stale gps.js case)
    assert f"js/modes/gps.js?v={VERSION}" in html
