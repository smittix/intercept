<p align="center">
  <img src="static/images/readme-banner.svg" alt="iNTERCEPT — Signal Intelligence Platform" width="100%">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/license-Apache--2.0-green.svg" alt="Apache 2.0 License">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg" alt="Platform">
</p>

<p align="center">
Support the developer of this open-source project
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/smittix" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me A Coffee" style="height: 41px !important;width: 174px !important;box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;-webkit-box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;" ></a>
</p>
<p align="center">
  <strong>Signal Intelligence Platform</strong><br>
  A web-based interface for software-defined radio tools.
</p>

<p align="center">
  <img src="static/images/screenshots/intercept-main.png" alt="Screenshot">
</p>

---

## Features

- **Pager Decoding** - POCSAG/FLEX via rtl_fm + multimon-ng
- **433MHz Sensors** - Weather stations, TPMS, IoT devices via rtl_433
- **Sub-GHz Analyzer** - RF capture and protocol decoding for 300-928 MHz ISM bands via HackRF
- **Aircraft Tracking** - ADS-B via dump1090 with real-time map and radar
- **Vessel Tracking** - AIS ship tracking with VHF DSC distress monitoring
- **ACARS Messaging** - Aircraft datalink messages via acarsdec
- **VDL2** - VHF Data Link Mode 2 aircraft datalink decoding via dumpvdl2
- **Listening Post** - Wideband frequency scanner with real-time audio monitoring
- **Weather Satellites** - NOAA APT and Meteor LRPT image decoding via SatDump with auto-scheduler
- **WebSDR** - Remote HF/shortwave listening via KiwiSDR network
- **ISS SSTV** - Slow-scan TV image reception from the International Space Station
- **HF SSTV** - Terrestrial SSTV on shortwave frequencies (80m-10m, VHF, UHF)
- **APRS** - Amateur packet radio position reports and telemetry via direwolf
- **Satellite Tracking** - Pass prediction with polar plot and ground track map
- **Utility Meters** - Electric, gas, and water meter reading via rtlamr
- **ADS-B History** - Persistent aircraft history with reporting dashboard (Postgres optional)
- **WiFi Scanning** - Monitor mode reconnaissance via aircrack-ng
- **Bluetooth Scanning** - Device discovery and tracker detection (with Ubertooth support)
- **BT Locate** - SAR Bluetooth device location with GPS-tagged signal trail mapping and proximity alerts
- **WiFi Locate** - Locate WiFi access points by BSSID with real-time signal meter, distance estimation, and proximity audio
- **GPS** - Real-time GPS position tracking with live map, speed, altitude, and satellite info
- **TSCM** - Counter-surveillance with RF baseline comparison and threat detection
- **Meshtastic** - LoRa mesh network integration
- **Space Weather** - Real-time solar and geomagnetic data from NOAA SWPC, NASA SDO, and HamQSL (no SDR required)
- **Spy Stations** - Number stations and diplomatic HF network database
- **Remote Agents** - Distributed SIGINT with remote sensor nodes
- **Offline Mode** - Bundled assets for air-gapped/field deployments
- **Drone Intelligence** - Multi-vector UAV detection via ASTM F3411 Remote ID (WiFi/BLE), RTL-SDR 433/868 MHz RF, and HackRF 2.4/5.8 GHz scanning with live contact map and risk scoring

---

## CW / Morse Decoder Notes

Live backend:
- Uses `rtl_fm` piped into `multimon-ng` (`MORSE_CW`) for real-time decode.

Recommended baseline settings:
- **Tone**: `700 Hz`
- **Bandwidth**: `200 Hz` (use `100 Hz` for crowded bands, `400 Hz` for drifting signals)
- **Threshold Mode**: `Auto`
- **WPM Mode**: `Auto`

Auto Tone Track behavior:
- Continuously measures nearby tone energy around the configured CW pitch.
- Steers the detector toward the strongest valid CW tone when signal-to-noise is sufficient.
- Use **Hold Tone Lock** to freeze tracking once the desired signal is centered.

Troubleshooting (no decode / noisy decode):
- Confirm demod path is **USB/CW-compatible** and frequency is tuned correctly.
- If multiple SDRs are connected and the selected one has no PCM output, Morse startup now auto-tries other detected SDR devices and reports the active device/serial in status logs.
- Match **tone** and **bandwidth** to the actual sidetone/pitch.
- Try **Threshold Auto** first; if needed, switch to manual threshold and recalibrate.
- Use **Reset/Calibrate** after major frequency or band condition changes.
- Raise **Minimum Signal Gate** to suppress random noise keying.

---

## Installation / Debian / Ubuntu / macOS

### Quick Start

```bash
git clone https://github.com/smittix/intercept.git
cd intercept
./setup.sh          # Interactive menu (first run launches setup wizard)
sudo ./start.sh
```

On first run, `setup.sh` launches a **guided wizard** that detects your OS, lets you choose install profiles, sets up the Python environment, and optionally configures environment variables and PostgreSQL.

On subsequent runs, it opens an **interactive menu**:

```
INTERCEPT Setup Menu
════════════════════════════════════════
  1) Install / Add Modules
  2) System Health Check
  3) Database Setup (ADS-B History)
  4) Update Tools
  5) Environment Configurator
  6) Uninstall / Cleanup
  7) View Status
  0) Exit
```

> **Production vs Dev server:** `start.sh` auto-detects gunicorn + gevent and runs a production server with cooperative greenlets — handles multiple SSE/WebSocket clients without blocking. Falls back to Flask dev server if gunicorn is not installed. For quick local development, you can still use `sudo -E venv/bin/python intercept.py` directly.

### Install Profiles

Choose what to install during the wizard or via menu option 1:

| # | Profile | Tools |
|---|---------|-------|
| 1 | Core SIGINT | rtl_sdr, multimon-ng, rtl_433, dump1090, acarsdec, dumpvdl2, ffmpeg, gpsd |
| 2 | Maritime & Radio | AIS-catcher, direwolf |
| 3 | Weather & Space | SatDump, radiosonde_auto_rx |
| 4 | RF Security | aircrack-ng, HackRF, BlueZ, hcxtools, Ubertooth, SoapySDR |
| 5 | Full SIGINT | All of the above |
| 6 | Custom | Per-tool checklist |

Multiple profiles can be combined (e.g. enter `1 3` for Core + Weather).

### CLI Flags

```bash
./setup.sh --non-interactive          # Headless full install (same as legacy behavior)
./setup.sh --profile=core,weather     # Install specific profiles
./setup.sh --health-check             # Check system health and exit
./setup.sh --postgres-setup           # Run PostgreSQL setup and exit
./setup.sh --menu                     # Force interactive menu
```

### Docker

```bash
git clone https://github.com/smittix/intercept.git
cd intercept
docker compose --profile basic up -d --build
```

> **Note:** Docker requires privileged mode for USB SDR access. SDR devices are passed through via `/dev/bus/usb`.

#### Multi-Architecture Builds (amd64 + arm64)

Cross-compile on an x64 machine and push to a registry. This is much faster than building natively on an RPi.

```bash
# One-time setup on your x64 build machine
docker run --privileged --rm tonistiigi/binfmt --install all
docker buildx create --name intercept-builder --use --bootstrap

# Build and push for both architectures
REGISTRY=ghcr.io/youruser ./build-multiarch.sh --push

# On the RPi5, just pull and run
INTERCEPT_IMAGE=ghcr.io/youruser/intercept:latest docker compose --profile basic up -d
```

Build script options:

| Flag | Description |
|------|-------------|
| `--push` | Push to container registry |
| `--load` | Load into local Docker (single platform only) |
| `--arm64-only` | Build arm64 only (for RPi deployment) |
| `--amd64-only` | Build amd64 only |

Environment variables: `REGISTRY`, `IMAGE_NAME`, `IMAGE_TAG`

#### Using a Pre-built Image

If you've pushed to a registry, you can skip building entirely on the target machine:

```bash
# Set in .env or export
INTERCEPT_IMAGE=ghcr.io/youruser/intercept:latest

# Then just run
docker compose --profile basic up -d
```

### Environment Configuration

Use the **Environment Configurator** (menu option 5) to interactively set any `INTERCEPT_*` variable. Settings are saved to a `.env` file that `start.sh` sources automatically on startup.

You can also create or edit `.env` manually:

```bash
# .env (auto-loaded by start.sh)
INTERCEPT_PORT=5050
INTERCEPT_ADSB_AUTO_START=true
INTERCEPT_DEFAULT_LAT=51.5074
INTERCEPT_DEFAULT_LON=-0.1278
```

### ADS-B History (Optional)

The ADS-B history feature persists aircraft messages to PostgreSQL for long-term analysis.

**Automated setup (local install):**

```bash
./setup.sh --postgres-setup
# Or use menu option 3: Database Setup
```

This will install PostgreSQL if needed, create the database/user/tables, and write the connection settings to `.env`.

**Docker:**

```bash
docker compose --profile history up -d
```

Set the following environment variables (in `.env`):

```bash
INTERCEPT_ADSB_HISTORY_ENABLED=true
INTERCEPT_ADSB_DB_HOST=adsb_db
INTERCEPT_ADSB_DB_PORT=5432
INTERCEPT_ADSB_DB_NAME=intercept_adsb
INTERCEPT_ADSB_DB_USER=intercept
INTERCEPT_ADSB_DB_PASSWORD=intercept
```

To store Postgres data on external storage, set `PGDATA_PATH` (defaults to `./pgdata`):

```bash
PGDATA_PATH=/mnt/usbpi1/intercept/pgdata
```

Then open **/adsb/history** for the reporting dashboard.

### System Health Check

Verify your installation is complete and working:

```bash
./setup.sh --health-check
# Or use menu option 2
```

Checks installed tools, SDR devices, port availability, permissions, Python venv, `.env` configuration, and PostgreSQL connectivity.

### Open the Interface

After starting, open **http://localhost:5050** in your browser. The username and password is <b>admin</b>:<b>admin</b>

The credentials can be changed in the ADMIN_USERNAME & ADMIN_PASSWORD variables in config.py

---

## Hardware Requirements

| Hardware | Purpose | Price |
|----------|---------|-------|
| **RTL-SDR** | Required for all SDR features | ~$25-35 |
| **WiFi adapter** | Must support promiscuous (monitor) mode | ~$20-40 |
| **Bluetooth adapter** | Device scanning (usually built-in) | - |
| **GPS** | Any Linux supported GPS Unit | ~10 |

Most features work with a basic RTL-SDR dongle (RTL2832U + R820T2).

| :exclamation:  Not using an RTL-SDR Device?   |
|-----------------------------------------------
|Intercept supports any device that SoapySDR supports. You must however have the correct module for your device installed! For example if you have an SDRPlay device you'd need to install soapysdr-module-sdrplay.

| :exclamation:  GPS Usage   |
|-----------------------------------------------
|gpsd is needed for real time location. Intercept automatically checks to see if you're running gpsd in the background when any maps are rendered.

---

## Discord Server

<p align="center">
  <a href="https://discord.gg/EyeksEJmWE">Join our Discord</a>
</p>


---

## Documentation

- [Usage Guide](docs/USAGE.md) - Detailed instructions for each mode
- [Distributed Agents](docs/DISTRIBUTED_AGENTS.md) - Remote sensor node deployment
- [Hardware Guide](docs/HARDWARE.md) - SDR hardware and advanced setup
- [Troubleshooting](docs/TROUBLESHOOTING.md) - Common issues and solutions
- [Security](docs/SECURITY.md) - Network security and best practices

---

## Disclaimer

This project was developed using AI as a coding partner, combining human direction with AI-assisted implementation. The goal: make Software Defined Radio more accessible by providing a clean, unified interface for common SDR tools.

**This software is for educational and authorized testing purposes only.**

- Only use with proper authorization
- Intercepting communications without consent may be illegal
- You are responsible for compliance with applicable laws

---

## License

Apache 2.0 License - see [LICENSE](LICENSE)

## Author

Created by **smittix** - [GitHub](https://github.com/smittix)

## Acknowledgments

[rtl-sdr](https://osmocom.org/projects/rtl-sdr/wiki) |
[multimon-ng](https://github.com/EliasOenal/multimon-ng) |
[rtl_433](https://github.com/merbanan/rtl_433) |
[dump1090](https://github.com/flightaware/dump1090) |
[AIS-catcher](https://github.com/jvde-github/AIS-catcher) |
[acarsdec](https://github.com/TLeconte/acarsdec) |
[direwolf](https://github.com/wb2osz/direwolf) |
[rtlamr](https://github.com/bemasher/rtlamr) |
[dumpvdl2](https://github.com/szpajder/dumpvdl2) |
[aircrack-ng](https://www.aircrack-ng.org/) |
[Leaflet.js](https://leafletjs.com/) |
[SatDump](https://github.com/SatDump/SatDump) |
[Celestrak](https://celestrak.org/) |
[Priyom.org](https://priyom.org/)










