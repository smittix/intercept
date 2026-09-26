/**
 * SDR hardware handling for the main page: capabilities, device reservation
 * and availability, the device/SDR-type selectors, status polling, bias-T,
 * remote-SDR config and protocol selection.
 *
 * Moved out of templates/index.html unchanged (only de-indented); a classic
 * script loaded after the inline block, sharing its top-level names as before.
 */
// SDR hardware capabilities
const sdrCapabilities = {
    'rtlsdr': { name: 'RTL-SDR', freq_min: 24, freq_max: 1766, gain_min: 0, gain_max: 50 },
    'sdrplay': { name: 'SDRplay', freq_min: 0.001, freq_max: 2000, gain_min: 0, gain_max: 59 },
    'limesdr': { name: 'LimeSDR', freq_min: 0.1, freq_max: 3800, gain_min: 0, gain_max: 73 },
    'hackrf': { name: 'HackRF', freq_min: 1, freq_max: 6000, gain_min: 0, gain_max: 102 }, // LNA(40)+VGA(62)
    'airspy': { name: 'Airspy', freq_min: 24, freq_max: 1800, gain_min: 0, gain_max: 45 },  // LNA(15)+Mix(15)+VGA(15)
    'usrp': { name: 'USRP', freq_min: 1, freq_max: 6000, gain_min: 0, gain_max: 76 },
    'bladerf': { name: 'BladeRF', freq_min: 47, freq_max: 6000, gain_min: 0, gain_max: 66 },
    'hydrasdr': { name: 'HydraSDR RFOne', freq_min: 24, freq_max: 1800, gain_min: 0, gain_max: 21 }
};

// Current device list with SDR type info
let currentDeviceList = [];

// SDR Device Usage Tracking
// Tracks which mode is using which device (keyed by "sdr_type:index")
const sdrDeviceUsage = {
    // "sdr_type:index": 'modeName' (e.g., "rtlsdr:0": 'pager', "hackrf:0": 'scanner')
};

function getDeviceInUseBy(deviceIndex, sdrType) {
    const key = `${sdrType || getSelectedSDRType()}:${deviceIndex}`;
    return sdrDeviceUsage[key] || null;
}

function isDeviceInUse(deviceIndex, sdrType) {
    const key = `${sdrType || getSelectedSDRType()}:${deviceIndex}`;
    return sdrDeviceUsage[key] !== undefined;
}

function reserveDevice(deviceIndex, modeName, sdrType) {
    const key = `${sdrType || getSelectedSDRType()}:${deviceIndex}`;
    sdrDeviceUsage[key] = modeName;
    updateDeviceSelectStatus();
}

function releaseDevice(modeName) {
    for (const [key, mode] of Object.entries(sdrDeviceUsage)) {
        if (mode === modeName) {
            delete sdrDeviceUsage[key];
        }
    }
    updateDeviceSelectStatus();
}

function getAvailableDevice() {
    // Find first device not in use (within selected SDR type)
    const sdrType = getSelectedSDRType();
    for (const device of currentDeviceList) {
        if ((device.sdr_type || 'rtlsdr') === sdrType && !isDeviceInUse(device.index, sdrType)) {
            return device.index;
        }
    }
    return null;
}

function updateDeviceSelectStatus() {
    // Update device dropdown to show which devices are in use
    const select = document.getElementById('deviceSelect');
    if (!select) return;

    const sdrType = getSelectedSDRType();
    const options = select.querySelectorAll('option');
    options.forEach(opt => {
        const idx = parseInt(opt.value);
        const usedBy = getDeviceInUseBy(idx, sdrType);
        const baseName = opt.textContent.replace(/ \[.*\]$/, ''); // Remove existing status
        if (usedBy) {
            opt.textContent = `${baseName} [${usedBy.toUpperCase()}]`;
            opt.style.color = 'var(--accent-orange)';
        } else {
            opt.textContent = baseName;
            opt.style.color = '';
        }
    });
}

async function checkDeviceAvailability(modeName) {
    const selectedDevice = parseInt(getSelectedDevice());
    const usedBy = getDeviceInUseBy(selectedDevice);

    if (usedBy && usedBy !== modeName) {
        // Device is in use by another mode
        const availableDevice = getAvailableDevice();

        if (availableDevice !== null) {
            // Another device is available - offer to switch
            const switchDevice = await AppFeedback.confirmAction({
                title: 'SDR Device In Use',
                message: `Device ${selectedDevice} is in use by ${usedBy.toUpperCase()}. Device ${availableDevice} is available. Switch to it?`,
                confirmLabel: 'Switch Device',
                confirmClass: 'btn-danger'
            });
            if (switchDevice) {
                document.getElementById('deviceSelect').value = availableDevice;
                return true; // Can proceed with new device
            }
            return false; // User declined to switch
        } else {
            // No other devices available
            showNotification('SDR In Use',
                `Device ${selectedDevice} is in use by ${usedBy.toUpperCase()}. ` +
                `No other SDR devices available. Stop ${usedBy} first or connect another SDR.`
            );
            return false;
        }
    }
    return true; // Device is available
}

function onSDRTypeChanged() {
    const sdrType = document.getElementById('sdrTypeSelect').value;
    const select = document.getElementById('deviceSelect');

    // Filter devices by selected SDR type
    const filteredDevices = currentDeviceList.filter(d =>
        (d.sdr_type || 'rtlsdr') === sdrType
    );

    if (filteredDevices.length === 0) {
        select.innerHTML = `<option value="0">No ${sdrCapabilities[sdrType]?.name || sdrType} devices found</option>`;
    } else {
        select.innerHTML = filteredDevices.map(d => {
            const serialSuffix = d.serial && d.serial !== 'N/A' && d.serial !== 'Unknown' ? ` (SN: ${d.serial})` : '';
            return `<option value="${d.index}" data-sdr-type="${escapeHtml(d.sdr_type || 'rtlsdr')}">${d.index}: ${escapeHtml(d.name)}${escapeHtml(serialSuffix)}</option>`;
        }).join('');
    }

    // Update capabilities display
    updateCapabilitiesDisplay(sdrType);
}

function updateCapabilitiesDisplay(sdrType) {
    const caps = sdrCapabilities[sdrType];
    if (caps) {
        document.getElementById('capFreqRange').textContent = `${caps.freq_min}-${caps.freq_max} MHz`;
        document.getElementById('capGainRange').textContent = `${caps.gain_min}-${caps.gain_max} dB`;
        // Update max attribute on all mode gain inputs so constraints match the SDR
        const gainMax = caps.gain_max;
        ['gain', 'sensorGain', 'weatherSatGain'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.max = gainMax;
        });
    }
}

function refreshDevices() {
    fetch('/devices')
        .then(r => r.json())
        .then(devices => {
            // Store full device list with SDR type info
            currentDeviceList = devices;
            deviceList = devices;

            // Auto-select SDR type if devices found
            if (devices.length > 0) {
                const firstType = devices[0].sdr_type || 'rtlsdr';
                document.getElementById('sdrTypeSelect').value = firstType;
            }

            // Trigger filter update
            onSDRTypeChanged();

            // Also refresh SDR status panel
            fetchSdrStatus();
        })
        .catch(err => {
            console.error('Failed to refresh devices:', err);
            const select = document.getElementById('deviceSelect');
            select.innerHTML = '<option value="0">Error loading devices</option>';
        });
}

// SDR Device Status Panel
let sdrStatusPollingInterval = null;

function renderSdrStatus(devices) {
    const container = document.getElementById('sdrStatusList');
    if (!container) return;

    if (!devices || devices.length === 0) {
        container.innerHTML = '<div style="padding: 8px; color: var(--text-secondary); font-size: 11px; text-align: center;">No SDR devices detected</div>';
        return;
    }

    const html = devices.map(d => {
        const isActive = d.in_use;
        const statusDot = isActive
            ? '<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #00ff88; box-shadow: 0 0 6px #00ff88; margin-right: 6px;"></span>'
            : '<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #555; margin-right: 6px;"></span>';
        const modeName = d.used_by ? d.used_by.toUpperCase() : 'IDLE';
        const modeColor = isActive ? '#00ff88' : '#666';
        const sdrType = (d.sdr_type || 'RTL').toUpperCase().replace('RTLSDR', 'RTL');

        return `<div style="display: flex; align-items: center; justify-content: space-between; padding: 6px 8px; border-bottom: 1px solid var(--border-color);">
            <div style="display: flex; align-items: center;">
                ${statusDot}
                <span style="font-size: 11px;">#${d.index} ${escapeHtml(d.name || 'Unknown')}${d.serial && d.serial !== 'N/A' && d.serial !== 'Unknown' ? ` (${escapeHtml(d.serial)})` : ''}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 6px;">
                <span style="font-size: 10px; color: ${modeColor}; font-weight: bold;">${modeName}</span>
                <span style="font-size: 9px; padding: 1px 4px; background: var(--bg-tertiary); border-radius: 3px; color: var(--text-secondary);">${sdrType}</span>
            </div>
        </div>`;
    }).join('');

    container.innerHTML = html;
}

function fetchSdrStatus() {
    // Avoid probing SDR inventory while HackRF SubGHz mode is active.
    // Device discovery runs hackrf_info and can disrupt active HackRF streams.
    if (typeof currentMode !== 'undefined' && currentMode === 'subghz') {
        return;
    }
    fetch('/devices/status')
        .then(r => r.json())
        .then(devices => {
            renderSdrStatus(devices);
        })
        .catch(err => {
            const transient = (typeof window.isTransientOrOffline === 'function' && window.isTransientOrOffline(err)) ||
                (typeof navigator !== 'undefined' && navigator.onLine === false) ||
                /failed to fetch|network io suspended|networkerror|timeout/i.test(String((err && err.message) || err || ''));
            if (!transient) {
                console.error('Failed to fetch SDR status:', err);
            }
            const container = document.getElementById('sdrStatusList');
            if (container) {
                if (transient) {
                    container.innerHTML = '<div style="padding: 8px; color: var(--text-secondary); font-size: 11px; text-align: center;">Status temporarily unavailable</div>';
                } else {
                    container.innerHTML = '<div style="padding: 8px; color: var(--accent-red); font-size: 11px; text-align: center;">Error loading status</div>';
                }
            }
        });
}

function startSdrStatusPolling() {
    // Initial fetch
    fetchSdrStatus();
    // Poll every 5 seconds
    sdrStatusPollingInterval = VisibleInterval.set(fetchSdrStatus, 5000);
}

function stopSdrStatusPolling() {
    if (sdrStatusPollingInterval) {
        VisibleInterval.clear(sdrStatusPollingInterval);
        sdrStatusPollingInterval = null;
    }
}

function getSelectedDevice() {
    return document.getElementById('deviceSelect').value;
}

function getSelectedSDRType() {
    return document.getElementById('sdrTypeSelect').value;
}

// Bias-T power setting
function saveBiasTSetting() {
    const enabled = document.getElementById('biasT')?.checked || false;
    localStorage.setItem('biasTEnabled', enabled);
    // Warn if any SDR mode is currently running — bias-T is applied at
    // start time and cannot be toggled on a running device.
    const anyRunning = isRunning || isSensorRunning
        || (typeof isAdsbRunning !== 'undefined' && isAdsbRunning);
    if (anyRunning) {
        showInfo('Bias-T change will take effect after restarting the active SDR mode');
    }
}

function getBiasTEnabled() {
    return document.getElementById('biasT')?.checked || false;
}

function loadBiasTSetting() {
    const saved = localStorage.getItem('biasTEnabled');
    if (saved === 'true') {
        const checkbox = document.getElementById('biasT');
        if (checkbox) checkbox.checked = true;
    }
}

function toggleRemoteSDR() {
    const useRemote = document.getElementById('useRemoteSDR').checked;
    const configDiv = document.getElementById('remoteSDRConfig');
    const localControls = document.querySelectorAll('#sdrTypeSelect, #deviceSelect');

    configDiv.style.display = useRemote ? 'block' : 'none';

    // Dim local device controls when using remote
    localControls.forEach(el => {
        el.style.opacity = useRemote ? '0.5' : '1';
        el.disabled = useRemote;
    });
}

function getRemoteSDRConfig() {
    const useRemote = document.getElementById('useRemoteSDR').checked;
    if (!useRemote) return null;

    const host = document.getElementById('rtlTcpHost').value.trim();
    const port = parseInt(document.getElementById('rtlTcpPort').value) || 1234;

    if (!host) {
        alert('Please enter rtl_tcp host address');
        return false;
    }

    return { host, port };
}

function getSelectedProtocols() {
    const protocols = [];
    if (document.getElementById('proto_pocsag512').checked) protocols.push('POCSAG512');
    if (document.getElementById('proto_pocsag1200').checked) protocols.push('POCSAG1200');
    if (document.getElementById('proto_pocsag2400').checked) protocols.push('POCSAG2400');
    if (document.getElementById('proto_flex').checked) protocols.push('FLEX');
    return protocols;
}
