# INTERCEPT

<p align="center">
  <img src="https://img.shields.io/badge/python-3.7+-blue.svg" alt="Python 3.7+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg" alt="Platform">
  <img src="https://img.shields.io/badge/author-smittix-cyan.svg" alt="Author">
</p>

<p align="center">
  <strong>Signal Intelligence Platform</strong>
</p>

<p align="center">
  A sleek, modern web-based front-end for signal intelligence tools.<br>
  Unified interface for pager decoding, 433MHz sensors, ADS-B aircraft tracking, satellite monitoring, WiFi reconnaissance, and Bluetooth scanning.
</p>

## Screenshot
<img src="screenshot2.png">

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/smittix/intercept.git
cd intercept
pip install -r requirements.txt

# Run (sudo recommended for full functionality)
sudo python3 intercept.py
```

Open `http://localhost:5050` in your browser. See [Installation](#installation) for external tool setup.

---

## What is INTERCEPT?

INTERCEPT is a **web-based front-end** that provides a unified, modern interface for signal intelligence tools:

- **rtl_fm + multimon-ng** - For decoding POCSAG and FLEX pager signals
- **rtl_433** - For decoding 433MHz ISM band devices (weather stations, sensors, etc.)
- **dump1090 / rtl_adsb** - For ADS-B aircraft tracking with real-time map visualization
- **Satellite tracking** - Pass prediction and Iridium burst detection using TLE data
- **aircrack-ng** - For WiFi reconnaissance and network analysis
- **hcitool / bluetoothctl** - For Bluetooth device scanning and tracking

Instead of running command-line tools manually, INTERCEPT handles the process management, output parsing, and presents decoded data in a clean, real-time web interface.

---

## Features

### 📟 Pager Decoding
- **Real-time decoding** of POCSAG (512/1200/2400) and FLEX protocols
- **Customizable frequency presets** stored in browser
- **Auto-restart** on frequency change while decoding

### 📡 433MHz Sensor Decoding
- **200+ device protocols** supported via rtl_433
- **Weather stations** - temperature, humidity, wind, rain
- **TPMS** - Tire pressure monitoring sensors
- **Doorbells, remotes, and IoT devices**
- **Smart meters** and utility monitors

### ✈️ ADS-B Aircraft Tracking
- **Real-time aircraft tracking** via dump1090 or rtl_adsb
- **Full-screen dashboard** - dedicated popout with virtual radar scope
- **Interactive Leaflet map** with OpenStreetMap tiles (dark-themed)
- **Aircraft trails** - optional flight path history visualization
- **Range rings** - distance reference circles from observer position
- **Aircraft filtering** - show all, military only, civil only, or emergency only
- **Marker clustering** - group nearby aircraft at lower zoom levels
- **Reception statistics** - max range, message rate, busiest hour, total seen
- **Observer location** - manual input or GPS geolocation
- **Audio alerts** - notifications for military and emergency aircraft
- **Emergency squawk highlighting** - visual alerts for 7500/7600/7700
- **Aircraft details popup** - callsign, altitude, speed, heading, squawk, ICAO

### 🛰️ Satellite Tracking
- **Full-screen dashboard** - dedicated popout with polar plot and ground track
- **Polar sky plot** - real-time satellite positions on azimuth/elevation display
- **Ground track map** - satellite orbit path with past/future trajectory
- **Pass prediction** for satellites using TLE data
- **Add satellites** via manual TLE entry or Celestrak import
- **Celestrak integration** - fetch by category (Amateur, Weather, ISS, Starlink, etc.)
- **Next pass countdown** - time remaining, visibility duration, max elevation
- **Telemetry panel** - real-time azimuth, elevation, range, velocity
- **Iridium burst detection** monitoring (demo mode)
- **Multiple satellite tracking** simultaneously

### 📶 WiFi Reconnaissance
- **Monitor mode** management via airmon-ng
- **Network scanning** with airodump-ng and channel hopping
- **Handshake capture** with real-time status and auto-detection
- **Deauthentication attacks** for authorized testing
- **Channel utilization** visualization (2.4GHz and 5GHz)
- **Security overview** chart and real-time radar display
- **Client vendor lookup** via OUI database
- **Drone detection** - automatic detection via SSID patterns and OUI (DJI, Parrot, Autel, etc.)
- **Rogue AP detection** - alerts for same SSID on multiple BSSIDs
- **Signal history graph** - track signal strength over time for any device
- **Network topology** - visual map of APs and connected clients
- **Channel recommendation** - optimal channel suggestions based on congestion
- **Hidden SSID revealer** - captures hidden networks from probe requests
- **Client probe analysis** - privacy leak detection from probe requests
- **Device correlation** - matches WiFi and Bluetooth devices by manufacturer

### 🔵 Bluetooth Scanning
- **BLE and Classic** Bluetooth device scanning
- **Multiple scan modes** - hcitool, bluetoothctl
- **Tracker detection** - AirTag, Tile, Samsung SmartTag, Chipolo
- **Device classification** - phones, audio, wearables, computers
- **Manufacturer lookup** via OUI database
- **Proximity radar** visualization
- **Device type breakdown** chart

### 🔔 Browser Notifications
- **Desktop notifications** for critical events (even when tab is in background)
- **Alerts for**: Drone detection, Rogue APs, Handshake capture, Hidden SSID reveals
- **Permission requested** on first interaction

### ❓ Help System
- **Built-in help page** accessible via ? button in header
- **Icon legend** for all stats bar icons
- **Mode-by-mode guides** with tips and instructions
- **Keyboard shortcut**: Press Escape to close

### 🎨 User Interface
- **Mode-specific header stats** - real-time badges showing key metrics per mode
- **UTC clock** - always visible in header for time-critical operations
- **Active mode indicator** - shows current mode with pulse animation
- **Collapsible sections** - click any header to collapse/expand
- **Panel styling** - gradient backgrounds with indicator dots
- **Tabbed mode selector** with icons (grouped by SDR/RF and Wireless)
- **Consistent design** - unified styling across main dashboard and popouts
- **Dark/Light theme toggle** - click moon/sun icon in header, preference saved
- **Keyboard shortcuts** - F1 or ? to open help

### ⌨️ Keyboard Shortcuts
| Key | Action |
|-----|--------|
| F1 | Open help |
| ? | Open help (when not typing) |
| Escape | Close help/modals |

### General
- **Web-based interface** - no desktop app needed
- **Live message streaming** via Server-Sent Events (SSE)
- **Audio alerts** with mute toggle
- **Message export** to CSV/JSON
- **Signal activity meter** and waterfall display
- **Message logging** to file with timestamps
- **RTL-SDR device detection** and selection
- **Configurable gain and PPM correction**
- **Device intelligence** dashboard with tracking
- **Disclaimer acceptance** on first use
- **Auto-stop** when switching between modes

---

## Requirements

### Hardware
- RTL-SDR compatible dongle (RTL2832U based)
- WiFi adapter capable of monitor mode (for WiFi features)
- Bluetooth adapter (for Bluetooth features)

### Software
- Python 3.7+
- Flask, skyfield (installed via `requirements.txt`)
- rtl-sdr tools (`rtl_fm`)
- multimon-ng (for pager decoding)
- rtl_433 (for 433MHz sensor decoding)
- dump1090 or rtl_adsb (for ADS-B aircraft tracking)
- aircrack-ng (for WiFi reconnaissance)
- BlueZ tools - hcitool, bluetoothctl (for Bluetooth)

## Installation

### Install external tools

Install the tools for the features you need:

| Tool | macOS | Ubuntu/Debian | Purpose |
|------|-------|---------------|---------|
| rtl-sdr | `brew install rtl-sdr` | `sudo apt install rtl-sdr` | Required for all SDR features |
| multimon-ng | `brew install multimon-ng` | `sudo apt install multimon-ng` | Pager decoding |
| rtl_433 | `brew install rtl_433` | `sudo apt install rtl-433` | 433MHz sensors |
| dump1090 | `brew install dump1090-mutability` | `sudo apt install dump1090-mutability` | ADS-B aircraft |
| aircrack-ng | `brew install aircrack-ng` | `sudo apt install aircrack-ng` | WiFi reconnaissance |
| bluez | Built-in (limited) | `sudo apt install bluez bluetooth` | Bluetooth scanning |

### Install and run

```bash
git clone https://github.com/smittix/intercept.git
cd intercept
pip install -r requirements.txt
sudo python3 intercept.py
```

Open `http://localhost:5050` in your browser.

> **Note:** Running as root/sudo is recommended for full functionality (monitor mode, raw sockets, etc.)

### Command-line options

```
python3 intercept.py --help

  -p, --port PORT    Port to run server on (default: 5050)
  -H, --host HOST    Host to bind to (default: 0.0.0.0)
  -d, --debug        Enable debug mode
  --check-deps       Check dependencies and exit
```

---

## Usage

### Pager Mode
1. **Select Device** - Choose your RTL-SDR device from the dropdown
2. **Set Frequency** - Enter a frequency in MHz or use a preset
3. **Choose Protocols** - Select which protocols to decode (POCSAG/FLEX)
4. **Adjust Settings** - Set gain, squelch, and PPM correction as needed
5. **Start Decoding** - Click the green "Start Decoding" button

### WiFi Mode
1. **Select Interface** - Choose a WiFi adapter capable of monitor mode
2. **Enable Monitor Mode** - Click "Enable Monitor" (uncheck "Kill processes" to preserve other connections)
3. **Start Scanning** - Click "Start Scanning" to begin
4. **View Networks** - Networks appear in the output panel with signal strength
5. **Track Devices** - Click 📈 on any network to track its signal over time
6. **Capture Handshakes** - Click "Capture" on a network to start handshake capture

### Bluetooth Mode
1. **Select Interface** - Choose your Bluetooth adapter
2. **Choose Mode** - Select scan mode (hcitool, bluetoothctl)
3. **Start Scanning** - Click "Start Scanning"
4. **View Devices** - Devices appear with name, address, and classification

### Aircraft Mode
1. **Check Tools** - Ensure dump1090 or rtl_adsb is installed
2. **Set Location** - Enter observer coordinates or click "Use GPS Location"
3. **Start Tracking** - Click "Start Tracking" to begin ADS-B reception
4. **View Map** - Aircraft appear on the interactive Leaflet map
5. **Click Aircraft** - Click markers for detailed information
6. **Display Options** - Toggle callsigns, altitude, trails, range rings, clustering
7. **Filter Aircraft** - Use dropdown to show all, military, civil, or emergency only
8. **Full Dashboard** - Click "Full Screen Dashboard" for dedicated radar view

### Satellite Mode
1. **Set Location** - Enter observer coordinates or click "Use My Location"
2. **Add Satellites** - Click "Add Satellite" to enter TLE data or fetch from Celestrak
3. **Calculate Passes** - Click "Calculate Passes" to predict upcoming passes
4. **View Sky Plot** - Polar plot shows satellite positions in real-time
5. **Ground Track** - Map displays satellite orbit path and current position
6. **Full Dashboard** - Click "Full Screen Dashboard" for dedicated satellite view
7. **Iridium Mode** - Switch tabs to monitor for Iridium burst transmissions

### Frequency Presets

- Click a preset button to quickly set a frequency
- Add custom presets using the input field and "Add" button
- Right-click a preset to remove it
- Click "Reset to Defaults" to restore default frequencies

---

## Troubleshooting

### No devices found
- Ensure your RTL-SDR is plugged in
- Check `rtl_test` works from command line
- On Linux, you may need to blacklist the DVB-T driver

### No messages appearing
- Verify the frequency is correct for your area
- Adjust the gain (try 30-40 dB)
- Check that pager services are active in your area
- Ensure antenna is connected

### WiFi monitor mode fails
- Ensure you're running as root/sudo
- Check your adapter supports monitor mode: `iw list | grep monitor`
- Try: `airmon-ng check kill` to stop interfering processes

### Device busy error
- Click "Kill All Processes" to stop any stale processes
- Unplug and replug the RTL-SDR device

---

## Configuration

INTERCEPT can be configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERCEPT_HOST` | `0.0.0.0` | Server bind address |
| `INTERCEPT_PORT` | `5050` | Server port |
| `INTERCEPT_DEBUG` | `false` | Enable debug mode |
| `INTERCEPT_LOG_LEVEL` | `WARNING` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `INTERCEPT_DEFAULT_GAIN` | `40` | Default RTL-SDR gain |

Example: `INTERCEPT_PORT=8080 sudo python3 intercept.py`

---

## License

MIT License - see [LICENSE](LICENSE) for details.

## Author

Created by **smittix** - [GitHub](https://github.com/smittix)

## Acknowledgments

- [rtl-sdr](https://osmocom.org/projects/rtl-sdr/wiki) - RTL-SDR drivers
- [multimon-ng](https://github.com/EliasOenal/multimon-ng) - Multi-protocol pager decoder
- [rtl_433](https://github.com/merbanan/rtl_433) - 433MHz sensor decoder
- [dump1090](https://github.com/flightaware/dump1090) - ADS-B decoder for aircraft tracking
- [aircrack-ng](https://www.aircrack-ng.org/) - WiFi security auditing tools
- [BlueZ](http://www.bluez.org/) - Official Linux Bluetooth protocol stack
- [Leaflet.js](https://leafletjs.com/) - Interactive maps for aircraft tracking
- [OpenStreetMap](https://www.openstreetmap.org/) - Map tile data
- [Celestrak](https://celestrak.org/) - Satellite TLE data
- Inspired by the SpaceX mission control aesthetic

---

## ⚠️ Disclaimer

**This software is for educational purposes only and intended for use by cybersecurity professionals in controlled environments.**

By using INTERCEPT, you acknowledge that:
- You will only use this tool with proper authorization
- Intercepting communications without consent may be illegal in your jurisdiction
- WiFi deauthentication and Bluetooth attacks should only be performed on networks/devices you own or have explicit permission to test
- You are solely responsible for ensuring compliance with all applicable laws and regulations
- The developers assume no liability for misuse of this software

A disclaimer must be accepted when first launching the application.
