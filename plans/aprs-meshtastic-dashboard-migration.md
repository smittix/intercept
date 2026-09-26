# APRS & Meshtastic → dedicated dashboards (migration plan)

Status: **proposed, awaiting approval.** No code in this plan has been written.

## Why

`CLAUDE.md` (UI direction, decided 2026-06-12): map-heavy modes get dedicated
dashboard pages (`/adsb/dashboard`, `/ais/dashboard`, `/satellite/dashboard`);
the `index.html` SPA keeps text/scan modes. It explicitly names APRS and
Meshtastic as map-centric and says they *"should migrate to dashboards under
their own plans — do not grow their SPA footprint."* This is that plan.

Benefits: a full-height map like the other trackers; bundled (offline) assets
so the page loads without CDN reachability; and it lets us delete a large slice
of SPA code once each mode has moved (APRS alone is 911 lines of JS).

## Current state (what exists today)

APRS (SPA):
- `static/js/modes/aprs-spa.js` — 911 lines (map, markers, station list,
  decoder start/stop, SSE + polling streams, signal meter). Already isolated
  into its own file by the main-page split.
- `templates/partials/modes/aprs.html` — 71-line SPA panel.
- Registry entry `aprs` in `static/js/mode-registry.js` (init/destroy call the
  global `initAprsMap`/`checkAprsTools`/`destroyAprsMode`).
- Backend already dashboard-ready — `routes/aprs.py` (`url_prefix="/aprs"`)
  exposes `/tools /status /stations /export /data /start /stop /stream
  /frequencies /spectrum`. **No backend changes needed.**

Meshtastic / Meshcore (SPA):
- `static/js/modes/meshtastic.js` — 2,376 lines (`Meshtastic` module, IIFE).
- `static/js/modes/meshcore.js` + `templates/partials/modes/meshcore.html`
  (183 lines) and `meshtastic.html` (189 lines).
- Registry entries `meshtastic` (module `Meshtastic`) and `meshcore`
  (module `MeshCore`).
- Backend: `routes/meshtastic.py` (`url_prefix="/meshtastic"`, ~30 endpoints
  incl. channels, send, traceroute, range-test, telemetry, QR, store-forward)
  and `routes/meshcore.py`. Feature-rich — this is much more than a map.
- Note: `CLAUDE.md` still calls the file `meshtastic_routes.py`; the actual
  files are `routes/meshtastic.py` and `routes/meshcore.py`. Worth fixing that
  line in `CLAUDE.md` when we touch this.

## Target pattern (mirror /adsb/dashboard)

For each dashboard, copy the shape already proven by `adsb_dashboard.html` +
`routes/adsb.py::adsb_dashboard`:

1. Route: `@aprs_bp.route("/dashboard")` returning a dedicated template, with
   `embedded = request.args.get("embedded","false")=="true"` and the same
   `shared_observer_location` / `default_latitude` / `default_longitude`
   context the other dashboards pass.
2. Template `templates/aprs_dashboard.html`: standalone page (own `<head>`),
   **bundled assets only** (local fonts, vendored Leaflet, core CSS), the
   global nav partial, a full-height map, and side/bottom panels for the
   station list, meter and controls. Its own `static/css/aprs_dashboard.css`.
3. Client JS: reuse the existing `/aprs/*` JSON + SSE endpoints. Start from
   `aprs-spa.js` logic, adapted to the dashboard layout (no SPA mode
   plumbing). Prefer the shared components the ADS-B/AIS dashboards use
   (`track-icons.js`, `map-utils.js`, HUD/label helpers) so it looks native.
4. Nav wiring: change the `aprs` nav buttons in `templates/partials/nav.html`
   (desktop line ~95, mobile line ~281) to link to `/aprs/dashboard` in a new
   tab, exactly like the `adsb`/`ais`/`satellite` `mode_item(... , '/path')`
   calls. Add `/aprs/dashboard` to `DASHBOARD_NAV_PATHS` in
   `core-mode-switch.js` so SPA teardown treats it as a dashboard nav.
5. Retire the SPA footprint: remove the `aprs` registry entry, its partial,
   its CSS/JS lazy-load asset-map entries, its include in the partials block,
   and delete `aprs-spa.js`. `tests/test_mode_registry.py` will confirm the
   registry/asset maps stay consistent after removal.

## Suggested sequencing (two separate rounds)

**Round A — APRS.** Smallest and already isolated; lowest risk; establishes the
template/route/CSS trio we reuse for Meshtastic. One dashboard, one nav change,
delete `aprs-spa.js` + partial + registry entry.

**Round B — Meshtastic (+ Meshcore).** Larger and genuinely feature-heavy
(traceroute, range-test, telemetry history, channel QR, store-forward). This is
not a straight map port; the dashboard needs tabs/panels for those tools. Treat
Meshcore as a sibling page or a tab. Recommend its **own** follow-up plan once
Round A has settled the pattern, rather than committing to a layout now.

## Verification (per round)

- Add the new dashboard path(s) to `DASHBOARDS` in
  `tests/smoke/test_pages_load.py` — the smoke test then loads each in
  dark/light × desktop/phone and fails on any JS error, missing `/static/`
  asset, or phone horizontal overflow.
- Add the new path(s) to `NAV_PAGES` in `tests/test_nav_state.py` (Kill All +
  global nav reachable on every dashboard).
- `tests/test_mode_registry.py` must stay green after removing the SPA entry.
- Full `pytest` + a manual pass in both themes and at phone width.

## Open questions for approval

1. **APRS-only this round, or both?** Recommendation: APRS now (Round A);
   Meshtastic under its own plan (Round B).
2. **Meshcore:** separate `/meshcore/dashboard`, or a tab inside the Meshtastic
   dashboard? (Backends are separate blueprints.)
3. **Old SPA mode:** hard-remove the registry entry (my recommendation, matches
   how ADS-B/AIS/satellite were done), or keep a stub that redirects `?mode=aprs`
   deep-links to the dashboard? Deep-link redirect is a small extra we can add
   if you want old links to keep working.
