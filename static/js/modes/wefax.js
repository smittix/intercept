/**
 * WeFax (Weather Fax) decoder module.
 *
 * IIFE providing start/stop controls, station selector, broadcast
 * schedule timeline, live image preview, decoded image gallery,
 * and audio waveform scope.
 */
var WeFax = (function () {
    'use strict';

    var state = {
        running: false,
        initialized: false,
        eventSource: null,
        stations: [],
        images: [],
        selectedStation: null,
        pollTimer: null,
        countdownInterval: null,
        schedulerPollTimer: null,
        schedulerEnabled: false,
    };

    // ---- Scope state ----

    var scopeCtx = null;
    var scopeAnim = null;
    var scopeHistory = [];
    var scopeWaveBuffer = [];
    var scopeDisplayWave = [];
    var SCOPE_HISTORY_LEN = 200;
    var SCOPE_WAVE_BUFFER_LEN = 2048;
    var SCOPE_WAVE_INPUT_SMOOTH = 0.55;
    var SCOPE_WAVE_DISPLAY_SMOOTH = 0.22;
    var SCOPE_WAVE_IDLE_DECAY = 0.96;
    var scopeRms = 0;
    var scopePeak = 0;
    var scopeTargetRms = 0;
    var scopeTargetPeak = 0;
    var scopeLastWaveAt = 0;
    var scopeLastInputSample = 0;
    var scopeImageBurst = 0;

    // ---- Initialisation ----

    function init() {
        if (state.initialized) {
            // Re-render cached data immediately so UI isn't empty
            if (state.stations.length) renderStationDropdown();
            loadImages();
            return;
        }
        state.initialized = true;
        loadStations();
        loadImages();
        checkSchedulerStatus();
    }

    function destroy() {
        closeImage();
        if (state.running) {
            stop();
        }
        disconnectSSE();
        stopScope();
        stopCountdownTimer();
        stopSchedulerPoll();
        if (state.pollTimer) {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
        }
    }

    // ---- Stations ----

    function loadStations() {
        fetch('/wefax/stations')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'ok' && data.stations) {
                    state.stations = data.stations;
                    renderStationDropdown();
                }
            })
            .catch(function (err) {
                console.error('WeFax: failed to load stations', err);
            });
    }

    function renderStationDropdown() {
        var sel = document.getElementById('wefaxStation');
        if (!sel) return;

        // Keep the placeholder
        sel.innerHTML = '<option value="">Select a station...</option>';

        state.stations.forEach(function (s) {
            var opt = document.createElement('option');
            opt.value = s.callsign;
            opt.textContent = s.callsign + ' — ' + s.name + ' (' + s.country + ')';
            sel.appendChild(opt);
        });
    }

    function onStationChange() {
        var sel = document.getElementById('wefaxStation');
        var callsign = sel ? sel.value : '';

        if (!callsign) {
            state.selectedStation = null;
            renderFrequencyDropdown([]);
            renderScheduleTimeline([]);
            renderBroadcastTimeline([]);
            stopCountdownTimer();
            return;
        }

        var station = state.stations.find(function (s) { return s.callsign === callsign; });
        state.selectedStation = station || null;

        if (station) {
            renderFrequencyDropdown(station.frequencies || []);
            // Set IOC/LPM from station defaults
            var iocSel = document.getElementById('wefaxIOC');
            var lpmSel = document.getElementById('wefaxLPM');
            if (iocSel && station.ioc) iocSel.value = String(station.ioc);
            if (lpmSel && station.lpm) lpmSel.value = String(station.lpm);
            renderScheduleTimeline(station.schedule || []);
            renderBroadcastTimeline(station.schedule || []);
            startCountdownTimer();
        }
    }

    function renderFrequencyDropdown(frequencies) {
        var sel = document.getElementById('wefaxFrequency');
        if (!sel) return;

        sel.innerHTML = '';

        if (frequencies.length === 0) {
            var opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'Select station first';
            sel.appendChild(opt);
            return;
        }

        frequencies.forEach(function (f) {
            var opt = document.createElement('option');
            opt.value = String(f.khz);
            opt.textContent = f.khz + ' kHz — ' + f.description;
            sel.appendChild(opt);
        });
    }

    // ---- Start / Stop ----

    function selectedFrequencyReference() {
        var alignCheckbox = document.getElementById('wefaxAutoUsbAlign');
        if (alignCheckbox && !alignCheckbox.checked) {
            return 'dial';
        }
        return 'auto';
    }

    function start() {
        if (state.running) return;

        var freqSel = document.getElementById('wefaxFrequency');
        var freqKhz = freqSel ? parseFloat(freqSel.value) : 0;
        if (!freqKhz || isNaN(freqKhz)) {
            flashStartError();
            return;
        }

        var stationSel = document.getElementById('wefaxStation');
        var station = stationSel ? stationSel.value : '';
        var iocSel = document.getElementById('wefaxIOC');
        var lpmSel = document.getElementById('wefaxLPM');
        var gainInput = document.getElementById('wefaxGain');
        var dsCheckbox = document.getElementById('wefaxDirectSampling');

        var device = (typeof getSelectedDevice === 'function')
            ? parseInt(getSelectedDevice(), 10) || 0 : 0;

        var body = {
            frequency_khz: freqKhz,
            station: station,
            device: device,
            sdr_type: (typeof getSelectedSDRType === 'function') ? getSelectedSDRType() : 'rtlsdr',
            gain: gainInput ? parseFloat(gainInput.value) || 40 : 40,
            ioc: iocSel ? parseInt(iocSel.value, 10) : 576,
            lpm: lpmSel ? parseInt(lpmSel.value, 10) : 120,
            direct_sampling: dsCheckbox ? dsCheckbox.checked : true,
            frequency_reference: selectedFrequencyReference(),
        };

        fetch('/wefax/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'started' || data.status === 'already_running') {
                    var tunedKhz = Number(data.tuned_frequency_khz);
                    if (isNaN(tunedKhz) || tunedKhz <= 0) tunedKhz = freqKhz;
                    state.running = true;
                    updateButtons(true);
                    if (data.usb_offset_applied) {
                        setStatus('Scanning ' + tunedKhz + ' kHz (USB aligned from ' + freqKhz + ' kHz)...');
                    } else {
                        setStatus('Scanning ' + tunedKhz + ' kHz...');
                    }
                    setStripFreq(tunedKhz);
                    connectSSE();
                } else {
                    var errMsg = data.message || 'unknown error';
                    setStatus('Error: ' + errMsg);
                    showStripError(errMsg);
                }
            })
            .catch(function (err) {
                var errMsg = err.message || 'Network error';
                setStatus('Error: ' + errMsg);
                showStripError(errMsg);
            });
    }

    function stop() {
        // Immediate UI feedback before waiting for backend response
        state.running = false;
        updateButtons(false);
        setStatus('Stopping...');
        if (!state.schedulerEnabled) {
            disconnectSSE();
        }

        fetch('/wefax/stop', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function () {
                setStatus('Stopped');
                loadImages();
            })
            .catch(function (err) {
                setStatus('Stopped');
                console.error('WeFax stop error:', err);
                reportActionableError('Stop WeFax', err);
            });
    }

    // ---- SSE ----

    function connectSSE() {
        disconnectSSE();

        var es = new EventSource('/wefax/stream');
        state.eventSource = es;

        es.onmessage = function (evt) {
            try {
                var data = JSON.parse(evt.data);
                if (data.type === 'scope') {
                    applyScopeData(data);
                } else {
                    handleProgress(data);
                }
            } catch (e) { /* ignore keepalives */ }
        };

        es.onerror = function () {
            // EventSource will auto-reconnect
        };

        // Show scope and start animation
        var panel = document.getElementById('wefaxScopePanel');
        if (panel) panel.style.display = 'block';
        initScope();
    }

    function disconnectSSE() {
        if (state.eventSource) {
            state.eventSource.close();
            state.eventSource = null;
        }
        stopScope();
        var panel = document.getElementById('wefaxScopePanel');
        if (panel) panel.style.display = 'none';
    }

    function handleProgress(data) {
        // Handle scheduler events
        if (data.type === 'schedule_capture_start') {
            setStatus('Auto-capture started: ' + (data.broadcast ? data.broadcast.content : ''));
            state.running = true;
            updateButtons(true);
            connectSSE();
            return;
        }
        if (data.type === 'schedule_capture_complete') {
            setStatus('Auto-capture complete');
            loadImages();
            return;
        }
        if (data.type === 'schedule_capture_skipped') {
            setStatus('Broadcast skipped: ' + (data.reason || ''));
            return;
        }

        if (data.type !== 'wefax_progress') return;

        var statusText = data.message || data.status || '';
        setStatus(statusText);

        var dot = document.getElementById('wefaxStripDot');
        if (dot) {
            dot.className = 'wefax-strip-dot ' + (data.status || 'idle');
        }

        var statusEl = document.getElementById('wefaxStripStatus');
        if (statusEl) {
            var labels = {
                scanning: 'Scanning',
                phasing: 'Phasing',
                receiving: 'Receiving',
                complete: 'Complete',
                error: 'Error',
                stopped: 'Idle',
            };
            statusEl.textContent = labels[data.status] || data.status || 'Idle';
        }

        // Update line count
        if (data.line_count) {
            var lineEl = document.getElementById('wefaxStripLines');
            if (lineEl) lineEl.textContent = String(data.line_count);
        }

        // Live preview
        if (data.partial_image) {
            var previewEl = document.getElementById('wefaxLivePreview');
            if (previewEl) {
                previewEl.src = data.partial_image;
                previewEl.style.display = 'block';
            }
            var idleEl = document.getElementById('wefaxIdleState');
            if (idleEl) idleEl.style.display = 'none';
        }

        // Image complete
        if (data.status === 'complete' && data.image) {
            scopeImageBurst = 1.0;
            loadImages();
            setStatus('Image decoded: ' + (data.line_count || '?') + ' lines');
        }

        if (data.status === 'complete') {
            state.running = false;
            updateButtons(false);
            if (!state.schedulerEnabled) {
                disconnectSSE();
            }
        }

        if (data.status === 'error') {
            state.running = false;
            updateButtons(false);
            showStripError(data.message || 'Decode error');
        }

        if (data.status === 'stopped') {
            state.running = false;
            updateButtons(false);
        }
    }

    // ---- Audio Waveform Scope ----

    function initScope() {
        var canvas = document.getElementById('wefaxScopeCanvas');
        if (!canvas) return;

        if (scopeAnim) { cancelAnimationFrame(scopeAnim); scopeAnim = null; }

        resizeScopeCanvas(canvas);
        scopeCtx = canvas.getContext('2d');
        scopeHistory = new Array(SCOPE_HISTORY_LEN).fill(0);
        scopeWaveBuffer = [];
        scopeDisplayWave = [];
        scopeRms = scopePeak = scopeTargetRms = scopeTargetPeak = 0;
        scopeImageBurst = scopeLastWaveAt = scopeLastInputSample = 0;
        drawScope();
    }

    function stopScope() {
        if (scopeAnim) { cancelAnimationFrame(scopeAnim); scopeAnim = null; }
        scopeCtx = null;
        scopeWaveBuffer = [];
        scopeDisplayWave = [];
        scopeHistory = [];
        scopeLastWaveAt = 0;
        scopeLastInputSample = 0;
    }

    function resizeScopeCanvas(canvas) {
        if (!canvas) return;
        var rect = canvas.getBoundingClientRect();
        var dpr = window.devicePixelRatio || 1;
        var width  = Math.max(1, Math.floor(rect.width  * dpr));
        var height = Math.max(1, Math.floor(rect.height * dpr));
        if (canvas.width !== width || canvas.height !== height) {
            canvas.width  = width;
            canvas.height = height;
        }
    }

    function applyScopeData(scopeData) {
        if (!scopeData || typeof scopeData !== 'object') return;

        scopeTargetRms  = Number(scopeData.rms)  || 0;
        scopeTargetPeak = Number(scopeData.peak) || 0;

        if (Array.isArray(scopeData.waveform) && scopeData.waveform.length) {
            for (var i = 0; i < scopeData.waveform.length; i++) {
                var sample = Number(scopeData.waveform[i]);
                if (!isFinite(sample)) continue;
                var normalized = Math.max(-127, Math.min(127, sample)) / 127;
                scopeLastInputSample += (normalized - scopeLastInputSample) * SCOPE_WAVE_INPUT_SMOOTH;
                scopeWaveBuffer.push(scopeLastInputSample);
            }
            if (scopeWaveBuffer.length > SCOPE_WAVE_BUFFER_LEN) {
                scopeWaveBuffer.splice(0, scopeWaveBuffer.length - SCOPE_WAVE_BUFFER_LEN);
            }
            scopeLastWaveAt = performance.now();
        }
    }

    function drawScope() {
        var ctx = scopeCtx;
        if (!ctx) return;

        resizeScopeCanvas(ctx.canvas);
        var W = ctx.canvas.width, H = ctx.canvas.height, midY = H / 2;

        // Phosphor persistence
        ctx.fillStyle = 'rgba(5, 5, 16, 0.26)';
        ctx.fillRect(0, 0, W, H);

        // Smooth RMS/Peak
        scopeRms  += (scopeTargetRms  - scopeRms)  * 0.25;
        scopePeak += (scopeTargetPeak - scopePeak) * 0.15;

        // Rolling envelope
        scopeHistory.push(Math.min(scopeRms / 32768, 1.0));
        if (scopeHistory.length > SCOPE_HISTORY_LEN) scopeHistory.shift();

        // Grid lines
        ctx.strokeStyle = 'rgba(40, 40, 80, 0.4)';
        ctx.lineWidth = 0.8;
        var gx, gy;
        for (var i = 1; i < 8; i++) {
            gx = (W / 8) * i;
            ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, H); ctx.stroke();
        }
        for (var g = 0.25; g < 1; g += 0.25) {
            gy = midY - g * midY;
            var gy2 = midY + g * midY;
            ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(W, gy);
            ctx.moveTo(0, gy2); ctx.lineTo(W, gy2); ctx.stroke();
        }

        // Center baseline
        ctx.strokeStyle = 'rgba(60, 60, 100, 0.5)';
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(0, midY); ctx.lineTo(W, midY); ctx.stroke();

        // Amplitude envelope (amber tint)
        var envStepX = W / (SCOPE_HISTORY_LEN - 1);
        ctx.strokeStyle = 'rgba(255, 170, 0, 0.35)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (var ei = 0; ei < scopeHistory.length; ei++) {
            var ex = ei * envStepX, amp = scopeHistory[ei] * midY * 0.85;
            if (ei === 0) ctx.moveTo(ex, midY - amp); else ctx.lineTo(ex, midY - amp);
        }
        ctx.stroke();
        ctx.beginPath();
        for (var ej = 0; ej < scopeHistory.length; ej++) {
            var ex2 = ej * envStepX, amp2 = scopeHistory[ej] * midY * 0.85;
            if (ej === 0) ctx.moveTo(ex2, midY + amp2); else ctx.lineTo(ex2, midY + amp2);
        }
        ctx.stroke();

        // Waveform trace (amber)
        var wavePoints = Math.min(Math.max(120, Math.floor(W / 3.2)), 420);
        if (scopeWaveBuffer.length > 1) {
            var waveIsFresh = (performance.now() - scopeLastWaveAt) < 700;
            var srcLen = scopeWaveBuffer.length;
            var srcWindow = Math.min(srcLen, 1536);
            var srcStart = srcLen - srcWindow;

            if (scopeDisplayWave.length !== wavePoints) {
                scopeDisplayWave = new Array(wavePoints).fill(0);
            }

            for (var wi = 0; wi < wavePoints; wi++) {
                var a = srcStart + Math.floor((wi / wavePoints) * srcWindow);
                var b = srcStart + Math.floor(((wi + 1) / wavePoints) * srcWindow);
                var start = Math.max(srcStart, Math.min(srcLen - 1, a));
                var end   = Math.max(start + 1, Math.min(srcLen, b));
                var sum = 0, count = 0;
                for (var j = start; j < end; j++) { sum += scopeWaveBuffer[j]; count++; }
                var targetSample = count > 0 ? sum / count : 0;
                scopeDisplayWave[wi] += (targetSample - scopeDisplayWave[wi]) * SCOPE_WAVE_DISPLAY_SMOOTH;
            }

            ctx.strokeStyle = waveIsFresh ? '#ffaa00' : 'rgba(255, 170, 0, 0.45)';
            ctx.lineWidth = 1.7;
            ctx.shadowColor = '#ffaa00';
            ctx.shadowBlur = waveIsFresh ? 6 : 2;

            var stepX = wavePoints > 1 ? W / (wavePoints - 1) : W;
            ctx.beginPath();
            ctx.moveTo(0, midY - scopeDisplayWave[0] * midY * 0.9);
            for (var qi = 1; qi < wavePoints - 1; qi++) {
                var x  = qi * stepX,       y  = midY - scopeDisplayWave[qi]     * midY * 0.9;
                var nx = (qi + 1) * stepX, ny = midY - scopeDisplayWave[qi + 1] * midY * 0.9;
                ctx.quadraticCurveTo(x, y, (x + nx) / 2, (y + ny) / 2);
            }
            ctx.lineTo((wavePoints - 1) * stepX,
                       midY - scopeDisplayWave[wavePoints - 1] * midY * 0.9);
            ctx.stroke();

            if (!waveIsFresh) {
                for (var di = 0; di < scopeDisplayWave.length; di++) {
                    scopeDisplayWave[di] *= SCOPE_WAVE_IDLE_DECAY;
                }
            }
        }
        ctx.shadowBlur = 0;

        // Peak indicator
        var peakNorm = Math.min(scopePeak / 32768, 1.0);
        if (peakNorm > 0.01) {
            var peakY = midY - peakNorm * midY * 0.9;
            ctx.strokeStyle = 'rgba(255, 68, 68, 0.6)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 4]);
            ctx.beginPath(); ctx.moveTo(0, peakY); ctx.lineTo(W, peakY); ctx.stroke();
            ctx.setLineDash([]);
        }

        // Image-decoded flash (amber overlay)
        if (scopeImageBurst > 0.01) {
            ctx.fillStyle = 'rgba(255, 170, 0, ' + (scopeImageBurst * 0.15) + ')';
            ctx.fillRect(0, 0, W, H);
            scopeImageBurst *= 0.88;
        }

        // Label updates
        var rmsLabel = document.getElementById('wefaxScopeRmsLabel');
        var peakLabel = document.getElementById('wefaxScopePeakLabel');
        var statusLabel = document.getElementById('wefaxScopeStatusLabel');
        if (rmsLabel) rmsLabel.textContent = Math.round(scopeRms);
        if (peakLabel) peakLabel.textContent = Math.round(scopePeak);
        if (statusLabel) {
            var fresh = (performance.now() - scopeLastWaveAt) < 700;
            if (fresh && scopeRms > 1300) {
                statusLabel.textContent = 'DEMODULATING';
                statusLabel.style.color = '#ffaa00';
            } else if (fresh && scopeRms > 500) {
                statusLabel.textContent = 'CARRIER';
                statusLabel.style.color = '#cc8800';
            } else if (fresh) {
                statusLabel.textContent = 'QUIET';
                statusLabel.style.color = '#666';
            } else {
                statusLabel.textContent = 'IDLE';
                statusLabel.style.color = '#444';
            }
        }

        scopeAnim = requestAnimationFrame(drawScope);
    }

    // ---- Images ----

    function loadImages() {
        fetch('/wefax/images')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'ok') {
                    state.images = data.images || [];
                    renderImageGallery();
                    var countEl = document.getElementById('wefaxImageCount');
                    if (countEl) countEl.textContent = String(state.images.length);
                    var stripCount = document.getElementById('wefaxStripImageCount');
                    if (stripCount) stripCount.textContent = String(state.images.length);
                }
            })
            .catch(function (err) {
                console.error('WeFax: failed to load images', err);
            });
    }

    function renderImageGallery() {
        var gallery = document.getElementById('wefaxGallery');
        if (!gallery) return;

        if (state.images.length === 0) {
            gallery.innerHTML = '<div class="wefax-gallery-empty">No images decoded yet</div>';
            return;
        }

        var html = '';
        // Show newest first
        var sorted = state.images.slice().reverse();
        sorted.forEach(function (img) {
            var ts = img.timestamp ? new Date(img.timestamp).toLocaleString() : '';
            var station = img.station || '';
            var freq = img.frequency_khz ? (img.frequency_khz + ' kHz') : '';
            html += '<div class="wefax-gallery-item">';
            html += '<img src="' + img.url + '" alt="WeFax" loading="lazy" onclick="WeFax.viewImage(\'' + img.url + '\', \'' + img.filename + '\')">';
            html += '<div class="wefax-gallery-meta">';
            html += '<span>' + station + (freq ? ' ' + freq : '') + '</span>';
            html += '<span>' + ts + '</span>';
            html += '</div>';
            html += '</div>';
        });
        gallery.innerHTML = html;
    }

    async function deleteImage(filename) {
        if (!filename) return;
        const confirmed = await AppFeedback.confirmAction({
            title: 'Delete Image',
            message: 'Delete this image? This cannot be undone.',
            confirmLabel: 'Delete',
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;
        fetch('/wefax/images/' + encodeURIComponent(filename), { method: 'DELETE' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'ok') {
                    closeImage();
                    loadImages();
                } else {
                    setStatus('Delete failed: ' + (data.message || 'unknown error'));
                }
            })
            .catch(function (err) {
                console.error('WeFax delete error:', err);
                reportActionableError('Delete Image', err);
            });
    }

    async function deleteAllImages() {
        const confirmed = await AppFeedback.confirmAction({
            title: 'Delete All Images',
            message: 'Delete all WeFax images? This cannot be undone.',
            confirmLabel: 'Delete All',
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;
        fetch('/wefax/images', { method: 'DELETE' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'ok') {
                    loadImages();
                }
            })
            .catch(function (err) {
                console.error('WeFax delete all error:', err);
                reportActionableError('Delete All Images', err);
            });
    }

    var currentModalUrl = null;
    var currentModalFilename = null;

    function viewImage(url, filename) {
        currentModalUrl = url;
        currentModalFilename = filename || null;

        var modal = document.getElementById('wefaxImageModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'wefaxImageModal';
            modal.className = 'wefax-image-modal';
            modal.innerHTML =
                '<div class="wefax-modal-toolbar">' +
                    '<button class="wefax-modal-btn" id="wefaxModalDownload" title="Download">' +
                        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>' +
                        ' Download' +
                    '</button>' +
                    '<button class="wefax-modal-btn delete" id="wefaxModalDelete" title="Delete">' +
                        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>' +
                        ' Delete' +
                    '</button>' +
                '</div>' +
                '<button class="wefax-modal-close" onclick="WeFax.closeImage()">&times;</button>' +
                '<img src="" alt="WeFax Image">';
            modal.addEventListener('click', function (e) {
                if (e.target === modal) closeImage();
            });
            modal.querySelector('#wefaxModalDownload').addEventListener('click', function (e) {
                e.stopPropagation();
                if (currentModalUrl && currentModalFilename) {
                    var a = document.createElement('a');
                    a.href = currentModalUrl;
                    a.download = currentModalFilename;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                }
            });
            modal.querySelector('#wefaxModalDelete').addEventListener('click', function (e) {
                e.stopPropagation();
                if (currentModalFilename) {
                    deleteImage(currentModalFilename);
                }
            });
            document.body.appendChild(modal);
        }

        modal.querySelector('img').src = url;
        modal.classList.add('show');
    }

    function closeImage() {
        var modal = document.getElementById('wefaxImageModal');
        if (modal) modal.classList.remove('show');
    }

    // ---- Schedule Timeline ----

    function renderScheduleTimeline(schedule) {
        var container = document.getElementById('wefaxScheduleTimeline');
        if (!container) return;

        if (!schedule || schedule.length === 0) {
            container.innerHTML = '<div class="wefax-schedule-empty">Select a station to see broadcast schedule</div>';
            return;
        }

        var now = new Date();
        var nowMin = now.getUTCHours() * 60 + now.getUTCMinutes();

        var html = '<div class="wefax-schedule-list">';
        schedule.forEach(function (entry) {
            var parts = entry.utc.split(':');
            var entryMin = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
            var diff = entryMin - nowMin;
            if (diff < -720) diff += 1440;
            if (diff > 720) diff -= 1440;

            var cls = 'wefax-schedule-entry';
            var badge = '';
            if (diff >= 0 && diff <= entry.duration_min) {
                cls += ' active';
                badge = '<span class="wefax-schedule-badge live">LIVE</span>';
            } else if (diff > 0 && diff <= 60) {
                cls += ' upcoming';
                badge = '<span class="wefax-schedule-badge soon">' + diff + 'm</span>';
            } else if (diff > 0) {
                badge = '<span class="wefax-schedule-badge">' + Math.floor(diff / 60) + 'h ' + (diff % 60) + 'm</span>';
            } else {
                cls += ' past';
            }

            html += '<div class="' + cls + '">';
            html += '<span class="wefax-schedule-time">' + entry.utc + '</span>';
            html += '<span class="wefax-schedule-content">' + entry.content + '</span>';
            html += badge;
            html += '</div>';
        });
        html += '</div>';
        container.innerHTML = html;
    }

    // ---- UI helpers ----

    function updateButtons(running) {
        var startBtn = document.getElementById('wefaxStartBtn');
        var stopBtn = document.getElementById('wefaxStopBtn');
        if (startBtn) startBtn.style.display = running ? 'none' : 'inline-flex';
        if (stopBtn) stopBtn.style.display = running ? 'inline-flex' : 'none';

        var dot = document.getElementById('wefaxStripDot');
        if (dot) dot.className = 'wefax-strip-dot ' + (running ? 'scanning' : 'idle');

        var statusEl = document.getElementById('wefaxStripStatus');
        if (statusEl && !running) statusEl.textContent = 'Idle';
    }

    function setStatus(msg) {
        var el = document.getElementById('wefaxStatusText');
        if (el) el.textContent = msg;
    }

    function setStripFreq(khz) {
        var el = document.getElementById('wefaxStripFreq');
        if (el) el.textContent = String(khz);
    }

    function showStripError(msg) {
        var statusEl = document.getElementById('wefaxStripStatus');
        if (statusEl) {
            statusEl.textContent = 'Error: ' + msg;
            statusEl.style.color = '#ff4444';
        }
        var dot = document.getElementById('wefaxStripDot');
        if (dot) dot.className = 'wefax-strip-dot error';
    }

    function flashStartError() {
        setStatus('Select a station and frequency first');

        // Flash the Start button itself (most visible feedback)
        var startBtn = document.getElementById('wefaxStartBtn');
        if (startBtn) {
            startBtn.classList.add('wefax-strip-btn-error');
            setTimeout(function () {
                startBtn.classList.remove('wefax-strip-btn-error');
            }, 2500);
        }

        // Show error in strip status text (right next to the button)
        var stripStatus = document.getElementById('wefaxStripStatus');
        if (stripStatus) {
            var prevText = stripStatus.textContent;
            stripStatus.textContent = 'Select Station';
            stripStatus.style.color = '#ffaa00';
            setTimeout(function () {
                stripStatus.textContent = prevText || 'Idle';
                stripStatus.style.color = '';
            }, 2500);
        }

        // Also update the schedule panel status
        var statusEl = document.getElementById('wefaxStatusText');
        if (statusEl) {
            statusEl.style.color = '#ffaa00';
            statusEl.style.fontWeight = '600';
            setTimeout(function () {
                statusEl.style.color = '';
                statusEl.style.fontWeight = '';
            }, 2500);
        }

        // Flash station/frequency dropdowns
        var stationSel = document.getElementById('wefaxStation');
        var freqSel = document.getElementById('wefaxFrequency');
        [stationSel, freqSel].forEach(function (el) {
            if (!el) return;
            el.style.borderColor = '#ffaa00';
            el.style.boxShadow = '0 0 4px #ffaa0066';
            setTimeout(function () {
                el.style.borderColor = '';
                el.style.boxShadow = '';
            }, 2500);
        });
    }

    // ---- Broadcast Timeline + Countdown ----

    function renderBroadcastTimeline(schedule) {
        var bar = document.getElementById('wefaxCountdownBar');
        var track = document.getElementById('wefaxTimelineTrack');
        if (!bar || !track) return;

        if (!schedule || schedule.length === 0) {
            bar.style.display = 'none';
            return;
        }

        bar.style.display = 'flex';

        // Clear existing broadcast markers
        var existing = track.querySelectorAll('.wefax-timeline-broadcast');
        for (var i = 0; i < existing.length; i++) {
            existing[i].parentNode.removeChild(existing[i]);
        }

        var now = new Date();
        var nowMin = now.getUTCHours() * 60 + now.getUTCMinutes();

        schedule.forEach(function (entry) {
            var parts = entry.utc.split(':');
            var startMin = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
            var duration = entry.duration_min || 20;
            var leftPct = (startMin / 1440) * 100;
            var widthPct = (duration / 1440) * 100;

            var block = document.createElement('div');
            block.className = 'wefax-timeline-broadcast';
            block.title = entry.utc + ' — ' + entry.content;

            // Mark active broadcasts
            var diff = nowMin - startMin;
            if (diff >= 0 && diff < duration) {
                block.classList.add('active');
            }

            block.style.left = leftPct + '%';
            block.style.width = Math.max(widthPct, 0.3) + '%';
            track.appendChild(block);
        });

        updateTimelineCursor();
    }

    function updateTimelineCursor() {
        var cursor = document.getElementById('wefaxTimelineCursor');
        if (!cursor) return;

        var now = new Date();
        var nowMin = now.getUTCHours() * 60 + now.getUTCMinutes() + now.getUTCSeconds() / 60;
        cursor.style.left = ((nowMin / 1440) * 100) + '%';
    }

    function startCountdownTimer() {
        stopCountdownTimer();
        updateCountdown();
        state.countdownInterval = VisibleInterval.set(function () {
            updateCountdown();
            updateTimelineCursor();
        }, 1000);
    }

    function updateCountdown() {
        var station = state.selectedStation;
        if (!station || !station.schedule || !station.schedule.length) return;

        var now = new Date();
        var nowMin = now.getUTCHours() * 60 + now.getUTCMinutes() + now.getUTCSeconds() / 60;

        // Find next upcoming or currently active broadcast
        var bestDiff = Infinity;
        var bestEntry = null;
        var isActive = false;

        station.schedule.forEach(function (entry) {
            var parts = entry.utc.split(':');
            var startMin = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
            var duration = entry.duration_min || 20;

            // Check if currently active
            var elapsed = nowMin - startMin;
            if (elapsed < 0) elapsed += 1440;
            if (elapsed >= 0 && elapsed < duration) {
                bestEntry = entry;
                bestDiff = 0;
                isActive = true;
                return;
            }

            // Time until start
            var diff = startMin - nowMin;
            if (diff < 0) diff += 1440;
            if (diff < bestDiff) {
                bestDiff = diff;
                bestEntry = entry;
            }
        });

        if (!bestEntry) return;

        var hoursEl = document.getElementById('wefaxCdHours');
        var minsEl = document.getElementById('wefaxCdMins');
        var secsEl = document.getElementById('wefaxCdSecs');
        var contentEl = document.getElementById('wefaxCountdownContent');
        var detailEl = document.getElementById('wefaxCountdownDetail');
        var boxes = document.getElementById('wefaxCountdownBoxes');

        if (isActive) {
            // Show "LIVE" countdown
            var parts = bestEntry.utc.split(':');
            var startMin2 = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
            var duration2 = bestEntry.duration_min || 20;
            var elapsed2 = nowMin - startMin2;
            if (elapsed2 < 0) elapsed2 += 1440;
            var remaining = duration2 - elapsed2;
            var remTotalSec = Math.max(0, Math.floor(remaining * 60));
            var h = Math.floor(remTotalSec / 3600);
            var m = Math.floor((remTotalSec % 3600) / 60);
            var s = remTotalSec % 60;

            if (hoursEl) hoursEl.textContent = String(h).padStart(2, '0');
            if (minsEl) minsEl.textContent = String(m).padStart(2, '0');
            if (secsEl) secsEl.textContent = String(s).padStart(2, '0');
            if (contentEl) contentEl.textContent = bestEntry.content;
            if (detailEl) detailEl.textContent = 'LIVE — ' + bestEntry.utc + ' UTC';

            // Set active class on boxes
            if (boxes) {
                var boxEls = boxes.querySelectorAll('.wefax-countdown-box');
                for (var i = 0; i < boxEls.length; i++) {
                    boxEls[i].classList.remove('imminent');
                    boxEls[i].classList.add('active');
                }
            }
        } else {
            // Countdown to next
            var totalSec = Math.max(0, Math.floor(bestDiff * 60));
            var h2 = Math.floor(totalSec / 3600);
            var m2 = Math.floor((totalSec % 3600) / 60);
            var s2 = totalSec % 60;

            if (hoursEl) hoursEl.textContent = String(h2).padStart(2, '0');
            if (minsEl) minsEl.textContent = String(m2).padStart(2, '0');
            if (secsEl) secsEl.textContent = String(s2).padStart(2, '0');
            if (contentEl) contentEl.textContent = bestEntry.content;
            if (detailEl) detailEl.textContent = 'Next at ' + bestEntry.utc + ' UTC';

            // Set imminent class when < 10 min
            if (boxes) {
                var boxEls2 = boxes.querySelectorAll('.wefax-countdown-box');
                var isImminent = bestDiff < 10;
                for (var j = 0; j < boxEls2.length; j++) {
                    boxEls2[j].classList.remove('active');
                    if (isImminent) {
                        boxEls2[j].classList.add('imminent');
                    } else {
                        boxEls2[j].classList.remove('imminent');
                    }
                }
            }
        }
    }

    function stopCountdownTimer() {
        if (state.countdownInterval) {
            VisibleInterval.clear(state.countdownInterval);
            state.countdownInterval = null;
        }
    }

    // ---- Auto-Capture Scheduler ----

    function checkSchedulerStatus() {
        fetch('/wefax/schedule/status')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var strip = document.getElementById('wefaxStripAutoSchedule');
                var sidebar = document.getElementById('wefaxSidebarAutoSchedule');
                if (strip) strip.checked = !!data.enabled;
                if (sidebar) sidebar.checked = !!data.enabled;
                state.schedulerEnabled = !!data.enabled;
                if (data.enabled) {
                    connectSSE();
                    startSchedulerPoll();
                }
            })
            .catch(function () { /* ignore */ });
    }

    function enableScheduler() {
        var stationSel = document.getElementById('wefaxStation');
        var station = stationSel ? stationSel.value : '';
        var freqSel = document.getElementById('wefaxFrequency');
        var freqKhz = freqSel ? parseFloat(freqSel.value) : 0;

        if (!station || !freqKhz || isNaN(freqKhz)) {
            flashStartError();
            syncSchedulerCheckboxes(false);
            return;
        }

        var deviceSel = document.getElementById('rtlDevice');
        var device = deviceSel ? parseInt(deviceSel.value, 10) || 0 : 0;
        var gainInput = document.getElementById('wefaxGain');
        var iocSel = document.getElementById('wefaxIOC');
        var lpmSel = document.getElementById('wefaxLPM');
        var dsCheckbox = document.getElementById('wefaxDirectSampling');

        fetch('/wefax/schedule/enable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                station: station,
                frequency_khz: freqKhz,
                device: device,
                gain: gainInput ? parseFloat(gainInput.value) || 40 : 40,
                ioc: iocSel ? parseInt(iocSel.value, 10) : 576,
                lpm: lpmSel ? parseInt(lpmSel.value, 10) : 120,
                direct_sampling: dsCheckbox ? dsCheckbox.checked : true,
                frequency_reference: selectedFrequencyReference(),
            }),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'ok') {
                    var status = 'Auto-capture enabled — ' + (data.scheduled_count || 0) + ' broadcasts scheduled';
                    if (data.usb_offset_applied && !isNaN(Number(data.tuned_frequency_khz))) {
                        status += ' (tuning ' + Number(data.tuned_frequency_khz) + ' kHz)';
                    }
                    setStatus(status);
                    syncSchedulerCheckboxes(true);
                    state.schedulerEnabled = true;
                    connectSSE();
                    startSchedulerPoll();
                } else {
                    setStatus('Scheduler error: ' + (data.message || 'unknown'));
                    syncSchedulerCheckboxes(false);
                }
            })
            .catch(function (err) {
                setStatus('Scheduler error: ' + err.message);
                syncSchedulerCheckboxes(false);
            });
    }

    function disableScheduler() {
        fetch('/wefax/schedule/disable', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function () {
                setStatus('Auto-capture disabled');
                syncSchedulerCheckboxes(false);
                state.schedulerEnabled = false;
                stopSchedulerPoll();
                if (!state.running) {
                    disconnectSSE();
                }
            })
            .catch(function (err) {
                console.error('WeFax scheduler disable error:', err);
                reportActionableError('Disable Scheduler', err);
            });
    }

    function toggleScheduler(checkbox) {
        if (checkbox.checked) {
            enableScheduler();
        } else {
            disableScheduler();
        }
    }

    function startSchedulerPoll() {
        stopSchedulerPoll();
        state.schedulerPollTimer = setInterval(function () {
            fetch('/wefax/status')
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.running && !state.running) {
                        state.running = true;
                        updateButtons(true);
                        setStatus('Auto-capture in progress...');
                        connectSSE();
                    } else if (!data.running && state.running) {
                        state.running = false;
                        updateButtons(false);
                        loadImages();
                    }
                })
                .catch(function () { /* ignore poll errors */ });
        }, 10000);
    }

    function stopSchedulerPoll() {
        if (state.schedulerPollTimer) {
            clearInterval(state.schedulerPollTimer);
            state.schedulerPollTimer = null;
        }
    }

    function syncSchedulerCheckboxes(enabled) {
        var strip = document.getElementById('wefaxStripAutoSchedule');
        var sidebar = document.getElementById('wefaxSidebarAutoSchedule');
        if (strip) strip.checked = enabled;
        if (sidebar) sidebar.checked = enabled;
    }

    // ---- Public API ----

    return {
        init: init,
        destroy: destroy,
        start: start,
        stop: stop,
        onStationChange: onStationChange,
        loadImages: loadImages,
        deleteImage: deleteImage,
        deleteAllImages: deleteAllImages,
        viewImage: viewImage,
        closeImage: closeImage,
        toggleScheduler: toggleScheduler,
    };
})();
