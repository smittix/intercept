/**
 * Wi-Fi mode for the main page: interface and monitor-mode control,
 * scanning, the network/client lists and the channel and security
 * visualisations. Moved out of templates/index.html unchanged (only
 * de-indented); a classic script loaded after the inline block, sharing its
 * top-level names as before.
 */
// ============== WIFI RECONNAISSANCE ==============

let wifiEventSource = null;
let monitorInterface = null;
let wifiNetworks = {};
let wifiClients = {};
let apCount = 0;
let clientCount = 0;
let handshakeCount = 0;
let rogueApCount = 0;
let droneCount = 0;
let detectedDrones = {};  // Track detected drones by BSSID
let ssidToBssids = {};  // Track SSIDs to their BSSIDs for rogue AP detection
let rogueApDetails = {};  // Store details about rogue APs: {ssid: [{bssid, signal, channel, firstSeen}]}
let rogueBssids = new Set();  // Track all BSSIDs that are suspected rogues
let activeCapture = null;  // {bssid, channel, file, startTime, pollInterval}
let watchMacs = JSON.parse(localStorage.getItem('watchMacs') || '[]');
let alertedMacs = new Set();  // Prevent duplicate alerts per session
let selectedWifiDevice = null;  // Selected network or client for details view
let selectedWifiType = null;  // 'network' or 'client'

// 5GHz channel mapping for the graph
const channels5g = ['36', '40', '44', '48', '52', '56', '60', '64', '100', '149', '153', '157', '161', '165'];

// Drone SSID patterns for detection
const dronePatterns = [
    /^DJI[-_]/i, /Mavic/i, /Phantom/i, /^Spark[-_]/i, /^Mini[-_]/i, /^Air[-_]/i,
    /Inspire/i, /Matrice/i, /Avata/i, /^FPV[-_]/i, /Osmo/i, /RoboMaster/i, /Tello/i,
    /Parrot/i, /Bebop/i, /Anafi/i, /^Disco[-_]/i, /Mambo/i, /Swing/i,
    /Autel/i, /^EVO[-_]/i, /Dragonfish/i, /Skydio/i,
    /Holy.?Stone/i, /Potensic/i, /SYMA/i, /Hubsan/i, /Eachine/i, /FIMI/i,
    /Yuneec/i, /Typhoon/i, /PowerVision/i, /PowerEgg/i,
    /Drone/i, /^UAV[-_]/i, /Quadcopter/i, /^RC[-_]Drone/i
];

// Drone OUI prefixes
const droneOuiPrefixes = {
    '60:60:1F': 'DJI', '48:1C:B9': 'DJI', '34:D2:62': 'DJI', 'E0:DB:55': 'DJI',
    'C8:6C:87': 'DJI', 'A0:14:3D': 'DJI', '70:D7:11': 'DJI', '98:3A:56': 'DJI',
    '90:03:B7': 'Parrot', '00:12:1C': 'Parrot', '00:26:7E': 'Parrot',
    '8C:F5:A3': 'Autel', 'D8:E0:E1': 'Autel', 'F8:0F:6F': 'Skydio'
};

// Check if network is a drone
function isDrone(ssid, bssid) {
    // Check SSID patterns
    if (ssid) {
        for (const pattern of dronePatterns) {
            if (pattern.test(ssid)) {
                return { isDrone: true, method: 'SSID', brand: ssid.split(/[-_\s]/)[0] };
            }
        }
    }
    // Check OUI prefix
    if (bssid) {
        const prefix = bssid.substring(0, 8).toUpperCase();
        if (droneOuiPrefixes[prefix]) {
            return { isDrone: true, method: 'OUI', brand: droneOuiPrefixes[prefix] };
        }
    }
    return { isDrone: false };
}

// Handle drone detection
function handleDroneDetection(net, droneInfo) {
    if (detectedDrones[net.bssid]) return; // Already detected

    detectedDrones[net.bssid] = {
        ssid: net.essid,
        bssid: net.bssid,
        brand: droneInfo.brand,
        method: droneInfo.method,
        signal: net.power,
        channel: net.channel,
        firstSeen: new Date().toISOString()
    };

    droneCount++;
    document.getElementById('droneCount').textContent = droneCount;

    // Calculate approximate distance from signal strength
    const rssi = parseInt(net.power) || -70;
    const distance = estimateDroneDistance(rssi);

    // Triple alert for drones
    playAlert();
    setTimeout(playAlert, 200);
    setTimeout(playAlert, 400);

    // Show drone alert
    showDroneAlert(net.essid, net.bssid, droneInfo.brand, distance, rssi);
}

// Estimate distance from RSSI (rough approximation)
function estimateDroneDistance(rssi) {
    // Using free-space path loss model (very approximate)
    // Reference: -30 dBm at 1 meter
    const txPower = -30;
    const n = 2.5; // Path loss exponent (2-4, higher for obstacles)
    const distance = Math.pow(10, (txPower - rssi) / (10 * n));
    return Math.round(distance);
}

// Show drone alert popup
function showDroneAlert(ssid, bssid, brand, distance, rssi) {
    const alertDiv = document.createElement('div');
    alertDiv.className = 'drone-alert';
    alertDiv.innerHTML = `
        <div style="font-weight: bold; color: var(--accent-orange); font-size: 16px;">DRONE DETECTED</div>
        <div style="margin: 10px 0;">
            <div><strong>SSID:</strong> ${escapeHtml(ssid || 'Unknown')}</div>
            <div><strong>BSSID:</strong> ${bssid}</div>
            <div><strong>Brand:</strong> ${brand || 'Unknown'}</div>
            <div><strong>Signal:</strong> ${rssi} dBm</div>
            <div><strong>Est. Distance:</strong> ~${distance}m</div>
        </div>
        <button onclick="this.parentElement.remove()" style="padding: 6px 16px; cursor: pointer; background: var(--accent-orange); border: none; color: #000; border-radius: 4px;">Dismiss</button>
    `;
    alertDiv.style.cssText = 'position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: #1a1a2e; border: 2px solid var(--accent-orange); padding: 20px; border-radius: 8px; z-index: 10000; text-align: center; box-shadow: 0 0 30px rgba(255,165,0,0.5); min-width: 280px;';
    document.body.appendChild(alertDiv);
    setTimeout(() => { if (alertDiv.parentElement) alertDiv.remove(); }, 15000);
}

// Initialize watch list display
function initWatchList() {
    updateWatchListDisplay();
}

// Add MAC to watch list
function addWatchMac() {
    const input = document.getElementById('watchMacInput');
    const mac = input.value.trim().toUpperCase();
    if (!mac || !/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(mac)) {
        alert('Please enter a valid MAC address (AA:BB:CC:DD:EE:FF)');
        return;
    }
    if (!watchMacs.includes(mac)) {
        watchMacs.push(mac);
        localStorage.setItem('watchMacs', JSON.stringify(watchMacs));
        updateWatchListDisplay();
    }
    input.value = '';
}

// Remove MAC from watch list
function removeWatchMac(mac) {
    watchMacs = watchMacs.filter(m => m !== mac);
    localStorage.setItem('watchMacs', JSON.stringify(watchMacs));
    alertedMacs.delete(mac);
    updateWatchListDisplay();
}

// Update watch list display
function updateWatchListDisplay() {
    const container = document.getElementById('watchList');
    if (!container) return;
    if (watchMacs.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim);">No MACs in watch list</div>';
    } else {
        container.innerHTML = watchMacs.map(mac =>
            `<div style="display: flex; justify-content: space-between; align-items: center; padding: 2px 0;">
                <span>${mac}</span>
                <button onclick="removeWatchMac('${mac}')" style="background: none; border: none; color: var(--accent-red); cursor: pointer; font-size: 10px;">✕</button>
            </div>`
        ).join('');
    }
}

// Check if MAC is in watch list and alert
function checkWatchList(mac, type) {
    const upperMac = mac.toUpperCase();
    if (watchMacs.includes(upperMac) && !alertedMacs.has(upperMac)) {
        alertedMacs.add(upperMac);
        // Play alert sound multiple times for urgency
        playAlert();
        setTimeout(playAlert, 300);
        setTimeout(playAlert, 600);
        // Show prominent alert
        showProximityAlert(mac, type);
    }
}

// Show proximity alert popup
function showProximityAlert(mac, type) {
    const alertDiv = document.createElement('div');
    alertDiv.className = 'proximity-alert';
    alertDiv.innerHTML = `
        <div style="font-weight: bold; color: var(--accent-red);">⚠ PROXIMITY ALERT</div>
        <div>Watched ${type} detected:</div>
        <div style="font-family: monospace; font-size: 14px;">${mac}</div>
        <button onclick="this.parentElement.remove()" style="margin-top: 8px; padding: 4px 12px; cursor: pointer;">Dismiss</button>
    `;
    alertDiv.style.cssText = 'position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: #1a1a2e; border: 2px solid var(--accent-red); padding: 20px; border-radius: 8px; z-index: 10000; text-align: center; box-shadow: 0 0 30px rgba(255,0,0,0.5);';
    document.body.appendChild(alertDiv);
    // Auto-dismiss after 10 seconds
    setTimeout(() => alertDiv.remove(), 10000);
}

// Check for rogue APs (same SSID, different BSSID)
// Extract OUI (manufacturer ID) from MAC address
function getOui(mac) {
    if (!mac) return '';
    return mac.toUpperCase().substring(0, 8);  // First 3 octets: "AA:BB:CC"
}

function checkRogueAP(ssid, bssid, channel, signal) {
    if (!ssid || ssid === 'Hidden' || ssid === '[Hidden]') return false;

    if (!ssidToBssids[ssid]) {
        ssidToBssids[ssid] = new Set();
    }

    // Store details for this BSSID
    if (!rogueApDetails[ssid]) {
        rogueApDetails[ssid] = [];
    }

    // Check if we already have this BSSID stored
    const existingEntry = rogueApDetails[ssid].find(e => e.bssid === bssid);
    if (!existingEntry) {
        rogueApDetails[ssid].push({
            bssid: bssid,
            channel: channel || '?',
            signal: signal || '?',
            oui: getOui(bssid),
            firstSeen: new Date().toLocaleTimeString()
        });
    }

    const isNewBssid = !ssidToBssids[ssid].has(bssid);
    ssidToBssids[ssid].add(bssid);

    // Only flag as rogue if multiple BSSIDs AND different manufacturers (OUIs)
    // This prevents false positives from mesh WiFi systems and enterprise networks
    if (ssidToBssids[ssid].size > 1 && isNewBssid) {
        // Check if all BSSIDs have the same OUI (manufacturer)
        const ouis = new Set(rogueApDetails[ssid].map(e => e.oui));

        // If all BSSIDs have the same OUI, it's likely a mesh system - not rogue
        if (ouis.size === 1) {
            // Same manufacturer - probably mesh system, not rogue
            return false;
        }

        // Different manufacturers detected - this is suspicious!
        rogueApCount++;
        document.getElementById('rogueApCount').textContent = rogueApCount;
        playAlert();

        // Mark ALL BSSIDs with this SSID as suspected rogues
        ssidToBssids[ssid].forEach(b => rogueBssids.add(b));

        // Get the BSSIDs to show in alert
        const bssidList = rogueApDetails[ssid].map(e => e.bssid).join(', ');
        showInfo(`Rogue AP: "${ssid}" has ${ouis.size} different vendors: ${bssidList}`);
        showNotification('Rogue AP Detected!', `"${ssid}" has different vendor BSSIDs`);

        // Update all network cards with this SSID to show rogue indicator
        ssidToBssids[ssid].forEach(rogueBssid => {
            const net = wifiNetworks[rogueBssid];
            if (net) addWifiNetworkCard(net, false);
        });

        return true;
    }
    return false;
}

// Show rogue AP details popup
function showRogueApDetails() {
    const rogueSSIDs = Object.keys(rogueApDetails).filter(ssid =>
        rogueApDetails[ssid].length > 1
    );

    if (rogueSSIDs.length === 0) {
        showInfo('No rogue APs detected. Rogue AP = same SSID on multiple BSSIDs.');
        return;
    }

    // Remove existing popup if any
    const existing = document.getElementById('rogueApPopup');
    if (existing) existing.remove();

    // Build details HTML
    let html = '<div style="max-height: 300px; overflow-y: auto;">';
    rogueSSIDs.forEach(ssid => {
        const aps = rogueApDetails[ssid];
        html += `<div style="margin-bottom: 12px;">
            <div style="color: var(--accent-red); font-weight: bold; margin-bottom: 4px;">
                "${ssid}" (${aps.length} BSSIDs)
            </div>
            <table style="width: 100%; font-size: 10px; border-collapse: collapse;">
                <tr style="color: var(--text-dim);">
                    <th style="text-align: left; padding: 2px 8px;">BSSID</th>
                    <th style="text-align: left; padding: 2px 8px;">CH</th>
                    <th style="text-align: left; padding: 2px 8px;">Signal</th>
                    <th style="text-align: left; padding: 2px 8px;">First Seen</th>
                </tr>`;
        aps.forEach((ap, idx) => {
            const bgColor = idx % 2 === 0 ? 'rgba(255,255,255,0.05)' : 'transparent';
            html += `<tr style="background: ${bgColor};">
                <td style="padding: 2px 8px; font-family: monospace;">${ap.bssid}</td>
                <td style="padding: 2px 8px;">${ap.channel}</td>
                <td style="padding: 2px 8px;">${ap.signal} dBm</td>
                <td style="padding: 2px 8px;">${ap.firstSeen}</td>
            </tr>`;
        });
        html += '</table></div>';
    });
    html += '</div>';
    html += '<div style="margin-top: 8px; font-size: 9px; color: var(--text-dim);">Multiple BSSIDs for same SSID may indicate rogue AP or legitimate multi-AP setup</div>';

    // Create popup
    const popup = document.createElement('div');
    popup.id = 'rogueApPopup';
    popup.style.cssText = `
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        background: var(--bg-primary);
        border: 1px solid var(--accent-red);
        border-radius: 8px;
        padding: 16px;
        z-index: 10000;
        min-width: 400px;
        max-width: 600px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    `;
    popup.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <span style="font-weight: bold; color: var(--accent-red);">Rogue AP Details</span>
            <button onclick="this.parentElement.parentElement.remove()"
                    style="background: none; border: none; color: var(--text-dim); cursor: pointer; font-size: 16px;">✕</button>
        </div>
        ${html}
    `;

    document.body.appendChild(popup);
}

// Show drone details popup
function showDroneDetails() {
    const drones = Object.values(detectedDrones);

    if (drones.length === 0) {
        showInfo('No drones detected. Drones are identified by SSID patterns and manufacturer OUI.');
        return;
    }

    // Remove existing popup if any
    const existing = document.getElementById('droneDetailsPopup');
    if (existing) existing.remove();

    // Build details HTML
    let html = '<div style="max-height: 300px; overflow-y: auto;">';
    html += `<table style="width: 100%; font-size: 10px; border-collapse: collapse;">
        <tr style="color: var(--text-dim);">
            <th style="text-align: left; padding: 4px 8px;">Brand</th>
            <th style="text-align: left; padding: 4px 8px;">SSID</th>
            <th style="text-align: left; padding: 4px 8px;">BSSID</th>
            <th style="text-align: left; padding: 4px 8px;">CH</th>
            <th style="text-align: left; padding: 4px 8px;">Signal</th>
            <th style="text-align: left; padding: 4px 8px;">Distance</th>
            <th style="text-align: left; padding: 4px 8px;">Detected</th>
        </tr>`;

    drones.forEach((drone, idx) => {
        const bgColor = idx % 2 === 0 ? 'rgba(255,165,0,0.1)' : 'transparent';
        const rssi = parseInt(drone.signal) || -70;
        const distance = estimateDroneDistance(rssi);
        const timeStr = new Date(drone.firstSeen).toLocaleTimeString();
        html += `<tr style="background: ${bgColor};">
            <td style="padding: 4px 8px; font-weight: bold; color: var(--accent-orange);">${drone.brand || 'Unknown'}</td>
            <td style="padding: 4px 8px;">${drone.ssid || '[Hidden]'}</td>
            <td style="padding: 4px 8px; font-family: monospace; font-size: 9px;">${drone.bssid}</td>
            <td style="padding: 4px 8px;">${drone.channel || '?'}</td>
            <td style="padding: 4px 8px;">${drone.signal || '?'} dBm</td>
            <td style="padding: 4px 8px;">~${distance}m</td>
            <td style="padding: 4px 8px;">${timeStr}</td>
        </tr>`;
    });
    html += '</table></div>';
    html += '<div style="margin-top: 8px; font-size: 9px; color: var(--text-dim);">Detection via: SSID pattern matching and manufacturer OUI lookup</div>';

    // Create popup
    const popup = document.createElement('div');
    popup.id = 'droneDetailsPopup';
    popup.style.cssText = `
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        background: var(--bg-primary);
        border: 1px solid var(--accent-orange);
        border-radius: 8px;
        padding: 16px;
        z-index: 10000;
        min-width: 500px;
        max-width: 700px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    `;
    popup.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <span style="font-weight: bold; color: var(--accent-orange);">Detected Drones (${drones.length})</span>
            <button onclick="this.parentElement.parentElement.remove()"
                    style="background: none; border: none; color: var(--text-dim); cursor: pointer; font-size: 16px;">✕</button>
        </div>
        ${html}
    `;

    document.body.appendChild(popup);
}

// Update 5GHz channel graph
function updateChannel5gGraph() {
    const bars = document.querySelectorAll('#channelGraph5g .channel-bar');
    const labels = document.querySelectorAll('#channelGraph5g .channel-label');

    // Count networks per 5GHz channel
    const channelCounts = {};
    channels5g.forEach(ch => channelCounts[ch] = 0);

    Object.values(wifiNetworks).forEach(net => {
        const ch = net.channel?.toString().trim();
        if (channels5g.includes(ch)) {
            channelCounts[ch]++;
        }
    });

    const maxCount = Math.max(1, ...Object.values(channelCounts));

    bars.forEach((bar, i) => {
        const ch = channels5g[i];
        const count = channelCounts[ch] || 0;
        const height = Math.max(2, (count / maxCount) * 50);
        bar.style.height = height + 'px';
        bar.className = 'channel-bar' + (count > 0 ? ' active' : '') + (count > 3 ? ' congested' : '') + (count > 5 ? ' very-congested' : '');
    });
}

// ============== NEW FEATURES ==============

// Network Topology Graph
function drawNetworkGraph() {
    const canvas = document.getElementById('networkGraph');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const width = canvas.offsetWidth;
    const height = canvas.offsetHeight;
    canvas.width = width;
    canvas.height = height;

    // Clear
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);

    const networks = Object.values(wifiNetworks);
    const clients = Object.values(wifiClients);

    if (networks.length === 0) {
        ctx.fillStyle = '#444';
        ctx.font = '12px sans-serif';
        ctx.fillText('Start scanning to see network topology', width / 2 - 100, height / 2);
        return;
    }

    // Calculate positions for APs (top row)
    const apPositions = {};
    const apSpacing = width / (networks.length + 1);
    networks.forEach((net, i) => {
        apPositions[net.bssid] = {
            x: apSpacing * (i + 1),
            y: 40,
            ssid: net.essid,
            isDrone: isDrone(net.essid, net.bssid).isDrone
        };
    });

    // Draw connections from clients to APs
    ctx.strokeStyle = '#1a1a1a';
    ctx.lineWidth = 1;
    clients.forEach(client => {
        if (client.ap && apPositions[client.ap]) {
            const ap = apPositions[client.ap];
            const clientY = 120 + (Math.random() * 60);
            const clientX = ap.x + (Math.random() - 0.5) * 80;

            ctx.beginPath();
            ctx.moveTo(ap.x, ap.y + 15);
            ctx.lineTo(clientX, clientY - 10);
            ctx.stroke();

            // Draw client node
            ctx.beginPath();
            ctx.arc(clientX, clientY, 6, 0, Math.PI * 2);
            ctx.fillStyle = '#00ff88';
            ctx.fill();
        }
    });

    // Draw AP nodes
    Object.entries(apPositions).forEach(([bssid, pos]) => {
        ctx.beginPath();
        ctx.arc(pos.x, pos.y, 12, 0, Math.PI * 2);
        ctx.fillStyle = pos.isDrone ? '#ff8800' : '#00d4ff';
        ctx.fill();

        // Draw label
        ctx.fillStyle = '#888';
        ctx.font = '9px sans-serif';
        ctx.textAlign = 'center';
        const label = (pos.ssid || 'Hidden').substring(0, 12);
        ctx.fillText(label, pos.x, pos.y + 25);
    });

    ctx.textAlign = 'left';
}

// Channel Recommendation
function updateChannelRecommendation() {
    const channelCounts24 = {};
    const channelCounts5 = {};

    // Initialize
    for (let i = 1; i <= 13; i++) channelCounts24[i] = 0;
    channels5g.forEach(ch => channelCounts5[ch] = 0);

    // Count networks per channel
    Object.values(wifiNetworks).forEach(net => {
        const ch = parseInt(net.channel);
        if (ch >= 1 && ch <= 13) {
            // 2.4 GHz channels overlap, so count neighbors too
            for (let i = Math.max(1, ch - 2); i <= Math.min(13, ch + 2); i++) {
                channelCounts24[i] = (channelCounts24[i] || 0) + (i === ch ? 1 : 0.5);
            }
        } else if (channels5g.includes(ch.toString())) {
            channelCounts5[ch.toString()]++;
        }
    });

    // Count total networks for context
    const totalNetworks = Object.keys(wifiNetworks).length;

    // Find best 2.4 GHz channel (1, 6, or 11 preferred - non-overlapping)
    const preferred24 = [1, 6, 11];
    let best24 = 1;
    let minCount24 = Infinity;
    let channelUsage24 = [];
    preferred24.forEach(ch => {
        channelUsage24.push({ channel: ch, count: channelCounts24[ch] || 0 });
        if ((channelCounts24[ch] || 0) < minCount24) {
            minCount24 = channelCounts24[ch] || 0;
            best24 = ch;
        }
    });

    // Find best 5 GHz channel
    let best5 = '36';
    let minCount5 = Infinity;
    let used5g = 0;
    channels5g.forEach(ch => {
        const count = channelCounts5[ch] || 0;
        if (count > 0) used5g++;
        if (count < minCount5) {
            minCount5 = count;
            best5 = ch;
        }
    });

    // Update UI with more context (with null checks for v2 layout)
    const rec24El = document.getElementById('rec24Channel');
    const rec24ReasonEl = document.getElementById('rec24Reason');
    const rec5El = document.getElementById('rec5Channel');
    const rec5ReasonEl = document.getElementById('rec5Reason');

    if (rec24El) rec24El.textContent = best24;
    if (totalNetworks === 0) {
        if (rec24ReasonEl) rec24ReasonEl.textContent = '(no networks detected)';
    } else {
        const usage = channelUsage24.map(c => `CH${c.channel}:${Math.round(c.count)}`).join(', ');
        if (rec24ReasonEl) rec24ReasonEl.textContent =
            minCount24 === 0 ? '(clear)' : `(${Math.round(minCount24)} interference) [${usage}]`;
    }

    if (rec5El) rec5El.textContent = best5;
    if (totalNetworks === 0) {
        if (rec5ReasonEl) rec5ReasonEl.textContent = '(no networks detected)';
    } else {
        if (rec5ReasonEl) rec5ReasonEl.textContent =
            minCount5 === 0 ? `(clear, ${channels5g.length - used5g} unused)` : `(${minCount5} networks)`;
    }
}

// Device Correlation (WiFi <-> Bluetooth)
let deviceCorrelations = [];
let correlationFetchPending = false;

function correlateDevices() {
    // Use server-side correlation API for better analysis
    if (correlationFetchPending) return;
    correlationFetchPending = true;

    fetch('/correlation?min_confidence=0.4')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                deviceCorrelations = data.correlations || [];
                updateCorrelationDisplay();
            }
        })
        .catch(err => {
            console.warn('Correlation fetch failed:', err);
            // Fallback to local OUI matching
            correlateDevicesLocal();
        })
        .finally(() => {
            correlationFetchPending = false;
        });
}

function correlateDevicesLocal() {
    // Fallback: simple OUI-based correlation
    deviceCorrelations = [];
    const wifiMacs = Object.keys(wifiNetworks).concat(Object.keys(wifiClients));
    const btDeviceMap = getBluetoothDevicesSnapshot();
    const btMacs = Object.keys(btDeviceMap);

    wifiMacs.forEach(wifiMac => {
        const wifiOui = wifiMac.substring(0, 8).toUpperCase();
        btMacs.forEach(btMac => {
            const btOui = btMac.substring(0, 8).toUpperCase();
            if (wifiOui === btOui) {
                const wifiDev = wifiNetworks[wifiMac] || wifiClients[wifiMac];
                const btDev = btDeviceMap[btMac];
                deviceCorrelations.push({
                    wifi_mac: wifiMac,
                    bt_mac: btMac,
                    wifi_name: wifiDev?.essid || wifiDev?.mac || wifiMac,
                    bt_name: btDev?.name || btMac,
                    confidence: 0.5,
                    reason: 'same OUI'
                });
            }
        });
    });
    updateCorrelationDisplay();
}

function getBluetoothDevicesSnapshot() {
    const snapshot = {};
    if (typeof BluetoothMode === 'undefined' || typeof BluetoothMode.getDevices !== 'function') {
        return snapshot;
    }

    const devices = BluetoothMode.getDevices();
    devices.forEach(device => {
        const address = String(device.address || device.mac || '').toUpperCase();
        // Correlation fallback is OUI-based, so only include MAC-form addresses.
        if (!/^[0-9A-F]{2}(:[0-9A-F]{2}){5}$/.test(address)) return;
        snapshot[address] = device;
    });

    return snapshot;
}

function updateCorrelationDisplay() {
    const list = document.getElementById('correlationList');
    if (!list) return;

    if (deviceCorrelations.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim);">No correlated devices found yet</div>';
        return;
    }

    list.innerHTML = deviceCorrelations.slice(0, 10).map(c => {
        const confidence = Math.round((c.confidence || 0.5) * 100);
        const confidenceColor = confidence >= 70 ? 'var(--accent-green)' :
            confidence >= 50 ? 'var(--accent-orange)' : 'var(--text-dim)';
        return `
            <div style="padding: 4px 0; border-bottom: 1px solid var(--border-color);">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: var(--accent-cyan);">${c.wifi_name || c.wifi_mac}</span>
                    <span class="correlation-badge" style="background: ${confidenceColor};">${confidence}%</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: #6495ED;">${c.bt_name || c.bt_mac}</span>
                    <span style="font-size: 9px; color: var(--text-dim);">${c.reason || ''}</span>
                </div>
            </div>
        `;
    }).join('');
}

// Hidden SSID Revealer
let revealedSsids = {};  // {bssid: ssid}

function revealHiddenSsid(bssid, ssid) {
    if (ssid && ssid !== '' && ssid !== 'Hidden' && ssid !== '[Hidden]') {
        if (!revealedSsids[bssid]) {
            revealedSsids[bssid] = ssid;
            updateHiddenSsidDisplay();
            showNotification('Hidden SSID Revealed', `"${ssid}" on ${bssid}`);
        }
    }
}

function updateHiddenSsidDisplay() {
    const list = document.getElementById('hiddenSsidList');
    if (!list) return;

    const entries = Object.entries(revealedSsids);
    const hiddenCount = Object.keys(hiddenNetworks).length;

    if (entries.length === 0) {
        if (hiddenCount > 0) {
            list.innerHTML = `<div style="color: var(--text-dim);">Monitoring ${hiddenCount} hidden network${hiddenCount > 1 ? 's' : ''}...</div>`;
        } else {
            list.innerHTML = '<div style="color: var(--text-dim);">No hidden networks detected</div>';
        }
        return;
    }

    let html = entries.map(([bssid, ssid]) => `
        <div style="padding: 4px 0; border-bottom: 1px solid var(--border-color);">
            <span style="color: var(--accent-green);">✓ "${escapeHtml(ssid)}"</span>
            <span style="color: var(--text-dim); font-size: 9px;"> (${bssid})</span>
        </div>
    `).join('');

    if (hiddenCount > 0) {
        html += `<div style="color: var(--text-dim); margin-top: 4px; font-size: 10px;">+ ${hiddenCount} hidden still monitoring</div>`;
    }

    list.innerHTML = html;
}

// NOTE: Browser Notifications code moved to static/js/core/audio.js

// Sync legacy WiFi data to v2 channel chart
function syncLegacyToChannelChart() {
    if (typeof ChannelChart === 'undefined') return;

    const networksList = Object.values(wifiNetworks);
    if (networksList.length === 0) return;

    // Calculate channel stats from legacy networks
    const stats = {};

    // Initialize 2.4 GHz channels
    for (let ch = 1; ch <= 11; ch++) {
        stats[ch] = { channel: ch, band: '2.4GHz', ap_count: 0, utilization_score: 0 };
    }
    // Initialize 5 GHz channels
    [36, 40, 44, 48, 149, 153, 157, 161, 165].forEach(ch => {
        stats[ch] = { channel: ch, band: '5GHz', ap_count: 0, utilization_score: 0 };
    });

    // Count APs per channel
    networksList.forEach(net => {
        const ch = parseInt(net.channel);
        if (stats[ch]) {
            stats[ch].ap_count++;
        }
    });

    // Calculate utilization (0-1)
    const maxAPs = Math.max(1, ...Object.values(stats).map(s => s.ap_count));
    Object.values(stats).forEach(s => {
        s.utilization_score = s.ap_count / maxAPs;
    });

    // Get active band from tab
    const activeTab = document.querySelector('.channel-band-tab.active');
    const band = activeTab ? activeTab.dataset.band : '2.4';
    const bandFilter = band === '2.4' ? '2.4GHz' : '5GHz';

    const filteredStats = Object.values(stats).filter(s => s.band === bandFilter);
    ChannelChart.update(filteredStats, []);
}

// Update visualizations periodically
setInterval(() => {
    if (currentMode === 'wifi') {
        updateChannelRecommendation();
        correlateDevices();
        updateHiddenSsidDisplay();
        updateProbeAnalysis();
        syncLegacyToChannelChart();
    }
}, 2000);

// Refresh WiFi interfaces
function refreshWifiInterfaces() {
    const select = document.getElementById('wifiInterfaceSelect');
    select.innerHTML = '<option value="">Loading interfaces...</option>';

    // Check if we're in agent mode
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';

    if (isAgentMode) {
        // Fetch from agent via controller
        fetch(`/controller/agents/${currentAgent}?refresh=true`)
            .then(r => {
                if (!r.ok) throw new Error('Failed to fetch agent interfaces');
                return r.json();
            })
            .then(data => {
                const interfaces = data.agent?.interfaces?.wifi_interfaces || [];
                if (interfaces.length === 0) {
                    select.innerHTML = '<option value="">No WiFi interfaces on agent</option>';
                    showNotification('WiFi', 'No WiFi interfaces found on remote agent.');
                    monitorInterface = null;
                    updateMonitorStatus(false);
                } else {
                    select.innerHTML = interfaces.map(i => {
                        let label = i.name || i;
                        if (i.display_name) label = i.display_name;
                        else if (i.type) label += ` (${i.type})`;
                        if (i.monitor_capable) label += ' [Monitor OK]';
                        return `<option value="${i.name || i}" data-type="${i.type || 'managed'}">${label}</option>`;
                    }).join('');
                    showNotification('WiFi', `Found ${interfaces.length} interface(s) on agent`);

                    // Check if any interface is already in monitor mode
                    const monitorIface = interfaces.find(i => i.type === 'monitor');
                    if (monitorIface) {
                        monitorInterface = monitorIface.name;
                        updateMonitorStatus(true);
                        select.value = monitorIface.name;
                    } else {
                        monitorInterface = null;
                        updateMonitorStatus(false);
                    }
                }
            })
            .catch(err => {
                console.error('Failed to refresh agent interfaces:', err);
                select.innerHTML = '<option value="">Error loading agent interfaces</option>';
                showNotification('WiFi', 'Failed to load agent interfaces');
            });
        return;
    }

    fetch('/wifi/interfaces')
        .then(r => {
            if (!r.ok) throw new Error('Failed to fetch interfaces');
            return r.json();
        })
        .then(data => {
            if (!data.interfaces || data.interfaces.length === 0) {
                select.innerHTML = '<option value="">No WiFi interfaces found</option>';
                showNotification('WiFi', 'No WiFi interfaces detected. Make sure you have a WiFi adapter connected.');
            } else {
                select.innerHTML = data.interfaces.map(i => {
                    // Build descriptive label with available info
                    let label = i.name;
                    let details = [];
                    if (i.chipset) details.push(i.chipset);
                    else if (i.driver) details.push(i.driver);
                    if (i.mac) details.push(i.mac.substring(0, 8) + '...');
                    if (details.length > 0) label += ' - ' + details.join(' | ');
                    label += ` (${i.type})`;
                    if (i.monitor_capable) label += ' [Monitor OK]';
                    return `<option value="${i.name}">${label}</option>`;
                }).join('');
                showNotification('WiFi', `Found ${data.interfaces.length} interface(s)`);
            }

            // Update tool status
            const statusDiv = document.getElementById('wifiToolStatus');
            if (statusDiv) {
                statusDiv.innerHTML = `
                    <span>airmon-ng:</span><span class="tool-status ${data.tools?.airmon ? 'ok' : 'missing'}">${data.tools?.airmon ? 'OK' : 'Missing'}</span>
                    <span>airodump-ng:</span><span class="tool-status ${data.tools?.airodump ? 'ok' : 'missing'}">${data.tools?.airodump ? 'OK' : 'Missing'}</span>
                `;
            }

            // Update monitor status
            if (data.monitor_interface) {
                monitorInterface = data.monitor_interface;
                updateMonitorStatus(true);
            }
        })
        .catch(err => {
            console.error('Error fetching WiFi interfaces:', err);
            select.innerHTML = '<option value="">Error loading interfaces</option>';
            showNotification('WiFi Error', 'Could not detect WiFi interfaces: ' + err.message);
        });
}

// Enable monitor mode
function enableMonitorMode() {
    const iface = document.getElementById('wifiInterfaceSelect').value;
    if (!iface) {
        alert('Please select an interface');
        return;
    }

    const killProcesses = document.getElementById('killProcesses').checked;
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';

    // Show loading state
    const btn = document.getElementById('monitorStartBtn');
    const originalText = btn.textContent;
    btn.textContent = 'Enabling...';
    btn.disabled = true;

    // Use agent endpoint if in agent mode
    const endpoint = isAgentMode
        ? `/controller/agents/${currentAgent}/wifi/monitor`
        : '/wifi/monitor';

    fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interface: iface, action: 'start', kill_processes: killProcesses })
    }).then(r => r.json())
        .then(data => {
            btn.textContent = originalText;
            btn.disabled = false;

            if (data.status === 'success') {
                monitorInterface = data.monitor_interface;
                updateMonitorStatus(true);
                const location = isAgentMode ? ' on remote agent' : '';
                showInfo('Monitor mode enabled on ' + monitorInterface + location + ' - Ready to scan!');

                // Refresh interface list and auto-select the monitor interface
                refreshWifiInterfaces();
            } else {
                alert('Error: ' + (data.message || 'Unknown error'));
            }
        })
        .catch(err => {
            btn.textContent = originalText;
            btn.disabled = false;
            alert('Error: ' + err.message);
        });
}

// Disable monitor mode
function disableMonitorMode() {
    const iface = monitorInterface || document.getElementById('wifiInterfaceSelect').value;
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';

    const endpoint = isAgentMode
        ? `/controller/agents/${currentAgent}/wifi/monitor`
        : '/wifi/monitor';

    fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interface: iface, action: 'stop' })
    }).then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                monitorInterface = null;
                updateMonitorStatus(false);
                showInfo('Monitor mode disabled');
            } else {
                alert('Error: ' + (data.message || 'Unknown error'));
            }
        });
}

function updateMonitorStatus(enabled) {
    document.getElementById('monitorStartBtn').style.display = enabled ? 'none' : 'block';
    document.getElementById('monitorStopBtn').style.display = enabled ? 'block' : 'none';
    document.getElementById('monitorStatus').innerHTML = enabled
        ? 'Monitor mode: <span style="color: var(--accent-green);">Active (' + monitorInterface + ')</span>'
        : 'Monitor mode: <span style="color: var(--accent-red);">Inactive</span>';
}

function getWifiChannelPresetList(preset) {
    switch (preset) {
        case '2.4-common':
            return '1,6,11';
        case '2.4-all':
            return '1,2,3,4,5,6,7,8,9,10,11,12,13';
        case '5-low':
            return '36,40,44,48';
        case '5-mid':
            return '52,56,60,64';
        case '5-high':
            return '149,153,157,161,165';
        default:
            return '';
    }
}

function buildWifiChannelConfig() {
    const preset = document.getElementById('wifiChannelPreset')?.value || '';
    const listInput = document.getElementById('wifiChannelList')?.value || '';
    const singleInput = document.getElementById('wifiChannel')?.value || '';

    const listValue = listInput.trim();
    const presetValue = getWifiChannelPresetList(preset);
    const channels = listValue || presetValue || '';
    const channel = channels ? null : (singleInput.trim() ? parseInt(singleInput.trim()) : null);

    return {
        channels: channels || null,
        channel: Number.isFinite(channel) ? channel : null,
    };
}

// Start WiFi scan - auto-enables monitor mode if needed
async function startWifiScan() {
    console.log('startWifiScan called');
    const band = document.getElementById('wifiBand').value;
    const channelConfig = buildWifiChannelConfig();

    // Auto-enable monitor mode if not already enabled
    if (!monitorInterface) {
        const iface = document.getElementById('wifiInterfaceSelect').value;
        console.log('Selected interface:', iface);

        if (!iface) {
            showNotification('WiFi Error', 'No WiFi interface selected. Please select an adapter from the dropdown.');
            alert('No WiFi interface selected. Please select an adapter from the dropdown above.');
            return;
        }

        // Show status
        document.getElementById('statusText').textContent = 'Enabling monitor mode...';
        document.getElementById('statusDot').classList.add('running');
        showNotification('WiFi', 'Enabling monitor mode on ' + iface + '...');

        try {
            const killProcesses = document.getElementById('killProcesses').checked;
            console.log('Enabling monitor mode, kill processes:', killProcesses);

            const monitorResp = await fetch('/wifi/monitor', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ interface: iface, action: 'start', kill_processes: killProcesses })
            });
            const monitorData = await monitorResp.json();
            console.log('Monitor response:', monitorData);

            if (monitorData.status === 'success') {
                monitorInterface = monitorData.monitor_interface;
                updateMonitorStatus(true);
                showNotification('Monitor Mode', 'Enabled on ' + monitorInterface);
            } else {
                document.getElementById('statusText').textContent = 'Idle';
                document.getElementById('statusDot').classList.remove('running');
                showNotification('Monitor Error', monitorData.message || 'Failed to enable monitor mode');
                alert('Monitor mode failed: ' + (monitorData.message || 'Unknown error'));
                return;
            }
        } catch (err) {
            console.error('Monitor mode error:', err);
            document.getElementById('statusText').textContent = 'Idle';
            document.getElementById('statusDot').classList.remove('running');
            showNotification('Monitor Error', err.message);
            alert('Monitor mode error: ' + err.message);
            return;
        }
    }

    // Now start the scan
    document.getElementById('statusText').textContent = 'Starting scan...';
    console.log('Starting scan on', monitorInterface);

    try {
        const scanResp = await fetch('/wifi/scan/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                interface: monitorInterface,
                band: band,
                channel: channelConfig.channel,
                channels: channelConfig.channels,
            })
        });
        const scanData = await scanResp.json();
        console.log('Scan response:', scanData);

        if (scanData.status === 'started') {
            setWifiRunning(true);
            startWifiStream();
            showNotification('WiFi Scanner', 'Scanning started on ' + monitorInterface);
        } else {
            document.getElementById('statusText').textContent = 'Idle';
            document.getElementById('statusDot').classList.remove('running');
            showNotification('Scan Error', scanData.message || 'Failed to start scan');
            alert('Scan failed: ' + (scanData.message || 'Unknown error'));
        }
    } catch (err) {
        console.error('Scan error:', err);
        document.getElementById('statusText').textContent = 'Idle';
        document.getElementById('statusDot').classList.remove('running');
        showNotification('Scan Error', err.message);
        alert('Scan error: ' + err.message);
    }
}

// Stop WiFi scan
function stopWifiScan() {
    setWifiRunning(false);
    if (wifiEventSource) {
        wifiEventSource.close();
        wifiEventSource = null;
    }

    if (typeof WiFiMode !== 'undefined' && typeof WiFiMode.stopScan === 'function') {
        return Promise.resolve(WiFiMode.stopScan()).catch((err) => {
            console.warn('[WiFi] stop via WiFiMode failed:', err);
        });
    }

    return postStopRequest('/wifi/scan/stop', LOCAL_STOP_TIMEOUT_MS);
}

function setWifiRunning(running) {
    isWifiRunning = running;
    document.getElementById('statusDot').classList.toggle('running', running);
    document.getElementById('statusText').textContent = running ? 'Scanning...' : 'Idle';
    document.getElementById('startWifiBtn').style.display = running ? 'none' : 'block';
    document.getElementById('stopWifiBtn').style.display = running ? 'block' : 'none';
}

// Batching state for WiFi updates
let pendingWifiUpdate = false;
let pendingWifiNetworks = [];
let pendingWifiClients = [];

function scheduleWifiUIUpdate() {
    if (pendingWifiUpdate) return;
    pendingWifiUpdate = true;
    requestAnimationFrame(() => {
        // Process networks
        pendingWifiNetworks.forEach(data => handleWifiNetworkImmediate(data));
        pendingWifiNetworks = [];

        // Process clients (limit to last 5 per frame)
        const clientsToProcess = pendingWifiClients.slice(-5);
        pendingWifiClients = [];
        clientsToProcess.forEach(data => handleWifiClientImmediate(data));

        // Update graphs once per frame instead of per-network
        updateChannelGraph();
        updateChannel5gGraph();

        // Update selected device panel
        updateWifiSelectedDevice();

        // Update probe analysis (throttled)
        if (clientsToProcess.length > 0) {
            scheduleProbeAnalysisUpdate();
        }

        pendingWifiUpdate = false;
    });
}

// Start WiFi event stream
function startWifiStream() {
    if (wifiEventSource) {
        wifiEventSource.close();
    }

    wifiEventSource = new EventSource('/wifi/stream');

    wifiEventSource.onmessage = function (e) {
        const data = JSON.parse(e.data);

        if (data.type === 'network') {
            pendingWifiNetworks.push(data);
            scheduleWifiUIUpdate();
        } else if (data.type === 'client') {
            pendingWifiClients.push(data);
            scheduleWifiUIUpdate();
        } else if (data.type === 'info' || data.type === 'raw') {
            showInfo(data.text);
        } else if (data.type === 'error') {
            showError(data.text);
        } else if (data.type === 'status') {
            if (data.text === 'stopped') {
                setWifiRunning(false);
            }
        }
    };

    wifiEventSource.onerror = function () {
        console.error('WiFi stream error');
    };
}

// Track networks that were originally hidden
let hiddenNetworks = {};  // {bssid: true} for networks first seen with hidden ESSID

// Handle discovered WiFi network (called from batched update)
function handleWifiNetworkImmediate(net) {
    const isNew = !wifiNetworks[net.bssid];
    const previousNet = wifiNetworks[net.bssid];
    wifiNetworks[net.bssid] = net;

    // Track if this network was originally hidden
    if (isNew) {
        const isHidden = !net.essid || net.essid === '' || net.essid === 'Hidden' || net.essid === '[Hidden]';
        if (isHidden) {
            hiddenNetworks[net.bssid] = true;
        }
    }

    // Check if a previously hidden network now has a revealed SSID
    if (hiddenNetworks[net.bssid] && net.essid && net.essid !== '' && net.essid !== 'Hidden' && net.essid !== '[Hidden]') {
        revealHiddenSsid(net.bssid, net.essid);
        delete hiddenNetworks[net.bssid];  // No longer hidden
    }

    if (isNew) {
        apCount++;
        document.getElementById('apCount').textContent = apCount;
        playAlert();
        pulseSignal();

        // Check for rogue AP (same SSID, different BSSID)
        checkRogueAP(net.essid, net.bssid, net.channel, net.power);

        // Check proximity watch list
        checkWatchList(net.bssid, 'AP');

        // Check for drone
        const droneCheck = isDrone(net.essid, net.bssid);
        if (droneCheck.isDrone) {
            handleDroneDetection(net, droneCheck);
            showNotification('Drone Detected', `${droneCheck.brand}: ${net.essid}`);
        }
    }

    // Update recon display
    const droneInfo = isDrone(net.essid, net.bssid);
    trackDevice({
        protocol: droneInfo.isDrone ? 'DRONE' : 'WiFi-AP',
        address: net.bssid,
        message: net.essid || '[Hidden SSID]',
        model: net.essid,
        channel: net.channel,
        privacy: net.privacy,
        isDrone: droneInfo.isDrone,
        droneBrand: droneInfo.brand
    });

    // Add to output
    addWifiNetworkCard(net, isNew);
    // Note: Channel graphs are updated in the batched scheduleWifiUIUpdate
}

// Handle discovered WiFi client (called from batched update)
function handleWifiClientImmediate(client) {
    const isNew = !wifiClients[client.mac];
    wifiClients[client.mac] = client;

    if (isNew) {
        clientCount++;
        document.getElementById('clientCount').textContent = clientCount;

        // Check proximity watch list
        checkWatchList(client.mac, 'Client');
    }

    // If client is connected to a hidden network and has probes, try to reveal the SSID
    if (client.bssid && hiddenNetworks[client.bssid] && client.probes) {
        const probes = client.probes.split(',').map(p => p.trim()).filter(p => p);
        if (probes.length > 0) {
            // Use the first probe as the likely SSID for this hidden network
            revealHiddenSsid(client.bssid, probes[0]);
            delete hiddenNetworks[client.bssid];
        }
    }

    // Track in device intelligence with vendor info
    const vendorInfo = client.vendor && client.vendor !== 'Unknown' ? ` [${client.vendor}]` : '';
    trackDevice({
        protocol: 'WiFi-Client',
        address: client.mac,
        message: (client.probes || '[No probes]') + vendorInfo,
        bssid: client.bssid,
        vendor: client.vendor
    });

    // Update probe analysis when we get client data with probes
    if (client.probes && client.probes.trim()) {
        scheduleProbeAnalysisUpdate();
    }

    // Add client card to device list
    addWifiClientCard(client, isNew);
}

// Throttled probe analysis (called less frequently)
let lastProbeAnalysisUpdate = 0;
function scheduleProbeAnalysisUpdate() {
    const now = Date.now();
    if (now - lastProbeAnalysisUpdate > 2000) {
        lastProbeAnalysisUpdate = now;
        updateProbeAnalysis();
    }
}

// Update client probe analysis panel
function updateProbeAnalysis() {
    const list = document.getElementById('probeAnalysisList');
    if (!list) return;

    const clientsWithProbes = Object.values(wifiClients).filter(c => c.probes && c.probes.trim());
    const allProbes = new Set();
    let privacyLeaks = 0;

    // Count unique probes and privacy leaks
    clientsWithProbes.forEach(client => {
        const probes = client.probes.split(',').map(p => p.trim()).filter(p => p);
        probes.forEach(p => allProbes.add(p));

        // Check for sensitive network names (home networks, corporate, etc.)
        probes.forEach(probe => {
            const lowerProbe = probe.toLowerCase();
            if (lowerProbe.includes('home') || lowerProbe.includes('office') ||
                lowerProbe.includes('corp') || lowerProbe.includes('work') ||
                lowerProbe.includes('private') || lowerProbe.includes('hotel') ||
                lowerProbe.includes('airport') || lowerProbe.match(/^[a-z]+-[a-z]+$/i)) {
                privacyLeaks++;
            }
        });
    });

    // Update counters (with null checks for v2 layout)
    const probeClientEl = document.getElementById('probeClientCount');
    const probeSSIDEl = document.getElementById('probeSSIDCount');
    const probePrivacyEl = document.getElementById('probePrivacyCount');
    if (probeClientEl) probeClientEl.textContent = clientsWithProbes.length;
    if (probeSSIDEl) probeSSIDEl.textContent = allProbes.size;
    if (probePrivacyEl) probePrivacyEl.textContent = privacyLeaks;

    if (clientsWithProbes.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim);">Waiting for client probe requests...</div>';
        return;
    }

    // Sort by number of probes (most revealing first)
    clientsWithProbes.sort((a, b) => {
        const aCount = (a.probes || '').split(',').length;
        const bCount = (b.probes || '').split(',').length;
        return bCount - aCount;
    });

    let html = '<div style="display: flex; flex-direction: column; gap: 8px;">';

    clientsWithProbes.forEach(client => {
        const probes = client.probes.split(',').map(p => p.trim()).filter(p => p);
        const vendorBadge = client.vendor && client.vendor !== 'Unknown'
            ? `<span style="background: var(--bg-tertiary); padding: 1px 4px; border-radius: 2px; font-size: 9px; margin-left: 5px;">${escapeHtml(client.vendor)}</span>`
            : '';

        // Check for privacy-revealing probes
        const probeHtml = probes.map(probe => {
            const lowerProbe = probe.toLowerCase();
            const isSensitive = lowerProbe.includes('home') || lowerProbe.includes('office') ||
                lowerProbe.includes('corp') || lowerProbe.includes('work') ||
                lowerProbe.includes('private') || lowerProbe.includes('hotel') ||
                lowerProbe.includes('airport') || lowerProbe.match(/^[a-z]+-[a-z]+$/i);

            const style = isSensitive
                ? 'background: var(--accent-orange); color: #000; padding: 1px 4px; border-radius: 2px; margin: 1px;'
                : 'background: var(--bg-tertiary); padding: 1px 4px; border-radius: 2px; margin: 1px;';

            return `<span style="${style}" title="${isSensitive ? 'Potentially sensitive - reveals user location history' : ''}">${escapeHtml(probe)}</span>`;
        }).join(' ');

        html += `
            <div style="border-left: 2px solid var(--accent-cyan); padding-left: 8px; cursor: pointer;" onclick="selectWifiDevice('${escapeAttr(client.mac)}', 'client')" title="Click for details">
                <div style="display: flex; align-items: center; gap: 5px; margin-bottom: 3px;">
                    <span style="color: var(--accent-cyan); font-family: monospace; font-size: 10px;">${escapeHtml(client.mac)}</span>
                    ${vendorBadge}
                    <span style="color: var(--text-dim); font-size: 9px;">(${probes.length} probe${probes.length !== 1 ? 's' : ''})</span>
                </div>
                <div style="display: flex; flex-wrap: wrap; gap: 2px; font-size: 10px;">
                    ${probeHtml}
                </div>
            </div>
        `;
    });

    html += '</div>';
    list.innerHTML = html;
}

// Select a WiFi network or client for detailed view
function selectWifiDevice(id, type) {
    selectedWifiDevice = id;
    selectedWifiType = type;
    updateWifiSelectedDevice();
}

// Update the selected WiFi device panel
function updateWifiSelectedDevice() {
    const panel = document.getElementById('wifiSelectedDevice');
    if (!panel) return;

    if (!selectedWifiDevice) {
        panel.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">Click a network or client to view details</div>';
        return;
    }

    if (selectedWifiType === 'network') {
        const net = wifiNetworks[selectedWifiDevice];
        if (!net) {
            panel.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">Network no longer visible</div>';
            return;
        }

        const power = parseInt(net.power) || -100;
        const signalPercent = Math.max(0, Math.min(100, (power + 100) * 2));
        const signalColor = power >= -50 ? 'var(--accent-green)' : power >= -70 ? 'var(--accent-orange)' : 'var(--accent-red)';
        const isRogue = rogueBssids.has(net.bssid);

        panel.innerHTML = `
            ${isRogue ? '<div class="rogue-indicator" style="margin: -10px -10px 10px -10px; padding: 8px;">SUSPECTED ROGUE ACCESS POINT</div>' : ''}
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                <div style="grid-column: span 2; text-align: center; padding-bottom: 10px; border-bottom: 1px solid var(--border-color);">
                    <div style="font-size: 18px; color: ${isRogue ? 'var(--accent-red)' : 'var(--accent-cyan)'}; font-weight: bold;">${escapeHtml(net.essid || '[Hidden]')}</div>
                    <div style="font-size: 10px; color: var(--text-muted);">${escapeHtml(net.bssid)}</div>
                </div>
                <div style="background: var(--surface-sunken); padding: 8px; border-radius: 4px;">
                    <div style="color: var(--text-dim); font-size: 9px;">SIGNAL</div>
                    <div style="color: ${signalColor}; font-size: 16px; font-weight: bold;">${power} dBm</div>
                    <div style="background: var(--bg-tertiary); height: 4px; border-radius: 2px; margin-top: 4px;">
                        <div style="background: ${signalColor}; height: 100%; width: ${signalPercent}%; border-radius: 2px;"></div>
                    </div>
                </div>
                <div style="background: var(--surface-sunken); padding: 8px; border-radius: 4px;">
                    <div style="color: var(--text-dim); font-size: 9px;">CHANNEL</div>
                    <div style="color: var(--accent-cyan); font-size: 16px; font-weight: bold;">${net.channel}</div>
                </div>
                <div style="background: var(--surface-sunken); padding: 8px; border-radius: 4px;">
                    <div style="color: var(--text-dim); font-size: 9px;">SECURITY</div>
                    <div style="color: ${(net.privacy || '').includes('WPA3') ? 'var(--accent-green)' : (net.privacy || '').includes('WPA') ? 'var(--accent-orange)' : 'var(--accent-red)'};">${escapeHtml(net.privacy || 'Unknown')}</div>
                </div>
                <div style="background: var(--surface-sunken); padding: 8px; border-radius: 4px;">
                    <div style="color: var(--text-dim); font-size: 9px;">BEACONS</div>
                    <div style="color: var(--text-secondary);">${net.beacons || 0}</div>
                </div>
                <div style="grid-column: span 2; display: flex; gap: 8px; margin-top: 8px;">
                    <button class="preset-btn" onclick="targetNetwork('${escapeAttr(net.bssid)}', '${escapeAttr(net.channel)}')" style="flex: 1;">Target</button>
                    <button class="preset-btn" onclick="captureHandshake('${escapeAttr(net.bssid)}', '${escapeAttr(net.channel)}')" style="flex: 1; border-color: var(--accent-orange); color: var(--accent-orange);">Handshake</button>
                </div>
            </div>
        `;
    } else if (selectedWifiType === 'client') {
        const client = wifiClients[selectedWifiDevice];
        if (!client) {
            panel.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">Client no longer visible</div>';
            return;
        }

        const power = parseInt(client.power) || -100;
        const signalPercent = Math.max(0, Math.min(100, (power + 100) * 2));
        const signalColor = power >= -50 ? 'var(--accent-green)' : power >= -70 ? 'var(--accent-orange)' : 'var(--accent-red)';
        const probes = (client.probes || '').split(',').map(p => p.trim()).filter(p => p);
        const associatedNet = client.bssid && wifiNetworks[client.bssid];

        panel.innerHTML = `
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                <div style="grid-column: span 2; text-align: center; padding-bottom: 10px; border-bottom: 1px solid var(--border-color);">
                    <div style="font-size: 14px; color: var(--accent-orange); font-weight: bold;">CLIENT DEVICE</div>
                    <div style="font-size: 12px; color: var(--text-secondary);">${escapeHtml(client.mac)}</div>
                    ${client.vendor ? `<div style="font-size: 10px; color: var(--text-muted);">${escapeHtml(client.vendor)}</div>` : ''}
                </div>
                <div style="background: var(--surface-sunken); padding: 8px; border-radius: 4px;">
                    <div style="color: var(--text-dim); font-size: 9px;">SIGNAL</div>
                    <div style="color: ${signalColor}; font-size: 16px; font-weight: bold;">${power} dBm</div>
                    <div style="background: var(--bg-tertiary); height: 4px; border-radius: 2px; margin-top: 4px;">
                        <div style="background: ${signalColor}; height: 100%; width: ${signalPercent}%; border-radius: 2px;"></div>
                    </div>
                </div>
                <div style="background: var(--surface-sunken); padding: 8px; border-radius: 4px;">
                    <div style="color: var(--text-dim); font-size: 9px;">PACKETS</div>
                    <div style="color: var(--text-secondary);">${client.packets || 0}</div>
                </div>
                ${associatedNet ? `
                <div style="grid-column: span 2; background: var(--surface-sunken); padding: 8px; border-radius: 4px;">
                    <div style="color: var(--text-dim); font-size: 9px;">CONNECTED TO</div>
                    <div style="color: var(--accent-cyan);">${escapeHtml(associatedNet.essid || associatedNet.bssid)}</div>
                </div>
                ` : ''}
                ${probes.length > 0 ? `
                <div style="grid-column: span 2; background: var(--surface-sunken); padding: 8px; border-radius: 4px;">
                    <div style="color: var(--text-dim); font-size: 9px;">PROBING FOR</div>
                    <div style="display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px;">
                        ${probes.slice(0, 5).map(p => `<span style="background: var(--accent-orange); color: #000; padding: 2px 6px; border-radius: 3px; font-size: 10px;">${escapeHtml(p)}</span>`).join('')}
                        ${probes.length > 5 ? `<span style="color: var(--text-muted);">+${probes.length - 5} more</span>` : ''}
                    </div>
                </div>
                ` : ''}
            </div>
        `;
    }
}

// Add WiFi network card to device list
function addWifiNetworkCard(net, isNew) {
    // Use the WiFi device list panel instead of the generic output
    const deviceList = document.getElementById('wifiDeviceListContent');
    if (!deviceList) return;

    // Remove placeholder if present
    const placeholder = deviceList.querySelector('div[style*="text-align: center"]');
    if (placeholder && placeholder.textContent.includes('Start scanning')) {
        placeholder.remove();
    }

    // Check if card already exists
    let card = document.getElementById('wifi_' + net.bssid.replace(/:/g, ''));

    if (!card) {
        card = document.createElement('div');
        card.id = 'wifi_' + net.bssid.replace(/:/g, '');
        card.className = 'sensor-card wifi-network-card';
        card.style.borderLeftColor = net.privacy.includes('WPA') ? 'var(--accent-orange)' :
            net.privacy.includes('WEP') ? 'var(--accent-red)' :
                'var(--accent-green)';
        card.style.cursor = 'pointer';
        card.onclick = () => selectWifiDevice(net.bssid, 'network');
        deviceList.insertBefore(card, deviceList.firstChild);

        // Update device count
        const countEl = document.getElementById('wifiDeviceListCount');
        if (countEl) countEl.textContent = Object.keys(wifiNetworks).length;
    }

    // Handle signal strength - airodump returns -1 when not measured
    let signalStrength = parseInt(net.power);
    if (isNaN(signalStrength) || signalStrength === -1) {
        signalStrength = null;  // No reading available
    }
    const signalBars = signalStrength !== null ? Math.max(0, Math.min(5, Math.floor((signalStrength + 100) / 15))) : 0;
    const signalDisplay = signalStrength !== null ? `${signalStrength} dBm` : 'N/A';

    const wpsEnabled = net.wps === '1' || net.wps === 'Yes' || (net.privacy || '').includes('WPS');
    const wpsHtml = wpsEnabled ? '<span class="wps-enabled">WPS</span>' : '';
    const isRogue = rogueBssids.has(net.bssid);
    const rogueHtml = isRogue ? '<div class="rogue-indicator">SUSPECTED ROGUE AP</div>' : '';

    // Update card border for rogue APs
    if (isRogue) {
        card.style.borderLeftColor = 'var(--accent-red)';
        card.style.borderLeftWidth = '4px';
        card.style.background = 'rgba(255, 0, 0, 0.1)';
    }

    card.innerHTML = `
        ${rogueHtml}
        <div class="header" style="display: flex; justify-content: space-between; margin-bottom: 8px;">
            <span class="device-name">${escapeHtml(net.essid || '[Hidden]')}${wpsHtml}</span>
            <span style="color: var(--text-dim); font-size: 10px;">CH ${net.channel}</span>
        </div>
        <div class="sensor-data">
            <div class="data-item">
                <div class="data-label">BSSID</div>
                <div class="data-value" style="font-size: 11px;">${escapeHtml(net.bssid)}</div>
            </div>
            <div class="data-item">
                <div class="data-label">Security</div>
                <div class="data-value" style="color: ${(net.privacy || '').includes('WPA') ? 'var(--accent-orange)' : net.privacy === 'OPN' ? 'var(--accent-green)' : 'var(--accent-red)'}">${escapeHtml(net.privacy || '')}</div>
            </div>
            <div class="data-item">
                <div class="data-label">Signal</div>
                <div class="data-value">${signalDisplay} ${'█'.repeat(signalBars)}${'░'.repeat(5 - signalBars)}</div>
            </div>
            <div class="data-item">
                <div class="data-label">Beacons</div>
                <div class="data-value">${net.beacons}</div>
            </div>
        </div>
        <div style="margin-top: 8px; display: flex; gap: 5px; flex-wrap: wrap;">
            <button class="preset-btn" onclick="targetNetwork('${escapeAttr(net.bssid)}', '${escapeAttr(net.channel)}')" style="font-size: 10px; padding: 4px 8px;">Target</button>
            <button class="preset-btn" onclick="captureHandshake('${escapeAttr(net.bssid)}', '${escapeAttr(net.channel)}')" style="font-size: 10px; padding: 4px 8px; border-color: var(--accent-orange); color: var(--accent-orange);">Handshake</button>
        </div>
    `;

    if (autoScroll) output.scrollTop = 0;

    // Feed to activity timeline if it's a new network
    if (isNew && typeof addTimelineEvent === 'function') {
        const normalized = typeof WiFiTimelineAdapter !== 'undefined'
            ? WiFiTimelineAdapter.normalizeNetwork({
                ssid: net.essid,
                bssid: net.bssid,
                channel: net.channel,
                rssi: signalStrength,
                security: net.privacy
            })
            : {
                id: net.bssid,
                label: net.essid || '[Hidden]',
                strength: signalBars || 3,
                duration: 1500,
                type: 'wifi'
            };
        addTimelineEvent('wifi', normalized);
    }
}

// Add WiFi client card to device list
function addWifiClientCard(client, isNew) {
    const deviceList = document.getElementById('wifiDeviceListContent');
    if (!deviceList) return;

    // Remove placeholder if present
    const placeholder = deviceList.querySelector('div[style*="text-align: center"]');
    if (placeholder && placeholder.textContent.includes('Start scanning')) {
        placeholder.remove();
    }

    // Check if card already exists
    let card = document.getElementById('client_' + client.mac.replace(/:/g, ''));

    if (!card) {
        card = document.createElement('div');
        card.id = 'client_' + client.mac.replace(/:/g, '');
        card.className = 'sensor-card wifi-client-card';
        card.style.borderLeftColor = 'var(--accent-purple)';
        card.style.cursor = 'pointer';
        card.onclick = () => selectWifiDevice(client.mac, 'client');
        deviceList.appendChild(card);  // Clients go after networks

        // Update device count
        const countEl = document.getElementById('wifiDeviceListCount');
        if (countEl) countEl.textContent = Object.keys(wifiNetworks).length + Object.keys(wifiClients).length;
    }

    // Handle signal strength
    let signalStrength = parseInt(client.power);
    if (isNaN(signalStrength) || signalStrength === -1) {
        signalStrength = null;
    }
    const signalBars = signalStrength !== null ? Math.max(0, Math.min(5, Math.floor((signalStrength + 100) / 15))) : 0;
    const signalDisplay = signalStrength !== null ? `${signalStrength} dBm` : 'N/A';

    // Get connected AP info
    const connectedAP = client.bssid && wifiNetworks[client.bssid];
    const apName = connectedAP ? (connectedAP.essid || '[Hidden]') : (client.bssid || 'Not Associated');

    // Format probes
    const probes = client.probes ? client.probes.split(',').map(p => p.trim()).filter(p => p) : [];
    const probesDisplay = probes.length > 0 ? probes.slice(0, 3).join(', ') + (probes.length > 3 ? ` +${probes.length - 3}` : '') : 'None';

    card.innerHTML = `
        <div class="header" style="display: flex; justify-content: space-between; margin-bottom: 8px;">
            <span class="device-name" style="color: var(--accent-purple);">${escapeHtml(client.vendor || 'Client')}</span>
            <span style="font-size: 10px; color: var(--text-dim);">CLIENT</span>
        </div>
        <div class="sensor-data">
            <div class="data-item">
                <div class="data-label">MAC</div>
                <div class="data-value" style="font-size: 11px;">${escapeHtml(client.mac)}</div>
            </div>
            <div class="data-item">
                <div class="data-label">Connected To</div>
                <div class="data-value" style="color: var(--accent-cyan);">${escapeHtml(apName)}</div>
            </div>
            <div class="data-item">
                <div class="data-label">Signal</div>
                <div class="data-value">${signalDisplay} ${'█'.repeat(signalBars)}${'░'.repeat(5 - signalBars)}</div>
            </div>
            <div class="data-item">
                <div class="data-label">Probes</div>
                <div class="data-value" style="font-size: 10px;">${escapeHtml(probesDisplay)}</div>
            </div>
        </div>
    `;
}

// Target a network for attack
function targetNetwork(bssid, channel) {
    document.getElementById('targetBssid').value = bssid;
    document.getElementById('wifiChannel').value = channel;
    showInfo('Targeted: ' + bssid + ' on channel ' + channel);
}

// Start handshake capture
async function captureHandshake(bssid, channel) {
    const confirmed = await AppFeedback.confirmAction({
        title: 'Capture Handshake',
        message: 'Start handshake capture for ' + bssid + '? This will stop the current scan.',
        confirmLabel: 'Start Capture',
        confirmClass: 'btn-danger'
    });
    if (!confirmed) {
        return;
    }

    const iface = monitorInterface || document.getElementById('wifiInterfaceSelect').value;
    if (!iface) {
        showError('No monitor interface available. Enable monitor mode first.');
        return;
    }

    // Stop any existing scan first
    if (isWifiRunning) {
        showInfo('Stopping current scan...');
        try {
            await fetch('/wifi/scan/stop', { method: 'POST' });
            if (wifiEventSource) {
                wifiEventSource.close();
                wifiEventSource = null;
            }
            setWifiRunning(false);
            // Brief delay to ensure process stops
            await new Promise(resolve => setTimeout(resolve, 500));
        } catch (e) {
            console.error('Error stopping scan:', e);
        }
    }

    try {
        const response = await fetch('/wifi/handshake/capture', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bssid: bssid, channel: channel, interface: iface })
        });
        const data = await response.json();

        if (data.status === 'started') {
            showInfo('Capturing handshakes for ' + bssid);
            setWifiRunning(true);

            // Update handshake indicator to show active capture
            const hsSpan = document.getElementById('handshakeCount');
            hsSpan.style.animation = 'pulse 1s infinite';
            hsSpan.title = 'Capturing: ' + bssid;

            // Show capture status panel
            const panel = document.getElementById('captureStatusPanel');
            panel.style.display = 'block';
            document.getElementById('captureTargetBssid').textContent = bssid;
            document.getElementById('captureTargetChannel').textContent = channel;
            document.getElementById('captureFilePath').textContent = data.capture_file;
            document.getElementById('captureStatus').textContent = 'Waiting for handshake...';
            document.getElementById('captureStatus').style.color = 'var(--accent-orange)';

            // Store active capture info and start polling
            activeCapture = {
                bssid: bssid,
                channel: channel,
                file: data.capture_file,
                startTime: Date.now(),
                pollInterval: setInterval(checkCaptureStatus, 5000)  // Check every 5 seconds
            };
        } else {
            showError('Handshake capture failed: ' + (data.message || 'Unknown error'));
        }
    } catch (err) {
        showError('Handshake capture error: ' + err.message);
        console.error('Handshake capture error:', err);
    }
}

// Check handshake capture status
function checkCaptureStatus() {
    if (!activeCapture) {
        showInfo('No active handshake capture');
        return;
    }

    fetch('/wifi/handshake/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file: activeCapture.file, bssid: activeCapture.bssid })
    }).then(r => r.json())
        .then(data => {
            const statusSpan = document.getElementById('captureStatus');
            const elapsed = Math.round((Date.now() - activeCapture.startTime) / 1000);
            const elapsedStr = elapsed < 60 ? elapsed + 's' : Math.floor(elapsed / 60) + 'm ' + (elapsed % 60) + 's';

            if (data.handshake_found) {
                // Handshake captured!
                statusSpan.textContent = '✓ VALID HANDSHAKE CAPTURED!';
                statusSpan.style.color = 'var(--accent-green)';
                handshakeCount++;
                document.getElementById('handshakeCount').textContent = handshakeCount;
                playAlert();
                showInfo('Handshake captured for ' + activeCapture.bssid + '. File: ' + data.file);
                showNotification('Handshake Captured!', `Target: ${activeCapture.bssid}`);

                // Stop polling
                if (activeCapture.pollInterval) {
                    clearInterval(activeCapture.pollInterval);
                }
                document.getElementById('handshakeCount').style.animation = '';

                // Show crack button in the capture panel
                const panel = document.getElementById('captureStatusPanel');
                const existingCrackBtn = panel.querySelector('.crack-btn');
                if (!existingCrackBtn) {
                    const crackDiv = document.createElement('div');
                    crackDiv.style.marginTop = '10px';
                    crackDiv.innerHTML = `
                      <button class="preset-btn crack-btn" onclick="crackHandshake('${data.file}', '${activeCapture.bssid}')" style="width: 100%; background: var(--accent-green); border-color: var(--accent-green); color: #000; font-weight: bold;">
                          Crack with Aircrack-ng
                      </button>
                  `;
                    panel.querySelector('.section') ? panel.querySelector('.section').appendChild(crackDiv) : panel.appendChild(crackDiv);
                }

                // Store the captured file for later use
                activeCapture.captured = true;
                activeCapture.capturedFile = data.file;
            } else if (data.file_exists) {
                const sizeKB = (data.file_size / 1024).toFixed(1);
                let extra = '';
                if (data.handshake_checked && data.handshake_valid === false) {
                    extra = data.handshake_reason ? ' • ' + data.handshake_reason : ' • No valid handshake yet';
                }
                statusSpan.textContent = 'Capturing... (' + sizeKB + ' KB, ' + elapsedStr + ')' + extra;
                statusSpan.style.color = 'var(--accent-orange)';
            } else if (data.status === 'stopped') {
                statusSpan.textContent = 'Capture stopped';
                statusSpan.style.color = 'var(--text-dim)';
                if (activeCapture.pollInterval) {
                    clearInterval(activeCapture.pollInterval);
                }
            } else {
                statusSpan.textContent = 'Waiting for data... (' + elapsedStr + ')';
                statusSpan.style.color = 'var(--accent-orange)';
            }
        })
        .catch(err => {
            console.error('Capture status check failed:', err);
        });
}

// Stop handshake capture
function stopHandshakeCapture() {
    if (activeCapture && activeCapture.pollInterval) {
        clearInterval(activeCapture.pollInterval);
    }

    // Stop the WiFi scan (which stops airodump-ng)
    stopWifiScan();

    document.getElementById('captureStatus').textContent = 'Stopped';
    document.getElementById('captureStatus').style.color = 'var(--text-dim)';
    document.getElementById('handshakeCount').style.animation = '';

    // Keep the panel visible so user can see the file path
    showInfo('Handshake capture stopped. Check ' + (activeCapture ? activeCapture.file : 'capture file'));

    activeCapture = null;
}

// Crack handshake with aircrack-ng
function crackHandshake(captureFile, bssid) {
    const wordlist = prompt('Enter path to wordlist file:\n\nCommon locations:\n- /usr/share/wordlists/rockyou.txt\n- /usr/share/john/password.lst', '/usr/share/wordlists/rockyou.txt');

    if (!wordlist) {
        showInfo('Cracking cancelled');
        return;
    }

    showInfo('Starting aircrack-ng... This may take a while.');

    fetch('/wifi/handshake/crack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            capture_file: captureFile,
            bssid: bssid,
            wordlist: wordlist
        })
    })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success' && data.password) {
                showInfo('PASSWORD FOUND: ' + data.password);
                showNotification('Password Cracked', data.password);
                alert('Password found!\n\n' + data.password + '\n\nThis has been logged.');
            } else if (data.status === 'not_found') {
                showInfo('Password not found in wordlist. Try a different wordlist.');
                alert('Password not found in wordlist.\n\nTry using a larger or different wordlist.');
            } else if (data.status === 'running') {
                showInfo('Aircrack-ng is running in background. Check terminal for progress.');
            } else {
                showError('Crack failed: ' + (data.message || 'Unknown error'));
            }
        })
        .catch(err => {
            showError('Crack error: ' + err.message);
            console.error('Crack error:', err);
        });
}

// Beacon Flood Detection
let beaconHistory = [];
let lastBeaconCheck = Date.now();

function checkBeaconFlood(networks) {
    const now = Date.now();
    const windowMs = 5000; // 5 second window

    // Add current networks to history
    beaconHistory.push({ time: now, count: Object.keys(networks).length });

    // Remove old entries
    beaconHistory = beaconHistory.filter(h => now - h.time < windowMs);

    // Calculate rate of new networks
    if (beaconHistory.length >= 2) {
        const oldest = beaconHistory[0];
        const newest = beaconHistory[beaconHistory.length - 1];
        const timeDiff = (newest.time - oldest.time) / 1000;
        const countDiff = newest.count - oldest.count;

        if (timeDiff > 0) {
            const rate = countDiff / timeDiff;

            // Alert if more than 10 new networks per second
            if (rate > 10) {
                document.getElementById('beaconFloodAlert').style.display = 'block';
                document.getElementById('beaconFloodRate').textContent = rate.toFixed(1);
                if (!muted) playAlertSound();
            } else if (rate < 2) {
                document.getElementById('beaconFloodAlert').style.display = 'none';
            }
        }
    }
}

// Send deauth
async function sendDeauth() {
    const bssid = document.getElementById('targetBssid').value;
    const client = document.getElementById('targetClient').value || 'FF:FF:FF:FF:FF:FF';
    const count = document.getElementById('deauthCount').value || '5';

    if (!bssid) {
        alert('Enter target BSSID');
        return;
    }

    const deauthConfirmed = await AppFeedback.confirmAction({
        title: 'Send Deauth Packets',
        message: 'Send ' + count + ' deauth packets to ' + bssid + '? Only use on networks you own or have authorization to test.',
        confirmLabel: 'Send Deauth',
        confirmClass: 'btn-danger'
    });
    if (!deauthConfirmed) {
        return;
    }

    fetch('/wifi/deauth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bssid: bssid, client: client, count: parseInt(count) })
    }).then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                showInfo(data.message);
            } else {
                alert('Error: ' + data.message);
            }
        });
}

// ============== WIFI VISUALIZATIONS ==============

let radarCtx = null;
let radarAngle = 0;
let radarAnimFrame = null;
let radarNetworks = [];  // {x, y, strength, ssid, bssid}
let targetBssidForSignal = null;

// Initialize radar canvas
function initRadar() {
    const canvas = document.getElementById('radarCanvas');
    if (!canvas) return;

    radarCtx = canvas.getContext('2d');
    canvas.width = 150;
    canvas.height = 150;

    // Start animation
    if (!radarAnimFrame) {
        animateRadar();
    }
}

// Animate radar sweep
function animateRadar() {
    if (!radarCtx) {
        radarAnimFrame = null;
        return;
    }

    const canvas = radarCtx.canvas;
    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const radius = Math.min(cx, cy) - 5;

    // Clear canvas
    radarCtx.fillStyle = 'rgba(0, 10, 10, 0.1)';
    radarCtx.fillRect(0, 0, canvas.width, canvas.height);

    // Draw grid circles
    radarCtx.strokeStyle = 'rgba(0, 212, 255, 0.2)';
    radarCtx.lineWidth = 1;
    for (let r = radius / 4; r <= radius; r += radius / 4) {
        radarCtx.beginPath();
        radarCtx.arc(cx, cy, r, 0, Math.PI * 2);
        radarCtx.stroke();
    }

    // Draw crosshairs
    radarCtx.beginPath();
    radarCtx.moveTo(cx, cy - radius);
    radarCtx.lineTo(cx, cy + radius);
    radarCtx.moveTo(cx - radius, cy);
    radarCtx.lineTo(cx + radius, cy);
    radarCtx.stroke();

    // Draw sweep line
    radarCtx.strokeStyle = 'rgba(0, 255, 136, 0.8)';
    radarCtx.lineWidth = 2;
    radarCtx.beginPath();
    radarCtx.moveTo(cx, cy);
    radarCtx.lineTo(
        cx + Math.cos(radarAngle) * radius,
        cy + Math.sin(radarAngle) * radius
    );
    radarCtx.stroke();

    // Draw sweep gradient
    const gradient = radarCtx.createConicalGradient ?
        null : // Not supported in all browsers
        radarCtx.createRadialGradient(cx, cy, 0, cx, cy, radius);

    radarCtx.fillStyle = 'rgba(0, 255, 136, 0.05)';
    radarCtx.beginPath();
    radarCtx.moveTo(cx, cy);
    radarCtx.arc(cx, cy, radius, radarAngle - 0.5, radarAngle);
    radarCtx.closePath();
    radarCtx.fill();

    // Draw network blips
    radarNetworks.forEach(net => {
        const age = Date.now() - net.timestamp;
        const alpha = Math.max(0.1, 1 - age / 10000);

        radarCtx.fillStyle = `rgba(0, 255, 136, ${alpha})`;
        radarCtx.beginPath();
        radarCtx.arc(net.x, net.y, 4 + (1 - alpha) * 3, 0, Math.PI * 2);
        radarCtx.fill();

        // Glow effect
        radarCtx.fillStyle = `rgba(0, 255, 136, ${alpha * 0.3})`;
        radarCtx.beginPath();
        radarCtx.arc(net.x, net.y, 8 + (1 - alpha) * 5, 0, Math.PI * 2);
        radarCtx.fill();
    });

    // Update angle
    radarAngle += 0.03;
    if (radarAngle > Math.PI * 2) radarAngle = 0;

    radarAnimFrame = requestAnimationFrame(animateRadar);
}

// Add network to radar
function addNetworkToRadar(net) {
    const canvas = document.getElementById('radarCanvas');
    if (!canvas) return;

    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const radius = Math.min(cx, cy) - 10;

    // Convert signal strength to distance (stronger = closer)
    const power = parseInt(net.power) || -80;
    const distance = Math.max(0.1, Math.min(1, (power + 100) / 60));
    const r = radius * (1 - distance);

    // Random angle based on BSSID hash
    let angle = 0;
    for (let i = 0; i < net.bssid.length; i++) {
        angle += net.bssid.charCodeAt(i);
    }
    angle = (angle % 360) * Math.PI / 180;

    const x = cx + Math.cos(angle) * r;
    const y = cy + Math.sin(angle) * r;

    // Update or add
    const existing = radarNetworks.find(n => n.bssid === net.bssid);
    if (existing) {
        existing.x = x;
        existing.y = y;
        existing.timestamp = Date.now();
    } else {
        radarNetworks.push({
            x, y,
            bssid: net.bssid,
            ssid: net.essid,
            timestamp: Date.now()
        });
    }

    // Limit to 50 networks
    if (radarNetworks.length > 50) {
        radarNetworks.shift();
    }
}

// Update channel graph
function updateChannelGraph() {
    const channels = {};
    for (let i = 1; i <= 13; i++) channels[i] = 0;

    // Count networks per channel
    Object.values(wifiNetworks).forEach(net => {
        const ch = parseInt(net.channel);
        if (ch >= 1 && ch <= 13) {
            channels[ch]++;
        }
    });

    // Find max for scaling
    const maxCount = Math.max(1, ...Object.values(channels));

    // Update bars
    const bars = document.querySelectorAll('#channelGraph .channel-bar');
    bars.forEach((bar, i) => {
        const ch = i + 1;
        const count = channels[ch] || 0;
        const height = Math.max(2, (count / maxCount) * 55);
        bar.style.height = height + 'px';

        bar.classList.remove('active', 'congested', 'very-congested');
        if (count > 0) bar.classList.add('active');
        if (count >= 3) bar.classList.add('congested');
        if (count >= 5) bar.classList.add('very-congested');
    });
}

// Update security donut chart
function updateSecurityDonut() {
    const canvas = document.getElementById('securityCanvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const radius = Math.min(cx, cy) - 2;
    const innerRadius = radius * 0.6;

    // Count security types
    let wpa3 = 0, wpa2 = 0, wep = 0, open = 0;
    Object.values(wifiNetworks).forEach(net => {
        const priv = (net.privacy || '').toUpperCase();
        if (priv.includes('WPA3')) wpa3++;
        else if (priv.includes('WPA')) wpa2++;
        else if (priv.includes('WEP')) wep++;
        else if (priv === 'OPN' || priv === '' || priv === 'OPEN') open++;
        else wpa2++; // Default to WPA2
    });

    const total = wpa3 + wpa2 + wep + open;

    // Update legend
    document.getElementById('wpa3Count').textContent = wpa3;
    document.getElementById('wpa2Count').textContent = wpa2;
    document.getElementById('wepCount').textContent = wep;
    document.getElementById('openCount').textContent = open;

    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (total === 0) {
        // Draw empty circle
        ctx.strokeStyle = '#1a1a1a';
        ctx.lineWidth = radius - innerRadius;
        ctx.beginPath();
        ctx.arc(cx, cy, (radius + innerRadius) / 2, 0, Math.PI * 2);
        ctx.stroke();
        return;
    }

    // Draw segments
    const colors = {
        wpa3: '#00ff88',
        wpa2: '#ff8800',
        wep: '#ff3366',
        open: '#00d4ff'
    };

    const data = [
        { value: wpa3, color: colors.wpa3 },
        { value: wpa2, color: colors.wpa2 },
        { value: wep, color: colors.wep },
        { value: open, color: colors.open }
    ];

    let startAngle = -Math.PI / 2;

    data.forEach(segment => {
        if (segment.value === 0) return;

        const sliceAngle = (segment.value / total) * Math.PI * 2;

        ctx.fillStyle = segment.color;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, radius, startAngle, startAngle + sliceAngle);
        ctx.closePath();
        ctx.fill();

        startAngle += sliceAngle;
    });

    // Draw inner circle (donut hole)
    ctx.fillStyle = '#000';
    ctx.beginPath();
    ctx.arc(cx, cy, innerRadius, 0, Math.PI * 2);
    ctx.fill();

    // Draw total in center
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 16px Roboto Condensed';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(total, cx, cy);
}

// Update signal strength meter for targeted network
function updateSignalMeter(net) {
    if (!net) return;

    targetBssidForSignal = net.bssid;

    const ssidEl = document.getElementById('targetSsid');
    const valueEl = document.getElementById('signalValue');
    const barsEl = document.querySelectorAll('.signal-bar-large');

    ssidEl.textContent = net.essid || net.bssid;

    const power = parseInt(net.power) || -100;
    valueEl.textContent = power + ' dBm';

    // Determine signal quality
    let quality = 'weak';
    let activeBars = 1;

    if (power >= -50) { quality = 'strong'; activeBars = 5; }
    else if (power >= -60) { quality = 'strong'; activeBars = 4; }
    else if (power >= -70) { quality = 'medium'; activeBars = 3; }
    else if (power >= -80) { quality = 'medium'; activeBars = 2; }
    else { quality = 'weak'; activeBars = 1; }

    valueEl.className = 'signal-value ' + quality;

    barsEl.forEach((bar, i) => {
        bar.className = 'signal-bar-large';
        if (i < activeBars) {
            bar.classList.add('active', quality);
        }
    });
}

// Hook into handleWifiNetworkImmediate to update visualizations
const originalHandleWifiNetworkImmediate = handleWifiNetworkImmediate;
handleWifiNetworkImmediate = function (net) {
    originalHandleWifiNetworkImmediate(net);

    // Update radar
    addNetworkToRadar(net);

    // Update security donut
    updateSecurityDonut();

    // Update signal meter if this is the targeted network
    if (targetBssidForSignal === net.bssid) {
        updateSignalMeter(net);
    }
    // Note: Channel graphs are updated in the batched scheduleWifiUIUpdate
};

// Update targetNetwork to also set signal meter
const originalTargetNetwork = targetNetwork;
targetNetwork = function (bssid, channel) {
    originalTargetNetwork(bssid, channel);

    const net = wifiNetworks[bssid];
    if (net) {
        updateSignalMeter(net);
    }
};
