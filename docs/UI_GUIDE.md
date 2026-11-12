# INTERCEPT UI Guide

How the frontend is put together, and what to touch when adding a mode or a dashboard.

## Two Kinds of Page

- **The SPA** (`templates/index.html`) holds the text and scan modes: Pager, 433MHz, WiFi, Bluetooth, TSCM, Waterfall and the rest. Switching mode swaps a sidebar panel and output area without a page load.
- **Dashboards** are standalone pages for map-heavy modes: ADS-B, AIS, Satellite (and ISS), APRS, Meshtastic and Meshcore, each at `/<mode>/dashboard`. They use bundled assets only, so they work offline.

Map-heavy modes get a dashboard; everything else stays in the SPA. Don't move a dashboard mode back into the SPA.

## Adding an SPA Mode

1. **Registry entry** in `static/js/mode-registry.js`. One object per mode drives the welcome catalog, sidebar toggles, the destroy map, the visuals container and init dispatch:

   ```js
   morse: {
       label: 'Morse', indicator: 'MORSE', outputTitle: 'CW/Morse Decoder', group: 'signals',
       elementId: 'morseMode',     // id of the sidebar panel
       visuals: true,              // show the visuals container
       module: 'MorseMode',        // global IIFE module; module.destroy() runs on exit
       init: () => { MorseMode.init(); },
   },
   ```

   Groups are `signals`, `tracking`, `space`, `wireless`, `intel` and `system`.

2. **Sidebar partial** at `templates/partials/modes/<mode>.html`, included in the partials block of `index.html`.
3. **Assets**: add the mode's CSS to `INTERCEPT_MODE_STYLE_MAP` and its JS to `INTERCEPT_MODE_SCRIPT_MAP` in `index.html`. Both load lazily on first use.
   - `static/js/modes/<mode>.js` is an IIFE that exposes one global with `init()` and `destroy()`.
   - `static/css/modes/<mode>.css` holds styles scoped to the mode's panel.
4. **Navigation**: add a `mode_item(...)` (and `mobile_item(...)`) to `templates/partials/nav.html`, with an inline SVG icon.
5. **Backend**: a blueprint in `routes/` with an SSE stream for live output.

`tests/test_mode_registry.py` checks that the registry, partials and assets agree.

## Adding a Dashboard

APRS, Meshtastic and Meshcore show the current pattern; copy one of them.

1. **Route** on the mode's blueprint, rendering `templates/<mode>_dashboard.html`.
2. **Head**: set three variables and include the shared head, which loads fonts, Leaflet, the core CSS, `dashboard-shell.css` and observer location in the right order:

   ```jinja
   <head>
       {% set dash_title = 'APRS TRACKER // INTERCEPT - See the Invisible' %}
       {% set dash_mode_css = 'css/modes/aprs.css' %}
       {% set dash_layout_css = 'css/aprs_dashboard.css' %}
       {% include 'partials/dashboard_head.html' %}
   </head>
   ```

   Set `dash_needs_chart = true` to also load Chart.js.

3. **Chrome**: `static/css/dashboard-shell.css` provides the radar background, scan line, body base and header agent selector. Put layout in `static/css/<mode>_dashboard.css`, which loads after the shell and wins the cascade.
4. **Navigation**: include `partials/nav.html` with `active_mode` set, and pass the dashboard path as the `href` argument of the mode's `mode_item(...)`. Point the welcome catalog at it (`static/js/core/welcome-live.js`) and the command palette (`static/js/core/command-palette.js`).
5. **Teardown**: add the path to `DASHBOARD_NAV_PATHS` in `static/js/modes/core-mode-switch.js`, so the SPA stops running scans before navigating away.

The ADS-B, AIS and Satellite dashboards predate the shared head and shell and carry their own chrome. Don't copy them for new work.

## Design Tokens

All tokens live in `static/css/core/variables.css`. Use them instead of literal values.

| Kind | Tokens |
|------|--------|
| Backgrounds | `--bg-primary`, `--bg-secondary`, `--bg-card`, `--bg-elevated`, `--surface-sunken` |
| Text | `--text-primary`, `--text-secondary`, `--text-dim`, `--text-muted` |
| Accents | `--accent-cyan` (primary), `--accent-green`, `--accent-red`, `--accent-amber`, `--accent-purple`, each with a `-dim` variant |
| Status | `--status-online`, `--status-warning`, `--status-error`, `--status-offline`, `--status-info` |
| Borders | `--border-color`, `--border-light`, `--border-focus` |
| Spacing | `--space-1` (4px) to `--space-16` (64px) |
| Type size | `--text-xs` (10px) to `--text-xl` (18px) |
| Radius | `--radius-sm` (3px) to `--radius-xl` (8px) |

Themes are set by attributes on `<html>`. `data-theme="light"` selects the light theme, and `data-ui-tier="lean"` or `"enhanced"` selects the visual effects level. Both remap the same tokens, so styles built on tokens follow automatically.

## Fonts

| Token | Font | Use for |
|-------|------|---------|
| `--font-sans` | Inter | Body text |
| `--font-mono` | Roboto Condensed (in the SPA and dashboards) | Labels, headings, readouts |
| `--font-data` | JetBrains Mono | MAC addresses, hex, coordinates, IDs, frequencies, raw decoder output |

`--font-mono` is a condensed **sans** despite its name. Anything that needs aligned characters uses `--font-data`. Never write a bare `monospace` font stack, and never redefine `--font-data` per page.

Fonts are bundled in `static/css/fonts-local.css`, so nothing loads from a CDN.

## Conventions

- **Icons are inline SVG**, not emoji.
- **Offline first**: vendor libraries go under `static/vendor/`; don't add CDN links.
- **Mode-scoped CSS**: prefix selectors with the mode's panel or page so styles don't leak between modes.
- **Real-time data comes over SSE**: close the `EventSource` in the mode's `destroy()`.
- **Check in a browser**: `pytest tests/smoke -q` loads every mode and dashboard and fails on JavaScript errors.

## File Map

```
templates/
  index.html                  SPA shell, asset maps, partial includes
  <mode>_dashboard.html       Dashboard pages
  partials/
    nav.html                  Global navigation (mode_item / mobile_item)
    dashboard_head.html       Shared <head> for APRS / Meshtastic / Meshcore / ISS
    modes/<mode>.html         SPA sidebar panels
static/
  css/core/variables.css      Design tokens and themes
  css/dashboard-shell.css     Shared dashboard chrome
  css/<mode>_dashboard.css    Dashboard layout
  css/modes/<mode>.css        SPA mode styles
  js/mode-registry.js         SPA mode declarations
  js/modes/<mode>.js          Mode modules (IIFE)
  js/modes/core-mode-switch.js  switchMode, DASHBOARD_NAV_PATHS
```
