# INTERCEPT Features

Every mode at a glance. Click a mode for how to use it.

## Signals

| Mode | What it does | Needs |
|------|--------------|-------|
| [Pager](USAGE.md#pager) | POCSAG 512/1200/2400 and FLEX decoding | SDR, multimon-ng |
| [433MHz Sensors](USAGE.md#433mhz-sensors) | 200+ protocols: weather stations, TPMS, IoT | SDR, rtl_433 |
| [Utility Meters](USAGE.md#utility-meters) | Electric, gas and water meter broadcasts | RTL-SDR, rtlamr |
| [Sub-GHz](USAGE.md#sub-ghz) | 300-928 MHz ISM capture, decode and replay | HackRF |
| [Waterfall](USAGE.md#waterfall) | Real-time spectrum with click-to-tune audio and bookmarks | RTL-SDR or HackRF |
| [CW/Morse](USAGE.md#cwmorse) | Morse by tone (HF) or on-off envelope (ISM) | SDR (HF needs direct sampling or an upconverter) |
| [OOK Decoder](USAGE.md#ook-decoder) | Raw on-off-keyed frames as bits, hex and ASCII | SDR, rtl_433 |

## Tracking

| Mode | What it does | Needs |
|------|--------------|-------|
| [Aircraft](USAGE.md#aircraft-ads-b-dashboard) | ADS-B radar dashboard, airband audio, Tune ATC | SDR, dump1090 or readsb |
| [ACARS](USAGE.md#acars) | Aircraft datalink messages (on the aircraft dashboard) | SDR, acarsdec |
| [VDL2](USAGE.md#vdl2) | VHF Data Link Mode 2 (on the aircraft dashboard) | SDR, dumpvdl2 |
| [ADS-B History](USAGE.md#ads-b-history-optional) | Long-term aircraft history and reporting | PostgreSQL |
| [Vessels](USAGE.md#vessels-ais-dashboard) | AIS dashboard with VHF DSC distress monitoring | RTL-SDR, AIS-catcher |
| [APRS](USAGE.md#aprs-dashboard) | Amateur packet radio positions and telemetry | SDR, direwolf |
| [GPS](USAGE.md#gps) | Live position, speed, altitude and satellites | GPS receiver, gpsd |
| [Radiosonde](USAGE.md#radiosonde) | Weather balloon telemetry | SDR, radiosonde_auto_rx |

## Space

| Mode | What it does | Needs |
|------|--------------|-------|
| [Satellite](USAGE.md#satellite-dashboard) | Pass prediction, polar plot, ground track, pass scheduling | None for prediction; SDR to receive |
| [ISS SSTV](USAGE.md#iss-sstv) | Slow-scan TV from the ISS, with Doppler correction | SDR, 2m antenna |
| [HF SSTV](USAGE.md#hf-sstv) | Amateur SSTV on HF, VHF and UHF | SDR (HF needs direct sampling or an upconverter) |
| [Weather Satellites](USAGE.md#weather-satellites) | NOAA APT and Meteor LRPT imagery, auto-scheduler | SDR, SatDump |
| [WeFax](USAGE.md#wefax) | HF weather fax charts | SDR with HF |
| [Meteor Scatter](USAGE.md#meteor-scatter) | VHF beacon reflections from meteor trails | SDR, VHF antenna |
| [Space Weather](USAGE.md#space-weather) | Solar and geomagnetic data from NOAA and NASA | Internet only |

## Wireless

| Mode | What it does | Needs |
|------|--------------|-------|
| [WiFi](USAGE.md#wifi) | Network and client discovery, handshake capture | Monitor-mode WiFi adapter |
| [Bluetooth](USAGE.md#bluetooth) | Device discovery and tracker detection (AirTag, Tile, SmartTag) | Bluetooth adapter |
| [WiFi Locate](USAGE.md#wifi-locate) | Find an access point by signal strength | Monitor-mode WiFi adapter |
| [BT Locate](USAGE.md#bt-locate) | Find a Bluetooth device, with a GPS trail | Bluetooth adapter, optional GPS |

## LoRa

| Mode | What it does | Needs |
|------|--------------|-------|
| [Meshtastic](USAGE.md#meshtastic-dashboard) | Messages, node search, map, stored history, Store & Forward | Meshtastic node (USB or TCP) |
| [Meshcore](USAGE.md#meshcore-dashboard) | Messages, contacts, node search, traceroute | Meshcore node (USB, TCP or BLE) |

## Intel

| Mode | What it does | Needs |
|------|--------------|-------|
| [TSCM](USAGE.md#tscm) | Counter-surveillance sweeps against an RF baseline | WiFi, Bluetooth and SDR |
| [TSCM Survey](USAGE.md#tscm-survey) | Survey workspace: baseline, findings, threats, client report | TSCM sweeps |
| [Activity](USAGE.md#activity) | One feed of sightings from every mode | Any running mode |
| [Drone Intelligence](USAGE.md#drone-intelligence) | Remote ID plus RF detection of drones | WiFi adapter, RTL-SDR, optional HackRF |
| [Spy Stations](USAGE.md#spy-stations) | Number station and diplomatic HF database | None |
| [WebSDR](USAGE.md#websdr) | Listen through remote KiwiSDR receivers | Internet only |

## Platform

| Feature | What it does |
|---------|--------------|
| [SDR devices](USAGE.md#sdr-devices-and-calibration) | Name each SDR and save its PPM correction, gain and bias-T |
| [Remote SDR](USAGE.md#remote-sdr-rtl_tcp) | Use an RTL-SDR on another machine over rtl_tcp |
| [Message store](USAGE.md#message-store) | Everything decoded, searchable, with CSV/JSON export |
| [Watchlists and alerts](USAGE.md#watchlists-and-alerts) | Push matches to ntfy, Telegram or webhooks |
| [CoT export](USAGE.md#cot-export-atak) | Positions to ATAK, WinTAK or TAK Server |
| [Accounts](USAGE.md#accounts) | Per-user sign-in and a users directory |
| [Remote agents](USAGE.md#remote-agents) | Control sensor nodes on other machines |
| [Offline mode](USAGE.md#offline-mode) | Bundled assets for air-gapped use |

## More Documentation

- [Usage Guide](USAGE.md): how to use each mode
- [Hardware Guide](HARDWARE.md): SDR hardware, drivers, multiple dongles
- [Webhooks and Alerts](WEBHOOKS.md): watchlists, alert rules and push notifications
- [Distributed Agents](DISTRIBUTED_AGENTS.md): remote sensor node deployment
- [Troubleshooting](TROUBLESHOOTING.md): common issues and solutions
- [Security](SECURITY.md): authentication and network exposure
