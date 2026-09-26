/**
 * Pager (POCSAG/FLEX) mode for the main page: the audio scope, start/stop and
 * status of the decoder, the SSE/polling streams, message rendering, shared
 * escape/validation/notification helpers and the Bluetooth-mode controls.
 *
 * Moved out of templates/index.html unchanged (only de-indented); a classic
 * script loaded after the inline block, sharing its top-level names as before.
 */
// Pager mode polling timer for agent mode
let pagerPollTimer = null;

// --- Pager Signal Scope ---
let pagerScopeCtx = null;
let pagerScopeAnim = null;
let pagerScopeHistory = [];
let pagerScopeWaveBuffer = [];
let pagerScopeDisplayWave = [];
const SCOPE_HISTORY_LEN = 200;
const SCOPE_WAVE_BUFFER_LEN = 2048;
const SCOPE_WAVE_INPUT_SMOOTH_ALPHA = 0.55;
const SCOPE_WAVE_DISPLAY_SMOOTH_ALPHA = 0.22;
const SCOPE_WAVE_IDLE_DECAY = 0.96;
let pagerScopeRms = 0;
let pagerScopePeak = 0;
let pagerScopeTargetRms = 0;
let pagerScopeTargetPeak = 0;
let pagerScopeMsgBurst = 0;
let pagerScopeLastWaveAt = 0;
let pagerScopeLastInputSample = 0;

function resizePagerScopeCanvas(canvas) {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.floor(rect.width * dpr));
    const height = Math.max(1, Math.floor(rect.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
    }
}

function applyPagerScopeData(scopeData) {
    if (!scopeData || typeof scopeData !== 'object') return;

    pagerScopeTargetRms = Number(scopeData.rms) || 0;
    pagerScopeTargetPeak = Number(scopeData.peak) || 0;

    if (Array.isArray(scopeData.waveform) && scopeData.waveform.length) {
        for (const packedSample of scopeData.waveform) {
            const sample = Number(packedSample);
            if (!Number.isFinite(sample)) continue;
            const normalized = Math.max(-127, Math.min(127, sample)) / 127;
            pagerScopeLastInputSample += (normalized - pagerScopeLastInputSample) * SCOPE_WAVE_INPUT_SMOOTH_ALPHA;
            pagerScopeWaveBuffer.push(pagerScopeLastInputSample);
        }
        if (pagerScopeWaveBuffer.length > SCOPE_WAVE_BUFFER_LEN) {
            pagerScopeWaveBuffer.splice(0, pagerScopeWaveBuffer.length - SCOPE_WAVE_BUFFER_LEN);
        }
        pagerScopeLastWaveAt = performance.now();
    }
}

function initPagerScope() {
    const canvas = document.getElementById('pagerScopeCanvas');
    if (!canvas) return;

    if (pagerScopeAnim) {
        cancelAnimationFrame(pagerScopeAnim);
        pagerScopeAnim = null;
    }

    resizePagerScopeCanvas(canvas);
    pagerScopeCtx = canvas.getContext('2d');
    pagerScopeHistory = new Array(SCOPE_HISTORY_LEN).fill(0);
    pagerScopeWaveBuffer = [];
    pagerScopeDisplayWave = [];
    pagerScopeRms = 0;
    pagerScopePeak = 0;
    pagerScopeTargetRms = 0;
    pagerScopeTargetPeak = 0;
    pagerScopeMsgBurst = 0;
    pagerScopeLastWaveAt = 0;
    pagerScopeLastInputSample = 0;
    drawPagerScope();
}

function drawPagerScope() {
    const ctx = pagerScopeCtx;
    if (!ctx) return;

    resizePagerScopeCanvas(ctx.canvas);
    const W = ctx.canvas.width;
    const H = ctx.canvas.height;
    const midY = H / 2;

    // Phosphor persistence
    ctx.fillStyle = 'rgba(5, 5, 16, 0.26)';
    ctx.fillRect(0, 0, W, H);

    // Smooth towards target values
    pagerScopeRms += (pagerScopeTargetRms - pagerScopeRms) * 0.25;
    pagerScopePeak += (pagerScopeTargetPeak - pagerScopePeak) * 0.15;

    // Keep a slow amplitude envelope for readability
    pagerScopeHistory.push(Math.min(pagerScopeRms / 32768, 1.0));
    if (pagerScopeHistory.length > SCOPE_HISTORY_LEN) {
        pagerScopeHistory.shift();
    }

    // Grid lines (horizontal + vertical)
    ctx.strokeStyle = 'rgba(40, 40, 80, 0.4)';
    ctx.lineWidth = 0.8;
    for (let i = 1; i < 8; i++) {
        const gx = (W / 8) * i;
        ctx.beginPath();
        ctx.moveTo(gx, 0);
        ctx.lineTo(gx, H);
        ctx.stroke();
    }
    for (let g = 0.25; g < 1; g += 0.25) {
        const gy = midY - g * midY;
        const gy2 = midY + g * midY;
        ctx.beginPath();
        ctx.moveTo(0, gy); ctx.lineTo(W, gy);
        ctx.moveTo(0, gy2); ctx.lineTo(W, gy2);
        ctx.stroke();
    }

    // Center baseline
    ctx.strokeStyle = 'rgba(60, 60, 100, 0.5)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, midY);
    ctx.lineTo(W, midY);
    ctx.stroke();

    // Slow envelope as context around baseline
    const envStepX = W / (SCOPE_HISTORY_LEN - 1);
    ctx.strokeStyle = 'rgba(90, 180, 255, 0.45)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < pagerScopeHistory.length; i++) {
        const x = i * envStepX;
        const amp = pagerScopeHistory[i] * midY * 0.85;
        const y = midY - amp;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.beginPath();
    for (let i = 0; i < pagerScopeHistory.length; i++) {
        const x = i * envStepX;
        const amp = pagerScopeHistory[i] * midY * 0.85;
        const y = midY + amp;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Actual waveform from real incoming audio samples
    const waveformPointCount = Math.min(Math.max(120, Math.floor(W / 3.2)), 420);
    if (pagerScopeWaveBuffer.length > 1) {
        const waveIsFresh = (performance.now() - pagerScopeLastWaveAt) < 700;
        const sourceLen = pagerScopeWaveBuffer.length;
        const sourceWindow = Math.min(sourceLen, 1536);
        const sourceStart = sourceLen - sourceWindow;

        if (pagerScopeDisplayWave.length !== waveformPointCount) {
            pagerScopeDisplayWave = new Array(waveformPointCount).fill(0);
        }

        for (let i = 0; i < waveformPointCount; i++) {
            const a = sourceStart + Math.floor((i / waveformPointCount) * sourceWindow);
            const b = sourceStart + Math.floor(((i + 1) / waveformPointCount) * sourceWindow);
            const start = Math.max(sourceStart, Math.min(sourceLen - 1, a));
            const end = Math.max(start + 1, Math.min(sourceLen, b));

            let sum = 0;
            let count = 0;
            for (let j = start; j < end; j++) {
                sum += pagerScopeWaveBuffer[j];
                count++;
            }
            const targetSample = count > 0 ? (sum / count) : 0;
            pagerScopeDisplayWave[i] += (targetSample - pagerScopeDisplayWave[i]) * SCOPE_WAVE_DISPLAY_SMOOTH_ALPHA;
        }

        ctx.strokeStyle = waveIsFresh ? '#2efbff' : 'rgba(46, 251, 255, 0.45)';
        ctx.lineWidth = 1.7;
        ctx.shadowColor = '#2efbff';
        ctx.shadowBlur = waveIsFresh ? 6 : 2;

        const stepX = waveformPointCount > 1 ? (W / (waveformPointCount - 1)) : W;
        ctx.beginPath();
        const firstY = midY - (pagerScopeDisplayWave[0] * midY * 0.9);
        ctx.moveTo(0, firstY);
        for (let i = 1; i < waveformPointCount - 1; i++) {
            const x = i * stepX;
            const y = midY - (pagerScopeDisplayWave[i] * midY * 0.9);
            const nx = (i + 1) * stepX;
            const ny = midY - (pagerScopeDisplayWave[i + 1] * midY * 0.9);
            const cx = (x + nx) / 2;
            const cy = (y + ny) / 2;
            ctx.quadraticCurveTo(x, y, cx, cy);
        }
        const lastX = (waveformPointCount - 1) * stepX;
        const lastY = midY - (pagerScopeDisplayWave[waveformPointCount - 1] * midY * 0.9);
        ctx.lineTo(lastX, lastY);
        ctx.stroke();

        if (!waveIsFresh) {
            for (let i = 0; i < pagerScopeDisplayWave.length; i++) {
                pagerScopeDisplayWave[i] *= SCOPE_WAVE_IDLE_DECAY;
            }
        }
    }
    ctx.shadowBlur = 0;

    // Peak indicator (dashed red line)
    const peakNorm = Math.min(pagerScopePeak / 32768, 1.0);
    if (peakNorm > 0.01) {
        const peakY = midY - peakNorm * midY * 0.9;
        ctx.strokeStyle = 'rgba(255, 68, 68, 0.6)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(0, peakY);
        ctx.lineTo(W, peakY);
        ctx.stroke();
        ctx.setLineDash([]);
    }

    // Message decode flash (green overlay)
    if (pagerScopeMsgBurst > 0.01) {
        ctx.fillStyle = `rgba(0, 255, 100, ${pagerScopeMsgBurst * 0.15})`;
        ctx.fillRect(0, 0, W, H);
        pagerScopeMsgBurst *= 0.88;
    }

    // Update labels
    const rmsLabel = document.getElementById('scopeRmsLabel');
    const peakLabel = document.getElementById('scopePeakLabel');
    const statusLabel = document.getElementById('scopeStatusLabel');
    if (rmsLabel) rmsLabel.textContent = Math.round(pagerScopeRms);
    if (peakLabel) peakLabel.textContent = Math.round(pagerScopePeak);
    if (statusLabel) {
        const waveIsFresh = (performance.now() - pagerScopeLastWaveAt) < 700;
        if (pagerScopeRms > 1300 && waveIsFresh) {
            statusLabel.textContent = 'DEMODULATING';
            statusLabel.style.color = '#00ff88';
        } else if (pagerScopeRms > 500) {
            statusLabel.textContent = 'CARRIER';
            statusLabel.style.color = '#2efbff';
        } else {
            statusLabel.textContent = 'QUIET';
            statusLabel.style.color = 'var(--text-dim)';
        }
    }

    pagerScopeAnim = requestAnimationFrame(drawPagerScope);
}

function stopPagerScope() {
    if (pagerScopeAnim) {
        cancelAnimationFrame(pagerScopeAnim);
        pagerScopeAnim = null;
    }
    pagerScopeCtx = null;
    pagerScopeWaveBuffer = [];
    pagerScopeDisplayWave = [];
    pagerScopeHistory = [];
    pagerScopeLastWaveAt = 0;
    pagerScopeLastInputSample = 0;
}

async function startDecoding() {
    const freq = document.getElementById('frequency').value;
    const gain = document.getElementById('gain').value;
    const squelch = document.getElementById('squelch').value;
    const ppm = document.getElementById('ppm').value;
    const device = getSelectedDevice();
    const protocols = getSelectedProtocols();

    if (protocols.length === 0) {
        alert('Please select at least one protocol');
        return;
    }

    // Check if using agent mode
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';

    // Check if device is available (only for local mode)
    if (!isAgentMode && !await checkDeviceAvailability('pager')) {
        return;
    }

    // Check for remote SDR (only for local mode)
    const remoteConfig = isAgentMode ? null : getRemoteSDRConfig();
    if (remoteConfig === false) return; // Validation failed

    const config = {
        frequency: freq,
        gain: gain,
        squelch: squelch,
        ppm: ppm,
        device: device,
        sdr_type: getSelectedSDRType(),
        protocols: protocols,
        bias_t: getBiasTEnabled()
    };

    // Add rtl_tcp params if using remote SDR (local mode only)
    if (remoteConfig) {
        config.rtl_tcp_host = remoteConfig.host;
        config.rtl_tcp_port = remoteConfig.port;
    }

    // Determine endpoint based on agent mode
    const endpoint = isAgentMode
        ? `/controller/agents/${currentAgent}/pager/start`
        : '/start';

    fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    }).then(r => r.json())
        .then(data => {
            // Handle controller proxy response format (agent response is nested in 'result')
            const scanResult = isAgentMode && data.result ? data.result : data;

            if (scanResult.status === 'started' || scanResult.status === 'success') {
                if (!isAgentMode) {
                    reserveDevice(parseInt(device), 'pager');
                }
                setRunning(true);
                startStream(isAgentMode);

                // Initialize filter bar
                const filterContainer = document.getElementById('filterBarContainer');
                const output = document.getElementById('output');
                if (filterContainer) {
                    // Clear any existing filter bar and create pager filter
                    filterContainer.innerHTML = '';
                    const filterBar = SignalCards.createPagerFilterBar(output);
                    filterContainer.appendChild(filterBar);
                    filterContainer.style.display = 'block';
                }

                // Clear address history for fresh session
                SignalCards.clearAddressHistory('pager');
            } else {
                alert('Error: ' + (scanResult.message || scanResult.error || 'Failed to start pager decoding'));
            }
        })
        .catch(err => {
            console.error('Start error:', err);
            alert('Error starting pager decoding: ' + err.message);
        });
}

function stopDecoding() {
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
    const endpoint = isAgentMode
        ? `/controller/agents/${currentAgent}/pager/stop`
        : '/stop';
    const timeoutMs = isAgentMode ? REMOTE_STOP_TIMEOUT_MS : LOCAL_STOP_TIMEOUT_MS;

    if (!isAgentMode) {
        releaseDevice('pager');
    }
    setRunning(false);
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    if (pagerPollTimer) {
        clearInterval(pagerPollTimer);
        pagerPollTimer = null;
    }

    return postStopRequest(endpoint, timeoutMs).then(async (data) => {
        if (!isAgentMode) {
            await confirmStopped('Pager decoder', '/status', data, () => setRunning(true));
        }
        return data;
    });
}

function killAll() {
    fetch('/killall', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            // Release all devices
            Object.keys(sdrDeviceUsage).forEach(idx => delete sdrDeviceUsage[idx]);
            updateDeviceSelectStatus();

            setRunning(false);
            setSensorRunning(false);
            isScannerRunning = false;
            isAudioPlaying = false;

            if (eventSource) {
                eventSource.close();
                eventSource = null;
            }
            showInfo('All processes stopped' + (data.processes.length ? ` (${data.processes.length} killed)` : ' (none were running)'));
        });
}

function checkStatus() {
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
    const endpoint = isAgentMode
        ? `/controller/agents/${currentAgent}/pager/status`
        : '/status';

    fetch(endpoint)
        .then(r => r.json())
        .then(data => {
            // Handle agent response format (may be nested in 'result')
            const statusData = isAgentMode && data.result ? data.result : data;
            const running = statusData.running;

            if (running !== isRunning) {
                setRunning(running);
                if (running && !eventSource) {
                    startStream(isAgentMode);
                }
            }
        })
        .catch(() => {
            // Silently ignore - server may be restarting or network issue
        });
}

// Periodic status check every 5 seconds
VisibleInterval.set(checkStatus, 5000);

function toggleLogging() {
    const enabled = document.getElementById('loggingEnabled').checked;
    const logFile = document.getElementById('logFilePath').value;
    fetch('/logging', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: enabled, log_file: logFile })
    }).then(r => r.json())
        .then(data => {
            showInfo(data.logging ? 'Logging enabled: ' + data.log_file : 'Logging disabled');
        });
}

function setRunning(running) {
    isRunning = running;
    document.getElementById('statusDot').classList.toggle('running', running);
    document.getElementById('statusText').textContent = running ? 'Decoding...' : 'Idle';
    document.getElementById('startBtn').style.display = running ? 'none' : 'block';
    document.getElementById('stopBtn').style.display = running ? 'block' : 'none';

    // Signal scope
    const scopePanel = document.getElementById('pagerScopePanel');
    if (scopePanel) {
        if (running) {
            scopePanel.style.display = 'block';
            initPagerScope();
        } else {
            stopPagerScope();
            scopePanel.style.display = 'none';
        }
    }
}

function startStream(isAgentMode = false) {
    if (eventSource) {
        eventSource.close();
    }

    // Use different stream endpoint for agent mode
    const streamUrl = isAgentMode ? '/controller/stream/all' : '/stream';
    eventSource = new EventSource(streamUrl);

    eventSource.onopen = function () {
        showInfo('Stream connected...');
    };

    eventSource.onmessage = function (e) {
        const data = JSON.parse(e.data);

        // Handle multi-agent stream format
        if (isAgentMode) {
            // Multi-agent stream tags data with scan_type and agent_name
            if (data.scan_type === 'pager' && data.payload) {
                const payload = data.payload;
                if (payload.type === 'message') {
                    // Add agent info to the message
                    payload.agent_name = data.agent_name;
                    addMessage(payload);
                } else if (payload.type === 'status') {
                    if (payload.text === 'stopped') {
                        setRunning(false);
                    } else if (payload.text === 'started') {
                        showInfo(`Decoder started on ${data.agent_name}, waiting for signals...`);
                    }
                } else if (payload.type === 'info') {
                    showInfo(`[${data.agent_name}] ${payload.text}`);
                } else if (payload.type === 'scope') {
                    applyPagerScopeData(payload);
                }
            } else if (data.type === 'keepalive') {
                // Ignore keepalive messages
            }
        } else {
            // Local stream format
            if (data.type === 'message') {
                addMessage(data);
            } else if (data.type === 'status') {
                if (data.text === 'stopped') {
                    setRunning(false);
                } else if (data.text === 'started') {
                    showInfo('Decoder started, waiting for signals...');
                }
            } else if (data.type === 'info') {
                showInfo(data.text);
            } else if (data.type === 'raw') {
                showInfo(data.text);
            } else if (data.type === 'scope') {
                applyPagerScopeData(data);
            }
        }
    };

    eventSource.onerror = function (e) {
        checkStatus();
    };

    // Start polling fallback for agent mode (in case push isn't enabled)
    if (isAgentMode) {
        startPagerPolling();
    }
}

// Track last message count to avoid duplicates during polling
let lastPagerMsgCount = 0;

function startPagerPolling() {
    if (pagerPollTimer) return;
    lastPagerMsgCount = 0;

    const pollInterval = 2000; // 2 seconds
    pagerPollTimer = setInterval(async () => {
        if (!isRunning) {
            clearInterval(pagerPollTimer);
            pagerPollTimer = null;
            return;
        }

        try {
            const response = await fetch(`/controller/agents/${currentAgent}/pager/data`);
            if (!response.ok) return;

            const data = await response.json();
            const result = data.result || data;
            const modeData = result.data || result;

            // Process messages from polling response
            if (modeData.messages && Array.isArray(modeData.messages)) {
                const newMsgs = modeData.messages.slice(lastPagerMsgCount);
                newMsgs.forEach(msg => {
                    // Convert to expected format
                    const displayMsg = {
                        type: 'message',
                        protocol: msg.protocol || 'UNKNOWN',
                        address: msg.address || '',
                        function: msg.function || '',
                        msg_type: msg.msg_type || 'Alpha',
                        message: msg.message || '',
                        timestamp: msg.received_at || new Date().toISOString(),
                        agent_name: result.agent_name || 'Remote Agent'
                    };
                    addMessage(displayMsg);
                });
                lastPagerMsgCount = modeData.messages.length;
            }
        } catch (err) {
            console.error('Pager polling error:', err);
        }
    }, pollInterval);
}

function addMessage(msg) {
    const output = document.getElementById('output');

    // Remove placeholder if present
    const placeholder = output.querySelector('.placeholder');
    if (placeholder) {
        placeholder.remove();
    }

    // Store message for export (always, even if filtered)
    allMessages.push(msg);

    // Check if message should be filtered from display
    const isFiltered = shouldFilterMessage(msg);

    // Check if address is muted
    const isMuted = SignalCards.isAddressMuted(msg.address);

    // Update counts (always, even if filtered)
    msgCount++;
    document.getElementById('msgCount').textContent = msgCount;

    let protoClass = '';
    if (msg.protocol.includes('POCSAG')) {
        pocsagCount++;
        protoClass = 'pocsag';
        document.getElementById('pocsagCount').textContent = pocsagCount;
    } else if (msg.protocol.includes('FLEX')) {
        flexCount++;
        protoClass = 'flex';
        document.getElementById('flexCount').textContent = flexCount;
    }

    // If filtered or muted, skip display but update filtered count
    if (isFiltered || isMuted) {
        filteredCount++;
        return;
    }

    // Play audio alert (only for non-filtered messages)
    playAlert();

    // Update signal meter
    pulseSignal();

    // Flash signal scope green on decode
    pagerScopeMsgBurst = 1.0;

    // Use SignalCards component to create the message card (auto-detects status)
    const msgEl = SignalCards.createPagerCard(msg);

    if (typeof PagerDirectory !== 'undefined') PagerDirectory.addMessage(msg);
    output.insertBefore(msgEl, output.firstChild);

    // Add to activity timeline
    if (typeof addTimelineEvent === 'function') {
        addTimelineEvent('pager', {
            id: `${msg.address}-${msg.timestamp}`,
            label: msg.address,
            sublabel: msg.protocol,
            timestamp: msg.timestamp || Date.now(),
            type: 'pager',
            status: msgEl.dataset.status || 'new'
        });
    }

    // Update filter counts
    SignalCards.updateCounts(output);

    // Auto-scroll to top (newest messages)
    if (autoScroll) {
        output.scrollTop = 0;
    }

    // Limit messages displayed (keep placeholder/empty-state)
    const cards = output.querySelectorAll('.signal-card');
    for (let i = cards.length - 1; i >= 100; i--) {
        cards[i].remove();
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escapeAttr(text) {
    // Escape for use in HTML attributes (especially onclick handlers)
    if (text === null || text === undefined) return '';
    var s = String(text);
    s = s.replace(/&/g, '&amp;');
    s = s.replace(/'/g, '&#39;');
    s = s.replace(/"/g, '&quot;');
    s = s.replace(/</g, '&lt;');
    s = s.replace(/>/g, '&gt;');
    return s;
}

function isValidMac(mac) {
    // Validate MAC address format (XX:XX:XX:XX:XX:XX)
    return /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(mac);
}

function isValidChannel(ch) {
    // Validate WiFi channel (1-200 covers all bands)
    const num = parseInt(ch, 10);
    return !isNaN(num) && num >= 1 && num <= 200;
}

function showInfo(text) {
    const output = document.getElementById('output');

    // Clear placeholder only (has the 'placeholder' class)
    const placeholder = output.querySelector('.placeholder');
    if (placeholder) {
        placeholder.remove();
    }

    const infoEl = document.createElement('div');
    infoEl.className = 'info-msg';
    infoEl.style.cssText = 'padding: 12px 15px; margin-bottom: 8px; background: var(--bg-card); border: 1px solid var(--border-color); border-left: 2px solid var(--accent-cyan); font-family: "Roboto Condensed", "Arial Narrow", sans-serif; font-size: 11px; color: var(--text-secondary); word-break: break-all;';
    infoEl.textContent = text;
    output.insertBefore(infoEl, output.firstChild);
}

function showError(text) {
    const output = document.getElementById('output');

    // Clear placeholder only (has the 'placeholder' class)
    const placeholder = output.querySelector('.placeholder');
    if (placeholder) {
        placeholder.remove();
    }

    const errorEl = document.createElement('div');
    errorEl.className = 'error-msg';
    errorEl.style.cssText = 'padding: 12px 15px; margin-bottom: 8px; background: #1a0a0a; border: 1px solid #2a1a1a; border-left: 2px solid #ff3366; font-family: "Roboto Condensed", "Arial Narrow", sans-serif; font-size: 11px; color: #ff6688; word-break: break-all;';
    errorEl.textContent = '⚠ ' + text;
    output.insertBefore(errorEl, output.firstChild);
}

function clearMessages() {
    if (currentMode === 'ook') { OokMode.clearOutput(); return; }
    document.getElementById('output').innerHTML = `
        <div class="placeholder" style="color: var(--text-secondary); text-align: center; padding: 50px;">
            Messages cleared. ${isRunning || isSensorRunning ? 'Waiting for new messages...' : 'Start decoding to receive messages.'}
        </div>
    `;
    if (typeof PagerDirectory  !== 'undefined') PagerDirectory.reset();
    if (typeof SensorDashboard !== 'undefined') SensorDashboard.reset();
    msgCount = 0;
    pocsagCount = 0;
    flexCount = 0;
    sensorCount = 0;
    filteredCount = 0;
    allMessages = [];
    uniqueDevices.clear();
    document.getElementById('msgCount').textContent = '0';
    document.getElementById('pocsagCount').textContent = '0';
    document.getElementById('flexCount').textContent = '0';
    document.getElementById('sensorCount').textContent = '0';
    document.getElementById('deviceCount').textContent = '0';

    // Clear meter aggregator data
    if (typeof MeterAggregator !== 'undefined') {
        MeterAggregator.clear();
    }

    // Reset recon data
    deviceDatabase.clear();
    newDeviceAlerts = 0;
    anomalyAlerts = 0;
    document.getElementById('trackedCount').textContent = '0';
    document.getElementById('newDeviceCount').textContent = '0';
    document.getElementById('anomalyCount').textContent = '0';
    document.getElementById('reconContent').innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 30px; font-size: 11px;">Device intelligence data will appear here as signals are intercepted.</div>';
}



// ============== BLUETOOTH COMPATIBILITY SHIMS ==============

function getBluetoothModeApi() {
    if (typeof BluetoothMode === 'undefined') return null;
    return BluetoothMode;
}

function syncBtRunningState() {
    const bt = getBluetoothModeApi();
    if (!bt || typeof bt.isScanning !== 'function') {
        return isBtRunning;
    }
    isBtRunning = bt.isScanning();
    return isBtRunning;
}

function refreshBtInterfaces() {
    const bt = getBluetoothModeApi();
    if (!bt) return;
    if (typeof bt.checkCapabilities === 'function') bt.checkCapabilities();
    syncBtRunningState();
}

function startBtScan() {
    const bt = getBluetoothModeApi();
    if (!bt || typeof bt.startScan !== 'function') return;
    bt.startScan();
    setTimeout(syncBtRunningState, 0);
}

function stopBtScan() {
    const bt = getBluetoothModeApi();
    let stopPromise = Promise.resolve();
    if (bt && typeof bt.stopScan === 'function') {
        stopPromise = Promise.resolve(bt.stopScan()).catch((err) => {
            console.warn('[BT] stop failed:', err);
        });
    }
    setTimeout(syncBtRunningState, 0);
    return stopPromise;
}

function setBtRunning(running) {
    isBtRunning = !!running;
    syncBtRunningState();
}

function initBtRadar() {
    // Radar lifecycle is handled by BluetoothMode.
    syncBtRunningState();
}

function resetBtAdapter() {
    // Legacy hook retained for old callers.
    if (typeof showInfo === 'function') {
        showInfo('Bluetooth adapter reset is handled by the Bluetooth mode backend.');
    } else {
        console.info('Bluetooth adapter reset is handled by the Bluetooth mode backend.');
    }
}
