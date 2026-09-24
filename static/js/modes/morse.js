/**
 * Morse Code (CW) decoder mode.
 * Lifecycle state machine: idle -> starting -> running -> stopping -> idle/error
 */
var MorseMode = (function () {
    'use strict';

    var SETTINGS_KEY = 'intercept.morse.settings.v3';
    var STATUS_POLL_MS = 5000;
    var LOCAL_STOP_TIMEOUT_MS = 12000;
    var START_TIMEOUT_MS = 60000;

    var state = {
        initialized: false,
        controlsBound: false,
        lifecycle: 'idle',
        eventSource: null,
        statusPollTimer: null,
        stopPromise: null,
        startSeq: 0,
        charCount: 0,
        decodedLog: [], // { timestamp, morse, char }
        rawLog: [],
        waiting: false,
        waitingStart: 0,
        lastMetrics: {
            wpm: 15,
            tone_freq: 700,
            level: 0,
            threshold: 0,
            noise_floor: 0,
            stop_ms: null,
        },
    };

    // Scope state
    var scopeCtx = null;
    var scopeAnim = null;
    var scopeHistory = [];
    var scopeThreshold = 0;
    var scopeToneOn = false;
    var scopeWaiting = false;
    var waitingStart = 0;
    var scopeRect = null;
    var SCOPE_HISTORY_LEN = 300;

    function el(id) {
        return document.getElementById(id);
    }

    function notifyInfo(text) {
        if (typeof showInfo === 'function') {
            showInfo(text);
        } else {
            console.info(text);
        }
    }

    function notifyError(text) {
        if (typeof showError === 'function') {
            showError(text);
        } else {
            alert(text);
        }
    }

    function parseJsonSafe(response) {
        return response.json().catch(function () { return {}; });
    }

    function postJson(url, payload, timeoutMs) {
        var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
        var timeoutId = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null;

        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
            signal: controller ? controller.signal : undefined,
        }).then(function (response) {
            return parseJsonSafe(response).then(function (data) {
                if (!response.ok) {
                    var msg = data.message || data.error || ('HTTP ' + response.status);
                    throw new Error(msg);
                }
                return data;
            });
        }).catch(function (err) {
            if (err && err.name === 'AbortError') {
                throw new Error('Request timed out');
            }
            throw err;
        }).finally(function () {
            if (timeoutId) clearTimeout(timeoutId);
        });
    }

    function collectConfig() {
        var config = {
            frequency: (el('morseFrequency') && el('morseFrequency').value) || '14.060',
            gain: (el('morseGain') && el('morseGain').value) || '40',
            ppm: (el('morsePPM') && el('morsePPM').value) || '',
            device: (el('deviceSelect') && el('deviceSelect').value) || '0',
            sdr_type: (el('sdrTypeSelect') && el('sdrTypeSelect').value) || 'rtlsdr',
            bias_t: (typeof getBiasTEnabled === 'function') ? getBiasTEnabled() : false,
            detect_mode: (el('morseDetectMode') && el('morseDetectMode').value) || 'goertzel',
            tone_freq: (el('morseToneFreq') && el('morseToneFreq').value) || '700',
            bandwidth_hz: (el('morseBandwidth') && el('morseBandwidth').value) || '200',
            auto_tone_track: !!(el('morseAutoToneTrack') && el('morseAutoToneTrack').checked),
            tone_lock: !!(el('morseToneLock') && el('morseToneLock').checked),
            threshold_mode: (el('morseThresholdMode') && el('morseThresholdMode').value) || 'auto',
            manual_threshold: (el('morseManualThreshold') && el('morseManualThreshold').value) || '0',
            threshold_multiplier: (el('morseThresholdMultiplier') && el('morseThresholdMultiplier').value) || '2.8',
            threshold_offset: (el('morseThresholdOffset') && el('morseThresholdOffset').value) || '0',
            signal_gate: (el('morseSignalGate') && el('morseSignalGate').value) || '0.05',
            wpm_mode: (el('morseWpmMode') && el('morseWpmMode').value) || 'auto',
            wpm: (el('morseWpm') && el('morseWpm').value) || '15',
            wpm_lock: !!(el('morseWpmLock') && el('morseWpmLock').checked),
        };

        // Add rtl_tcp params if using remote SDR
        if (typeof getRemoteSDRConfig === 'function') {
            var remoteConfig = getRemoteSDRConfig();
            if (remoteConfig) {
                config.rtl_tcp_host = remoteConfig.host;
                config.rtl_tcp_port = remoteConfig.port;
            }
        }

        return config;
    }

    function persistSettings() {
        try {
            var payload = {
                frequency: (el('morseFrequency') && el('morseFrequency').value) || '14.060',
                gain: (el('morseGain') && el('morseGain').value) || '40',
                ppm: (el('morsePPM') && el('morsePPM').value) || '',
                detect_mode: (el('morseDetectMode') && el('morseDetectMode').value) || 'goertzel',
                tone_freq: (el('morseToneFreq') && el('morseToneFreq').value) || '700',
                bandwidth_hz: (el('morseBandwidth') && el('morseBandwidth').value) || '200',
                auto_tone_track: !!(el('morseAutoToneTrack') && el('morseAutoToneTrack').checked),
                tone_lock: !!(el('morseToneLock') && el('morseToneLock').checked),
                threshold_mode: (el('morseThresholdMode') && el('morseThresholdMode').value) || 'auto',
                manual_threshold: (el('morseManualThreshold') && el('morseManualThreshold').value) || '0',
                threshold_multiplier: (el('morseThresholdMultiplier') && el('morseThresholdMultiplier').value) || '2.8',
                threshold_offset: (el('morseThresholdOffset') && el('morseThresholdOffset').value) || '0',
                signal_gate: (el('morseSignalGate') && el('morseSignalGate').value) || '0.05',
                wpm_mode: (el('morseWpmMode') && el('morseWpmMode').value) || 'auto',
                wpm: (el('morseWpm') && el('morseWpm').value) || '15',
                wpm_lock: !!(el('morseWpmLock') && el('morseWpmLock').checked),
                show_raw: !!(el('morseShowRaw') && el('morseShowRaw').checked),
                show_diag: !!(el('morseShowDiag') && el('morseShowDiag').checked),
            };
            localStorage.setItem(SETTINGS_KEY, JSON.stringify(payload));
        } catch (_) {
            // Ignore local storage errors.
        }
    }

    function applySettings(settings) {
        if (!settings || typeof settings !== 'object') return;

        if (el('morseFrequency') && settings.frequency !== undefined) el('morseFrequency').value = settings.frequency;
        if (el('morseGain') && settings.gain !== undefined) el('morseGain').value = settings.gain;
        if (el('morsePPM') && settings.ppm !== undefined) el('morsePPM').value = settings.ppm;
        if (el('morseToneFreq') && settings.tone_freq !== undefined) el('morseToneFreq').value = settings.tone_freq;
        if (el('morseBandwidth') && settings.bandwidth_hz !== undefined) el('morseBandwidth').value = settings.bandwidth_hz;
        if (el('morseThresholdMode') && settings.threshold_mode !== undefined) el('morseThresholdMode').value = settings.threshold_mode;
        if (el('morseManualThreshold') && settings.manual_threshold !== undefined) el('morseManualThreshold').value = settings.manual_threshold;
        if (el('morseThresholdMultiplier') && settings.threshold_multiplier !== undefined) el('morseThresholdMultiplier').value = settings.threshold_multiplier;
        if (el('morseThresholdOffset') && settings.threshold_offset !== undefined) el('morseThresholdOffset').value = settings.threshold_offset;
        if (el('morseSignalGate') && settings.signal_gate !== undefined) el('morseSignalGate').value = settings.signal_gate;
        if (el('morseWpmMode') && settings.wpm_mode !== undefined) el('morseWpmMode').value = settings.wpm_mode;
        if (el('morseWpm') && settings.wpm !== undefined) el('morseWpm').value = settings.wpm;

        if (el('morseAutoToneTrack') && settings.auto_tone_track !== undefined) el('morseAutoToneTrack').checked = !!settings.auto_tone_track;
        if (el('morseToneLock') && settings.tone_lock !== undefined) el('morseToneLock').checked = !!settings.tone_lock;
        if (el('morseWpmLock') && settings.wpm_lock !== undefined) el('morseWpmLock').checked = !!settings.wpm_lock;
        if (el('morseShowRaw') && settings.show_raw !== undefined) el('morseShowRaw').checked = !!settings.show_raw;
        if (el('morseShowDiag') && settings.show_diag !== undefined) el('morseShowDiag').checked = !!settings.show_diag;

        if (settings.detect_mode) {
            setDetectMode(settings.detect_mode);
        }
        updateToneLabel((el('morseToneFreq') && el('morseToneFreq').value) || '700');
        updateWpmLabel((el('morseWpm') && el('morseWpm').value) || '15');
        onThresholdModeChange();
        onWpmModeChange();
        toggleRawPanel();
        toggleDiagPanel();
    }

    function loadSettings() {
        try {
            var raw = localStorage.getItem(SETTINGS_KEY);
            if (!raw) {
                if (el('morseShowDiag')) el('morseShowDiag').checked = true;
                toggleDiagPanel();
                persistSettings();
                return;
            }
            var parsed = JSON.parse(raw);
            applySettings(parsed);
        } catch (_) {
            // Ignore malformed settings.
            if (el('morseShowDiag')) el('morseShowDiag').checked = true;
            toggleDiagPanel();
        }
    }

    function bindControls() {
        if (state.controlsBound) return;
        state.controlsBound = true;

        var ids = [
            'morseFrequency', 'morseGain', 'morsePPM', 'morseDetectMode', 'morseToneFreq',
            'morseBandwidth', 'morseAutoToneTrack', 'morseToneLock', 'morseThresholdMode',
            'morseManualThreshold', 'morseThresholdMultiplier', 'morseThresholdOffset',
            'morseSignalGate', 'morseWpmMode', 'morseWpm', 'morseWpmLock',
            'morseShowRaw', 'morseShowDiag'
        ];

        ids.forEach(function (id) {
            var node = el(id);
            if (!node) return;
            node.addEventListener('change', persistSettings);
            if (node.tagName === 'INPUT' && (node.type === 'range' || node.type === 'number' || node.type === 'text')) {
                node.addEventListener('input', persistSettings);
            }
        });

        if (el('morseShowRaw')) {
            el('morseShowRaw').addEventListener('change', toggleRawPanel);
        }
        if (el('morseShowDiag')) {
            el('morseShowDiag').addEventListener('change', toggleDiagPanel);
        }
    }

    function setLifecycle(next) {
        state.lifecycle = next;
        updateUI();
    }

    function isTransition() {
        return state.lifecycle === 'starting' || state.lifecycle === 'stopping';
    }

    function isActive() {
        return state.lifecycle === 'starting' || state.lifecycle === 'running' || state.lifecycle === 'stopping';
    }

    function init() {
        bindControls();

        if (state.initialized) {
            checkStatus();
            return;
        }

        state.initialized = true;
        loadSettings();
        updateUI();
        checkStatus();

        if (!state.statusPollTimer) {
            state.statusPollTimer = setInterval(checkStatus, STATUS_POLL_MS);
        }
    }

    function destroy() {
        if (state.statusPollTimer) {
            clearInterval(state.statusPollTimer);
            state.statusPollTimer = null;
        }

        if (state.lifecycle === 'running' || state.lifecycle === 'starting') {
            stop({ silent: true }).catch(function () { });
        } else {
            disconnectSSE();
            stopScope();
        }

        state.initialized = false;
    }

    function start() {
        if (state.lifecycle === 'running' || state.lifecycle === 'starting') {
            return Promise.resolve({ status: 'already_running' });
        }

        if (state.lifecycle === 'stopping' && state.stopPromise) {
            return state.stopPromise.then(function () {
                return start();
            });
        }

        if (typeof checkDeviceAvailability === 'function' && !checkDeviceAvailability('morse')) {
            return Promise.resolve({ status: 'blocked' });
        }

        clearDiagLog();
        clearDecodedText();
        clearRawText();
        appendDiagLine('[start] requesting decoder startup...');

        var payload = collectConfig();
        persistSettings();

        var seq = ++state.startSeq;
        setLifecycle('starting');

        return postJson('/morse/start', payload, START_TIMEOUT_MS)
            .then(function (data) {
                if (seq !== state.startSeq) {
                    return data;
                }

                if (data.status !== 'started') {
                    throw new Error(data.message || 'Failed to start Morse decoder');
                }

                if (typeof reserveDevice === 'function') {
                    var parsedDevice = Number(payload.device);
                    if (Number.isFinite(parsedDevice)) {
                        reserveDevice(parsedDevice, 'morse');
                    }
                }

                setLifecycle('running');
                connectSSE();
                startScope();
                setStatusText('Listening');
                applyMetrics(data.config || {}, true);
                appendDiagLine('[start] decoder started');
                notifyInfo('Morse decoder started');
                return data;
            })
            .catch(function (err) {
                if (seq !== state.startSeq) {
                    return { status: 'stale' };
                }
                var initialErrorMsg = String(err && err.message ? err.message : err);
                if (initialErrorMsg === 'Request timed out while waiting for decoder startup') {
                    return fetch('/morse/status')
                        .then(function (r) { return parseJsonSafe(r); })
                        .then(function (statusData) {
                            var statusError = statusData && (statusData.error || statusData.message);
                            var resolvedError = statusError ? String(statusError) : initialErrorMsg;
                            setLifecycle('error');
                            setStatusText('Start failed');
                            appendDiagLine('[start] failed: ' + resolvedError);
                            notifyError('Failed to start Morse decoder: ' + resolvedError);
                            return { status: 'error', message: resolvedError };
                        })
                        .catch(function () {
                            setLifecycle('error');
                            setStatusText('Start failed');
                            appendDiagLine('[start] failed: ' + initialErrorMsg);
                            notifyError('Failed to start Morse decoder: ' + initialErrorMsg);
                            return { status: 'error', message: initialErrorMsg };
                        });
                }
                setLifecycle('error');
                setStatusText('Start failed');
                appendDiagLine('[start] failed: ' + initialErrorMsg);
                notifyError('Failed to start Morse decoder: ' + initialErrorMsg);
                return { status: 'error', message: initialErrorMsg };
            });
    }

    function stop(options) {
        options = options || {};

        if (state.stopPromise) {
            return state.stopPromise;
        }

        var currentlyActive = isActive();
        if (!currentlyActive && !options.force) {
            disconnectSSE();
            stopScope();
            setLifecycle('idle');
            if (typeof releaseDevice === 'function') releaseDevice('morse');
            return Promise.resolve({ status: 'not_running' });
        }

        state.startSeq += 1; // invalidate in-flight start responses
        setLifecycle('stopping');
        setStatusText('Stopping...');

        disconnectSSE();
        stopScope();
        if (typeof releaseDevice === 'function') {
            releaseDevice('morse');
        }

        var stopPromise;
        if (options.skipRequest) {
            stopPromise = Promise.resolve({ status: 'skipped' });
        } else {
            stopPromise = postJson('/morse/stop', {}, LOCAL_STOP_TIMEOUT_MS)
                .catch(function (err) {
                    appendDiagLine('[stop] ' + (err && err.message ? err.message : err));
                    return { status: 'error', message: String(err && err.message ? err.message : err) };
                });
        }

        state.stopPromise = stopPromise.then(function (data) {
            if (data && data.stop_ms !== undefined) {
                state.lastMetrics.stop_ms = Number(data.stop_ms);
                updateMetricLabel('morseMetricStopMs', 'STOP ' + Math.round(state.lastMetrics.stop_ms) + ' ms');
            }

            if (data && Array.isArray(data.cleanup_steps)) {
                appendDiagLine('[stop] ' + data.cleanup_steps.join(' | '));
            }
            if (data && Array.isArray(data.alive) && data.alive.length) {
                appendDiagLine('[stop] still alive: ' + data.alive.join(', '));
            }

            if (!data || data.status === 'error') {
                return data;  // Stay in 'stopping' — let checkStatus resolve
            }
            setLifecycle('idle');
            setStatusText('Standby');
            return data;
        }).finally(function () {
            state.stopPromise = null;
        });

        return state.stopPromise;
    }

    function checkStatus() {
        if (!state.initialized) return;
        if (state.stopPromise) return;  // Don't poll during in-flight stop

        fetch('/morse/status')
            .then(function (r) { return parseJsonSafe(r); })
            .then(function (data) {
                if (!data || typeof data !== 'object') return;
                // Guard against in-flight polls that were dispatched before stop
                if (state.stopPromise) return;

                if (data.running) {
                    if (state.lifecycle === 'stopping') return;  // Don't override post-timeout stopping
                    if (data.state === 'starting') {
                        setLifecycle('starting');
                    } else if (data.state === 'stopping') {
                        setLifecycle('stopping');
                    } else {
                        setLifecycle('running');
                    }

                    if (!state.eventSource) connectSSE();
                    if (!scopeAnim && state.lifecycle === 'running') startScope();

                    var message = data.message || (state.lifecycle === 'running' ? 'Listening' : data.state);
                    setStatusText(message);
                    if (data.config) {
                        applyMetrics(data.config, true);
                    }
                } else if (state.lifecycle === 'running' || state.lifecycle === 'starting' || state.lifecycle === 'stopping') {
                    disconnectSSE();
                    stopScope();
                    setLifecycle('idle');
                    setStatusText('Standby');
                    if (typeof releaseDevice === 'function') {
                        releaseDevice('morse');
                    }
                }

                if (data.error) {
                    appendDiagLine('[status] ' + data.error);
                }
            })
            .catch(function () {
                // Ignore status polling errors.
            });
    }

    function connectSSE() {
        disconnectSSE();

        var es = new EventSource('/morse/stream');
        es.onmessage = function (e) {
            try {
                var msg = JSON.parse(e.data);
                handleMessage(msg);
            } catch (_) {
                // Ignore malformed events.
            }
        };

        es.onerror = function () {
            if (state.lifecycle === 'running') {
                appendDiagLine('[stream] reconnecting...');
            }
        };

        state.eventSource = es;
    }

    function disconnectSSE() {
        if (state.eventSource) {
            state.eventSource.close();
            state.eventSource = null;
        }
    }

    function handleMessage(msg) {
        if (!msg || typeof msg !== 'object') return;

        var type = msg.type;

        if (type === 'scope') {
            handleScope(msg);
            applyMetrics(msg, false);
            return;
        }

        if (type === 'morse_char') {
            appendChar(msg.char, msg.morse, msg.timestamp || '--:--:--');
            return;
        }

        if (type === 'morse_space') {
            appendSpace();
            appendRawToken(' // ');
            return;
        }

        if (type === 'morse_element') {
            appendRawToken(msg.element || '');
            return;
        }

        if (type === 'morse_gap') {
            if (msg.gap === 'char') {
                appendRawToken(' / ');
            } else if (msg.gap === 'word') {
                appendRawToken(' // ');
            }
            return;
        }

        if (type === 'status') {
            handleStatus(msg);
            return;
        }

        if (type === 'info') {
            appendDiagLine(msg.text || '[info]');
            return;
        }

        if (type === 'error') {
            appendDiagLine('[error] ' + (msg.text || 'Decoder error'));
            return;
        }
    }

    function handleStatus(msg) {
        var stateValue = String(msg.state || msg.status || '').toLowerCase();
        if (stateValue === 'starting') {
            setLifecycle('starting');
            setStatusText('Starting...');
        } else if (stateValue === 'running') {
            setLifecycle('running');
            setStatusText('Listening');
        } else if (stateValue === 'stopping') {
            setLifecycle('stopping');
            setStatusText('Stopping...');
        }

        if (msg.metrics) {
            applyMetrics(msg.metrics, false);
        }

        if (msg.stop_ms !== undefined) {
            state.lastMetrics.stop_ms = Number(msg.stop_ms);
            updateMetricLabel('morseMetricStopMs', 'STOP ' + Math.round(state.lastMetrics.stop_ms) + ' ms');
        }

        if (msg.cleanup_steps && Array.isArray(msg.cleanup_steps)) {
            appendDiagLine('[cleanup] ' + msg.cleanup_steps.join(' | '));
        }

        if (msg.alive && Array.isArray(msg.alive) && msg.alive.length) {
            appendDiagLine('[cleanup] alive: ' + msg.alive.join(', '));
        }

        if (msg.status === 'stopped' || stateValue === 'idle') {
            disconnectSSE();
            stopScope();
            setLifecycle('idle');
            setStatusText('Standby');
            if (typeof releaseDevice === 'function') {
                releaseDevice('morse');
            }
        }
    }

    function handleScope(msg) {
        var amps = Array.isArray(msg.amplitudes) ? msg.amplitudes : [];

        if (msg.waiting && amps.length === 0) {
            if (!scopeWaiting) {
                scopeWaiting = true;
                waitingStart = Date.now();
                appendDiagLine('[morse] waiting for PCM stream...');
            }
            var waitElapsedMs = waitingStart ? (Date.now() - waitingStart) : 0;
            if (waitElapsedMs > 10000 && el('morseDiagLog') && el('morseDiagLog').children.length < 6) {
                appendDiagLine('[hint] No samples after 10s. Check SDR device, frequency, and HF direct sampling path.');
            }
        } else if (amps.length > 0) {
            scopeWaiting = false;
            waitingStart = 0;
        }

        for (var i = 0; i < amps.length; i++) {
            scopeHistory.push(amps[i]);
            if (scopeHistory.length > SCOPE_HISTORY_LEN) {
                scopeHistory.shift();
            }
        }

        scopeThreshold = Number(msg.threshold) || 0;
        scopeToneOn = !!msg.tone_on;

        if (msg.tone_freq !== undefined) {
            state.lastMetrics.tone_freq = Number(msg.tone_freq) || state.lastMetrics.tone_freq;
        }
        if (msg.wpm !== undefined) {
            state.lastMetrics.wpm = Number(msg.wpm) || state.lastMetrics.wpm;
        }
    }

    function applyMetrics(metrics, fromConfig) {
        if (!metrics || typeof metrics !== 'object') return;

        if (metrics.wpm !== undefined) {
            state.lastMetrics.wpm = Number(metrics.wpm) || state.lastMetrics.wpm;
        }

        if (metrics.tone_freq !== undefined) {
            state.lastMetrics.tone_freq = Number(metrics.tone_freq) || state.lastMetrics.tone_freq;
        }

        if (metrics.level !== undefined) {
            state.lastMetrics.level = Number(metrics.level) || 0;
        }

        if (metrics.threshold !== undefined) {
            state.lastMetrics.threshold = Number(metrics.threshold) || 0;
        } else if (fromConfig && metrics.manual_threshold !== undefined) {
            state.lastMetrics.threshold = Number(metrics.manual_threshold) || state.lastMetrics.threshold;
        }

        if (metrics.noise_floor !== undefined) {
            state.lastMetrics.noise_floor = Number(metrics.noise_floor) || 0;
        }

        if (metrics.snr !== undefined) {
            state.lastMetrics.snr = Number(metrics.snr) || 0;
        }
        if (metrics.noise_ref !== undefined) {
            state.lastMetrics.noise_ref = Number(metrics.noise_ref) || 0;
        }
        if (metrics.snr_on !== undefined) {
            state.lastMetrics.snr_on = Number(metrics.snr_on) || 0;
        }
        if (metrics.snr_off !== undefined) {
            state.lastMetrics.snr_off = Number(metrics.snr_off) || 0;
        }

        updateMetricLabel('morseMetricTone', 'TONE ' + Math.round(state.lastMetrics.tone_freq || 700) + ' Hz');
        updateMetricLabel('morseMetricLevel', 'SNR ' + (state.lastMetrics.snr || 0).toFixed(2) + ' (on>' + (state.lastMetrics.snr_on || 0).toFixed(2) + ' off>' + (state.lastMetrics.snr_off || 0).toFixed(2) + ')');
        updateMetricLabel('morseMetricThreshold', 'THRESH ' + (state.lastMetrics.threshold || 0).toFixed(2));
        updateMetricLabel('morseMetricNoise', 'NOISE_REF ' + (state.lastMetrics.noise_ref || 0).toFixed(4));

        var toneScope = el('morseScopeToneLabel');
        if (toneScope) {
            toneScope.textContent = scopeToneOn ? 'ON' : '--';
        }

        var thresholdScope = el('morseScopeThreshLabel');
        if (thresholdScope) {
            thresholdScope.textContent = state.lastMetrics.threshold > 0
                ? Math.round(state.lastMetrics.threshold)
                : '--';
        }

        var barWpm = el('morseStatusBarWpm');
        if (barWpm) barWpm.textContent = Math.round(state.lastMetrics.wpm || 0) + ' WPM';

        var barTone = el('morseStatusBarTone');
        if (barTone) barTone.textContent = Math.round(state.lastMetrics.tone_freq || 700) + ' Hz';

        var metricState = el('morseMetricState');
        if (metricState) metricState.textContent = 'STATE ' + state.lifecycle;
    }

    function appendChar(ch, morse, timestamp) {
        if (!ch) return;

        state.charCount += 1;
        state.decodedLog.push({
            timestamp: timestamp || '--:--:--',
            morse: morse || '',
            char: ch,
        });

        var panel = el('morseDecodedText');
        if (panel) {
            var span = document.createElement('span');
            span.className = 'morse-char';
            span.textContent = ch;
            span.title = (morse || '') + ' (' + (timestamp || '--:--:--') + ')';
            panel.appendChild(span);
            panel.scrollTop = panel.scrollHeight;
        }

        updateCharCounts();
    }

    function appendSpace() {
        var panel = el('morseDecodedText');
        if (!panel) return;

        var span = document.createElement('span');
        span.className = 'morse-word-space';
        span.textContent = ' ';
        panel.appendChild(span);
        panel.scrollTop = panel.scrollHeight;
    }

    function appendRawToken(token) {
        if (!token) return;
        state.rawLog.push(token);
        if (state.rawLog.length > 2000) {
            state.rawLog.splice(0, state.rawLog.length - 2000);
        }

        var rawText = el('morseRawText');
        if (rawText) {
            rawText.textContent = state.rawLog.join('');
            rawText.scrollTop = rawText.scrollHeight;
        }
    }

    function clearRawText() {
        state.rawLog = [];
        var rawText = el('morseRawText');
        if (rawText) rawText.textContent = '';
    }

    function updateCharCounts() {
        var countEl = el('morseCharCount');
        if (countEl) countEl.textContent = state.charCount + ' chars';

        var barChars = el('morseStatusBarChars');
        if (barChars) barChars.textContent = state.charCount + ' chars decoded';
    }

    function clearDecodedText() {
        state.charCount = 0;
        state.decodedLog = [];

        var panel = el('morseDecodedText');
        if (panel) panel.innerHTML = '';

        updateCharCounts();
    }

    function startScope() {
        var canvas = el('morseScopeCanvas');
        if (!canvas) return;

        var rect = canvas.getBoundingClientRect();
        if (!rect.width) return;

        var dpr = window.devicePixelRatio || 1;
        canvas.width = Math.max(1, Math.floor(rect.width * dpr));
        canvas.height = Math.max(1, Math.floor(80 * dpr));
        canvas.style.height = '80px';

        scopeCtx = canvas.getContext('2d');
        if (!scopeCtx) return;
        scopeCtx.setTransform(1, 0, 0, 1, 0, 0);
        scopeCtx.scale(dpr, dpr);

        scopeHistory = [];
        scopeRect = rect;

        if (scopeAnim) {
            cancelAnimationFrame(scopeAnim);
            scopeAnim = null;
        }

        function draw() {
            if (!scopeCtx || !scopeRect) return;

            var w = scopeRect.width;
            var h = 80;

            scopeCtx.fillStyle = '#050510';
            scopeCtx.fillRect(0, 0, w, h);

            if (scopeHistory.length === 0) {
                if (scopeWaiting) {
                    var elapsed = waitingStart ? (Date.now() - waitingStart) / 1000 : 0;
                    var text = elapsed > 10 ? 'No audio data - check SDR log below' : 'Awaiting SDR data...';
                    scopeCtx.fillStyle = elapsed > 10 ? '#887744' : '#556677';
                    scopeCtx.font = '12px monospace';
                    scopeCtx.textAlign = 'center';
                    scopeCtx.fillText(text, w / 2, h / 2);
                    scopeCtx.textAlign = 'start';
                }
                scopeAnim = requestAnimationFrame(draw);
                return;
            }

            var maxVal = 0;
            for (var i = 0; i < scopeHistory.length; i++) {
                if (scopeHistory[i] > maxVal) maxVal = scopeHistory[i];
            }
            if (maxVal <= 0) maxVal = 1;

            var barWidth = w / SCOPE_HISTORY_LEN;
            var thresholdNorm = scopeThreshold / maxVal;

            for (var j = 0; j < scopeHistory.length; j++) {
                var norm = scopeHistory[j] / maxVal;
                var barHeight = norm * (h - 10);
                var x = j * barWidth;
                var y = h - barHeight;

                scopeCtx.fillStyle = scopeHistory[j] > scopeThreshold ? '#00ff88' : '#334455';
                scopeCtx.fillRect(x, y, Math.max(barWidth - 1, 1), barHeight);
            }

            if (scopeThreshold > 0) {
                var yThresh = h - (thresholdNorm * (h - 10));
                scopeCtx.strokeStyle = '#ff4444';
                scopeCtx.lineWidth = 1;
                scopeCtx.setLineDash([4, 4]);
                scopeCtx.beginPath();
                scopeCtx.moveTo(0, yThresh);
                scopeCtx.lineTo(w, yThresh);
                scopeCtx.stroke();
                scopeCtx.setLineDash([]);
            }

            if (scopeToneOn) {
                scopeCtx.fillStyle = '#00ff88';
                scopeCtx.beginPath();
                scopeCtx.arc(w - 12, 12, 5, 0, Math.PI * 2);
                scopeCtx.fill();
            }

            scopeAnim = requestAnimationFrame(draw);
        }

        draw();
    }

    function stopScope() {
        var canvas = el('morseScopeCanvas');
        if (canvas) {
            var ctx = canvas.getContext('2d');
            if (ctx) {
                var w = canvas.clientWidth || canvas.width || 1;
                var h = canvas.clientHeight || 80;
                ctx.clearRect(0, 0, w, h);
                ctx.fillStyle = '#050510';
                ctx.fillRect(0, 0, w, h);
            }
        }
        if (scopeAnim) {
            cancelAnimationFrame(scopeAnim);
            scopeAnim = null;
        }
        scopeCtx = null;
        scopeRect = null;
        scopeHistory = [];
        scopeWaiting = false;
        waitingStart = 0;
        drawIdleScope();
    }

    // Idle: what the scope will show, faintly. "CQ" keyed as a tone envelope,
    // the threshold that separates marks from gaps, and a caption.
    var IDLE_PATTERN = '-.-. --.-';

    function drawIdleScope() {
        var canvas = el('morseScopeCanvas');
        if (!canvas || scopeAnim) return;
        var rect = canvas.getBoundingClientRect();
        if (!rect.width) return;
        var dpr = window.devicePixelRatio || 1;
        var w = rect.width, h = 80;
        canvas.width = Math.max(1, Math.floor(w * dpr));
        canvas.height = Math.max(1, Math.floor(h * dpr));
        var ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.fillStyle = '#050510';
        ctx.fillRect(0, 0, w, h);

        // Units: dit 1, dah 3, gap 1 within a letter, 3 between letters
        var units = [];
        for (var i = 0; i < IDLE_PATTERN.length; i++) {
            var c = IDLE_PATTERN[i];
            if (c === ' ') { units.push(0, 0); continue; }
            var len = c === '-' ? 3 : 1;
            for (var k = 0; k < len; k++) units.push(1);
            units.push(0);
        }
        var unitW = Math.min(18, (w * 0.6) / units.length);
        var x0 = (w - unitW * units.length) / 2;
        var threshY = h * 0.55;
        for (var u = 0; u < units.length; u++) {
            var on = units[u] === 1;
            var barH = on ? h * 0.7 : h * 0.12;
            ctx.fillStyle = on ? 'rgba(0, 255, 136, 0.22)' : 'rgba(51, 68, 85, 0.5)';
            ctx.fillRect(x0 + u * unitW, h - barH, Math.max(unitW - 1, 1), barH);
        }
        ctx.strokeStyle = 'rgba(255, 68, 68, 0.35)';
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(0, threshY);
        ctx.lineTo(w, threshY);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = '#556677';
        ctx.font = '11px ' + (getComputedStyle(document.body).getPropertyValue('--font-mono') || 'monospace');
        ctx.textAlign = 'left';
        ctx.fillText('Idle · tone level shows here: marks above the threshold, gaps below', 10, 16);
    }

    function appendDiagLine(text) {
        var log = el('morseDiagLog');
        if (!log) return;

        var showDiag = !!(el('morseShowDiag') && el('morseShowDiag').checked);
        if (!showDiag && scopeWaiting) {
            showDiag = true;
        }
        if (!showDiag) return;

        log.style.display = 'block';
        var line = document.createElement('div');
        line.textContent = text;
        log.appendChild(line);

        while (log.children.length > 32) {
            log.removeChild(log.firstChild);
        }
        log.scrollTop = log.scrollHeight;
    }

    function clearDiagLog() {
        var log = el('morseDiagLog');
        if (!log) return;
        log.innerHTML = '';
        log.style.display = 'none';
    }

    function toggleDiagPanel() {
        var log = el('morseDiagLog');
        if (!log) return;

        var showDiag = !!(el('morseShowDiag') && el('morseShowDiag').checked);
        if (!showDiag) {
            log.style.display = 'none';
        } else if (log.children.length > 0) {
            log.style.display = 'block';
        }
    }

    function toggleRawPanel() {
        var panel = el('morseRawPanel');
        if (!panel) return;

        var showRaw = !!(el('morseShowRaw') && el('morseShowRaw').checked);
        panel.style.display = showRaw ? 'block' : 'none';
    }

    function setStatusText(text) {
        var statusText = el('morseStatusText');
        if (statusText) statusText.textContent = text;
    }

    function updateMetricLabel(id, text) {
        var node = el(id);
        if (node) node.textContent = text;
    }

    function updateUI() {
        var startBtn = el('morseStartBtn');
        var stopBtn = el('morseStopBtn');
        var indicator = el('morseStatusIndicator');

        var running = state.lifecycle === 'running';
        var starting = state.lifecycle === 'starting';
        var stopping = state.lifecycle === 'stopping';
        var busy = isTransition();

        if (startBtn) {
            startBtn.style.display = running || starting ? 'none' : 'block';
            startBtn.disabled = busy;
        }

        if (stopBtn) {
            stopBtn.style.display = (running || starting || stopping) ? 'block' : 'none';
            stopBtn.disabled = stopping;
            stopBtn.textContent = stopping ? 'Stopping...' : 'Stop Decoder';
        }

        if (indicator) {
            if (running) {
                indicator.style.background = '#00ff88';
            } else if (starting || stopping) {
                indicator.style.background = '#ffaa00';
            } else if (state.lifecycle === 'error') {
                indicator.style.background = '#ff5555';
            } else {
                indicator.style.background = 'var(--text-dim)';
            }
        }

        if (state.lifecycle === 'idle') setStatusText('Standby');
        if (state.lifecycle === 'starting') setStatusText('Starting...');
        if (state.lifecycle === 'running') setStatusText('Listening');
        if (state.lifecycle === 'stopping') setStatusText('Stopping...');
        if (state.lifecycle === 'error') setStatusText('Error');

        var scopePanel = el('morseScopePanel');
        if (scopePanel) scopePanel.style.display = 'block';
        if (!running && !starting) drawIdleScope();

        var outputPanel = el('morseOutputPanel');
        if (outputPanel) outputPanel.style.display = 'block';

        var scopeStatus = el('morseScopeStatusLabel');
        if (scopeStatus) {
            if (running) {
                scopeStatus.textContent = 'ACTIVE';
                scopeStatus.style.color = '#0f0';
            } else if (starting) {
                scopeStatus.textContent = 'STARTING';
                scopeStatus.style.color = '#ffaa00';
            } else if (stopping) {
                scopeStatus.textContent = 'STOPPING';
                scopeStatus.style.color = '#ffaa00';
            } else {
                scopeStatus.textContent = 'IDLE';
                scopeStatus.style.color = '#444';
            }
        }

        var stateBar = el('morseStatusBarState');
        if (stateBar) {
            stateBar.textContent = state.lifecycle.toUpperCase();
        }

        var metricState = el('morseMetricState');
        if (metricState) {
            metricState.textContent = 'STATE ' + state.lifecycle;
        }

        var controls = [
            'morseFrequency', 'morseGain', 'morsePPM', 'morseToneFreq', 'morseBandwidth',
            'morseAutoToneTrack', 'morseToneLock', 'morseThresholdMode', 'morseManualThreshold',
            'morseThresholdMultiplier', 'morseThresholdOffset', 'morseSignalGate', 'morseWpmMode',
            'morseWpm', 'morseWpmLock', 'morseShowRaw', 'morseShowDiag',
            'morseCalibrateBtn', 'morseDecodeFileBtn', 'morseFileInput'
        ];

        controls.forEach(function (id) {
            var node = el(id);
            if (!node) return;
            node.disabled = busy;
        });

        toggleRawPanel();
        toggleDiagPanel();
    }

    function updateToneLabel(value) {
        var toneLabel = el('morseToneFreqLabel');
        if (toneLabel) toneLabel.textContent = String(value);
        persistSettings();
    }

    function updateWpmLabel(value) {
        var wpmLabel = el('morseWpmLabel');
        if (wpmLabel) wpmLabel.textContent = String(value);
        persistSettings();
    }

    function onThresholdModeChange() {
        var mode = (el('morseThresholdMode') && el('morseThresholdMode').value) || 'auto';
        var manualRow = el('morseManualThresholdRow');
        var autoRow = el('morseThresholdAutoRow');
        var offsetRow = el('morseThresholdOffsetRow');

        if (manualRow) manualRow.style.display = mode === 'manual' ? 'block' : 'none';
        if (autoRow) autoRow.style.display = mode === 'manual' ? 'none' : 'block';
        if (offsetRow) offsetRow.style.display = mode === 'manual' ? 'none' : 'block';

        persistSettings();
    }

    function onWpmModeChange() {
        var mode = (el('morseWpmMode') && el('morseWpmMode').value) || 'auto';
        var manualRow = el('morseWpmManualRow');
        if (manualRow) {
            manualRow.style.display = mode === 'manual' ? 'block' : 'none';
        }
        persistSettings();
    }

    function setFreq(mhz) {
        var freq = el('morseFrequency');
        if (freq) {
            freq.value = String(mhz);
            persistSettings();
        }
    }

    function exportTxt() {
        var text = state.decodedLog.map(function (entry) { return entry.char; }).join('');
        downloadFile('morse_decoded.txt', text, 'text/plain');
    }

    function exportCsv() {
        var lines = ['timestamp,morse,character'];
        state.decodedLog.forEach(function (entry) {
            lines.push(entry.timestamp + ',"' + entry.morse + '",' + entry.char);
        });
        downloadFile('morse_decoded.csv', lines.join('\n'), 'text/csv');
    }

    function copyToClipboard() {
        var text = state.decodedLog.map(function (entry) { return entry.char; }).join('');
        if (!navigator.clipboard || !navigator.clipboard.writeText) return;

        navigator.clipboard.writeText(text).then(function () {
            var btn = el('morseCopyBtn');
            if (!btn) return;
            var original = btn.textContent;
            btn.textContent = 'Copied!';
            setTimeout(function () {
                btn.textContent = original;
            }, 1200);
        }).catch(function () {
            // Ignore clipboard failures.
        });
    }

    function downloadFile(filename, content, type) {
        var blob = new Blob([content], { type: type });
        var url = URL.createObjectURL(blob);
        var anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        anchor.click();
        URL.revokeObjectURL(url);
    }

    function calibrate() {
        if (state.lifecycle !== 'running') {
            notifyInfo('Morse decoder is not running');
            return;
        }

        postJson('/morse/calibrate', {}, 2000)
            .then(function () {
                appendDiagLine('[calibrate] estimator reset requested');
                notifyInfo('Morse estimator reset');
            })
            .catch(function (err) {
                notifyError('Calibration failed: ' + (err && err.message ? err.message : err));
            });
    }

    function decodeFile() {
        var input = el('morseFileInput');
        if (!input || !input.files || !input.files[0]) {
            notifyError('Select a WAV file first.');
            return;
        }

        var file = input.files[0];
        var config = collectConfig();
        var formData = new FormData();
        formData.append('audio', file);

        formData.append('tone_freq', config.tone_freq);
        formData.append('wpm', config.wpm);
        formData.append('bandwidth_hz', config.bandwidth_hz);
        formData.append('auto_tone_track', String(config.auto_tone_track));
        formData.append('tone_lock', String(config.tone_lock));
        formData.append('threshold_mode', config.threshold_mode);
        formData.append('manual_threshold', config.manual_threshold);
        formData.append('threshold_multiplier', config.threshold_multiplier);
        formData.append('threshold_offset', config.threshold_offset);
        formData.append('wpm_mode', config.wpm_mode);
        formData.append('wpm_lock', String(config.wpm_lock));
        formData.append('signal_gate', config.signal_gate);

        var decodeBtn = el('morseDecodeFileBtn');
        if (decodeBtn) {
            decodeBtn.disabled = true;
            decodeBtn.textContent = 'Decoding...';
        }

        fetch('/morse/decode-file', {
            method: 'POST',
            body: formData,
        }).then(function (response) {
            return parseJsonSafe(response).then(function (data) {
                if (!response.ok || data.status !== 'ok') {
                    throw new Error(data.message || ('HTTP ' + response.status));
                }

                clearDecodedText();
                clearRawText();

                var text = String(data.text || '');
                var raw = String(data.raw || '');

                if (text.length > 0) {
                    for (var i = 0; i < text.length; i++) {
                        if (text[i] === ' ') {
                            appendSpace();
                        } else {
                            appendChar(text[i], '', '--:--:--');
                        }
                    }
                }

                if (raw) {
                    state.rawLog = [raw];
                    var rawText = el('morseRawText');
                    if (rawText) rawText.textContent = raw;
                }

                if (data.metrics) {
                    applyMetrics(data.metrics, false);
                }

                toggleRawPanel();
                notifyInfo('File decode complete: ' + (data.char_count || 0) + ' chars');
            });
        }).catch(function (err) {
            notifyError('WAV decode failed: ' + (err && err.message ? err.message : err));
        }).finally(function () {
            if (decodeBtn) {
                decodeBtn.disabled = false;
                decodeBtn.textContent = 'Decode File';
            }
        });
    }

    function setDetectMode(mode) {
        var hidden = el('morseDetectMode');
        if (hidden) hidden.value = mode;

        // Update toggle button styles
        var btnGoertzel = el('morseDetectGoertzel');
        var btnEnvelope = el('morseDetectEnvelope');
        if (btnGoertzel && btnEnvelope) {
            if (mode === 'envelope') {
                btnEnvelope.style.background = 'var(--accent)';
                btnEnvelope.style.color = '#000';
                btnGoertzel.style.background = '';
                btnGoertzel.style.color = '';
            } else {
                btnGoertzel.style.background = 'var(--accent)';
                btnGoertzel.style.color = '#000';
                btnEnvelope.style.background = '';
                btnEnvelope.style.color = '';
            }
        }

        // Toggle preset groups
        var hfPresets = el('morseHFPresets');
        var ismPresets = el('morseISMPresets');
        if (hfPresets) hfPresets.style.display = mode === 'envelope' ? 'none' : 'flex';
        if (ismPresets) ismPresets.style.display = mode === 'envelope' ? 'flex' : 'none';

        // Toggle CW detector section (tone freq, bandwidth, tone track -- not needed for envelope)
        var toneGroup = el('morseToneFreqGroup');
        if (toneGroup) toneGroup.style.display = mode === 'envelope' ? 'none' : '';

        // Toggle antenna notes
        var hfNote = el('morseHFNote');
        var envNote = el('morseEnvelopeNote');
        if (hfNote) hfNote.style.display = mode === 'envelope' ? 'none' : '';
        if (envNote) envNote.style.display = mode === 'envelope' ? '' : 'none';

        // Update hint text
        var hint = el('morseDetectHint');
        if (hint) {
            hint.textContent = mode === 'envelope'
                ? 'OOK Envelope: AM demod, RMS detection. For ISM-band OOK/CW.'
                : 'CW Tone: HF bands, USB demod, Goertzel filter. For amateur CW.';
        }

        // Set sensible default frequency when switching modes
        var freqEl = el('morseFrequency');
        if (freqEl) {
            var curFreq = parseFloat(freqEl.value);
            if (mode === 'envelope' && curFreq < 30) {
                freqEl.value = '433.300';
            } else if (mode === 'goertzel' && curFreq > 30) {
                freqEl.value = '14.060';
            }
        }

        // Set WPM default for envelope mode (OOK transmitters tend to be slower)
        var wpmEl = el('morseWpm');
        var wpmLabel = el('morseWpmLabel');
        if (mode === 'envelope' && wpmEl) {
            wpmEl.value = '12';
            if (wpmLabel) wpmLabel.textContent = '12';
        }

        persistSettings();
    }

    return {
        init: init,
        destroy: destroy,
        start: start,
        stop: stop,
        setFreq: setFreq,
        setDetectMode: setDetectMode,
        exportTxt: exportTxt,
        exportCsv: exportCsv,
        copyToClipboard: copyToClipboard,
        clearText: clearDecodedText,
        calibrate: calibrate,
        decodeFile: decodeFile,
        updateToneLabel: updateToneLabel,
        updateWpmLabel: updateWpmLabel,
        onThresholdModeChange: onThresholdModeChange,
        onWpmModeChange: onWpmModeChange,
        isActive: isActive,
    };
})();
