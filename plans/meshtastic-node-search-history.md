# Meshtastic/Meshcore: node search + stored message history (issue #270)

Status: **Done.** Part 1 shipped as the Meshtastic Nodes tab in v2.33.42 (message a
node from it in v2.33.43) and Meshcore node search in v2.33.73. Part 2 shipped as
stored message history with Store & Forward replay in v2.33.72. Follow-ups from the
reporter's testing landed in v2.33.75 (real message times), v2.33.78 (favourite,
ignore and remove nodes, find in conversation, replies) and v2.33.81 (stable node
list). Followed the regression fix shipped in v2.33.41 (mesh dashboards were missing
core scripts).

## Context

#270 (split from #253, plus @bob1234uk's comments) asks for two features on the
Meshtastic dashboard (and, where it applies, Meshcore):

1. **Node search / Nodes tab** — many nodes never appear on the map (they have no
   position), and there's no way to look one up. Add a searchable node list.
2. **Existing/stored message history** — the app only shows messages received
   *after* it connected; show what's already stored on the node too.

## Current state (grounded)

- `/meshtastic/nodes` (`loadNodes()`) already returns every node the device
  knows, and it's called on connect. But the dashboard only turns nodes into
  **map markers when they have a position** (`updateNodeMarker`); there is **no
  node-list UI**. The `meshNode*` element ids are the *own-node* panel, not a
  list of the mesh.
- `/meshtastic/messages` (`loadMessages()`, called on connect) returns
  `_recent_messages` — "messages received **since the listener was started**."
  It does **not** read the device's persisted store, which is why history from
  before the app connected never shows.

## Part 1 — Nodes tab with search (mostly frontend)

- Add a **Nodes** tab to the dashboard's tabbed side panel (between Messages and
  Channels), rendering all nodes from `/meshtastic/nodes`:
  - Columns/fields: name (long/short), node id, last-heard (relative), SNR/RSSI,
    hops-away, battery, and a position indicator.
  - **Search box** filtering by name or node id (live).
  - Click a node → if it has a position, centre/zoom the map on it and open its
    marker; always show its details (reuse the existing node popup content).
  - Show a clear "no position" state for nodes that can't be mapped (bob's main
    pain point — they exist only in the list).
- Backend: confirm `/meshtastic/nodes` already includes last-heard + SNR + hops;
  if any are missing from the serialized node, enrich the response from the
  meshtastic lib's node DB (`interface.nodes`). Likely small.
- Meshcore already has a Nodes/Contacts panel + tabs; assess whether it needs
  the same search affordance or already covers this.

## Part 2 — Stored message history (backend-led)

- The device retains a limited recent message history; the meshtastic Python lib
  surfaces prior text messages during the initial connect/node-DB sync. On
  connect, capture those and seed `_recent_messages` (or a separate
  "stored"-tagged list) so `loadMessages()` returns them.
- Investigate exactly what the lib exposes on connect (packet log vs. a message
  store) — this is device/firmware-dependent, so the deliverable may be
  "history as far back as the device provides," not unlimited.
- Frontend: render seeded history in the feed, visually distinguishable from
  live messages if useful (e.g. a subtle "history" divider). De-dup against live
  messages that also arrive.
- Optional: expose received-packet history (beyond text) if the lib provides it;
  scope carefully to avoid a firehose.

## Sequencing

**270a — Nodes tab + search** first: higher value (bob's headline ask), mostly
frontend, low risk, no device-history uncertainty.
**270b — Stored history** second: needs meshtastic-lib investigation and is
inherently best-effort (bounded by what the device retains).

## Verification

- Unit/route tests for any `/meshtastic/nodes` enrichment and a history-seed
  endpoint/param.
- Playwright: Nodes tab renders injected nodes, search filters, clicking a
  positioned node centres the map, a position-less node shows the no-map state.
- `tests/test_dashboard_scripts.py` already guards the dashboard's script deps.
- **Hardware check required** for Part 2 especially: what history actually comes
  back is device-dependent and can't be verified here — @bob1234uk to confirm.

## Open questions

1. Nodes tab for **both** Meshtastic and Meshcore, or Meshtastic first?
   (Meshcore already has a contacts/nodes panel.)
2. History depth: seed into the existing feed, or a separate "History" view?
3. Include non-text received packets, or text messages only for now?
