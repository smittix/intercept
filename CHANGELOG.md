# Changelog

All notable changes to iNTERCEPT will be documented in this file.

## [2.33.24] - 2026-09-25

Polish round 4.

### Changed

- **Sidebar status card** carries the mode's Start / Stop (it presses the mode's own button), so long sidebars need no scrolling.
- **Toolbar**: theme, settings, help and a labelled "Stop all" stay; display mode, network monitor, agents, voice alerts, cheat sheet, shortcuts and log out move to a labelled **More** menu.
- **Notification history**: a bell lists the pop-ups shown in this tab (the last 50, following you between pages), with a count of unseen ones.
- **Header**: aircraft, vessels, Wi-Fi networks and Bluetooth devices in view as chips that open their mode; zeros are left out.
- **ADS-B and AIS maps**: a reception outline, the furthest contact heard in each 10° of bearing, built up across sessions per location ("Reach" toggle, and a reset).
- **Satellite Command**: every tracked satellite's passes over the next 24 hours on one timeline, bar height the peak elevation; click to select.
- **Spy Stations**: a table view, one row per station, alongside the cards.
- **Weather satellite**: decoded images grouped by pass (satellite, time, frequency, mode, count), with image sizes; the sidebar opens on the controls rather than "Getting Started" (in every mode, guide sections are no longer opened by default).
- **Meters**: cards side by side.
- **Readability**: muted and dim text, and the Enhanced tier's teal, now meet 4.5:1 contrast in both themes; 27 hard-coded near-black greys use the theme's colour; every page has a keyboard focus ring.
- **Loading placeholders** share one style: a small spinner and dim text.

### Fixed

- Meters read the meter number from `ID` only, so all SCM+ (`EndpointID`) and IDM (`ERTSerialNumber`) meters were merged into one "Unknown" card; rates from readings seconds apart were in the millions.
- `AppFeedback` was not on `window`, so checks for it failed; the main page's `showInfo` drew its own box instead of a toast.
- A WeFax scheduler test failed between 00:00 and 02:00 UTC.

---

## [2.33.23] - 2026-09-24

### Changed

- **WebSDR globe**: receivers are flat dots coloured by load (green under half their listener slots used, amber at half or more) instead of bars standing off the globe; latitude and longitude lines; the globe fills more of the panel. Your location is marked, the view opens on it, and choosing a receiver draws a path from you to it. Hovering a receiver in the list highlights it on the globe.
- **WebSDR list**: nearest first, with distance and direction, place and antenna, and listener slots as a bar; a summary line (receivers, free slots, nearest); search, and sort by nearest, most free or name.
- `GET /websdr/receivers` takes optional `lat` and `lon`, adding `distance_km` and `bearing` and sorting nearest first before the 100-receiver cap.

### Fixed

- WebSDR receiver names, places and antenna notes showed raw HTML and entities from the KiwiSDR directory.
- A receiver without a position was drawn at 0,0.

---

## [2.33.22] - 2026-09-24

Polish round 3.

### Changed

- **Light theme**: with the Enhanced interface tier (the default), the light theme left the sidebar, section headers, output panel, nav and many panels near-black. Enhanced-tier colour rules now apply only in the dark theme; panels set into the page use a new `--surface-sunken` colour that suits both themes; inline dark panel colours use the theme's. Scopes, waterfalls and maps stay dark as displays.
- **Sidebar**: a mode's description-only opening section folds behind an (i) on the status card, so the sidebar opens on a section you can set (17 modes).
- **Sidebar**: the red "Kill All Processes" button goes; it duplicated "Stop all running processes" (⊗) in the toolbar.
- **Bottom bar** (Recon, Mute, Auto-scroll, export, Clear) shows only for Pager, 433 MHz, Meters and OOK, the modes whose feed it acts on.
- **Waterfall**: an idle note explains what will appear and how to tune; the mode buttons wrap instead of clipping LSB.
- **WebSDR**: receivers load on first visit (the server caches the list for an hour).
- **ADS-B and AIS dashboards**: a trend line of the live count over the last 15 minutes.
- **Phone**: the welcome page lists modes first; the bottom bar is one row that scrolls sideways.

### Fixed

- Firefox showed white scrollbars on the dashboards, ADS-B history and network monitor.
- The WeFax sidebar status card sat below five sections.
- The light theme's active nav group had an invisible label.
- The Settings tabs left About alone on a second row.

---

## [2.33.21] - 2026-09-24

A second polish pass: cleaner, more consistent, and less space spent on nothing.

### Changed

- **Sidebar**: a status card at the top says, for the mode in view, whether it is running and for how long, and which SDR, frequency and gain it will use. Section headers get icons. The sections you open are remembered per mode; a mode you have not arranged opens its first section.
- **Header**: the Run State strip sits in the header row instead of taking a row of its own.
- **Signal bars** use the same colours everywhere.
- **Pop-ups**: the ADS-B dashboard's five differently styled banners (airband tools, aircraft database, readsb, DVB drivers, alerts) are now the app's toasts, top right, clear of the controls. The Agents page uses them too.
- **Welcome page**: a Live card shows what is running (each a link back to it), the SDRs attached and in use, and the last 24 hours of observations by source. Mode tiles whose decoder is running get a light.
- **System Health**: temperatures as a heat grid of tiles, RAM and swap as gauges, and CPU and RAM history lines.
- **TSCM**: a threat gauge (No data, Low, Elevated, High) leads the banner, with the counts as neutral tiles beside it. The sweep panels say whether the sweep is stopped, listening, or failed to start.
- **433 MHz**: the "Audio Waveform" was a sine wave synthesised from each packet's level; it is replaced by a plot of the packets over the last minute, each as tall as its level, coloured by SNR and labelled with its device, over the noise floor. The server no longer builds the waveform or sends `scope` events.
- **OOK**: the latest frame is drawn as a pulse train with its bytes beneath, and the panel shows while idle.
- **Morse** and **Meteor**: idle views show what they will show (a keyed "CQ" envelope; the odd faint meteor streak) instead of an empty box.
- **Mode timelines** (433 MHz, pager, TSCM) say "No activity in this window" on one line, instead of a large block repeating the empty state below.
- **ADS-B history**: sparklines on the Messages, Snapshots and Aircraft totals, and a Traffic strip of aircraft per hour. `GET /adsb/history/traffic` returns the per-bucket counts.
- **Radiosonde**: each sonde card has an ascent profile (temperature against altitude), and says Ascending, or Descending with the burst altitude.
- **BT Locate** and **drone detection** maps open on your location instead of the whole world, with a note until something is plotted.
- **Agents**: health rings (response time), what each agent is running, a compact header and summary line, and the register form folds away once there are agents.

### Fixed

- The live empty state stayed visible above items in lists that append (OOK's frame log).
- Selected toggles in OOK and Morse (PWM/PPM/Manchester, MSB/LSB, CW Tone) were black text on nothing: `--accent` was never defined.
- Firefox showed a white default scrollbar beside every scrolling panel on the main page.

---

## [2.33.20] - 2026-09-24

### Changed

- **Activity feed** (Intel > Activity):
  - **Source tiles** above the feed show each source's icon and count in the time window. Click one to hide or show that source.
  - **A timeline** of the whole window: stacked bars per minute, coloured by source, so bursts and quiet periods stand out. Hover a bar for its counts, or click it to show just that period.
  - **Rows** carry the source's icon, and signal strength as a small bar beside the dBm.
  - **A Map** toggle shows the sightings that carry a position (aircraft, vessels, APRS) as dots coloured by source.
  - `GET /observations/histogram` returns the per-source counts over time.

---

## [2.33.19] - 2026-09-24

A polish pass.

### Changed

- **The Wi-Fi radar matches the Bluetooth radar**: the same face (rings, bearing ticks, fading sweep), Strong / Medium / Weak bands instead of Close / Mid / Far, and the same colours. Its bands now use the list's signal thresholds, so a network the list shows as strong sits in the strong ring.
- **BT Locate steers by a signal gauge** instead of an estimated distance. The arc fills with the smoothed signal, the trend beneath reads STRONGER, WEAKER or STEADY, and a tick marks the best reading so far, which shows when the target has been passed. "Confidence: +/- N m" and its map circle, a formula over distance estimates, are replaced by the signal's spread in dB.
- **GPS has a sky plot**: centre overhead, edge the horizon, north up, satellites coloured by constellation and filled when used in the fix, with short trails. It needs neither WebGL nor the internet; the 3D globe is a toggle away.
- **Space weather gauges**: Kp on a 0-9 arc labelled by storm level, solar wind speed on an arc, and Bz as a bar either side of zero labelled by what it means. The X-ray chart marks each flare class once and tints the M and X bands.
- **Empty lists show when they are listening**: an icon and a headline (Listening, with a pulse; Stopped; Couldn't start) above the detail.
- **The Run State strip shows what is running** and the mode in view, with the idle modes folded behind "+N idle". A chip opens its mode.

---

## [2.33.18] - 2026-09-24

### Changed

- **Times follow your browser.** With no time zone chosen in Settings, times read US Eastern. They now use the browser's own zone and clock format. A zone you chose is kept.
- **ESRI World Imagery is the default map.** CARTO's dark tiles now carry an "API KEY REQUIRED" watermark without a key. A map you chose is kept.
- **Locate is obvious and starts at once.** Every Bluetooth device and Wi-Fi network row has a Locate button, and pressing it starts locating; it used to wait for Start to be pressed again.
- **A polished Bluetooth radar**, labelled Strong / Medium / Weak rather than in metres, which signal strength cannot give. Bluetooth rows no longer show a distance for the same reason.

### Fixed

- **Stop could be slow or not stop at all** (pager, 433 MHz, ADS-B). Processes are now stopped together and waited for until gone, so the SDR is free when released. The pager's rtl_fm no longer blocks for two seconds on every stop. If a decoder survives, the page shows it running and points to Kill All; the ADS-B dashboard shows STOPPING while it works.
- **Bluetooth labelled most devices AirTags or Samsung SmartTags.** Samsung's company ID alone counted as a SmartTag, every Apple device away from its owner as an AirTag, and the COVID exposure-notification service as Find My. Apple's Find My status byte now decides between AirTag, AirPods and other accessories, and iPhones, iPads and Macs are not trackers.
- **Space weather was missing solar wind, Kp and flux data.** NOAA retired the solar wind feeds and changed the format of others; the new feeds are read, and flare probabilities show the latest days.
- **Waterfall clicks were tuned one after another.** Clicking faster than the SDR restarts now goes straight to the last frequency.
- **GPS waited silently on a gpsd with no receiver**, as the gpsd package starts one at boot. The detected receiver is now handed to gpsd, or the fix is named.
- **Satellite data downloads were blocked (403).** Every start fetched TLEs from CelesTrak, which blocks addresses that download too often. The schedule now survives restarts: at most daily, and never retried within two hours.

---

## [2.33.17] - 2026-09-24

### Fixed

- **With two Wi-Fi tabs open, each showed only about half the live updates.** The tabs competed for events from one queue. Each tab now receives every event.

### Removed

- **Distance estimates from signal strength.** Signal tooltips and the signal assessment panel showed "Est. range: < 3 meters" and the like, worked out from RSSI alone. A single receiver cannot measure distance this way: the same reading comes from a weak transmitter nearby or a strong one behind a wall.

---

## [2.33.16] - 2026-09-24

### Fixed

- **Alerts, recordings, MQTT and the activity feed depended on how many browser tabs were open.** Each decoded event was processed by every open SSE stream: with two tabs, alerts fired twice and MQTT messages went out twice; with none, nothing was alerted, recorded or published. Events are now processed once, when decoded, whether or not a page is open. This applies to every mode that streams through the shared fan-out, and to ADS-B, Bluetooth and Wi-Fi.

### Changed

- **If you relied on alerts or MQTT only while a page was open,** they now also run with no page open, for as long as the mode is running.

---

## [2.33.15] - 2026-09-24

### Added

- **Past TSCM sweeps are listed** (`GET /tscm/sweeps`) with what each detected, and the survey's sweep step shows them. Any completed sweep can be compared with the active baseline.

### Changed

- **The TSCM client report is a printable page** (`/tscm/report/print`). The "PDF" was plain text, saved by the panel with a `.pdf` name that no PDF reader opens. Print the page, or choose "Save as PDF" as the printer. The text version is at `/tscm/report/text`, and `/tscm/report/pdf` now leads to the printable page.

### Fixed

- **The TSCM report left out the baseline comparison and meeting windows.** The report generator supported both, but no route passed them. The report now compares the sweep with the baseline it ran against and summarises its meeting windows.
- **Meeting windows were compared with device sightings an hour out in summer time** (or by whatever the local UTC offset is), so devices seen during a meeting could be counted as outside it.
- **Drone detection reported "running" with no working source**, and used an RTL-SDR without claiming it. It now claims the SDR, starts only the sources that can run, lists those left out, and fails with the reasons when none can run. The source indicators now light.
- **SubGHz returned 409 Conflict for a missing HackRF tool.** It now returns a 400 naming the tool, with the install command for your platform.
- **Remote agents said "sensor not available (missing tools)"**, or showed a raw `[Errno 2]`. They now name the missing tools, with install advice.
- **TSCM signal descriptions read badly.** For example, "suggest may be ambient noise" and "may indicate indicates likely nearby source" now read "suggest ambient noise or a distant source" and "may indicate a nearby source".
- **AIS dashboard DSC messages** show elapsed time ("3 min ago") like every other list.

---

## [2.33.14] - 2026-09-24

### Added

- **Activity feed** (Intel > Activity). One reverse-chronological stream of sightings from every mode that reports them, colour-coded by source. Filter by source, time window (15 minutes to 24 hours) or a single device; pause to read without losing anything, since new sightings are held and shown on resume. A Bluetooth address the TSCM identity engine has grouped under MAC randomisation shows that grouping, labelled as the engine's judgement. The feed says what the data supports: sightings close together were seen in the same window, nothing more.
- **Observations.** A sighting is recorded as `{ts, source, identifier, entity, rssi, lat, lon, summary, raw}`. Every mode that already passed events through the event pipeline records them with no change of its own; 433 MHz and Wi-Fi record where their data is ingested, so they record whether or not a page is open. `GET /observations` filters by source, identifier (or resolved identity) and time window; `/observations/stream` tails them live; `/observations/stats` shows per-source counts and the policy below.
- **Volume and retention are part of it.** Each device is recorded at most once per interval (15 s for aircraft and vessels, 10 s for Wi-Fi and Bluetooth, 5 s otherwise) and each source at 5 a second after a burst of 20, so a busy ADS-B feed cannot swamp the feed or the table. Writes are batched. Sightings older than 24 hours, and the oldest beyond 100,000, are deleted by the cleanup manager (`INTERCEPT_OBSERVATION_RETENTION_HOURS`, `INTERCEPT_OBSERVATION_MAX_ROWS`).

### Fixed

- **The test suite wrote to the real database.** Tests that did not set up their own now use a temporary one.
- **A WeFax scheduler test failed between about 12:20 and 13:00 UTC**, when its fixed broadcast time had already passed.

---

## [2.33.13] - 2026-09-24

### Added

- **Elapsed time in live lists.** Message, device and station times read "14 s ago" and stay current (one page-wide clock, once a second), with the exact time in your timezone on hover. Ten separate "ago" helpers, most refreshed every 10 or 30 seconds or never, now share one implementation.
- **Empty lists say why they are empty.** Each live list shows, while it has nothing in it, whether its decoder is running and for how long ("rtl_433 running · 0 readings in 4 s"), stopped, or why it last failed to start, naming the missing tool or busy device. Covers the shared feed, the sensor grid, APRS, Wi-Fi, Bluetooth, Meshtastic, OOK, drone, radiosonde, the image galleries, and the ADS-B, ACARS, AIS and DSC dashboard lists.
- **Copy buttons on identifiers**: ICAO hex, MMSI, MAC, BSSID and Meshtastic node ids.
- **Notes and tags on devices.** Attach a note and tags to any observed device; they are kept across restarts and shown wherever the device appears. A note does not mark a device known-good.

### Changed

- **Hidden tabs stop polling.** Clocks, countdowns and status polls pause while the tab is hidden. Measured on the main page in pager mode: 925 → 101 interval callbacks and 39 → 3 requests per minute. Data streams and recording are unaffected.
- **Install advice matches your platform.** Commands come from the dependency map for the package manager actually present, or the project's page when there is none; the 433 MHz mode no longer tells Linux users to use Homebrew.

### Fixed

- **Kill All could stop partway**, if OOK's cleanup failed, on an undefined name. Undefined-name checking is back on in `ruff`.

---

## [2.33.12] - 2026-09-24

A lifecycle contract now runs against every mode: start, stop and start again without restarting iNTERCEPT, with the tool missing, twice in a row, and with garbage on the decoder's output. These are what it found.

### Fixed

- **Stopping Morse or OOK could hang indefinitely.** Both closed the decoder's pipes before terminating it. `close()` waits for a reader thread blocked in `read()`, which only returns when the process exits, so the stop request never completed. The same happened to a Morse start whose first attempt received no samples. The process is now terminated first.
- **A pager start without `multimon-ng` left the SDR marked busy.** The device was claimed before the tool check and not released, so every later start of any mode on it failed with "device busy" until restart.
- **One corrupt byte ended the receiver waterfall.** `rtl_power` output was decoded strictly, and an invalid byte raised inside the read loop. The Bluetooth scanners (`hcitool`, `bluetoothctl`, Ubertooth) had the same weakness, and their output includes device names chosen by whoever owns the device, so any nearby device could stop a scan.
- **Drone detection could leave `rtl_433` running after stop.** A worker still starting when stop ran kept its process, holding the SDR.
- **Meshtastic with no USB device reported itself running**, and with more than one serial port the library called `sys.exit()`. The port is now chosen before connecting, with a clear error for none or several.
- **A missing tool gave a vague 500** in SSTV, general SSTV, WeFax, the receiver's audio and rtlamr ("Failed to start decoder"). They now return 400 naming the tool.
- **ADS-B returned HTTP 200 for a failed start** (device busy, dump1090 exiting), so the history page's start button never showed the failure.
- **Stopping ACARS, VDL2 or APRS when not running returned an error**; it is now a no-op, like every other mode.
- **Weather satellite needed a restart after installing SatDump**; a missing decoder is now checked again.

### Tests

- `tests/test_mode_lifecycle.py` applies the contract to every mode, and to the agent, faking decoders with real OS pipes so reader threads block as they do in production. Modes it cannot cover are listed with the reason.
- The suite now fails if peak memory passes 1 GB; a full run peaks near 350 MB. A 16 GB leak previously went unnoticed for months.

---

## [2.33.11] - 2026-09-24

### Added

- **TSCM Survey workspace** (Intel > TSCM Survey). Most of the TSCM backend had no way in from the UI. One page now walks a survey in the order a practitioner works:
  1. **Baseline:** which baseline is active, when it was captured, its health (with the age and device count behind the score), and the others to activate.
  2. **Sweep:** the latest sweep, what each sweep type covers, and what it found that the baseline did not (new, missing and changed devices).
  3. **Known devices:** every device profiled this session with its score *and the indicators that make it up*, marking devices known or removing them, the known-device registry, and a lookup.
  4. **Threats and findings:** unresolved threats by severity, resolving them with notes, adding them to a case, case notes, high-interest devices with their playbook and timeline, and cross-protocol correlations.
  5. **Report:** the client report and JSON or CSV annexes for the latest sweep, with site, examiner and tiers.

  Sweeps and baseline recording stay in TSCM mode, which owns interface, SDR and agent selection; the workspace links there. Nothing on the page is derived from RSSI beyond the dBm reading itself: no distance, bearing, location or movement.

### Fixed

- **A TSCM baseline's age was wrong outside UTC.** It is stored in UTC and was compared with local time, so in the UK a baseline recorded a moment ago read as an hour old, and baselines aged into "noisy" and "stale" early.
- **Comparing a baseline with a sweep that had not finished failed with a server error.** It now says the sweep has no results yet, rather than reporting every baseline device as missing.

---

## [2.33.10] - 2026-09-24

### Changed

- **The TSCM client report states observations rather than labels derived from them.** Each finding's signal line now gives the measurements, for example `Signal: -48 dBm, observed for 80 minutes (40 sightings)`, in place of `Signal: Strong (Confidence: High)`. The "confidence" encoded only those three numbers but read to a client as confidence that a device was a bug.
- The **Assessment** line describes the pattern (`Pattern consistent with an Apple AirTag`), and the **Interpretation** line ("probable close proximity") is gone: signal strength alone cannot place a device.
- The **Risk Score** line is gone from the client report. It summed unrelated indicator scores, and the indicators themselves are listed under each finding.
- The **overall assessment** states what needs doing (`2 devices require investigation, 2 devices require review.`) instead of HIGH/ELEVATED/MODERATE, which were derived from a count: three trackers in an office read "HIGH, requiring immediate attention".
- The JSON and CSV technical annexes still carry the signal classification, interpretation and risk score for the practitioner.

### Fixed

- **A TSCM sweep that detected nothing was reported as clear.** With zero devices on every band the report read "OVERALL ASSESSMENT: LOW. No significant indicators of surveillance activity were detected", presenting a sweep whose equipment was not receiving as a clean room. It now reads INCONCLUSIVE and says why. An enabled Wi-Fi or Bluetooth band that detected no devices is listed first under the sweep's limitations, with a prompt to check the adapter; a quiet RF band can be genuine and is not flagged.
- **TSCM report findings all read "Signal: Minimal (Confidence: Low)".** The report looked for `rssi_mean`, `observation_count` and `observation_duration_seconds`, none of which a correlation-engine profile carries, so every finding was assessed from no data. It now uses the profile's `rssi_current`, `detection_count` and first/last-seen times.
- **The TSCM CSV annex listed every device as `informational` with risk score 0**, including high-interest ones, because device timelines carry no risk fields. Device rows now take risk level, score and indicators from the device's finding.
- **CSV annex cells could be read as spreadsheet formulas.** Bluetooth names are chosen by whoever owns the device; a name beginning `=`, `+`, `-` or `@` now gets a leading apostrophe so Excel and LibreOffice treat it as text.
- **Generating a TSCM report for a running or aborted sweep failed with a server error** (the sweep has no results yet).
- **TSCM report sweep times were UTC shown as local time**, beside a "Generated" time that really is local, and a running sweep's duration was off by the UTC offset. Stored times are now converted to local time.
- A device profile with a null protocol no longer aborts report generation.
- The generated admin password file is now written beside the database rather than relative to the working directory, so running the test suite no longer overwrites a real `instance/.initial_password` (#288).

---

## [2.33.9] - 2026-09-23

### Added

- **Named SDR devices.** Settings > SDR lists each detected receiver with a display name, which then replaces the hardware name ("Generic RTL2832U", "Device 0") in every device selector. Useful where several dongles each have a dedicated antenna. Names are capped at 48 characters and may not contain control characters or any of `` < > " ' ` ``. Requested by @bob1234uk (#269).
- **Per-device PPM correction, default gain and bias-T.** Set once per receiver, applied on every start of any mode using it: pager, 433 MHz sensors, morse, OOK, ACARS, VDL2, rtlamr, APRS, DSC, radiosonde, waterfall and Meteor, plus ADS-B and AIS, whose RTL-SDR commands now carry the correction (`dump1090 --ppm`, `AIS-catcher -p`). A mode's own field wins when filled in; left blank, the device default applies. The PPM (and pager and sensor gain) fields now start blank for that reason (#238).
- Settings follow the dongle across replugs when it reports a unique serial number. Most RTL-SDRs ship with the factory serial `00000001`; those, and any serial two dongles share, are keyed by USB position instead, and the SDR tab says so. Give a dongle its own serial with `rtl_eeprom -s` to make its settings follow it.
- Remote agents apply the same defaults, read from the agent host's own settings, and now validate PPM and gain before starting a decoder.

### Fixed

- **rtlamr ignored PPM correction and could not connect.** The correction was passed to `rtl_tcp` as `-p`, which is its listen port; it is now `-P`.

---

## [2.33.8] - 2026-09-23

### Security

- **A password you did not choose must now be changed before the interface is usable.** Removing the shipped `admin` default in 2.33.6 closed the vulnerability, but an install seeded with a generated password could still be left on it indefinitely. Accounts seeded that way, and any existing install still using `admin`, are now flagged: login redirects to a change-password page and every other route is blocked until a new password is set. An explicitly configured `INTERCEPT_ADMIN_PASSWORD` is treated as the operator's own choice and does not force a change.

### Added

- **Change Password page** at `/change-password`, reachable whether or not a change is required. Requires the current password, a minimum of 12 characters, and confirmation.

### Fixed

- **Logout now clears the whole session** rather than only the `logged_in` flag, so no state survives into the next session.

---

## [2.33.7] - 2026-09-23

### Added
- **Kill All Processes from any page** — the control previously lived only in the main dashboard's System panel, so it was unavailable from the ADS-B, AIS and satellite dashboards, history, network monitor and agents pages — exactly where you end up when troubleshooting. It is now in the global nav (and the mobile nav) on every page that carries it, styled as a destructive action and confirming before it fires. On the main dashboard it delegates to the existing handler so SDR reservations, run flags and SSE connections are still reset. Requested by @bob1234uk (#274).

---

## [2.33.6] - 2026-09-23

### Security

- **The `/controller/*` API required no authentication.** `app.py`'s global login gate deliberately skipped every path under `/controller/`, on the basis that those routes authenticated callers themselves. They did not: `routes/controller.py` contained no session or credential check of any kind. Anyone able to reach the port could list registered agents (including their API keys), register an agent pointing at any URL, delete agents, and use the command proxy to start or stop SDR modes on remote nodes. The blueprint now authenticates every route, accepting a session or — for agent push only — a valid API key.
- **Agent API keys were returned to clients.** `_row_to_agent()` serialised `api_key`, so every `GET /controller/agents` response carried the shared secret of every configured agent. Responses now carry `has_api_key` instead, and internal callers fetch the secret explicitly via `get_agent_api_key()`.
- **Agent push accepted unauthenticated data for keyless agents.** `/controller/api/ingest` only checked `X-API-Key` when the agent had one configured, so an agent registered without a key accepted a push from anybody. A key is now required, and compared in constant time.
- **WebSocket endpoints skipped authentication.** `/ws/*` was allowed through on the assumption that a page load had already authenticated the client, but a WebSocket client need not load a page, and these endpoints carry live RF and audio data. The session is now verified on the upgrade request.
- **The default admin password `admin` has been removed.** With no `INTERCEPT_ADMIN_PASSWORD` set, first-run now generates a random password, logs it and writes it to `instance/.initial_password` — behaviour that already existed but was unreachable because of the default. Existing installs still using `admin` now log a prominent warning at startup.
- **Path traversal in `/offline/check-asset`.** The handler checked that a path began with `/static/vendor/` but never normalised it, so `/static/vendor/../../../etc/passwd` passed and its existence was reported. It leaked existence only, not contents. The path is now resolved and containment asserted, matching the pattern already used in `routes/recordings.py`.
- **A stale authentication exemption for audio streaming.** `app.py` exempted every path under `/listening/audio/` from the login gate. The blueprint was later renamed to `/receiver`, so the rule matched no route and the audio endpoints were in fact protected — but the exemption remained, ready to reopen a hole the moment a `/listening` route was added back. Removed, with a test asserting no route sits under an exempted prefix.
- **Session cookie and secret key hardening.** `SESSION_COOKIE_HTTPONLY` and `SESSION_COOKIE_SAMESITE=Lax` are now set explicitly rather than inherited from browser defaults, and `SESSION_COOKIE_SECURE` is set when TLS is configured. `instance/secret.key` is created with mode 0600.

### Changed

- **Remote agents must now have an API key to push data.** An agent registered without one will be refused by `/controller/api/ingest`. Set a key on each agent in Settings, and in the agent's own `controller_api_key` configuration.

---

## [2.33.5] - 2026-09-23

### Fixed
- **Meshtastic nodes did not always appear without a page reload** — the map and node list were only refreshed when an incoming packet's portnum contained `POSITION` or `NODEINFO`. A node first heard via any other packet type (telemetry, text message, routing) was recorded in the stats but never fetched into the node list, so it stayed off the map until the page was reloaded. The list now also refreshes the first time a previously unseen node is heard, whatever the packet type, still debounced to avoid repeated fetches. MeshCore was unaffected — it pushes each node over SSE straight to the sidebar and map. Reported by @bob1234uk (#261).

---

## [2.33.4] - 2026-09-23

### Fixed
- **ADS-B aircraft from a remote agent never cleared** — `intercept_agent.py` accumulated every aircraft it heard for the lifetime of the scan, with no expiry, and served the whole set on every dashboard poll. The dashboard stamps each arrival as freshly seen, so its own 60-second expiry could never fire and contacts stayed on the map until the mode was stopped. The agent now drops aircraft not heard within `MAX_AIRCRAFT_AGE_SECONDS` (5 minutes), the same TTL local mode already used, and reports an accurate live count. Local (non-agent) tracking was unaffected. Reported by @bob1234uk (#263).

---

## [2.33.3] - 2026-09-23

### Added
- **433MHz sensor unit selection** — new Units dropdown in the sensor panel, passed to rtl_433's `-C` flag: Metric (°C, km/h, mm), Imperial (°F, mph, in), or Native (whatever each device reports, the previous behaviour). Converting at the decoder rather than in the browser keeps logged and displayed values in agreement and covers wind, rain and pressure as well as temperature. Supported on remote agents too. Requested in #252.

### Changed
- **Sensor units now default to Metric.** Devices that previously reported °F will read °C after this update. Select "Native (as each device reports)" in the sensor panel to restore the old behaviour. The setting applies when the scan starts, so changing it requires stopping and restarting the sensor.

---

## [2.33.2] - 2026-09-23

### Fixed
- **DSC call types and distress labels were mislabelled** — `utils/dsc/constants.py` held a second, unverified copy of the ITU-R M.493 lookup tables that disagreed with the spec-verified tables in `decoder.py`. Format specifiers 112 and 120 were swapped, so a routine individual call was labelled `DISTRESS` and an actual distress alert was labelled `INDIVIDUAL` in the VHF DSC monitor. Nature-of-distress codes were shifted one position against the spec, mislabelling every nature (a sinking read as "listing", man overboard read as "piracy"). The telecommand table also mapped 111 to `TEST` where the spec assigns 118. All three tables now match the spec, unverified telecommand codes report `UNKNOWN (<code>)` rather than guessing, and the tests assert exact values instead of key membership. Reported by @jimarndt (#244).

---

## [2.33.1] - 2026-09-23

### Fixed
- **ACARS SIGILL in the amd64 Docker image** — `acarsdec` upstream hardcodes `-Ofast -march=native`, so the published amd64 binary was compiled for the build runner's CPU and contained AVX-512 instructions. Starting ACARS on any CPU without AVX-512 (e.g. Core i5-8500) crashed immediately with `Illegal instruction`. The container build now strips `-march=native` while keeping `-Ofast`. Same fix class as the SatDump build (#185); ARM64 images were unaffected. Thanks to @mitchross for the diagnosis (#246).

---

## [2.33.0] - 2026-08-31

### Added
- **CARTO API key setting** — CARTO now watermarks raster tile requests that don't include an API key. Settings > Map Tiles has a new CARTO API Key field (shown whenever a CartoDB provider is selected) that appends a free key (no account needed, from carto.com/basemaps/apikey) to tile requests to remove the watermark.

### Fixed
- **CartoDB watermark flash on mode maps** — Drone, SSTV/ISS, MeshCore, Meshtastic, WebSDR, and Weather Satellite maps painted an instant, hardcoded, unkeyed CartoDB tile layer before upgrading to the configured provider. If Settings was already initialized elsewhere in the session, they now use the real configured (and potentially keyed) tile layer immediately instead.

## [2.32.0] - 2026-07-07

### Added
- **ADS-B historical playback** — new Playback tab on the ADS-B history page. Loads time-bucketed snapshots from the Postgres history database and animates aircraft positions on a Leaflet map. Controls: configurable time window (15 min – 24 h), bucket step (10 s – 5 min), play/pause, speed multiplier (1×–20×), and a time scrubber. Requires the `history` Docker profile or `INTERCEPT_ADSB_HISTORY_ENABLED=true`. Backed by new `GET /adsb/history/playback` endpoint.

---

## [2.31.0] - 2026-07-07

### Added
- **BladeRF support** — bladeRF 2.0 micro (47 MHz – 6 GHz) and bladeRF x40/x115 (300 MHz – 3.8 GHz) detected and usable via SoapyBladeRF. TX capable. Supports FM demod, ADS-B, ISM, AIS, and I/Q capture.
- **HydraSDR RFOne support** — Detected and usable via SoapyHydraSDR (24 MHz – 1800 MHz, RX only, 10 MHz instantaneous bandwidth). Supports FM demod, ADS-B, ISM, AIS, and I/Q capture.

---

## [2.30.0] - 2026-07-07

### Added
- **APRS station export** — `GET /aprs/export?format=json|csv` downloads all currently tracked APRS stations, consistent with the existing WiFi/Bluetooth/AIS export pattern.
- **USRP support** — Ettus USRP devices (N200, B200, B210) are now detected and usable via the SoapyUHD bridge. FM demod, ADS-B, ISM, AIS, and I/Q capture all supported through the existing SoapySDR toolchain.
- **MQTT data export** — Optional MQTT publisher that broadcasts decoded events from every module to a configurable broker. Disabled by default; set `INTERCEPT_MQTT_BROKER` to enable. Topics follow the pattern `<prefix>/<module>/<event_type>` (e.g. `intercept/aprs/packet`). Configure with `INTERCEPT_MQTT_PORT`, `INTERCEPT_MQTT_USER`, `INTERCEPT_MQTT_PASSWORD`, `INTERCEPT_MQTT_TOPIC_PREFIX`, `INTERCEPT_MQTT_RETAIN`.

---

## [2.29.0] - 2026-07-05

### Added
- **TSCM sweep metadata** — Site/Location and Examiner name fields on the sweep config, embedded in the HTML and PDF/annex reports.
- **Mark Cleared** — dim and badge a device as cleared directly from the live sweep view; cleared devices are excluded from generated reports, and the executive summary shows a cleared-device count. Resets at the start of each new sweep.
- **Examiner Ignore List** — persist your own devices (phone, laptop, etc.) in `localStorage` so they're filtered from the live display and every report export. Sidebar section lists current entries with per-item removal and a clear-all button.
- **TSCM report category filter** — choose which risk categories (High Interest, Needs Review, Informational) to include in generated reports, across the HTML report, PDF, JSON annex, and CSV annex. Defaults to High Interest + Needs Review.

### Fixed
- **ADS-B history Postgres password ignored** — the local setup wizard wrote the database password under the wrong `.env` key (`INTERCEPT_ADSB_DB_PASS` instead of `INTERCEPT_ADSB_DB_PASSWORD`), so a custom password set during setup was silently dropped and the app fell back to the default. Docker Compose also hardcoded the Postgres password instead of reading it from `.env`. Both now correctly honor `INTERCEPT_ADSB_DB_PASSWORD`.

---

## [2.28.0] - 2026-07-05

### Added
- **Signal ID** — Offline signal identification against a bundled 594-signal database (seeded from SigID Wiki via Artemis-DB). Match an unknown signal by frequency, bandwidth, and modulation; results are ranked 0–100 with match reasons and SigID Wiki links.
- **Signal ID modal** — Standalone overlay (`SignalIdModal`) accessible from a new "Signal ID" button in the global nav Intel group, and from a dedicated "Identify Signal" button in the waterfall sidebar. Pre-populates from the current waterfall frequency when opened from there; accepts manual entry from the nav.
- **`POST /signalid/match` route** — Scored matching API with 60-second in-process cache. Scores frequency centrality (40 pts), bandwidth match (30 pts), modulation match (20 pts), and region match (10 pts). Returns ranked matches with `match_reasons` annotations.
- **`bin/import_artemis.py`** — One-command database refresh script. Downloads the latest Artemis-DB tar (~300 MB), extracts it, and merges new signals into `data/signals.json`. Run with `python3 bin/import_artemis.py --download`.

---

## [2.27.0] - 2026-05-20

### Fixed
- **Two-window hang** — Opening the app in two browser tabs/windows caused it to become completely unresponsive. Root cause: HTTP/1.1 limits browsers to 6 connections per origin (shared across all tabs). VoiceAlerts was automatically opening 3 SSE streams per window on page load, so two windows produced 8 persistent connections and permanently blocked all regular HTTP requests. VoiceAlerts streams are now opt-in (disabled by default); users can enable them in settings.
- **Alert messages split between windows** — The `/alerts/stream` SSE endpoint read from a single queue, so two windows would each receive only half the alerts. Now uses `sse_stream_fanout` so every window gets every alert.
- **Bluetooth v2 stream split between windows** — Same single-queue issue in `/api/bluetooth/stream`. Fixed with fanout via `subscribe_fanout_queue`, preserving named SSE events (`device_update`, `scan_started`, etc.).
- **ICAO lookup cache unbounded growth** — `_looked_up_icaos` set was never evicted; capped at 50 000 entries with LRU eviction to prevent memory growth under sustained ADS-B load.
- **Concurrent ICAO clear race** — `popitem()` on the ICAO dict could raise `RuntimeError` if a clear happened concurrently; guarded with try/except.
- **Bluetooth tracker fingerprint stability** — Tracker signature scan was incorrectly resetting stability counters on unchanged payloads; now skips the scan when the BLE payload fingerprint is unchanged.

### Added
- **UI Tier system** — Three display modes selectable from the nav bar: *Lean* (minimal, no decorative elements), *Standard* (default), and *Enhanced* (full animations and ambient effects). Replaces the old animations toggle.
- **Display mode in first-run setup** — The first-run modal now includes a display mode selection step so new users can pick their preferred visual style during initial setup.

### Performance
- ADS-B SSE snapshot priming moved inside the response generator (avoids blocking before headers are sent).
- WiFi network filter combined into a single list pass instead of chained filters.
- Bluetooth tracker signature scan skips processing when the BLE payload fingerprint is unchanged.
- `DataStore` cleanup minimises lock hold time by collecting expired keys before acquiring the write lock.

---

## [2.26.11] - 2026-03-14

### Fixed
- **APRS map ignores configured observer position** — The APRS map always fell back to the centre of the US (39.8°N, 98.6°W) when no live GPS fix was available, ignoring the observer position configured in `.env` (`INTERCEPT_DEFAULT_LAT` / `INTERCEPT_DEFAULT_LON`). Now seeds the APRS user location from the shared observer location on page load, so the map centres correctly and distance calculations work. (#193)

---

## [2.26.10] - 2026-03-14

### Fixed
- **APRS stop timeout and inverted SDR device status** — The APRS stop endpoint terminated two processes sequentially (up to 4s) while the frontend timed out at 2.2s, causing console errors and the SDR status panel to show stale state (active after stop, idle during use). Now releases the SDR device immediately and terminates processes in a background thread so the response returns instantly. (#194)

---

## [2.26.9] - 2026-03-14

### Fixed
- **ADS-B bias-t support for RTL-SDR Blog V4** — When dump1090 lacks native `--enable-biast` support, the system now falls back to `rtl_biast` (from RTL-SDR Blog drivers) to enable bias-t power before starting dump1090. The Blog V4's built-in LNA requires bias-t to receive ADS-B signals. (#195)

---

## [2.26.8] - 2026-03-14

### Fixed
- **acarsdec build failure on macOS** — `HOST_NAME_MAX` is Linux-specific (`<limits.h>`) and undefined on macOS, causing 3 compile errors in `acarsdec.c`. Now patched with `#define HOST_NAME_MAX 255` before building. Also fixed deprecated `-Ofast` flag warning on all macOS architectures (was only patched for arm64). (#187)

---

## [2.26.7] - 2026-03-14

### Fixed
- **Health check SDR detection on macOS** — `timeout` (GNU coreutils) is not available on macOS, causing `rtl_test` to silently fail and report "No RTL-SDR device found" even when one is connected. Now tries `timeout`, then `gtimeout` (Homebrew coreutils), then falls back to a background process with manual kill. (#188)

---

## [2.26.6] - 2026-03-14

### Fixed
- **Oversized branded 'i' logo on dashboards** — `.logo span { display: inline }` in dashboard CSS had higher specificity (0,1,1) than `.brand-i { display: inline-block }` (0,1,0), forcing the branded "i" SVG to render as inline which ignores width/height. Added `.logo .brand-i` selector (0,2,0) to retain `inline-block` display. (#189)

---

## [2.26.5] - 2026-03-14

### Fixed
- **Database errors crash entire UI** — `get_setting()` now catches `sqlite3.OperationalError` and returns the default value instead of propagating the exception. Previously, if the database was inaccessible (e.g. root-owned `instance/` directory from running with `sudo`), the `inject_offline_settings` context processor would crash every page render with a 500 Internal Server Error. (#190)

---

## [2.26.4] - 2026-03-14

### Fixed
- **Environment Configurator crash** — `read_env_var()` crashed with "Setup failed at line 2333" when `.env` existed but didn't contain the variable being looked up. `grep` returned exit code 1 (no match), which `pipefail` propagated and `set -e` turned into a fatal error. Fixed by appending `|| true` to the pipeline. (#191)

---

## [2.26.3] - 2026-03-13

### Fixed
- **SatDump AVX2 crash** — SatDump now compiles with `-march=x86-64` on x86_64 platforms (Docker and `setup.sh`), preventing "Illegal instruction" crashes on CPUs without AVX2. SIMD plugins still use runtime detection for acceleration on capable hardware. (#185)

---

## [2.26.2] - 2026-03-13

### Fixed
- **Docker startup crash** — `.dockerignore` excluded the entire `data/` directory, which is now a Python package (`data.oui`, `data.patterns`, `data.satellites`). Caused `ModuleNotFoundError: No module named 'data.oui'` on container startup. Fixed by only excluding non-code files from `data/`.

---

## [2.26.1] - 2026-03-13

### Fixed
- **Default admin credentials** — Default `ADMIN_PASSWORD` changed from empty string to `admin`, matching the README documentation (`admin:admin`)
- **Config credential sync** — Admin password changes in `config.py` or via `INTERCEPT_ADMIN_PASSWORD` env var now sync to the database on restart, without needing to delete the DB

---

## [2.26.0] - 2026-03-13

### Fixed
- **SSE fanout crash** - `_run_fanout` daemon thread no longer crashes with `AttributeError: 'NoneType' object has no attribute 'get'` when source queue becomes None during interpreter shutdown
- **Branded logo FOUC** - Added inline `width`/`height` to branded "i" SVG elements across 10 templates to prevent oversized rendering before CSS loads; refresh no longer needed

---

## [2.25.0] - 2026-03-12

### Added
- **SSEManager** - Centralized SSE connection management with exponential backoff reconnection and visual connection status indicator
- **Loading button states** - `withLoadingButton()` utility for async action buttons across all modes
- **Actionable error reporting** - `reportActionableError()` added to 5 mode JS files for user-friendly error messages
- **Destructive action confirmation modals** - Custom modal system replacing 25 native `confirm()` calls

### Changed
- **Accessibility improvements** - aria-labels on interactive elements, form label associations, keyboard-navigable lists
- **CSS variable adoption** - Replaced hardcoded hex colors with CSS custom properties across 16+ files
- **Inline style extraction** - `classList.toggle()` replaces inline `display` manipulation throughout codebase
- **Merged `global-nav.css` into `layout.css`** - Consolidated navigation styles
- **Reduced `!important` usage** - Responsive.css `!important` count reduced from 71 to 8
- **Standardized breakpoints** - Unified to 480/768/1024/1280px across all responsive styles
- **Mobile UX polish** - Improved touch targets, code overflow handling, and responsive layouts

### Fixed
- Deep-linked mode scripts now wait for body parse before executing, preventing initialization failures

---

## [2.24.0] - 2026-03-10

### Added
- **WiFi Locate Mode** - Locate WiFi access points by BSSID with real-time signal meter, distance estimation, RSSI chart, and audio proximity tones. Hand-off from WiFi detail drawer, environment presets (Free Space/Outdoor/Indoor), and signal-lost detection.

### Changed
- Mobile navigation bar reorganized into labeled groups (SIG, TRK, SPC, WIFI, INTEL, SYS) for better usability
- flask-limiter made optional — rate limiting degrades gracefully if package is missing

### Fixed
- Radiosonde setup missing `semver` Python dependency — `setup.sh` now explicitly installs it alongside `requirements.txt`

## [2.23.0] - 2026-02-27

### Added
- **Radiosonde Weather Balloon Tracking** - 400-406 MHz reception via radiosonde_auto_rx with telemetry, map, and station distance tracking
- **CW/Morse Code Decoder** - Custom Goertzel tone detection with OOK/AM envelope detection mode for ISM bands
- **WeFax (Weather Fax) Decoder** - HF weather fax reception with auto-scheduler, broadcast timeline, and image gallery
- **System Health Monitoring** - Telemetry dashboard with process monitoring and system metrics
- **HTTPS Support** - TLS via `INTERCEPT_HTTPS` configuration
- **ADS-B Voice Alerts** - Text-to-speech notifications for military and emergency aircraft detections
- **HackRF TSCM RF Scan** - HackRF support added to TSCM counter-surveillance RF sweep
- **Multi-SDR WeFax** - Multiple SDR hardware support for WeFax decoder
- **Tool Path Overrides** - `INTERCEPT_*_PATH` environment variables for custom tool locations
- **Homebrew Tool Detection** - Native path detection for Apple Silicon Homebrew installations
- **Production Server** - `start.sh` with gunicorn + gevent for concurrent SSE/WebSocket handling — eliminates multi-client page load delays

### Changed
- Morse decoder rebuilt with custom Goertzel decoder, replacing multimon-ng dependency
- GPS mode upgraded to textured 3D globe visualization
- Destroy lifecycle added to all mode modules to prevent resource leaks
- Docker container now uses gunicorn + gevent by default via `start.sh`

### Fixed
- ADS-B device release leak and startup performance regression
- ADS-B probe incorrectly treating "No devices found" as success
- USB claim race condition after SDR probe
- SDR device registry collision when multiple SDR types present
- APRS 15-minute startup delay caused by pipe buffering
- APRS map centering at [0,0] when GPS unavailable
- DSC decoder ITU-R M.493 compliance issues
- Weather satellite 0dB SNR — increased sample rate for Meteor LRPT
- SSE fanout backlog causing delayed updates across all modes
- SSE reconnect packet loss during client reconnection
- Waterfall monitor tuning race conditions
- Mode FOUC (flash of unstyled content) on initial navigation
- Various Morse decoder stability and lifecycle fixes

---

## [2.22.3] - 2026-02-23

### Fixed
- Waterfall control panel rendered as unstyled text for up to 20 seconds on first visit — CSS is now loaded eagerly with the rest of the page assets
- WebSDR globe failed to render on first page load — initialization now waits for a layout frame before mounting the WebGL renderer, ensuring the container has non-zero dimensions
- Waterfall monitor audio took minutes to start — `_waitForPlayback` now only reports success on actual audio playback (`playing`/`timeupdate`), not from the WAV header alone (`loadeddata`/`canplay`)
- Waterfall monitor could not be stopped — `stopMonitor()` now pauses audio and updates the UI immediately instead of waiting for the backend stop request (which blocked for 1+ seconds during SDR process cleanup)
- Stopping the waterfall no longer shows a stale "WebSocket closed before ready" message — the `onclose` handler now detects intentional closes

---

## [2.22.1] - 2026-02-23

### Fixed
- PWA install prompt not appearing — manifest now includes required PNG icons (192×192, 512×512)
- Apple touch icon updated to PNG for iOS Safari compatibility
- Service worker cache bumped to bust stale cached assets

---

## [2.22.0] - 2026-02-23

### Added
- **Waterfall Receiver Overhaul** - WebSocket-based I/Q streaming with server-side FFT, click-to-tune, zoom controls, and auto-scaling
- **Voice Alerts** - Configurable text-to-speech event notifications across modes
- **Signal Fingerprinting** - RF device identification and pattern analysis mode
- **SignalID** - Automatic signal classification via SigIDWiki API integration
- **PWA Support** - Installable web app with service worker caching and manifest
- **Real-time Signal Scope** - Live signal visualization for pager, sensor, and SSTV modes
- **ADS-B MSG2 Surface Parsing** - Ground vehicle movement tracking from MSG2 frames
- **Cheat Sheets** - Quick reference overlays for keyboard shortcuts and mode controls
- App icon (SVG) for PWA and browser tab

### Changed
- **WebSDR overhaul** - Improved receiver management, audio streaming, and UI
- **Mode stop responsiveness** - Faster timeout handling and improved WiFi/Bluetooth scanner shutdown
- **Mode transitions** - Smoother navigation with performance instrumentation
- **BT Locate** - Refactored JS engine with improved trail management and signal smoothing
- **Listening Post** - Refactored with cross-module frequency routing
- **SSTV decoder** - State machine improvements and partial image streaming
- Analytics mode removed; per-mode analytics panels integrated into existing dashboards

### Fixed
- ADS-B SSE multi-client fanout stability and update flush timing
- WiFi scanner robustness and monitor mode teardown reliability
- Agent client reliability improvements for remote sensor nodes
- SSTV VIS detector state reporting in signal monitor diagnostics

### Documentation
- Complete documentation audit across README, FEATURES, USAGE, help modal, and GitHub Pages
- Fixed license badge (MIT → Apache 2.0) to match actual LICENSE file
- Fixed tool name `rtl_amr` → `rtlamr` throughout all docs
- Fixed incorrect entry point examples (`python app.py` → `sudo -E venv/bin/python intercept.py`)
- Removed duplicate AIS Vessel Tracking section from FEATURES.md
- Updated SSTV requirements: pure Python decoder, no external `slowrx` needed
- Added ACARS and VDL2 mode descriptions to in-app help modal
- GitHub Pages site: corrected Docker command, license, and tool name references

---

## [2.21.1] - 2026-02-20

### Fixed
- BT Locate map first-load rendering race that could cause blank/late map initialization
- BT Locate mode switch timing so Leaflet invalidation runs after panel visibility settles
- BT Locate trail restore startup latency by batching historical GPS point rendering

---

## [2.21.0] - 2026-02-20

### Added
- Analytics panels for operational insights and temporal pattern analysis

### Changed
- Global map theme refresh with improved contrast and cross-dashboard consistency
- Cross-app UX refinements for accessibility, mode consistency, and render performance
- BT Locate enhancements including improved continuity, smoothing, and confidence reporting

### Fixed
- Weather satellite auto-scheduler and Mercator tracking reliability issues
- Bluetooth/WiFi runtime health issues affecting scanner continuity
- ADS-B SSE multi-client fanout stability and remote VDL2 streaming reliability

---

## [2.15.0] - 2026-02-09

### Added
- **Real-time WebSocket Waterfall** - I/Q capture with server-side FFT
  - Click-to-tune, zoom controls, and auto-scaling quantization
  - Shared waterfall UI across SDR modes with function bar controls
  - WebSocket frame serialization and connection reuse
- **Cross-Module Frequency Routing** - Tune from Listening Post directly to decoders
- **Pure Python SSTV Decoder** - Replaces broken slowrx C dependency
  - Real-time decode progress with partial image streaming
  - VIS detector state in signal monitor diagnostics
  - Image gallery with delete and download functionality
- **Real-time Signal Scope** - Live signal visualization for pager, sensor, and SSTV modes
- **SSTV Image Gallery** - Delete and download decoded images
- **USB Device Probe** - Detect broken SDR devices before rtl_fm crashes

### Fixed
- DMR dsd-fme protocol flags, device label, and tuning controls
- DMR frontend/backend state desync causing 409 on start
- Digital voice decoder producing no output due to wrong dsd-fme flags
- SDR device lock-up from unreleased device registry on process crash
- APRS crash on large station count and station list overflow
- Settings modal overflowing viewport on smaller screens
- Waterfall crash on zoom by reusing WebSocket and adding USB release retry
- PD120 SSTV decode hang and false leader tone detection
- WebSocket waterfall blocked by login redirect
- TSCM sweep KeyError on RiskLevel.NEEDS_REVIEW

### Removed
- GSM Spy functionality removed for legal compliance

---

## [2.14.0] - 2026-02-06

### Added
- **DMR Digital Voice Decoder** - Decode DMR, P25, NXDN, and D-STAR protocols
  - Integration with dsd-fme (Digital Speech Decoder - Florida Man Edition)
  - Real-time SSE streaming of sync, call, voice, and slot events
  - Call history table with talkgroup, source ID, and protocol tracking
  - Protocol auto-detection or manual selection
  - Pipeline error diagnostics with rtl_fm stderr capture
- **DMR Visual Synthesizer** - Canvas-based signal activity visualization
  - Spring-physics animated bars reacting to SSE decoder events
  - Color-coded by event type: cyan (sync), green (call), orange (voice)
  - Center-outward ripple bursts on sync events
  - Smooth decay and idle breathing animation
  - Responsive canvas with window resize handling
- **HF SSTV General Mode** - Terrestrial slow-scan TV on shortwave frequencies
  - Predefined HF SSTV frequencies (14.230, 21.340, 28.680 MHz, etc.)
  - Modulation support for USB/LSB reception
- **WebSDR Integration** - Remote HF/shortwave listening via WebSDR servers
- **Listening Post Enhancements** - Improved signal scanner and audio handling

### Fixed
- APRS rtl_fm startup failure and SDR device conflicts
- DSD voice decoder detection for dsd-fme and PulseAudio errors
- dsd-fme protocol flags and ncurses disable for headless operation
- dsd-fme audio output flag for pipeline compatibility
- TSCM sweep scan resilience with per-device error isolation
- TSCM WiFi detection using scanner singleton for device availability
- TSCM correlation and cluster emission fixes
- Detected Threats panel items now clickable to show device details
- Proximity radar tooltip flicker on hover
- Radar blip flicker by deferring renders during hover
- ISS position API priority swap to avoid timeout delays
- Updater settings panel error when updater.js is blocked
- Missing scapy in optionals dependency group

---

## [2.13.1] - 2026-02-04

### Added
- **UI Overhaul** - Revamped styling with slate/cyan theme
  - Switched app font to JetBrains Mono
  - Global navigation bar across all dashboards
  - Cyan-tinted map tiles as default
- **Signal Scanner Rewrite** - Switched to rtl_power sweep for better coverage
  - SNR column added to signal hits table
  - SNR threshold control for power scan
  - Improved sweep progress tracking and stability
  - Frequency-based sweep display with range syncing
- **Listening Post Audio** - WAV streaming with retry and fallback
  - WebSocket audio fallback for listening
  - User-initiated audio play prompt
  - Audio pipeline restart for fresh stream headers

### Fixed
- WiFi connected clients panel now filters to selected AP instead of showing all clients
- USB device contention when starting audio pipeline
- Dual scrollbar issue on main dashboard
- Controls bar alignment in dashboard pages
- Mode query routing from dashboard nav

---

## [2.13.0] - 2026-02-04

### Added
- **WiFi Client Display** - Connected clients shown in AP detail drawer
  - Real-time client updates via SSE streaming
  - Probed SSID badges for connected clients
  - Signal strength indicators and vendor identification
- **Help Modal** - Keyboard shortcuts reference system
- **Main Dashboard Button** - Quick navigation from any page
- **Settings Modal** - Accessible from all dashboards

### Changed
- Dashboard CSS improvements and consistency fixes

---

## [2.12.1] - 2026-02-02

### Added
- **SDR Device Registry** - Prevents decoder conflicts between concurrent modes
- **SDR Device Status Panel** - Shows connected SDR devices with ADS-B Bias-T toggle
- **Real-time Doppler Tracking** - ISS SSTV reception with Doppler correction
- **TCP Connection Support** - Meshtastic devices connectable over TCP
- **Shared Observer Location** - Configurable shared location with auto-start options
- **slowrx Source Build** - Fallback build for Debian/Ubuntu

### Fixed
- SDR device type not synced on page refresh
- Meshtastic connection type not restored on page refresh
- WiFi deep scan polling on agent with normalized scan_type value
- Auto-detect RTL-SDR drivers and blacklist instead of prompting
- TPMS pressure field mappings for 433MHz sensor display
- Agent capabilities cache invalidation after monitor mode toggle

---

## [2.12.0] - 2026-01-29

### Added
- **ISS SSTV Decoder Mode** - Receive Slow Scan Television transmissions from the ISS
  - Real-time ISS tracking globe with accurate position via N2YO API
  - Leaflet world map showing ISS ground track and current position
  - Location settings for ISS pass predictions
  - Integration with satellite tracking TLE data
- **GitHub Update Notifications** - Automatic new version alerts
  - Checks for updates on app startup
  - Unobtrusive notification when new releases are available
  - Configurable check interval via settings
- **Meshtastic Enhancements**
  - QR code support for easy device sharing
  - Telemetry display with battery, voltage, and environmental data
  - Traceroute visualization for mesh network topology
  - Improved node synchronization between map and top bar
- **UI Improvements**
  - New Space category for satellite and ISS-related modes
  - Pulsating ring effect for tracked aircraft/vessels
  - Map marker highlighting for selected aircraft in ADS-B
  - Consolidated settings and dependencies into single modal
- **Auto-Update TLE Data** - Satellite tracking data updates automatically on app startup
- **GPS Auto-Connect** - AIS dashboard now connects to gpsd automatically

### Changed
- **Utility Meters** - Added device grouping by ID with consumption trends
- **Utility Meters** - Device intelligence and manufacturer information display

### Fixed
- **SoapySDR** - Module detection on macOS with Homebrew
- **dump1090** - Build failures in Docker containers
- **dump1090** - Build failures on Kali Linux and newer GCC versions
- **Flask** - Ensure Flask 3.0+ compatibility in setup script
- **psycopg2** - Now optional for Flask/Werkzeug compatibility
- **Bias-T** - Setting now properly passed to ADS-B and AIS dashboards
- **Dark Mode Maps** - Removed CSS filter that was inverting dark tiles
- **Map Tiles** - Fixed CARTO tile URLs and added cache-busting
- **Meshtastic** - Traceroute button and dark mode map fixes
- **ADS-B Dashboard** - Height adjustment to prevent bottom controls cutoff
- **Audio Visualizer** - Now works without spectrum canvas

---

## [2.11.0] - 2026-01-28

### Added
- **Meshtastic Mesh Network Integration** - LoRa mesh communication support
  - Connect to Meshtastic devices (Heltec, T-Beam, RAK) via USB/Serial
  - Real-time message streaming via SSE
  - Channel configuration with encryption key support
  - Node information display with signal metrics (RSSI, SNR)
  - Message history with up to 500 messages
- **Ubertooth One BLE Scanner** - Advanced Bluetooth scanning
  - Passive BLE packet capture across all 40 BLE channels
  - Raw advertising payload access
  - Integration with existing Bluetooth scanning modes
  - Automatic detection of Ubertooth hardware
- **Offline Mode** - Run iNTERCEPT without internet connectivity
  - Bundled Leaflet 1.9.4 (JS, CSS, marker images)
  - Bundled Chart.js 4.4.1
  - Bundled Inter and JetBrains Mono fonts (woff2)
  - Local asset status checking and validation
- **Settings Modal** - New configuration interface accessible from navigation
  - Offline tab: Toggle offline mode, configure asset sources
  - Display tab: Theme and animation preferences
  - About tab: Version info and links
- **Multiple Map Tile Providers** - Choose from:
  - OpenStreetMap (default)
  - CartoDB Dark
  - CartoDB Positron (light)
  - ESRI World Imagery
  - Custom tile server URL

### Changed
- **Dashboard Templates** - Conditional asset loading based on offline settings
- **Bluetooth Scanner** - Added Ubertooth backend alongside BlueZ/DBus
- **Dependencies** - Added meshtastic SDK to requirements.txt

### Technical
- Added `routes/meshtastic.py` for Meshtastic API endpoints
- Added `utils/meshtastic.py` for device management
- Added `utils/bluetooth/ubertooth_scanner.py` for Ubertooth support
- Added `routes/offline.py` for offline mode API
- Added `static/js/core/settings-manager.js` for client-side settings
- Added `static/css/settings.css` for settings modal styles
- Added `static/css/modes/meshtastic.css` for Meshtastic UI
- Added `static/js/modes/meshtastic.js` for Meshtastic frontend
- Added `templates/partials/modes/meshtastic.html` for Meshtastic mode
- Added `templates/partials/settings-modal.html` for settings UI
- Added `static/vendor/` directory structure for bundled assets

---

## [2.10.0] - 2026-01-25

### Added
- **AIS Vessel Tracking** - Real-time ship tracking via AIS-catcher
  - Full-screen dashboard with interactive maritime map
  - Vessel details: name, MMSI, callsign, destination, ETA
  - Navigation data: speed, course, heading, rate of turn
  - Ship type classification and dimensions
  - Multi-SDR support (RTL-SDR, HackRF, LimeSDR, Airspy, SDRplay)
- **VHF DSC Channel 70 Monitoring** - Digital Selective Calling for maritime distress
  - Real-time decoding of DSC messages (Distress, Urgency, Safety, Routine)
  - MMSI country identification via Maritime Identification Digits (MID) lookup
  - Position extraction and map markers for distress alerts
  - Prominent visual overlay for DISTRESS and URGENCY alerts
  - Permanent database storage for critical alerts with acknowledgement workflow
- **Spy Stations Database** - Number stations and diplomatic HF networks
  - Comprehensive database from priyom.org
  - Station profiles with frequencies, schedules, operators
  - Filter by type (number/diplomatic), country, and mode
  - Tune integration with Listening Post
  - Famous stations: UVB-76, Cuban HM01, Israeli E17z
- **SDR Device Conflict Detection** - Prevents collisions between AIS and DSC
- **DSC Alert Summary** - Dashboard counts for unacknowledged distress/urgency alerts
- **AIS-catcher Installation** - Added to setup.sh for Debian and macOS

### Changed
- **UI Labels** - Renamed "Scanner" to "Listening Post" and "RTLAMR" to "Meters"
- **Pager Filter** - Changed from onchange to oninput for real-time filtering
- **Vessels Dashboard** - Now includes VHF DSC message panel alongside AIS tracking
- **Dependencies** - Added scipy and numpy for DSC signal processing

### Fixed
- **DSC Position Decoder** - Corrected octal literal in quadrant check

---

## [2.9.5] - 2026-01-14

### Added
- **MAC-Randomization Resistant Detection** - TSCM now identifies devices using randomized MAC addresses
- **Clickable Score Cards** - Click on threat scores to see detailed findings
- **Device Detail Expansion** - Click-to-expand device details in TSCM results
- **Root Privilege Check** - Warning display when running without required privileges
- **Real-time Device Streaming** - Devices stream to dashboard during TSCM sweep

### Changed
- **TSCM Correlation Engine** - Improved device correlation with comprehensive reporting
- **Device Classification System** - Enhanced threat classification and scoring
- **WiFi Scanning** - Improved scanning reliability and device naming

### Fixed
- **RF Scanning** - Fixed scanning issues with improved status feedback
- **TSCM Modal Readability** - Improved modal styling and close button visibility
- **Linux Device Detection** - Added more fallback methods for device detection
- **macOS Device Detection** - Fixed TSCM device detection on macOS
- **Bluetooth Event Type** - Fixed device type being overwritten
- **rtl_433 Bias-T Flag** - Corrected bias-t flag handling

---

## [2.9.0] - 2026-01-10

### Added
- **Landing Page** - Animated welcome screen with logo reveal and "See the Invisible" tagline
- **New Branding** - Redesigned logo featuring 'i' with signal wave brackets
- **Logo Assets** - Full-size SVG logos in `/static/img/` for external use
- **Instagram Promo** - Animated HTML promo video template in `/promo/` directory
- **Listening Post Scanner** - Fully functional frequency scanning with signal detection
  - Scan button toggles between start/stop states
  - Signal hits logged with Listen button to tune directly
  - Proper 4-column display (Time, Frequency, Modulation, Action)

### Changed
- **Rebranding** - Application renamed from "INTERCEPT" to "iNTERCEPT"
- **Updated Tagline** - "Signal Intelligence & Counter Surveillance Platform"
- **Setup Script** - Now installs Python packages via apt first (more reliable on Debian/Ubuntu)
  - Uses `--system-site-packages` for venv to leverage apt packages
  - Added fallback logic when pip fails
- **Troubleshooting Docs** - Added sections for pip install issues and apt alternatives

### Fixed
- **Tuning Dial Audio** - Fixed audio stopping when using tuning knob
  - Added restart prevention flags to avoid overlapping restarts
  - Increased debounce time for smoother operation
  - Added silent mode for programmatic value changes
- **Scanner Signal Hits** - Fixed table column count and colspan
- **Favicon** - Updated to new 'i' logo design

---

## [2.0.0] - 2026-01-06

### Added
- **Listening Post Mode** - New frequency scanner with automatic signal detection
  - Scans frequency ranges and stops on detected signals
  - Real-time audio monitoring with ffmpeg integration
  - Skip button to continue scanning after signal detection
  - Configurable dwell time, squelch, and step size
  - Preset frequency bands (FM broadcast, Air band, Marine, etc.)
  - Activity log of detected signals
- **Aircraft Dashboard Improvements**
  - Dependency warning when rtl_fm or ffmpeg not installed
  - Auto-restart audio when switching frequencies
  - Fixed toolbar overflow with custom frequency input
- **Device Correlation** - Match WiFi and Bluetooth devices by manufacturer
- **Settings System** - SQLite-based persistent settings storage
- **Comprehensive Test Suite** - Added tests for routes, validation, correlation, database

### Changed
- **Documentation Overhaul**
  - Simplified README with clear macOS and Debian installation steps
  - Added Docker installation option
  - Complete tool reference table in HARDWARE.md
  - Removed redundant/confusing content
- **Setup Script Rewrite**
  - Full macOS support with Homebrew auto-installation
  - Improved Debian/Ubuntu package detection
  - Added ffmpeg to tool checks
  - Better error messages with platform-specific install commands
- **Dockerfile Updated**
  - Added ffmpeg for Listening Post audio encoding
  - Added dump1090 with fallback for different package names

### Fixed
- SoapySDR device detection for RTL-SDR and HackRF
- Aircraft dashboard toolbar layout when using custom frequency input
- Frequency switching now properly stops/restarts audio

### Technical
- Added `utils/constants.py` for centralized configuration values
- Added `utils/database.py` for SQLite settings storage
- Added `utils/correlation.py` for device correlation logic
- Added `routes/listening_post.py` for scanner endpoints
- Added `routes/settings.py` for settings API
- Added `routes/correlation.py` for correlation API

---

## [1.2.0] - 2026-12-29

### Added
- Airspy SDR support
- GPS coordinate persistence
- SoapySDR device detection improvements

### Fixed
- RTL-SDR and HackRF detection via SoapySDR

---

## [1.1.0] - 2026-12-18

### Added
- Satellite tracking with TLE data
- Full-screen dashboard for aircraft radar
- Full-screen dashboard for satellite tracking

---

## [1.0.0] - 2026-12-15

### Initial Release
- Pager decoding (POCSAG/FLEX)
- 433MHz sensor decoding
- ADS-B aircraft tracking
- WiFi reconnaissance
- Bluetooth scanning
- Multi-SDR support (RTL-SDR, LimeSDR, HackRF)

