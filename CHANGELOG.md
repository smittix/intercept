# Changelog

All notable changes to iNTERCEPT will be documented in this file.

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

