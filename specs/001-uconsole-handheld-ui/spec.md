# Feature Specification: uConsole Handheld UI

**Feature Branch**: `001-uconsole-handheld`
**Created**: 2026-07-31
**Status**: Draft
**Input**: User description: "I want to make a UI that is adapted for the small screen of the ClockworkPi uConsole handheld computer. The current build is focused on regular monitors and it is hard to read and navigate."

## Clarifications

### Session 2026-07-31

- Q: What kind of metric defines "power efficient" for this feature? → A: Battery runtime extension target, measured against today's desktop-oriented UI running on the same uConsole hardware.
- Q: What's the target battery-runtime extension? → A: 25%, vs. the current desktop-oriented UI running on the same uConsole hardware under an equivalent workload.
- Q: What's the primary mechanism for hitting that target? → A: Automatically throttle UI re-renders/live-update frequency when a mode is idle or not the actively viewed mode; background data capture continues unaffected.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Navigate and orient on the small screen (Priority: P1)

An operator carrying a uConsole in the field opens the console and needs to
tell at a glance what mode is active and switch to a different mode, using
only the uConsole's built-in controls, without the navigation menu becoming
a wall of tiny, hard-to-read labels.

**Why this priority**: If an operator can't reliably read and select modes,
nothing else about the interface matters — this is the entry point to every
other capability.

**Independent Test**: Load the console on a uConsole-sized screen, and
switch between at least three different operating modes using only the
device's built-in input, confirming every label is legible and every
selection lands on the intended target.

**Acceptance Scenarios**:

1. **Given** the console is loaded on a uConsole-sized screen, **When** the
   user opens the mode navigation, **Then** every mode name is fully
   legible without zooming and without horizontal scrolling.
2. **Given** the mode navigation is open, **When** the user selects a
   different mode using only the uConsole's built-in input, **Then** the
   console switches to that mode without requiring a mouse or touchscreen.
3. **Given** any mode is active, **When** the user glances at the screen,
   **Then** the currently active mode and its high-level status are
   identifiable without additional navigation.

---

### User Story 2 - Read live data without losing information (Priority: P2)

An operator monitoring a streaming feed (for example, decoded pager
messages or sensor readings) needs to read incoming rows at a glance on the
uConsole's small display, without the layout shrinking text below
legibility or forcing horizontal scrolling to see the field that matters.

**Why this priority**: Reading incoming SIGINT data is the core value of
the platform; if the data itself becomes unreadable on the small screen,
navigation improvements alone aren't enough.

**Independent Test**: Load a text/scan-heavy mode on a uConsole-sized
screen and confirm incoming rows display at full width, with the most
important fields visible, without requiring horizontal scrolling to read
them.

**Acceptance Scenarios**:

1. **Given** a mode is streaming live text data, **When** new data arrives,
   **Then** it is displayed in readable, full-width rows without truncating
   the primary field.
2. **Given** a data table has more columns than fit comfortably on the
   screen, **When** it is viewed on the uConsole, **Then** the most
   important columns stay visible and the rest remain reachable without
   breaking the page layout.

---

### User Story 3 - Operate controls reliably with the device's built-in input (Priority: P3)

An operator starts/stops a decoder or changes a setting using the
uConsole's built-in pointing control, which is less precise than a mouse,
and needs each control to be big enough and spaced well enough to hit
reliably on the first try.

**Why this priority**: This refines the experience once navigation and
readability (P1, P2) are solid — misclicks are an annoyance, not a
blocker, but they compound the "hard to navigate" complaint.

**Independent Test**: On a uConsole-sized screen, perform a start/stop
action and a settings change using only the device's built-in input,
confirming no adjacent control is accidentally triggered.

**Acceptance Scenarios**:

1. **Given** the compact layout, **When** the user targets an actionable
   control (button/toggle) with the uConsole's built-in input, **Then** the
   control activates correctly on the first attempt without triggering a
   neighboring control.

---

### User Story 4 - View and interact with map-based dashboards on the small screen (Priority: P2)

An operator opens a map-centric dashboard (ADS-B, AIS, or satellite
tracking) on the uConsole and needs to see the map, pan and select tracks,
and read key details (e.g., callsign, MMSI, altitude) using only the
built-in keyboard and trackball, without controls overlapping or crowding
out the map.

**Why this priority**: Map dashboards carry essential situational-awareness
data and are explicitly in scope, but they present a fundamentally
different challenge (map real estate vs. dense text/controls) from the
text-oriented modes, so they're tracked as their own story rather than
folded into User Story 1/2.

**Independent Test**: Load an ADS-B, AIS, or satellite dashboard on a
uConsole-sized screen; pan the map, select a track, and read its detail
panel using only the built-in keyboard and trackball.

**Acceptance Scenarios**:

1. **Given** a map dashboard loaded on a uConsole-sized screen, **When**
   the user pans and selects a track using the built-in trackball, **Then**
   the map responds and the selected track's details are legible without
   overlapping the map.
2. **Given** the dashboard's controls (layer toggles, filters), **When**
   viewed on the small screen, **Then** they are reachable without
   obscuring the majority of the map view.

---

### Edge Cases

- Data tables with many columns (e.g., WiFi/Bluetooth scan results) MUST
  degrade to showing the most critical fields first rather than shrinking
  every column to illegibility or forcing whole-page horizontal scroll.
- Map-based dashboard pages (ADS-B, AIS, satellite) are in scope — see User
  Story 4 and FR-010 — but are not expected to match desktop-level map
  density; essential controls and the map itself take priority over
  secondary chrome.
- The console continues to be used on standard monitors after this change;
  that experience MUST NOT regress.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The console MUST present its mode navigation legibly on the
  target uConsole screen, with no horizontal scrolling required to read or
  select any mode name.
- **FR-002**: Users MUST be able to switch between all operating modes
  using only the uConsole's integrated keyboard and trackball/trackpad,
  without requiring an external mouse or a touchscreen.
- **FR-003**: All body text, labels, and primary control text MUST remain
  readable at arm's length on the target uConsole screen without the user
  zooming the browser.
- **FR-004**: For each mode, the layout MUST prioritize the single most
  important piece of live information (e.g., the primary readout or most
  recent data row) so it is visible on first load without scrolling.
- **FR-005**: Every mode and feature reachable in the existing
  desktop-oriented layout MUST remain reachable in the uConsole-adapted
  layout — no functionality may be dropped to fit the smaller screen.
- **FR-006**: The existing desktop/large-monitor experience MUST be
  unaffected by this change; the uConsole-adapted layout MUST apply only
  when the console is being viewed on a small screen.
- **FR-007**: Critical status indicators (active mode, decoder/SDR running
  state, error or alert banners) MUST remain visible or reachable within
  one interaction from anywhere in the compact layout.
- **FR-008**: Interactive controls (buttons, toggles, menu items) in the
  compact layout MUST be sized and spaced so that adjacent controls are not
  accidentally triggered when using the uConsole's built-in pointing
  control.
- **FR-009**: Data tables with more columns than fit the target screen
  width MUST surface the most important columns by default and make the
  remaining columns reachable without breaking the row layout or forcing
  scrolling of the entire page.
- **FR-010**: Map-based dashboard pages (ADS-B, AIS, satellite) MUST remain
  usable on the target uConsole screen: the map view, its essential
  controls, and a selected item's detail data MUST be reachable and legible
  using only the uConsole's integrated keyboard and trackball/trackpad.
- **FR-011**: The uConsole-adapted layout MUST automatically reduce how
  often the display refreshes with live updates for a mode that is idle or
  not the actively viewed mode. Background data capture (decoders and other
  processing) MUST continue unaffected by this throttling — no data is
  dropped or skipped, only how often the display refreshes.
- **FR-012**: When a throttled mode becomes the actively viewed mode again,
  the UI MUST resume full-frequency updates and display the
  currently-available data without requiring a manual refresh.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the uConsole's screen, a first-time user can identify the
  active mode and switch to a different mode within 10 seconds using only
  the device's built-in input.
- **SC-002**: All body text and primary control labels are readable at
  arm's length on the target screen without zooming, verified across every
  existing mode.
- **SC-003**: The single most relevant piece of live data for the active
  mode is visible without scrolling on first load, in at least 90% of
  existing modes.
- **SC-004**: Every mode and feature available on a standard desktop
  browser remains reachable on the uConsole-adapted layout — zero
  functionality loss.
- **SC-005**: Existing users on standard monitors report no visual or
  functional regression after this change ships.
- **SC-006**: Using only the uConsole's built-in pointing control, a user
  can activate an intended control on the first attempt at least 95% of
  the time, without triggering a neighboring control.
- **SC-007**: On a map-based dashboard, a user can pan the map and select an
  individual track/vessel/pass to view its details, using only the
  uConsole's built-in keyboard and trackball, without controls obscuring
  the majority of the map view.
- **SC-008**: Running the uConsole-adapted layout on uConsole hardware
  extends battery runtime by at least 25% compared to running the current
  desktop-oriented layout on the same hardware under an equivalent
  workload.

## Assumptions

- **Target display**: 5" IPS, 1280×720 (ClockworkPi uConsole CM4/CM5
  hardware revision). Other uConsole revisions with different resolutions
  (e.g., the 1280×480 A06 variant) are not the design target for this
  feature.
- **Primary input**: the uConsole's integrated keyboard and trackball/
  trackpad. An external mouse or touchscreen accessory is not assumed to be
  available.
- The uConsole is used in its fixed, integrated clamshell orientation;
  screen rotation/orientation-change handling is out of scope.
- This feature changes visual layout and navigation only — it does not add,
  remove, or change the behavior of any existing signal-processing
  capability.
- The uConsole-adapted layout is additive/responsive: it activates based on
  the viewing screen's size, and the current desktop-oriented layout
  remains the default experience on larger screens (per the project's
  existing responsive-design approach).
- Scope includes both the main text/scan-oriented SPA and the map-based
  dashboard pages (ADS-B, AIS, satellite); dashboards get a usable, legible
  compact treatment per FR-010/US4, not a full map-interaction redesign.
