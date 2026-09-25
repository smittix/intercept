/**
 * Device intelligence / reconnaissance for the main page: tracks devices
 * seen across signals, flags anomalies, and drives the recon panel. Moved
 * out of templates/index.html unchanged (only de-indented); a classic script
 * loaded after the inline block, sharing its top-level names as before.
 */
// ============== DEVICE INTELLIGENCE & RECONNAISSANCE ==============

// Device tracking database
const deviceDatabase = new Map(); // key: deviceId, value: device profile
// Default to false if not set
let reconEnabled = localStorage.getItem('reconEnabled') === 'true';
let newDeviceAlerts = 0;
let anomalyAlerts = 0;

// Device profile structure
function createDeviceProfile(deviceId, protocol, firstSeen) {
    return {
        id: deviceId,
        protocol: protocol,
        firstSeen: firstSeen,
        lastSeen: firstSeen,
        transmissionCount: 1,
        transmissions: [firstSeen], // timestamps of recent transmissions
        avgInterval: null, // average time between transmissions
        addresses: new Set(),
        models: new Set(),
        messages: [],
        isNew: true,
        anomalies: [],
        signalStrength: [],
        encrypted: null // null = unknown, true/false
    };
}

// Analyze transmission patterns for anomalies
function analyzeTransmissions(profile) {
    const anomalies = [];
    const now = Date.now();

    // Need at least 3 transmissions to analyze patterns
    if (profile.transmissions.length < 3) {
        return anomalies;
    }

    // Calculate intervals between transmissions
    const intervals = [];
    for (let i = 1; i < profile.transmissions.length; i++) {
        intervals.push(profile.transmissions[i] - profile.transmissions[i - 1]);
    }

    // Calculate average and standard deviation
    const avg = intervals.reduce((a, b) => a + b, 0) / intervals.length;
    profile.avgInterval = avg;

    const variance = intervals.reduce((a, b) => a + Math.pow(b - avg, 2), 0) / intervals.length;
    const stdDev = Math.sqrt(variance);

    // Check for burst transmission (sudden increase in frequency)
    const lastInterval = intervals[intervals.length - 1];
    if (avg > 0 && lastInterval < avg * 0.2) {
        anomalies.push({
            type: 'burst',
            severity: 'medium',
            message: 'Burst transmission detected - interval ' + Math.round(lastInterval / 1000) + 's vs avg ' + Math.round(avg / 1000) + 's'
        });
    }

    // Check for silence break (device was quiet, now transmitting again)
    if (avg > 0 && lastInterval > avg * 5) {
        anomalies.push({
            type: 'silence_break',
            severity: 'low',
            message: 'Device resumed after ' + Math.round(lastInterval / 60000) + ' min silence'
        });
    }

    return anomalies;
}

// Check for encryption indicators
function detectEncryption(message) {
    if (!message || message === '[No Message]' || message === '[Tone Only]') {
        return null; // Can't determine
    }

    // Check for non-printable characters (outside printable ASCII range)
    const hasNonPrintable = /[^\x20-\x7E]/.test(message);

    // Check for common encrypted patterns (hex strings)
    const hexPattern = /^[0-9A-Fa-f\s]+$/;

    if (hasNonPrintable) {
        return true; // Contains non-printable chars — likely encrypted or encoded
    }
    if (hexPattern.test(message.replace(/\s/g, ''))) {
        return true; // Pure hex data — likely encoded
    }

    // All printable ASCII (covers base64, structured data, punctuation, etc.)
    return false; // Likely plaintext
}

// Generate device fingerprint
function generateDeviceId(data) {
    if (data.protocol && data.protocol.includes('POCSAG')) {
        return 'PAGER_' + (data.address || 'UNK');
    } else if (data.protocol === 'FLEX') {
        return 'FLEX_' + (data.address || 'UNK');
    } else if (data.protocol === 'WiFi-AP') {
        return 'WIFI_AP_' + (data.address || 'UNK').replace(/:/g, '');
    } else if (data.protocol === 'WiFi-Client') {
        return 'WIFI_CLIENT_' + (data.address || 'UNK').replace(/:/g, '');
    } else if (data.protocol === 'Bluetooth' || data.protocol === 'BLE') {
        return 'BT_' + (data.address || 'UNK').replace(/:/g, '');
    } else if (data.protocol === 'Meter') {
        // Utility meter (rtlamr)
        return 'METER_' + (data.meterId || data.address || 'UNK');
    } else if (data.model) {
        // 433MHz sensor
        const id = data.id || data.channel || data.unit || '0';
        return 'SENSOR_' + data.model.replace(/\s+/g, '_') + '_' + id;
    }
    return 'UNKNOWN_' + Date.now();
}

// Track a device transmission
function trackDevice(data) {
    const now = Date.now();
    const deviceId = generateDeviceId(data);
    const protocol = data.protocol || data.model || 'Unknown';

    let profile = deviceDatabase.get(deviceId);
    let isNewDevice = false;

    if (!profile) {
        // New device discovered
        profile = createDeviceProfile(deviceId, protocol, now);
        isNewDevice = true;
        newDeviceAlerts++;
        document.getElementById('newDeviceCount').textContent = newDeviceAlerts;
    } else {
        // Update existing profile
        profile.lastSeen = now;
        profile.transmissionCount++;
        profile.transmissions.push(now);
        profile.isNew = false;

        // Keep only last 100 transmissions for analysis
        if (profile.transmissions.length > 100) {
            profile.transmissions = profile.transmissions.slice(-100);
        }
    }

    // Track addresses
    if (data.address) profile.addresses.add(data.address);
    if (data.model) profile.models.add(data.model);

    // Store recent messages (keep last 10)
    if (data.message) {
        profile.messages.unshift({
            text: data.message,
            time: now
        });
        if (profile.messages.length > 10) profile.messages.pop();

        // Detect encryption
        const encrypted = detectEncryption(data.message);
        if (encrypted !== null) profile.encrypted = encrypted;
    }

    // Analyze for anomalies
    const newAnomalies = analyzeTransmissions(profile);
    if (newAnomalies.length > 0) {
        profile.anomalies = profile.anomalies.concat(newAnomalies);
        anomalyAlerts += newAnomalies.length;
        document.getElementById('anomalyCount').textContent = anomalyAlerts;
    }

    deviceDatabase.set(deviceId, profile);
    document.getElementById('trackedCount').textContent = deviceDatabase.size;

    // Update recon display
    if (reconEnabled) {
        updateReconDisplay(deviceId, profile, isNewDevice, newAnomalies);
    }

    return { deviceId, profile, isNewDevice, anomalies: newAnomalies };
}

// Update reconnaissance display
function updateReconDisplay(deviceId, profile, isNewDevice, anomalies) {
    const content = document.getElementById('reconContent');

    // Remove placeholder if present
    const placeholder = content.querySelector('div[style*="text-align: center"]');
    if (placeholder) placeholder.remove();

    // Check if device row already exists
    let row = document.getElementById('device_' + deviceId.replace(/[^a-zA-Z0-9]/g, '_'));

    if (!row) {
        // Create new row
        row = document.createElement('div');
        row.id = 'device_' + deviceId.replace(/[^a-zA-Z0-9]/g, '_');
        row.className = 'device-row' + (isNewDevice ? ' new-device' : '');
        content.insertBefore(row, content.firstChild);
    }

    // Determine protocol badge class
    let badgeClass = 'proto-unknown';
    if (profile.protocol.includes('POCSAG')) badgeClass = 'proto-pocsag';
    else if (profile.protocol === 'FLEX') badgeClass = 'proto-flex';
    else if (profile.protocol.includes('SENSOR') || profile.models.size > 0) badgeClass = 'proto-433';

    // Calculate transmission rate bar width
    const maxRate = 100; // Max expected transmissions
    const rateWidth = Math.min(100, (profile.transmissionCount / maxRate) * 100);

    // Determine timeline status
    const timeSinceLast = Date.now() - profile.lastSeen;
    let timelineDot = 'recent';
    if (timeSinceLast > 300000) timelineDot = 'old'; // > 5 min
    else if (timeSinceLast > 60000) timelineDot = 'stale'; // > 1 min

    // Build encryption indicator
    let encStatus = 'Unknown';
    let encClass = '';
    if (profile.encrypted === true) { encStatus = 'Encrypted'; encClass = 'encrypted'; }
    else if (profile.encrypted === false) { encStatus = 'Plaintext'; encClass = 'plaintext'; }

    // Format time
    const lastSeenStr = getRelativeTime(new Date(profile.lastSeen).toTimeString().split(' ')[0]);
    const firstSeenStr = new Date(profile.firstSeen).toLocaleTimeString();

    // Update row content
    row.className = 'device-row' + (isNewDevice ? ' new-device' : '') + (anomalies.length > 0 ? ' anomaly' : '');
    row.innerHTML = `
        <div class="device-info">
            <div class="device-name-row">
                <span class="timeline-dot ${timelineDot}"></span>
                <span class="badge ${badgeClass}">${profile.protocol.substring(0, 10)}</span>
                ${deviceId.substring(0, 30)}
            </div>
            <div class="device-id">
                First: ${firstSeenStr} | Last: ${lastSeenStr} | TX: ${profile.transmissionCount}
                ${profile.avgInterval ? ' | Interval: ' + Math.round(profile.avgInterval / 1000) + 's' : ''}
            </div>
        </div>
        <div class="device-meta ${encClass}">${encStatus}</div>
        <div>
            <div class="transmission-bar">
                <div class="transmission-bar-fill" style="width: ${rateWidth}%"></div>
            </div>
        </div>
        <div class="device-meta">${Array.from(profile.addresses).slice(0, 2).join(', ')}</div>
    `;

    // Show anomaly alerts
    if (anomalies.length > 0) {
        anomalies.forEach(a => {
            const alertEl = document.createElement('div');
            alertEl.style.cssText = 'padding: 5px 15px; background: rgba(255,51,102,0.1); border-left: 2px solid var(--accent-red); font-size: 10px; color: var(--accent-red);';
            alertEl.textContent = '⚠ ' + a.message;
            row.appendChild(alertEl);
        });
    }

    // Limit displayed devices
    while (content.children.length > 50) {
        content.lastElementChild.remove();
    }
}

// Toggle recon panel visibility
function toggleRecon() {
    reconEnabled = !reconEnabled;
    localStorage.setItem('reconEnabled', reconEnabled);
    document.getElementById('reconPanel').style.display = reconEnabled ? 'block' : 'none';
    document.getElementById('reconBtn')?.classList.toggle('active', reconEnabled);

    // Populate recon display if enabled and we have data
    if (reconEnabled && deviceDatabase.size > 0) {
        deviceDatabase.forEach((profile, deviceId) => {
            updateReconDisplay(deviceId, profile, false, []);
        });
    }
}

// Initialize recon state
document.getElementById('reconPanel').style.display = reconEnabled ? 'block' : 'none';
if (reconEnabled) {
    document.getElementById('reconBtn')?.classList.add('active');
}

// Hook into existing message handlers to track devices
const originalAddMessage = addMessage;
addMessage = function (msg) {
    originalAddMessage(msg);
    trackDevice(msg);
};

const originalAddSensorReading = addSensorReading;
addSensorReading = function (data) {
    originalAddSensorReading(data);
    trackDevice(data);
};

// Hook rtlamr readings into device intelligence
const originalAddRtlamrReading = addRtlamrReading;
addRtlamrReading = function (data) {
    originalAddRtlamrReading(data);
    // Transform rtlamr data for device tracking
    const msgData = data.Message || {};
    const meterInfo = getMeterTypeInfo(msgData.EndpointType, data.Type);
    trackDevice({
        protocol: 'Meter',
        meterId: String(msgData.ID || 'Unknown'),
        address: String(msgData.ID || 'Unknown'),
        message: `${meterInfo.utility} - ${(msgData.Consumption || 0).toLocaleString()} units`,
        model: meterInfo.manufacturer || data.Type || 'Unknown',
        meterType: data.Type,
        endpointType: msgData.EndpointType,
        utility: meterInfo.utility,
        manufacturer: meterInfo.manufacturer,
        consumption: msgData.Consumption
    });
};

// Meter type/manufacturer lookup based on ERT endpoint types and message formats
function getMeterTypeInfo(endpointType, msgType) {
    // Common ERT endpoint type mappings (varies by utility)
    const endpointInfo = {
        // Electric meter types (0-7 common)
        0: { utility: 'Electric', manufacturer: 'Generic' },
        1: { utility: 'Electric', manufacturer: 'Generic' },
        2: { utility: 'Electric', manufacturer: 'Itron' },
        3: { utility: 'Electric', manufacturer: 'Itron' },
        4: { utility: 'Electric', manufacturer: 'Landis+Gyr' },
        5: { utility: 'Electric', manufacturer: 'Landis+Gyr' },
        6: { utility: 'Electric', manufacturer: 'Elster' },
        7: { utility: 'Electric', manufacturer: 'Elster' },
        // Gas meter types (8-15)
        8: { utility: 'Gas', manufacturer: 'Itron' },
        9: { utility: 'Gas', manufacturer: 'Itron' },
        10: { utility: 'Gas', manufacturer: 'Sensus' },
        11: { utility: 'Gas', manufacturer: 'Sensus' },
        12: { utility: 'Gas', manufacturer: 'Badger' },
        13: { utility: 'Gas', manufacturer: 'Neptune' },
        // Water meter types (16-23)
        16: { utility: 'Water', manufacturer: 'Badger' },
        17: { utility: 'Water', manufacturer: 'Badger' },
        18: { utility: 'Water', manufacturer: 'Neptune' },
        19: { utility: 'Water', manufacturer: 'Neptune' },
        20: { utility: 'Water', manufacturer: 'Sensus' },
        21: { utility: 'Water', manufacturer: 'Sensus' },
        22: { utility: 'Water', manufacturer: 'Master Meter' },
        23: { utility: 'Water', manufacturer: 'Mueller' },
        // Extended types
        156: { utility: 'Electric', manufacturer: 'Itron OpenWay' },
        157: { utility: 'Electric', manufacturer: 'Itron OpenWay' },
        180: { utility: 'Gas', manufacturer: 'Itron ERT' },
        188: { utility: 'Water', manufacturer: 'Badger ORION' },
        220: { utility: 'Electric', manufacturer: 'Landis+Gyr Focus' }
    };

    // Message type hints
    const msgTypeInfo = {
        'SCM': { utility: 'Electric', manufacturer: 'Standard ERT' },
        'SCM+': { utility: 'Electric', manufacturer: 'Enhanced ERT' },
        'IDM': { utility: 'Electric', manufacturer: 'Interval Data' },
        'NetIDM': { utility: 'Electric', manufacturer: 'Network IDM' },
        'R900': { utility: 'Water', manufacturer: 'Neptune R900' },
        'R900BCD': { utility: 'Water', manufacturer: 'Neptune R900' }
    };

    // Try endpoint type first
    if (endpointType !== undefined && endpointInfo[endpointType]) {
        return endpointInfo[endpointType];
    }

    // Fall back to message type
    if (msgType && msgTypeInfo[msgType]) {
        return msgTypeInfo[msgType];
    }

    // Default based on endpoint range
    if (endpointType !== undefined) {
        if (endpointType < 8) return { utility: 'Electric', manufacturer: 'Unknown' };
        if (endpointType < 16) return { utility: 'Gas', manufacturer: 'Unknown' };
        if (endpointType < 24) return { utility: 'Water', manufacturer: 'Unknown' };
    }

    return { utility: 'Unknown', manufacturer: 'Unknown' };
}

// Export device database
function exportDeviceDB() {
    const data = [];
    deviceDatabase.forEach((profile, id) => {
        data.push({
            id: id,
            protocol: profile.protocol,
            firstSeen: new Date(profile.firstSeen).toISOString(),
            lastSeen: new Date(profile.lastSeen).toISOString(),
            transmissionCount: profile.transmissionCount,
            avgIntervalSeconds: profile.avgInterval ? Math.round(profile.avgInterval / 1000) : null,
            addresses: Array.from(profile.addresses),
            models: Array.from(profile.models),
            encrypted: profile.encrypted,
            anomalyCount: profile.anomalies.length,
            recentMessages: profile.messages.slice(0, 5).map(m => m.text)
        });
    });
    downloadFile(JSON.stringify(data, null, 2), 'intercept_device_intelligence.json', 'application/json');
}

// Toggle recon panel collapse
function toggleReconCollapse() {
    const panel = document.getElementById('reconPanel');
    const icon = document.getElementById('reconCollapseIcon');
    panel.classList.toggle('collapsed');
    icon.textContent = panel.classList.contains('collapsed') ? '▶' : '▼';
}
