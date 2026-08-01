# Phase 1 Data Model: uConsole Handheld UI

This feature introduces **no new persisted entities, database schema, or API
payloads** — it is UI-layer only (see spec.md Assumptions and plan.md
Technical Context). The Requirements/Success Criteria do, however, imply two
client-side, unpersisted concepts that the implementation needs a shared
understanding of. Both are derived on the fly from the browser, never
written to `localStorage`, SQLite, or sent to the backend.

## Viewport Class (derived, not stored)

| Field | Values | Notes |
|---|---|---|
| class | `desktop` \| `handheld` | `handheld` = the new uConsole-class breakpoint from research.md Decision 1 (short viewport at desktop-plus width) |

- **Derivation**: Pure CSS media query (`max-height` combined with the
  existing width range). No JavaScript computes or stores this — the
  cascade itself is the source of truth, so it can never drift from what's
  actually on screen.
- **Used by**: FR-001–FR-010 (navigation, text sizing, control spacing, table
  column priority, dashboard sidebar behavior) — all expressed as CSS rules
  scoped to this breakpoint.

## Mode Activity State (in-memory, per mode module)

| Field | Values | Notes |
|---|---|---|
| state | `active` \| `background` \| `idle-active` | See lifecycle below |

- **`active`**: The mode is the one currently shown in the SPA/dashboard and
  the tab/window is focused. Full-frequency SSE/poll-driven rendering, as
  today.
- **`background`**: The mode is not currently shown. Already fully handled
  by the existing `destroy()` hooks in `static/js/mode-registry.js` — the
  `EventSource`/timers are closed, so there is nothing new to build for this
  state.
- **`idle-active`** *(new)*: The mode is currently shown, but the browser tab
  or window is not focused (`document.hidden` via the Page Visibility API).
  This is the state FR-011 adds handling for.

**Lifecycle / transitions**:

```text
background --(mode selected / init() runs)--> active
active --(mode switched away / destroy() runs)--> background
active --(tab/window loses focus)--> idle-active
idle-active --(tab/window regains focus)--> active   [FR-012: resume full rate immediately]
```

**Effect of state on behavior**:

- Only `idle-active` changes rendering behavior: it throttles how often a
  mode's SSE/poll handler writes incoming data to the DOM (FR-011).
- It never pauses, closes, or drops the underlying `EventSource`/decoder —
  data capture is unaffected in every state (constitution Principle V, and
  FR-011's explicit "no data is dropped or skipped").
- Transitioning back to `active` (FR-012) must display whatever data is
  already available immediately, without requiring the user to manually
  refresh.

No relationships, uniqueness, or identity rules apply — both concepts are
recomputed from the DOM/browser APIs on demand and hold no state that
outlives the current page/tab.
