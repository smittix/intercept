---

description: "Task list for uConsole Handheld UI"
---

# Tasks: uConsole Handheld UI

**Input**: Design documents from `/specs/001-uconsole-handheld-ui/`
**Prerequisites**: [plan.md](plan.md) (required), [spec.md](spec.md) (required for user stories), [research.md](research.md), [data-model.md](data-model.md)

**Tests**: Not requested in the feature spec. Per plan.md's Constitution
Check (Principle II), this repo has no JS/browser test framework, so
verification is manual browser testing — each user story phase ends with a
manual-verification task instead of an automated test task.

**Organization**: Tasks are grouped by user story (from spec.md) to enable
independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- File paths are exact and relative to the repository root

## Path Conventions

Single existing Flask web app — no new top-level directories. Templates in
`templates/`, static JS in `static/js/`, static CSS in `static/css/`, per
plan.md's Project Structure.

## Phase 1: Setup

**Purpose**: Establish the one piece of shared "basic structure" every later
task nests under — the new breakpoint itself (research.md Decision 1).

- [ ] T001 Add the shared uConsole/handheld responsive breakpoint as a
      documented block at the top of `static/css/responsive.css` — a
      height-aware media query (e.g. `@media (max-width: 1366px) and
      (max-height: 820px)`) covering the uConsole's 1280×720 panel without
      also matching typical tall desktop monitors at similar widths. Leave
      it empty except for a comment explaining its purpose; Foundational and
      per-story tasks below add rules inside it.

**Checkpoint**: The breakpoint exists and compiles with no visual effect yet
— safe to build on.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared infrastructure that every user story phase below reuses.

**⚠️ CRITICAL**: Complete this phase before starting any user story phase.

- [ ] T002 [P] Create shared idle/visibility throttle helper module
      `static/js/core/idle-throttle.js`, following the existing pattern of
      `static/js/core/settings-manager.js`. Export a small function (e.g.
      `registerIdleThrottle(applyFn, options)`) that: batches/delays calls to
      `applyFn` (the DOM-write step of a mode's SSE/poll handler) while
      `document.hidden` is `true`, and flushes the latest pending call
      immediately on the `visibilitychange` event when the tab regains
      focus. Must not touch the underlying `EventSource`/timer — only the
      DOM-write callback. This is the mechanism FR-011/FR-012 (data-model.md
      "Mode Activity State") build on.
- [ ] T003 [P] Default `data-ui-tier` to `lean` when the T001 breakpoint is
      active and the user has not made an explicit choice in Settings.
      Update the tier-bootstrap inline script in `templates/partials/nav.html`
      and the duplicated bootstrap snippets in `templates/adsb_dashboard.html`,
      `templates/ais_dashboard.html`, and `templates/satellite_dashboard.html`
      (each currently does
      `localStorage.getItem('intercept-ui-tier')||'enhanced'`) to check
      `window.matchMedia` against the T001 breakpoint and fall back to
      `'lean'` instead of `'enhanced'` only when no stored preference exists.
- [ ] T004 [P] Add compact-layout design tokens for the T001 breakpoint in
      `static/css/core/variables.css`: reduced `--header-height`,
      `--nav-height`, `--sidebar-width` values, and confirm/extend
      `--touch-min`/`--touch-comfortable` (from `static/css/responsive.css`)
      are large enough for reliable trackball targeting. Scope all of these
      under the T001 media query so desktop values are untouched.

**Checkpoint**: Shared breakpoint, throttle helper, tier default, and
compact tokens exist — user story phases can now proceed independently.

---

## Phase 3: User Story 1 - Navigate and orient on the small screen (Priority: P1) 🎯 MVP

**Goal**: Legible, keyboard/trackball-only mode navigation with a glanceable
active-mode indicator on the uConsole screen.

**Independent Test**: Load the console at 1280×720, switch between at least
three modes using only keyboard/trackball, confirm every label is legible
and every selection lands on the intended target.

### Implementation for User Story 1

- [ ] T005 [US1] Collapse the always-visible desktop sidebar/mode-navigation
      in `templates/index.html` and `templates/partials/nav.html` into a
      legible, no-horizontal-scroll layout under the T001 breakpoint in
      `static/css/responsive.css`, reusing the existing mobile
      hamburger/drawer mechanism (research.md Decision 1) rather than
      building a new one. Use the T004 `--sidebar-width`/`--nav-height`
      tokens.
- [ ] T006 [US1] Apply the T004 compact tokens to mode-navigation list items
      in `static/css/index.css` so each item meets the minimum
      trackball-friendly target size and keyboard focus order is preserved
      (no custom tabindex removal) — satisfies FR-002/FR-008 for navigation.
- [ ] T007 [US1] Ensure the currently active mode and its high-level status
      are visible in the header/status area at the T001 breakpoint without
      opening navigation, in `templates/index.html` and `static/css/index.css`
      — satisfies FR-007 and Acceptance Scenario 3.
- [ ] T008 [US1] Manually verify Acceptance Scenarios 1-3 in a browser
      resized to 1280×720: mode nav is fully legible with no horizontal
      scroll, switching modes works with keyboard/trackball only, and the
      active mode/status is glanceable. Also confirm no visual change at
      1024px/768px/a standard desktop width (regression check).

**Checkpoint**: User Story 1 is independently functional and testable.

---

## Phase 4: User Story 2 - Read live data without losing information (Priority: P2)

**Goal**: Streaming/live data stays readable at full width, and idle modes
throttle their refresh rate without losing data.

**Independent Test**: Load a text/scan-heavy mode at 1280×720, confirm
incoming rows are readable at full width with the most important field
visible; confirm background-tab throttling doesn't drop data.

### Implementation for User Story 2

- [ ] T009 [P] [US2] Under the T001 breakpoint, apply column-priority rules
      to wide data tables/rows (WiFi/Bluetooth scan results, pager/sensor
      stream rows, etc.) in `static/css/modes/*.css` and `static/css/index.css`
      so the most important field(s) stay visible and the rest remain
      reachable without breaking the row layout or forcing whole-page
      horizontal scroll — satisfies FR-004/FR-009.
- [ ] T010 [US2] Wire the T002 `idle-throttle` helper into the SSE/poll
      message handlers of the live-data mode modules in `static/js/modes/*.js`
      (the modules using `EventSource`/`setInterval` per
      `static/js/mode-registry.js`, e.g. `bluetooth.js`, `bt_locate.js`,
      `gps.js`, `meshtastic.js`, `meshcore.js`, `morse.js`, `ook.js`,
      `subghz.js`, `sstv.js`, `sstv-general.js`, `weather-satellite.js`,
      `waterfall.js`, `system.js`, plus the inline pager/sensor/rtlamr/aprs/
      tscm/radiosonde handlers in `templates/index.html`): batch/delay the
      DOM-write step through the helper while the tab is hidden, and flush
      immediately on refocus. Do not alter the `EventSource`/timer
      lifecycle itself — only the render step. Satisfies FR-011/FR-012.
- [ ] T011 [US2] Manually verify Acceptance Scenarios 1-2 at 1280×720 for at
      least one text-heavy mode (e.g. pager or WiFi scan): live rows display
      full-width without truncation, and a wide table's important columns
      stay visible. Also verify: backgrounding the tab and returning shows
      the latest data immediately (T002/T010), with no console errors.

**Checkpoint**: User Stories 1 AND 2 both work independently.

---

## Phase 5: User Story 3 - Operate controls reliably with the device's built-in input (Priority: P3)

**Goal**: Buttons/toggles/menu items are large and spaced enough for
reliable trackball activation.

**Independent Test**: At 1280×720, perform a start/stop action and a
settings change using only keyboard/trackball, confirming no adjacent
control is accidentally triggered.

### Implementation for User Story 3

- [ ] T012 [US3] Under the T001 breakpoint, increase spacing/size of
      interactive controls (start/stop buttons, toggles, menu items) to the
      T004 compact-layout minimum target size in `templates/partials/modes/*.html`
      and `static/css/core/components.css` — satisfies FR-008.
- [ ] T013 [US3] Manually verify Acceptance Scenario 1 at 1280×720: target a
      start/stop control and a settings toggle with keyboard/trackball only
      and confirm first-attempt activation without triggering a neighboring
      control.

**Checkpoint**: User Stories 1, 2, AND 3 all work independently.

---

## Phase 6: User Story 4 - View and interact with map-based dashboards on the small screen (Priority: P2)

**Goal**: ADS-B/AIS/Satellite dashboards remain usable — map, controls, and
selected-item details all reachable via keyboard/trackball at 1280×720.

**Independent Test**: Load an ADS-B, AIS, or satellite dashboard at
1280×720; pan the map, select a track, and read its detail panel using only
keyboard/trackball.

### Implementation for User Story 4

- [ ] T014 [US4] Extend the existing `@media (max-width: 768px)`
      sidebar-overlay rule in `templates/layout/base_dashboard.html` to also
      trigger at the T001 breakpoint, collapsing the always-visible 320px
      `.dashboard-sidebar` into the existing slide-in drawer for the
      ADS-B/AIS/Satellite dashboards (research.md Decision 4) — satisfies
      FR-010.
- [ ] T015 [P] [US4] Verify/extend the `html[data-ui-tier="lean"]` rules in
      `static/css/core/variables.css` so the `.scanline` and `.radar-bg`
      continuous CSS animations in `templates/layout/base_dashboard.html`
      are fully disabled under the `lean` tier (T003 makes `lean` the
      default at the T001 breakpoint) — supports SC-008 for dashboard pages.
- [ ] T016 [US4] Ensure dashboard layer-toggle/filter controls and the
      selected-item detail panel remain reachable via keyboard/trackball
      without obscuring the majority of the map under the T001 breakpoint,
      in `static/css/adsb_dashboard.css`, `static/css/ais_dashboard.css`,
      and `static/css/satellite_dashboard.css` — satisfies FR-010 and
      Acceptance Scenario 2.
- [ ] T017 [US4] Manually verify Acceptance Scenarios 1-2 at 1280×720 for at
      least one dashboard (e.g. ADS-B): pan/select a track via trackball,
      confirm its detail panel is legible without overlapping the map, and
      confirm layer/filter controls don't obscure most of the map.

**Checkpoint**: All four user stories are independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Confirm nothing outside this feature's scope broke, and
validate the two feature-wide success criteria that no single story fully
covers on its own.

- [ ] T018 [P] Run `ruff check .`, `black --check .`, and `mypy .` from the
      repository root to confirm no Python files were inadvertently touched
      (plan.md's Technical Context states no Python changes are expected —
      this task confirms that held).
- [ ] T019 [P] Run `pytest tests/test_mode_registry.py` to confirm the
      existing registry/asset-consistency guard (CLAUDE.md) still passes
      unchanged.
- [ ] T020 Full regression pass for SC-005: manually exercise a
      representative sample of modes and one dashboard at the existing
      768px, 1024px, and a standard desktop width (e.g. 1920×1080),
      confirming the T001 breakpoint does not fire there and desktop/tablet/
      mobile layouts are visually unaffected.
- [ ] T021 Verify SC-008 (≥25% battery-runtime extension): on uConsole
      hardware, compare battery drain (or a CPU/GPU-utilization proxy if
      hardware isn't available) running an equivalent workload before and
      after this feature, per research.md Decision 2's throttling mechanism.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup (T001, the breakpoint) —
  BLOCKS all user stories.
- **User Stories (Phase 3-6)**: All depend on Foundational (Phase 2)
  completion. Per spec.md's own scoping, US1-US4 have no dependencies on
  each other and can proceed in parallel (if staffed) or in priority order
  (P1 → P2 → P2 → P3, i.e. US1 → US2/US4 → US3).
- **Polish (Phase 7)**: Depends on all desired user stories being complete.

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational — no dependency on
  other stories.
- **User Story 2 (P2)**: Can start after Foundational — no dependency on
  other stories (uses the T002 throttle helper and T004 tokens from
  Foundational, not from US1).
- **User Story 3 (P3)**: Can start after Foundational — no dependency on
  other stories.
- **User Story 4 (P2)**: Can start after Foundational — no dependency on
  other stories (dashboards are separate pages from the SPA that US1-3
  touch).

### Within Each User Story

- Story-specific CSS/template tasks before that story's manual-verification
  task.
- T010 (US2 throttle wiring) depends on T002 (Foundational) but not on T009
  (US2's own table-layout task) — they touch different files and can run in
  parallel within the story.

### Parallel Opportunities

- T002, T003, T004 (Foundational) touch different files and can run in
  parallel.
- T009 and T010 (US2) touch different files (CSS vs. JS) and can run in
  parallel.
- T015 (US4) touches only `variables.css`/`base_dashboard.html` CSS rules
  and can run in parallel with T014/T016 once T001/T003 exist.
- Once Foundational (Phase 2) completes, all four user story phases (3-6)
  can be worked in parallel by different contributors.
- T018 and T019 (Polish) are independent commands and can run in parallel.

---

## Parallel Example: Foundational Phase

```bash
# After T001 (breakpoint) is done, launch T002-T004 together:
Task: "Create static/js/core/idle-throttle.js"
Task: "Update tier-bootstrap scripts in templates/partials/nav.html + 3 dashboard templates"
Task: "Add compact-layout tokens to static/css/core/variables.css"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001).
2. Complete Phase 2: Foundational (T002-T004) — CRITICAL, blocks all stories.
3. Complete Phase 3: User Story 1 (T005-T008).
4. **STOP and VALIDATE**: Run T008's manual verification independently.
5. Deploy/demo if ready — a uConsole user can now navigate the console
   legibly, even before live-data throttling or dashboard support ship.

### Incremental Delivery

1. Setup + Foundational → shared breakpoint/tokens/throttle helper ready.
2. Add User Story 1 → verify (T008) → deploy/demo (MVP).
3. Add User Story 2 → verify (T011) → deploy/demo.
4. Add User Story 4 → verify (T017) → deploy/demo (both P2 stories done).
5. Add User Story 3 → verify (T013) → deploy/demo.
6. Polish (T018-T021) → confirm zero regression and the SC-008 power target.

### Parallel Team Strategy

With multiple developers: complete Setup + Foundational together first (one
person can do T001 then T002-T004 in parallel), then split
US1/US2/US3/US4 across developers — they touch disjoint files (SPA
templates/CSS/JS vs. dashboard templates/CSS) so integration risk is low.

---

## Notes

- [P] tasks touch different files with no dependency on incomplete tasks in
  the same phase.
- [Story] label maps each task to its user story for traceability back to
  spec.md.
- No automated tests are generated (not requested in spec.md); each story
  ends with an explicit manual-verification task instead, per plan.md's
  Constitution Check on Principle II.
- Every task in Phases 3-6 must also satisfy FR-005/FR-006 implicitly:
  changes are additive under the T001 breakpoint only — never remove or
  restyle the existing desktop-width rules.
- Commit after each task or logical group, per this repo's standing git
  workflow.
