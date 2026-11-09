"""Meshtastic Nodes tab: node actions keep the map and list stable (#270)."""

NODES = [
    {
        "num": 1,
        "id": "!00000001",
        "long_name": "Home",
        "has_position": True,
        "latitude": 51.5,
        "longitude": -0.1,
        "last_heard": "2026-11-09T10:00:00+00:00",
    },
    {
        "num": 2,
        "id": "!00000002",
        "long_name": "Alpha",
        "has_position": True,
        "latitude": 51.6,
        "longitude": -0.2,
        "last_heard": "2026-11-09T09:00:00+00:00",
    },
    {
        "num": 3,
        "id": "!00000003",
        "long_name": "Bravo",
        "has_position": True,
        "latitude": 51.7,
        "longitude": -0.3,
        "last_heard": "2026-11-09T08:00:00+00:00",
    },
]


def test_favourite_keeps_map_and_list_stable(browser, base_url):
    context = browser.new_context()
    context.add_init_script("localStorage.setItem('disclaimerAccepted', 'true');")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    favourites = set()

    def nodes(route):
        data = [dict(n, is_favorite=n["id"] in favourites) for n in NODES]
        route.fulfill(json={"status": "ok", "nodes": data, "count": 3, "with_position_count": 3})

    def action(route):
        node_id = route.request.url.split("/nodes/")[1].split("/")[0]
        favourites.add(node_id.replace("%21", "!"))
        route.fulfill(json={"status": "ok"})

    page.route(
        "**/meshtastic/status",
        lambda r: r.fulfill(
            json={
                "available": True,
                "running": True,
                "device": "sim",
                "connection_type": "serial",
                "node_info": {"num": 1, "id": "!00000001", "long_name": "Home"},
            }
        ),
    )
    page.route("**/meshtastic/nodes", nodes)
    page.route("**/meshtastic/nodes/*/favorite", action)
    page.route("**/meshtastic/messages*", lambda r: r.fulfill(json={"status": "ok", "messages": [], "count": 0}))
    page.route("**/meshtastic/channels", lambda r: r.fulfill(json={"status": "ok", "channels": [], "count": 0}))
    page.route("**/meshtastic/stream", lambda r: r.fulfill(status=204, body=""))

    page.goto(base_url + "/meshtastic/dashboard", wait_until="domcontentloaded")
    page.wait_for_selector('#meshNodeList .mesh-node-row[data-node="!00000003"]', state="attached", timeout=15000)
    page.wait_for_timeout(500)

    # Count map refits from here on
    page.evaluate("""() => {
        window.__fits = 0;
        const orig = L.Map.prototype.fitBounds;
        L.Map.prototype.fitBounds = function () { window.__fits++; return orig.apply(this, arguments); };
    }""")
    bravo_btn = page.query_selector('#meshNodeList .mesh-node-row[data-node="!00000002"] .mesh-node-fav')

    # Unchanged data: the rows are not rebuilt, so a click in progress survives
    page.evaluate("() => Meshtastic.nodeAction('!00000009', 'favorite')")  # unknown node, list unchanged
    page.wait_for_timeout(300)
    assert page.evaluate("(el) => el.isConnected", bravo_btn), "unchanged list was rebuilt"

    page.evaluate("() => Meshtastic.nodeAction('!00000003', 'favorite')")
    page.wait_for_timeout(300)
    order = page.eval_on_selector_all("#meshNodeList .mesh-node-row", "rows => rows.map(r => r.dataset.node)")
    assert order == ["!00000001", "!00000003", "!00000002"], order
    assert (
        page.get_attribute('#meshNodeList .mesh-node-row[data-node="!00000003"] .mesh-node-fav', "aria-pressed")
        == "true"
    )
    assert page.evaluate("() => window.__fits") == 0, "map was refit after a node action"

    # A second node can still be favourited
    page.evaluate("() => meshDashTab('nodes')")
    page.evaluate("() => document.querySelectorAll('.setup-overlay, .disclaimer-overlay').forEach(e => e.remove())")
    page.click('#meshNodeList .mesh-node-row[data-node="!00000002"] .mesh-node-fav')
    page.wait_for_timeout(300)
    assert (
        page.get_attribute('#meshNodeList .mesh-node-row[data-node="!00000002"] .mesh-node-fav', "aria-pressed")
        == "true"
    )
    assert errors == [], errors
    context.close()
