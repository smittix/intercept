# INTERCEPT Usage Guide

How to use each mode. Sections follow the navigation bar: Signals, Tracking, Space, Wireless, LoRa and Intel, then the platform features that work across modes.

Text and scan modes run in the main app at `/`. Map-heavy modes (Aircraft, Vessels, APRS, Satellite, Meshtastic and Meshcore) open as full-screen dashboards; the navigation bar takes you between the two.

Every mode has a short in-app reference: open **More → Mode cheat sheet**, or click **?** in the navigation bar for the full help.

## Before You Start

### SDR Devices and Calibration

Open **Settings → SDR** to configure each attached SDR once:

- **Name**: shown in every device picker, so you can tell dongles apart
- **PPM correction**: the dongle's frequency error. Cheap RTL-SDRs are often tens of PPM out, which can stop AIS, ADS-B or pager decoding entirely
- **Default gain** and **bias-T**

Settings follow the dongle by serial number where it has a unique one, otherwise by USB position. Any mode whose gain or PPM box is left blank uses these saved values; a value typed into a mode overrides them for that run.

### Remote SDR (rtl_tcp)

Tick **Use Remote SDR (rtl_tcp)** in a mode's SDR section and enter the host and port of an `rtl_tcp` server to use an RTL-SDR on another machine. Pager, 433MHz, Meters, Morse, APRS and DSC support it. ADS-B and AIS do not; for those, run a [remote agent](#remote-agents) on the other machine.

### Observer Location

Set your location once in **Settings → Location**, or use **Use GPS** with a gpsd receiver. It is shared by the maps, satellite pass prediction, ISS Doppler correction and range calculations. To keep a separate location per mode, set `INTERCEPT_SHARED_OBSERVER_LOCATION=false`.

## Signals

### Pager

1. **Select device**: choose your SDR (RTL-SDR, HackRF or a SoapySDR device)
2. **Set frequency**: enter a frequency in MHz or click a preset
3. **Choose protocols**: POCSAG 512/1200/2400 and FLEX
4. **Adjust settings**: gain, squelch and PPM (blank uses the device default)
5. **Start decoding**

Click a preset to tune it, add your own with the input field, right-click a preset to remove it, or reset to the defaults.

### 433MHz Sensors

1. **Select device** and **start decoding**
2. Decoded sensors appear in real time

Supports 200+ protocols via rtl_433, including weather stations, TPMS, doorbells and IoT devices. Leave gain on auto, and use the protocol filter to cut false positives.

### Utility Meters

1. **Start** to receive meter broadcasts via rtlamr
2. Meters appear with ID, type and consumption
3. **Filter** by meter type (electric, gas, water) or meter ID

Most meters broadcast around 915 MHz. Message types 5, 7, 13 and 21 are the most common.

### Sub-GHz

1. **Connect a HackRF One**
2. **Set a frequency** in the 300-928 MHz ISM range, or use a preset
3. **Start capture** for live spectrum and protocol decoding
4. **Replay** a capture, on frequencies you are licensed to transmit on

Common targets: garage doors, key fobs and sensors on 315, 433, 868 and 915 MHz.

### Waterfall

A real-time spectrum waterfall with audio.

1. **Select device**: RTL-SDR or HackRF
2. **Set the center frequency and span**, or pick a band (FM broadcast, airband, marine, 2m)
3. **Start Waterfall**
4. **Click a signal** to tune it, and choose the demodulator (Auto, WFM, NFM, AM, USB, LSB)
5. **Save bookmarks** for frequencies you return to, or use the quick-tune list (121.5 guard, marine CH16 and others)
6. **Handoff** sends the tuned frequency to a decoder mode

Hover over the waterfall to read a frequency. Peak hold shows maximum power.

### CW/Morse

1. **Choose a detection mode**: **CW tone** for HF amateur Morse, or **OOK envelope** for keyed ISM/UHF signals
2. **Set the frequency**, or use a band preset (80m-10m, or 315/433/868/915 MHz)
3. **Adjust the tone frequency and bandwidth** to match the signal
4. **Start**: decoded characters appear with a WPM estimate and signal level

HF frequencies need an HF-capable SDR or an upconverter. Try 7.030 or 14.060 MHz.

### OOK Decoder

Decodes raw on-off-keyed frames via rtl_433's flex decoder and shows them as bits, hex and ASCII. Use it to reverse-engineer protocols rtl_433 does not already know.

1. **Set the frequency** (315, 433.92, 868 or 915 MHz)
2. **Choose the modulation**: PWM (most ISM remotes and sensors), PPM or Manchester
3. **Set pulse timing**: short and long pulse widths, gap/reset and tolerance, or use a quick preset
4. **Start Decoder**

To find the timing, run `rtl_433 -f 433.92M -A` in a terminal: it prints the pulse and gap widths. Garbled output usually means the timings are off by a factor of two.

## Tracking

### Aircraft (ADS-B Dashboard)

The aircraft dashboard (`/adsb/dashboard`) shows a radar map with ACARS and VDL2 panels and an airband receiver.

1. **Choose a source** in the ADS-B tracking bar: a local SDR, or tick **Remote** and enter the host and port of another dump1090's SBS feed (port 30003)
2. **Set gain and bias-T**, then **Start**
3. **Click an aircraft** for its details: callsign, registration, type, altitude, speed and squawk
4. **Display options**: trails, range rings, labels, **Reach** (your real reception outline, built up from the furthest contact in each direction) and alerts
5. **Filter** to all, military, civil or emergency aircraft, or a range

Toolbar tools:

- **Tune ATC**: tunes the airband receiver to the nearest airport's tower, approach or ATIS frequency for the selected aircraft
- **Squawk**: squawk code reference
- **Lookup**: opens the selected aircraft on FlightAware
- **Instruments**: a pop-out instrument panel for the selected aircraft
- **Report** and **History**: session report, and the long-term history dashboard
- **Antenna**: antenna guidance for 1090 MHz

ACARS and VDL2 open from the tabs on the left edge. Each has its own source picker, so they can run on a local SDR while ADS-B runs on an agent, or the other way round.

Aircraft types and registrations need the aircraft database: accept the download prompt the first time. Emergency squawks are highlighted: **7500** hijack, **7600** radio failure, **7700** general emergency. ADS-B starts manually; set `INTERCEPT_ADSB_AUTO_START=true` to start it when the dashboard opens.

#### ACARS

1. Open the ACARS panel and **select a region** (North America, Europe, Asia-Pacific) to fill in frequencies
2. **Select frequencies**: 131.550 MHz is the primary worldwide
3. **Start**: messages appear with flight, registration and content

A vertical antenna works best. A quarter-wave is 57 cm at 130 MHz.

#### VDL2

1. Open the VDL2 panel. The standard VDL2 frequencies (136.975, 136.725, 136.775 MHz and others) are preset
2. **Start** to decode via dumpvdl2
3. Click a message for the full AVLC frame and raw JSON; export as CSV or JSON

VDL2 is busiest near airports and along flight corridors.

#### ADS-B History (Optional)

History stores aircraft messages and snapshots in PostgreSQL for long-term reporting.

```bash
./setup.sh --postgres-setup      # local install
docker compose --profile history up -d   # Docker
```

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERCEPT_ADSB_HISTORY_ENABLED` | `false` | Enables history storage and reporting |
| `INTERCEPT_ADSB_DB_HOST` | `localhost` | Postgres host (`adsb_db` in Docker) |
| `INTERCEPT_ADSB_DB_PORT` | `5432` | Postgres port |
| `INTERCEPT_ADSB_DB_NAME` | `intercept_adsb` | Database name |
| `INTERCEPT_ADSB_DB_USER` | `intercept` | Database user |
| `INTERCEPT_ADSB_DB_PASSWORD` | `intercept` | Database password |

To keep Postgres data on external storage, set `PGDATA_PATH` (defaults to `./pgdata`). Then open **History** on the aircraft dashboard, or `/adsb/history`. If it shows **HISTORY DISABLED**, check `INTERCEPT_ADSB_HISTORY_ENABLED=true` and that Postgres is running.

### Vessels (AIS Dashboard)

1. Open the vessels dashboard (`/ais/dashboard`) and **Start** to receive AIS on 161.975 and 162.025 MHz
2. **Click a vessel** for name, MMSI, callsign, destination, speed and heading
3. **Display options**: trails, rings, labels and **Reach**

**Start DSC** monitors VHF channel 70 (Digital Selective Calling) alongside AIS. Distress, urgency, safety and routine calls are listed; distress positions are plotted with a pulsing marker and an audio alert.

A VHF antenna for 162 MHz works best. Coastal range is typically around 40 nautical miles.

### APRS (Dashboard)

1. Open the APRS dashboard (`/aprs/dashboard`)
2. **Set the frequency**: 144.390 MHz in North America, 144.800 MHz in Europe
3. **Start** to decode via direwolf
4. Stations appear on the map with position reports, weather, telemetry and messages

### GPS

1. **Connect** to gpsd
2. Position, speed, heading, altitude, fix type and satellites appear in real time, with accuracy figures (DOP and error estimates)

Make sure gpsd is running (for example `sudo gpsd /dev/ttyUSB0 -F /var/run/gpsd.sock`). A cold start can take 30-60 seconds to get a fix.

### Radiosonde

Tracks weather balloons via radiosonde_auto_rx.

1. **Select a region** (Global, Europe, US, Australia) or set a custom frequency range in 400-406 MHz
2. **Set gain** (0 for auto; around 40 dB is a good start)
3. **Start**: active sondes appear with position, altitude, temperature, humidity and pressure

## Space

### Satellite (Dashboard)

1. Open the satellite dashboard (`/satellite/dashboard`) and set your location
2. **Add satellites**: **Celestrak** fetches by category (space stations, weather, amateur, cubesats and more), or **Add TLE** for your own
3. **Calculate** upcoming passes; the polar plot and ground track follow the selected satellite live
4. **Schedule a pass**: choose a decoder (telemetry AX.25/AFSK, GMSK, BPSK, or Meteor LRPT capture), minimum elevation and gain, and optionally record IQ

TLEs refresh automatically every 24 hours, or use **Refresh TLE**.

### ISS SSTV

1. **Select device** and set the frequency: 145.800 MHz (the usual ISS downlink) or a preset such as 437.800 MHz
2. **Choose the modulation**: FM for the ISS, or USB/LSB
3. **Set your location** for Doppler correction and pass prediction
4. **Update TLE**, then wait for the next pass (the countdown shows when)
5. **Start**: decoded images appear in the gallery

The ISS's signal shifts by up to ±3.5 kHz during a pass; INTERCEPT retunes as it goes. You need at least a V-dipole (51 cm elements at 145.8 MHz, 120 degrees apart): the stock antenna will not work. ISS SSTV events are occasional, so check ARISS for dates. Passes above 30 degrees are best. Common modes: PD120, PD180, Martin 1, Scottie 1.

### HF SSTV

1. **Select device** and **choose a frequency** from the presets, or enter your own
2. **Modulation** is set from the frequency (USB for HF, FM for VHF/UHF)
3. **Start**: decoded images appear in the gallery

14.230 MHz USB (20m) is the busiest. HF (3-30 MHz) needs an HF-capable SDR or an upconverter; 145 and 433 MHz work with a plain RTL-SDR.

### Weather Satellites

1. **Set your location**
2. **Select a satellite**: NOAA (APT) or Meteor (LRPT)
3. **View passes** with polar plot and ground track
4. **Start Capture** when a satellite is overhead, or enable the **auto-scheduler** to capture every pass
5. Decoded images appear in the gallery

Decoding uses SatDump. Use a circularly polarised antenna (QFH or turnstile) for 137 MHz.

### WeFax

Decodes HF weather fax charts from maritime and aviation weather stations.

1. **Select a station**: its frequencies fill in automatically
2. **Check the settings**: IOC 576 and 120 LPM are standard
3. **Start**, or enable **Auto Capture** to record scheduled broadcasts

HF needs **Direct Sampling (Q-branch)** on an RTL-SDR, or an upconverter. The **Auto USB align** option shifts listed carrier frequencies by -1.9 kHz for USB reception.

### Meteor Scatter

Detects VHF beacon reflections from meteor trails.

1. **Set the center frequency**: 143.050 MHz for the GRAVES radar (Europe)
2. **Set gain, sample rate and FFT size**
3. **Tune detection**: SNR threshold, minimum duration, cooldown and drift tolerance
4. **Start**: pings appear on the waterfall and in the event log; export as CSV or JSON

A Yagi pointed towards the transmitter works best. Activity peaks during meteor showers (Perseids, Geminids).

### Space Weather

No SDR needed: data comes from NOAA SWPC, NASA SDO and HamQSL.

- Solar indices, NOAA scales, band conditions and charts load automatically
- **Solar imagery**: SDO 193Å, 304Å and magnetogram
- **D-RAP maps**: HF absorption at 5-30 MHz
- **Aurora forecast**, **alerts** and **active regions**

Data refreshes every 5 minutes. Kp of 5 or more means a geomagnetic storm, which affects HF propagation.

## Wireless

### WiFi

1. **Select an adapter** that supports monitor mode
2. **Enable monitor mode** (untick "Kill processes" to keep other connections)
3. **Start scanning**: networks and clients appear with signal strength, channel and encryption
4. **Track** a network's signal over time, or **Capture** a handshake

Run with `sudo`. Check your adapter with `iw list | grep monitor`.

### Bluetooth

1. **Select an adapter** and **start scanning**
2. Devices appear with name, address, manufacturer and classification

Known trackers are flagged automatically, including Apple AirTag, Tile, Samsung SmartTag and Chipolo, using a database of 47,000+ fingerprints.

### WiFi Locate

1. **Set the target**: a BSSID, or click **Locate** on a network in WiFi mode
2. **Choose the environment**: open field, outdoor or indoor
3. **Start Locate**: a large dBm reading, signal bar, distance estimate and RSSI history show how close you are
4. **Enable audio** for proximity tones that speed up as you get closer

WiFi Locate needs a deep scan for continuous updates and starts one if needed. The distance is an estimate; the environment setting affects it a lot.

### BT Locate

1. **Set the target**: a MAC address, a name pattern, or an IRK for devices that randomise their address (**Detect** extracts IRKs from paired devices). Or click **Locate** on a device in Bluetooth mode
2. **Choose the environment**: open field, outdoor or indoor
3. **Start Locate**: the display shows proximity (immediate, near, far), estimated distance and RSSI
4. **Follow the signal**, with optional audio tones; with GPS, detections are plotted as a trail

## LoRa

### Meshtastic (Dashboard)

1. Open the Meshtastic dashboard and **Connect** to a node over USB serial or TCP
2. **Messages**: the mesh's messages, kept between sessions. **Find in conversation** searches them, **Text only** hides non-text packets, and **↩** replies
3. **Missed**: asks a Store & Forward router for messages sent while you were away (15 minutes to 24 hours)
4. **Nodes**: every node the device knows, including those without a position. Search by name or ID, click to centre the map, message a node directly, and favourite, ignore or remove nodes
5. **Channels** and **Info**: channel settings and node details

Message times are the time the node heard them when the node has a clock (GPS, the phone app or mesh time sync); otherwise the time they reached INTERCEPT.

### Meshcore (Dashboard)

1. Open the Meshcore dashboard and **Scan** or **Connect** to a node over serial, TCP or Bluetooth
2. **Send** messages to channels and contacts
3. **Nodes and contacts**: search, add contacts by public key, and run a traceroute

## Intel

### TSCM

Technical surveillance countermeasures: sweeps for hidden transmitters and trackers.

1. **Select a sweep type**: Quick (2 min), Standard (5 min), Full (15 min), or a preset for wireless cameras, body-worn devices or GPS trackers
2. **Select sources**: WiFi, Bluetooth and/or RF (SDR)
3. **Select a baseline** to compare against (optional)
4. **Start Sweep**: devices are classified and scored
5. **Record a baseline** in a known-clean environment for later comparisons

Scores: **0-2** informational, **3-5** needs review, **6+** high interest. Full coverage needs a WiFi adapter, a Bluetooth adapter and an SDR.

### TSCM Survey

A workspace that walks a survey in order, using the sweeps and baselines from TSCM mode:

1. **Baseline**: the active baseline, its age and health
2. **Sweep**: what the latest sweep found that the baseline did not (new, missing and changed devices)
3. **Known devices**: each device's score and the indicators behind it; mark devices as known
4. **Threats and findings**: resolve threats with notes, add them to a case, and review correlations
5. **Report**: the client report, with JSON or CSV annexes

### Activity

One reverse-chronological feed of sightings from every mode that reports them. Filter by source, time window (15 minutes to 24 hours) or a single device. **Pause** to read: new sightings are held and shown when you resume.

### Drone Intelligence

1. **Set the WiFi interface** (monitor mode) for Remote ID, and the RTL-SDR index if you have several
2. **Start Scan**: all available detection methods run together
3. Contacts appear with ID, detection methods, risk level and last seen; those broadcasting GPS appear on the map

Detection methods:

- **Remote ID (WiFi/BLE)**: decodes ASTM F3411 broadcasts (drone position, operator ID, type, speed, altitude)
- **433/868 MHz RF** (RTL-SDR): control and telemetry links
- **2.4/5.8 GHz** (HackRF, receive only): video downlinks

Risk is **high** for a drone without Remote ID or with malformed frames, **medium** when seen on several methods or with a large signal mismatch, and **low** for a compliant Remote ID broadcast. Contacts expire after 120 seconds without a detection.

### Spy Stations

A reference database of number stations and diplomatic HF networks, from priyom.org.

1. **Filter** by type, country or mode (USB, AM, CW, OFDM)
2. **Details** shows a station's full information
3. **Tune In** sends the frequency to the Waterfall or WebSDR

Most activity is on HF (3-30 MHz). Notable stations include UVB-76 "The Buzzer" (4625 kHz), E06 and HM01. Nothing is decrypted or decoded.

### WebSDR

1. **Set a frequency** in kHz and a mode (USB, LSB, AM, CW)
2. **Find Receivers** to list KiwiSDR receivers worldwide, sorted by distance
3. **Select a receiver** to listen; audio streams to the browser with an S-meter

Needs an internet connection. Spy station presets tune straight to known frequencies.

## Platform

### Message Store

`/messages` keeps everything the decoders hear in one searchable store. Filter by mode, time, identity and position, and export as CSV or JSON. Storage limits (rows, size and age) are set on the same page.

### Watchlists and Alerts

Watchlists match aircraft, ships, callsigns, MAC addresses, frequencies or keywords across all modes. Matches, and alert rules, can be pushed to ntfy, Telegram or any webhook, with a per-station cooldown, an hourly cap and quiet hours. Configure them under **Settings → Alerts**. See [Webhooks and Alerts](WEBHOOKS.md).

### CoT Export (ATAK)

ADS-B, AIS and APRS positions, and Meshtastic node positions, can be sent as Cursor-on-Target to ATAK, WinTAK or TAK Server. It is off unless `INTERCEPT_COT_HOST` is set:

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERCEPT_COT_HOST` | (empty: off) | Destination address or multicast group |
| `INTERCEPT_COT_PORT` | `6969` | Destination port |
| `INTERCEPT_COT_PROTO` | `udp` | `udp` or `tcp` |
| `INTERCEPT_COT_STALE_SECONDS` | `60` | How long a position stays valid in TAK |

### Accounts

Sign in with your account; on first start the `admin` password is generated (see [Security](SECURITY.md#authentication)). Under **System → Accounts Management** you can change your display name and password, and browse the users and roles directory.

### Remote Agents

Run INTERCEPT's agent on other machines and control them from one interface.

1. **Create** `intercept_agent.cfg` on the remote machine:
   ```ini
   [agent]
   name = sensor-node-1
   port = 8020

   [controller]
   url = http://192.168.1.100:5050
   api_key = your-secret-key
   push_enabled = true

   [modes]
   pager = true
   sensor = true
   adsb = true
   ```
2. **Start it**: `python intercept_agent.py --config intercept_agent.cfg`
3. **Register it** at `/controller/manage` in the main instance (name, base URL and API key must match), then **Test**
4. **Select the agent** from the source dropdown in a supported mode. **Show All Agents** combines data from all of them

See the [Distributed Agents Guide](DISTRIBUTED_AGENTS.md) for details.

### Offline Mode

1. Open **Settings → Offline** and turn on **Offline Mode**
2. Switch assets and fonts from CDN to local
3. Choose a map tile provider, or enter your own tile server URL
4. **Check Assets** confirms the local files are present

Local assets live in `static/vendor/`. Maps need a tile server you can reach offline, such as self-hosted OpenStreetMap tiles.
