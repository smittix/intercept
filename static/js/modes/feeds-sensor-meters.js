/**
 * 433 MHz sensor and utility-meter (rtlamr) feed handling for the main page:
 * the SSE/agent streams, per-device dedup and the reading/meter cards. Moved
 * out of templates/index.html unchanged (only de-indented); a classic script
 * loaded after the inline block. Shared top-level names (e.g. uniqueDevices)
 * stay in the global lexical scope other scripts already use.
 */
// Track unique sensor devices
let uniqueDevices = new Set();

// Sensor frequency
function setSensorFreq(freq) {
    document.getElementById('sensorFrequency').value = freq;
    if (isSensorRunning) {
        fetch('/stop_sensor', { method: 'POST' })
            .then(() => setTimeout(() => startSensorDecoding(), 500));
    }
}

// --- Sensor packets: one mark per packet, by level and SNR (BurstPlot) ---
let sensorBurstPlot = null;

function initSensorScope() {
    const el = document.getElementById('sensorBurstPlot');
    if (!el || typeof BurstPlot === 'undefined') return;
    if (sensorBurstPlot) sensorBurstPlot.destroy();
    sensorBurstPlot = BurstPlot.create(el, { title: 'Packets' });
}

function addSensorBurst(msg) {
    if (!sensorBurstPlot || !msg || msg.rssi === undefined || msg.rssi === null) return;
    sensorBurstPlot.add({ level: msg.rssi, snr: msg.snr, noise: msg.noise, label: msg.model });
}

function stopSensorScope() {
    if (sensorBurstPlot) sensorBurstPlot.destroy();
    sensorBurstPlot = null;
}

// Start sensor decoding
async function startSensorDecoding() {
    const freq = document.getElementById('sensorFrequency').value;
    const gain = document.getElementById('sensorGain').value;
    const ppm = document.getElementById('sensorPpm').value;
    const units = document.getElementById('sensorUnits').value;
    const device = getSelectedDevice();

    // Check if using remote agent
    if (typeof currentAgent !== 'undefined' && currentAgent !== 'local') {
        // Check for conflicts with other running SDR modes
        if (typeof checkAgentModeConflict === 'function' && !await checkAgentModeConflict('sensor')) {
            return;  // User cancelled or conflict not resolved
        }

        // Route through agent proxy
        const config = {
            frequency: freq,
            gain: gain,
            ppm: ppm,
            units: units,
            device: device
        };

        fetch(`/controller/agents/${currentAgent}/sensor/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        }).then(r => r.json())
            .then(data => {
                // Handle controller proxy response (agent response is nested in 'result')
                const scanResult = data.result || data;
                if (scanResult.status === 'started' || scanResult.status === 'success') {
                    setSensorRunning(true);
                    startAgentSensorStream();
                    showInfo(`Sensor started on remote agent`);
                } else {
                    alert('Error: ' + (scanResult.message || 'Failed to start sensor on agent'));
                }
            })
            .catch(err => {
                alert('Error connecting to agent: ' + err.message);
            });
        return;
    }

    // Check if device is available
    if (!await checkDeviceAvailability('sensor')) {
        return;
    }

    // Check for remote SDR
    const remoteConfig = getRemoteSDRConfig();
    if (remoteConfig === false) return; // Validation failed

    const config = {
        frequency: freq,
        gain: gain,
        ppm: ppm,
        units: units,
        device: device,
        sdr_type: getSelectedSDRType(),
        bias_t: getBiasTEnabled()
    };

    // Add rtl_tcp params if using remote SDR
    if (remoteConfig) {
        config.rtl_tcp_host = remoteConfig.host;
        config.rtl_tcp_port = remoteConfig.port;
    }

    fetch('/start_sensor', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    }).then(r => r.json())
        .then(data => {
            if (data.status === 'started') {
                reserveDevice(parseInt(device), 'sensor');
                setSensorRunning(true);
                startSensorStream();

                // Initialize sensor filter bar
                const filterContainer = document.getElementById('filterBarContainer');
                const output = document.getElementById('output');
                if (filterContainer) {
                    filterContainer.innerHTML = '';
                    const filterBar = SignalCards.createSensorFilterBar(output);
                    filterContainer.appendChild(filterBar);
                    filterContainer.style.display = 'block';
                }

                // Clear address history for fresh session
                SignalCards.clearAddressHistory('sensor');

                // Clear existing output
                output.innerHTML = '<div class="placeholder signal-empty-state" style="display: none;"></div>';
            } else {
                showInfo('Error: ' + (data.message || 'Failed to start sensor'));
            }
        })
        .catch(err => {
            showInfo('Error starting sensor: ' + err.message);
        });
}

// Stop sensor decoding
function stopSensorDecoding() {
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
    const endpoint = isAgentMode
        ? `/controller/agents/${currentAgent}/sensor/stop`
        : '/stop_sensor';
    const timeoutMs = isAgentMode ? REMOTE_STOP_TIMEOUT_MS : LOCAL_STOP_TIMEOUT_MS;

    setSensorRunning(false);
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    if (agentPollInterval) {
        clearInterval(agentPollInterval);
        agentPollInterval = null;
    }
    if (!isAgentMode) {
        releaseDevice('sensor');
    }

    return postStopRequest(endpoint, timeoutMs).then(async (data) => {
        if (isAgentMode && data && data.status !== 'error' && data.status !== 'timeout') {
            showInfo('Sensor stopped on remote agent');
        }
        if (!isAgentMode) {
            await confirmStopped('433 MHz decoder', '/sensor/status', data, () => setSensorRunning(true));
        }
        return data;
    });
}

// Polling interval for agent data
let agentPollInterval = null;

// Start polling agent for sensor data
function startAgentSensorStream() {
    if (agentPollInterval) {
        clearInterval(agentPollInterval);
    }

    // Poll every 2 seconds for new data
    agentPollInterval = setInterval(() => {
        if (!isSensorRunning || currentAgent === 'local') {
            clearInterval(agentPollInterval);
            agentPollInterval = null;
            return;
        }

        fetch(`/controller/agents/${currentAgent}/sensor/data`)
            .then(r => r.json())
            .then(data => {
                if (data.sensors) {
                    data.sensors.forEach(sensor => {
                        displaySensorMessage(sensor);
                    });
                }
            })
            .catch(err => console.error('Agent poll error:', err));
    }, 2000);
}

// Display a sensor message (works for both local and remote)
function displaySensorMessage(msg) {
    const output = document.getElementById('output');
    if (!output) return;

    // Remove placeholder
    const placeholder = output.querySelector('.placeholder');
    if (placeholder) placeholder.style.display = 'none';

    addSensorBurst(msg);

    // Create signal card if SignalCards is available
    if (typeof SignalCards !== 'undefined' && SignalCards.createFromSensor) {
        const card = SignalCards.createFromSensor(msg);
        if (card) {
            output.insertBefore(card, output.firstChild);
            sensorCount++;
            updateStats();
        }
    }
}

function setSensorRunning(running) {
    isSensorRunning = running;
    document.getElementById('statusDot').classList.toggle('running', running);
    document.getElementById('statusText').textContent = running ? 'Listening...' : 'Idle';
    document.getElementById('startSensorBtn').style.display = running ? 'none' : 'block';
    document.getElementById('stopSensorBtn').style.display = running ? 'block' : 'none';

    // Signal scope
    const scopePanel = document.getElementById('sensorScopePanel');
    if (scopePanel) {
        if (running) {
            scopePanel.style.display = 'block';
            initSensorScope();
        } else {
            stopSensorScope();
            scopePanel.style.display = 'none';
        }
    }
}

function startSensorStream() {
    if (eventSource) {
        eventSource.close();
    }

    eventSource = new EventSource('/stream_sensor');

    eventSource.onopen = function () {
        showInfo('Sensor stream connected...');
    };

    eventSource.onmessage = function (e) {
        const data = JSON.parse(e.data);
        if (data.type === 'sensor') {
            addSensorReading(data);
        } else if (data.type === 'status') {
            if (data.text === 'stopped') {
                setSensorRunning(false);
            }
        } else if (data.type === 'info' || data.type === 'raw') {
            showInfo(data.text);
        }
    };

    eventSource.onerror = function (e) {
        console.error('Sensor stream error');
    };
}

function addSensorReading(data) {
    const output = document.getElementById('output');
    const placeholder = output.querySelector('.placeholder');
    if (placeholder) placeholder.remove();

    // Store for export
    allMessages.push(data);
    playAlert();
    pulseSignal();

    addSensorBurst(data);

    sensorCount++;
    document.getElementById('sensorCount').textContent = sensorCount;

    // Track unique devices by model + id
    const deviceKey = (data.model || 'Unknown') + '_' + (data.id || data.channel || '0');
    if (!uniqueDevices.has(deviceKey)) {
        uniqueDevices.add(deviceKey);
        document.getElementById('deviceCount').textContent = uniqueDevices.size;
    }

    // Convert rtl_433 data format to our card format
    const msg = {
        model: data.model || 'Unknown',
        id: data.id || data.channel || 'N/A',
        channel: data.channel,
        timestamp: data.time || new Date().toISOString(),
        raw: data.raw,
        frequency: data.freq
    };

    // Map common sensor fields
    if (data.temperature_C !== undefined) {
        msg.temperature = data.temperature_C;
        msg.temperature_unit = 'C';
    } else if (data.temperature_F !== undefined) {
        msg.temperature = data.temperature_F;
        msg.temperature_unit = 'F';
    }
    if (data.humidity !== undefined) msg.humidity = data.humidity;
    if (data.battery_ok !== undefined) msg.battery = data.battery_ok ? 'OK' : 'LOW';
    if (data.pressure_hPa !== undefined) {
        msg.pressure = data.pressure_hPa;
        msg.pressure_unit = 'hPa';
    } else if (data.pressure_PSI !== undefined) {
        msg.pressure = data.pressure_PSI;
        msg.pressure_unit = 'PSI';
    } else if (data.pressure_kPa !== undefined) {
        msg.pressure = data.pressure_kPa;
        msg.pressure_unit = 'kPa';
    } else if (data.tire_pressure_kPa !== undefined) {
        msg.pressure = data.tire_pressure_kPa;
        msg.pressure_unit = 'kPa';
    }
    if (data.flags !== undefined) msg.state = data.flags;
    else if (data.state !== undefined) msg.state = data.state;
    if (data.wind_avg_km_h !== undefined) {
        msg.wind_speed = data.wind_avg_km_h;
        msg.wind_unit = 'km/h';
    }
    if (data.rain_mm !== undefined) {
        msg.rain = data.rain_mm;
        msg.rain_unit = 'mm';
    }

    if (data.snr  !== undefined) msg.snr  = data.snr;
    if (data.rssi !== undefined) msg.rssi = data.rssi;
    // Create card using SignalCards component
    const card = SignalCards.createSensorCard(msg);
    if (typeof SensorDashboard !== 'undefined') SensorDashboard.addReading(msg);
    output.insertBefore(card, output.firstChild);

    // Add to activity timeline
    if (typeof addTimelineEvent === 'function') {
        addTimelineEvent('sensor', {
            id: `${msg.model}-${msg.sensor_id}-${msg.timestamp}`,
            label: msg.model || 'Unknown Sensor',
            sublabel: msg.sensor_id ? `ID: ${msg.sensor_id}` : '',
            timestamp: msg.timestamp || Date.now(),
            type: 'sensor',
            status: card.dataset.status || 'new'
        });
    }

    // Update filter counts using sensor-specific filter bar
    const sensorFilterBar = document.getElementById('sensorFilterBar');
    if (sensorFilterBar && sensorFilterBar.applyFilters) {
        sensorFilterBar.applyFilters();
    }

    if (autoScroll) output.scrollTop = 0;

    // Keep list manageable
    const cards = output.querySelectorAll('.signal-card');
    for (let i = cards.length - 1; i >= 100; i--) {
        cards[i].remove();
    }
}

function toggleSensorLogging() {
    const enabled = document.getElementById('sensorLogging').checked;
    fetch('/logging', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: enabled, log_file: 'sensor_data.log' })
    });
}

// ========================================
// RTLAMR Functions
// ========================================
let isRtlamrRunning = false;

function setRtlamrFreq(freq) {
    document.getElementById('rtlamrFrequency').value = freq;
}

// RTLAMR mode polling timer for agent mode
let rtlamrPollTimer = null;
let rtlamrCurrentAgent = null;

async function startRtlamrDecoding() {
    const freq = document.getElementById('rtlamrFrequency').value;
    const gain = document.getElementById('rtlamrGain').value;
    const ppm = document.getElementById('rtlamrPpm').value;
    const device = getSelectedDevice();
    const msgtype = document.getElementById('rtlamrMsgType').value;
    const filterid = document.getElementById('rtlamrFilterId').value;
    const unique = document.getElementById('rtlamrUnique').checked;

    // Check if using agent mode
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
    rtlamrCurrentAgent = isAgentMode ? currentAgent : null;

    // Check if device is available (only for local mode)
    if (!isAgentMode && !await checkDeviceAvailability('rtlamr')) {
        return;
    }

    const config = {
        frequency: freq,
        gain: gain,
        ppm: ppm,
        device: device,
        sdr_type: getSelectedSDRType(),
        msgtype: msgtype,
        filterid: filterid,
        unique: unique,
        format: 'json'
    };

    // Determine endpoint based on agent mode
    const endpoint = isAgentMode
        ? `/controller/agents/${currentAgent}/rtlamr/start`
        : '/start_rtlamr';

    fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    }).then(r => r.json())
        .then(data => {
            // Handle controller proxy response format
            const scanResult = isAgentMode && data.result ? data.result : data;

            if (scanResult.status === 'started' || scanResult.status === 'success') {
                if (!isAgentMode) {
                    reserveDevice(parseInt(device), 'rtlamr');
                }
                setRtlamrRunning(true);
                startRtlamrStream(isAgentMode);

                // Initialize meter filter bar (reuse sensor filter bar since same structure)
                const filterContainer = document.getElementById('filterBarContainer');
                const output = document.getElementById('output');
                if (filterContainer) {
                    filterContainer.innerHTML = '';
                    const filterBar = SignalCards.createSensorFilterBar(output);
                    filterBar.id = 'meterFilterBar';
                    filterBar.querySelector('#sensorSearchInput').id = 'meterSearchInput';
                    filterBar.querySelector('#meterSearchInput').placeholder = 'Search meter ID...';
                    filterContainer.appendChild(filterBar);
                    filterContainer.style.display = 'block';
                }

                // Clear address history for fresh session
                SignalCards.clearAddressHistory('meter');

                // Clear existing output
                output.innerHTML = '<div class="placeholder signal-empty-state" style="display: none;"></div>';
            } else {
                alert('Error: ' + (scanResult.message || scanResult.error || 'Failed to start'));
            }
        });
}

function stopRtlamrDecoding() {
    const isAgentMode = rtlamrCurrentAgent !== null;
    const endpoint = isAgentMode
        ? `/controller/agents/${rtlamrCurrentAgent}/rtlamr/stop`
        : '/stop_rtlamr';
    const timeoutMs = isAgentMode ? REMOTE_STOP_TIMEOUT_MS : LOCAL_STOP_TIMEOUT_MS;

    rtlamrCurrentAgent = null;
    setRtlamrRunning(false);
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    if (rtlamrPollTimer) {
        clearInterval(rtlamrPollTimer);
        rtlamrPollTimer = null;
    }
    if (!isAgentMode) {
        releaseDevice('rtlamr');
    }

    return postStopRequest(endpoint, timeoutMs);
}

function setRtlamrRunning(running) {
    isRtlamrRunning = running;
    document.getElementById('statusDot').classList.toggle('running', running);
    document.getElementById('statusText').textContent = running ? 'Listening...' : 'Idle';
    document.getElementById('startRtlamrBtn').style.display = running ? 'none' : 'block';
    document.getElementById('stopRtlamrBtn').style.display = running ? 'block' : 'none';
    
    // Update mode indicator with frequency
    if (running) {
        const freq = document.getElementById('rtlamrFrequency').value;
        setActiveModeIndicator('METERS @ ' + freq + ' MHz');
    } else {
        setActiveModeIndicator('METERS');
    }
}

function startRtlamrStream(isAgentMode = false) {
    if (eventSource) {
        eventSource.close();
    }

    // Use different stream endpoint for agent mode
    const streamUrl = isAgentMode ? '/controller/stream/all' : '/stream_rtlamr';
    eventSource = new EventSource(streamUrl);

    eventSource.onopen = function () {
        showInfo('RTLAMR stream connected...');
    };

    eventSource.onmessage = function (e) {
        const data = JSON.parse(e.data);

        if (isAgentMode) {
            // Handle multi-agent stream format
            if (data.scan_type === 'rtlamr' && data.payload) {
                const payload = data.payload;
                if (payload.type === 'rtlamr') {
                    payload.agent_name = data.agent_name;
                    addRtlamrReading(payload);
                } else if (payload.type === 'status') {
                    if (payload.text === 'stopped') {
                        setRtlamrRunning(false);
                    }
                } else if (payload.type === 'info' || payload.type === 'raw') {
                    showInfo(`[${data.agent_name}] ${payload.text}`);
                }
            }
        } else {
            // Local stream format
            if (data.type === 'rtlamr') {
                addRtlamrReading(data);
            } else if (data.type === 'status') {
                if (data.text === 'stopped') {
                    setRtlamrRunning(false);
                }
            } else if (data.type === 'info' || data.type === 'raw') {
                showInfo(data.text);
            }
        }
    };

    eventSource.onerror = function (e) {
        console.error('RTLAMR stream error');
    };

    // Start polling fallback for agent mode
    if (isAgentMode) {
        startRtlamrPolling();
    }
}

// Track last reading count for polling
let lastRtlamrReadingCount = 0;

function startRtlamrPolling() {
    if (rtlamrPollTimer) return;
    lastRtlamrReadingCount = 0;

    const pollInterval = 2000;
    rtlamrPollTimer = setInterval(async () => {
        if (!isRtlamrRunning || !rtlamrCurrentAgent) {
            clearInterval(rtlamrPollTimer);
            rtlamrPollTimer = null;
            return;
        }

        try {
            const response = await fetch(`/controller/agents/${rtlamrCurrentAgent}/rtlamr/data`);
            if (!response.ok) return;

            const data = await response.json();
            const result = data.result || data;
            const readings = result.data || [];

            // Process new readings
            if (readings.length > lastRtlamrReadingCount) {
                const newReadings = readings.slice(lastRtlamrReadingCount);
                newReadings.forEach(reading => {
                    const displayReading = {
                        type: 'rtlamr',
                        ...reading,
                        agent_name: result.agent_name || 'Remote Agent'
                    };
                    addRtlamrReading(displayReading);
                });
                lastRtlamrReadingCount = readings.length;
            }
        } catch (err) {
            console.error('RTLAMR polling error:', err);
        }
    }, pollInterval);
}

function addRtlamrReading(data) {
    const output = document.getElementById('output');
    const placeholder = output.querySelector('.placeholder');
    if (placeholder) placeholder.remove();

    // Store for export (all raw readings)
    allMessages.push(data);
    pulseSignal();

    sensorCount++;
    document.getElementById('sensorCount').textContent = sensorCount;

    // Aggregate meter data using MeterAggregator
    const { meter, isNew } = MeterAggregator.ingest(data);

    // Track unique meters by ID
    const meterId = meter.id;
    if (meterId !== 'Unknown') {
        const deviceKey = 'METER_' + meterId;
        if (!uniqueDevices.has(deviceKey)) {
            uniqueDevices.add(deviceKey);
            document.getElementById('deviceCount').textContent = uniqueDevices.size;
        }
    }

    // Check if card already exists for this meter
    const existingCard = document.getElementById('metercard_' + meterId);

    if (existingCard) {
        // Update existing card in place
        SignalCards.updateAggregatedMeterCard(existingCard, meter);
    } else {
        // Create new aggregated meter card
        const card = SignalCards.createAggregatedMeterCard(meter);
        output.insertBefore(card, output.firstChild);

        // Only play alert for new meters (not updates)
        playAlert();
    }

    // Update filter counts
    SignalCards.updateCounts(output);

    // Limit to max 50 unique meters (cards won't pile up since we update in place)
    const cards = output.querySelectorAll('.signal-card.meter-aggregated');
    for (let i = cards.length - 1; i >= 50; i--) {
        cards[i].remove();
    }
}

function toggleRtlamrUnique() {
    // No action needed, value is read on start
}

function toggleRtlamrLogging() {
    const enabled = document.getElementById('rtlamrLogging').checked;
    fetch('/logging', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: enabled, log_file: 'rtlamr_data.log' })
    });
}
