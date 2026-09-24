/**
 * SubGHz Transceiver Mode
 * HackRF One SubGHz signal capture, decode, replay, and spectrum analysis
 */

const SubGhz = (function() {
    let eventSource = null;
    let statusTimer = null;
    let statusPollTimer = null;
    let rxStartTime = null;
    let sweepCanvas = null;
    let sweepCtx = null;
    let sweepData = [];
    let pendingTxCaptureId = null;
    let pendingTxCaptureMeta = null;
    let pendingTxBursts = [];
    let txTimelineDragState = null;
    let rxScopeCanvas = null;
    let rxScopeCtx = null;
    let rxScopeData = [];
    let rxScopeResizeObserver = null;
    let rxWaterfallCanvas = null;
    let rxWaterfallCtx = null;
    let rxWaterfallPalette = null;
    let rxWaterfallResizeObserver = null;
    let rxWaterfallPaused = false;
    let rxWaterfallFloor = 20;
    let rxWaterfallRange = 180;
    let decodeScopeCanvas = null;
    let decodeScopeCtx = null;
    let decodeScopeData = [];
    let decodeScopeResizeObserver = null;
    let decodeWaterfallCanvas = null;
    let decodeWaterfallCtx = null;
    let decodeWaterfallPalette = null;
    let decodeWaterfallResizeObserver = null;

    // Dashboard state
    let activePanel = null;      // null = hub, 'rx'|'sweep'|'tx'|'saved'
    let signalCount = 0;
    let captureCount = 0;
    let consoleEntries = [];
    let consoleCollapsed = false;
    let currentPhase = null;     // 'tuning'|'listening'|'decoding'|null
    let currentMode = 'idle';    // tracks backend mode for timer/strip
    let lastRawLine = '';
    let lastRawLineTs = 0;
    let lastBurstLineTs = 0;
    let burstBadgeTimer = null;
    let lastRxHintTs = 0;
    let captureSelectMode = false;
    let selectedCaptureIds = new Set();
    let latestCaptures = [];
    let lastTxCaptureId = null;
    let lastTxRequest = null;
    let txModalIntent = 'tx';

    // HackRF detection
    let hackrfDetected = false;
    let rtl433Detected = false;
    let sweepDetected = false;

    // Interactive sweep state
    const SWEEP_PAD = { top: 20, right: 20, bottom: 30, left: 50 };
    const SWEEP_POWER_MIN = -100;
    const SWEEP_POWER_MAX = 0;

    let sweepHoverFreq = null;
    let sweepHoverPower = null;
    let sweepSelectedFreq = null;
    let sweepPeaks = [];
    let sweepPeakHold = [];
    let sweepInteractionBound = false;
    let sweepResizeObserver = null;
    let sweepTooltipEl = null;
    let sweepCtxMenuEl = null;
    let sweepActionBarEl = null;
    let sweepDismissHandler = null;

    /**
     * Initialize the SubGHz mode
     */
    function init() {
        loadCaptures();
        startStream();
        startStatusPolling();
        syncTriggerControls();

        // Check HackRF availability and restore panel state
        fetch('/subghz/status')
            .then(r => r.json())
            .then(data => {
                updateDeviceStatus(data);
                updateStatusUI(data);

                const mode = data.mode || 'idle';
                if (mode === 'decode') {
                    // Legacy decode mode may still be running via API, but this UI
                    // intentionally focuses on RAW capture/replay/sweep.
                    showHub();
                    showConsole();
                    startStatusTimer();
                    addConsoleEntry('Decode mode is disabled in this UI layout.', 'warn');
                } else if (mode === 'rx') {
                    showPanel('rx');
                    updateRxDisplay(getParams());
                    initRxScope();
                    initRxWaterfall();
                    syncWaterfallControls();
                    showConsole();
                    startStatusTimer();
                } else if (mode === 'sweep') {
                    showPanel('sweep');
                    initSweepCanvas();
                    showConsole();
                } else if (mode === 'tx') {
                    showPanel('tx');
                    showConsole();
                    startStatusTimer();
                } else {
                    showHub();
                }
            })
            .catch(() => showHub());
    }

    function syncTriggerControls() {
        const enabled = !!document.getElementById('subghzTriggerEnabled')?.checked;
        const preEl = document.getElementById('subghzTriggerPreMs');
        const postEl = document.getElementById('subghzTriggerPostMs');
        if (preEl) preEl.disabled = !enabled;
        if (postEl) postEl.disabled = !enabled;
    }

    function startStatusPolling() {
        if (statusPollTimer) VisibleInterval.clear(statusPollTimer);
        const refresh = () => {
            fetch('/subghz/status')
                .then(r => r.json())
                .then(data => {
                    updateDeviceStatus(data);
                    updateStatusUI(data);
                })
                .catch(() => {});
        };
        refresh();
        statusPollTimer = VisibleInterval.set(refresh, 3000);
    }

    // ------ DEVICE DETECTION ------

    function updateDeviceStatus(data) {
        const hackrfAvailable = !!data.hackrf_available;
        const hackrfInfoAvailable = data.hackrf_info_available !== false;
        const hackrfDetectionPaused = data.hackrf_detection_paused === true;
        const hackrfConnectedRaw = data.hackrf_connected;
        const hackrfConnected = hackrfConnectedRaw === true;
        const hackrfKnownDisconnected = hackrfConnectedRaw === false;
        const hackrfDetectUnknown = hackrfAvailable && !hackrfConnected && !hackrfKnownDisconnected;
        hackrfDetected = hackrfConnected;
        rtl433Detected = !!data.rtl433_available;
        sweepDetected = !!data.sweep_available;

        // Sidebar device indicator
        const dot = document.getElementById('subghzDeviceDot');
        const label = document.getElementById('subghzDeviceLabel');
        if (dot) {
            dot.className = 'subghz-device-dot';
            if (hackrfDetectUnknown) {
                dot.classList.add('unknown');
            } else {
                dot.classList.add(hackrfConnected ? 'connected' : 'disconnected');
            }
        }
        if (label) {
            if (hackrfConnected) {
                label.textContent = 'HackRF Connected';
            } else if (!hackrfAvailable) {
                label.textContent = 'HackRF Tools Missing';
            } else if (hackrfDetectUnknown && hackrfDetectionPaused) {
                label.textContent = 'HackRF Status Paused (active stream)';
            } else if (hackrfDetectUnknown && !hackrfInfoAvailable) {
                label.textContent = 'HackRF Detection Unavailable';
            } else if (hackrfDetectUnknown) {
                label.textContent = 'HackRF Status Unknown';
            } else {
                label.textContent = 'HackRF Not Detected';
            }
            label.classList.toggle('error', !hackrfConnected && hackrfKnownDisconnected);
        }

        // Tool badges
        setToolBadge('subghzToolHackrf', hackrfAvailable);
        setToolBadge('subghzToolSweep', sweepDetected);

        // Stats strip device badge
        const stripDot = document.getElementById('subghzStripDeviceDot');
        if (stripDot) {
            stripDot.className = 'subghz-strip-device-dot';
            if (hackrfDetectUnknown) {
                stripDot.classList.add('unknown');
            } else {
                stripDot.classList.add(hackrfConnected ? 'connected' : 'disconnected');
            }
        }
    }

    function setToolBadge(id, available) {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.toggle('available', available);
        el.classList.toggle('missing', !available);
    }

    /**
     * Set frequency from preset button
     */
    function setFreq(mhz) {
        const el = document.getElementById('subghzFrequency');
        if (el) el.value = mhz;
    }

    /**
     * Switch between RAW receive / sweep sidebar tabs.
     * Only toggles sidebar tab content visibility — does NOT open visuals panels.
     */
    function switchTab(tab) {
        document.querySelectorAll('.subghz-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.tab === tab);
        });
        const tabRx = document.getElementById('subghzTabRx');
        const tabSweep = document.getElementById('subghzTabSweep');
        if (tabRx) tabRx.classList.toggle('active', tab === 'rx');
        if (tabSweep) tabSweep.classList.toggle('active', tab === 'sweep');
    }

    /**
     * Get common parameters from inputs
     */
    function getParams() {
        const freqMhz = parseFloat(document.getElementById('subghzFrequency')?.value || '433.92');
        const serial = (document.getElementById('subghzDeviceSerial')?.value || '').trim();
        const params = {
            frequency_hz: Math.round(freqMhz * 1000000),
            lna_gain: parseInt(document.getElementById('subghzLnaGain')?.value || '24'),
            vga_gain: parseInt(document.getElementById('subghzVgaGain')?.value || '20'),
            sample_rate: parseInt(document.getElementById('subghzSampleRate')?.value || '2000000'),
        };
        const triggerEnabled = !!document.getElementById('subghzTriggerEnabled')?.checked;
        params.trigger_enabled = triggerEnabled;
        if (triggerEnabled) {
            params.trigger_pre_ms = parseInt(document.getElementById('subghzTriggerPreMs')?.value || '350');
            params.trigger_post_ms = parseInt(document.getElementById('subghzTriggerPostMs')?.value || '700');
        }
        if (serial) params.device_serial = serial;
        return params;
    }

    // ------ COORDINATE HELPERS ------

    function sweepPixelToFreqPower(canvasX, canvasY) {
        if (!sweepCanvas || sweepData.length < 2) return { freq: 0, power: 0, inChart: false };
        const w = sweepCanvas.width;
        const h = sweepCanvas.height;
        const chartW = w - SWEEP_PAD.left - SWEEP_PAD.right;
        const chartH = h - SWEEP_PAD.top - SWEEP_PAD.bottom;
        const inChart = canvasX >= SWEEP_PAD.left && canvasX <= w - SWEEP_PAD.right &&
                        canvasY >= SWEEP_PAD.top && canvasY <= h - SWEEP_PAD.bottom;
        const ratio = Math.max(0, Math.min(1, (canvasX - SWEEP_PAD.left) / chartW));
        const freqMin = sweepData[0].freq;
        const freqMax = sweepData[sweepData.length - 1].freq;
        const freq = freqMin + ratio * (freqMax - freqMin);
        const powerRatio = Math.max(0, Math.min(1, (h - SWEEP_PAD.bottom - canvasY) / chartH));
        const power = SWEEP_POWER_MIN + powerRatio * (SWEEP_POWER_MAX - SWEEP_POWER_MIN);
        return { freq, power, inChart };
    }

    function sweepFreqToPixelX(freqMhz) {
        if (!sweepCanvas || sweepData.length < 2) return 0;
        const chartW = sweepCanvas.width - SWEEP_PAD.left - SWEEP_PAD.right;
        const freqMin = sweepData[0].freq;
        const freqMax = sweepData[sweepData.length - 1].freq;
        const ratio = (freqMhz - freqMin) / (freqMax - freqMin);
        return SWEEP_PAD.left + ratio * chartW;
    }

    function interpolatePower(freqMhz) {
        if (sweepData.length === 0) return SWEEP_POWER_MIN;
        if (sweepData.length === 1) return sweepData[0].power;
        let lo = 0, hi = sweepData.length - 1;
        if (freqMhz <= sweepData[lo].freq) return sweepData[lo].power;
        if (freqMhz >= sweepData[hi].freq) return sweepData[hi].power;
        while (hi - lo > 1) {
            const mid = (lo + hi) >> 1;
            if (sweepData[mid].freq <= freqMhz) lo = mid;
            else hi = mid;
        }
        const t = (freqMhz - sweepData[lo].freq) / (sweepData[hi].freq - sweepData[lo].freq);
        return sweepData[lo].power + t * (sweepData[hi].power - sweepData[lo].power);
    }

    // ------ RX SCOPE ------

    function initRxScope() {
        rxScopeCanvas = document.getElementById('subghzRxScope');
        if (!rxScopeCanvas) return;
        rxScopeCtx = rxScopeCanvas.getContext('2d');
        resizeRxScope();

        if (!rxScopeResizeObserver && rxScopeCanvas.parentElement) {
            rxScopeResizeObserver = new ResizeObserver(() => {
                resizeRxScope();
                drawRxScope();
            });
            rxScopeResizeObserver.observe(rxScopeCanvas.parentElement);
        }

        drawRxScope();
    }

    function initDecodeScope() {
        decodeScopeCanvas = document.getElementById('subghzDecodeScope');
        if (!decodeScopeCanvas) return;
        decodeScopeCtx = decodeScopeCanvas.getContext('2d');
        resizeDecodeScope();

        if (!decodeScopeResizeObserver && decodeScopeCanvas.parentElement) {
            decodeScopeResizeObserver = new ResizeObserver(() => {
                resizeDecodeScope();
                drawDecodeScope();
            });
            decodeScopeResizeObserver.observe(decodeScopeCanvas.parentElement);
        }

        drawDecodeScope();
    }

    function initRxWaterfall() {
        rxWaterfallCanvas = document.getElementById('subghzRxWaterfall');
        if (!rxWaterfallCanvas) return;
        rxWaterfallCtx = rxWaterfallCanvas.getContext('2d');
        rxWaterfallPalette = rxWaterfallPalette || buildWaterfallPalette();
        resizeRxWaterfall();
        clearWaterfall(rxWaterfallCtx, rxWaterfallCanvas);
        syncWaterfallControls();

        if (!rxWaterfallResizeObserver && rxWaterfallCanvas.parentElement) {
            rxWaterfallResizeObserver = new ResizeObserver(() => {
                resizeRxWaterfall();
                clearWaterfall(rxWaterfallCtx, rxWaterfallCanvas);
            });
            rxWaterfallResizeObserver.observe(rxWaterfallCanvas.parentElement);
        }
    }

    function initDecodeWaterfall() {
        decodeWaterfallCanvas = document.getElementById('subghzDecodeWaterfall');
        if (!decodeWaterfallCanvas) return;
        decodeWaterfallCtx = decodeWaterfallCanvas.getContext('2d');
        decodeWaterfallPalette = decodeWaterfallPalette || buildWaterfallPalette();
        resizeDecodeWaterfall();
        clearWaterfall(decodeWaterfallCtx, decodeWaterfallCanvas);

        if (!decodeWaterfallResizeObserver && decodeWaterfallCanvas.parentElement) {
            decodeWaterfallResizeObserver = new ResizeObserver(() => {
                resizeDecodeWaterfall();
                clearWaterfall(decodeWaterfallCtx, decodeWaterfallCanvas);
            });
            decodeWaterfallResizeObserver.observe(decodeWaterfallCanvas.parentElement);
        }
    }

    function resizeRxScope() {
        if (!rxScopeCanvas || !rxScopeCanvas.parentElement) return;
        const rect = rxScopeCanvas.parentElement.getBoundingClientRect();
        rxScopeCanvas.width = Math.max(10, rect.width);
        rxScopeCanvas.height = Math.max(10, rect.height);
    }

    function resizeDecodeScope() {
        if (!decodeScopeCanvas || !decodeScopeCanvas.parentElement) return;
        const rect = decodeScopeCanvas.parentElement.getBoundingClientRect();
        decodeScopeCanvas.width = Math.max(10, rect.width);
        decodeScopeCanvas.height = Math.max(10, rect.height);
    }

    function resizeRxWaterfall() {
        if (!rxWaterfallCanvas || !rxWaterfallCanvas.parentElement) return;
        const rect = rxWaterfallCanvas.parentElement.getBoundingClientRect();
        rxWaterfallCanvas.width = Math.max(10, rect.width);
        rxWaterfallCanvas.height = Math.max(10, rect.height);
    }

    function resizeDecodeWaterfall() {
        if (!decodeWaterfallCanvas || !decodeWaterfallCanvas.parentElement) return;
        const rect = decodeWaterfallCanvas.parentElement.getBoundingClientRect();
        decodeWaterfallCanvas.width = Math.max(10, rect.width);
        decodeWaterfallCanvas.height = Math.max(10, rect.height);
    }

    function updateRxLevel(level) {
        updateLevel('subghzRxLevel', level);
    }

    function updateDecodeLevel(level) {
        updateLevel('subghzDecodeLevel', level);
    }

    function updateRxWaveform(samples) {
        if (!Array.isArray(samples)) return;
        if (!rxScopeCanvas) initRxScope();
        rxScopeData = samples;
        drawRxScope();
    }

    function updateDecodeWaveform(samples) {
        if (!Array.isArray(samples)) return;
        if (!decodeScopeCanvas) initDecodeScope();
        decodeScopeData = samples;
        drawDecodeScope();
    }

    function updateRxSpectrum(bins) {
        if (!Array.isArray(bins) || !bins.length) return;
        if (rxWaterfallPaused) return;
        if (!rxWaterfallCanvas) initRxWaterfall();
        drawWaterfallRow(rxWaterfallCtx, rxWaterfallCanvas, rxWaterfallPalette, bins, rxWaterfallFloor, rxWaterfallRange);
    }

    function updateDecodeSpectrum(bins) {
        if (!Array.isArray(bins) || !bins.length) return;
        if (!decodeWaterfallCanvas) initDecodeWaterfall();
        drawWaterfallRow(decodeWaterfallCtx, decodeWaterfallCanvas, decodeWaterfallPalette, bins, rxWaterfallFloor, rxWaterfallRange);
    }

    function drawRxScope() {
        drawScope(rxScopeCtx, rxScopeCanvas, rxScopeData);
    }

    function drawDecodeScope() {
        drawScope(decodeScopeCtx, decodeScopeCanvas, decodeScopeData);
    }

    function buildWaterfallPalette() {
        const stops = [
            { v: 0, c: [7, 11, 18] },
            { v: 64, c: [11, 42, 111] },
            { v: 128, c: [0, 212, 255] },
            { v: 192, c: [255, 170, 0] },
            { v: 255, c: [255, 255, 255] },
        ];
        const palette = new Array(256);
        for (let i = 0; i < stops.length - 1; i++) {
            const a = stops[i];
            const b = stops[i + 1];
            const span = b.v - a.v;
            for (let v = a.v; v <= b.v; v++) {
                const t = span === 0 ? 0 : (v - a.v) / span;
                const r = Math.round(a.c[0] + (b.c[0] - a.c[0]) * t);
                const g = Math.round(a.c[1] + (b.c[1] - a.c[1]) * t);
                const bch = Math.round(a.c[2] + (b.c[2] - a.c[2]) * t);
                palette[v] = [r, g, bch];
            }
        }
        return palette;
    }

    function drawScope(ctx, canvas, data) {
        if (!ctx || !canvas) return;
        const w = canvas.width;
        const h = canvas.height;
        ctx.clearRect(0, 0, w, h);
        ctx.fillStyle = '#0d1117';
        ctx.fillRect(0, 0, w, h);

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
        ctx.lineWidth = 1;
        for (let i = 1; i < 4; i++) {
            const y = (h / 4) * i;
            ctx.beginPath();
            ctx.moveTo(0, y);
            ctx.lineTo(w, y);
            ctx.stroke();
        }

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.15)';
        ctx.beginPath();
        ctx.moveTo(0, h / 2);
        ctx.lineTo(w, h / 2);
        ctx.stroke();

        if (!data || !data.length) return;

        let peak = 0;
        for (let i = 0; i < data.length; i++) {
            const abs = Math.abs(Number(data[i]) || 0);
            if (abs > peak) peak = abs;
        }
        // Auto-scale low-amplitude noise/signal so activity is visible.
        const gain = peak > 0 ? Math.min(12, 0.92 / peak) : 1;

        ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00d4ff';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        const n = data.length;
        if (n === 1) {
            const v = Math.max(-1, Math.min(1, (Number(data[0]) || 0) * gain));
            const y = (0.5 - (v / 2)) * h;
            ctx.moveTo(0, y);
            ctx.lineTo(w, y);
            ctx.stroke();
            return;
        }
        for (let i = 0; i < n; i++) {
            const x = (i / (n - 1)) * w;
            const v = Math.max(-1, Math.min(1, (Number(data[i]) || 0) * gain));
            const y = (0.5 - (v / 2)) * h;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();
    }

    function clearWaterfall(ctx, canvas) {
        if (!ctx || !canvas) return;
        ctx.fillStyle = '#0d1117';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
    }

    function drawWaterfallRow(ctx, canvas, palette, bins, floor, range) {
        if (!ctx || !canvas) return;
        const w = canvas.width;
        const h = canvas.height;
        if (h < 2 || w < 2) return;

        // Shift image down by 1px
        ctx.drawImage(canvas, 0, 0, w, h - 1, 0, 1, w, h - 1);

        const row = ctx.createImageData(w, 1);
        const data = row.data;
        const paletteRef = palette || buildWaterfallPalette();
        for (let x = 0; x < w; x++) {
            const idx = Math.floor((x / (w - 1)) * (bins.length - 1));
            const raw = Math.max(0, Math.min(255, bins[idx] || 0));
            const rangeVal = Math.max(16, range || 180);
            const normalized = Math.max(0, Math.min(1, (raw - (floor || 0)) / rangeVal));
            const val = Math.round(normalized * 255);
            const c = paletteRef[val] || [0, 0, 0];
            const offset = x * 4;
            data[offset] = c[0];
            data[offset + 1] = c[1];
            data[offset + 2] = c[2];
            data[offset + 3] = 255;
        }
        ctx.putImageData(row, 0, 0);
    }

    function updateLevel(id, level) {
        const el = document.getElementById(id);
        if (!el) return;
        const clamped = Math.max(0, Math.min(100, Number(level) || 0));
        // Boost low-level values so weak-but-real activity is visible.
        const boosted = clamped <= 0 ? 0 : Math.min(100, Math.round(Math.sqrt(clamped / 100) * 100));
        el.style.width = boosted + '%';
    }

    function syncWaterfallControls() {
        const floorEl = document.getElementById('subghzWfFloor');
        const rangeEl = document.getElementById('subghzWfRange');
        const floorVal = document.getElementById('subghzWfFloorVal');
        const rangeVal = document.getElementById('subghzWfRangeVal');
        const pauseBtn = document.getElementById('subghzWfPauseBtn');

        if (floorEl) floorEl.value = rxWaterfallFloor;
        if (rangeEl) rangeEl.value = rxWaterfallRange;
        if (floorVal) floorVal.textContent = String(rxWaterfallFloor);
        if (rangeVal) rangeVal.textContent = String(rxWaterfallRange);
        if (pauseBtn) {
            pauseBtn.textContent = rxWaterfallPaused ? 'RESUME' : 'PAUSE';
            pauseBtn.classList.toggle('paused', rxWaterfallPaused);
        }
    }

    function setWaterfallFloor(value) {
        const next = Math.max(0, Math.min(200, parseInt(value, 10) || 0));
        rxWaterfallFloor = next;
        syncWaterfallControls();
    }

    function setWaterfallRange(value) {
        const next = Math.max(16, Math.min(255, parseInt(value, 10) || 180));
        rxWaterfallRange = next;
        syncWaterfallControls();
    }

    function toggleWaterfall() {
        rxWaterfallPaused = !rxWaterfallPaused;
        syncWaterfallControls();
    }

    function updateRxStats(stats) {
        const sizeEl = document.getElementById('subghzRxFileSize');
        const rateEl = document.getElementById('subghzRxRate');
        if (sizeEl) sizeEl.textContent = formatBytes(stats.file_size || 0);
        if (rateEl) rateEl.textContent = (stats.rate_kb ? stats.rate_kb.toFixed(1) : '0') + ' KB/s';
    }

    function resetRxVisuals() {
        rxScopeData = [];
        updateRxLevel(0);
        drawRxScope();
        clearWaterfall(rxWaterfallCtx, rxWaterfallCanvas);
        updateRxStats({ file_size: 0, rate_kb: 0 });
        updateRxHint('', 0, '');
    }

    function resetDecodeVisuals() {
        decodeScopeData = [];
        updateDecodeLevel(0);
        drawDecodeScope();
        clearWaterfall(decodeWaterfallCtx, decodeWaterfallCanvas);
    }

    // ------ STATUS ------

    function updateStatusUI(data) {
        const dot = document.getElementById('subghzStatusDot');
        const text = document.getElementById('subghzStatusText');
        const timer = document.getElementById('subghzStatusTimer');
        const mode = data.mode || 'idle';
        currentMode = mode;

        if (dot) {
            dot.className = 'subghz-status-dot';
            if (mode !== 'idle') dot.classList.add(mode);
        }

        const labels = { idle: 'Idle', rx: 'Capturing', decode: 'Decoding', tx: 'Transmitting', sweep: 'Sweeping' };
        if (text) text.textContent = labels[mode] || mode;

        if (timer && data.elapsed_seconds) {
            timer.textContent = formatDuration(data.elapsed_seconds);
        } else if (timer) {
            timer.textContent = '';
        }

        // Toggle sidebar buttons
        toggleButtons(mode);

        // Update stats strip
        updateStatsStrip(mode);

        // RX recording indicator
        const rec = document.getElementById('subghzRxRecording');
        if (rec) rec.style.display = (mode === 'rx') ? 'flex' : 'none';

        if (mode === 'idle') {
            if (burstBadgeTimer) {
                clearTimeout(burstBadgeTimer);
                burstBadgeTimer = null;
            }
            setBurstIndicator('idle', 'NO BURST');
            setRxBurstPill('idle', 'IDLE');
            updateRxHint('', 0, '');
            setBurstCanvasHighlight('rx', false);
            setBurstCanvasHighlight('decode', false);
        }

        if (activePanel === 'tx') {
            updateTxPanelState(mode === 'tx');
        }
    }

    function toggleButtons(mode) {
        const setEnabled = (id, enabled) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.disabled = !enabled;
            el.classList.toggle('disabled', !enabled);
        };

        const enableMap = [
            ['subghzRxStartBtn', mode === 'idle'],
            ['subghzRxStopBtn', mode === 'rx'],
            ['subghzRxStartBtnPanel', mode === 'idle'],
            ['subghzRxStopBtnPanel', mode === 'rx'],
            ['subghzSweepStartBtn', mode === 'idle'],
            ['subghzSweepStopBtn', mode === 'sweep'],
            ['subghzSweepStartBtnPanel', mode === 'idle'],
            ['subghzSweepStopBtnPanel', mode === 'sweep'],
        ];

        for (const [id, enabled] of enableMap) {
            setEnabled(id, enabled);
        }
    }

    function formatDuration(seconds) {
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return m > 0 ? `${m}m ${s}s` : `${s}s`;
    }

    function formatBytes(bytes) {
        const sizes = ['B', 'KB', 'MB', 'GB'];
        let val = Math.max(0, Number(bytes) || 0);
        let idx = 0;
        while (val >= 1024 && idx < sizes.length - 1) {
            val /= 1024;
            idx += 1;
        }
        const fixed = idx === 0 ? 0 : 1;
        return `${val.toFixed(fixed)} ${sizes[idx]}`;
    }

    function startStatusTimer() {
        rxStartTime = Date.now();
        if (statusTimer) VisibleInterval.clear(statusTimer);
        statusTimer = VisibleInterval.set(() => {
            const elapsed = (Date.now() - rxStartTime) / 1000;
            const formatted = formatDuration(elapsed);

            // Update sidebar timer
            const timer = document.getElementById('subghzStatusTimer');
            if (timer) timer.textContent = formatted;

            // Update stats strip timer
            const stripTimer = document.getElementById('subghzStripTimer');
            if (stripTimer) stripTimer.textContent = formatted;

            // Update TX elapsed if TX panel is active
            if (currentMode === 'tx') {
                const txElapsed = document.getElementById('subghzTxElapsed');
                if (txElapsed) txElapsed.textContent = formatted;
            }
        }, 1000);
    }

    function stopStatusTimer() {
        if (statusTimer) {
            VisibleInterval.clear(statusTimer);
            statusTimer = null;
        }
        rxStartTime = null;
        const timer = document.getElementById('subghzStatusTimer');
        if (timer) timer.textContent = '';
        const stripTimer = document.getElementById('subghzStripTimer');
        if (stripTimer) stripTimer.textContent = '';
    }

    // ------ RECEIVE ------

    function startRx() {
        const params = getParams();
        fetch('/subghz/receive/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'started') {
                updateStatusUI({ mode: 'rx' });
                startStatusTimer();
                showPanel('rx');
                updateRxDisplay(params);
                initRxScope();
                initRxWaterfall();
                syncWaterfallControls();
                resetRxVisuals();
                showConsole();
                addConsoleEntry('RX capture started at ' + (params.frequency_hz / 1e6).toFixed(3) + ' MHz', 'info');
                if (params.trigger_enabled) {
                    const pre = Number(params.trigger_pre_ms || 0);
                    const post = Number(params.trigger_post_ms || 0);
                    addConsoleEntry(
                        `Smart trigger armed (pre ${pre}ms / post ${post}ms)`,
                        'info'
                    );
                }
                updatePhaseIndicator('tuning');
                setTimeout(() => updatePhaseIndicator('listening'), 500);
            } else {
                addConsoleEntry(data.message || 'Failed to start capture', 'error');
                alert(data.message || 'Failed to start capture');
            }
        })
        .catch(err => alert('Error: ' + err.message));
    }

    function stopRx() {
        fetch('/subghz/receive/stop', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                updateStatusUI({ mode: 'idle' });
                stopStatusTimer();
                resetRxVisuals();
                addConsoleEntry('Capture stopped', 'warn');
                updatePhaseIndicator(null);
                loadCaptures();
            })
            .catch(err => alert('Error: ' + err.message));
    }

    // ------ DECODE ------

    function startDecode() {
        const params = getParams();
        clearDecodeOutput();
        fetch('/subghz/decode/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'started') {
                updateStatusUI({ mode: 'decode' });
                showPanel('decode');
                showConsole();
                initDecodeScope();
                initDecodeWaterfall();
                resetDecodeVisuals();
                addConsoleEntry('Decode started at ' + (params.frequency_hz / 1e6).toFixed(3) + ' MHz', 'info');
                addConsoleEntry('[decode] Profile: ' + (params.decode_profile || 'weather'), 'info');
                if (data.sample_rate && Number(data.sample_rate) !== Number(params.sample_rate)) {
                    addConsoleEntry(
                        '[decode] Sample rate adjusted to ' + (data.sample_rate / 1000).toFixed(0) + ' kHz for stability',
                        'info'
                    );
                }
                updatePhaseIndicator('tuning');
                setTimeout(() => updatePhaseIndicator('listening'), 800);
            } else {
                addConsoleEntry(data.message || 'Failed to start decode', 'error');
                alert(data.message || 'Failed to start decode');
            }
        })
        .catch(err => alert('Error: ' + err.message));
    }

    function stopDecode() {
        fetch('/subghz/decode/stop', { method: 'POST' })
            .then(r => r.json())
            .then(() => {
                updateStatusUI({ mode: 'idle' });
                addConsoleEntry('Decode stopped', 'warn');
                resetDecodeVisuals();
                updatePhaseIndicator(null);
            })
            .catch(err => alert('Error: ' + err.message));
    }

    function clearDecodeOutput() {
        const el = document.getElementById('subghzDecodeOutput');
        if (el) el.innerHTML = '<div class="subghz-empty">Waiting for signals...</div>';
        lastRawLine = '';
        lastRawLineTs = 0;
        lastBurstLineTs = 0;
    }

    function appendDecodeEntry(data) {
        const el = document.getElementById('subghzDecodeOutput');
        if (!el) return;

        // Remove empty placeholder
        const empty = el.querySelector('.subghz-empty');
        if (empty) empty.remove();

        const entry = document.createElement('div');
        entry.className = 'subghz-decode-entry';
        const model = data.model || 'Unknown';
        const isRaw = model.toLowerCase() === 'raw';
        if (isRaw) {
            const rawText = String(data.text || '').trim();
            const now = Date.now();
            if (rawText && rawText === lastRawLine && (now - lastRawLineTs) < 2500) {
                return;
            }
            lastRawLine = rawText;
            lastRawLineTs = now;
        }
        if (isRaw) {
            entry.classList.add('is-raw');
        }

        let html = `<span class="subghz-decode-model">${escapeHtml(model)}</span>`;
        if (isRaw && typeof data.text === 'string') {
            html += `<span class="subghz-decode-rawtext">: ${escapeHtml(data.text)}</span>`;
        } else {
            const skipKeys = ['type', 'model', 'time', 'mic'];
            for (const [key, value] of Object.entries(data)) {
                if (skipKeys.includes(key)) continue;
                html += `<span class="subghz-decode-field"><strong>${escapeHtml(key)}:</strong> ${escapeHtml(String(value))}</span> `;
            }
        }

        entry.innerHTML = html;
        el.appendChild(entry);
        el.scrollTop = el.scrollHeight;

        while (el.children.length > 200) {
            el.removeChild(el.firstChild);
        }

        // Dashboard updates
        if (!isRaw) {
            signalCount++;
            updateStatsStrip('decode');
            addConsoleEntry('Signal: ' + model, 'success');
        }
        updatePhaseIndicator('decoding');
    }

    function setBurstCanvasHighlight(mode, active) {
        const targets = mode === 'decode'
            ? ['subghzDecodeScope', 'subghzDecodeWaterfall']
            : ['subghzRxScope', 'subghzRxWaterfall'];
        for (const id of targets) {
            const canvas = document.getElementById(id);
            const host = canvas?.parentElement;
            if (host) host.classList.toggle('burst-active', !!active);
        }
    }

    function setBurstIndicator(state, text) {
        const badge = document.getElementById('subghzBurstIndicator');
        const label = document.getElementById('subghzBurstText');
        if (!badge || !label) return;
        badge.classList.remove('active', 'recent');
        if (state === 'active') badge.classList.add('active');
        if (state === 'recent') badge.classList.add('recent');
        label.textContent = text || 'NO BURST';
    }

    function setRxBurstPill(state, text) {
        const pill = document.getElementById('subghzRxBurstPill');
        if (!pill) return;
        pill.classList.remove('active', 'recent');
        if (state === 'active') pill.classList.add('active');
        if (state === 'recent') pill.classList.add('recent');
        pill.textContent = text || 'IDLE';
    }

    function updateRxHint(hint, confidence, protocolHint) {
        const textEl = document.getElementById('subghzRxHintText');
        const confEl = document.getElementById('subghzRxHintConfidence');
        if (textEl) {
            if (hint) {
                textEl.textContent = protocolHint
                    ? `${hint} - ${protocolHint}`
                    : hint;
            } else {
                textEl.textContent = 'No modulation hint yet';
            }
        }
        if (confEl) {
            if (typeof confidence === 'number' && confidence > 0) {
                confEl.textContent = `${Math.round(confidence * 100)}%`;
            } else {
                confEl.textContent = '--';
            }
        }
    }

    function clearBurstIndicatorLater(delayMs) {
        if (burstBadgeTimer) clearTimeout(burstBadgeTimer);
        burstBadgeTimer = setTimeout(() => {
            setBurstIndicator('idle', 'NO BURST');
            setRxBurstPill('idle', 'IDLE');
            setBurstCanvasHighlight('rx', false);
            setBurstCanvasHighlight('decode', false);
        }, delayMs);
    }

    function handleRxBurst(data) {
        if (!data) return;
        const mode = data.mode === 'decode' ? 'decode' : 'rx';

        if (data.event === 'start') {
            const startOffset = Math.max(0, Number(data.start_offset_s || 0));
            setBurstIndicator('active', `LIVE ${mode.toUpperCase()} +${startOffset.toFixed(2)}s`);
            if (mode === 'rx') setRxBurstPill('active', 'BURST');
            setBurstCanvasHighlight(mode, true);
            if (burstBadgeTimer) {
                clearTimeout(burstBadgeTimer);
                burstBadgeTimer = null;
            }
            return;
        }

        if (data.event !== 'end') return;
        const now = Date.now();
        if ((now - lastBurstLineTs) < 250) return;
        lastBurstLineTs = now;

        const durationMs = Math.max(0, parseInt(data.duration_ms || 0, 10) || 0);
        const peakLevel = Math.max(0, Math.min(100, parseInt(data.peak_level || 0, 10) || 0));
        const startOffset = Math.max(0, Number(data.start_offset_s || 0));
        const modHint = typeof data.modulation_hint === 'string' ? data.modulation_hint.trim() : '';
        const fp = typeof data.fingerprint === 'string' ? data.fingerprint.trim() : '';
        const extras = [
            modHint ? modHint : '',
            fp ? `fp ${fp.slice(0, 8)}` : '',
        ].filter(Boolean).join(' • ');
        const burstMsg = `RF burst ${durationMs} ms @ +${startOffset.toFixed(2)}s (peak ${peakLevel}%)${extras ? ' - ' + extras : ''}`;

        setBurstCanvasHighlight(mode, false);
        setBurstIndicator('recent', `${durationMs}ms - ${peakLevel}%`);
        if (mode === 'rx') setRxBurstPill('recent', `${durationMs}ms`);
        clearBurstIndicatorLater(2200);

        addConsoleEntry(`[${mode}] ${burstMsg}`, 'success');

        if (mode === 'decode') {
            appendDecodeEntry({
                model: 'RF Burst',
                duration_ms: durationMs,
                peak_level: `${peakLevel}%`,
                offset_s: startOffset.toFixed(2),
            });
        }
    }

    // ------ TRANSMIT ------

    function estimateCaptureDurationSeconds(capture) {
        if (!capture) return 0;
        const direct = Number(capture.duration_seconds || 0);
        if (direct > 0) return direct;
        const sr = Number(capture.sample_rate || 0);
        const size = Number(capture.size_bytes || 0);
        if (sr > 0 && size > 0) return size / (sr * 2);
        return 0;
    }

    function syncTxSegmentSelection(changedField) {
        const startEl = document.getElementById('subghzTxSegmentStart');
        const endEl = document.getElementById('subghzTxSegmentEnd');
        const enabledEl = document.getElementById('subghzTxSegmentEnabled');
        const summaryEl = document.getElementById('subghzTxSegmentSummary');
        const totalEl = document.getElementById('subghzTxModalDuration');

        const total = estimateCaptureDurationSeconds(pendingTxCaptureMeta);
        const segmentEnabled = !!enabledEl?.checked && total > 0;

        if (startEl) startEl.disabled = !segmentEnabled;
        if (endEl) endEl.disabled = !segmentEnabled;

        if (!segmentEnabled) {
            if (summaryEl) summaryEl.textContent = `Full capture (${total.toFixed(3)} s)`;
            return;
        }

        let start = Math.max(0, Number(startEl?.value || 0));
        let end = Math.max(0, Number(endEl?.value || total));
        if (changedField === 'start' && end <= start) end = Math.min(total, start + 0.05);
        if (changedField === 'end' && end <= start) start = Math.max(0, end - 0.05);
        start = Math.max(0, Math.min(total, start));
        end = Math.max(start + 0.01, Math.min(total, end));

        if (startEl) startEl.value = start.toFixed(3);
        if (endEl) endEl.value = end.toFixed(3);
        if (totalEl) totalEl.textContent = `${total.toFixed(3)} s`;
        if (summaryEl) summaryEl.textContent = `Segment ${start.toFixed(3)}s - ${end.toFixed(3)}s (${(end - start).toFixed(3)} s)`;
    }

    function applyTxBurstSegment(startSeconds, durationSeconds, paddingSeconds) {
        const total = estimateCaptureDurationSeconds(pendingTxCaptureMeta);
        if (total <= 0) return;
        const pad = Math.max(0, Number(paddingSeconds || 0));
        const start = Math.max(0, Number(startSeconds || 0) - pad);
        const end = Math.min(total, Number(startSeconds || 0) + Number(durationSeconds || 0) + pad);

        const enabledEl = document.getElementById('subghzTxSegmentEnabled');
        const startEl = document.getElementById('subghzTxSegmentStart');
        const endEl = document.getElementById('subghzTxSegmentEnd');

        if (enabledEl) enabledEl.checked = true;
        if (startEl) startEl.value = start.toFixed(3);
        if (endEl) endEl.value = end.toFixed(3);
        syncTxSegmentSelection('end');
    }

    function setTxTimelineRangeText(text) {
        const rangeEl = document.getElementById('subghzTxBurstRange');
        if (rangeEl) rangeEl.textContent = text;
    }

    function bindTxTimelineEditor(timeline, totalSeconds) {
        if (!timeline || totalSeconds <= 0) return;
        const selection = timeline.querySelector('.subghz-tx-burst-selection');
        if (!selection) return;

        timeline.onmousedown = (event) => {
            if (event.button !== 0) return;
            if (event.target?.classList?.contains('subghz-tx-burst-marker')) return;
            const rect = timeline.getBoundingClientRect();
            const startPx = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
            txTimelineDragState = { rect, startPx, currentPx: startPx };
            timeline.classList.add('dragging');
            selection.style.display = '';
            selection.style.left = `${startPx}px`;
            selection.style.width = '1px';
            setTxTimelineRangeText('Drag to define TX segment');
            event.preventDefault();
        };

        const onMove = (event) => {
            if (!txTimelineDragState) return;
            const { rect, startPx } = txTimelineDragState;
            const currentPx = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
            txTimelineDragState.currentPx = currentPx;
            const left = Math.min(startPx, currentPx);
            const width = Math.max(1, Math.abs(currentPx - startPx));
            selection.style.left = `${left}px`;
            selection.style.width = `${width}px`;

            const startSec = (left / rect.width) * totalSeconds;
            const endSec = ((left + width) / rect.width) * totalSeconds;
            setTxTimelineRangeText(
                `Selected ${startSec.toFixed(3)}s - ${endSec.toFixed(3)}s (${(endSec - startSec).toFixed(3)}s)`
            );
        };

        const onUp = () => {
            if (!txTimelineDragState) return;
            const { rect, startPx, currentPx } = txTimelineDragState;
            txTimelineDragState = null;
            timeline.classList.remove('dragging');

            const left = Math.min(startPx, currentPx);
            const right = Math.max(startPx, currentPx);
            const startSec = (left / rect.width) * totalSeconds;
            const endSec = (right / rect.width) * totalSeconds;
            const minSpanSeconds = Math.max(0.01, totalSeconds * 0.0025);
            if ((endSec - startSec) >= minSpanSeconds) {
                applyTxBurstSegment(startSec, endSec - startSec, 0.0);
                setTxTimelineRangeText(
                    `Segment ${startSec.toFixed(3)}s - ${endSec.toFixed(3)}s (${(endSec - startSec).toFixed(3)}s)`
                );
            } else {
                selection.style.display = 'none';
                setTxTimelineRangeText('Drag on timeline to select TX segment');
            }
        };

        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        timeline.onmouseleave = () => {};
        timeline.dataset.editorBound = '1';
        timeline._txEditorCleanup = () => {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        };
    }

    function renderTxBurstAssist(capture) {
        const section = document.getElementById('subghzTxBurstAssist');
        const timeline = document.getElementById('subghzTxBurstTimeline');
        const list = document.getElementById('subghzTxBurstList');
        if (!section || !timeline || !list) return;
        if (typeof timeline._txEditorCleanup === 'function') {
            timeline._txEditorCleanup();
            timeline._txEditorCleanup = null;
        }

        pendingTxBursts = Array.isArray(capture?.bursts)
            ? capture.bursts
                .map(b => ({
                    start_seconds: Math.max(0, Number(b.start_seconds || 0)),
                    duration_seconds: Math.max(0, Number(b.duration_seconds || 0)),
                    peak_level: Math.max(0, Math.min(100, Number(b.peak_level || 0))),
                    modulation_hint: typeof b.modulation_hint === 'string' ? b.modulation_hint : '',
                    modulation_confidence: Math.max(0, Math.min(1, Number(b.modulation_confidence || 0))),
                    fingerprint: typeof b.fingerprint === 'string' ? b.fingerprint : '',
                }))
                .filter(b => b.duration_seconds > 0)
                .sort((a, b) => a.start_seconds - b.start_seconds)
            : [];

        timeline.innerHTML = '';
        list.innerHTML = '';
        timeline.classList.remove('dragging');
        const selection = document.createElement('div');
        selection.className = 'subghz-tx-burst-selection';
        timeline.appendChild(selection);
        const total = estimateCaptureDurationSeconds(capture);
        setTxTimelineRangeText('Drag on timeline to select TX segment');

        if (!pendingTxBursts.length || total <= 0) {
            section.style.display = '';
            const empty = document.createElement('div');
            empty.className = 'subghz-tx-burst-empty';
            empty.textContent = 'No burst markers in this capture yet. Record a fresh RAW capture to auto-mark burst timings.';
            list.appendChild(empty);
            bindTxTimelineEditor(timeline, Math.max(0, total));
            return;
        }

        section.style.display = '';
        const showBursts = pendingTxBursts.slice(0, 60);
        for (let i = 0; i < showBursts.length; i++) {
            const burst = showBursts[i];
            const leftPct = Math.max(0, Math.min(100, (burst.start_seconds / total) * 100));
            const widthPct = Math.max(0.35, Math.min(100, (burst.duration_seconds / total) * 100));

            const marker = document.createElement('button');
            marker.type = 'button';
            marker.className = 'subghz-tx-burst-marker';
            marker.style.left = `${leftPct}%`;
            marker.style.width = `${widthPct}%`;
            marker.title = `Burst ${i + 1}: +${burst.start_seconds.toFixed(3)}s for ${burst.duration_seconds.toFixed(3)}s`;
            marker.addEventListener('click', () => {
                applyTxBurstSegment(burst.start_seconds, burst.duration_seconds, 0.06);
            });
            timeline.appendChild(marker);

            const row = document.createElement('div');
            row.className = 'subghz-tx-burst-item';
            const text = document.createElement('span');
            const burstParts = [
                `#${i + 1}`,
                `+${burst.start_seconds.toFixed(3)}s`,
                `${burst.duration_seconds.toFixed(3)}s`,
                `peak ${burst.peak_level}%`,
            ];
            if (burst.modulation_hint) {
                burstParts.push(`${burst.modulation_hint} ${Math.round(burst.modulation_confidence * 100)}%`);
            }
            if (burst.fingerprint) {
                burstParts.push(`fp ${burst.fingerprint.slice(0, 8)}`);
            }
            text.textContent = burstParts.join('  ');
            const useBtn = document.createElement('button');
            useBtn.type = 'button';
            useBtn.textContent = 'Use';
            useBtn.addEventListener('click', () => {
                applyTxBurstSegment(burst.start_seconds, burst.duration_seconds, 0.06);
            });
            row.appendChild(text);
            row.appendChild(useBtn);
            list.appendChild(row);
        }
        bindTxTimelineEditor(timeline, total);
    }

    function cleanupTxModalState(closeOverlay = true, clearCapture = true) {
        if (clearCapture) {
            pendingTxCaptureId = null;
            pendingTxCaptureMeta = null;
        }
        pendingTxBursts = [];
        txTimelineDragState = null;
        txModalIntent = 'tx';
        const timeline = document.getElementById('subghzTxBurstTimeline');
        if (timeline && typeof timeline._txEditorCleanup === 'function') {
            timeline._txEditorCleanup();
            timeline._txEditorCleanup = null;
        }
        if (closeOverlay) {
            const overlay = document.getElementById('subghzTxModalOverlay');
            if (overlay) overlay.classList.remove('active');
        }
    }

    function pickStrongestBurstSegment(totalDuration, paddingSeconds = 0.06) {
        if (!Array.isArray(pendingTxBursts) || pendingTxBursts.length === 0 || totalDuration <= 0) return null;
        const strongest = pendingTxBursts
            .slice()
            .sort((a, b) => {
                const peakDiff = Number(b.peak_level || 0) - Number(a.peak_level || 0);
                if (peakDiff !== 0) return peakDiff;
                return Number(b.duration_seconds || 0) - Number(a.duration_seconds || 0);
            })[0];
        const startRaw = Number(strongest?.start_seconds || 0);
        const durRaw = Number(strongest?.duration_seconds || 0);
        if (durRaw <= 0) return null;
        const start = Math.max(0, startRaw - paddingSeconds);
        const end = Math.min(totalDuration, startRaw + durRaw + paddingSeconds);
        if (end <= start) return null;
        return {
            start_seconds: Number(start.toFixed(3)),
            duration_seconds: Number((end - start).toFixed(3)),
        };
    }

    function populateTxModalFromCapture(capture) {
        if (!capture) return;
        pendingTxCaptureMeta = capture;
        const freqMhz = (Number(capture.frequency_hz || 0) / 1000000).toFixed(3);
        const freqEl = document.getElementById('subghzTxModalFreq');
        if (freqEl) freqEl.textContent = freqMhz + ' MHz';
        const total = estimateCaptureDurationSeconds(capture);
        const durationEl = document.getElementById('subghzTxModalDuration');
        if (durationEl) durationEl.textContent = `${total.toFixed(3)} s`;
        const enabledEl = document.getElementById('subghzTxSegmentEnabled');
        const startEl = document.getElementById('subghzTxSegmentStart');
        const endEl = document.getElementById('subghzTxSegmentEnd');
        if (enabledEl) enabledEl.checked = false;
        if (startEl) {
            startEl.value = '0.000';
            startEl.min = '0';
            startEl.max = total.toFixed(3);
            startEl.step = '0.01';
        }
        if (endEl) {
            endEl.value = total.toFixed(3);
            endEl.min = '0';
            endEl.max = total.toFixed(3);
            endEl.step = '0.01';
        }
        syncTxSegmentSelection();
        renderTxBurstAssist(capture);

        if (txModalIntent === 'trim') {
            if (enabledEl) enabledEl.checked = true;
            const auto = pickStrongestBurstSegment(total, 0.06);
            if (auto) {
                applyTxBurstSegment(auto.start_seconds, auto.duration_seconds, 0);
                setTxTimelineRangeText(
                    `Trim target ${auto.start_seconds.toFixed(3)}s - ${(auto.start_seconds + auto.duration_seconds).toFixed(3)}s`
                );
            } else {
                syncTxSegmentSelection();
                setTxTimelineRangeText('Select a segment, then click Trim + Save');
            }
        }
    }

    function getModalTxSegment(options = {}) {
        const allowAutoBurst = options.allowAutoBurst === true;
        const requireSelection = options.requireSelection === true;
        const totalDuration = estimateCaptureDurationSeconds(pendingTxCaptureMeta);
        if (totalDuration <= 0) {
            return { error: 'Capture duration unavailable' };
        }

        const segmentEnabled = !!document.getElementById('subghzTxSegmentEnabled')?.checked;
        if (segmentEnabled) {
            const startVal = Number(document.getElementById('subghzTxSegmentStart')?.value || 0);
            const endVal = Number(document.getElementById('subghzTxSegmentEnd')?.value || 0);
            const startSeconds = Math.max(0, Math.min(totalDuration, startVal));
            const endSeconds = Math.max(0, Math.min(totalDuration, endVal));
            const durationSeconds = endSeconds - startSeconds;
            if (durationSeconds <= 0) {
                return { error: 'Segment end must be greater than start' };
            }
            return {
                start_seconds: Number(startSeconds.toFixed(3)),
                duration_seconds: Number(durationSeconds.toFixed(3)),
                source: 'manual',
            };
        }

        if (allowAutoBurst) {
            const auto = pickStrongestBurstSegment(totalDuration, 0.06);
            if (auto) {
                return { ...auto, source: 'auto-burst' };
            }
        }

        if (requireSelection) {
            return { error: 'Select a segment on the timeline first' };
        }
        return null;
    }

    function buildTxRequest(captureId, segment) {
        const txGain = parseInt(document.getElementById('subghzTxGain')?.value || '20', 10);
        const maxDuration = parseInt(document.getElementById('subghzTxMaxDuration')?.value || '10', 10);
        const serial = (document.getElementById('subghzDeviceSerial')?.value || '').trim();
        const body = {
            capture_id: captureId,
            tx_gain: txGain,
            max_duration: maxDuration,
        };
        if (segment && Number(segment.duration_seconds || 0) > 0) {
            body.start_seconds = Number(segment.start_seconds.toFixed(3));
            body.duration_seconds = Number(segment.duration_seconds.toFixed(3));
        }
        if (serial) body.device_serial = serial;
        return body;
    }

    function transmitWithBody(body, logMessage, logLevel) {
        const txGain = Number(body.tx_gain || 0);
        showPanel('tx');
        updateTxPanelState(true);
        showConsole();
        addConsoleEntry(logMessage || 'Preparing transmission...', logLevel || 'warn');

        fetch('/subghz/transmit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'transmitting') {
                    lastTxCaptureId = body.capture_id;
                    const txSegment = data.segment && typeof data.segment === 'object'
                        ? {
                            start_seconds: Number(data.segment.start_seconds || 0),
                            duration_seconds: Number(data.segment.duration_seconds || 0),
                        }
                        : (typeof body.start_seconds === 'number' && typeof body.duration_seconds === 'number'
                            ? { start_seconds: body.start_seconds, duration_seconds: body.duration_seconds }
                            : null);
                    lastTxRequest = { capture_id: body.capture_id };
                    if (txSegment && txSegment.duration_seconds > 0) {
                        lastTxRequest.start_seconds = Number(txSegment.start_seconds.toFixed(3));
                        lastTxRequest.duration_seconds = Number(txSegment.duration_seconds.toFixed(3));
                    }

                    updateStatusUI({ mode: 'tx' });
                    updateTxPanelState(true);
                    startStatusTimer();
                    addConsoleEntry('Transmitting on ' + ((data.frequency_hz || 0) / 1e6).toFixed(3) + ' MHz', 'warn');
                    if (txSegment && txSegment.duration_seconds > 0) {
                        addConsoleEntry(
                            `TX segment ${txSegment.start_seconds.toFixed(3)}s + ${txSegment.duration_seconds.toFixed(3)}s`,
                            'info'
                        );
                    }

                    const freqDisplay = document.getElementById('subghzTxFreqDisplay');
                    const gainDisplay = document.getElementById('subghzTxGainDisplay');
                    if (freqDisplay && data.frequency_hz) freqDisplay.textContent = (data.frequency_hz / 1e6).toFixed(3) + ' MHz';
                    if (gainDisplay) gainDisplay.textContent = txGain + ' dB';
                } else {
                    updateTxPanelState(false);
                    addConsoleEntry(data.message || 'TX failed', 'error');
                    alert(data.message || 'TX failed');
                }
            })
            .catch(err => {
                updateTxPanelState(false);
                alert('TX error: ' + err.message);
            });
    }

    function showTxConfirm(captureId, intent) {
        txModalIntent = intent === 'trim' ? 'trim' : 'tx';
        pendingTxCaptureId = captureId;
        pendingTxCaptureMeta = null;
        pendingTxBursts = [];
        const burstAssist = document.getElementById('subghzTxBurstAssist');
        if (burstAssist) burstAssist.style.display = 'none';

        const overlay = document.getElementById('subghzTxModalOverlay');
        if (overlay) overlay.classList.add('active');

        fetch(`/subghz/captures/${encodeURIComponent(captureId)}`)
            .then(r => r.json())
            .then(data => {
                if (data.capture) {
                    populateTxModalFromCapture(data.capture);
                } else {
                    throw new Error('Capture not found');
                }
            })
            .catch(() => {
                const durationEl = document.getElementById('subghzTxModalDuration');
                if (durationEl) durationEl.textContent = '--';
                const summaryEl = document.getElementById('subghzTxSegmentSummary');
                if (summaryEl) summaryEl.textContent = 'Segment controls unavailable';
                const burstAssistEl = document.getElementById('subghzTxBurstAssist');
                if (burstAssistEl) burstAssistEl.style.display = 'none';
            });
    }

    function showTrimCapture(captureId) {
        showTxConfirm(captureId, 'trim');
    }

    function cancelTx() {
        cleanupTxModalState(true, true);
    }

    function confirmTx() {
        if (!pendingTxCaptureId) return;
        const segment = getModalTxSegment({ allowAutoBurst: false, requireSelection: false });
        if (segment && segment.error) {
            alert(segment.error);
            return;
        }
        const body = buildTxRequest(pendingTxCaptureId, segment);
        cleanupTxModalState(true, true);
        transmitWithBody(body, 'Preparing transmission...', 'warn');
    }

    function trimCaptureSelection() {
        if (!pendingTxCaptureId) return;
        const segment = getModalTxSegment({ allowAutoBurst: true, requireSelection: true });
        if (!segment || segment.error) {
            alert(segment?.error || 'Select a segment before trimming');
            return;
        }

        const trimBtn = document.getElementById('subghzTxTrimBtn');
        const originalText = trimBtn?.textContent || 'Trim + Save';
        if (trimBtn) {
            trimBtn.disabled = true;
            trimBtn.textContent = 'Trimming...';
        }

        fetch(`/subghz/captures/${encodeURIComponent(pendingTxCaptureId)}/trim`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                start_seconds: segment.start_seconds,
                duration_seconds: segment.duration_seconds,
            }),
        })
            .then(async r => ({ ok: r.ok, data: await r.json() }))
            .then(({ ok, data }) => {
                if (!ok || data.status === 'error') {
                    throw new Error(data.message || 'Trim failed');
                }
                if (!data.capture) {
                    throw new Error('Trim completed but capture metadata missing');
                }

                pendingTxCaptureId = data.capture.id;
                txModalIntent = 'tx';
                populateTxModalFromCapture(data.capture);
                loadCaptures();

                addConsoleEntry(
                    `Trimmed capture saved (${segment.duration_seconds.toFixed(3)}s).`,
                    'success'
                );
            })
            .catch(err => {
                alert('Trim failed: ' + err.message);
            })
            .finally(() => {
                if (trimBtn) {
                    trimBtn.disabled = false;
                    trimBtn.textContent = originalText;
                }
            });
    }

    function stopTx() {
        fetch('/subghz/transmit/stop', { method: 'POST' })
            .then(r => r.json())
            .then(() => {
                finalizeTxUi('Transmission stopped');
            })
            .catch(err => alert('Error: ' + err.message));
    }

    function finalizeTxUi(message) {
        updateStatusUI({ mode: 'idle' });
        updateTxPanelState(false);
        stopStatusTimer();
        if (message) addConsoleEntry(message, 'info');
        updatePhaseIndicator(null);
        loadCaptures();
    }

    function updateTxPanelState(transmitting) {
        const txDisplay = document.getElementById('subghzTxDisplay');
        const label = document.getElementById('subghzTxStateLabel');
        const stopBtn = document.getElementById('subghzTxStopBtn');
        const chooseBtn = document.getElementById('subghzTxChooseCaptureBtn');
        const replayBtn = document.getElementById('subghzTxReplayLastBtn');
        if (txDisplay) {
            txDisplay.classList.toggle('transmitting', !!transmitting);
            txDisplay.classList.toggle('idle', !transmitting);
        }
        if (label) label.textContent = transmitting ? 'TRANSMITTING' : 'READY';
        if (stopBtn) {
            stopBtn.style.display = transmitting ? '' : 'none';
            stopBtn.disabled = !transmitting;
        }
        if (chooseBtn) {
            chooseBtn.style.display = transmitting ? 'none' : '';
            chooseBtn.disabled = !!transmitting;
        }
        if (replayBtn) {
            const canReplay = !!(lastTxRequest && lastTxRequest.capture_id);
            replayBtn.style.display = (!transmitting && canReplay) ? '' : 'none';
            replayBtn.disabled = transmitting || !canReplay;
        }
    }

    function replayLastTx() {
        if (!lastTxRequest || !lastTxRequest.capture_id) {
            addConsoleEntry('No previous transmission capture selected yet.', 'warn');
            return;
        }
        const body = buildTxRequest(lastTxRequest.capture_id, (
            typeof lastTxRequest.start_seconds === 'number' &&
            typeof lastTxRequest.duration_seconds === 'number'
        ) ? {
            start_seconds: lastTxRequest.start_seconds,
            duration_seconds: lastTxRequest.duration_seconds,
        } : null);
        transmitWithBody(body, 'Replaying last selected segment...', 'info');
    }

    // ------ SWEEP ------

    function startSweep() {
        const startMhz = parseFloat(document.getElementById('subghzSweepStart')?.value || '300');
        const endMhz = parseFloat(document.getElementById('subghzSweepEnd')?.value || '928');
        const serial = (document.getElementById('subghzDeviceSerial')?.value || '').trim();

        sweepData = [];
        showPanel('sweep');
        initSweepCanvas();

        const body = {
            freq_start_mhz: startMhz,
            freq_end_mhz: endMhz,
        };
        if (serial) body.device_serial = serial;

        fetch('/subghz/sweep/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'started') {
                updateStatusUI({ mode: 'sweep' });
                showConsole();
                addConsoleEntry('Sweep ' + startMhz + ' - ' + endMhz + ' MHz', 'info');
                updatePhaseIndicator('tuning');
                setTimeout(() => updatePhaseIndicator('listening'), 300);
            } else {
                addConsoleEntry(data.message || 'Failed to start sweep', 'error');
                alert(data.message || 'Failed to start sweep');
            }
        })
        .catch(err => alert('Error: ' + err.message));
    }

    function stopSweep() {
        fetch('/subghz/sweep/stop', { method: 'POST' })
            .then(r => r.json())
            .then(() => {
                updateStatusUI({ mode: 'idle' });
                addConsoleEntry('Sweep stopped', 'warn');
                updatePhaseIndicator(null);
            })
            .catch(err => alert('Error: ' + err.message));
    }

    function initSweepCanvas() {
        sweepCanvas = document.getElementById('subghzSweepCanvas');
        if (!sweepCanvas) return;
        sweepCtx = sweepCanvas.getContext('2d');
        resizeSweepCanvas();
        bindSweepInteraction();

        if (!sweepResizeObserver && sweepCanvas.parentElement) {
            sweepResizeObserver = new ResizeObserver(() => {
                resizeSweepCanvas();
                drawSweepChart();
            });
            sweepResizeObserver.observe(sweepCanvas.parentElement);
        }
    }

    function resizeSweepCanvas() {
        if (!sweepCanvas || !sweepCanvas.parentElement) return;
        const rect = sweepCanvas.parentElement.getBoundingClientRect();
        sweepCanvas.width = rect.width - 24;
        sweepCanvas.height = rect.height - 24;
    }

    function updateSweepChart(points) {
        for (const pt of points) {
            const idx = sweepData.findIndex(d => Math.abs(d.freq - pt.freq) < 0.01);
            if (idx >= 0) {
                sweepData[idx].power = pt.power;
            } else {
                sweepData.push(pt);
            }
        }
        sweepData.sort((a, b) => a.freq - b.freq);

        detectPeaks();
        drawSweepChart();
    }

    function detectPeaks() {
        if (sweepData.length < 5) { sweepPeaks = []; return; }
        const now = Date.now();
        const candidates = [];

        for (let i = 2; i < sweepData.length - 2; i++) {
            const p = sweepData[i].power;
            if (p > sweepData[i - 1].power && p > sweepData[i + 1].power &&
                p > sweepData[i - 2].power && p > sweepData[i + 2].power) {
                let leftMin = p, rightMin = p;
                for (let j = 1; j <= 20 && i - j >= 0; j++) leftMin = Math.min(leftMin, sweepData[i - j].power);
                for (let j = 1; j <= 20 && i + j < sweepData.length; j++) rightMin = Math.min(rightMin, sweepData[i + j].power);
                const prominence = p - Math.max(leftMin, rightMin);
                if (prominence >= 10) {
                    candidates.push({ freq: sweepData[i].freq, power: p, prominence });
                }
            }
        }

        candidates.sort((a, b) => b.power - a.power);
        sweepPeaks = candidates.slice(0, 10);

        for (const peak of sweepPeaks) {
            const existing = sweepPeakHold.find(h => Math.abs(h.freq - peak.freq) < 0.5);
            if (existing) {
                if (peak.power >= existing.power) {
                    existing.power = peak.power;
                    existing.ts = now;
                }
            } else {
                sweepPeakHold.push({ freq: peak.freq, power: peak.power, ts: now });
            }
        }

        sweepPeakHold = sweepPeakHold.filter(h => now - h.ts < 5000);
        updatePeakList();
    }

    function drawSweepChart() {
        if (!sweepCtx || !sweepCanvas || sweepData.length < 2) return;

        const ctx = sweepCtx;
        const w = sweepCanvas.width;
        const h = sweepCanvas.height;
        const pad = SWEEP_PAD;

        ctx.clearRect(0, 0, w, h);

        ctx.fillStyle = '#0d1117';
        ctx.fillRect(0, 0, w, h);

        const freqMin = sweepData[0].freq;
        const freqMax = sweepData[sweepData.length - 1].freq;
        const powerMin = SWEEP_POWER_MIN;
        const powerMax = SWEEP_POWER_MAX;

        const chartW = w - pad.left - pad.right;
        const chartH = h - pad.top - pad.bottom;

        const freqToX = f => pad.left + ((f - freqMin) / (freqMax - freqMin)) * chartW;
        const powerToY = p => pad.top + chartH - ((p - powerMin) / (powerMax - powerMin)) * chartH;

        // Grid
        ctx.strokeStyle = '#1a1f2e';
        ctx.lineWidth = 1;
        ctx.font = '10px Roboto Condensed, monospace';
        ctx.fillStyle = '#666';

        for (let db = powerMin; db <= powerMax; db += 20) {
            const y = powerToY(db);
            ctx.beginPath();
            ctx.moveTo(pad.left, y);
            ctx.lineTo(w - pad.right, y);
            ctx.stroke();
            ctx.fillText(db + ' dB', 4, y + 3);
        }

        const freqRange = freqMax - freqMin;
        const freqStep = freqRange > 500 ? 100 : freqRange > 200 ? 50 : freqRange > 50 ? 10 : 5;
        for (let f = Math.ceil(freqMin / freqStep) * freqStep; f <= freqMax; f += freqStep) {
            const x = freqToX(f);
            ctx.beginPath();
            ctx.moveTo(x, pad.top);
            ctx.lineTo(x, h - pad.bottom);
            ctx.stroke();
            ctx.fillText(f + '', x - 10, h - 8);
        }

        // Spectrum line
        ctx.beginPath();
        ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00d4ff';
        ctx.lineWidth = 1.5;

        for (let i = 0; i < sweepData.length; i++) {
            const x = freqToX(sweepData[i].freq);
            const y = powerToY(sweepData[i].power);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();

        // Fill under curve
        ctx.lineTo(freqToX(freqMax), powerToY(powerMin));
        ctx.lineTo(freqToX(freqMin), powerToY(powerMin));
        ctx.closePath();
        ctx.fillStyle = 'rgba(0, 212, 255, 0.05)';
        ctx.fill();

        // Peak hold dashes
        const now = Date.now();
        ctx.strokeStyle = 'rgba(255, 170, 0, 0.4)';
        ctx.lineWidth = 2;
        for (const hold of sweepPeakHold) {
            const age = (now - hold.ts) / 5000;
            ctx.globalAlpha = 1 - age;
            const x = freqToX(hold.freq);
            const y = powerToY(hold.power);
            ctx.beginPath();
            ctx.moveTo(x - 6, y);
            ctx.lineTo(x + 6, y);
            ctx.stroke();
        }
        ctx.globalAlpha = 1;

        // Peak markers
        for (const peak of sweepPeaks) {
            const x = freqToX(peak.freq);
            const y = powerToY(peak.power);
            ctx.fillStyle = '#ffaa00';
            ctx.beginPath();
            ctx.moveTo(x, y - 8);
            ctx.lineTo(x - 4, y - 2);
            ctx.lineTo(x + 4, y - 2);
            ctx.closePath();
            ctx.fill();
            ctx.font = '9px Roboto Condensed, monospace';
            ctx.fillStyle = 'rgba(255, 170, 0, 0.8)';
            ctx.textAlign = 'center';
            ctx.fillText(peak.freq.toFixed(1), x, y - 10);
        }
        ctx.textAlign = 'start';

        // Active frequency marker
        const activeFreq = parseFloat(document.getElementById('subghzFrequency')?.value);
        if (activeFreq && activeFreq >= freqMin && activeFreq <= freqMax) {
            const x = freqToX(activeFreq);
            ctx.save();
            ctx.setLineDash([6, 4]);
            ctx.strokeStyle = 'rgba(0, 255, 136, 0.6)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(x, pad.top);
            ctx.lineTo(x, h - pad.bottom);
            ctx.stroke();
            ctx.restore();
        }

        // Selected frequency marker
        if (sweepSelectedFreq !== null && sweepSelectedFreq >= freqMin && sweepSelectedFreq <= freqMax) {
            const x = freqToX(sweepSelectedFreq);
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.7)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(x, pad.top);
            ctx.lineTo(x, h - pad.bottom);
            ctx.stroke();
        }

        // Hover cursor line
        if (sweepHoverFreq !== null && sweepHoverFreq >= freqMin && sweepHoverFreq <= freqMax) {
            const x = freqToX(sweepHoverFreq);
            ctx.save();
            ctx.setLineDash([3, 3]);
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.25)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(x, pad.top);
            ctx.lineTo(x, h - pad.bottom);
            ctx.stroke();
            ctx.restore();
        }
    }

    // ------ SWEEP INTERACTION ------

    function bindSweepInteraction() {
        if (sweepInteractionBound || !sweepCanvas) return;
        sweepInteractionBound = true;
        sweepCanvas.style.cursor = 'crosshair';

        if (!sweepTooltipEl) {
            sweepTooltipEl = document.createElement('div');
            sweepTooltipEl.className = 'subghz-sweep-tooltip';
            document.body.appendChild(sweepTooltipEl);
        }

        if (!sweepCtxMenuEl) {
            sweepCtxMenuEl = document.createElement('div');
            sweepCtxMenuEl.className = 'subghz-sweep-ctx-menu';
            document.body.appendChild(sweepCtxMenuEl);
        }

        sweepDismissHandler = (e) => {
            if (sweepCtxMenuEl && !sweepCtxMenuEl.contains(e.target)) {
                sweepCtxMenuEl.style.display = 'none';
            }
            if (sweepActionBarEl && !sweepActionBarEl.contains(e.target) && e.target !== sweepCanvas) {
                sweepActionBarEl.classList.remove('visible');
            }
        };
        document.addEventListener('click', sweepDismissHandler);

        function mouseToCanvas(e) {
            const rect = sweepCanvas.getBoundingClientRect();
            const scaleX = sweepCanvas.width / rect.width;
            const scaleY = sweepCanvas.height / rect.height;
            return {
                x: (e.clientX - rect.left) * scaleX,
                y: (e.clientY - rect.top) * scaleY,
            };
        }

        sweepCanvas.addEventListener('mousemove', (e) => {
            const { x, y } = mouseToCanvas(e);
            const info = sweepPixelToFreqPower(x, y);
            if (!info.inChart || sweepData.length < 2) {
                sweepHoverFreq = null;
                sweepHoverPower = null;
                if (sweepTooltipEl) sweepTooltipEl.style.display = 'none';
                drawSweepChart();
                return;
            }
            sweepHoverFreq = info.freq;
            sweepHoverPower = interpolatePower(info.freq);
            if (sweepTooltipEl) {
                sweepTooltipEl.innerHTML =
                    '<span class="tip-freq">' + sweepHoverFreq.toFixed(3) + ' MHz</span>' +
                    ' &middot; ' +
                    '<span class="tip-power">' + sweepHoverPower.toFixed(1) + ' dB</span>';
                sweepTooltipEl.style.left = (e.clientX + 14) + 'px';
                sweepTooltipEl.style.top = (e.clientY - 30) + 'px';
                sweepTooltipEl.style.display = 'block';
            }
            drawSweepChart();
        });

        sweepCanvas.addEventListener('mouseleave', () => {
            sweepHoverFreq = null;
            sweepHoverPower = null;
            if (sweepTooltipEl) sweepTooltipEl.style.display = 'none';
            drawSweepChart();
        });

        sweepCanvas.addEventListener('click', (e) => {
            const { x, y } = mouseToCanvas(e);
            const info = sweepPixelToFreqPower(x, y);
            if (!info.inChart || sweepData.length < 2) return;
            sweepSelectedFreq = info.freq;
            tuneFromSweep(info.freq);
            showSweepActionBar(e.clientX, e.clientY, info.freq);
            drawSweepChart();
        });

        sweepCanvas.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            const { x, y } = mouseToCanvas(e);
            const info = sweepPixelToFreqPower(x, y);
            if (!info.inChart || sweepData.length < 2) return;
            const freq = info.freq;
            const freqStr = freq.toFixed(3);

            sweepCtxMenuEl.innerHTML =
                '<div class="subghz-ctx-header">' + freqStr + ' MHz</div>' +
                '<div class="subghz-ctx-item" data-action="tune"><span class="ctx-icon">&#9654;</span>Tune Here</div>' +
                '<div class="subghz-ctx-item" data-action="capture"><span class="ctx-icon">&#9679;</span>Open RAW at ' + freqStr + ' MHz</div>';

            sweepCtxMenuEl.style.left = e.clientX + 'px';
            sweepCtxMenuEl.style.top = e.clientY + 'px';
            sweepCtxMenuEl.style.display = 'block';

            sweepCtxMenuEl.querySelectorAll('.subghz-ctx-item').forEach(item => {
                item.onclick = () => {
                    sweepCtxMenuEl.style.display = 'none';
                    const action = item.dataset.action;
                    if (action === 'tune') tuneFromSweep(freq);
                    else if (action === 'capture') tuneAndCapture(freq);
                };
            });
        });
    }

    // ------ SWEEP ACTIONS ------

    function tuneFromSweep(freqMhz) {
        const el = document.getElementById('subghzFrequency');
        if (el) el.value = freqMhz.toFixed(3);
        sweepSelectedFreq = freqMhz;
        drawSweepChart();
    }

    function tuneAndCapture(freqMhz) {
        tuneFromSweep(freqMhz);
        stopSweep();
        hideSweepActionBar();
        setTimeout(() => {
            showPanel('rx');
            updateRxDisplay(getParams());
            showConsole();
            addConsoleEntry('Tuned to ' + freqMhz.toFixed(3) + ' MHz. Press Start to capture RAW.', 'info');
        }, 300);
    }

    // ------ FLOATING ACTION BAR ------

    function showSweepActionBar(clientX, clientY, freqMhz) {
        if (!sweepActionBarEl) {
            sweepActionBarEl = document.createElement('div');
            sweepActionBarEl.className = 'subghz-sweep-action-bar';
            document.body.appendChild(sweepActionBarEl);
        }

        sweepActionBarEl.innerHTML =
            '<button class="subghz-action-btn tune">Tune</button>' +
            '<button class="subghz-action-btn capture">Open RAW</button>';

        sweepActionBarEl.querySelector('.tune').onclick = (e) => {
            e.stopPropagation();
            tuneFromSweep(freqMhz);
            hideSweepActionBar();
        };
        sweepActionBarEl.querySelector('.capture').onclick = (e) => {
            e.stopPropagation();
            tuneAndCapture(freqMhz);
        };

        sweepActionBarEl.style.left = (clientX + 10) + 'px';
        sweepActionBarEl.style.top = (clientY + 14) + 'px';
        sweepActionBarEl.classList.remove('visible');
        void sweepActionBarEl.offsetHeight;
        sweepActionBarEl.classList.add('visible');
    }

    function hideSweepActionBar() {
        if (sweepActionBarEl) sweepActionBarEl.classList.remove('visible');
    }

    // ------ PEAK LIST ------

    function updatePeakList() {
        // Update sidebar, sweep panel, and any other peak lists
        const lists = [
            document.getElementById('subghzPeakList'),
            document.getElementById('subghzSweepPeakList'),
        ];
        for (const list of lists) {
            if (!list) continue;
            list.innerHTML = '';
            for (const peak of sweepPeaks) {
                const item = document.createElement('div');
                item.className = 'subghz-peak-item';
                item.innerHTML =
                    '<span class="peak-freq">' + peak.freq.toFixed(3) + ' MHz</span>' +
                    '<span class="peak-power">' + peak.power.toFixed(1) + ' dB</span>';
                item.onclick = () => tuneFromSweep(peak.freq);
                list.appendChild(item);
            }
        }
    }

    // ------ CAPTURES LIBRARY ------

    function loadCaptures() {
        fetch('/subghz/captures')
            .then(r => r.json())
            .then(data => {
                const captures = data.captures || [];
                latestCaptures = captures;
                const validIds = new Set(captures.map(c => c.id));
                selectedCaptureIds = new Set([...selectedCaptureIds].filter(id => validIds.has(id)));
                captureCount = captures.length;
                updateStatsStrip();
                updateSavedSelectionUi();
                renderCaptures(captures);
            })
            .catch(() => {});
    }

    function burstCountForCapture(cap) {
        return Array.isArray(cap?.bursts) ? cap.bursts.length : 0;
    }

    function updateSavedSelectionUi() {
        const selectBtn = document.getElementById('subghzSavedSelectBtn');
        const selectAllBtn = document.getElementById('subghzSavedSelectAllBtn');
        const deleteBtn = document.getElementById('subghzSavedDeleteSelectedBtn');
        const countEl = document.getElementById('subghzSavedSelectionCount');
        if (selectBtn) selectBtn.textContent = captureSelectMode ? 'Done' : 'Select';
        if (selectAllBtn) selectAllBtn.style.display = captureSelectMode ? '' : 'none';
        if (deleteBtn) {
            deleteBtn.style.display = captureSelectMode ? '' : 'none';
            deleteBtn.disabled = selectedCaptureIds.size === 0;
        }
        if (countEl) {
            countEl.style.display = captureSelectMode ? '' : 'none';
            countEl.textContent = `${selectedCaptureIds.size} selected`;
        }
    }

    function renderCaptures(captures) {
        // Render to both the visuals panel and the sidebar
        const targets = [
            {
                list: document.getElementById('subghzCapturesList'),
                empty: document.getElementById('subghzCapturesEmpty'),
                selectable: true,
            },
            {
                list: document.getElementById('subghzSidebarCaptures'),
                empty: document.getElementById('subghzSidebarCapturesEmpty'),
                selectable: false,
            },
        ];

        for (const { list, empty, selectable } of targets) {
            if (!list) continue;

            // Clear existing cards
            list.querySelectorAll('.subghz-capture-card').forEach(c => c.remove());

            if (captures.length === 0) {
                if (empty) empty.style.display = '';
                continue;
            }

            if (empty) empty.style.display = 'none';

            for (const cap of captures) {
                const freqMhz = (cap.frequency_hz / 1000000).toFixed(3);
                const sizeKb = (cap.size_bytes / 1024).toFixed(1);
                const ts = cap.timestamp ? new Date(cap.timestamp).toLocaleString() : '';
                const dur = cap.duration_seconds ? cap.duration_seconds.toFixed(1) + 's' : '';
                const burstCount = burstCountForCapture(cap);
                const selected = selectedCaptureIds.has(cap.id);
                const modulationHint = typeof cap.modulation_hint === 'string' ? cap.modulation_hint : '';
                const modulationConfidence = Number(cap.modulation_confidence || 0);
                const protocolHint = typeof cap.protocol_hint === 'string' ? cap.protocol_hint : '';
                const dominantFingerprint = typeof cap.dominant_fingerprint === 'string' ? cap.dominant_fingerprint : '';
                const fingerprintGroup = typeof cap.fingerprint_group === 'string' ? cap.fingerprint_group : '';
                const fingerprintGroupSize = Number(cap.fingerprint_group_size || 0);
                const labelSource = typeof cap.label_source === 'string' ? cap.label_source : '';

                const card = document.createElement('div');
                card.className = 'subghz-capture-card';
                if (burstCount > 0) card.classList.add('has-bursts');
                if (selectable && captureSelectMode) card.classList.add('select-mode');
                if (selectable && selected) card.classList.add('selected');

                let actionsHtml = '';
                if (selectable && captureSelectMode) {
                    actionsHtml = `
                        <div class="subghz-capture-actions select-mode">
                            <button class="select-btn ${selected ? 'selected' : ''}" onclick="SubGhz.toggleCaptureSelection('${escapeHtml(cap.id)}', event)">
                                ${selected ? 'Selected' : 'Select'}
                            </button>
                        </div>
                    `;
                } else {
                    actionsHtml = `
                        <div class="subghz-capture-actions">
                            <button class="replay-btn" onclick="SubGhz.showTxConfirm('${escapeHtml(cap.id)}')">Replay</button>
                            <button class="trim-btn" onclick="SubGhz.showTrimCapture('${escapeHtml(cap.id)}')">Trim</button>
                            <button onclick="SubGhz.renameCapture('${escapeHtml(cap.id)}')">Rename</button>
                            <button onclick="SubGhz.downloadCapture('${escapeHtml(cap.id)}')">Download</button>
                            <button class="delete-btn" onclick="SubGhz.deleteCapture('${escapeHtml(cap.id)}')">Delete</button>
                        </div>
                    `;
                }

                card.innerHTML = `
                    <div class="subghz-capture-header">
                        <span class="subghz-capture-freq">${escapeHtml(freqMhz)} MHz</span>
                        <div class="subghz-capture-header-right">
                            ${burstCount > 0 ? `<span class="subghz-capture-burst-badge">${burstCount} burst${burstCount === 1 ? '' : 's'}</span>` : ''}
                            <span class="subghz-capture-time">${escapeHtml(ts)}</span>
                        </div>
                    </div>
                    ${burstCount > 0 ? `
                        <div class="subghz-capture-burst-line">
                            <span class="subghz-capture-burst-flag">BURSTS DETECTED</span>
                            <span class="subghz-capture-burst-count">${burstCount}</span>
                        </div>
                    ` : ''}
                    ${(cap.label || modulationHint || dominantFingerprint) ? `
                        <div class="subghz-capture-tag-row">
                            ${cap.label && labelSource === 'auto' ? `<span class="subghz-capture-tag auto">AUTO LABEL</span>` : ''}
                            ${modulationHint ? `<span class="subghz-capture-tag hint">${escapeHtml(modulationHint)} ${modulationConfidence > 0 ? Math.round(modulationConfidence * 100) + '%' : ''}</span>` : ''}
                            ${fingerprintGroup ? `<span class="subghz-capture-tag fingerprint">${escapeHtml(fingerprintGroup)}${fingerprintGroupSize > 1 ? ' x' + fingerprintGroupSize : ''}</span>` : ''}
                        </div>
                    ` : ''}
                    ${cap.label ? `<div class="subghz-capture-label">${escapeHtml(cap.label)}</div>` : ''}
                    ${protocolHint ? `<div class="subghz-capture-label">${escapeHtml(protocolHint)}</div>` : ''}
                    ${dominantFingerprint ? `<div class="subghz-capture-label">Fingerprint: ${escapeHtml(dominantFingerprint)}</div>` : ''}
                    <div class="subghz-capture-meta">
                        <span>${escapeHtml(dur)}</span>
                        <span>${escapeHtml(sizeKb)} KB</span>
                        <span>${escapeHtml(String(cap.sample_rate / 1000))} kHz</span>
                    </div>
                    ${actionsHtml}
                `;
                if (selectable && captureSelectMode) {
                    card.addEventListener('click', (event) => toggleCaptureSelection(cap.id, event));
                }
                list.appendChild(card);
            }
        }
    }

    function toggleCaptureSelectMode(forceValue) {
        captureSelectMode = typeof forceValue === 'boolean' ? forceValue : !captureSelectMode;
        if (!captureSelectMode) selectedCaptureIds.clear();
        updateSavedSelectionUi();
        renderCaptures(latestCaptures);
    }

    function selectAllCaptures() {
        if (!captureSelectMode) return;
        const allIds = latestCaptures.map(c => c.id);
        if (selectedCaptureIds.size >= allIds.length) {
            selectedCaptureIds.clear();
        } else {
            selectedCaptureIds = new Set(allIds);
        }
        updateSavedSelectionUi();
        renderCaptures(latestCaptures);
    }

    function toggleCaptureSelection(id, event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        if (!captureSelectMode) return;
        if (selectedCaptureIds.has(id)) selectedCaptureIds.delete(id);
        else selectedCaptureIds.add(id);
        updateSavedSelectionUi();
        renderCaptures(latestCaptures);
    }

    async function deleteSelectedCaptures() {
        if (!captureSelectMode || selectedCaptureIds.size === 0) return;
        const ids = [...selectedCaptureIds];
        const confirmed = await AppFeedback.confirmAction({
            title: 'Delete Captures',
            message: `Delete ${ids.length} selected capture${ids.length === 1 ? '' : 's'}? This cannot be undone.`,
            confirmLabel: 'Delete',
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;

        Promise.all(
            ids.map(id => fetch(`/subghz/captures/${encodeURIComponent(id)}`, { method: 'DELETE' }))
        )
            .then(() => {
                selectedCaptureIds.clear();
                captureSelectMode = false;
                updateSavedSelectionUi();
                loadCaptures();
            })
            .catch(err => alert('Error deleting captures: ' + err.message));
    }

    async function deleteCapture(id) {
        const confirmed = await AppFeedback.confirmAction({
            title: 'Delete Capture',
            message: 'Delete this capture? This cannot be undone.',
            confirmLabel: 'Delete',
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;
        fetch(`/subghz/captures/${encodeURIComponent(id)}`, { method: 'DELETE' })
            .then(r => r.json())
            .then(() => loadCaptures())
            .catch(err => alert('Error: ' + err.message));
    }

    function renameCapture(id) {
        const label = prompt('Enter label for this capture:');
        if (label === null) return;
        fetch(`/subghz/captures/${encodeURIComponent(id)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label: label }),
        })
        .then(r => r.json())
        .then(() => loadCaptures())
        .catch(err => alert('Error: ' + err.message));
    }

    function downloadCapture(id) {
        window.open(`/subghz/captures/${encodeURIComponent(id)}/download`, '_blank');
    }

    // ------ SSE STREAM ------

    function startStream() {
        if (eventSource) {
            eventSource.close();
        }

        eventSource = new EventSource('/subghz/stream');

        eventSource.onmessage = function(e) {
            try {
                const data = JSON.parse(e.data);
                handleEvent(data);
            } catch (err) {
                // Ignore parse errors (keepalives etc.)
            }
        };

        eventSource.onerror = function() {
            setTimeout(() => {
                if (document.getElementById('subghzMode')?.classList.contains('active')) {
                    startStream();
                }
            }, 3000);
        };
    }

    function handleEvent(data) {
        const type = data.type;

        if (type === 'status') {
            updateStatusUI(data);
                if (data.status === 'started') {
                    if (data.mode === 'rx') startStatusTimer();
                    if (data.mode === 'decode') {
                        showConsole();
                    }
                    if (data.mode === 'sweep') {
                        if (activePanel !== 'sweep') showPanel('sweep');
                    }
                } else if (data.status === 'stopped' || data.status === 'decode_stopped' || data.status === 'sweep_stopped') {
                    stopStatusTimer();
                    resetRxVisuals();
                    resetDecodeVisuals();
                    addConsoleEntry('Operation stopped', 'warn');
                    updatePhaseIndicator(null);
                    if (data.mode === 'idle') loadCaptures();
                }
        } else if (type === 'decode') {
            appendDecodeEntry(data);
        } else if (type === 'decode_raw') {
            appendDecodeEntry({ model: 'Raw', text: data.text });
        } else if (type === 'rx_level') {
            updateRxLevel(data.level);
        } else if (type === 'rx_waveform') {
            updateRxWaveform(data.samples);
        } else if (type === 'rx_spectrum') {
            updateRxSpectrum(data.bins);
        } else if (type === 'rx_stats') {
            updateRxStats(data);
        } else if (type === 'rx_hint') {
            const confidence = Number(data.confidence || 0);
            updateRxHint(data.modulation_hint || '', confidence, data.protocol_hint || '');
            const now = Date.now();
            if ((now - lastRxHintTs) > 4000 && data.modulation_hint) {
                lastRxHintTs = now;
                addConsoleEntry(
                    `[rx] Hint: ${data.modulation_hint} (${Math.round(confidence * 100)}%)` +
                    (data.protocol_hint ? ` - ${data.protocol_hint}` : ''),
                    'info'
                );
            }
        } else if (type === 'decode_level') {
            updateDecodeLevel(data.level);
        } else if (type === 'decode_waveform') {
            updateDecodeWaveform(data.samples);
        } else if (type === 'decode_spectrum') {
            updateDecodeSpectrum(data.bins);
        } else if (type === 'rx_burst') {
            handleRxBurst(data);
        } else if (type === 'sweep') {
            if (data.points) {
                updateSweepChart(data.points);
                updatePhaseIndicator('decoding');
            }
        } else if (type === 'tx_status') {
            if (data.status === 'transmitting') {
                updateStatusUI({ mode: 'tx' });
                if (activePanel !== 'tx') showPanel('tx');
                updateTxPanelState(true);
                startStatusTimer();
                addConsoleEntry('Transmission started', 'warn');
            } else if (data.status === 'tx_complete' || data.status === 'tx_stopped') {
                if (currentMode === 'tx' || activePanel === 'tx') {
                    finalizeTxUi('Transmission ended');
                } else {
                    updateStatusUI({ mode: 'idle' });
                    stopStatusTimer();
                    loadCaptures();
                }
            }
        } else if (type === 'info') {
            // rtl_433 stderr info lines
            if (data.text) addConsoleEntry(data.text, 'info');
        } else if (type === 'error') {
            addConsoleEntry(data.message || 'Error', 'error');
            updatePhaseIndicator('error');
            alert(data.message || 'SubGHz error');
        }
    }

    // ------ UTILITIES ------

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    /**
     * Clean up when switching away from SubGHz mode
     */
    function destroy() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
        if (statusPollTimer) {
            VisibleInterval.clear(statusPollTimer);
            statusPollTimer = null;
        }
        if (burstBadgeTimer) {
            clearTimeout(burstBadgeTimer);
            burstBadgeTimer = null;
        }
        const txTimeline = document.getElementById('subghzTxBurstTimeline');
        if (txTimeline && typeof txTimeline._txEditorCleanup === 'function') {
            txTimeline._txEditorCleanup();
            txTimeline._txEditorCleanup = null;
        }
        txTimelineDragState = null;
        stopStatusTimer();
        setBurstIndicator('idle', 'NO BURST');
        setRxBurstPill('idle', 'IDLE');
        setBurstCanvasHighlight('rx', false);
        setBurstCanvasHighlight('decode', false);

        // Clean up interactive sweep elements
        if (sweepTooltipEl) { sweepTooltipEl.remove(); sweepTooltipEl = null; }
        if (sweepCtxMenuEl) { sweepCtxMenuEl.remove(); sweepCtxMenuEl = null; }
        if (sweepActionBarEl) { sweepActionBarEl.remove(); sweepActionBarEl = null; }
        if (sweepDismissHandler) {
            document.removeEventListener('click', sweepDismissHandler);
            sweepDismissHandler = null;
        }
        if (sweepResizeObserver) {
            sweepResizeObserver.disconnect();
            sweepResizeObserver = null;
        }
        sweepInteractionBound = false;
        sweepHoverFreq = null;
        sweepSelectedFreq = null;
        sweepPeaks = [];
        sweepPeakHold = [];

        if (rxScopeResizeObserver) {
            rxScopeResizeObserver.disconnect();
            rxScopeResizeObserver = null;
        }
        if (rxWaterfallResizeObserver) {
            rxWaterfallResizeObserver.disconnect();
            rxWaterfallResizeObserver = null;
        }
        rxScopeCanvas = null;
        rxScopeCtx = null;
        rxScopeData = [];
        rxWaterfallCanvas = null;
        rxWaterfallCtx = null;
        rxWaterfallPalette = null;
        rxWaterfallPaused = false;
        if (decodeScopeResizeObserver) {
            decodeScopeResizeObserver.disconnect();
            decodeScopeResizeObserver = null;
        }
        if (decodeWaterfallResizeObserver) {
            decodeWaterfallResizeObserver.disconnect();
            decodeWaterfallResizeObserver = null;
        }
        decodeScopeCanvas = null;
        decodeScopeCtx = null;
        decodeScopeData = [];
        decodeWaterfallCanvas = null;
        decodeWaterfallCtx = null;
        decodeWaterfallPalette = null;

        // Reset dashboard state
        activePanel = null;
        signalCount = 0;
        captureCount = 0;
        consoleEntries = [];
        consoleCollapsed = false;
        currentPhase = null;
        currentMode = 'idle';
        lastRawLine = '';
        lastRawLineTs = 0;
        lastBurstLineTs = 0;
        lastRxHintTs = 0;
        pendingTxBursts = [];
        captureSelectMode = false;
        selectedCaptureIds = new Set();
        latestCaptures = [];
        lastTxCaptureId = null;
        lastTxRequest = null;
        txModalIntent = 'tx';
    }

    // ------ DASHBOARD: HUB & PANELS ------

    function showHub() {
        activePanel = null;
        const hub = document.getElementById('subghzActionHub');
        if (hub) hub.style.display = '';
        const panels = ['Rx', 'Sweep', 'Tx', 'Saved'];
        panels.forEach(p => {
            const el = document.getElementById('subghzPanel' + p);
            if (el) el.style.display = 'none';
        });
        updateStatsStrip('idle');
        updateStatusUI({ mode: currentMode });
    }

    function showPanel(panel) {
        activePanel = panel;
        const hub = document.getElementById('subghzActionHub');
        if (hub) hub.style.display = 'none';
        const panelMap = { rx: 'Rx', sweep: 'Sweep', tx: 'Tx', saved: 'Saved' };
        Object.values(panelMap).forEach(p => {
            const el = document.getElementById('subghzPanel' + p);
            if (el) el.style.display = 'none';
        });
        const target = document.getElementById('subghzPanel' + (panelMap[panel] || ''));
        if (target) target.style.display = '';
        if (panel === 'rx') {
            initRxScope();
            initRxWaterfall();
            syncWaterfallControls();
        } else if (panel === 'saved') {
            updateSavedSelectionUi();
            loadCaptures();
        } else if (panel === 'tx') {
            updateTxPanelState(currentMode === 'tx');
        }
        updateStatsStrip();
        updateStatusUI({ mode: currentMode });
    }

    function hubAction(action) {
        if (action === 'rx') {
            showPanel('rx');
            updateRxDisplay(getParams());
            showConsole();
            addConsoleEntry('RAW panel ready. Press Start when you want to capture.', 'info');
        } else if (action === 'txselect') {
            showPanel('saved');
            loadCaptures();
        } else if (action === 'sweep') {
            startSweep();
        } else if (action === 'saved') {
            showPanel('saved');
            loadCaptures();
        }
    }

    function backToHub() {
        // Stop any running operation
        if (currentMode !== 'idle') {
            if (currentMode === 'rx') stopRx();
            else if (currentMode === 'sweep') stopSweep();
            else if (currentMode === 'tx') stopTx();
        }
        showHub();
        const consoleEl = document.getElementById('subghzConsole');
        if (consoleEl) consoleEl.style.display = 'none';
        updatePhaseIndicator(null);
    }

    function stopActive() {
        if (currentMode === 'rx') stopRx();
        else if (currentMode === 'sweep') stopSweep();
        else if (currentMode === 'tx') stopTx();
    }

    // ------ DASHBOARD: STATS STRIP ------

    function updateStatsStrip(mode) {
        const stripDot = document.getElementById('subghzStripDot');
        const stripStatus = document.getElementById('subghzStripStatus');
        const stripFreq = document.getElementById('subghzStripFreq');
        const stripMode = document.getElementById('subghzStripMode');
        const stripSignals = document.getElementById('subghzStripSignals');
        const stripCaptures = document.getElementById('subghzStripCaptures');

        if (!mode) mode = currentMode || 'idle';

        if (stripDot) {
            stripDot.className = 'subghz-strip-dot';
            if (mode !== 'idle' && mode !== 'saved') {
                stripDot.classList.add(mode, 'active');
            }
        }

        const labels = { idle: 'Idle', rx: 'Capturing', decode: 'Decoding', tx: 'Transmitting', sweep: 'Sweeping', saved: 'Library' };
        if (stripStatus) stripStatus.textContent = labels[mode] || mode;

        const freqEl = document.getElementById('subghzFrequency');
        if (stripFreq && freqEl) {
            stripFreq.textContent = freqEl.value || '--';
        }

        const modeLabels = { idle: '--', decode: 'READ', rx: 'RAW', sweep: 'SWEEP', tx: 'TX', saved: 'SAVED' };
        if (stripMode) stripMode.textContent = modeLabels[mode] || '--';

        if (stripSignals) stripSignals.textContent = signalCount;
        if (stripCaptures) stripCaptures.textContent = captureCount;
    }

    // ------ DASHBOARD: RX DISPLAY ------

    function updateRxDisplay(params) {
        const freqEl = document.getElementById('subghzRxFreq');
        const lnaEl = document.getElementById('subghzRxLna');
        const vgaEl = document.getElementById('subghzRxVga');
        const srEl = document.getElementById('subghzRxSampleRate');

        if (freqEl) freqEl.textContent = (params.frequency_hz / 1e6).toFixed(3) + ' MHz';
        if (lnaEl) lnaEl.textContent = params.lna_gain + ' dB';
        if (vgaEl) vgaEl.textContent = params.vga_gain + ' dB';
        if (srEl) srEl.textContent = (params.sample_rate / 1000) + ' kHz';
    }

    // ------ DASHBOARD: CONSOLE ------

    function addConsoleEntry(msg, level) {
        level = level || '';
        const now = new Date();
        const ts = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        consoleEntries.push({ ts, msg, level });
        if (consoleEntries.length > 100) consoleEntries.shift();

        const log = document.getElementById('subghzConsoleLog');
        if (!log) return;

        const entry = document.createElement('div');
        entry.className = 'subghz-log-entry';
        entry.innerHTML = '<span class="subghz-log-ts">' + escapeHtml(ts) + '</span>' +
                          '<span class="subghz-log-msg ' + escapeHtml(level) + '">' + escapeHtml(msg) + '</span>';
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;

        while (log.children.length > 100) {
            log.removeChild(log.firstChild);
        }
    }

    function showConsole() {
        const consoleEl = document.getElementById('subghzConsole');
        if (consoleEl) consoleEl.style.display = '';
    }

    function toggleConsole() {
        consoleCollapsed = !consoleCollapsed;
        const body = document.getElementById('subghzConsoleBody');
        const btn = document.getElementById('subghzConsoleToggleBtn');
        if (body) body.classList.toggle('collapsed', consoleCollapsed);
        if (btn) btn.classList.toggle('collapsed', consoleCollapsed);
    }

    function clearConsole() {
        consoleEntries = [];
        const log = document.getElementById('subghzConsoleLog');
        if (log) log.innerHTML = '';
    }

    function updatePhaseIndicator(phase) {
        currentPhase = phase;
        const steps = ['tuning', 'listening', 'decoding'];
        const phaseEls = {
            tuning: document.getElementById('subghzPhaseTuning'),
            listening: document.getElementById('subghzPhaseListening'),
            decoding: document.getElementById('subghzPhaseDecoding'),
        };

        if (!phase) {
            Object.values(phaseEls).forEach(el => {
                if (el) el.className = 'subghz-phase-step';
            });
            return;
        }

        if (phase === 'error') {
            Object.values(phaseEls).forEach(el => {
                if (el) {
                    el.className = 'subghz-phase-step';
                    el.classList.add('error');
                }
            });
            return;
        }

        const activeIdx = steps.indexOf(phase);
        steps.forEach((step, idx) => {
            const el = phaseEls[step];
            if (!el) return;
            el.className = 'subghz-phase-step';
            if (idx < activeIdx) el.classList.add('completed');
            else if (idx === activeIdx) el.classList.add('active');
        });
    }

    // ------ PUBLIC API ------
    return {
        init,
        destroy,
        setFreq,
        syncTriggerControls,
        switchTab,
        startRx,
        stopRx,
        startDecode,
        stopDecode,
        startSweep,
        stopSweep,
        showTxConfirm,
        showTrimCapture,
        cancelTx,
        syncTxSegmentSelection,
        confirmTx,
        trimCaptureSelection,
        stopTx,
        replayLastTx,
        loadCaptures,
        toggleCaptureSelectMode,
        selectAllCaptures,
        deleteSelectedCaptures,
        toggleCaptureSelection,
        deleteCapture,
        renameCapture,
        downloadCapture,
        tuneFromSweep,
        tuneAndCapture,
        // Dashboard
        showHub,
        showPanel,
        hubAction,
        backToHub,
        stopActive,
        toggleConsole,
        clearConsole,
        // Waterfall controls
        toggleWaterfall,
        setWaterfallFloor,
        setWaterfallRange,
    };
})();
