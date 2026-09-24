"""Configuration settings for intercept application."""

from __future__ import annotations

import logging
import os
import sys

# Application version
VERSION = "2.33.16"

# Changelog - latest release notes (shown on welcome screen)
CHANGELOG = [
    {
        "version": "2.33.16",
        "date": "September 2026",
        "highlights": [
            "Alerts, recording, MQTT and the activity feed now handle each event once, whether or not a page is open. Before, they ran once per open browser tab, and not at all with none.",
        ],
    },
    {
        "version": "2.33.15",
        "date": "September 2026",
        "highlights": [
            "TSCM client report is a printable page: print it, or save it as a PDF. It now includes the baseline comparison and meeting windows, and past sweeps are listed in the survey.",
            "Fix: drone detection claims its SDR and says which sources started; SubGHz and remote agents name a missing tool; meeting windows were compared an hour out in summer time.",
        ],
    },
    {
        "version": "2.33.14",
        "date": "September 2026",
        "highlights": [
            "Feat: Activity feed (Intel > Activity). Sightings from every mode that reports them in one stream, newest first, colour-coded by source, filterable by source, time window and device, and pausable without losing anything.",
            "433 MHz and Wi-Fi record sightings whenever they run; busy sources are thinned so ADS-B cannot swamp the feed, and sightings are kept 24 hours (configurable).",
        ],
    },
    {
        "version": "2.33.13",
        "date": "September 2026",
        "highlights": [
            'Live lists show elapsed time ("14 s ago"), kept current every second, with the exact time on hover.',
            'An empty list now says why: "rtl_433 running · 0 readings in 4 s", stopped, or the reason it failed to start.',
            "Copy buttons on ICAO, MMSI, MAC and node identifiers, and notes and tags on any device, shown wherever it appears.",
            "Hidden tabs stop their clocks and status polls; install advice matches your platform.",
        ],
    },
    {
        "version": "2.33.12",
        "date": "September 2026",
        "highlights": [
            "Fix: stopping Morse or OOK could hang indefinitely, and a failed pager start left the SDR marked busy until restart.",
            "Fix: one corrupt byte ended the receiver waterfall; Bluetooth scanners could be stopped by a nearby device's name.",
            "Fix: several modes reported a missing tool as a vague server error, or a failed start as success; they now name the tool.",
            "Fix: Meshtastic with no USB device reported itself running, and with two serial ports could shut the server down.",
        ],
    },
    {
        "version": "2.33.11",
        "date": "September 2026",
        "highlights": [
            "Feat: TSCM Survey workspace (Intel > TSCM Survey). One page walks a survey in order: the active baseline and its health, what the latest sweep shows that the baseline did not, known devices, threats and findings, then the report.",
            "Fix: a TSCM baseline's age read hours too old outside UTC, and comparing a sweep that had not finished failed with a server error.",
        ],
    },
    {
        "version": "2.33.10",
        "date": "September 2026",
        "highlights": [
            "Fix: TSCM reports now assess each finding's signal from what was observed; every finding previously read 'Minimal, Low confidence'.",
            "Fix: the TSCM CSV annex listed every device as informational with risk score 0, and could carry spreadsheet formulas from device names.",
            "Fix: a TSCM report for a running or aborted sweep failed with a server error; sweep times were shown in UTC beside a local 'Generated' time.",
            "Fix: a TSCM sweep that detected nothing is now reported as INCONCLUSIVE, not as a clean LOW, and an enabled Wi-Fi or Bluetooth band that saw no devices is called out.",
            "Change: the TSCM client report states what was measured (dBm, time observed, sightings) and the pattern seen, instead of a confidence label, a proximity guess and a risk score. The technical annexes keep those values.",
        ],
    },
    {
        "version": "2.33.9",
        "date": "September 2026",
        "highlights": [
            "Feat: name your SDRs. Settings > SDR gives each receiver a display name that replaces 'Device 0' in every device selector (#269).",
            "Feat: per-device PPM correction, default gain and bias-T. A mode uses them whenever its own field is left blank; a value entered in the mode still wins (#238).",
            "Fix: rtlamr passed PPM correction to rtl_tcp as its listen port.",
        ],
    },
    {
        "version": "2.33.8",
        "date": "September 2026",
        "highlights": [
            "Security: an account seeded with a password you did not choose now has to set one before the interface is usable. New installs, and any install still using the old admin/admin default, are prompted at first login.",
            "Feat: a Change Password page, reachable at /change-password whether or not a change is required.",
        ],
    },
    {
        "version": "2.33.7",
        "date": "September 2026",
        "highlights": [
            "Feat: Kill All Processes is now in the global nav, so it is reachable from every page instead of only the main dashboard's System panel.",
        ],
    },
    {
        "version": "2.33.6",
        "date": "September 2026",
        "highlights": [
            "Security: the /controller/* API required no authentication at all \u2014 anyone who could reach the port could list remote agents, read their API keys, register or delete agents, and start or stop SDR hardware on remote nodes. It now requires a session, or an API key for agent push.",
            "Security: agent API keys are no longer included in API responses.",
            "Security: WebSocket endpoints now verify the session; a stale auth exemption for audio streaming was removed; the default admin password has been removed; session cookies set SameSite and HttpOnly explicitly.",
            "Action required: if you run remote agents, each must now have an API key configured to push data.",
        ],
    },
    {
        "version": "2.33.5",
        "date": "September 2026",
        "highlights": [
            "Fix: Meshtastic nodes first heard via telemetry, text or routing packets did not appear until the page was reloaded \u2014 the node list only refreshed on position/nodeinfo packets.",
        ],
    },
    {
        "version": "2.33.4",
        "date": "September 2026",
        "highlights": [
            "Fix: ADS-B aircraft tracked via a remote agent never left the map \u2014 the agent kept every contact for the life of the scan and re-sent them on each poll, so they only cleared when the mode was stopped. Contacts now age out after 5 minutes, matching local mode.",
        ],
    },
    {
        "version": "2.33.3",
        "date": "September 2026",
        "highlights": [
            "Feat: 433MHz sensor Units selector (Metric / Imperial / Native). Defaults to Metric, so temperatures now read in °C — pick Native to restore the previous per-device behaviour.",
        ],
    },
    {
        "version": "2.33.2",
        "date": "September 2026",
        "highlights": [
            "Fix: DSC call types and nature-of-distress labels were wrong — format specifiers 112/120 were swapped, so a routine individual call displayed as DISTRESS and a real mayday displayed as INDIVIDUAL. Nature-of-distress labels were shifted one position off every code.",
        ],
    },
    {
        "version": "2.33.1",
        "date": "September 2026",
        "highlights": [
            "Fix: ACARS crashed with SIGILL in the amd64 Docker image — acarsdec was compiled with the build host's instruction set (AVX-512) and would not run on older CPUs. Pull the latest image to pick up the fix.",
        ],
    },
    {
        "version": "2.33.0",
        "date": "August 2026",
        "highlights": [
            "Feat: CARTO API key setting (Settings > Map Tiles) removes the new watermark CARTO adds to unauthenticated tile requests — free key, no account needed",
            "Fix: mode maps (Drone, SSTV, MeshCore, Meshtastic, WebSDR, Weather Satellite) no longer briefly flash unkeyed CartoDB tiles when Settings is already loaded",
        ],
    },
    {
        "version": "2.32.0",
        "date": "July 2026",
        "highlights": [
            "Feat: ADS-B historical playback — replay past aircraft positions on an interactive map with play/pause, speed control, and time scrubber (requires history profile)",
        ],
    },
    {
        "version": "2.31.0",
        "date": "July 2026",
        "highlights": [
            "Feat: BladeRF support — bladeRF 2.0 micro and x40/x115 detected and usable via SoapyBladeRF (47 MHz – 6 GHz, TX capable)",
            "Feat: HydraSDR RFOne support — detected and usable via SoapyHydraSDR (24 MHz – 1800 MHz, RX only)",
        ],
    },
    {
        "version": "2.30.0",
        "date": "July 2026",
        "highlights": [
            "Feat: APRS station export — download all tracked stations as JSON or CSV via /aprs/export",
            "Feat: USRP support — Ettus USRP N200/B200/B210 detected and usable via SoapyUHD bridge",
            "Feat: MQTT data export — publish decoded events from all modules to a configurable broker (set INTERCEPT_MQTT_BROKER to enable)",
        ],
    },
    {
        "version": "2.29.0",
        "date": "July 2026",
        "highlights": [
            "Feat: TSCM sweep metadata — Site/Location and Examiner name embedded in reports",
            "Feat: Mark Cleared button dims/excludes devices from TSCM reports, with a cleared-device count in the summary",
            "Feat: Examiner Ignore List — persist your own devices so they're filtered from the live view and every report export",
            "Feat: TSCM report category filter (High Interest / Needs Review / Informational) for HTML, PDF, JSON, and CSV exports",
            "Fix: ADS-B history Postgres password is now correctly read from .env (setup wizard and Docker Compose previously ignored a custom password)",
        ],
    },
    {
        "version": "2.28.0",
        "date": "July 2026",
        "highlights": [
            "Feat: Signal ID — offline signal identification against a bundled 594-signal database",
            "Feat: Signal ID modal accessible from the global nav and waterfall sidebar",
            "Feat: scored matching by frequency, bandwidth, modulation, and region (0–100)",
            "Feat: import script (bin/import_artemis.py) to refresh database from Artemis-DB",
        ],
    },
    {
        "version": "2.27.0",
        "date": "May 2026",
        "highlights": [
            "Fix: two-window hang caused by browser HTTP/1.1 connection pool exhaustion",
            "Fix: SSE alert and Bluetooth streams now fan out to all windows (no more split messages)",
            "Feat: UI tier system — lean, standard, enhanced display modes via nav toggle",
            "Feat: first-run setup modal includes display mode selection",
            "Perf: ADS-B SSE snapshot priming moved into generator; WiFi filter combined into single pass",
            "Perf: Bluetooth tracker signature scan skips unchanged fingerprints",
            "Fix: ICAO lookup cache capped at 50k entries with LRU eviction",
        ],
    },
    {
        "version": "2.26.13",
        "date": "March 2026",
        "highlights": [
            "Fix TSCM sweep module variable scoping and stale progress bar",
            "Fix 5GHz WiFi scanning failures in deep scan and band detection",
            "Fix ADS-B remote mode incorrectly stopping other SDR services",
            "Fix radiosonde false 'missing' report at end of setup",
            "Satellite tracker: TLE auto-refresh, polar plot fixes, pass calculation improvements",
            "Fix weather satellite handoff (remove defunct METEOR-M2)",
            "Add multi-arch Docker CI workflow (amd64 + arm64)",
        ],
    },
    {
        "version": "2.26.12",
        "date": "March 2026",
        "highlights": [
            "AIS and ADS-B dashboards now use configured observer position from .env",
        ],
    },
    {
        "version": "2.26.11",
        "date": "March 2026",
        "highlights": [
            "APRS map now centres on configured observer position from .env",
        ],
    },
    {
        "version": "2.26.8",
        "date": "March 2026",
        "highlights": [
            "Fix acarsdec build failure on macOS (HOST_NAME_MAX undefined)",
        ],
    },
    {
        "version": "2.26.7",
        "date": "March 2026",
        "highlights": [
            "Fix health check SDR detection on macOS (timeout command not available)",
        ],
    },
    {
        "version": "2.26.6",
        "date": "March 2026",
        "highlights": [
            "Fix oversized branded 'i' logo on Aircraft & Vessel dashboards",
        ],
    },
    {
        "version": "2.26.5",
        "date": "March 2026",
        "highlights": [
            "Fix database errors crashing the entire UI — pages now degrade gracefully",
        ],
    },
    {
        "version": "2.26.4",
        "date": "March 2026",
        "highlights": [
            "Fix Environment Configurator crash when .env exists but variable is missing",
        ],
    },
    {
        "version": "2.26.3",
        "date": "March 2026",
        "highlights": [
            "Fix SatDump AVX2 crash on older CPUs — build now targets baseline x86-64",
        ],
    },
    {
        "version": "2.26.2",
        "date": "March 2026",
        "highlights": [
            "Fix Docker startup crash — data/ Python package was excluded by .dockerignore",
        ],
    },
    {
        "version": "2.26.1",
        "date": "March 2026",
        "highlights": [
            "Fix default admin credentials — now matches README (admin:admin)",
            "Admin password changes in config.py / env vars now sync to DB on restart",
        ],
    },
    {
        "version": "2.26.0",
        "date": "March 2026",
        "highlights": [
            "Fix SSE fanout thread crash when source queue is None during shutdown",
            "Fix branded 'i' logo FOUC (flash of unstyled content) on first page load",
        ],
    },
    {
        "version": "2.25.0",
        "date": "March 2026",
        "highlights": [
            "UI/UX overhaul — SSEManager with exponential backoff and connection status indicator",
            "Accessibility improvements — aria-labels, form label associations, keyboard list navigation",
            "Destructive action confirmation modals replace native confirm() dialogs",
            "CSS variable adoption, inline style extraction, and reduced !important usage",
            "Loading button states, actionable error reporting, and mobile UX polish",
        ],
    },
    {
        "version": "2.24.0",
        "date": "March 2026",
        "highlights": [
            "WiFi Locate mode — locate access points by BSSID with real-time signal meter, distance estimation, RSSI chart, and audio proximity tones",
            "Mobile navigation reorganized into labeled groups for better usability",
            "flask-limiter made optional for graceful degradation",
            "Radiosonde setup fix — missing semver dependency",
        ],
    },
    {
        "version": "2.23.0",
        "date": "February 2026",
        "highlights": [
            "Radiosonde weather balloon tracking mode with telemetry, map, and station distance",
            "CW/Morse code decoder with Goertzel tone detection and OOK envelope mode",
            "WeFax (Weather Fax) decoder with auto-scheduler and broadcast timeline",
            "System Health monitoring mode with telemetry dashboard",
            "HTTPS support, HackRF TSCM RF scan, ADS-B voice alerts",
            "Production server (start.sh) with gunicorn + gevent for concurrent multi-client support",
            "Multi-SDR support for WeFax, tool path overrides, native Homebrew detection",
            "GPS mode upgraded to textured 3D globe",
            "Destroy lifecycle added to all mode modules to prevent resource leaks",
            "Dozens of bug fixes across ADS-B, APRS, SSE, Morse, waterfall, and more",
        ],
    },
    {
        "version": "2.22.3",
        "date": "February 2026",
        "highlights": [
            "Waterfall control panel no longer shows as unstyled text on first visit",
            "WebSDR globe renders correctly on first page load without requiring a refresh",
            "Waterfall monitor audio no longer takes minutes to start — playback detection now waits for real audio data instead of just the WAV header",
            "Waterfall monitor stop is now instant — audio pauses and UI updates immediately instead of waiting for backend cleanup",
            "Stopping the waterfall no longer shows a stale 'WebSocket closed before ready' message",
        ],
    },
    {
        "version": "2.22.1",
        "date": "February 2026",
        "highlights": [
            "Waterfall receiver overhaul: WebSocket I/Q streaming with server-side FFT, click-to-tune, and zoom controls",
            "Voice alerts for configurable event notifications across modes",
            "Signal fingerprinting mode for RF device identification and pattern analysis",
            "SignalID integration via SigIDWiki API for automatic signal classification",
            "PWA support: installable web app with service worker and manifest",
            "Mode stop responsiveness improvements with faster timeout handling",
            "Navigation performance instrumentation and smoother mode transitions",
            "Pager, sensor, and SSTV real-time signal scope visualization",
            "ADS-B MSG2 surface movement parsing for ground vehicle tracking",
            "WebSDR major overhaul with improved receiver management and audio streaming",
            "Documentation audit: fixed license, tool names, entry points, and SSTV decoder references",
            "Help modal updated with ACARS and VDL2 mode descriptions",
        ],
    },
    {
        "version": "2.21.1",
        "date": "February 2026",
        "highlights": [
            "BT Locate map first-load fix with render stabilization retries during initial mode open",
            "BT Locate trail restore optimization for faster startup when historical GPS points exist",
            "BT Locate mode-switch map invalidation timing fix to prevent delayed/blank map render",
        ],
    },
    {
        "version": "2.21.0",
        "date": "February 2026",
        "highlights": [
            "Global map theme refresh with improved contrast and cross-dashboard consistency",
            "Cross-app UX updates for accessibility, mode consistency, and render performance",
            "Weather satellite reliability fixes for auto-scheduler and Mercator pass tracking",
            "Bluetooth/WiFi runtime health fixes with BT Locate continuity and confidence improvements",
            "ADS-B/VDL2 streaming reliability upgrades for multi-client SSE fanout and remote decoding",
            "Analytics enhancements with operational insights and temporal pattern panels",
        ],
    },
    {
        "version": "2.20.0",
        "date": "February 2026",
        "highlights": [
            "Space Weather mode: real-time solar and geomagnetic monitoring from NOAA SWPC, NASA SDO, and HamQSL",
            "Kp index, solar wind, X-ray flux charts with Chart.js visualization",
            "HF band conditions, D-RAP absorption maps, aurora forecast, and solar imagery",
            "NOAA Space Weather Scales (G/S/R), flare probability, and active solar regions",
            "No SDR hardware required — all data from public APIs with server-side caching",
        ],
    },
    {
        "version": "2.19.0",
        "date": "February 2026",
        "highlights": [
            "VDL2 mode with modal message viewer, consolidated into ADS-B dashboard",
            "ADS-B: trails enabled by default, radar modes removed, CSV export added",
            "Bundled Roboto Condensed font for offline mode with SVG icon overhaul",
            "Help modal updated with all modes and correct SVG icons",
            "Setup script overhauled for reliability and macOS compatibility",
            "GPS fix for preserving satellites across DOP-only SKY messages",
            "Fix gpsd deadlock causing GPS connect to hang",
        ],
    },
    {
        "version": "2.18.0",
        "date": "February 2026",
        "highlights": [
            "Bluetooth: service data inspector, appearance codes, MAC cluster tracking, and behavioral flags",
            "Bluetooth: IRK badge display, distance estimation with confidence, and signal stability metrics",
            "ACARS: SoapySDR device support for SDRplay, LimeSDR, Airspy, and other non-RTL backends",
            "ADS-B: stale dump1090 process cleanup via PID file tracking",
            "GPS: error state indicator and UI refinements",
            "Proximity radar and signal card UI improvements",
        ],
    },
    {
        "version": "2.17.0",
        "date": "February 2026",
        "highlights": [
            "BT Locate: SAR Bluetooth device location with GPS-tagged signal trail and proximity alerts",
            "IRK auto-detection: extract Identity Resolving Keys from paired devices (macOS/Linux)",
            "GPS mode: real-time position tracking with live map, speed, altitude, and satellite info",
            "Bluetooth scanner lifecycle fix for bleak scan timeout tracking",
        ],
    },
    {
        "version": "2.16.0",
        "date": "February 2026",
        "highlights": [
            "Sub-GHz analyzer with real-time RF capture and protocol decoding",
            "Weather satellite auto-scheduler with polar plot and ground track map",
            "SatDump support for local (non-Docker) installs via setup.sh",
            "Shared waterfall UI across SDR modes",
            "Listening post audio stuttering fix and SDR race condition fixes",
            "Multi-arch Docker build support (amd64 + arm64)",
        ],
    },
    {
        "version": "2.15.0",
        "date": "February 2026",
        "highlights": [
            "Real-time WebSocket waterfall with I/Q capture and server-side FFT",
            "Cross-module frequency routing from Listening Post to decoders",
            "Pure Python SSTV decoder replacing broken slowrx dependency",
            "Real-time signal scope for pager, sensor, and SSTV modes",
            "USB-level device probe to prevent cryptic rtl_fm crashes",
            "SDR device lock-up fix from unreleased device registry on crash",
        ],
    },
    {
        "version": "2.14.0",
        "date": "February 2026",
        "highlights": [
            "HF SSTV general mode with predefined shortwave frequencies",
            "WebSDR integration for remote HF/shortwave listening",
            "Listening Post signal scanner and audio pipeline improvements",
            "TSCM sweep resilience, WiFi detection, and correlation fixes",
            "APRS rtl_fm startup and SDR device conflict fixes",
        ],
    },
    {
        "version": "2.13.1",
        "date": "February 2026",
        "highlights": [
            "UI overhaul with slate/cyan theme and JetBrains Mono font",
            "Signal scanner rewritten with rtl_power sweep and SNR filtering",
            "Listening Post audio streaming via WAV with retry/fallback",
            "WiFi connected clients panel now filters to selected AP",
            "Global navigation bar across all dashboards",
            "Fixed USB device contention when starting audio pipeline",
        ],
    },
    {
        "version": "2.13.0",
        "date": "February 2026",
        "highlights": [
            "WiFi client display in AP detail drawer with real-time SSE updates",
            "Help modal system with keyboard shortcuts reference",
            "Global navbar and settings modal accessible from all dashboards",
            "Probed SSID badges for connected clients",
        ],
    },
    {
        "version": "2.12.1",
        "date": "February 2026",
        "highlights": [
            "SDR device registry to prevent decoder conflicts",
            "SDR device status panel and ADS-B Bias-T toggle",
            "Real-time Doppler tracking for ISS SSTV reception",
            "TCP connection support for Meshtastic",
            "Shared observer location with auto-start options",
        ],
    },
    {
        "version": "2.12.0",
        "date": "January 2026",
        "highlights": [
            "ISS SSTV decoder with real-time ISS tracking globe",
            "GitHub update notifications for new releases",
            "Meshtastic QR code support and telemetry display",
            "New Space category with reorganized UI",
        ],
    },
    {
        "version": "2.11.0",
        "date": "January 2026",
        "highlights": [
            "Meshtastic LoRa mesh network integration",
            "Ubertooth One BLE scanning support",
            "Offline mode with bundled assets",
            "Settings modal with tile provider configuration",
        ],
    },
    {
        "version": "2.10.0",
        "date": "January 2026",
        "highlights": [
            "AIS vessel tracking with VHF DSC distress monitoring",
            "Spy Stations database (number stations & diplomatic HF)",
            "MMSI country identification and distress alert overlays",
            "SDR device conflict detection for AIS/DSC",
        ],
    },
    {
        "version": "2.9.5",
        "date": "January 2026",
        "highlights": [
            "Enhanced TSCM with MAC-randomization resistant detection",
            "Clickable score cards and device detail expansion",
            "RF scanning improvements with status feedback",
            "Root privilege check and warning display",
        ],
    },
    {
        "version": "2.9.0",
        "date": "January 2026",
        "highlights": [
            "New dropdown navigation menus for cleaner UI",
            "TSCM baseline recording now captures device data",
            "Device identity engine integration for threat detection",
            "Welcome screen with mode selection",
        ],
    },
    {
        "version": "2.8.0",
        "date": "December 2025",
        "highlights": [
            "Added TSCM counter-surveillance mode",
            "WiFi/Bluetooth device correlation engine",
            "Tracker detection (AirTag, Tile, SmartTag)",
            "Risk scoring and threat classification",
        ],
    },
]


def _get_env(key: str, default: str) -> str:
    """Get environment variable with default."""
    return os.environ.get(f"INTERCEPT_{key}", default)


def _get_env_int(key: str, default: int) -> int:
    """Get environment variable as integer with default."""
    try:
        return int(os.environ.get(f"INTERCEPT_{key}", str(default)))
    except ValueError:
        return default


def _get_env_float(key: str, default: float) -> float:
    """Get environment variable as float with default."""
    try:
        return float(os.environ.get(f"INTERCEPT_{key}", str(default)))
    except ValueError:
        return default


def _get_env_bool(key: str, default: bool) -> bool:
    """Get environment variable as boolean with default."""
    val = os.environ.get(f"INTERCEPT_{key}", "").lower()
    if val in ("true", "1", "yes", "on"):
        return True
    if val in ("false", "0", "no", "off"):
        return False
    return default


# Logging configuration
_log_level_str = _get_env("LOG_LEVEL", "WARNING").upper()
LOG_LEVEL = getattr(logging, _log_level_str, logging.WARNING)
LOG_FORMAT = _get_env("LOG_FORMAT", "%(asctime)s - %(levelname)s - %(message)s")

# Server settings
HOST = _get_env("HOST", "0.0.0.0")
PORT = _get_env_int("PORT", 5050)
DEBUG = _get_env_bool("DEBUG", False)
THREADED = _get_env_bool("THREADED", True)

# HTTPS / SSL settings
HTTPS = _get_env_bool("HTTPS", False)
SSL_CERT = _get_env("SSL_CERT", "")
SSL_KEY = _get_env("SSL_KEY", "")

# Default RTL-SDR settings
DEFAULT_GAIN = _get_env("DEFAULT_GAIN", "40")
DEFAULT_DEVICE = _get_env("DEFAULT_DEVICE", "0")

# Pager defaults
DEFAULT_PAGER_FREQ = _get_env("PAGER_FREQ", "929.6125M")

# Timeouts
PROCESS_TIMEOUT = _get_env_int("PROCESS_TIMEOUT", 5)
SOCKET_TIMEOUT = _get_env_int("SOCKET_TIMEOUT", 5)
SSE_TIMEOUT = _get_env_int("SSE_TIMEOUT", 1)

# WiFi settings
WIFI_UPDATE_INTERVAL = _get_env_float("WIFI_UPDATE_INTERVAL", 2.0)
AIRODUMP_HEADER_LINES = _get_env_int("AIRODUMP_HEADER_LINES", 2)

# Bluetooth settings
BT_SCAN_TIMEOUT = _get_env_int("BT_SCAN_TIMEOUT", 10)
BT_UPDATE_INTERVAL = _get_env_float("BT_UPDATE_INTERVAL", 2.0)

# ADS-B settings
ADSB_SBS_PORT = _get_env_int("ADSB_SBS_PORT", 30003)
ADSB_UPDATE_INTERVAL = _get_env_float("ADSB_UPDATE_INTERVAL", 1.0)
ADSB_AUTO_START = _get_env_bool("ADSB_AUTO_START", False)
ADSB_HISTORY_ENABLED = _get_env_bool("ADSB_HISTORY_ENABLED", False)
ADSB_DB_HOST = _get_env("ADSB_DB_HOST", "localhost")
ADSB_DB_PORT = _get_env_int("ADSB_DB_PORT", 5432)
ADSB_DB_NAME = _get_env("ADSB_DB_NAME", "intercept_adsb")
ADSB_DB_USER = _get_env("ADSB_DB_USER", "intercept")
ADSB_DB_PASSWORD = _get_env("ADSB_DB_PASSWORD", "intercept")
ADSB_HISTORY_BATCH_SIZE = _get_env_int("ADSB_HISTORY_BATCH_SIZE", 500)
ADSB_HISTORY_FLUSH_INTERVAL = _get_env_float("ADSB_HISTORY_FLUSH_INTERVAL", 1.0)
ADSB_HISTORY_QUEUE_SIZE = _get_env_int("ADSB_HISTORY_QUEUE_SIZE", 50000)

# Observer location settings
SHARED_OBSERVER_LOCATION_ENABLED = _get_env_bool("SHARED_OBSERVER_LOCATION", True)
DEFAULT_LATITUDE = _get_env_float("DEFAULT_LAT", 0.0)
DEFAULT_LONGITUDE = _get_env_float("DEFAULT_LON", 0.0)

# Satellite settings
SATELLITE_UPDATE_INTERVAL = _get_env_int("SATELLITE_UPDATE_INTERVAL", 30)
SATELLITE_TRAJECTORY_POINTS = _get_env_int("SATELLITE_TRAJECTORY_POINTS", 30)
SATELLITE_ORBIT_MINUTES = _get_env_int("SATELLITE_ORBIT_MINUTES", 45)

# Weather satellite settings
WEATHER_SAT_DEFAULT_GAIN = _get_env_float("WEATHER_SAT_GAIN", 30.0)
WEATHER_SAT_SAMPLE_RATE = _get_env_int("WEATHER_SAT_SAMPLE_RATE", 2400000)
WEATHER_SAT_MIN_ELEVATION = _get_env_float("WEATHER_SAT_MIN_ELEVATION", 15.0)
WEATHER_SAT_PREDICTION_HOURS = _get_env_int("WEATHER_SAT_PREDICTION_HOURS", 24)
WEATHER_SAT_SCHEDULE_REFRESH_MINUTES = _get_env_int("WEATHER_SAT_SCHEDULE_REFRESH_MINUTES", 30)
WEATHER_SAT_CAPTURE_BUFFER_SECONDS = _get_env_int("WEATHER_SAT_CAPTURE_BUFFER_SECONDS", 30)

# WeFax (Weather Fax) settings
WEFAX_DEFAULT_GAIN = _get_env_float("WEFAX_GAIN", 40.0)
WEFAX_SAMPLE_RATE = _get_env_int("WEFAX_SAMPLE_RATE", 22050)
WEFAX_DEFAULT_IOC = _get_env_int("WEFAX_IOC", 576)
WEFAX_DEFAULT_LPM = _get_env_int("WEFAX_LPM", 120)
WEFAX_SCHEDULE_REFRESH_MINUTES = _get_env_int("WEFAX_SCHEDULE_REFRESH_MINUTES", 30)
WEFAX_CAPTURE_BUFFER_SECONDS = _get_env_int("WEFAX_CAPTURE_BUFFER_SECONDS", 30)

# SubGHz transceiver settings (HackRF)
SUBGHZ_DEFAULT_FREQUENCY = _get_env_float("SUBGHZ_FREQUENCY", 433.92)
SUBGHZ_DEFAULT_SAMPLE_RATE = _get_env_int("SUBGHZ_SAMPLE_RATE", 2000000)
SUBGHZ_DEFAULT_LNA_GAIN = _get_env_int("SUBGHZ_LNA_GAIN", 32)
SUBGHZ_DEFAULT_VGA_GAIN = _get_env_int("SUBGHZ_VGA_GAIN", 20)
SUBGHZ_DEFAULT_TX_GAIN = _get_env_int("SUBGHZ_TX_GAIN", 20)
SUBGHZ_MAX_TX_DURATION = _get_env_int("SUBGHZ_MAX_TX_DURATION", 10)
SUBGHZ_SWEEP_START_MHZ = _get_env_float("SUBGHZ_SWEEP_START", 300.0)
SUBGHZ_SWEEP_END_MHZ = _get_env_float("SUBGHZ_SWEEP_END", 928.0)

# Radiosonde settings
RADIOSONDE_FREQ_MIN = _get_env_float("RADIOSONDE_FREQ_MIN", 400.0)
RADIOSONDE_FREQ_MAX = _get_env_float("RADIOSONDE_FREQ_MAX", 406.0)
RADIOSONDE_DEFAULT_GAIN = _get_env_float("RADIOSONDE_GAIN", 40.0)
RADIOSONDE_UDP_PORT = _get_env_int("RADIOSONDE_UDP_PORT", 55673)

# Update checking
GITHUB_REPO = _get_env("GITHUB_REPO", "smittix/intercept")
UPDATE_CHECK_ENABLED = _get_env_bool("UPDATE_CHECK_ENABLED", True)
UPDATE_CHECK_INTERVAL_HOURS = _get_env_int("UPDATE_CHECK_INTERVAL_HOURS", 6)

# Activity feed observations: how long they are kept, and a ceiling on rows
OBSERVATION_RETENTION_HOURS = _get_env_float("OBSERVATION_RETENTION_HOURS", 24.0)
OBSERVATION_MAX_ROWS = _get_env_int("OBSERVATION_MAX_ROWS", 100_000)

# Alerting
ALERT_WEBHOOK_URL = _get_env("ALERT_WEBHOOK_URL", "")
ALERT_WEBHOOK_SECRET = _get_env("ALERT_WEBHOOK_SECRET", "")
ALERT_WEBHOOK_TIMEOUT = _get_env_int("ALERT_WEBHOOK_TIMEOUT", 5)

# MQTT data export (optional — disabled when MQTT_BROKER is empty)
MQTT_BROKER = _get_env("MQTT_BROKER", "")
MQTT_PORT = _get_env_int("MQTT_PORT", 1883)
MQTT_USER = _get_env("MQTT_USER", "")
MQTT_PASSWORD = _get_env("MQTT_PASSWORD", "")
MQTT_TOPIC_PREFIX = _get_env("MQTT_TOPIC_PREFIX", "intercept")
MQTT_RETAIN = _get_env_bool("MQTT_RETAIN", False)

# Admin credentials.
# ADMIN_PASSWORD has no default on purpose. When it is unset, first-run
# database init generates a random password, logs it, and writes it to
# instance/.initial_password. A shipped default of "admin" meant every
# install that never set the env var shared the same known credentials.
ADMIN_USERNAME = _get_env("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = _get_env("ADMIN_PASSWORD", "")

# Signal identification region (affects match ranking; does not filter results)
# Valid values: GLOBAL, EU, US, UK, AU
REGION = _get_env("REGION", "GLOBAL")


def configure_logging() -> None:
    """Configure application logging."""
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, stream=sys.stderr)
    # Suppress Flask development server warning
    logging.getLogger("werkzeug").setLevel(LOG_LEVEL)
