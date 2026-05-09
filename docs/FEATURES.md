# INTERCEPT Features

Complete feature list for all modules.

## Pager Decoding

- **Real-time decoding** of POCSAG (512/1200/2400) and FLEX protocols
- **Customizable frequency presets** stored in browser
- **Auto-restart** on frequency change while decoding

## 433MHz Sensor Decoding

- **200+ device protocols** supported via rtl_433
- **Weather stations** - temperature, humidity, wind, rain
- **TPMS** - Tire pressure monitoring sensors
- **Doorbells, remotes, and IoT devices**
- **Smart meters** and utility monitors

## Sub-GHz Analyzer

- **HackRF-based** signal capture and analysis for 300-928 MHz ISM bands
- **Protocol decoding** - identify and decode common Sub-GHz protocols
- **Signal replay/transmit** capabilities for authorized testing
- **Wideband spectrum analysis** with real-time visualization
- **I/Q capture** - record raw samples for offline analysis

## Spy Stations (Number Stations)

- **Comprehensive database** of active number stations and diplomatic networks
- **Station profiles** - frequencies, schedules, operators, descriptions
- **Filter by type** - number stations vs diplomatic networks
- **Filter by country** - Russia, Cuba, Israel, Poland, North Korea, etc.
- **Filter by mode** - USB, AM, CW, OFDM
- **Tune integration** - click to tune Listening Post to station frequency
- **Source links** - references to priyom.org for detailed information
- **Famous stations** - UVB-76 "The Buzzer", Cuban HM01, Israeli E17z

## ADS-B Aircraft Tracking

- **Real-time aircraft tracking** via dump1090 or rtl_adsb
- **Full-screen dashboard** - dedicated popout with virtual radar scope
- **Interactive Leaflet map** with OpenStreetMap tiles (dark-themed)
- **Aircraft trails** - optional flight path history visualization
- **Range rings** - distance reference circles from observer position
- **Aircraft filtering** - show all, military only, civil only, or emergency only
- **Marker clustering** - group nearby aircraft at lower zoom levels
- **Reception statistics** - max range, message rate, busiest hour, total seen
- **Persistent ADS-B history** - SQLite (default, no install required) or optional PostgreSQL storage for message and snapshot history
- **History reporting dashboard** - session controls, aircraft timelines, and detail modal
- **Observer location** - manual input or GPS geolocation
- **Audio alerts** - notifications for military and emergency aircraft
- **Emergency squawk highlighting** - visual alerts for 7500/7600/7700
- **Aircraft details popup** - callsign, altitude, speed, heading, squawk, ICAO
- **PPM frequency correction** - input field auto-hides when HackRF is selected (HackRF does not support software PPM)

<p align="center">
  <img src="/static/images/screenshots/screenshot_radar.png" alt="Screenshot">
</p>

## AIS Vessel Tracking

- **Real-time vessel tracking** via AIS-catcher or rtl_ais
- **Full-screen dashboard** - dedicated popout with maritime map
- **Interactive Leaflet map** with OpenStreetMap tiles (dark-themed)
- **Vessel trails** - optional track history visualization
- **Vessel details popup** - name, MMSI, callsign, destination, ship type, speed, heading
- **Country identification** - flag lookup via Maritime Identification Digits (MID)
- **PPM frequency correction** - per-session correction for RTL-SDR frequency drift; auto-hides for HackRF
- **Multi-SDR command routing** - correct AIS-catcher flags used automatically per device type (RTL-SDR vs SoapySDR)

### VHF DSC Channel 70 Monitoring

Digital Selective Calling (DSC) monitoring on the international maritime distress frequency.

- **Real-time DSC decoding** - Distress, Urgency, Safety, and Routine messages
- **MMSI country lookup** - 180+ Maritime Identification Digit codes
- **Distress nature identification** - Fire, Flooding, Collision, Sinking, Piracy, MOB, etc.
- **Position extraction** - Automatic lat/lon parsing from distress messages
- **Map markers** - Distress positions plotted with pulsing alert markers
- **Visual alert overlay** - Prominent popup for DISTRESS and URGENCY messages
- **Audio alerts** - Notification sound for critical messages
- **Alert persistence** - Critical alerts stored permanently in database
- **Acknowledgement workflow** - Track response status with notes
- **SDR conflict detection** - Prevents device collisions with AIS tracking
- **Alert summary** - Dashboard counts for unacknowledged distress/urgency

## ACARS Messaging

- **Real-time ACARS decoding** via acarsdec
- **Aircraft datalink messages** - operational, weather, and position reports
- **Multi-SDR support** - RTL-SDR, HackRF, LimeSDR, Airspy, SDRplay
- **Message filtering** - filter by message type, flight, or registration
- **Bias-T support** - global Bias-T toggle activates RTL-SDR Blog bias-T power before acarsdec starts (for active antennas and LNAs)

## VDL2 (VHF Data Link Mode 2)

- **Real-time VDL2 decoding** via dumpvdl2 on standard VDL2 frequencies
- **ACARS-over-AVLC** message capture with full frame parsing
- **Signal analysis** - frequency, signal level, noise level, SNR, burst length
- **AVLC frame details** - source/destination addresses, frame type, command/response
- **Raw JSON inspection** - expandable raw message data for each frame
- **Multi-frequency monitoring** - simultaneous reception on multiple VDL2 channels
- **Multi-SDR support** - RTL-SDR, HackRF, LimeSDR, Airspy, SDRplay
- **CSV/JSON export** - export captured messages for offline analysis
- **Integrated with ADS-B dashboard** - VDL2 messages linked to aircraft tracking

## CW/Morse Code Decoder

- **Custom Goertzel tone detection** for CW (continuous wave) Morse decoding
- **OOK/AM envelope detection** mode for on-off keying signals in ISM bands
- **HF frequency presets** for amateur CW bands (160m-10m)
- **ISM band presets** for OOK envelope mode (315 MHz, 433 MHz, 868 MHz, 915 MHz)
- **Real-time character and word output** with WPM estimation
- **Multi-SDR support** - RTL-SDR, HackRF, LimeSDR, Airspy, SDRplay

## WeFax (Weather Fax)

- **HF weather fax reception** from marine and meteorological broadcast stations
- **Broadcast timeline** with scheduled transmission times by station
- **Auto-scheduler** for unattended capture of scheduled broadcasts
- **Image gallery** with timestamped decoded weather charts
- **Station presets** for major WeFax broadcasters worldwide
- **Multi-SDR support** - RTL-SDR, HackRF, LimeSDR, Airspy, SDRplay

## Listening Post

- **Wideband frequency scanning** via rtl_power sweep with SNR filtering
- **Real-time audio monitoring** with FM and SSB demodulation
- **Cross-module frequency routing** from scanner to decoders
- **Waterfall spectrum display** for visual signal identification
- **Customizable frequency presets** and band bookmarks
- **Multi-SDR support** - RTL-SDR, LimeSDR, HackRF, Airspy, SDRplay

## Weather Satellites

- **NOAA APT** and **Meteor LRPT** image decoding via SatDump
- **Auto-scheduler** with pass prediction and automatic capture
- **Polar plot** - real-time satellite position on azimuth/elevation display
- **Ground track map** - orbit path with past/future trajectory
- **Image gallery** with timestamped decoded imagery

## WebSDR

- **KiwiSDR network integration** for remote HF/shortwave listening
- **WebSocket audio streaming** from remote receivers
- **Receiver discovery** with automatic caching
- **Frequency tuning** with band presets

## ISS SSTV

- **ISS SSTV image reception** on 145.800 MHz FM during special event transmissions
- **Real-time ISS tracking** with world map and pass predictions
- **Doppler correction** - optional lat/lon input for real-time frequency shift compensation
- **Next pass countdown** - time remaining until ISS is overhead
- **Image gallery** with timestamped decoded imagery
- **TLE updates** - fetch latest ISS orbital elements
- **Multi-SDR support** - RTL-SDR, HackRF, LimeSDR, Airspy, SDRplay

## HF SSTV

- **Terrestrial SSTV decoding** across HF (80m-10m), VHF (6m, 2m), and UHF (70cm) bands
- **Predefined frequency lookup** for 13 active SSTV calling frequencies
- **Auto-modulation selection** - frequency table maps to correct mode (USB, LSB, FM)
- **Image gallery** with decoded transmissions
- **Common modes supported** - PD120, PD180, Martin1, Scottie1, Robot36

## APRS

- **Amateur packet radio** position reports and telemetry via direwolf
- **Region-specific frequencies** - 144.390 MHz (North America), 144.800 MHz (Europe), and more
- **Real-time position tracking** on interactive map
- **Message and telemetry display** from APRS network

## Utility Meter Reading

- **Smart meter monitoring** via rtl_amr for electric, gas, and water meters
- **Real-time JSON output** with meter ID, consumption, and signal data
- **Multiple meter protocol support** via rtl_tcp integration

## Space Weather

- **Real-time solar indices** - Solar Flux Index (SFI), Kp index, A-index, sunspot number
- **NOAA Space Weather Scales** - Geomagnetic storms (G), solar radiation (S), radio blackouts (R)
- **HF band conditions** - Day/night propagation from HamQSL for 80m through 10m bands
- **Solar wind monitoring** - Speed, density, and IMF Bz from DSCOVR satellite
- **X-ray flux chart** - GOES X-ray data with flare class scale (A/B/C/M/X)
- **Flare probability** - 1-day and 3-day C/M/X-class flare forecasts
- **Solar imagery** - NASA SDO 193A, 304A, and magnetogram images
- **D-RAP absorption maps** - HF radio absorption at 5-30 MHz frequency bands
- **Aurora forecast** - OVATION aurora oval visualization
- **SWPC alerts** - Real-time space weather alerts and warnings
- **Active solar regions** - Current sunspot region data with location and area
- **Auto-refresh** - 5-minute polling with manual refresh option
- **No SDR required** - Data fetched from NOAA SWPC, NASA SDO, and HamQSL public APIs

## Radiosonde Weather Balloon Tracking

- **400-406 MHz reception** via radiosonde_auto_rx for weather balloon telemetry
- **Frequency presets** for common radiosonde bands
- **Real-time telemetry** - altitude, temperature, humidity, pressure, GPS position
- **Interactive map** with balloon trajectory and burst point prediction
- **Station location** with configurable observer position
- **Distance tracking** - real-time distance-to-balloon calculation
- **Multi-SDR support** - RTL-SDR, HackRF, LimeSDR, Airspy, SDRplay

## Satellite Tracking

- **Full-screen dashboard** - dedicated popout with polar plot and ground track
- **Polar sky plot** - real-time satellite positions on azimuth/elevation display
- **Ground track map** - satellite orbit path with past/future trajectory
- **Pass prediction** for satellites using TLE data
- **Add satellites** via manual TLE entry or Celestrak import
- **Celestrak integration** - fetch by category (Amateur, Weather, ISS, Starlink, etc.)
- **Next pass countdown** - time remaining, visibility duration, max elevation
- **Telemetry panel** - real-time azimuth, elevation, range, velocity
- **Multiple satellite tracking** simultaneously

<p align="center">
  <img src="/static/images/screenshots/screenshot_sat.png" alt="Screenshot">
</p>
<p align="center">
  <img src="/static/images/screenshots/screenshot_sat_2.png" alt="Screenshot">
</p>

## WiFi Reconnaissance

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

## Bluetooth Scanning

- **BLE and Classic** Bluetooth device scanning
- **Multiple scan modes** - hcitool, bluetoothctl, bleak
- **Tracker detection** - AirTag, Tile, Samsung SmartTag, Chipolo
- **Device classification** - phones, audio, wearables, computers
- **Manufacturer lookup** via OUI database and Bluetooth Company IDs
- **Proximity radar** visualization
- **Device type breakdown** chart

## BT Locate (SAR Bluetooth Device Location)

Search and rescue Bluetooth device location with GPS-tagged signal trail mapping.

### Core Features
- **Target tracking** - Locate devices by MAC address, name pattern, or IRK (Identity Resolving Key)
- **RPA resolution** - Resolve BLE Resolvable Private Addresses using IRK for tracking devices with randomized addresses
- **IRK auto-detection** - Extract IRKs from paired devices on macOS and Linux
- **GPS-tagged signal trail** - Every detection is tagged with GPS coordinates for trail mapping
- **Proximity bands** - IMMEDIATE (<1m), NEAR (1-5m), FAR (>5m) with color-coded HUD
- **RSSI history chart** - Real-time signal strength sparkline for trend analysis
- **Distance estimation** - Log-distance path loss model with environment presets
- **Audio proximity alerts** - Web Audio API tones that increase in pitch as signal strengthens
- **Hand-off from Bluetooth mode** - One-click transfer of a device from BT scanner to BT Locate

### Environment Presets
- **Open Field** (n=2.0) - Free space path loss
- **Outdoor** (n=2.2) - Typical outdoor environment
- **Indoor** (n=3.0) - Indoor with walls and obstacles

### Map & Trail
- Interactive Leaflet map with GPS trail visualization
- Trail points color-coded by proximity band
- Polyline connecting detection points for path visualization
- Supports user-configured tile providers

### Requirements
- Bluetooth adapter (built-in or USB)
- GPS receiver (optional, falls back to manual coordinates)

## WiFi Locate

Locate a WiFi access point by BSSID using real-time signal strength tracking.

### Core Features
- **Target by BSSID** - Enter any MAC address or hand off from the WiFi scanner
- **Real-time signal meter** - Large dBm display with color-coded strength (good/medium/weak)
- **20-segment signal bar** - Visual proximity indicator with red/yellow/green segments
- **RSSI history chart** - Canvas sparkline showing signal trend over time
- **Distance estimation** - Log-distance path loss model with configurable environment presets
- **Audio proximity alerts** - Web Audio API tones that increase in pitch and frequency as signal strengthens
- **Signal lost detection** - 30-second timeout with visual overlay when target disappears
- **Hand-off from WiFi mode** - One-click transfer from WiFi detail drawer to WiFi Locate
- **Stats tracking** - Current, min, max, and average RSSI across session

### Environment Presets
- **Open Field** (n=2.0) - Free space path loss
- **Outdoor** (n=2.8) - Typical outdoor environment (default)
- **Indoor** (n=3.5) - Indoor with walls and obstacles

### Mode Transition
- WiFi scan is preserved when switching between WiFi and WiFi Locate modes
- Deep scan auto-starts if not already running

### Requirements
- WiFi adapter capable of monitor mode
- aircrack-ng suite for deep scanning

## GPS Mode

Real-time GPS position tracking with live map visualization.

### Features
- **Live position tracking** - Real-time latitude, longitude, altitude display
- **Interactive map** - Current position on Leaflet map with track history
- **Speed and heading** - Real-time speed (km/h) and compass heading
- **Satellite info** - Number of satellites in view and fix quality
- **Track recording** - Record GPS tracks with export capability
- **Accuracy display** - Horizontal and vertical position accuracy (EPX/EPY)

### Requirements
- USB GPS receiver connected via gpsd
- gpsd daemon running (`sudo gpsd /dev/ttyUSB0 -F /var/run/gpsd.sock`)

## TSCM Counter-Surveillance Mode

Technical Surveillance Countermeasures (TSCM) screening for detecting wireless surveillance indicators.

### Wireless Sweep Features
- **BLE scanning** with manufacturer data detection (AirTags, Tile, SmartTags, ESP32)
- **WiFi scanning** for rogue APs, hidden SSIDs, camera devices
- **RF spectrum analysis** (RTL-SDR or HackRF) - FM bugs, ISM bands, video transmitters
- **Cross-protocol correlation** - links devices across BLE/WiFi/RF
- **Baseline comparison** - detect new/unknown devices vs known environment

### MAC-Randomization Resistant Detection
- **Device fingerprinting** based on advertisement payloads, not MAC addresses
- **Behavioral clustering** - groups observations into probable physical devices
- **Session tracking** - monitors device presence windows
- **Timing pattern analysis** - detects characteristic advertising intervals
- **RSSI trajectory correlation** - identifies co-located devices

### Risk Assessment
- **Three-tier scoring model**:
  - Informational (0-2): Known or expected devices
  - Needs Review (3-5): Unusual devices requiring assessment
  - High Interest (6+): Multiple indicators warrant investigation
- **Risk indicators**: Stable RSSI, audio-capable, ESP32 chipsets, hidden identity, MAC rotation
- **Audit trail** - full evidence chain for each link/flag
- **Client-safe disclaimers** - findings are indicators, not confirmed surveillance

### Limitations (Documented)
- Cannot detect non-transmitting devices
- False positives/negatives expected
- Results require professional verification
- No cryptographic de-randomization
- Passive screening only (no active probing by default)

## Drone Intelligence

Multi-vector UAV detection and identification system combining three complementary detection methods into unified contact tracking.

### Detection Vectors

- **Remote ID (WiFi/BLE)** — Parses ASTM F3411-22a broadcast frames from WiFi Beacon and BLE Advertisement packets. Extracts drone ID, operator ID, drone type, GPS position, altitude, speed, and emergency status. Mandatory for all drones >250g in the US/EU since 2023.
- **RTL-SDR RF (433/868 MHz)** — Monitors ISM bands for control link and telemetry signals characteristic of consumer and FPV drones. Detects DJI OcuSync, FrSky, FlySky, and generic FSK/GFSK drone control protocols.
- **HackRF (2.4/5.8 GHz)** — Wide-scan of video downlink and telemetry bands used by most consumer drones. Detects power above noise floor across 2.400–2.483 GHz and 5.725–5.875 GHz ISM bands.

### Contact Correlation

The `DroneCorrelator` merges raw observations from all three vectors into unified `DroneContact` objects:
- **TTL-based store** — contacts expire after 120 seconds of no activity
- **Multi-vector fusion** — a single contact can be seen on 1–3 vectors simultaneously
- **Deduplication** — observations from the same vector within 5 seconds are collapsed

### Risk Scoring

| Level | Criteria |
|-------|----------|
| High | No Remote ID broadcast (non-compliant) or ASTM non-conformant frame |
| Medium | Multiple detection vectors active, or RSSI delta >15 dB between vectors |
| Low | Compliant Remote ID present, single detection vector |

### Live Map

Remote ID contacts with GPS position data are plotted on a Leaflet map. Markers show drone ID and last known coordinates. Map updates in real time via SSE.

### Requirements

- WiFi adapter capable of monitor mode (for BLE/WiFi Remote ID)
- RTL-SDR dongle (for 433/868 MHz RF detection)
- HackRF One (optional, for 2.4/5.8 GHz detection)
- Python package: `opendroneid>=1.0`

## Meshtastic Mesh Networks

Integration with Meshtastic LoRa mesh networking devices for decentralized communication.

### Device Support
- **Heltec** - LoRa32 series
- **T-Beam** - TTGO T-Beam with GPS
- **RAK** - WisBlock series
- Any Meshtastic-compatible device via USB/Serial

### Features
- **Real-time messaging** - Stream messages as they arrive
- **Channel configuration** - Set encryption keys and channel names
- **Node information** - View connected nodes with signal metrics
- **Message history** - Up to 500 messages retained
- **Signal quality** - RSSI and SNR for each message
- **Hop tracking** - See message hop count

### Requirements
- Physical Meshtastic device connected via USB
- Meshtastic Python SDK (`pip install meshtastic`)

## Ubertooth One BLE Scanning

Advanced Bluetooth Low Energy scanning using Ubertooth One hardware.

### Capabilities
- **40-channel scanning** - Capture BLE advertisements across all channels
- **Raw payload access** - Full advertising data for analysis
- **Passive sniffing** - No active scanning required
- **MAC address extraction** - Public and random address types
- **RSSI measurement** - Signal strength for proximity estimation

### Integration
- Works alongside standard BlueZ/DBus Bluetooth scanning
- Automatically detected when ubertooth-btle is available
- Falls back to standard adapter if Ubertooth not present

### Requirements
- Ubertooth One hardware
- ubertooth-btle command-line tool installed
- libubertooth library

## Remote Agents (Distributed SIGINT)

Deploy lightweight sensor nodes across multiple locations and aggregate data to a central controller.

### Architecture
- **Hub-and-spoke model** - Central controller with multiple remote agents
- **Push and Pull modes** - Agents can push data automatically or respond to on-demand requests
- **API key authentication** - Secure communication between agents and controller

### Agent Features
- **Standalone deployment** - Run on Raspberry Pi, mini PCs, or any Linux device with SDR
- **All modes supported** - Pager, sensor, ADS-B, AIS, WiFi, Bluetooth, and more
- **GPS integration** - Automatic location tagging from USB GPS receivers
- **Multi-SDR support** - Run multiple modes simultaneously on agents with multiple SDRs
- **Capability discovery** - Controller auto-detects available modes and devices

### Controller Features
- **Agent management UI** - Register, test, and remove agents from `/controller/manage`
- **Real-time status** - Health monitoring with online/offline indicators
- **Unified data stream** - Aggregate data from all agents via SSE
- **Dashboard integration** - Agent selector in ADS-B, AIS, and main dashboards
- **Device conflict detection** - Smart warnings when SDR is in use

### Use Cases
- **Wide-area monitoring** - Cover larger geographic areas with distributed sensors
- **Remote installations** - Deploy sensors in locations without direct access
- **Redundancy** - Multiple nodes for reliable coverage
- **Triangulation** - Use multiple GPS-enabled agents for signal location

## System Health

- **Telemetry dashboard** with real-time system metrics
- **Process monitoring** for all running SDR tools and decoders
- **CPU, memory, and disk usage** tracking
- **SDR device status** overview
- **No SDR required** - monitors system health independently

## User Interface

- **Mode-specific header stats** - real-time badges showing key metrics per mode
- **UTC clock** - always visible in header for time-critical operations
- **SSE connection status indicator** - real-time connection state with SSEManager and exponential backoff reconnection
- **Accessibility** - aria-labels, form label associations, keyboard list navigation, and destructive action confirmation modals
- **Active mode indicator** - shows current mode with pulse animation
- **Collapsible sections** - click any header to collapse/expand
- **Panel styling** - gradient backgrounds with indicator dots
- **Tabbed mode selector** with icons (grouped by SDR/RF and Wireless)
- **Consistent design** - unified styling across main dashboard and popouts
- **Dark/Light theme toggle** - click moon/sun icon in header, preference saved
- **Browser notifications** - desktop alerts for critical events (drones, rogue APs, handshakes)
- **Built-in help page** - accessible via ? button or F1 key

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| F1 | Open help |
| ? | Open help (when not typing) |
| Escape | Close help/modals |

## Offline Mode

Run iNTERCEPT without internet connectivity by using bundled local assets.

### Bundled Assets
- **Leaflet 1.9.4** - Map library with marker images
- **Chart.js 4.4.1** - Signal strength graphs
- **Inter font** - Primary UI font (400, 500, 600, 700 weights)
- **JetBrains Mono font** - Monospace/code font (400, 500, 600, 700 weights)

### Settings Modal
Access via the gear icon in the navigation bar:
- **Offline Tab** - Toggle offline mode, configure asset sources (CDN vs local)
- **Display Tab** - Theme and animation preferences
- **About Tab** - Version info and links

### Map Tile Providers
Choose from multiple tile sources for maps:
- **OpenStreetMap** - Default, general purpose
- **CartoDB Dark** - Dark themed, matches UI
- **CartoDB Positron** - Light themed
- **ESRI World Imagery** - Satellite imagery
- **Custom URL** - Connect to your own tile server (e.g., local OpenStreetMap tile cache)

### Local Asset Status
The settings modal shows availability status for each bundled asset:
- Green "Available" badge when asset is present
- Red "Missing" badge when asset is not found
- Click "Check Assets" to refresh status

### Use Cases
- **Air-gapped environments** - Run on isolated networks
- **Field deployments** - Operate without reliable internet
- **Local tile servers** - Use pre-cached map tiles for specific regions
- **Reduced latency** - Faster loading with local assets

## General

- **Web-based interface** - no desktop app needed
- **Production server** - gunicorn + gevent via `start.sh` for concurrent SSE/WebSocket handling (falls back to Flask dev server)
- **Live message streaming** via Server-Sent Events (SSE)
- **Audio alerts** with mute toggle
- **Message export** to CSV/JSON
- **Signal activity meter** and waterfall display
- **Message logging** to file with timestamps
- **HTTPS support** via `INTERCEPT_HTTPS` configuration for secure deployments
- **Voice alerts** for configurable event notifications across modes
- **Multi-SDR hardware support** - RTL-SDR, LimeSDR, HackRF, Airspy, SDRplay
- **Automatic device detection** across all supported hardware
- **Hardware-specific gain caps** - gain inputs update automatically per device type (RTL-SDR: 0–50 dB, HackRF: 0–102 dB, Airspy: 0–45 dB, LimeSDR: 0–73 dB, SDRplay: 0–59 dB)
- **PPM correction fields auto-hide for HackRF** - all PPM inputs are hidden when HackRF is the selected device type
- **Hardware-specific validation** - frequency/gain ranges per device type
- **Tool path overrides** via `INTERCEPT_*_PATH` environment variables
- **Native Homebrew detection** for Apple Silicon tool paths
- **Configurable gain and PPM correction** across all modes
- **Device intelligence** dashboard with tracking
- **GPS dongle support** - USB GPS receivers for precise observer location
- **Disclaimer acceptance** on first use
- **Auto-stop** when switching between modes

