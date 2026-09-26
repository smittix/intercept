# Meshtastic & Meshcore → dedicated dashboards (Round B plan)

Status: **B1 (Meshtastic) done** in v2.33.34 — `/meshtastic/dashboard` shipped and Meshtastic removed from the SPA. B2 (Meshcore) remains. Follow-up to the
APRS migration (Round A, shipped in v2.33.33); see
[[aprs-meshtastic-dashboard-migration]].

## Why

Same driver as APRS: `CLAUDE.md` says map-centric modes get dedicated dashboard
pages and that APRS **and Meshtastic** "should migrate to dashboards under their
own plans — do not grow their SPA footprint." Meshtastic and Meshcore are the
last two map-centric modes still living in the main-page SPA.

## What makes this different from APRS (and easier in one way)

**Easier:** `static/js/modes/meshtastic.js` (2,376 lines) and
`static/js/modes/meshcore.js` are already **self-contained IIFE modules** —
`Meshtastic` / `MeshCore` — with **no dependency on any main-page global**
(no `currentAgent`, `getSelectedDevice`, `postStopRequest`, `switchMode`, etc.;
Meshtastic uses only `MapUtils`). So, unlike APRS, we do **not** need a glue
layer. The dashboard just has to provide the same DOM element IDs, load the
existing module unchanged, and call `Meshtastic.init()` on load.

**Harder:** these are **not** SDR modes and **not** just a map. Meshtastic
connects to a physical node over **serial / BLE / TCP** and is a full mesh
console. Its feature set (all already built, backed by `routes/meshtastic.py`,
~30 endpoints):

- connection bar: type (serial/BLE/TCP), device/port, hostname, connect/disconnect
- own-node status (name, id, model, sats)
- **messages**: per-channel message feed + compose/send
- **nodes**: node list, positions on the map, neighbours
- **channels**: list, configure modal, QR export
- **traceroute** (modal + results), **position request**
- **telemetry history**, **range test** (start/stop/status), **store-forward**
- firmware check

Meshcore (`routes/meshcore.py`) is a separate, smaller console: BLE/serial
connect, messages/send, nodes, repeaters, **contacts CRUD**, telemetry,
traceroute.

Current SPA footprint to remove afterwards: the `#meshtasticVisuals` block
(~124 lines) and `#meshcoreVisuals` block in `index.html`, the two mode
partials (`meshtastic.html` 189 / `meshcore.html` 183), the two registry
entries, and the CSS/JS asset-map wiring. The module files move, not delete.

## Porting approach (per dashboard)

Mirror `/adsb/dashboard` / `/aprs/dashboard`, but because the modules are
self-contained the steps are mechanical:

1. **Route** in the existing blueprint: `@meshtastic_bp.route("/dashboard")`
   returning a new template with the `embedded` flag (mesh needs no
   observer/lat/lon, but pass them if the map wants a default centre).
2. **Template** `templates/meshtastic_dashboard.html`: standalone page, bundled
   assets, global nav, and the **existing visuals markup moved in verbatim** so
   every element ID the module expects is present (connection bar, status,
   messages, nodes, channels, map, modals). Load `js/modes/meshtastic.js`
   unchanged plus a 3-line inline init (`Meshtastic.init()` on
   `DOMContentLoaded`, `invalidateMap()` after first paint).
3. **CSS** `static/css/meshtastic_dashboard.css`: page chrome + a full-height
   layout. The mode's own styles stay in `css/modes/meshtastic.css` (linked).
   The current mode uses a tabless stacked panel; on a full page we can give it
   a **map + side tabs** (Messages / Nodes / Channels / Tools) so the console
   breathes — this is the main *design* decision, see open questions.
4. **Nav**: point the Meshtastic nav buttons (desktop `nav.html:137`, mobile
   `:309`) at `/meshtastic/dashboard`; add the path to `DASHBOARD_NAV_PATHS`.
5. **Retire SPA footprint**: remove the registry entry, the `#meshtasticVisuals`
   block, the partial, and the asset-map entries; the catalog card links to the
   dashboard. `meshtastic.js` moves to the dashboard's script list.
6. Repeat for Meshcore as its own `/meshcore/dashboard`.

## Sequencing — two sub-rounds

**B1 — Meshtastic** (bigger; do first, it sets the console layout).
**B2 — Meshcore** (smaller; reuses B1's page shell and chrome).

Keeping them separate keeps each PR reviewable and each dashboard shippable on
its own, and matches how we did APRS.

## Verification (per sub-round)

- Add the new path to `DASHBOARDS` in `tests/smoke/test_pages_load.py` and to
  `NAV_PAGES` in `tests/test_nav_state.py`.
- `tests/test_mode_registry.py`: add the dashboard template to the orphan-asset
  whitelist (mesh CSS/JS now belong to it) and confirm it stays green after the
  registry entry is removed.
- Full `pytest` + `ruff` + `node --check`; manual pass in both themes and at
  phone width. The smoke test can't exercise a real node connection, so it only
  proves the page and module load clean — connect/messaging still needs a manual
  check against hardware.

## Open questions for approval

1. **Layout.** Keep the current stacked-panel arrangement on the full page, or
   redesign as **map + tabbed side panel** (Messages / Nodes / Channels /
   Tools)? Recommendation: map + tabs — it's the point of going full-screen.
2. **Meshcore placement.** Its own `/meshcore/dashboard` (my recommendation,
   consistent with everything else), or a tab within the Meshtastic dashboard?
   They are separate blueprints and separate radios, so I lean to separate pages.
3. **Scope of B1.** Port the whole feature set (traceroute, range-test,
   telemetry, store-forward, QR) in one go, or ship a core dashboard
   (connect / messages / nodes / map / channels) first and fold the advanced
   tools into a B1.1? Recommendation: port everything — the module already
   implements it, so it's markup wiring, not new logic.
4. **Old SPA deep-links.** Same as APRS: hard-remove the SPA mode (my
   recommendation), or add a `?mode=meshtastic` → dashboard redirect?
