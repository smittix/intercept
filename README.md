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
<img src="screenshot.png">

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
- **Interactive Leaflet map** with OpenStreetMap tiles
- **Dark-themed map** matching application aesthetic
- **Aircraft details** - callsign, altitude, speed, heading, squawk
- **Click aircraft markers** for detailed popup information
- **Auto-fit view** to show all tracked aircraft
- **Emergency aircraft highlighting** in red

### 🛰️ Satellite Tracking
- **Pass prediction** for satellites using TLE data
- **Add satellites** via manual TLE entry or Celestrak import
- **Celestrak integration** - fetch satellites by category (Amateur, Weather, ISS, Starlink, etc.)
- **Iridium burst detection** monitoring
- **Next pass countdown** with elevation and duration
- **Multiple satellite tracking** simultaneously

### 📶 WiFi Reconnaissance
- **Monitor mode** management via airmon-ng
- **Network scanning** with airodump-ng
- **Channel hopping** or fixed channel monitoring
- **Deauthentication attacks** for authorized testing
- **Handshake capture** with real-time status and auto-detection
- **Channel utilization** visualization (2.4GHz and 5GHz)
- **Security overview** chart (WPA3/WPA2/WEP/Open)
- **Real-time radar** display of nearby networks
- **Client vendor lookup** via OUI database
- **Proximity alerts** - watch list for specific MAC addresses

#### 🚁 Drone Detection
- **Automatic detection** of drones via SSID patterns and manufacturer OUI
- **Supported brands**: DJI, Parrot, Autel, Skydio, Holy Stone, and many more
- **Distance estimation** from signal strength
- **Visual alerts** with triple audio notification
- **Clickable drone counter** - view all detected drones with details

#### ⚠️ Rogue AP Detection
- **Automatic detection** of same SSID on multiple BSSIDs
- **Clickable counter** - view which SSIDs triggered alerts
- **Detailed popup** showing all BSSIDs, channels, and signal strength

#### 📈 Signal History Graph
- **Real-time line chart** showing signal strength over time
- **Track any device** - click the 📈 button on any network
- **Visual movement detection** - see devices approaching or departing

#### 🕸️ Network Topology Graph
- **Visual map** of all access points and connected clients
- **Color-coded nodes** - cyan for APs, green for clients, orange for drones
- **Auto-updating** as new devices are discovered

#### 💡 Channel Recommendation
- **Automatic analysis** of channel congestion
- **Recommends optimal channels** for both 2.4GHz and 5GHz
- **Considers channel overlap** for accurate 2.4GHz recommendations

#### 👁️ Hidden SSID Revealer
- **Captures hidden SSIDs** from probe requests
- **Displays revealed networks** with BSSID mapping
- **Desktop notifications** when new hidden SSIDs are revealed

#### 🔗 Device Correlation
- **Matches WiFi and Bluetooth devices** with same manufacturer
- **OUI-based correlation** to identify multi-radio devices
- **Useful for tracking** devices across protocols

#### 📡 Client Probe Analysis
- **Track client probe requests** - see what networks devices are looking for
- **Privacy leak detection** - highlights sensitive network names (home, office, hotel, airport)
- **Vendor identification** - shows device manufacturer
- **Sorted by exposure** - most revealing clients shown first
- **Unique SSID counter** - total unique networks being probed

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
- **Collapsible sections** - click any header to collapse/expand
- **Icon-based stats bar** with tooltips
- **Tabbed mode selector** with icons (grouped by SDR/RF and Wireless)
- **Compact, modern design** with consistent styling
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

## Stats Bar Icons

| Icon | Meaning |
|------|---------|
| 📟 | POCSAG messages decoded |
| 📠 | FLEX messages decoded |
| 📨 | Total messages received |
| 🌡️ | Unique sensors detected |
| 📊 | Device types found |
| ✈️ | Aircraft being tracked |
| 🛰️ | Satellites being monitored |
| 📡 | WiFi Access Points |
| 👤 | Connected WiFi clients |
| 🤝 | Captured handshakes |
| 🚁 | Detected drones (click for details) |
| ⚠️ | Rogue APs (click for details) |
| 🔵 | Bluetooth devices |
| 📍 | BLE beacons detected |

---

## Requirements

### Hardware
- RTL-SDR compatible dongle (RTL2832U based)
- WiFi adapter capable of monitor mode (for WiFi features)
- Bluetooth adapter (for Bluetooth features)

### Software
- Python 3.7+
- Flask
- requests (for Celestrak API)
- rtl-sdr tools (`rtl_fm`)
- multimon-ng (for pager decoding)
- rtl_433 (for 433MHz sensor decoding)
- dump1090 or rtl_adsb (for ADS-B aircraft tracking)
- aircrack-ng (for WiFi reconnaissance)
- BlueZ tools - hcitool, bluetoothctl (for Bluetooth)

## Installation

### 1. Install RTL-SDR tools

**macOS (Homebrew):**
```bash
brew install rtl-sdr
```

**Ubuntu/Debian:**
```bash
sudo apt-get install rtl-sdr
```

**Arch Linux:**
```bash
sudo pacman -S rtl-sdr
```

### 2. Install multimon-ng

**macOS (Homebrew):**
```bash
brew install multimon-ng
```

**Ubuntu/Debian:**
```bash
sudo apt-get install multimon-ng
```

**From source:**
```bash
git clone https://github.com/EliasOenal/multimon-ng.git
cd multimon-ng
mkdir build && cd build
cmake ..
make
sudo make install
```

### 3. Install rtl_433 (optional, for 433MHz sensors)

**macOS (Homebrew):**
```bash
brew install rtl_433
```

**Ubuntu/Debian:**
```bash
sudo apt-get install rtl-433
```

**From source:**
```bash
git clone https://github.com/merbanan/rtl_433.git
cd rtl_433
mkdir build && cd build
cmake ..
make
sudo make install
```

### 4. Install aircrack-ng (optional, for WiFi)

**macOS (Homebrew):**
```bash
brew install aircrack-ng
```

**Ubuntu/Debian:**
```bash
sudo apt-get install aircrack-ng
```

### 5. Install dump1090 (optional, for ADS-B aircraft tracking)

**macOS (Homebrew):**
```bash
brew install dump1090-mutability
```

**Ubuntu/Debian:**
```bash
sudo apt-get install dump1090-mutability
```

**From source:**
```bash
git clone https://github.com/flightaware/dump1090.git
cd dump1090
make
sudo cp dump1090 /usr/local/bin/
```

### 6. Install Bluetooth tools (optional)

**Ubuntu/Debian:**
```bash
sudo apt-get install bluez bluetooth
```

**macOS:**
Bluetooth tools are built-in, though with limited functionality compared to Linux.

### 7. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 8. Clone and run

```bash
git clone https://github.com/smittix/intercept.git
cd intercept
sudo python3 intercept.py
```

Open your browser to `http://localhost:5050`

> **Note:** Running as root/sudo is recommended for full functionality (monitor mode, raw sockets, etc.)

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
2. **Start Tracking** - Click "Start Tracking" to begin ADS-B reception
3. **View Map** - Aircraft appear on the interactive Leaflet map
4. **Click Aircraft** - Click markers for detailed information (altitude, speed, heading)
5. **Toggle Labels** - Use checkboxes to show/hide callsigns and flight levels

### Satellite Mode
1. **Add Satellites** - Click "Add Satellite" to enter TLE data manually, or use "Celestrak" to fetch by category
2. **Select Category** - Choose from Amateur, Weather, ISS, Starlink, GPS, etc.
3. **View Passes** - Next pass predictions shown with elevation and duration
4. **Track Multiple** - Add multiple satellites to track simultaneously
5. **Iridium Bursts** - Monitor for Iridium satellite burst transmissions

### Frequency Presets

- Click a preset button to quickly set a frequency
- Add custom presets using the input field and "Add" button
- Right-click a preset to remove it
- Click "Reset to Defaults" to restore default frequencies

---

## API Endpoints

### Pager & Sensor
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main web interface |
| `/devices` | GET | List RTL-SDR devices |
| `/start` | POST | Start pager decoding |
| `/stop` | POST | Stop pager decoding |
| `/start_sensor` | POST | Start 433MHz sensor listening |
| `/stop_sensor` | POST | Stop 433MHz sensor listening |
| `/status` | GET | Get decoder status |
| `/stream` | GET | SSE stream for pager messages |
| `/stream_sensor` | GET | SSE stream for sensor data |

### WiFi
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/wifi/interfaces` | GET | List WiFi interfaces and tools |
| `/wifi/monitor` | POST | Enable/disable monitor mode |
| `/wifi/scan/start` | POST | Start WiFi scanning |
| `/wifi/scan/stop` | POST | Stop WiFi scanning |
| `/wifi/deauth` | POST | Send deauthentication packets |
| `/wifi/handshake/capture` | POST | Start handshake capture |
| `/wifi/handshake/status` | POST | Check handshake capture status |
| `/wifi/networks` | GET | Get discovered networks |
| `/wifi/stream` | GET | SSE stream for WiFi events |

### Bluetooth
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/bt/interfaces` | GET | List Bluetooth interfaces and tools |
| `/bt/scan/start` | POST | Start Bluetooth scanning |
| `/bt/scan/stop` | POST | Stop Bluetooth scanning |
| `/bt/enum` | POST | Enumerate device services |
| `/bt/devices` | GET | Get discovered devices |
| `/bt/stream` | GET | SSE stream for Bluetooth events |

### Aircraft (ADS-B)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/adsb/start` | POST | Start ADS-B tracking |
| `/adsb/stop` | POST | Stop ADS-B tracking |
| `/adsb/aircraft` | GET | Get tracked aircraft |
| `/adsb/stream` | GET | SSE stream for aircraft data |
| `/adsb/tools` | GET | Check ADS-B tool availability |

### Satellite
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/satellite/add` | POST | Add satellite with TLE data |
| `/satellite/remove` | POST | Remove satellite from tracking |
| `/satellite/list` | GET | Get tracked satellites |
| `/satellite/passes` | GET | Get pass predictions |
| `/satellite/celestrak/<category>` | GET | Fetch satellites from Celestrak |

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
