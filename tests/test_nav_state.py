"""Tests for nav group localStorage persistence (JS logic verified via structure check)."""


def _logged_in_get(client, path):
    """Make a GET request with a pre-seeded logged-in session."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    return client.get(path)


def test_index_page_includes_nav_state_init(client):
    """nav group init function must be present in the index page."""
    resp = _logged_in_get(client, "/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "initNavGroupState" in html
    assert "localStorage" in html


def test_nav_groups_have_data_group_attributes(client):
    """Each nav group must have a data-group attribute for state keying."""
    resp = _logged_in_get(client, "/")
    html = resp.data.decode()
    for group in ["signals", "tracking", "space", "wireless", "intel", "system"]:
        assert f'data-group="{group}"' in html, f"Missing data-group={group}"


NAV_PAGES = [
    "/",
    "/adsb/dashboard",
    "/ais/dashboard",
    "/satellite/dashboard",
    "/adsb/history",
    "/controller/monitor",
    "/controller/manage",
]


def test_kill_all_available_on_every_nav_page(client):
    """Kill All must be reachable wherever the global nav is, not just the SPA.

    It previously existed only in the main dashboard's System panel, which
    made it unavailable exactly when troubleshooting from a dashboard (#274).
    """
    for path in NAV_PAGES:
        resp = _logged_in_get(client, path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"
        html = resp.data.decode()
        assert "killAllProcesses()" in html, f"{path} has no Kill All control"
        assert "window.killAllProcesses" in html, f"{path} does not define killAllProcesses"


def test_kill_all_defers_to_dashboard_implementation(client):
    """On the SPA the nav control must delegate rather than post twice.

    index.html's killAll() posts to /killall and also resets SPA state
    (device reservations, run flags, SSE). Posting again from the nav
    wrapper would double up the request.
    """
    html = _logged_in_get(client, "/").data.decode()
    assert "if (typeof killAll === 'function')" in html
