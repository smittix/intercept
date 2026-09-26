/**
 * APRS dashboard: full-page Leaflet map, station cards, packet log and signal
 * meter for the /aprs/dashboard page. The map/marker/stream/meter logic is the
 * same as the former SPA APRS mode; the glue below replaces the handful of
 * main-page globals it used (device selection, stop helper, agent state) so the
 * page is self-contained. Agent selection is handled by js/core/agents.js,
 * which owns the global `currentAgent` and populates #deviceSelect for remotes.
 */

// Stop-request timeouts (the SPA defined these on the main page).
const LOCAL_STOP_TIMEOUT_MS = 8000;
const REMOTE_STOP_TIMEOUT_MS = 8000;

// The dashboard has no live GPS stream; it centres on the observer location.
let gpsLastPosition = null;
let gpsConnected = false;

function getSelectedDevice() {
    const el = document.getElementById('deviceSelect');
    return el ? el.value : '0';
}

function getSelectedSDRType() {
    const el = document.getElementById('deviceSelect');
    const opt = el && el.options[el.selectedIndex];
    return (opt && opt.dataset.sdrType) || 'rtlsdr';
}

// No remote-SDR (rtl_tcp) UI on the dashboard; agent mode covers remote sources.
function getRemoteSDRConfig() {
    return null;
}

// Populate the local device selector. agents.js calls this when "Local" is
// selected and populates the same #deviceSelect itself for remote agents.
function refreshDevices() {
    fetch('/devices')
        .then((r) => r.json())
        .then((devices) => {
            const select = document.getElementById('deviceSelect');
            if (!select) return;
            select.innerHTML = '';
            if (!devices || devices.length === 0) {
                const opt = document.createElement('option');
                opt.value = '0';
                opt.textContent = 'No devices found';
                select.appendChild(opt);
                return;
            }
            devices.forEach((d) => {
                const opt = document.createElement('option');
                opt.value = d.index;
                opt.dataset.sdrType = d.sdr_type || 'rtlsdr';
                opt.textContent = `${d.index}: ${d.name}`;
                select.appendChild(opt);
            });
        })
        .catch(() => {});
}

// POST a stop request with a timeout (the SPA had a shared helper for this).
async function postStopRequest(url, timeoutMs = LOCAL_STOP_TIMEOUT_MS) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        await fetch(url, { method: 'POST', signal: controller.signal });
    } catch (e) {
        /* best effort; the UI has already returned to standby */
    } finally {
        clearTimeout(timer);
    }
}

let aprsMap = null;
let aprsMapOverlays = null;
let aprsMarkers = {};
let aprsEventSource = null;
let isAprsRunning = false;
let aprsPacketCount = 0;
let aprsStationCount = 0;
let aprsMeterLastUpdate = 0;
let aprsMeterCheckInterval = null;
let aprsClockInterval = null;
const APRS_METER_TIMEOUT = 5000; // 5 seconds for "no signal" state

// APRS user location (from GPS or shared observer location)
let aprsUserLocation = { lat: null, lon: null };

// Seed from configured observer location so the map centres on the
// user's position even without a live GPS fix.
(function _seedAprsLocation() {
    if (typeof ObserverLocation !== 'undefined' && ObserverLocation.getShared) {
        const shared = ObserverLocation.getShared();
        if (shared && aprsHasValidCoordinates(shared.lat, shared.lon)) {
            aprsUserLocation.lat = shared.lat;
            aprsUserLocation.lon = shared.lon;
            return;
        }
    }
    // Fallback: read the Jinja-injected defaults directly
    const lat = Number(window.INTERCEPT_DEFAULT_LAT);
    const lon = Number(window.INTERCEPT_DEFAULT_LON);
    if (aprsHasValidCoordinates(lat, lon)) {
        aprsUserLocation.lat = lat;
        aprsUserLocation.lon = lon;
    }
})();

// Listen for observer location changes from settings or other sources
window.addEventListener('observer-location-changed', function(e) {
    if (e.detail && aprsHasValidCoordinates(e.detail.lat, e.detail.lon)) {
        updateAprsUserLocation({ latitude: e.detail.lat, longitude: e.detail.lon });
    }
});

let aprsUserMarker = null;

// Calculate distance in miles using Haversine formula
function aprsCalculateDistanceMi(lat1, lon1, lat2, lon2) {
    const R = 3958.8; // Earth's radius in miles
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
        Math.sin(dLon / 2) * Math.sin(dLon / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
}

function aprsHasValidCoordinates(lat, lon) {
    return lat != null && lon != null &&
        Number.isFinite(Number(lat)) && Number.isFinite(Number(lon));
}

// Update APRS user location from GPS
function updateAprsUserLocation(position) {
    const lat = Number(position && position.latitude);
    const lon = Number(position && position.longitude);
    if (!aprsHasValidCoordinates(lat, lon)) return;

    aprsUserLocation.lat = lat;
    aprsUserLocation.lon = lon;

    // Update user marker on map
    if (aprsMap) {
        if (aprsUserMarker) {
            aprsUserMarker.setLatLng([lat, lon]);
        } else {
            aprsUserMarker = L.marker([lat, lon], {
                icon: L.divIcon({
                    className: 'aprs-user-marker',
                    html: '<div style="width: 14px; height: 14px; background: #ff0; border: 2px solid #000; border-radius: 50%; box-shadow: 0 0 10px #ff0;"></div>',
                    iconSize: [14, 14],
                    iconAnchor: [7, 7]
                }),
                zIndexOffset: 1000
            }).bindPopup('Your Location (GPS)').addTo(aprsMap);
        }

        // Center map on first GPS fix
        if (!aprsMap._gpsInitialized) {
            aprsMap.setView([lat, lon], 8);
            aprsMap._gpsInitialized = true;
        }
    }

    // Show GPS indicator
    const indicator = document.getElementById('aprsGpsIndicator');
    if (indicator) indicator.style.display = 'inline-flex';

    // Update distances in existing station list
    updateAprsStationDistances();
}

// Update distances for all stations in the list
function updateAprsStationDistances() {
    if (!aprsHasValidCoordinates(aprsUserLocation.lat, aprsUserLocation.lon)) return;

    // Update station list items
    const listEl = document.getElementById('aprsStationList');
    if (listEl) {
        listEl.querySelectorAll('[data-callsign]').forEach(stationEl => {
            const lat = parseFloat(stationEl.dataset.lat);
            const lon = parseFloat(stationEl.dataset.lon);
            if (!isNaN(lat) && !isNaN(lon)) {
                const dist = aprsCalculateDistanceMi(aprsUserLocation.lat, aprsUserLocation.lon, lat, lon);
                const distSpan = stationEl.querySelector('.aprs-distance');
                if (distSpan) {
                    distSpan.textContent = dist.toFixed(1) + ' mi';
                }
            }
        });
    }
}

function checkAprsTools() {
    fetch('/aprs/tools')
        .then(r => r.json())
        .then(data => {
            // Update function bar tool indicators
            const direwolfEl = document.getElementById('aprsStripDirewolf');
            const multimonEl = document.getElementById('aprsStripMultimon');

            if (direwolfEl) {
                direwolfEl.className = 'strip-tool' + (data.direwolf ? ' ok' : '');
                direwolfEl.title = 'direwolf: ' + (data.direwolf ? 'OK' : 'Missing');
            }
            if (multimonEl) {
                multimonEl.className = 'strip-tool' + (data.multimon_ng ? ' ok' : '');
                multimonEl.title = 'multimon-ng: ' + (data.multimon_ng ? 'OK' : 'Missing');
            }
        })
        .catch(() => {
            const direwolfEl = document.getElementById('aprsStripDirewolf');
            const multimonEl = document.getElementById('aprsStripMultimon');
            if (direwolfEl) {
                direwolfEl.className = 'strip-tool';
                direwolfEl.title = 'direwolf: Error';
            }
            if (multimonEl) {
                multimonEl.className = 'strip-tool';
                multimonEl.title = 'multimon-ng: Error';
            }
        });
}

async function initAprsMap() {
    if (aprsMap) return;

    const mapContainer = document.getElementById('aprsMap');
    if (!mapContainer) return;

    // Refresh from ObserverLocation in case it changed since page load
    if (!aprsHasValidCoordinates(aprsUserLocation.lat, aprsUserLocation.lon) ||
        (aprsUserLocation.lat === 0 && aprsUserLocation.lon === 0)) {
        if (typeof ObserverLocation !== 'undefined' && ObserverLocation.getShared) {
            const shared = ObserverLocation.getShared();
            if (shared && aprsHasValidCoordinates(shared.lat, shared.lon)) {
                aprsUserLocation.lat = shared.lat;
                aprsUserLocation.lon = shared.lon;
            }
        }
    }

    // Use GPS location if available, otherwise default to center of US
    const gpsLat = Number(gpsLastPosition && gpsLastPosition.latitude);
    const gpsLon = Number(gpsLastPosition && gpsLastPosition.longitude);
    const hasUserLocation = aprsHasValidCoordinates(aprsUserLocation.lat, aprsUserLocation.lon);
    const hasGpsLocation = aprsHasValidCoordinates(gpsLat, gpsLon);

    const initialLat = hasUserLocation ? aprsUserLocation.lat : (hasGpsLocation ? gpsLat : 39.8283);
    const initialLon = hasUserLocation ? aprsUserLocation.lon : (hasGpsLocation ? gpsLon : -98.5795);
    const initialZoom = (hasUserLocation || hasGpsLocation) ? 8 : 4;

    aprsMap = MapUtils.init('aprsMap', {
        center: [initialLat, initialLon],
        zoom: initialZoom,
        minZoom: 2,
        maxZoom: 18,
    });
    if (!aprsMap) return;
    window.aprsMap = aprsMap;

    aprsMapOverlays = MapUtils.addTacticalOverlays(aprsMap, {
        scaleBar: true,
    });

    // Add user marker if GPS position is already available
    if (gpsConnected && hasGpsLocation) {
        updateAprsUserLocation({ latitude: gpsLat, longitude: gpsLon });
        aprsMap._gpsInitialized = true;
    }

    // Update time display (both map header and function bar)
    if (aprsClockInterval) VisibleInterval.clear(aprsClockInterval);
    aprsClockInterval = VisibleInterval.set(() => {
        const now = new Date();
        const timeStr = now.toLocaleTimeString('en-US', { hour12: false });
        const utcStr = now.toUTCString().slice(17, 25) + ' UTC';

        const timeEl = document.getElementById('aprsMapTime');
        if (timeEl) timeEl.textContent = timeStr;

        const stripTimeEl = document.getElementById('aprsStripTime');
        if (stripTimeEl) stripTimeEl.textContent = utcStr;
    }, 1000);
}

function destroyAprsMode() {
    stopAprsMeterCheck();
    if (aprsEventSource) {
        aprsEventSource.close();
        aprsEventSource = null;
    }
    if (aprsPollTimer) {
        clearInterval(aprsPollTimer);
        aprsPollTimer = null;
    }
    if (aprsClockInterval) {
        VisibleInterval.clear(aprsClockInterval);
        aprsClockInterval = null;
    }
    if (aprsMap) {
        try {
            aprsMap.remove();
        } catch (_) {}
        aprsMap = null;
        window.aprsMap = null;
        aprsMapOverlays = null;
    }
    aprsMarkers = {};
    aprsUserMarker = null;
}

function updateAprsStatus(state, freq) {
    // Update function bar status
    const stripDot = document.getElementById('aprsStripDot');
    const stripStatus = document.getElementById('aprsStripStatus');
    const stripFreq = document.getElementById('aprsStripFreq');

    if (stripDot) {
        stripDot.className = 'status-dot ' + state;
    }
    if (stripStatus) {
        stripStatus.textContent = state.toUpperCase();
        if (state === 'listening') {
            stripStatus.style.color = 'var(--accent-cyan)';
        } else if (state === 'tracking') {
            stripStatus.style.color = 'var(--accent-green)';
        } else if (state === 'error') {
            stripStatus.style.color = 'var(--accent-red)';
        } else {
            stripStatus.style.color = '';
        }
    }
    if (freq && stripFreq) {
        stripFreq.textContent = freq;
    }
}

// APRS mode polling timer for agent mode
let aprsPollTimer = null;
let aprsCurrentAgent = null;
const aprsAgentStationSignatures = new Map();

function resetAprsAgentStationTracking() {
    aprsAgentStationSignatures.clear();
}

function extractAprsStationsFromPayload(payload) {
    if (!payload) return [];
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload.stations)) return payload.stations;
    if (Array.isArray(payload.data)) return payload.data;
    if (payload.data && Array.isArray(payload.data.stations)) return payload.data.stations;
    if (payload.data && Array.isArray(payload.data.data)) return payload.data.data;
    if (payload.result && Array.isArray(payload.result.stations)) return payload.result.stations;
    if (payload.result && Array.isArray(payload.result.data)) return payload.result.data;
    if (payload.data && payload.data.result && Array.isArray(payload.data.result.stations)) {
        return payload.data.result.stations;
    }
    return [];
}

function getAprsStationSignature(station) {
    if (!station || typeof station !== 'object') return '';
    const receivedAt = station.received_at || station.last_seen || station.timestamp || '';
    const lat = station.lat ?? station.latitude ?? '';
    const lon = station.lon ?? station.longitude ?? '';
    const payloadHint = station.raw || station.comment || station.path || '';
    return `${receivedAt}|${lat},${lon}|${payloadHint}`;
}

function processAprsAgentStations(stations, agentName) {
    if (!Array.isArray(stations) || stations.length === 0) return;

    stations.forEach((station) => {
        const callsign = String(station && station.callsign ? station.callsign : '').trim();
        if (!callsign) return;
        const lat = station.lat ?? station.latitude ?? null;
        const lon = station.lon ?? station.longitude ?? null;

        const signature = getAprsStationSignature(station);
        if (aprsAgentStationSignatures.get(callsign) === signature) return;
        aprsAgentStationSignatures.set(callsign, signature);

        aprsPacketCount++;
        document.getElementById('aprsPacketCount').textContent = aprsPacketCount;
        document.getElementById('aprsStripPackets').textContent = aprsPacketCount;

        const dot = document.getElementById('aprsStripDot');
        if (dot && !dot.classList.contains('tracking')) {
            updateAprsStatus('tracking');
        }

        processAprsPacket({
            type: 'aprs',
            ...station,
            lat,
            lon,
            callsign,
            agent_name: station.agent_name || agentName || 'Remote Agent'
        });
    });
}

async function loadAprsStationSnapshot(isAgentMode = false) {
    try {
        const endpoint = (isAgentMode && aprsCurrentAgent)
            ? `/controller/agents/${aprsCurrentAgent}/aprs/data`
            : '/aprs/stations';
        const response = await fetch(endpoint);
        if (!response.ok) return;
        const payload = await response.json();
        const stations = extractAprsStationsFromPayload(payload);
        if (!Array.isArray(stations) || stations.length === 0) return;
        if (isAgentMode) {
            processAprsAgentStations(stations, payload.agent_name);
            return;
        }

        stations.forEach((station) => {
            const callsign = String(station && station.callsign ? station.callsign : '').trim();
            if (!callsign) return;
            const packet = {
                type: 'aprs',
                ...station,
                callsign,
                lat: station.lat ?? station.latitude ?? null,
                lon: station.lon ?? station.longitude ?? null,
                packet_type: station.packet_type || 'position',
            };
            if (aprsHasValidCoordinates(packet.lat, packet.lon) && aprsMap) {
                updateAprsMarker(packet);
            }
            updateAprsStationList(packet);
        });
    } catch (err) {
        console.debug('APRS snapshot load failed:', err);
    }
}

function startAprs() {
    // Get values from function bar controls
    const region = document.getElementById('aprsStripRegion').value;
    const device = getSelectedDevice();
    const gain = document.getElementById('aprsStripGain').value;
    const sdrType = (typeof getSelectedSDRType === 'function') ? getSelectedSDRType() : 'rtlsdr';

    // Check if using agent mode
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
    aprsCurrentAgent = isAgentMode ? currentAgent : null;

    // Check for remote SDR (only for local mode)
    const remoteConfig = isAgentMode ? null : getRemoteSDRConfig();
    if (remoteConfig === false) return; // Validation failed

    // Build request body
    const requestBody = {
        region,
        device: parseInt(device),
        gain: parseInt(gain),
        sdr_type: sdrType
    };

    // Add rtl_tcp params if using remote SDR
    if (remoteConfig) {
        requestBody.rtl_tcp_host = remoteConfig.host;
        requestBody.rtl_tcp_port = remoteConfig.port;
    }

    // Add custom frequency if selected
    if (region === 'custom') {
        const customFreq = document.getElementById('aprsStripCustomFreq').value;
        if (!customFreq) {
            alert('Please enter a custom frequency');
            return;
        }
        requestBody.frequency = customFreq;
    }

    // Determine endpoint based on agent mode
    const endpoint = isAgentMode
        ? `/controller/agents/${currentAgent}/aprs/start`
        : '/aprs/start';

    fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
    })
    .then(r => r.json())
    .then(data => {
        // Handle controller proxy response format
        const scanResult = isAgentMode && data.result ? data.result : data;

        if (scanResult.status === 'started' || scanResult.status === 'success') {
            isAprsRunning = true;
            aprsPacketCount = 0;
            aprsStationCount = 0;
            resetAprsAgentStationTracking();

            if (aprsMap) {
                Object.values(aprsMarkers).forEach((marker) => {
                    try {
                        aprsMap.removeLayer(marker);
                    } catch (_) {}
                });
            }
            aprsMarkers = {};

            // Initialize APRS filter bar and clear history
            const filterContainer = document.getElementById('aprsFilterBarContainer');
            const stationList = document.getElementById('aprsStationList');
            if (filterContainer && !document.getElementById('aprsFilterBar')) {
                const filterBar = SignalCards.createAprsFilterBar(stationList);
                filterContainer.appendChild(filterBar);
            }
            SignalCards.clearAddressHistory('aprs');

            // Clear existing station cards
            stationList.innerHTML = '<div class="signal-cards-placeholder" style="padding: 20px; text-align: center; color: var(--text-muted);">Waiting for stations...</div>';
            const packetLog = document.getElementById('aprsPacketLog');
            if (packetLog) {
                packetLog.innerHTML = '<div style="color: var(--text-muted);">Waiting for packets...</div>';
            }
            document.getElementById('aprsPacketCount').textContent = '0';
            document.getElementById('aprsStationCount').textContent = '0';

            // Update function bar buttons
            document.getElementById('aprsStripStartBtn').style.display = 'none';
            document.getElementById('aprsStripStopBtn').style.display = 'inline-block';
            // Update map status
            document.getElementById('aprsMapStatus').textContent = 'TRACKING';
            document.getElementById('aprsMapStatus').style.color = 'var(--accent-green)';
            // Update function bar status
            updateAprsStatus('listening', scanResult.frequency);
            // Reset function bar stats
            document.getElementById('aprsStripStations').textContent = '0';
            document.getElementById('aprsStripPackets').textContent = '0';
            document.getElementById('aprsStripSignal').textContent = '--';
            // Disable controls while running
            document.getElementById('aprsStripRegion').disabled = true;
            document.getElementById('aprsStripGain').disabled = true;
            const customFreqInput = document.getElementById('aprsStripCustomFreq');
            if (customFreqInput) customFreqInput.disabled = true;
            startAprsMeterCheck();
            startAprsStream(isAgentMode);
            // Backfill current stations in case position packets arrived before
            // map initialization or SSE attachment.
            loadAprsStationSnapshot(isAgentMode);
        } else {
            alert('APRS Error: ' + (scanResult.message || scanResult.error || 'Failed to start'));
            updateAprsStatus('error');
        }
    })
    .catch(err => {
        alert('APRS Error: ' + err);
        updateAprsStatus('error');
    });
}

async function stopAprs() {
    const isAgentMode = aprsCurrentAgent !== null;
    const endpoint = isAgentMode
        ? `/controller/agents/${aprsCurrentAgent}/aprs/stop`
        : '/aprs/stop';
    const timeoutMs = isAgentMode ? REMOTE_STOP_TIMEOUT_MS : LOCAL_STOP_TIMEOUT_MS;

    isAprsRunning = false;
    aprsCurrentAgent = null;
    resetAprsAgentStationTracking();
    document.getElementById('aprsStripStopBtn').style.display = 'none';
    document.getElementById('aprsMapStatus').textContent = 'STOPPING';
    document.getElementById('aprsMapStatus').style.color = '';
    updateAprsStatus('standby');
    document.getElementById('aprsStripFreq').textContent = '--';
    document.getElementById('aprsStripSignal').textContent = '--';
    document.getElementById('aprsStripRegion').disabled = false;
    document.getElementById('aprsStripGain').disabled = false;
    const customFreqInput = document.getElementById('aprsStripCustomFreq');
    if (customFreqInput) customFreqInput.disabled = false;
    const signalStat = document.getElementById('aprsStripSignalStat');
    if (signalStat) {
        signalStat.classList.remove('good', 'warning', 'poor');
    }
    stopAprsMeterCheck();
    if (aprsEventSource) {
        aprsEventSource.close();
        aprsEventSource = null;
    }
    if (aprsPollTimer) {
        clearInterval(aprsPollTimer);
        aprsPollTimer = null;
    }

    await postStopRequest(endpoint, timeoutMs);
    document.getElementById('aprsStripStartBtn').style.display = 'inline-block';
    document.getElementById('aprsMapStatus').textContent = 'STANDBY';
}

function startAprsStream(isAgentMode = false) {
    if (aprsEventSource) aprsEventSource.close();

    // Use different stream endpoint for agent mode
    const streamUrl = isAgentMode ? '/controller/stream/all' : '/aprs/stream';
    aprsEventSource = new EventSource(streamUrl + (streamUrl.includes('?') ? '&' : '?') + 't=' + Date.now());

    aprsEventSource.onmessage = function (e) {
        const data = JSON.parse(e.data);

        if (isAgentMode) {
            // Handle multi-agent stream format
            if (data.scan_type === 'aprs' && data.payload) {
                const payload = data.payload;
                if (payload.type === 'aprs') {
                    aprsPacketCount++;
                    document.getElementById('aprsPacketCount').textContent = aprsPacketCount;
                    document.getElementById('aprsStripPackets').textContent = aprsPacketCount;
                    const dot = document.getElementById('aprsStripDot');
                    if (dot && !dot.classList.contains('tracking')) {
                        updateAprsStatus('tracking');
                    }
                    // Add agent info
                    payload.agent_name = data.agent_name;
                    processAprsPacket(payload);
                } else if (payload.type === 'meter') {
                    updateAprsMeter(payload.level);
                } else {
                    const stations = extractAprsStationsFromPayload(payload);
                    processAprsAgentStations(stations, data.agent_name);
                }
            }
        } else {
            // Local stream format
            if (data.type === 'aprs') {
                aprsPacketCount++;
                // Update map footer and function bar
                document.getElementById('aprsPacketCount').textContent = aprsPacketCount;
                document.getElementById('aprsStripPackets').textContent = aprsPacketCount;
                // Switch to tracking state on first packet
                const dot = document.getElementById('aprsStripDot');
                if (dot && !dot.classList.contains('tracking')) {
                    updateAprsStatus('tracking');
                }
                processAprsPacket(data);
            } else if (data.type === 'meter') {
                // Update signal indicator in function bar
                updateAprsMeter(data.level);
            }
        }
    };

    aprsEventSource.onerror = function () {
        console.error('APRS stream error');
        updateAprsStatus('error');
    };

    // Start polling fallback for agent mode
    if (isAgentMode) {
        startAprsPolling();
    }
}

function startAprsPolling() {
    if (aprsPollTimer) return;
    resetAprsAgentStationTracking();

    const pollInterval = 2000;
    aprsPollTimer = setInterval(async () => {
        if (!isAprsRunning || !aprsCurrentAgent) {
            clearInterval(aprsPollTimer);
            aprsPollTimer = null;
            return;
        }

        try {
            const response = await fetch(`/controller/agents/${aprsCurrentAgent}/aprs/data`);
            if (!response.ok) return;

            const payload = await response.json();
            const stations = extractAprsStationsFromPayload(payload);
            const agentName = payload.agent_name ||
                (payload.data && payload.data.agent_name) ||
                'Remote Agent';
            processAprsAgentStations(stations, agentName);
        } catch (err) {
            console.error('APRS polling error:', err);
        }
    }, pollInterval);
}

// Signal Meter Functions
function resetAprsMeter() {
    aprsMeterLastUpdate = 0;
    // Reset function bar signal indicator
    const signalEl = document.getElementById('aprsStripSignal');
    const signalStat = document.getElementById('aprsStripSignalStat');
    if (signalEl) signalEl.textContent = '--';
    if (signalStat) signalStat.classList.remove('good', 'warning', 'poor');
}

function updateAprsMeter(level) {
    aprsMeterLastUpdate = Date.now();

    // Update function bar signal indicator
    const signalEl = document.getElementById('aprsStripSignal');
    const signalStat = document.getElementById('aprsStripSignalStat');

    if (signalEl) {
        // Show signal level as bars
        if (level >= 60) {
            signalEl.textContent = '●●●';
        } else if (level >= 30) {
            signalEl.textContent = '●●○';
        } else if (level >= 10) {
            signalEl.textContent = '●○○';
        } else {
            signalEl.textContent = '○○○';
        }
    }

    if (signalStat) {
        signalStat.classList.remove('good', 'warning', 'poor');
        if (level >= 60) {
            signalStat.classList.add('good');
        } else if (level >= 30) {
            signalStat.classList.add('warning');
        } else {
            signalStat.classList.add('poor');
        }
    }
}

function startAprsMeterCheck() {
    // Check for no-signal state every second
    aprsMeterCheckInterval = setInterval(function () {
        if (aprsMeterLastUpdate > 0 && (Date.now() - aprsMeterLastUpdate) > APRS_METER_TIMEOUT) {
            // No meter updates for 5 seconds - show no-signal state
            const signalEl = document.getElementById('aprsStripSignal');
            const signalStat = document.getElementById('aprsStripSignalStat');
            if (signalEl) signalEl.textContent = '○○○';
            if (signalStat) {
                signalStat.classList.remove('good', 'warning');
                signalStat.classList.add('poor');
            }
        }
    }, 1000);
}

function stopAprsMeterCheck() {
    if (aprsMeterCheckInterval) {
        clearInterval(aprsMeterCheckInterval);
        aprsMeterCheckInterval = null;
    }
}

// Handle region selection changes to show/hide custom frequency input
document.addEventListener('DOMContentLoaded', function() {
    const regionSelect = document.getElementById('aprsStripRegion');
    const customFreqControl = document.getElementById('aprsStripCustomFreqControl');

    if (regionSelect && customFreqControl) {
        regionSelect.addEventListener('change', function() {
            if (this.value === 'custom') {
                customFreqControl.style.display = 'flex';
            } else {
                customFreqControl.style.display = 'none';
            }
        });
    }
});

function processAprsPacket(packet) {
    // Update packet log
    const logEl = document.getElementById('aprsPacketLog');
    const logEntry = document.createElement('div');
    logEntry.style.cssText = 'padding: 3px 0; border-bottom: 1px solid var(--border-color);';

    const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const callsign = packet.callsign || 'UNKNOWN';
    const packetType = packet.packet_type || 'unknown';

    logEntry.innerHTML = `<span style="color: var(--text-muted);">${time}</span> <span style="color: var(--accent-cyan); font-weight: bold;">${callsign}</span> <span style="color: var(--accent-green);">[${packetType}]</span>`;

    // Remove placeholder if present
    const placeholder = logEl.querySelector('div[style*="color: var(--text-muted)"]');
    if (placeholder && placeholder.textContent.includes('Waiting')) {
        placeholder.remove();
    }

    logEl.insertBefore(logEntry, logEl.firstChild);

    // Keep log manageable
    while (logEl.children.length > 100) {
        logEl.lastElementChild.remove();
    }

    // Update map if position data
    if (aprsHasValidCoordinates(packet.lat, packet.lon) && aprsMap) {
        updateAprsMarker(packet);
    }

    // Update station list
    updateAprsStationList(packet);
}

function getAprsMarkerCategory(packet) {
    const symbolCode = (packet.symbol && packet.symbol.length > 1) ? packet.symbol[1] : '';
    const speed = parseFloat(packet.speed || 0);
    const vehicleSymbols = new Set(['>', 'k', 'u', 'v', '[', '<', 's', 'b', 'j']);

    if ((Number.isFinite(speed) && speed > 2) || vehicleSymbols.has(symbolCode)) {
        return 'vehicle';
    }
    return 'tower';
}

function getAprsMarkerSvg(category) {
    if (category === 'vehicle') {
        return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 14l2-5a2 2 0 0 1 2-1h10a2 2 0 0 1 2 1l2 5v4h-2a2 2 0 0 1-4 0H9a2 2 0 0 1-4 0H3v-4z"/><circle cx="7" cy="18" r="1.7"/><circle cx="17" cy="18" r="1.7"/></svg>';
    }
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l3 7h-2l1 3h-2l1 8h-2l1-8h-2l1-3H9l3-7z"/><path d="M5 21h14" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';
}

function buildAprsMarkerIcon(packet) {
    const category = getAprsMarkerCategory(packet);
    const callsign = packet.callsign || 'UNKNOWN';
    const html = `
        <div class="aprs-map-marker ${category}">
            <span class="aprs-map-marker-icon">${getAprsMarkerSvg(category)}</span>
            <span class="aprs-map-marker-label">${callsign}</span>
        </div>
    `;
    return L.divIcon({
        className: 'aprs-map-marker-wrap',
        html,
        iconSize: [110, 24],
        iconAnchor: [55, 12]
    });
}

function updateAprsMarker(packet) {
    const callsign = packet.callsign;
    const lat = Number(packet.lat);
    const lon = Number(packet.lon);
    if (!aprsHasValidCoordinates(lat, lon)) {
        return;
    }

    // Calculate distance if user location available
    let distStr = '';
    if (aprsHasValidCoordinates(aprsUserLocation.lat, aprsUserLocation.lon)) {
        const dist = aprsCalculateDistanceMi(aprsUserLocation.lat, aprsUserLocation.lon, lat, lon);
        distStr = `Distance: ${dist.toFixed(1)} mi<br>`;
    }

    if (aprsMarkers[callsign]) {
        // Update existing marker position and popup
        aprsMarkers[callsign].setLatLng([lat, lon]);
        aprsMarkers[callsign].setIcon(buildAprsMarkerIcon(packet));
        aprsMarkers[callsign].setPopupContent(`
            <div style="font-family: monospace;">
                <strong>${callsign}</strong><br>
                Position: ${lat.toFixed(4)}, ${lon.toFixed(4)}<br>
                ${distStr}
                ${packet.altitude ? `Altitude: ${packet.altitude} ft<br>` : ''}
                ${packet.speed ? `Speed: ${packet.speed} kts<br>` : ''}
                ${packet.course ? `Course: ${packet.course}°<br>` : ''}
            </div>
        `);
    } else {
        // Create new marker
        aprsStationCount++;
        // Update map footer and function bar
        document.getElementById('aprsStationCount').textContent = aprsStationCount;
        document.getElementById('aprsStripStations').textContent = aprsStationCount;

        const marker = L.marker([lat, lon], { icon: buildAprsMarkerIcon(packet) }).addTo(aprsMap);

        marker.bindPopup(`
            <div style="font-family: monospace;">
                <strong>${callsign}</strong><br>
                Position: ${lat.toFixed(4)}, ${lon.toFixed(4)}<br>
                ${distStr}
                ${packet.altitude ? `Altitude: ${packet.altitude} ft<br>` : ''}
                ${packet.speed ? `Speed: ${packet.speed} kts<br>` : ''}
                ${packet.course ? `Course: ${packet.course}°<br>` : ''}
            </div>
        `);

        aprsMarkers[callsign] = marker;
    }
}

function updateAprsStationList(packet) {
    const listEl = document.getElementById('aprsStationList');
    const callsign = packet.callsign;

    // Remove placeholder if present
    const placeholder = listEl.querySelector('.signal-cards-placeholder');
    if (placeholder) {
        placeholder.remove();
    }

    // Calculate distance if user location available
    let distance = null;
    const hasPos = aprsHasValidCoordinates(packet.lat, packet.lon);
    const lat = hasPos ? Number(packet.lat) : null;
    const lon = hasPos ? Number(packet.lon) : null;
    if (hasPos && aprsHasValidCoordinates(aprsUserLocation.lat, aprsUserLocation.lon)) {
        distance = aprsCalculateDistanceMi(aprsUserLocation.lat, aprsUserLocation.lon, lat, lon);
    }

    // Check if station already exists
    let stationEl = listEl.querySelector(`[data-callsign="${callsign}"]`);
    const isExisting = !!stationEl;

    // Prepare message object for card creation
    const msg = {
        callsign: callsign,
        packet_type: packet.packet_type || 'unknown',
        latitude: lat,
        longitude: lon,
        altitude: packet.altitude,
        speed: packet.speed,
        course: packet.course,
        comment: packet.comment,
        symbol: packet.symbol,
        path: packet.path,
        raw: packet.raw,
        timestamp: new Date().toISOString(),
        distance: distance
    };

    // Create or update the card
    const newCard = SignalCards.createAprsCard(msg, { compact: true });
    newCard.dataset.callsign = callsign;

    // Store position for distance updates
    if (hasPos) {
        newCard.dataset.lat = lat;
        newCard.dataset.lon = lon;
    }

    // Add click handler to focus map
    newCard.style.cursor = 'pointer';
    newCard.addEventListener('click', (e) => {
        // Don't trigger if clicking on buttons
        if (e.target.closest('button')) return;
        if (aprsMarkers[callsign] && aprsMap) {
            aprsMap.setView(aprsMarkers[callsign].getLatLng(), 10);
            aprsMarkers[callsign].openPopup();
        }
    });

    if (isExisting) {
        // Replace existing card
        stationEl.replaceWith(newCard);
    } else {
        // Insert new card at top
        listEl.insertBefore(newCard, listEl.firstChild);
    }

    // Keep list manageable (use live childElementCount, not static NodeList)
    const MAX_APRS_STATION_CARDS = 200;
    while (listEl.childElementCount > MAX_APRS_STATION_CARDS && listEl.lastElementChild) {
        listEl.removeChild(listEl.lastElementChild);
    }

    // Update filter counts if filter bar exists
    SignalCards.updateCounts(listEl);
}

// Bring the page up: check tools, build the map, load local devices, and
// backfill any stations already tracked from a running decoder.
document.addEventListener('DOMContentLoaded', function () {
    checkAprsTools();
    initAprsMap();
    refreshDevices();
    loadAprsStationSnapshot(false);
});
