# Implementation Plan: uConsole Handheld UI

**Branch**: `001-uconsole-handheld` | **Date**: 2026-07-31 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-uconsole-handheld-ui/spec.md`

## Summary

Adapt INTERCEPT's existing desktop-oriented Flask/Jinja/vanilla-JS UI so it is
legible and navigable on the ClockworkPi uConsole's 1280×720 display using
only its integrated keyboard/trackball, without regressing the desktop
experience or dropping any mode/feature — and do so power-efficiently
(≥25% battery-runtime extension target) by throttling display refresh for
idle/background modes.

The core technical finding driving this plan: the uConsole is 1280px wide,
which already clears every existing `min-width`/`max-width` breakpoint in
`static/css/responsive.css` (768px, 1024px) — so it currently renders as full
desktop chrome despite having roughly a third of a monitor's vertical space.
The fix is a new **height-aware** breakpoint layered onto the existing
responsive system, combined with the codebase's existing `data-ui-tier`
low-GPU-cost visual tier and `mode-registry.js` lifecycle hooks — not a new
framework or parallel UI.

## Technical Context

**Language/Version**: Python 3 (Flask backend, unchanged by this feature) +
vanilla ES6 JavaScript (no build step, IIFE-per-mode pattern) + CSS custom
properties (design tokens in `static/css/core/variables.css`)
**Primary Dependencies**: Flask/Jinja2 (existing templates), Leaflet.js
(existing map library used by SPA tracking modes and the ADS-B/AIS/Satellite
dashboards) — no new dependencies introduced
**Storage**: N/A — this feature is UI-only; it reads/writes only the existing
client-side `localStorage` preference (`intercept-ui-tier`), no schema or
persistence changes
**Testing**: `pytest` (existing backend suite, unaffected — no Python changes
expected); no JS/browser test framework exists in this repo, so frontend
verification is manual, per the constitution's Development Workflow clause
for UI changes (see Constitution Check)
**Target Platform**: Browser on ClockworkPi uConsole (CM4/CM5, 5" IPS,
1280×720, keyboard + trackball/trackpad only) as the new target, alongside
existing desktop/tablet/mobile browsers which must keep working unchanged
**Project Type**: Single Flask web application (server-rendered templates +
static JS/CSS) — no frontend/backend repo split
**Performance Goals**: Primary content visible without scrolling on a
1280×720 viewport (SC-003); ≥25% battery-runtime extension vs. today's UI on
the same uConsole hardware under equivalent workload (SC-008)
**Constraints**: No backend/API/SSE protocol changes (UI-layer only, per
spec Assumptions); zero desktop regression (FR-006); zero functionality loss
vs. desktop (FR-005)
**Scale/Scope**: All ~29 SPA modes registered in `static/js/mode-registry.js`
plus the 3 map-based dashboard pages (ADS-B, AIS, Satellite) sharing
`templates/layout/base_dashboard.html`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Backward Compatibility First | **PASS** | New breakpoint and throttling are additive — gated behind a new `max-height` media query and an idle/visibility check that only fire in the uConsole-class viewport or when a mode is not focused. Existing desktop-width, focused-tab behavior is untouched. No REST/SSE/env/schema changes. |
| II. Test-First Bug Fixes & Regression Safety | **PASS (via manual verification)** | This is new feature work, not a bug fix, so the reproduce-first mandate doesn't apply; no backend/Python code is expected to change, so `pytest` scope is unaffected. No JS/browser test harness exists in this repo. Per the constitution's Development Workflow clause, verification is manual: exercise the dev server in a browser at 1280×720 (golden path + edge cases) and at the existing 768px/1024px/desktop breakpoints (regression). This is documented here as the applicable verification method, not treated as a gate failure. |
| III. Standards Compliance & Static Verification | **PASS** | `ruff`/`black`/`mypy` apply only if Python changes are introduced (none planned). New CSS/JS extends the existing token system (`variables.css`), the existing `responsive.css` breakpoint file, and the existing `mode-registry.js` init/destroy pattern rather than introducing a parallel styling or state system. |
| IV. Surgical, Minimal-Footprint Changes | **PASS** | Plan deliberately reuses existing mechanisms (`data-ui-tier="lean"`, `mode-registry.js` destroy hooks, the dashboard sidebar-overlay pattern) instead of building new ones. Per-mode CSS/template touches should stay scoped to layout/sizing rules, not rewrites. |
| V. Process, Resource & Hardware Safety | **PASS** | No subprocess/decoder changes. New throttle timers/visibility listeners must be registered and torn down inside each mode's existing `init()`/`destroy()` hooks so they don't leak across mode switches — this is a design requirement carried into Phase 1/tasks, not a violation. |
| VI. Input Validation & Secure-by-Default | **N/A** | No new user input, subprocess invocation, or file path handling introduced by this feature. |

No violations requiring justification — Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-uconsole-handheld-ui/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md         # Phase 1 output (/speckit-plan command)
├── checklists/
│   └── requirements.md  # Spec quality checklist (/speckit-specify + /speckit-clarify)
└── tasks.md              # Phase 2 output (/speckit-tasks command — NOT created by /speckit-plan)
```

No `contracts/` directory: this feature makes no backend/API/SSE contract
changes (see research.md Decision 2 and spec.md Assumptions) — it is
UI-layer only, so there is no external interface to document a contract for.
No `quickstart.md`: there is no new setup/install step; the feature is
exercised by loading the existing dev server in a browser sized to the
uConsole viewport (see research.md Decision 5).

### Source Code (repository root)

```text
intercept-uconsole/                        # existing single Flask app — no restructuring
├── templates/
│   ├── index.html                         # Main SPA shell: sidebar, header, mode containers
│   ├── partials/
│   │   ├── nav.html                       # Global nav — shared by SPA + all dashboards
│   │   └── modes/*.html                   # Per-mode sidebar/content partials
│   ├── layout/
│   │   ├── base.html                      # Shared page shell
│   │   └── base_dashboard.html            # Shared dashboard shell (sidebar-overlay pattern to extend)
│   ├── adsb_dashboard.html
│   ├── ais_dashboard.html
│   └── satellite_dashboard.html
├── static/
│   ├── css/
│   │   ├── core/variables.css             # Design tokens + data-ui-tier="lean" (power-conscious tier to reuse)
│   │   ├── core/layout.css                # App shell layout
│   │   ├── responsive.css                 # Width-only breakpoints today — new height-aware breakpoint goes here
│   │   ├── index.css                      # SPA-specific styles
│   │   ├── modes/*.css                    # Per-mode styles
│   │   ├── adsb_dashboard.css
│   │   ├── ais_dashboard.css
│   │   └── satellite_dashboard.css
│   └── js/
│       ├── mode-registry.js               # Per-mode init/destroy hooks — throttle lifecycle goes here
│       └── modes/*.js                     # Per-mode IIFE modules (SSE/poll handlers to make throttle-aware)
└── tests/
    └── test_mode_registry.py              # Existing registry/asset consistency guard — must keep passing
```

**Structure Decision**: No new top-level directories or services. This
feature extends three existing surfaces in place — `static/css/responsive.css`
(new height-aware breakpoint), `static/css/core/variables.css` (reuse the
existing `lean` tier as the uConsole default), and `static/js/mode-registry.js`
plus individual `static/js/modes/*.js` modules (idle-throttle hook using each
mode's existing `destroy()`/`init()` lifecycle) — plus targeted template
changes in `templates/index.html`, `templates/partials/`, and
`templates/layout/base_dashboard.html` for the three map dashboards.

## Complexity Tracking

*No Constitution Check violations — this section is intentionally empty.*
