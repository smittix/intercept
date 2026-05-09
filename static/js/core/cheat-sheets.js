/* INTERCEPT Per-Mode Cheat Sheets */
const CheatSheets = (function () {
    'use strict';

    const CONTENT = {
        pager:       { title: 'Pager Decoder',           icon: '📟', hardware: 'RTL-SDR dongle',                          description: 'Decodes POCSAG and FLEX pager protocols via rtl_fm + multimon-ng.',          whatToExpect: 'Numeric and alphanumeric pager messages with address codes.',   tips: ['Try frequencies 152.240, 157.450, 462.9625 MHz', 'Gain 38–45 dB works well for most dongles', 'POCSAG 512/1200/2400 baud are common'] },
        sensor:      { title: '433MHz Sensors',          icon: '🌡️', hardware: 'RTL-SDR dongle',                          description: 'Decodes 433MHz IoT sensors via rtl_433.',                                   whatToExpect: 'JSON events from weather stations, door sensors, car key fobs.', tips: ['Leave gain on AUTO', 'Walk around to discover hidden sensors', 'Protocol filter narrows false positives'] },
        wifi:        { title: 'WiFi Scanner',            icon: '📡', hardware: 'WiFi adapter (monitor mode)',              description: 'Scans WiFi networks and clients via airodump-ng or nmcli.',                 whatToExpect: 'SSIDs, BSSIDs, channel, signal strength, encryption type.',    tips: ['Run airmon-ng check kill before monitoring', 'Proximity radar shows signal strength', 'TSCM baseline detects rogue APs'] },
        bluetooth:   { title: 'Bluetooth Scanner',       icon: '🔵', hardware: 'Built-in or USB Bluetooth adapter',        description: 'Scans BLE and classic Bluetooth devices. Identifies trackers.',              whatToExpect: 'Device names, MACs, RSSI, manufacturer, tracker type.',        tips: ['Proximity radar shows device distance', 'Known tracker DB has 47K+ fingerprints', 'Use BT Locate to physically find a tracker'] },
        bt_locate:   { title: 'BT Locate (SAR)',         icon: '🎯', hardware: 'Bluetooth adapter + optional GPS',         description: 'SAR Bluetooth locator. Tracks RSSI over time to triangulate position.', whatToExpect: 'RSSI chart, proximity band (IMMEDIATE/NEAR/FAR), GPS trail.',   tips: ['Handoff from Bluetooth mode to lock onto a device', 'Indoor n=3.0 gives better distance estimates', 'Follow the heat trail toward stronger signal'] },
        wifi_locate: { title: 'WiFi Locate',              icon: '📶', hardware: 'WiFi adapter (monitor mode)',              description: 'Locate a WiFi AP by BSSID with real-time signal strength tracking.', whatToExpect: 'Big dBm meter, signal bar, RSSI chart, distance estimate, proximity beeps.', tips: ['Handoff from WiFi mode — click Locate on any network', 'Deep scan required for continuous RSSI updates', 'Indoor n=3.5 gives better distance estimates indoors', 'Enable audio for proximity tones that speed up as you get closer'] },
        meshtastic:  { title: 'Meshtastic',              icon: '🕸️', hardware: 'Meshtastic LoRa node (USB)',               description: 'Monitors Meshtastic LoRa mesh network messages and positions.',             whatToExpect: 'Text messages, node map, telemetry.',                          tips: ['Default channel must match your mesh', 'Long-Fast has best range', 'GPS nodes appear on map automatically'] },
        adsb:        { title: 'ADS-B Aircraft',          icon: '✈️', hardware: 'RTL-SDR + 1090MHz antenna',               description: 'Tracks aircraft via ADS-B Mode S transponders using dump1090.',             whatToExpect: 'Flight numbers, positions, altitude, speed, squawk codes.',     tips: ['1090MHz — use a dedicated antenna', 'Emergency squawks: 7500 hijack, 7600 radio fail, 7700 emergency', 'Full Dashboard shows map view'] },
        ais:         { title: 'AIS Vessels',             icon: '🚢', hardware: 'RTL-SDR + VHF antenna (162 MHz)',          description: 'Tracks marine vessels via AIS using AIS-catcher.',                         whatToExpect: 'MMSI, vessel names, positions, speed, heading, cargo type.',    tips: ['VHF antenna centered at 162MHz works best', 'DSC distress alerts appear in red', 'Coastline range ~40 nautical miles'] },
        aprs:        { title: 'APRS',                    icon: '📻', hardware: 'RTL-SDR + VHF + direwolf',                description: 'Decodes APRS amateur packet radio via direwolf TNC modem.',                whatToExpect: 'Station positions, weather reports, messages, telemetry.',      tips: ['Primary APRS frequency: 144.390 MHz (North America)', 'direwolf must be running', 'Positions appear on the map'] },
        satellite:   { title: 'Satellite Tracker',       icon: '🛰️', hardware: 'None (pass prediction only)',              description: 'Predicts satellite pass times using TLE data from CelesTrak.',              whatToExpect: 'Pass windows with AOS/LOS times, max elevation, bearing.',      tips: ['Set observer location in Settings', 'Plan ISS SSTV using pass times', 'TLEs auto-update every 24 hours'] },
        sstv:        { title: 'ISS SSTV',                icon: '🖼️', hardware: 'RTL-SDR + 145MHz antenna',               description: 'Receives ISS SSTV images via slowrx.',                                    whatToExpect: 'Color images during ISS SSTV events (PD180 mode).',             tips: ['ISS SSTV: 145.800 MHz', 'Check ARISS for active event dates', 'ISS must be overhead — check pass times'] },
        weathersat:  { title: 'Weather Satellites',      icon: '🌤️', hardware: 'RTL-SDR + 137MHz turnstile/QFH antenna',  description: 'Decodes NOAA APT and Meteor LRPT weather imagery via SatDump.',          whatToExpect: 'Infrared/visible cloud imagery.',                              tips: ['NOAA 15/18/19: 137.1–137.9 MHz APT', 'Meteor M2-3: 137.9 MHz LRPT', 'Use circular polarized antenna (QFH or turnstile)'] },
        sstv_general:{ title: 'HF SSTV',                 icon: '📷', hardware: 'RTL-SDR + HF upconverter',                description: 'Receives HF SSTV transmissions.',                                          whatToExpect: 'Amateur radio images on 14.230 MHz (USB mode).',                tips: ['14.230 MHz USB is primary HF SSTV frequency', 'Scottie 1 and Martin 1 most common', 'Best during daylight hours'] },
        gps:         { title: 'GPS Receiver',            icon: '🗺️', hardware: 'USB GPS receiver (NMEA)',                 description: 'Streams GPS position and feeds location to other modes.',                  whatToExpect: 'Lat/lon, altitude, speed, heading, satellite count.',          tips: ['BT Locate uses GPS for trail logging', 'Set observer location for satellite prediction', 'Verify a 3D fix before relying on altitude'] },
        spaceweather:{ title: 'Space Weather',           icon: '☀️', hardware: 'None (NOAA/SpaceWeatherLive data)',        description: 'Monitors solar activity and geomagnetic storm indices.',                   whatToExpect: 'Kp index, solar flux, X-ray flare alerts, CME tracking.',      tips: ['High Kp (≥5) = geomagnetic storm', 'X-class flares cause HF radio blackouts', 'Check before HF or satellite operations'] },
        controller_monitor: { title: 'Controller Monitor', icon: '🖧', hardware: 'Optional remote agents',               description: 'Aggregated controller view across connected agents and local sources.',    whatToExpect: 'Combined device activity, logs, and agent health in one place.', tips: ['Use it to compare what each agent is seeing', 'Check agent status before remote starts', 'Open Manage to add or troubleshoot agents'] },
        tscm:        { title: 'TSCM Counter-Surveillance', icon: '🔍', hardware: 'WiFi + Bluetooth adapters',             description: 'Technical Surveillance Countermeasures — detects hidden devices.',        whatToExpect: 'RF baseline comparison, rogue device alerts, tracker detection.', tips: ['Take baseline in a known-clean environment', 'New strong signals = potential bug', 'Correlate WiFi + Bluetooth observations'] },
        spystations: { title: 'Spy Stations',            icon: '🕵️', hardware: 'RTL-SDR + HF antenna',                   description: 'Database of known number stations, military, and diplomatic HF signals.', whatToExpect: 'Scheduled broadcasts, frequency database, tune-to links.',    tips: ['Numbers stations often broadcast on the hour', 'Use Spectrum Waterfall to tune directly', 'STANAG and HF mil signals are common'] },
        websdr:      { title: 'WebSDR',                  icon: '🌐', hardware: 'None (uses remote SDR servers)',           description: 'Access remote WebSDR receivers worldwide for HF shortwave listening.', whatToExpect: 'Live audio from global HF receivers, waterfall display.',      tips: ['websdr.org lists available servers', 'Good for HF when local antenna is lacking', 'Use in-app player for seamless experience'] },
        drone:       { title: 'Drone Intelligence',      icon: '🚁', hardware: 'WiFi adapter (monitor mode) + RTL-SDR + optional HackRF', description: 'Multi-vector UAV detection: ASTM F3411 Remote ID (WiFi/BLE), RTL-SDR 433/868 MHz RF fingerprinting, HackRF 2.4/5.8 GHz.', whatToExpect: 'Drone contacts with ID, operator, GPS position (if broadcast), detection vectors, and risk level.', tips: ['Remote ID is mandatory in the US/EU since 2023 — absence flags high risk', 'RTL-SDR catches DJI/FPV video links on 2.4 GHz if HackRF unavailable', 'Risk HIGH = no Remote ID or non-compliant; MEDIUM = multi-vector or RSSI anomaly', 'Map markers appear only for contacts with GPS coordinates from Remote ID'] },
        subghz:      { title: 'SubGHz Transceiver',      icon: '📡', hardware: 'HackRF One',                             description: 'Transmit and receive sub-GHz RF signals for IoT and industrial protocols.', whatToExpect: 'Raw signal capture, replay, and protocol analysis.',        tips: ['Only use on licensed frequencies', 'Capture mode records raw IQ for replay', 'Common: garage doors, keyfobs, 315/433/868/915 MHz'] },
        rtlamr:      { title: 'Utility Meter Reader',    icon: '⚡', hardware: 'RTL-SDR dongle',                          description: 'Reads AMI/AMR smart utility meter broadcasts via rtlamr.',                whatToExpect: 'Meter IDs, consumption readings, interval data.',               tips: ['Most meters broadcast on 915 MHz', 'MSG types 5, 7, 13, 21 most common', 'Consumption data is read-only public broadcast'] },
        waterfall:   { title: 'Spectrum Waterfall',      icon: '🌊', hardware: 'RTL-SDR or HackRF (WebSocket)',           description: 'Full-screen real-time FFT spectrum waterfall display.',                  whatToExpect: 'Color-coded signal intensity scrolling over time.',             tips: ['Turbo palette has best contrast for weak signals', 'Peak hold shows max power in red', 'Hover over waterfall to see frequency'] },
        radiosonde:  { title: 'Radiosonde Tracker',       icon: '🎈', hardware: 'RTL-SDR dongle',                          description: 'Tracks weather balloons via radiosonde telemetry using radiosonde_auto_rx.', whatToExpect: 'Position, altitude, temperature, humidity, pressure from active sondes.', tips: ['Sondes transmit on 400–406 MHz', 'Set your region to narrow the scan range', 'Gain 40 dB is a good starting point'] },
        morse:       { title: 'CW/Morse Decoder',        icon: '📡', hardware: 'RTL-SDR + HF antenna (or upconverter)',    description: 'Decodes CW Morse code via Goertzel tone detection or OOK envelope detection.', whatToExpect: 'Decoded Morse characters, WPM estimate, signal level.', tips: ['CW Tone mode for HF amateur bands (e.g. 7.030, 14.060 MHz)', 'OOK Envelope mode for ISM/UHF signals', 'Use band presets for quick tuning to CW sub-bands'] },
        meteor:      { title: 'Meteor Scatter',           icon: '☄️', hardware: 'RTL-SDR + VHF antenna (143 MHz)',         description: 'Monitors VHF beacon reflections from meteor ionization trails.',             whatToExpect: 'Waterfall display with transient ping detections and event logging.', tips: ['GRAVES radar at 143.050 MHz is the primary target', 'Use a Yagi pointed south (from Europe) for best results', 'Peak activity during annual meteor showers (Perseids, Geminids)'] },
        ook: {
            title: 'OOK Signal Decoder',
            icon: '📡',
            hardware: 'RTL-SDR dongle',
            description: 'Decodes raw On-Off Keying (OOK) signals via rtl_433 flex decoder. Captures frames with configurable pulse timing and displays raw bits, hex, and ASCII — useful for reverse-engineering unknown ISM-band protocols.',
            whatToExpect: 'Decoded bit sequences, hex payloads, and ASCII interpretation. Each frame shows bit count, timestamp, and optional RSSI.',
            tips: [
                '<strong>Identifying modulation</strong> — <em>PWM</em>: pulse widths vary (short=0, long=1), gaps constant — most common for ISM remotes/sensors. <em>PPM</em>: pulses constant, gap widths encode data. <em>Manchester</em>: self-clocking, equal-width pulses, data in transitions.',
                '<strong>Finding pulse timing</strong> — Run <code>rtl_433 -f 433.92M -A</code> in a terminal to auto-analyze signals. It prints detected pulse widths (short/long) and gap timings. Use those values in the Short/Long Pulse fields.',
                '<strong>Common ISM timings</strong> — 300/600µs (weather stations, door sensors), 400/800µs (car keyfobs), 500/1500µs (garage doors, doorbells), 500µs Manchester (tire pressure monitors).',
                '<strong>Frequencies to try</strong> — 315 MHz (North America keyfobs), 433.920 MHz (global ISM), 868 MHz (Europe ISM), 915 MHz (US ISM/meters).',
                '<strong>Troubleshooting</strong> — Garbled output? Try halving or doubling pulse timings. No frames? Increase tolerance (±200–300µs). Too many frames? Enable deduplication. Wrong characters? Toggle MSB/LSB bit order.',
                '<strong>Tolerance &amp; reset</strong> — Tolerance is how much timing can drift (±150µs default). Reset limit is the silence gap that ends a frame (8000µs). Lower gap limit if frames are merging together.',
            ]
        },
    };

    function show(mode) {
        const data    = CONTENT[mode];
        const modal   = document.getElementById('cheatSheetModal');
        const content = document.getElementById('cheatSheetContent');
        if (!modal || !content) return;

        if (!data) {
            content.innerHTML = `<p style="color:var(--text-dim); font-family:var(--font-mono);">No cheat sheet for: ${mode}</p>`;
        } else {
            content.innerHTML = `
<div style="font-family:var(--font-mono, monospace);">
  <div style="font-size:24px; margin-bottom:4px;">${data.icon}</div>
  <h2 style="margin:0 0 8px; font-size:16px; color:var(--accent-cyan, #4aa3ff);">${data.title}</h2>
  <div style="font-size:11px; color:var(--text-dim); margin-bottom:12px; border-bottom:1px solid rgba(255,255,255,0.08); padding-bottom:8px;">
    Hardware: <span style="color:var(--text-secondary);">${data.hardware}</span>
  </div>
  <p style="font-size:12px; color:var(--text-secondary); margin:0 0 12px;">${data.description}</p>
  <div style="margin-bottom:12px;">
    <div style="font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-dim); margin-bottom:4px;">What to expect</div>
    <p style="font-size:12px; color:var(--text-secondary); margin:0;">${data.whatToExpect}</p>
  </div>
  <div>
    <div style="font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-dim); margin-bottom:6px;">Tips</div>
    <ul style="margin:0; padding-left:16px; display:flex; flex-direction:column; gap:4px;">
      ${data.tips.map(t => `<li style="font-size:11px; color:var(--text-secondary);">${t}</li>`).join('')}
    </ul>
  </div>
</div>`;
        }
        modal.style.display = 'flex';
    }

    function hide() {
        const modal = document.getElementById('cheatSheetModal');
        if (modal) modal.style.display = 'none';
    }

    function showForCurrentMode() {
        const mode = document.body.getAttribute('data-mode');
        if (mode) show(mode);
    }

    return { show, hide, showForCurrentMode };
})();

window.CheatSheets = CheatSheets;
