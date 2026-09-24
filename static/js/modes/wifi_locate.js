/**
 * WiFi Locate — WiFi AP Location Mode
 * Real-time signal strength meter with proximity audio for locating WiFi devices by BSSID.
 * Reuses existing WiFi v2 API (/wifi/v2/start, /wifi/v2/stop, /wifi/v2/stream, /wifi/v2/status).
 */
const WiFiLocate = (function() {
    'use strict';

    const API_BASE = '/wifi/v2';
    const MAX_RSSI_POINTS = 60;
    const SIGNAL_LOST_TIMEOUT_MS = 30000;
    const BAR_SEGMENTS = 20;
    const TX_POWER = -30;

    const ENV_PATH_LOSS = {
        FREE_SPACE: 2.0,
        OUTDOOR: 2.8,
        INDOOR: 3.5,
    };

    let eventSource = null;
    let targetBssid = null;
    let targetSsid = null;
    let rssiHistory = [];
    let chartCanvas = null;
    let chartCtx = null;
    let audioCtx = null;
    let audioEnabled = false;
    let beepTimer = null;
    let currentEnvironment = 'OUTDOOR';
    let handoffData = null;
    let modeActive = false;
    let locateActive = false;
    let rssiMin = null;
    let rssiMax = null;
    let rssiSum = 0;
    let rssiCount = 0;
    let lastUpdateTime = 0;
    let signalLostTimer = null;

    function debugLog(...args) {
        console.debug('[WiFiLocate]', ...args);
    }

    // ========================================================================
    // Lifecycle
    // ========================================================================

    function init() {
        modeActive = true;
        chartCanvas = document.getElementById('wflRssiChart');
        chartCtx = chartCanvas ? chartCanvas.getContext('2d') : null;
        buildBarSegments();
    }

    function start() {
        const bssidInput = document.getElementById('wflBssid');
        const bssid = (bssidInput?.value || '').trim().toUpperCase();

        if (!bssid || !/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(bssid)) {
            if (typeof showNotification === 'function') {
                showNotification('Invalid BSSID', 'Enter a valid MAC address (AA:BB:CC:DD:EE:FF)');
            }
            return;
        }

        targetBssid = bssid;
        targetSsid = handoffData?.ssid || null;
        locateActive = true;

        // Reset stats
        rssiHistory = [];
        rssiMin = null;
        rssiMax = null;
        rssiSum = 0;
        rssiCount = 0;
        lastUpdateTime = 0;

        // Update UI
        updateTargetDisplay();
        showHud(true);
        updateStatDisplay('--', '--', '--', '--');
        updateRssiDisplay('--', '');
        updateDistanceDisplay('--');
        clearBarSegments();
        hideSignalLost();

        // Toggle buttons
        const startBtn = document.getElementById('wflStartBtn');
        const stopBtn = document.getElementById('wflStopBtn');
        const statusEl = document.getElementById('wflScanStatus');
        if (startBtn) startBtn.style.display = 'none';
        if (stopBtn) stopBtn.style.display = '';
        if (statusEl) statusEl.style.display = '';

        // Check if WiFi scan is running, auto-start deep scan if needed
        checkAndStartScan().then(() => {
            connectSSE();
        });
    }

    function stop() {
        locateActive = false;

        // Close SSE
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }

        // Clear timers
        clearBeepTimer();
        clearSignalLostTimer();

        // Stop audio
        stopAudio();

        // Toggle buttons
        const startBtn = document.getElementById('wflStartBtn');
        const stopBtn = document.getElementById('wflStopBtn');
        const statusEl = document.getElementById('wflScanStatus');
        if (startBtn) startBtn.style.display = '';
        if (stopBtn) stopBtn.style.display = 'none';
        if (statusEl) statusEl.style.display = 'none';

        // Show idle UI
        showHud(false);
    }

    function destroy() {
        stop();
        modeActive = false;
        targetBssid = null;
        targetSsid = null;
    }

    function setActiveMode(active) {
        modeActive = active;
    }

    // ========================================================================
    // WiFi Scan Management
    // ========================================================================

    async function checkAndStartScan() {
        try {
            const resp = await fetch(`${API_BASE}/scan/status`);
            const data = await resp.json();
            if (data.scanning && data.scan_type === 'deep') {
                debugLog('Deep scan already running');
                return;
            }
            // Auto-start deep scan
            debugLog('Starting deep scan for locate');
            await fetch(`${API_BASE}/scan/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ scan_type: 'deep' }),
            });
        } catch (e) {
            debugLog('Error checking/starting scan:', e);
        }
    }

    // ========================================================================
    // SSE Connection
    // ========================================================================

    function connectSSE() {
        if (eventSource) {
            eventSource.close();
        }

        const streamUrl = `${API_BASE}/stream`;
        eventSource = new EventSource(streamUrl);

        eventSource.onopen = () => {
            debugLog('SSE connected');
        };

        eventSource.onmessage = (event) => {
            if (!locateActive || !targetBssid) return;
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'keepalive') return;

                // Filter for our target BSSID
                if (data.type === 'network_update' && data.network) {
                    const net = data.network;
                    const bssid = (net.bssid || '').toUpperCase();
                    if (bssid === targetBssid) {
                        const rssi = parseInt(net.rssi_current ?? net.signal ?? net.rssi, 10);
                        if (!isNaN(rssi)) {
                            // Pick up SSID if we don't have it yet
                            if (!targetSsid && net.essid) {
                                targetSsid = net.essid;
                                updateTargetDisplay();
                            }
                            updateMeter(rssi);
                        }
                    }
                }
            } catch (e) {
                debugLog('SSE parse error:', e);
            }
        };

        eventSource.onerror = () => {
            debugLog('SSE error, reconnecting...');
            if (locateActive) {
                setTimeout(() => {
                    if (locateActive) connectSSE();
                }, 3000);
            }
        };
    }

    // ========================================================================
    // Signal Processing
    // ========================================================================

    function updateMeter(rssi) {
        lastUpdateTime = Date.now();
        hideSignalLost();
        resetSignalLostTimer();

        // Update stats
        rssiCount++;
        rssiSum += rssi;
        if (rssiMin === null || rssi < rssiMin) rssiMin = rssi;
        if (rssiMax === null || rssi > rssiMax) rssiMax = rssi;
        const avg = Math.round(rssiSum / rssiCount);

        // Update history
        rssiHistory.push(rssi);
        if (rssiHistory.length > MAX_RSSI_POINTS) {
            rssiHistory.shift();
        }

        // Determine strength class
        let cls = 'weak';
        if (rssi >= -50) cls = 'good';
        else if (rssi >= -70) cls = 'medium';

        // Update displays
        updateRssiDisplay(rssi, cls);
        updateDistanceDisplay(estimateDistance(rssi));
        updateBarSegments(rssi);
        updateStatDisplay(rssi, rssiMin, rssiMax, avg);
        drawRssiChart();

        // Audio
        if (audioEnabled) {
            scheduleBeeps(rssi);
        }
    }

    function estimateDistance(rssi) {
        const n = ENV_PATH_LOSS[currentEnvironment] || 2.8;
        const dist = Math.pow(10, (TX_POWER - rssi) / (10 * n));
        if (dist < 1) return dist.toFixed(2) + ' m';
        if (dist < 100) return dist.toFixed(1) + ' m';
        return Math.round(dist) + ' m';
    }

    // ========================================================================
    // UI Updates
    // ========================================================================

    function showHud(show) {
        const hud = document.getElementById('wflHud');
        const waiting = document.getElementById('wflWaiting');
        if (hud) hud.style.display = show ? '' : 'none';
        if (waiting) waiting.style.display = show ? 'none' : '';
    }

    function updateTargetDisplay() {
        const ssidEl = document.getElementById('wflTargetSsid');
        const bssidEl = document.getElementById('wflTargetBssid');
        if (ssidEl) ssidEl.textContent = targetSsid || 'Unknown SSID';
        if (bssidEl) bssidEl.textContent = targetBssid || '--';
    }

    function updateRssiDisplay(value, cls) {
        const el = document.getElementById('wflRssiValue');
        if (!el) return;
        el.textContent = typeof value === 'number' ? value + ' dBm' : value;
        el.className = 'wfl-rssi-display' + (cls ? ' ' + cls : '');
    }

    function updateDistanceDisplay(text) {
        const el = document.getElementById('wflDistance');
        if (el) el.textContent = text;
    }

    function updateStatDisplay(current, min, max, avg) {
        const set = (id, v) => {
            const el = document.getElementById(id);
            if (el) el.textContent = v;
        };
        set('wflStatCurrent', typeof current === 'number' ? current + ' dBm' : current);
        set('wflStatMin', typeof min === 'number' ? min + ' dBm' : min);
        set('wflStatMax', typeof max === 'number' ? max + ' dBm' : max);
        set('wflStatAvg', typeof avg === 'number' ? avg + ' dBm' : avg);
    }

    // ========================================================================
    // Bar Segments
    // ========================================================================

    function buildBarSegments() {
        const container = document.getElementById('wflBarContainer');
        if (!container || container.children.length === BAR_SEGMENTS) return;
        container.innerHTML = '';
        for (let i = 0; i < BAR_SEGMENTS; i++) {
            const seg = document.createElement('div');
            seg.className = 'wfl-bar-segment';
            container.appendChild(seg);
        }
    }

    function updateBarSegments(rssi) {
        const container = document.getElementById('wflBarContainer');
        if (!container) return;
        // Map RSSI -100..-20 to 0..20 active segments
        const strength = Math.max(0, Math.min(1, (rssi + 100) / 80));
        const activeCount = Math.round(strength * BAR_SEGMENTS);
        const segments = container.children;
        for (let i = 0; i < segments.length; i++) {
            segments[i].classList.toggle('active', i < activeCount);
        }
    }

    function clearBarSegments() {
        const container = document.getElementById('wflBarContainer');
        if (!container) return;
        for (let i = 0; i < container.children.length; i++) {
            container.children[i].classList.remove('active');
        }
    }

    // ========================================================================
    // RSSI Chart
    // ========================================================================

    function drawRssiChart() {
        if (!chartCtx || !chartCanvas) return;

        const w = chartCanvas.width = chartCanvas.parentElement.clientWidth - 16;
        const h = chartCanvas.height = chartCanvas.parentElement.clientHeight - 24;
        chartCtx.clearRect(0, 0, w, h);

        if (rssiHistory.length < 2) return;

        const minR = -100, maxR = -20;
        const range = maxR - minR;

        // Grid lines
        chartCtx.strokeStyle = 'rgba(255,255,255,0.05)';
        chartCtx.lineWidth = 1;
        [-30, -50, -70, -90].forEach(v => {
            const y = h - ((v - minR) / range) * h;
            chartCtx.beginPath();
            chartCtx.moveTo(0, y);
            chartCtx.lineTo(w, y);
            chartCtx.stroke();
        });

        // Draw RSSI line
        const step = w / (MAX_RSSI_POINTS - 1);
        chartCtx.beginPath();
        chartCtx.strokeStyle = '#00ff88';
        chartCtx.lineWidth = 2;

        rssiHistory.forEach((rssi, i) => {
            const x = i * step;
            const y = h - ((rssi - minR) / range) * h;
            if (i === 0) chartCtx.moveTo(x, y);
            else chartCtx.lineTo(x, y);
        });
        chartCtx.stroke();

        // Fill under
        const lastIdx = rssiHistory.length - 1;
        chartCtx.lineTo(lastIdx * step, h);
        chartCtx.lineTo(0, h);
        chartCtx.closePath();
        chartCtx.fillStyle = 'rgba(0,255,136,0.08)';
        chartCtx.fill();
    }

    // ========================================================================
    // Audio Proximity
    // ========================================================================

    function playTone(freq, duration) {
        if (!audioCtx || audioCtx.state !== 'running') return;
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.frequency.value = freq;
        osc.type = 'sine';
        gain.gain.value = 0.2;
        gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
        osc.start();
        osc.stop(audioCtx.currentTime + duration);
    }

    function playProximityTone(rssi) {
        if (!audioCtx || audioCtx.state !== 'running') return;
        const strength = Math.max(0, Math.min(1, (rssi + 100) / 70));
        const freq = 400 + strength * 800;
        const duration = 0.06 + (1 - strength) * 0.12;
        playTone(freq, duration);
    }

    function scheduleBeeps(rssi) {
        clearBeepTimer();
        playProximityTone(rssi);
        // Repeat interval: stronger signal = faster beeps
        const strength = Math.max(0, Math.min(1, (rssi + 100) / 70));
        const interval = 1200 - strength * 1000; // 1200ms (weak) to 200ms (strong)
        beepTimer = setInterval(() => {
            if (audioEnabled && locateActive) {
                playProximityTone(rssi);
            } else {
                clearBeepTimer();
            }
        }, interval);
    }

    function clearBeepTimer() {
        if (beepTimer) {
            clearInterval(beepTimer);
            beepTimer = null;
        }
    }

    function toggleAudio() {
        const cb = document.getElementById('wflAudioEnable');
        audioEnabled = cb?.checked || false;
        if (audioEnabled) {
            if (!audioCtx) {
                try {
                    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                } catch (e) {
                    console.error('[WiFiLocate] AudioContext creation failed:', e);
                    return;
                }
            }
            audioCtx.resume().then(() => {
                playTone(600, 0.08);
            });
        } else {
            stopAudio();
        }
    }

    function stopAudio() {
        audioEnabled = false;
        clearBeepTimer();
        const cb = document.getElementById('wflAudioEnable');
        if (cb) cb.checked = false;
    }

    // ========================================================================
    // Signal Lost Timer
    // ========================================================================

    function resetSignalLostTimer() {
        clearSignalLostTimer();
        signalLostTimer = setTimeout(() => {
            if (locateActive) showSignalLost();
        }, SIGNAL_LOST_TIMEOUT_MS);
    }

    function clearSignalLostTimer() {
        if (signalLostTimer) {
            clearTimeout(signalLostTimer);
            signalLostTimer = null;
        }
    }

    function showSignalLost() {
        const el = document.getElementById('wflSignalLost');
        if (el) el.style.display = '';
        clearBeepTimer();
    }

    function hideSignalLost() {
        const el = document.getElementById('wflSignalLost');
        if (el) el.style.display = 'none';
    }

    // ========================================================================
    // Environment
    // ========================================================================

    function setEnvironment(env) {
        currentEnvironment = env;
        document.querySelectorAll('.wfl-env-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.env === env);
        });
        // Recalc distance with last known RSSI
        if (rssiHistory.length > 0) {
            const lastRssi = rssiHistory[rssiHistory.length - 1];
            updateDistanceDisplay(estimateDistance(lastRssi));
        }
    }

    // ========================================================================
    // Handoff from WiFi mode
    // ========================================================================

    function handoff(info) {
        handoffData = info;
        const bssidInput = document.getElementById('wflBssid');
        if (bssidInput) bssidInput.value = info.bssid || '';
        targetSsid = info.ssid || null;

        const card = document.getElementById('wflHandoffCard');
        const nameEl = document.getElementById('wflHandoffName');
        const metaEl = document.getElementById('wflHandoffMeta');
        if (card) card.style.display = '';
        if (nameEl) nameEl.textContent = info.ssid || 'Hidden Network';
        if (metaEl) metaEl.textContent = info.bssid || '';

        // Switch to WiFi Locate mode, unless already there: a switch from
        // wifi_locate to itself is not a Wi-Fi transition, and stops the scan
        // this mode reads from. Resolves when the switch is done.
        const alreadyHere = typeof currentMode !== 'undefined' && currentMode === 'wifi_locate';
        if (!alreadyHere && typeof switchMode === 'function') {
            return switchMode('wifi_locate');
        }
        return Promise.resolve();
    }

    function clearHandoff() {
        handoffData = null;
        const card = document.getElementById('wflHandoffCard');
        if (card) card.style.display = 'none';
    }

    // ========================================================================
    // Public API
    // ========================================================================

    return {
        init,
        start,
        stop,
        destroy,
        handoff,
        clearHandoff,
        setEnvironment,
        toggleAudio,
        setActiveMode,
    };
})();
