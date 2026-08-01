# Phase 0 Research: uConsole Handheld UI

All Technical Context fields were resolvable directly from the existing
codebase and the clarified spec — no `NEEDS CLARIFICATION` markers remain.
This document records the design decisions made from that research.

## Decision 1: Add a height-aware responsive breakpoint

**Decision**: Add a new breakpoint to `static/css/responsive.css` gated on
viewport **height** (short landscape, e.g. `max-height: ~800px` combined with
the existing desktop-width range), layered alongside — not replacing — the
current `min-width: 768px` / `min-width: 1024px` breakpoints.

**Rationale**: The uConsole's screen is 1280×720. At 1280px wide it already
clears every `min-width` threshold in `responsive.css` today, so it renders
full desktop chrome (always-visible sidebar, desktop spacing) despite having
roughly a third of a monitor's vertical space. A width-only breakpoint system
structurally cannot distinguish "wide desktop monitor" from "wide-but-short
handheld" — that gap is the root cause of the "hard to read and navigate"
complaint. A height-gated rule lets desktop-width layouts activate a compact
variant only when vertical room is actually scarce, leaving genuinely tall
desktop viewports untouched (satisfies FR-006).

**Alternatives considered**:
- *Reuse the existing ≤1024px mobile breakpoint* — rejected. That breakpoint
  also assumes narrow width and touch input (hamburger nav, touch-sized
  targets sized for phones), which doesn't match the uConsole's 1280px-wide,
  keyboard/trackball-only reality; reusing it would misclassify the device.
- *User-agent sniffing for uConsole hardware* — rejected as fragile (breaks
  on browser UA string changes) and inconsistent with the project's existing
  viewport-based responsive approach.

## Decision 2: Throttle idle/background modes via existing lifecycle hooks

**Decision**: Implement FR-011/FR-012 (automatic refresh throttling) as a
small shared helper that each mode's SSE/poll message handler opts into,
driven by the Page Visibility API (`document.hidden`) and the existing
`init()`/`destroy()` hooks already defined per mode in
`static/js/mode-registry.js` — not a new state-management layer.

**Rationale**: `mode-registry.js` already tears down each mode's
`EventSource`/timers via `destroy()` whenever it stops being the active mode
— the "not the actively viewed mode" case in FR-011 is already handled today
for background modes. The remaining gap is *intra-mode* idling: the active
mode's own handler still does full-rate DOM writes even when the
window/tab isn't focused. A shared debounce/batch helper on the DOM-write
side of each mode's message handler closes that gap without touching the
underlying stream, the backend, or data capture — satisfying "no data is
dropped or skipped" (FR-011) and instant resume on refocus (FR-012).

**Alternatives considered**:
- *A new global polling/throttling framework replacing every mode's
  `EventSource`* — rejected as disproportionate to the problem (Constitution
  Principle IV, Surgical Changes) compared to an opt-in shared helper.
- *Pausing/closing the `EventSource` connection itself when idle* — rejected:
  reopening long-lived SSE connections is more disruptive and can be more
  battery-costly than briefly reducing render frequency, and adds
  reconnection-storm risk that would need to be reconciled with Constitution
  Principle V (no leaked/unbounded resources) more carefully than adjusting
  render cadence does.

## Decision 3: Reuse the existing `data-ui-tier="lean"` visual tier

**Decision**: Default the new uConsole-class breakpoint to the codebase's
existing `lean` UI tier (`html[data-ui-tier="lean"]` in
`static/css/core/variables.css`), which already strips shadows, blur,
ambient gradients, and the scanline animation, rather than inventing a
second "power mode" concept. The existing Settings-driven tier choice
(persisted in `localStorage` as `intercept-ui-tier`) remains an explicit
override.

**Rationale**: The codebase already ships a purpose-built low-GPU-cost
visual tier plus a `prefers-reduced-motion` override — exactly the kind of
rendering-cost reduction the power-efficiency requirement calls for, already
wired through Settings. Defaulting to it at the uConsole breakpoint reduces
paint/GPU load with zero new CSS system, directly supporting the SC-008
battery-runtime target.

**Alternatives considered**:
- *A parallel "power-saver" CSS class* — rejected: functionally duplicates
  `lean` tier with no added benefit, and would violate Principle III
  (one enforced standard) and Principle IV (minimal footprint).

## Decision 4: Extend the existing dashboard sidebar-overlay pattern

**Decision**: For the map-based dashboards (FR-010/US4), extend
`templates/layout/base_dashboard.html`'s existing width-based sidebar-overlay
behavior (`@media (max-width: 768px)`, which turns the always-visible 320px
`.dashboard-sidebar` into a slide-in drawer) with the same height-aware
breakpoint from Decision 1, and let the `lean` tier (Decision 3) disable the
continuous `scanline`/`radar-bg` CSS animations at that breakpoint.

**Rationale**: The dashboards already have a working overlay-sidebar
mechanism for constrained screens; extending its trigger condition is
surgical (Principle IV) versus building a new sidebar mechanism. The
`scanline` element runs a continuous 8s CSS animation
(`animation: scanline 8s linear infinite`) purely for visual flavor — a real,
avoidable, continuous paint cost directly opposed to the power-efficiency
goal, and one the `lean` tier is already designed to suppress.

**Alternatives considered**:
- *A separate compact dashboard template* — rejected as unnecessary
  duplication for what is a CSS-breakpoint-scoped adjustment.

## Decision 5: Manual browser verification, no new test tooling

**Decision**: Verify this feature by exercising the dev server in a browser
resized to 1280×720 (golden path + edge cases from spec.md), plus regression
checks at the existing 768px/1024px breakpoints and a standard desktop width.
No JS/browser test framework is introduced.

**Rationale**: The repository's `pytest` suite covers only the Python
backend; there is no existing JS/browser test harness, and this feature
introduces no backend code for `pytest` to cover. The constitution's
Development Workflow section already anticipates this for UI/frontend
changes ("the dev server MUST be exercised in a browser... a passing test
suite verifies code correctness, not feature correctness"), so this is the
applicable verification method rather than a gap. Introducing a new test
framework solely for this feature would be a disproportionate new dependency
for a brownfield project whose constitution (Principle IV) discourages
unrequested new abstractions.

**Alternatives considered**:
- *Introduce Playwright or a visual-regression tool* — noted as a reasonable
  *future* investment for the project generally, but out of scope here: it
  would be the first frontend test tooling in the repo, with no existing
  precedent or CI wiring to extend, and isn't required to satisfy this
  feature's spec.
