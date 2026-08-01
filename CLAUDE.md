# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

INTERCEPT is a web-based Signal Intelligence (SIGINT) platform providing a unified Flask interface for software-defined radio (SDR) tools. It supports pager decoding, 433MHz sensors, ADS-B aircraft tracking, ACARS messaging, WiFi/Bluetooth scanning, satellite tracking, ISS SSTV decoding, AIS vessel tracking, weather satellite imagery (NOAA APT & Meteor LRPT), and Meshtastic mesh networking.

## Common Commands

### Docker (Primary)
```bash
# Build and run (basic profile)
docker compose --profile basic up -d

# Build and run with ADS-B history (Postgres)
docker compose --profile history up -d

# Rebuild after code changes
docker compose --profile basic up -d --build

# Multi-arch build (amd64 + arm64 for RPi)
./build-multiarch.sh
```

### Local Setup (Alternative)
```bash
# First-time setup (interactive wizard with install profiles)
./setup.sh

# Or headless full install
./setup.sh --non-interactive

# Or install specific profiles
./setup.sh --profile=core,weather

# Run with production server (gunicorn + gevent, handles concurrent SSE/WebSocket)
sudo ./start.sh

# Or for quick local dev (Flask dev server)
sudo -E venv/bin/python intercept.py

# Other setup utilities
./setup.sh --health-check      # Verify installation
./setup.sh --postgres-setup    # Set up ADS-B history database
./setup.sh --menu              # Force interactive menu
```

### Testing
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_bluetooth.py

# Run with coverage
pytest --cov=routes --cov=utils

# Run a specific test
pytest tests/test_bluetooth.py::test_function_name -v
```

### Linting and Formatting
```bash
# Lint with ruff
ruff check .

# Auto-fix linting issues
ruff check --fix .

# Format with black
black .

# Type checking
mypy .
```

## Architecture

### Entry Points
- `setup.sh` - Menu-driven installer with profile system (wizard, health check, PostgreSQL setup, env configurator, update, uninstall). Sources `.env` on startup via `start.sh`.
- `start.sh` - Production startup script (gunicorn + gevent auto-detection, CLI flags, HTTPS, `.env` sourcing, fallback to Flask dev server)
- `intercept.py` - Direct Flask dev server entry point (quick local development)
- `app.py` - Flask application initialization, global state management, process lifecycle, SSE streaming infrastructure, conditional gevent monkey-patch

### Route Blueprints (routes/)
Each signal type has its own Flask blueprint:
- `pager.py` - POCSAG/FLEX decoding via rtl_fm + multimon-ng
- `sensor.py` - 433MHz IoT sensors via rtl_433
- `adsb.py` - Aircraft tracking via dump1090 (SBS protocol on port 30003)
- `acars.py` - Aircraft datalink messages via acarsdec
- `wifi.py`, `wifi_v2.py` - WiFi scanning (legacy and unified APIs)
- `bluetooth.py`, `bluetooth_v2.py` - Bluetooth scanning (legacy and unified APIs)
- `satellite.py` - Pass prediction using TLE data
- `sstv.py` - ISS SSTV image decoding via slowrx
- `weather_sat.py` - NOAA APT & Meteor LRPT via SatDump
- `ais.py` - AIS vessel tracking and VHF DSC distress monitoring
- `aprs.py` - Amateur packet radio via direwolf
- `rtlamr.py` - Utility meter reading
- `meshtastic_routes.py` - Meshtastic LoRa mesh networking

### Core Utilities (utils/)

**SDR Abstraction Layer** (`utils/sdr/`):
- `SDRFactory` with factory pattern for multiple SDR types (RTL-SDR, LimeSDR, HackRF, Airspy, SDRPlay)
- Each type has a `CommandBuilder` for generating CLI commands

**Bluetooth Module** (`utils/bluetooth/`):
- Multi-backend: DBus/BlueZ primary, fallback for systems without BlueZ
- `aggregator.py` - Merges observations across time
- `tracker_signatures.py` - 47K+ known tracker fingerprints (AirTag, Tile, SmartTag)
- `heuristics.py` - Behavioral analysis for device classification

**TSCM (Counter-Surveillance)** (`utils/tscm/`):
- `baseline.py` - Snapshot "normal" RF environment
- `detector.py` - Compare current scan to baseline, flag anomalies
- `device_identity.py` - Track devices despite MAC randomization
- `correlation.py` - Cross-reference Bluetooth and WiFi observations

**WiFi Utilities** (`utils/wifi/`):
- Platform-agnostic scanner with parsers for airodump-ng, nmcli, iw, iwlist, airport (macOS)
- `channel_analyzer.py` - Frequency band analysis

**Weather Satellite** (`utils/weather_sat.py`):
- Singleton `WeatherSatDecoder` using SatDump CLI for NOAA APT and Meteor LRPT
- Subprocess management with stdout parsing, image watcher via rglob
- Pass prediction using skyfield TLE data

**SSTV Decoder** (`utils/sstv.py`):
- ISS SSTV reception via slowrx with Doppler tracking
- Singleton pattern, image gallery with timestamped filenames

### Key Patterns

**Server-Sent Events (SSE)**: All real-time features stream via SSE endpoints (`/stream_pager`, `/stream_sensor`, etc.). Pattern uses `queue.Queue` with timeout and keepalive messages. Under gunicorn + gevent, each SSE connection is a lightweight greenlet instead of an OS thread.

**Process Management**: External decoders run as subprocesses with output threads feeding queues. Use `safe_terminate()` for cleanup. Global locks prevent race conditions.

**Data Stores**: `DataStore` class with TTL-based automatic cleanup (WiFi: 10min, Bluetooth: 5min, Aircraft: 5min).

**Input Validation**: Centralized in `utils/validation.py` - always validate frequencies, gains, device indices before spawning processes.

### External Tool Integrations

| Tool | Purpose | Integration |
|------|---------|-------------|
| rtl_fm | FM demodulation | Subprocess, pipes to multimon-ng |
| multimon-ng | Pager decoding | Reads from rtl_fm stdout |
| rtl_433 | 433MHz sensors | JSON output parsing |
| dump1090 | ADS-B decoding | SBS protocol socket (port 30003) |
| acarsdec | ACARS messages | Output parsing |
| airmon-ng/airodump-ng | WiFi scanning | Monitor mode, CSV parsing |
| bluetoothctl/hcitool | Bluetooth | Fallback when DBus unavailable |
| slowrx | SSTV decoding | Subprocess with audio pipe |
| SatDump | Weather satellites | CLI live mode, NOAA APT + Meteor LRPT |
| AIS-catcher | AIS vessel tracking | JSON output parsing |
| direwolf | APRS | TNC modem for packet radio |

### Frontend Structure
- **UI direction (decided 2026-06-12)**: map-heavy modes get dedicated dashboard
  pages (`/adsb/dashboard`, `/ais/dashboard`, `/satellite/dashboard`); the SPA
  in `index.html` keeps text/scan modes. APRS and Meshtastic are map-centric
  and should migrate to dashboards under their own plans — do not grow their
  SPA footprint.
- **Templates**: `templates/index.html` (main SPA), `templates/partials/modes/*.html` (sidebar panels), `templates/partials/nav.html` (global nav)
- **JS Modules**: `static/js/modes/*.js` - IIFE pattern per mode (e.g., `WeatherSat`, `SSTV`, `Meshtastic`)
- **CSS**: `static/css/modes/*.css` - scoped styles per mode, CSS variables for theming (`--bg-card`, `--accent-cyan`, `--font-mono`)
- **Mode Integration**: Each mode is declared once in `static/js/mode-registry.js`
  (label, group, elementId, module, init/destroy hooks, visuals flag). The
  catalog, sidebar toggles, destroy map, visuals list, and init dispatch in
  `templates/index.html` are all derived from it. A new mode additionally needs:
  its partial in `templates/partials/modes/`, entries in the CSS/JS lazy-load
  asset maps in `index.html`, and its include in the partials block.
  `tests/test_mode_registry.py` enforces registry/asset consistency.

### Docker
- `Dockerfile` - Single-stage build with all SDR tools compiled from source (dump1090, AIS-catcher, slowrx, SatDump, etc.). CMD runs `start.sh` (gunicorn + gevent)
- `docker-compose.yml` - Two profiles: `basic` (standalone) and `history` (with Postgres for ADS-B)
- `build-multiarch.sh` - Multi-arch build script for amd64 + arm64 (RPi5)
- Data persisted via `./data:/app/data` volume mount

### Configuration
- `config.py` - Environment variable support with `INTERCEPT_` prefix (e.g., `INTERCEPT_PORT`, `INTERCEPT_WEATHER_SAT_GAIN`)
- Database: SQLite in `instance/` directory for settings, baselines, history

## Testing Notes

Tests use pytest with extensive mocking of external tools. Key fixtures in `tests/conftest.py`. Mock subprocess calls when testing decoder integration.

### Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.
2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:

Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:

Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

### Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]


<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
specs/001-uconsole-handheld-ui/plan.md
<!-- SPECKIT END -->
