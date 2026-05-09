/**
 * Weather Satellite Mode
 * Meteor LRPT decoder interface with auto-scheduler,
 * polar plot, styled real-world map, countdown, and timeline.
 */

const WeatherSat = (function() {
    const METEOR_NORAD_IDS = {
        'METEOR-M2-3': 57166,
        'METEOR-M2-4': 59051,
    };

    // State
    let isRunning = false;
    let eventSource = null;
    let images = [];
    let allPasses = [];
    let passes = [];
    let selectedPassIndex = -1;
    let currentSatellite = null;
    let countdownInterval = null;
    let schedulerEnabled = false;
    let groundMap = null;
    let groundTrackLayer = null;
    let groundOverlayLayer = null;
    let groundGridLayer = null;
    let satCrosshairMarker = null;
    let observerMarker = null;
    let consoleEntries = [];
    let consoleCollapsed = false;
    let currentPhase = 'idle';
    let consoleAutoHideTimer = null;
    let currentModalFilename = null;
    let locationListenersAttached = false;
    let initialized = false;
    let imageRefreshInterval = null;
    let lastDecodeJobSignature = null;
    let lastDecodeSatellite = null;
    let consoleFilter = 'all';

    // Timezone — delegates to global InterceptTime utility
    function formatShortTime(isoString) {
        return typeof InterceptTime !== 'undefined' ? InterceptTime.shortTime(isoString) : (isoString || '--');
    }

    function formatDateTime(isoString) {
        return typeof InterceptTime !== 'undefined' ? InterceptTime.dateTime(isoString) : (isoString || '--');
    }

    function getTZLabel() {
        return typeof InterceptTime !== 'undefined' ? InterceptTime.tzSuffix() : '';
    }

    function setTimezone(tz) {
        if (typeof InterceptTime !== 'undefined') InterceptTime.setTimezone(tz);
        const sel = document.getElementById('wxsatTimezone');
        if (sel && sel.value !== tz) sel.value = tz;
        applyPassFilter();
        renderGallery();
        updateTimelineLabels();
    }

    /**
     * Convert an azimuth angle (0-360) to a cardinal direction label.
     */
    function azToDir(az) {
        if (typeof az !== 'number' || isNaN(az)) return '?';
        const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
        return dirs[Math.round(az / 22.5) % 16];
    }

    /**
     * Find the best upcoming pass (highest max elevation).
     */
    function findBestPass(passList) {
        const now = new Date();
        const upcoming = passList.filter(p => {
            const end = parsePassDate(p.endTimeISO);
            return end && end > now;
        });
        if (upcoming.length === 0) return null;
        return upcoming.reduce((best, p) => (p.maxEl > best.maxEl) ? p : best, upcoming[0]);
    }

    /**
     * Initialize the Weather Satellite mode
     */
    function init() {
        // Sync timezone selector with global setting
        const tzSel = document.getElementById('wxsatTimezone');
        if (tzSel && typeof InterceptTime !== 'undefined') tzSel.value = InterceptTime.getTimezone();

        if (initialized) {
            checkStatus();
            loadImages();
            loadLocationInputs();
            loadPasses();
            startCountdownTimer();
            checkSchedulerStatus();
            initGroundMap();
            loadLatestDecodeJob();
            return;
        }
        initialized = true;

        // Listen for global timezone/format changes
        if (typeof InterceptTime !== 'undefined') {
            InterceptTime.onChange(() => {
                const sel = document.getElementById('wxsatTimezone');
                if (sel) sel.value = InterceptTime.getTimezone();
                applyPassFilter();
                renderGallery();
                updateTimelineLabels();
            });
        }

        checkStatus();
        loadImages();
        loadLocationInputs();
        loadPasses();
        startCountdownTimer();
        checkSchedulerStatus();
        initGroundMap();
        ensureImageRefresh();
        loadLatestDecodeJob();
    }

    /**
     * Get passes filtered by the currently selected satellite.
     */
    function getFilteredPasses() {
        const satSelect = document.getElementById('weatherSatSelect');
        const selected = satSelect?.value;
        if (!selected) return passes;
        return passes.filter(p => p.satellite === selected);
    }

    /**
     * Re-render passes, timeline, countdown and polar plot using filtered list.
     */
    function applyPassFilter() {
        const filtered = getFilteredPasses();
        selectedPassIndex = -1;
        renderPasses(filtered);
        renderTimeline(filtered);
        updateCountdownFromPasses();
        if (filtered.length > 0) {
            selectPass(0);
        } else {
            updateGroundTrack(null);
        }
    }

    /**
     * Get observer coordinates from shared location or local storage.
     */
    function getObserverCoords() {
        let lat;
        let lon;

        if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
            const shared = ObserverLocation.getShared();
            lat = Number(shared?.lat);
            lon = Number(shared?.lon);
        } else {
            lat = Number(localStorage.getItem('observerLat'));
            lon = Number(localStorage.getItem('observerLon'));
        }

        if (!isFinite(lat) || !isFinite(lon)) return null;
        if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
        return { lat, lon };
    }

    /**
     * Center the ground map on current observer coordinates when available.
     */
    function centerGroundMapOnObserver(zoom = 1) {
        if (!groundMap) return;
        const observer = getObserverCoords();
        if (!observer) return;
        const lat = Math.max(-85, Math.min(85, observer.lat));
        const lon = normalizeLon(observer.lon);
        groundMap.setView([lat, lon], zoom, { animate: false });
    }

    /**
     * Load observer location into input fields
     */
    function loadLocationInputs() {
        const latInput = document.getElementById('wxsatObsLat');
        const lonInput = document.getElementById('wxsatObsLon');

        let storedLat = localStorage.getItem('observerLat');
        let storedLon = localStorage.getItem('observerLon');
        if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
            const shared = ObserverLocation.getShared();
            storedLat = shared.lat.toString();
            storedLon = shared.lon.toString();
        }

        if (latInput && storedLat) latInput.value = storedLat;
        if (lonInput && storedLon) lonInput.value = storedLon;

        // Only attach listeners once — re-calling init() on mode switch must not
        // accumulate duplicate listeners that fire loadPasses() multiple times.
        if (!locationListenersAttached) {
            if (latInput) latInput.addEventListener('change', saveLocationFromInputs);
            if (lonInput) lonInput.addEventListener('change', saveLocationFromInputs);
            const satSelect = document.getElementById('weatherSatSelect');
            if (satSelect) {
                satSelect.addEventListener('change', () => {
                    resetDecodeJobDisplay();
                    applyPassFilter();
                    loadImages();
                    loadLatestDecodeJob();
                });
            }
            locationListenersAttached = true;
        }
    }

    /**
     * Save location from inputs and refresh passes
     */
    function saveLocationFromInputs() {
        const latInput = document.getElementById('wxsatObsLat');
        const lonInput = document.getElementById('wxsatObsLon');

        const lat = parseFloat(latInput?.value);
        const lon = parseFloat(lonInput?.value);

        if (!isNaN(lat) && lat >= -90 && lat <= 90 &&
            !isNaN(lon) && lon >= -180 && lon <= 180) {
            if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
                ObserverLocation.setShared({ lat, lon });
            } else {
                localStorage.setItem('observerLat', lat.toString());
                localStorage.setItem('observerLon', lon.toString());
            }
            loadPasses();
            centerGroundMapOnObserver(1);
        }
    }

    /**
     * Use GPS for location
     */
    function useGPS(btn) {
        if (!navigator.geolocation) {
            showNotification('Weather Sat', 'GPS not available in this browser');
            return;
        }

        const originalText = btn.innerHTML;
        btn.innerHTML = '<span style="opacity: 0.7;">...</span>';
        btn.disabled = true;

        navigator.geolocation.getCurrentPosition(
            (pos) => {
                const latInput = document.getElementById('wxsatObsLat');
                const lonInput = document.getElementById('wxsatObsLon');

                const lat = pos.coords.latitude.toFixed(4);
                const lon = pos.coords.longitude.toFixed(4);

                if (latInput) latInput.value = lat;
                if (lonInput) lonInput.value = lon;

                if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
                    ObserverLocation.setShared({ lat: parseFloat(lat), lon: parseFloat(lon) });
                } else {
                    localStorage.setItem('observerLat', lat);
                    localStorage.setItem('observerLon', lon);
                }

                btn.innerHTML = originalText;
                btn.disabled = false;
                showNotification('Weather Sat', 'Location updated');
                loadPasses();
                centerGroundMapOnObserver(1);
            },
            (err) => {
                btn.innerHTML = originalText;
                btn.disabled = false;
                showNotification('Weather Sat', 'Failed to get location');
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );
    }

    /**
     * Check decoder status
     */
    async function checkStatus() {
        try {
            const response = await fetch('/weather-sat/status');
            const data = await response.json();

            if (!data.available) {
                updateStatusUI('unavailable', 'SatDump not installed');
                return;
            }

            if (data.running) {
                isRunning = true;
                currentSatellite = data.satellite;
                updateStatusUI('capturing', `Capturing ${data.satellite}...`);
                startStream();
            } else {
                updateStatusUI('idle', 'Idle');
            }
        } catch (err) {
            console.error('Failed to check weather sat status:', err);
        }
    }

    /**
     * Start capture
     */
    async function start() {
        const satSelect = document.getElementById('weatherSatSelect');
        const gainInput = document.getElementById('weatherSatGain');
        const biasTInput = document.getElementById('weatherSatBiasT');
        const deviceSelect = document.getElementById('deviceSelect');

        const satellite = satSelect?.value || 'METEOR-M2-3';
        const gain = parseFloat(gainInput?.value || '40');
        const biasT = biasTInput?.checked || false;
        const device = parseInt(deviceSelect?.value || '0', 10);

        clearConsole();
        showConsole(true);
        updatePhaseIndicator('tuning');
        addConsoleEntry('Starting capture...', 'info');
        updateStatusUI('connecting', 'Starting...');

        const startBtn = document.getElementById('weatherSatStartBtn');
        if (startBtn) startBtn.classList.add('btn-loading');
        try {
            const config = {
                satellite,
                device,
                gain,
                bias_t: biasT,
                sdr_type: typeof getSelectedSDRType === 'function' ? getSelectedSDRType() : 'rtlsdr',
            };

            // Add rtl_tcp params if using remote SDR
            if (typeof getRemoteSDRConfig === 'function') {
                var remoteConfig = getRemoteSDRConfig();
                if (remoteConfig) {
                    config.rtl_tcp_host = remoteConfig.host;
                    config.rtl_tcp_port = remoteConfig.port;
                }
            }

            const response = await fetch('/weather-sat/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });

            const data = await response.json();

            if (data.status === 'started' || data.status === 'already_running') {
                isRunning = true;
                currentSatellite = data.satellite || satellite;
                updateStatusUI('capturing', `${data.satellite} ${data.frequency} MHz`);
                updateFreqDisplay(data.frequency, data.mode);
                startStream();
                showNotification('Weather Sat', `Capturing ${data.satellite} on ${data.frequency} MHz`);
            } else {
                updateStatusUI('idle', 'Start failed');
                showNotification('Weather Sat', data.message || 'Failed to start');
            }
        } catch (err) {
            console.error('Failed to start weather sat:', err);
            reportActionableError('Start Weather Satellite', err, {
                onRetry: () => start()
            });
            updateStatusUI('idle', 'Error');
        } finally {
            if (startBtn) startBtn.classList.remove('btn-loading');
        }
    }

    /**
     * Pre-select a satellite without starting capture.
     * Used by the satellite dashboard handoff so the user can review
     * settings before hitting Start.
     */
    function preSelect(satellite) {
        const satSelect = document.getElementById('weatherSatSelect');
        if (satSelect) {
            satSelect.value = satellite;
            satSelect.dispatchEvent(new Event('change'));
        }
    }

    /**
     * Start capture for a specific pass
     */
    function startPass(satellite) {
        const satSelect = document.getElementById('weatherSatSelect');
        if (satSelect) {
            satSelect.value = satellite;
            satSelect.dispatchEvent(new Event('change'));
        }
        start();
    }

    /**
     * Stop capture
     */
    async function stop() {
        // Optimistically update UI immediately so stop feels responsive,
        // even if the server takes time to terminate the process.
        isRunning = false;
        stopStream();
        updateStatusUI('idle', 'Stopping...');
        try {
            await fetch('/weather-sat/stop', { method: 'POST' });
            updateStatusUI('idle', 'Stopped');
            showNotification('Weather Sat', 'Capture stopped');
        } catch (err) {
            console.error('Failed to stop weather sat:', err);
            reportActionableError('Stop Weather Satellite', err);
        }
    }

    /**
     * Start test decode from a pre-recorded file
     */
    async function testDecode() {
        const satSelect = document.getElementById('wxsatTestSatSelect');
        const fileInput = document.getElementById('wxsatTestFilePath');
        const rateSelect = document.getElementById('wxsatTestSampleRate');

        const satellite = satSelect?.value || 'METEOR-M2-3';
        const inputFile = (fileInput?.value || '').trim();
        const sampleRate = parseInt(rateSelect?.value || '1000000', 10);

        if (!inputFile) {
            showNotification('Weather Sat', 'Enter a file path');
            return;
        }

        clearConsole();
        showConsole(true);
        updatePhaseIndicator('decoding');
        addConsoleEntry(`Test decode: ${inputFile}`, 'info');
        updateStatusUI('connecting', 'Starting file decode...');

        try {
            const response = await fetch('/weather-sat/test-decode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    satellite,
                    input_file: inputFile,
                    sample_rate: sampleRate,
                })
            });

            const data = await response.json();

            if (data.status === 'started' || data.status === 'already_running') {
                isRunning = true;
                currentSatellite = data.satellite || satellite;
                updateStatusUI('decoding', `Decoding ${data.satellite} from file`);
                updateFreqDisplay(data.frequency, data.mode);
                startStream();
                showNotification('Weather Sat', `Decoding ${data.satellite} from file`);
            } else {
                updateStatusUI('idle', 'Decode failed');
                showNotification('Weather Sat', data.message || 'Failed to start decode');
                addConsoleEntry(data.message || 'Failed to start decode', 'error');
            }
        } catch (err) {
            console.error('Failed to start test decode:', err);
            reportActionableError('Start Test Decode', err, {
                onRetry: () => testDecode()
            });
            updateStatusUI('idle', 'Error');
        }
    }

    /**
     * Update status UI
     */
    function updateStatusUI(status, text) {
        const dot = document.getElementById('wxsatStripDot');
        const statusText = document.getElementById('wxsatStripStatus');
        const startBtn = document.getElementById('wxsatStartBtn');
        const stopBtn = document.getElementById('wxsatStopBtn');

        if (dot) {
            dot.className = 'wxsat-strip-dot';
            if (status === 'capturing') dot.classList.add('capturing');
            else if (status === 'decoding') dot.classList.add('decoding');
        }

        if (statusText) statusText.textContent = text || status;

        if (startBtn && stopBtn) {
            if (status === 'capturing' || status === 'decoding') {
                startBtn.style.display = 'none';
                stopBtn.style.display = 'inline-block';
            } else {
                startBtn.style.display = 'inline-block';
                stopBtn.style.display = 'none';
            }
        }
    }

    /**
     * Update frequency display in strip
     */
    function updateFreqDisplay(freq, mode) {
        const freqEl = document.getElementById('wxsatStripFreq');
        const modeEl = document.getElementById('wxsatStripMode');
        if (freqEl) freqEl.textContent = freq || '--';
        if (modeEl) modeEl.textContent = mode || '--';
    }

    /**
     * Start SSE stream
     */
    function startStream() {
        if (eventSource) eventSource.close();

        eventSource = new EventSource('/weather-sat/stream');

        eventSource.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'weather_sat_progress') {
                    handleProgress(data);
                } else if (data.type && data.type.startsWith('schedule_')) {
                    handleSchedulerSSE(data);
                }
            } catch (err) {
                console.error('Failed to parse SSE:', err);
            }
        };

        eventSource.onerror = () => {
            // Close the failed connection first to avoid leaking it
            stopStream();
            setTimeout(() => {
                if (isRunning || schedulerEnabled) startStream();
            }, 3000);
        };
    }

    /**
     * Stop SSE stream
     */
    function stopStream() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
    }

    /**
     * Handle progress update
     */
    function handleProgress(data) {
        const captureStatus = document.getElementById('wxsatCaptureStatus');
        const captureMsg = document.getElementById('wxsatCaptureMsg');
        const captureElapsed = document.getElementById('wxsatCaptureElapsed');
        const progressBar = document.getElementById('wxsatProgressFill');

        if (data.status === 'capturing' || data.status === 'decoding') {
            updateStatusUI(data.status, `${data.status === 'decoding' ? 'Decoding' : 'Capturing'} ${data.satellite}...`);

            if (captureStatus) captureStatus.classList.add('active');
            if (captureMsg) captureMsg.textContent = data.message || '';
            if (captureElapsed) captureElapsed.textContent = formatElapsed(data.elapsed_seconds || 0);
            if (progressBar) progressBar.style.width = (data.progress || 0) + '%';

            // Console updates
            showConsole(true);
            if (data.message) addConsoleEntry(data.message, data.log_type || 'info');
            if (data.capture_phase) updatePhaseIndicator(data.capture_phase);

        } else if (data.status === 'complete') {
            if (data.image) {
                images.unshift(data.image);
                updateImageCount(images.length);
                renderGallery();
                showNotification('Weather Sat', `New image: ${data.image.product || data.image.satellite}`);
            }

            if (!data.image) {
                // Capture ended
                isRunning = false;
                if (!schedulerEnabled) stopStream();
                updateStatusUI('idle', 'Capture complete');
                if (captureStatus) captureStatus.classList.remove('active');

                addConsoleEntry('Capture complete', 'signal');
                updatePhaseIndicator('complete');
                if (consoleAutoHideTimer) clearTimeout(consoleAutoHideTimer);
                consoleAutoHideTimer = setTimeout(() => showConsole(false), 30000);
            }

        } else if (data.status === 'error') {
            isRunning = false;
            if (!schedulerEnabled) stopStream();
            updateStatusUI('idle', 'Error');
            showNotification('Weather Sat', data.message || 'Capture error');
            if (captureStatus) captureStatus.classList.remove('active');

            if (data.message) addConsoleEntry(data.message, 'error');
            updatePhaseIndicator('error');
            if (consoleAutoHideTimer) clearTimeout(consoleAutoHideTimer);
            consoleAutoHideTimer = setTimeout(() => showConsole(false), 15000);
            loadImages();
        }
    }

    /**
     * Handle scheduler SSE events
     */
    function handleSchedulerSSE(data) {
        if (data.type === 'schedule_capture_start') {
            isRunning = true;
            const p = data.pass || {};
            currentSatellite = p.satellite;
            updateStatusUI('capturing', `Auto: ${p.name || p.satellite} ${p.frequency} MHz`);
            showNotification('Weather Sat', `Auto-capture started: ${p.name || p.satellite}`);
        } else if (data.type === 'schedule_capture_complete') {
            const p = data.pass || {};
            showNotification('Weather Sat', `Auto-capture complete: ${p.name || ''}`);
            // Reset UI — the decoder's stop() doesn't emit a progress complete event
            // when called internally by the scheduler, so we handle it here.
            isRunning = false;
            updateStatusUI('idle', 'Auto-capture complete');
            const captureStatus = document.getElementById('wxsatCaptureStatus');
            if (captureStatus) captureStatus.classList.remove('active');
            updatePhaseIndicator('complete');
            loadImages();
            loadPasses();
        } else if (data.type === 'schedule_capture_skipped') {
            const reason = data.reason || 'unknown';
            const p = data.pass || {};
            showNotification('Weather Sat', `Pass skipped (${reason}): ${p.name || p.satellite}`);
        }
    }

    /**
     * Format elapsed seconds
     */
    function formatElapsed(seconds) {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return `${m}:${s.toString().padStart(2, '0')}`;
    }

    /**
     * Parse pass timestamps, accepting legacy malformed UTC strings (+00:00Z).
     */
    function parsePassDate(value) {
        if (!value || typeof value !== 'string') return null;

        let parsed = new Date(value);
        if (!Number.isNaN(parsed.getTime())) {
            return parsed;
        }

        // Backward-compatible cleanup for accidentally double-suffixed UTC timestamps.
        parsed = new Date(value.replace(/\+00:00Z$/, 'Z'));
        if (!Number.isNaN(parsed.getTime())) {
            return parsed;
        }

        return null;
    }

    /**
     * Load pass predictions (with trajectory + ground track)
     */
    async function loadPasses() {
        let storedLat, storedLon;
        
        // Use ObserverLocation if available, otherwise fall back to localStorage
        if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
            const shared = ObserverLocation.getShared();
            storedLat = shared?.lat?.toString();
            storedLon = shared?.lon?.toString();
        } else {
            storedLat = localStorage.getItem('observerLat');
            storedLon = localStorage.getItem('observerLon');
        }

        if (!storedLat || !storedLon) {
            allPasses = [];
            passes = [];
            selectedPassIndex = -1;
            renderPasses([]);
            renderTimeline([]);
            updateCountdownFromPasses();
            updatePassAnalysis([]);
            updateGroundTrack(null);
            const passCountEl = document.getElementById('wxsatStripPassCount');
            if (passCountEl) passCountEl.textContent = '0';
            return;
        }

        try {
            const url = `/weather-sat/passes?latitude=${storedLat}&longitude=${storedLon}&hours=48&min_elevation=5&trajectory=true&ground_track=true`;
            const response = await fetch(url);
            const data = await response.json();

            if (data.status === 'ok') {
                allPasses = data.passes || [];
                applyPassFilter();
            }
        } catch (err) {
            console.error('Failed to load passes:', err);
        }
    }

    /**
     * Filter displayed passes by the currently selected satellite dropdown value.
     * Updates the module-level `passes` from `allPasses` so selectPass/countdown work.
     */
    function applyPassFilter() {
        const satSelect = document.getElementById('weatherSatSelect');
        const selected = satSelect?.value;
        passes = selected
            ? allPasses.filter(p => p.satellite === selected)
            : allPasses.slice();

        selectedPassIndex = -1;
        renderPasses(passes);
        renderTimeline(passes);
        updateTimelineLabels();
        updateCountdownFromPasses();
        updatePassAnalysis(passes);
        // Update strip pass count
        const passCountEl = document.getElementById('wxsatStripPassCount');
        if (passCountEl) passCountEl.textContent = passes.length;
        if (passes.length > 0) {
            selectPass(0);
        } else {
            updateGroundTrack(null);
            drawPolarPlot(null);
        }
    }

    /**
     * Select a pass to display in polar plot and map
     */
    function selectPass(index) {
        const filtered = getFilteredPasses();
        if (index < 0 || index >= filtered.length) return;
        selectedPassIndex = index;
        const pass = filtered[index];

        // Highlight active card
        document.querySelectorAll('.wxsat-pass-card').forEach((card, i) => {
            card.classList.toggle('selected', i === index);
        });

        // Update polar plot
        drawPolarPlot(pass);

        // Update ground track
        updateGroundTrack(pass);

        // Update polar panel subtitle
        const polarSat = document.getElementById('wxsatPolarSat');
        if (polarSat) polarSat.textContent = `${pass.name} ${pass.maxEl}\u00b0`;

        // Update pass geometry detail panel
        updatePassGeometry(pass);
    }

    /**
     * Update the AOS/TCA/LOS pass geometry detail panel.
     */
    function updatePassGeometry(pass) {
        const panel = document.getElementById('wxsatPassGeometry');
        if (!panel) return;

        if (!pass) {
            panel.style.display = 'none';
            return;
        }
        panel.style.display = 'flex';

        const aosTime = document.getElementById('wxsatGeomAosTime');
        const aosAz = document.getElementById('wxsatGeomAosAz');
        const tcaEl = document.getElementById('wxsatGeomTcaEl');
        const tcaAz = document.getElementById('wxsatGeomTcaAz');
        const losTime = document.getElementById('wxsatGeomLosTime');
        const losAz = document.getElementById('wxsatGeomLosAz');
        const meta = document.getElementById('wxsatGeomMeta');

        const tzLabel = getTZLabel();
        if (aosTime) aosTime.textContent = formatShortTime(pass.startTimeISO) + tzLabel;
        if (aosAz) aosAz.textContent = `${Math.round(pass.riseAz || 0)}\u00b0 ${azToDir(pass.riseAz)}`;
        if (tcaEl) tcaEl.textContent = `${pass.maxEl}\u00b0 el`;
        if (tcaAz) tcaAz.textContent = `${Math.round(pass.maxElAz || pass.tcaAz || 0)}\u00b0 ${azToDir(pass.maxElAz || pass.tcaAz)}`;
        if (losTime) losTime.textContent = formatShortTime(pass.endTimeISO) + tzLabel;
        if (losAz) losAz.textContent = `${Math.round(pass.setAz || 0)}\u00b0 ${azToDir(pass.setAz)}`;

        const durMin = Math.round((pass.duration || 0) / 60);
        if (meta) meta.textContent = `${durMin} min / ${pass.quality}`;
    }

    /**
     * Render pass predictions list
     */
    function renderPasses(passList) {
        const container = document.getElementById('wxsatPassesList');
        const countEl = document.getElementById('wxsatPassesCount');

        if (countEl) countEl.textContent = passList.length;

        if (!container) return;

        if (passList.length === 0) {
            const hasLocation = localStorage.getItem('observerLat') !== null ||
                (window.ObserverLocation && ObserverLocation.isSharedEnabled() && ObserverLocation.getShared()?.lat);
            container.innerHTML = `
                <div class="wxsat-gallery-empty">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width: 32px; height: 32px; margin-bottom: 8px; opacity: 0.3;">
                        ${hasLocation
                            ? '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'
                            : '<circle cx="12" cy="12" r="10"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/>'}
                    </svg>
                    <p style="font-size: 12px; font-weight: 600; color: var(--text-secondary);">
                        ${hasLocation ? 'No passes in next 24 hours' : 'Set your location'}
                    </p>
                    <p style="font-size: 11px; margin-top: 4px;">
                        ${hasLocation
                            ? 'All Meteor passes may be below the minimum elevation. Try again later.'
                            : 'Enter lat/lon in the strip bar above or click GPS to load pass predictions'}
                    </p>
                </div>
            `;
            // Hide geometry panel when no passes
            const geom = document.getElementById('wxsatPassGeometry');
            if (geom) geom.style.display = 'none';
            return;
        }

        const bestPass = findBestPass(passList);

        container.innerHTML = passList.map((pass, idx) => {
            const modeClass = pass.mode === 'APT' ? 'apt' : 'lrpt';
            const timeStr = formatDateTime(pass.startTimeISO) + getTZLabel();
            const now = new Date();
            const passStart = parsePassDate(pass.startTimeISO);
            const diffMs = passStart ? passStart - now : NaN;
            const diffMins = Number.isFinite(diffMs) ? Math.floor(diffMs / 60000) : NaN;
            const isSelected = idx === selectedPassIndex;
            const isBest = bestPass && pass.startTimeISO === bestPass.startTimeISO && pass.satellite === bestPass.satellite;

            let countdown = '--';
            if (!Number.isFinite(diffMs)) {
                countdown = '--';
            } else if (diffMs < 0) {
                countdown = 'NOW';
            } else if (diffMins < 60) {
                countdown = `in ${diffMins}m`;
            } else {
                const hrs = Math.floor(diffMins / 60);
                const mins = diffMins % 60;
                countdown = `in ${hrs}h${mins}m`;
            }

            const riseDir = azToDir(pass.riseAz);
            const setDir = azToDir(pass.setAz);
            const bestBadge = isBest ? '<span class="wxsat-pass-best-badge">BEST</span>' : '';
            const durMin = Math.round((pass.duration || 0) / 60);
            const aosStr = formatShortTime(pass.startTimeISO);
            const losStr = formatShortTime(pass.endTimeISO);
            const tzLabel = getTZLabel();

            return `
                <div class="wxsat-pass-card${isSelected ? ' selected' : ''}" onclick="WeatherSat.selectPass(${idx})">
                    <div class="wxsat-pass-sat">
                        <span class="wxsat-pass-sat-name">${escapeHtml(pass.name)}${bestBadge}</span>
                        <span class="wxsat-pass-mode ${modeClass}">${escapeHtml(pass.mode)}</span>
                    </div>
                    <div class="wxsat-pass-details">
                        <span class="wxsat-pass-detail-label">AOS</span>
                        <span class="wxsat-pass-detail-value">${escapeHtml(aosStr)}${escapeHtml(tzLabel)} &middot; ${Math.round(pass.riseAz || 0)}&deg; ${riseDir}</span>
                        <span class="wxsat-pass-detail-label">LOS</span>
                        <span class="wxsat-pass-detail-value">${escapeHtml(losStr)}${escapeHtml(tzLabel)} &middot; ${Math.round(pass.setAz || 0)}&deg; ${setDir}</span>
                        <span class="wxsat-pass-detail-label">Peak</span>
                        <span class="wxsat-pass-detail-value">${pass.maxEl}&deg; el &middot; ${durMin} min</span>
                        <span class="wxsat-pass-detail-label">Track</span>
                        <span class="wxsat-pass-detail-value">${riseDir} <span class="wxsat-dir-arrow">&rarr;</span> ${setDir}</span>
                    </div>
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 4px;">
                        <span class="wxsat-pass-quality ${pass.quality}">${pass.quality}</span>
                        <span style="font-size: 10px; color: var(--text-dim); font-family: 'Roboto Condensed', 'Arial Narrow', sans-serif;">${countdown}</span>
                    </div>
                    <div style="margin-top: 6px; text-align: right;">
                        <button class="wxsat-strip-btn" onclick="event.stopPropagation(); WeatherSat.startPass('${escapeHtml(pass.satellite)}')" style="font-size: 10px; padding: 2px 8px;">Capture</button>
                    </div>
                </div>
            `;
        }).join('');
    }

    // ========================
    // Polar Plot
    // ========================

    /**
     * Draw polar plot for a pass trajectory
     */
    function drawPolarPlot(pass) {
        if (!pass) return;
        const canvas = document.getElementById('wxsatPolarCanvas');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const w = canvas.width;
        const h = canvas.height;
        const cx = w / 2;
        const cy = h / 2;
        const r = Math.min(cx, cy) - 20;

        ctx.clearRect(0, 0, w, h);

        // Background
        ctx.fillStyle = '#0d1117';
        ctx.fillRect(0, 0, w, h);

        // Grid circles (30, 60, 90 deg elevation)
        ctx.strokeStyle = '#2a3040';
        ctx.lineWidth = 0.5;
        [90, 60, 30].forEach((el, i) => {
            const gr = r * (1 - el / 90);
            ctx.beginPath();
            ctx.arc(cx, cy, gr, 0, Math.PI * 2);
            ctx.stroke();
            // Label
            ctx.fillStyle = '#555';
            ctx.font = '9px Roboto Condensed, monospace';
            ctx.textAlign = 'left';
            ctx.fillText(el + '\u00b0', cx + gr + 3, cy - 2);
        });

        // Horizon circle
        ctx.strokeStyle = '#3a4050';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();

        // Cardinal directions
        ctx.fillStyle = '#666';
        ctx.font = '10px Roboto Condensed, monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('N', cx, cy - r - 10);
        ctx.fillText('S', cx, cy + r + 10);
        ctx.fillText('E', cx + r + 10, cy);
        ctx.fillText('W', cx - r - 10, cy);

        // Cross hairs
        ctx.strokeStyle = '#2a3040';
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.moveTo(cx, cy - r);
        ctx.lineTo(cx, cy + r);
        ctx.moveTo(cx - r, cy);
        ctx.lineTo(cx + r, cy);
        ctx.stroke();

        // Trajectory
        const trajectory = pass?.trajectory;
        if (!trajectory || trajectory.length === 0) return;

        const color = pass.mode === 'LRPT' ? '#00ff88' : '#00d4ff';

        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;

        trajectory.forEach((pt, i) => {
            const elRad = (90 - pt.el) / 90;
            const azRad = (pt.az - 90) * Math.PI / 180; // offset: N is up
            const px = cx + r * elRad * Math.cos(azRad);
            const py = cy + r * elRad * Math.sin(azRad);

            if (i === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
        });
        ctx.stroke();

        // Start point (green dot)
        const start = trajectory[0];
        const startR = (90 - start.el) / 90;
        const startAz = (start.az - 90) * Math.PI / 180;
        ctx.fillStyle = '#00ff88';
        ctx.beginPath();
        ctx.arc(cx + r * startR * Math.cos(startAz), cy + r * startR * Math.sin(startAz), 4, 0, Math.PI * 2);
        ctx.fill();

        // End point (red dot)
        const end = trajectory[trajectory.length - 1];
        const endR = (90 - end.el) / 90;
        const endAz = (end.az - 90) * Math.PI / 180;
        ctx.fillStyle = '#ff4444';
        ctx.beginPath();
        ctx.arc(cx + r * endR * Math.cos(endAz), cy + r * endR * Math.sin(endAz), 4, 0, Math.PI * 2);
        ctx.fill();

        // Max elevation marker
        let maxEl = 0;
        let maxPt = trajectory[0];
        trajectory.forEach(pt => { if (pt.el > maxEl) { maxEl = pt.el; maxPt = pt; } });
        const maxR = (90 - maxPt.el) / 90;
        const maxAz = (maxPt.az - 90) * Math.PI / 180;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(cx + r * maxR * Math.cos(maxAz), cy + r * maxR * Math.sin(maxAz), 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = color;
        ctx.font = '9px Roboto Condensed, monospace';
        ctx.textAlign = 'center';
        ctx.fillText(Math.round(maxEl) + '\u00b0', cx + r * maxR * Math.cos(maxAz), cy + r * maxR * Math.sin(maxAz) - 8);
    }

    // ========================
    // Ground Track Map
    // ========================

    /**
     * Initialize styled real-world map panel.
     */
    async function initGroundMap() {
        const container = document.getElementById('wxsatGroundMap');
        if (!container) return;
        if (typeof L === 'undefined') return;
        const observer = getObserverCoords();
        const defaultCenter = observer
            ? [Math.max(-85, Math.min(85, observer.lat)), normalizeLon(observer.lon)]
            : [12, 0];
        const defaultZoom = 1;

        if (!groundMap) {
            groundMap = L.map(container, {
                center: defaultCenter,
                zoom: defaultZoom,
                minZoom: 1,
                maxZoom: 7,
                zoomControl: false,
                attributionControl: false,
                worldCopyJump: true,
                preferCanvas: true,
            });

            // Add fallback tiles immediately so the map is visible instantly
            const fallbackTiles = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                subdomains: 'abcd',
                maxZoom: 18,
                noWrap: false,
                crossOrigin: true,
                className: 'tile-layer-cyan',
            }).addTo(groundMap);

            // Upgrade tiles in background via Settings (with timeout fallback)
            if (typeof Settings !== 'undefined' && Settings.createTileLayer) {
                try {
                    await Promise.race([
                        Settings.init(),
                        new Promise((_, reject) => setTimeout(() => reject(new Error('Settings timeout')), 5000))
                    ]);
                    groundMap.removeLayer(fallbackTiles);
                    Settings.createTileLayer().addTo(groundMap);
                    Settings.registerMap(groundMap);
                } catch (e) {
                    console.warn('WeatherSat: Settings init failed/timed out, using fallback tiles:', e);
                }
            }

            groundGridLayer = L.layerGroup().addTo(groundMap);
            addStyledGridOverlay(groundGridLayer);

            groundTrackLayer = L.layerGroup().addTo(groundMap);
            groundOverlayLayer = L.layerGroup().addTo(groundMap);
        }

        setTimeout(() => {
            if (!groundMap) return;
            groundMap.invalidateSize(false);
            groundMap.setView(defaultCenter, defaultZoom, { animate: false });
            updateGroundTrack(getSelectedPass());
        }, 140);
    }

    /**
     * Update map panel subtitle.
     */
    function updateProjectionInfo(text) {
        const infoEl = document.getElementById('wxsatMapInfo');
        if (infoEl) infoEl.textContent = text || '--';
    }

    /**
     * Normalize longitude to [-180, 180).
     */
    function normalizeLon(value) {
        const lon = Number(value);
        if (!isFinite(lon)) return 0;
        return ((((lon + 180) % 360) + 360) % 360) - 180;
    }

    /**
     * Build track segments that do not cross the date line.
     */
    function buildTrackSegments(track) {
        const segments = [];
        let currentSegment = [];

        track.forEach((point) => {
            const lat = Number(point?.lat);
            const lon = normalizeLon(point?.lon);
            if (!isFinite(lat) || !isFinite(lon)) return;

            if (currentSegment.length > 0) {
                const prevLon = currentSegment[currentSegment.length - 1][1];
                if (Math.abs(lon - prevLon) > 180) {
                    if (currentSegment.length > 1) segments.push(currentSegment);
                    currentSegment = [];
                }
            }

            currentSegment.push([lat, lon]);
        });

        if (currentSegment.length > 1) segments.push(currentSegment);
        return segments;
    }

    /**
     * Draw a subtle graticule over the base map for a cyber/wireframe look.
     */
    function addStyledGridOverlay(layer) {
        if (!layer || typeof L === 'undefined') return;
        layer.clearLayers();

        for (let lon = -180; lon <= 180; lon += 30) {
            const line = [];
            for (let lat = -85; lat <= 85; lat += 5) line.push([lat, lon]);
            L.polyline(line, {
                color: '#4ed2ff',
                weight: lon % 60 === 0 ? 1.1 : 0.8,
                opacity: lon % 60 === 0 ? 0.2 : 0.12,
                interactive: false,
                lineCap: 'round',
            }).addTo(layer);
        }

        for (let lat = -75; lat <= 75; lat += 15) {
            const line = [];
            for (let lon = -180; lon <= 180; lon += 5) line.push([lat, lon]);
            L.polyline(line, {
                color: '#5be7ff',
                weight: lat % 30 === 0 ? 1.1 : 0.8,
                opacity: lat % 30 === 0 ? 0.2 : 0.12,
                interactive: false,
                lineCap: 'round',
            }).addTo(layer);
        }
    }

    function clearSatelliteCrosshair() {
        if (!groundOverlayLayer || !satCrosshairMarker) return;
        groundOverlayLayer.removeLayer(satCrosshairMarker);
        satCrosshairMarker = null;
    }

    function createSatelliteCrosshairIcon() {
        return L.divIcon({
            className: 'wxsat-crosshair-icon',
            iconSize: [30, 30],
            iconAnchor: [15, 15],
            html: `
                <div class="wxsat-crosshair-marker">
                    <span class="wxsat-crosshair-h"></span>
                    <span class="wxsat-crosshair-v"></span>
                    <span class="wxsat-crosshair-ring"></span>
                    <span class="wxsat-crosshair-dot"></span>
                </div>
            `,
        });
    }

    /**
     * Update selected ground track and redraw map overlays.
     */
    function updateGroundTrack(pass) {
        if (!groundMap || !groundTrackLayer) return;

        groundTrackLayer.clearLayers();
        observerMarker = null;

        if (!pass) {
            clearSatelliteCrosshair();
            updateProjectionInfo('--');
            return;
        }

        const track = pass?.groundTrack;
        if (!Array.isArray(track) || track.length === 0) {
            clearSatelliteCrosshair();
            updateProjectionInfo(`${pass.name || pass.satellite || '--'} --`);
            return;
        }

        const color = pass.mode === 'LRPT' ? '#27ffc6' : '#58ddff';
        const glowClass = pass.mode === 'LRPT' ? 'wxsat-pass-track lrpt' : 'wxsat-pass-track apt';
        const segments = buildTrackSegments(track);
        const validPoints = track
            .map((point) => [Number(point?.lat), normalizeLon(point?.lon)])
            .filter((point) => isFinite(point[0]) && isFinite(point[1]));

        segments.forEach((segment) => {
            L.polyline(segment, {
                color,
                weight: 2.3,
                opacity: 0.9,
                className: glowClass,
                interactive: false,
                lineJoin: 'round',
            }).addTo(groundTrackLayer);
        });

        if (validPoints.length > 0) {
            L.circleMarker(validPoints[0], {
                radius: 4.5,
                color: '#00ffa2',
                fillColor: '#00ffa2',
                fillOpacity: 0.95,
                weight: 0,
                interactive: false,
            }).addTo(groundTrackLayer);

            L.circleMarker(validPoints[validPoints.length - 1], {
                radius: 4.5,
                color: '#ff5e5e',
                fillColor: '#ff5e5e',
                fillOpacity: 0.95,
                weight: 0,
                interactive: false,
            }).addTo(groundTrackLayer);
        }

        let obsLat;
        let obsLon;
        if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
            const shared = ObserverLocation.getShared();
            obsLat = shared?.lat;
            obsLon = shared?.lon;
        } else {
            obsLat = parseFloat(localStorage.getItem('observerLat'));
            obsLon = parseFloat(localStorage.getItem('observerLon'));
        }

        if (isFinite(obsLat) && isFinite(obsLon)) {
            observerMarker = L.circleMarker([obsLat, obsLon], {
                radius: 5.5,
                color: '#ffd45b',
                fillColor: '#ffd45b',
                fillOpacity: 0.8,
                weight: 1,
                className: 'wxsat-observer-marker',
                interactive: false,
            }).addTo(groundTrackLayer);
        }

        updateSatelliteCrosshair(pass);
    }

    function getSelectedPass() {
        const filtered = getFilteredPasses();
        if (selectedPassIndex < 0 || selectedPassIndex >= filtered.length) return null;
        return filtered[selectedPassIndex];
    }

    function getSatellitePositionForPass(pass, atTime = new Date()) {
        const track = pass?.groundTrack;
        if (!Array.isArray(track) || track.length === 0) return null;

        const first = track[0];
        if (track.length === 1) {
            const lat = Number(first.lat);
            const lon = Number(first.lon);
            if (!isFinite(lat) || !isFinite(lon)) return null;
            return { lat, lon };
        }

        const start = parsePassDate(pass.startTimeISO);
        const end = parsePassDate(pass.endTimeISO);

        let fraction = 0;
        if (start && end && end > start) {
            const totalMs = end.getTime() - start.getTime();
            const elapsedMs = atTime.getTime() - start.getTime();
            fraction = Math.max(0, Math.min(1, elapsedMs / totalMs));
        }

        const lastIndex = track.length - 1;
        const idxFloat = fraction * lastIndex;
        const idx0 = Math.floor(idxFloat);
        const idx1 = Math.min(lastIndex, idx0 + 1);
        const t = idxFloat - idx0;

        const p0 = track[idx0];
        const p1 = track[idx1];
        const lat0 = Number(p0?.lat);
        const lon0 = Number(p0?.lon);
        const lat1 = Number(p1?.lat);
        const lon1 = Number(p1?.lon);

        if (!isFinite(lat0) || !isFinite(lon0) || !isFinite(lat1) || !isFinite(lon1)) {
            return null;
        }

        return {
            lat: lat0 + ((lat1 - lat0) * t),
            lon: lon0 + ((lon1 - lon0) * t),
        };
    }

    function updateSatelliteCrosshair(pass) {
        if (!groundMap || !groundOverlayLayer || typeof L === 'undefined') return;

        if (!pass) {
            clearSatelliteCrosshair();
            updateProjectionInfo('--');
            return;
        }

        const position = getSatellitePositionForPass(pass);
        if (!position) {
            clearSatelliteCrosshair();
            updateProjectionInfo(`${pass.name || pass.satellite || '--'} --`);
            return;
        }

        const latlng = [position.lat, normalizeLon(position.lon)];
        if (!satCrosshairMarker) {
            satCrosshairMarker = L.marker(latlng, {
                icon: createSatelliteCrosshairIcon(),
                interactive: false,
                keyboard: false,
                zIndexOffset: 900,
            }).addTo(groundOverlayLayer);
        } else {
            satCrosshairMarker.setLatLng(latlng);
        }

        const infoText =
            `${pass.name || pass.satellite || 'Satellite'} ` +
            `${position.lat.toFixed(2)}°, ${normalizeLon(position.lon).toFixed(2)}°`;
        updateProjectionInfo(infoText);

        if (!satCrosshairMarker.getTooltip()) {
            satCrosshairMarker.bindTooltip(infoText, {
                direction: 'top',
                offset: [0, -12],
                opacity: 0.92,
                className: 'wxsat-map-tooltip',
            });
        } else {
            satCrosshairMarker.setTooltipContent(infoText);
        }
    }

    // ========================
    // Countdown
    // ========================

    /**
     * Start the countdown interval timer
     */
    function startCountdownTimer() {
        if (countdownInterval) clearInterval(countdownInterval);
        countdownInterval = setInterval(updateCountdownFromPasses, 1000);
    }

    /**
     * Update countdown display from passes array
     */
    function updateCountdownFromPasses() {
        const now = new Date();
        let nextPass = null;
        let isActive = false;
        const filtered = getFilteredPasses();

        for (const pass of filtered) {
            const start = parsePassDate(pass.startTimeISO);
            const end = parsePassDate(pass.endTimeISO);
            if (!start || !end) {
                continue;
            }
            if (end > now) {
                nextPass = pass;
                isActive = start <= now;
                break;
            }
        }

        const daysEl = document.getElementById('wxsatCdDays');
        const hoursEl = document.getElementById('wxsatCdHours');
        const minsEl = document.getElementById('wxsatCdMins');
        const secsEl = document.getElementById('wxsatCdSecs');
        const satEl = document.getElementById('wxsatCountdownSat');
        const detailEl = document.getElementById('wxsatCountdownDetail');
        const boxes = document.getElementById('wxsatCountdownBoxes');

        if (!nextPass) {
            if (daysEl) daysEl.textContent = '--';
            if (hoursEl) hoursEl.textContent = '--';
            if (minsEl) minsEl.textContent = '--';
            if (secsEl) secsEl.textContent = '--';
            if (satEl) satEl.textContent = '--';
            if (detailEl) detailEl.textContent = 'No passes predicted';
            if (boxes) boxes.querySelectorAll('.wxsat-countdown-box').forEach(b => {
                b.classList.remove('imminent', 'active');
            });
            return;
        }

        const target = parsePassDate(nextPass.startTimeISO);
        if (!target) {
            if (daysEl) daysEl.textContent = '--';
            if (hoursEl) hoursEl.textContent = '--';
            if (minsEl) minsEl.textContent = '--';
            if (secsEl) secsEl.textContent = '--';
            if (satEl) satEl.textContent = '--';
            if (detailEl) detailEl.textContent = 'Invalid pass time';
            if (boxes) boxes.querySelectorAll('.wxsat-countdown-box').forEach(b => {
                b.classList.remove('imminent', 'active');
            });
            return;
        }
        let diffMs = target - now;

        if (isActive) {
            diffMs = 0;
        }

        const totalSec = Math.max(0, Math.floor(diffMs / 1000));
        const d = Math.floor(totalSec / 86400);
        const h = Math.floor((totalSec % 86400) / 3600);
        const m = Math.floor((totalSec % 3600) / 60);
        const s = totalSec % 60;

        if (daysEl) daysEl.textContent = d.toString().padStart(2, '0');
        if (hoursEl) hoursEl.textContent = h.toString().padStart(2, '0');
        if (minsEl) minsEl.textContent = m.toString().padStart(2, '0');
        if (secsEl) secsEl.textContent = s.toString().padStart(2, '0');
        const passTimeStr = formatShortTime(nextPass.startTimeISO) + getTZLabel();
        if (satEl) satEl.textContent = `${nextPass.name} ${nextPass.frequency} MHz`;
        if (detailEl) {
            if (isActive) {
                detailEl.textContent = `ACTIVE - ${nextPass.maxEl}\u00b0 max el`;
            } else {
                const bestPass = findBestPass(filtered);
                const durMin = Math.round((nextPass.duration || 0) / 60);
                const bestNote = bestPass && bestPass.startTimeISO !== nextPass.startTimeISO
                    ? ` | Best: ${bestPass.name} ${formatShortTime(bestPass.startTimeISO)}${getTZLabel()} (${bestPass.maxEl}\u00b0)`
                    : '';
                detailEl.textContent = `${passTimeStr} / ${nextPass.maxEl}\u00b0 max el / ${durMin} min${bestNote}`;
            }
        }

        // Countdown box states
        if (boxes) {
            const isImminent = totalSec < 600 && totalSec > 0; // < 10 min
            boxes.querySelectorAll('.wxsat-countdown-box').forEach(b => {
                b.classList.toggle('imminent', isImminent);
                b.classList.toggle('active', isActive);
            });
        }

        // Keep timeline cursor in sync
        updateTimelineCursor();
        // Keep selected satellite marker synchronized with time progression.
        updateSatelliteCrosshair(getSelectedPass());
    }

    // ========================
    // Timeline
    // ========================

    /**
     * Render 24h timeline with pass markers
     */
    function renderTimeline(passList) {
        const track = document.getElementById('wxsatTimelineTrack');
        const cursor = document.getElementById('wxsatTimelineCursor');
        if (!track) return;

        // Clear existing pass markers
        track.querySelectorAll('.wxsat-timeline-pass').forEach(el => el.remove());

        const now = new Date();
        const dayStart = new Date(now);
        dayStart.setHours(0, 0, 0, 0);
        const dayMs = 24 * 60 * 60 * 1000;

        passList.forEach((pass, idx) => {
            const start = parsePassDate(pass.startTimeISO);
            const end = parsePassDate(pass.endTimeISO);
            if (!start || !end) return;

            const startPct = Math.max(0, Math.min(100, ((start - dayStart) / dayMs) * 100));
            const endPct = Math.max(0, Math.min(100, ((end - dayStart) / dayMs) * 100));
            const widthPct = Math.max(0.5, endPct - startPct);

            const marker = document.createElement('div');
            marker.className = `wxsat-timeline-pass ${pass.mode === 'LRPT' ? 'lrpt' : 'apt'}`;
            marker.style.left = startPct + '%';
            marker.style.width = widthPct + '%';
            marker.title = `${pass.name} ${formatShortTime(pass.startTimeISO)}${getTZLabel()} (${pass.maxEl}\u00b0)`;
            marker.onclick = () => selectPass(idx);
            track.appendChild(marker);
        });

        // Update cursor position
        updateTimelineCursor();
    }

    /**
     * Update timeline cursor to current time
     */
    function updateTimelineCursor() {
        const cursor = document.getElementById('wxsatTimelineCursor');
        if (!cursor) return;

        const now = new Date();
        const dayStart = new Date(now);
        dayStart.setHours(0, 0, 0, 0);
        const pct = ((now - dayStart) / (24 * 60 * 60 * 1000)) * 100;
        cursor.style.left = pct + '%';
    }

    /**
     * Update timeline hour labels to match the selected timezone.
     */
    function updateTimelineLabels() {
        const labels = document.querySelector('.wxsat-timeline-labels');
        if (!labels) return;
        const hours = [0, 6, 12, 18, 24];
        const spans = labels.querySelectorAll('span');
        if (spans.length !== hours.length) return;

        const tz = typeof InterceptTime !== 'undefined' ? InterceptTime.getTimezone() : 'UTC';
        const ianaName = typeof InterceptTime !== 'undefined' ? InterceptTime.getIANA() : undefined;

        hours.forEach((h, i) => {
            if (h === 24) {
                spans[i].textContent = '24:00';
                return;
            }
            if (tz === 'UTC' || tz === 'local') {
                spans[i].textContent = `${String(h).padStart(2, '0')}:00`;
            } else {
                const d = new Date();
                d.setHours(h, 0, 0, 0);
                const opts = { hour: '2-digit', minute: '2-digit', hour12: false };
                if (ianaName) opts.timeZone = ianaName;
                spans[i].textContent = d.toLocaleTimeString(undefined, opts).slice(0, 5);
            }
        });
    }

    /**
     * Update the pass analysis bar with stats about current passes.
     */
    function updatePassAnalysis(passList) {
        const totalEl = document.getElementById('wxsatAnalysisTotal');
        const excellentEl = document.getElementById('wxsatAnalysisExcellent');
        const goodEl = document.getElementById('wxsatAnalysisGood');
        const fairEl = document.getElementById('wxsatAnalysisFair');
        const bestEl = document.getElementById('wxsatAnalysisBest');

        const now = new Date();
        const upcoming = passList.filter(p => {
            const end = parsePassDate(p.endTimeISO);
            return end && end > now;
        });

        const excellent = upcoming.filter(p => p.quality === 'excellent').length;
        const good = upcoming.filter(p => p.quality === 'good').length;
        const fair = upcoming.filter(p => p.quality === 'fair').length;

        if (totalEl) totalEl.textContent = upcoming.length;
        if (excellentEl) excellentEl.textContent = excellent;
        if (goodEl) goodEl.textContent = good;
        if (fairEl) fairEl.textContent = fair;

        const best = findBestPass(passList);
        if (bestEl) {
            if (best) {
                const t = formatShortTime(best.startTimeISO) + getTZLabel();
                const bestDurMin = Math.round((best.duration || 0) / 60);
                bestEl.textContent = `Best: ${best.name} at ${t} (${best.maxEl}\u00b0 el, ${bestDurMin} min)`;
            } else {
                bestEl.textContent = 'No upcoming passes';
            }
        }
    }

    // ========================
    // Auto-Scheduler
    // ========================

    /**
     * Toggle auto-scheduler
     */
    async function toggleScheduler(source) {
        const checked = source?.checked ?? false;

        const stripCheckbox = document.getElementById('wxsatAutoSchedule');
        const sidebarCheckbox = document.getElementById('wxsatSidebarAutoSchedule');

        // Sync both checkboxes to the source of truth
        if (stripCheckbox) stripCheckbox.checked = checked;
        if (sidebarCheckbox) sidebarCheckbox.checked = checked;

        if (checked) {
            await enableScheduler();
        } else {
            await disableScheduler();
        }
    }

    /**
     * Enable auto-scheduler
     */
    async function enableScheduler() {
        let lat, lon;
        if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
            const shared = ObserverLocation.getShared();
            lat = shared?.lat;
            lon = shared?.lon;
        } else {
            lat = parseFloat(localStorage.getItem('observerLat'));
            lon = parseFloat(localStorage.getItem('observerLon'));
        }

        if (isNaN(lat) || isNaN(lon)) {
            showNotification('Weather Sat', 'Set observer location first');
            const stripCheckbox = document.getElementById('wxsatAutoSchedule');
            const sidebarCheckbox = document.getElementById('wxsatSidebarAutoSchedule');
            if (stripCheckbox) stripCheckbox.checked = false;
            if (sidebarCheckbox) sidebarCheckbox.checked = false;
            return;
        }

        const deviceSelect = document.getElementById('deviceSelect');
        const gainInput = document.getElementById('weatherSatGain');
        const biasTInput = document.getElementById('weatherSatBiasT');

        try {
            const response = await fetch('/weather-sat/schedule/enable', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    latitude: lat,
                    longitude: lon,
                    device: parseInt(deviceSelect?.value || '0', 10),
                    gain: parseFloat(gainInput?.value || '40'),
                    bias_t: biasTInput?.checked || false,
                }),
            });

            let data = {};
            try {
                data = await response.json();
            } catch (err) {
                data = {};
            }

            if (!response.ok || !data || data.enabled !== true) {
                schedulerEnabled = false;
                updateSchedulerUI({ enabled: false, scheduled_count: 0 });
                showNotification('Weather Sat', data.message || 'Failed to enable auto-scheduler');
                return;
            }

            schedulerEnabled = true;
            updateSchedulerUI(data);
            startStream();
            showNotification('Weather Sat', `Auto-scheduler enabled (${data.scheduled_count || 0} passes)`);
        } catch (err) {
            console.error('Failed to enable scheduler:', err);
            reportActionableError('Enable Scheduler', err, {
                onRetry: () => enableScheduler()
            });
            schedulerEnabled = false;
            updateSchedulerUI({ enabled: false, scheduled_count: 0 });
        }
    }

    /**
     * Disable auto-scheduler
     */
    async function disableScheduler() {
        try {
            const response = await fetch('/weather-sat/schedule/disable', { method: 'POST' });
            if (!response.ok) {
                showNotification('Weather Sat', 'Failed to disable auto-scheduler');
                return;
            }
            schedulerEnabled = false;
            updateSchedulerUI({ enabled: false });
            if (!isRunning) stopStream();
            showNotification('Weather Sat', 'Auto-scheduler disabled');
        } catch (err) {
            console.error('Failed to disable scheduler:', err);
            reportActionableError('Disable Scheduler', err);
        }
    }

    /**
     * Check current scheduler status
     */
    async function checkSchedulerStatus() {
        try {
            const response = await fetch('/weather-sat/schedule/status');
            if (!response.ok) return;
            const data = await response.json();
            schedulerEnabled = data.enabled;
            updateSchedulerUI(data);
            if (schedulerEnabled) startStream();
        } catch (err) {
            // Scheduler endpoint may not exist yet
        }
    }

    /**
     * Update scheduler UI elements
     */
    function updateSchedulerUI(data) {
        const stripCheckbox = document.getElementById('wxsatAutoSchedule');
        const sidebarCheckbox = document.getElementById('wxsatSidebarAutoSchedule');
        const statusEl = document.getElementById('wxsatSchedulerStatus');

        if (stripCheckbox) stripCheckbox.checked = data.enabled;
        if (sidebarCheckbox) sidebarCheckbox.checked = data.enabled;
        if (statusEl) {
            if (data.enabled) {
                statusEl.textContent = `Active: ${data.scheduled_count || 0} passes queued`;
                statusEl.style.color = '#00ff88';
            } else {
                statusEl.textContent = 'Disabled';
                statusEl.style.color = '';
            }
        }
    }

    // ========================
    // Images
    // ========================

    /**
     * Load decoded images
     */
    async function loadImages() {
        try {
            const satSelect = document.getElementById('weatherSatSelect');
            const selectedSatellite = satSelect?.value || '';
            const url = selectedSatellite
                ? `/weather-sat/images?satellite=${encodeURIComponent(selectedSatellite)}`
                : '/weather-sat/images';
            const response = await fetch(url);
            const data = await response.json();

            if (data.status === 'ok') {
                images = data.images || [];
                updateImageCount(images.length);
                renderGallery();
            }
        } catch (err) {
            console.error('Failed to load weather sat images:', err);
        }
    }

    /**
     * Update image count
     */
    function updateImageCount(count) {
        const countEl = document.getElementById('wxsatImageCount');
        const stripCount = document.getElementById('wxsatStripImageCount');
        if (countEl) countEl.textContent = count;
        if (stripCount) stripCount.textContent = count;
    }

    /**
     * Render image gallery grouped by date
     */
    function renderGallery() {
        const gallery = document.getElementById('wxsatGallery');
        if (!gallery) return;

        if (images.length === 0) {
            gallery.innerHTML = `
                <div class="wxsat-gallery-empty">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <circle cx="12" cy="12" r="10"/>
                        <path d="M2 12h20"/>
                        <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                    </svg>
                    <p>No images decoded yet</p>
                    <p style="margin-top: 4px; font-size: 11px;">Select a satellite pass and start capturing</p>
                </div>
            `;
            return;
        }

        // Sort by timestamp descending
        const sorted = [...images].sort((a, b) => {
            return new Date(b.timestamp || 0) - new Date(a.timestamp || 0);
        });

        // Group by date (timezone-aware via global InterceptTime)
        const groups = {};
        sorted.forEach(img => {
            let dateKey = 'Unknown Date';
            if (img.timestamp) {
                dateKey = typeof InterceptTime !== 'undefined'
                    ? InterceptTime.dateOnly(img.timestamp)
                    : new Date(img.timestamp).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
            }
            if (!groups[dateKey]) groups[dateKey] = [];
            groups[dateKey].push(img);
        });

        let html = '';
        for (const [date, imgs] of Object.entries(groups)) {
            html += `<div class="wxsat-date-header">${escapeHtml(date)}</div>`;
            html += imgs.map(img => {
                const fn = escapeHtml(img.filename || img.url.split('/').pop());
                const deleteButton = img.deletable === false ? '' : `
                    <div class="wxsat-image-actions">
                        <button onclick="event.stopPropagation(); WeatherSat.deleteImage('${fn}')" title="Delete image">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                            </svg>
                        </button>
                    </div>`;
                return `
                <div class="wxsat-image-card">
                    <div class="wxsat-image-clickable" onclick="WeatherSat.showImage('${escapeHtml(img.url)}', '${escapeHtml(img.satellite)}', '${escapeHtml(img.product)}', '${fn}')">
                        <img src="${escapeHtml(img.url)}" alt="${escapeHtml(img.satellite)} ${escapeHtml(img.product)}" class="wxsat-image-preview" loading="lazy">
                        <div class="wxsat-image-info">
                            <div class="wxsat-image-sat">${escapeHtml(img.satellite)}</div>
                            <div class="wxsat-image-product">${escapeHtml(img.product || img.mode)}</div>
                            <div class="wxsat-image-timestamp">${formatTimestamp(img.timestamp)}</div>
                        </div>
                    </div>
                    ${deleteButton}
                </div>`;
            }).join('');
        }

        gallery.innerHTML = html;
    }

    /**
     * Show full-size image
     */
    function showImage(url, satellite, product, filename) {
        currentModalFilename = filename || null;

        let modal = document.getElementById('wxsatImageModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'wxsatImageModal';
            modal.className = 'wxsat-image-modal';
            modal.innerHTML = `
                <div class="wxsat-modal-toolbar">
                    <button class="wxsat-modal-btn delete" onclick="WeatherSat.deleteImage(WeatherSat._getModalFilename())" title="Delete image">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                        </svg>
                    </button>
                </div>
                <button class="wxsat-modal-close" onclick="WeatherSat.closeImage()">&times;</button>
                <img src="" alt="Weather Satellite Image">
                <div class="wxsat-modal-info"></div>
            `;
            modal.addEventListener('click', (e) => {
                if (e.target === modal) closeImage();
            });
            document.body.appendChild(modal);
        }

        modal.querySelector('img').src = url;
        const info = modal.querySelector('.wxsat-modal-info');
        if (info) {
            info.textContent = `${satellite || ''} ${product ? '// ' + product : ''}`;
        }
        modal.classList.add('show');
    }

    /**
     * Close image modal
     */
    function closeImage() {
        const modal = document.getElementById('wxsatImageModal');
        if (modal) modal.classList.remove('show');
    }

    /**
     * Delete a single image
     */
    async function deleteImage(filename) {
        if (!filename) return;
        const confirmed = await AppFeedback.confirmAction({
            title: 'Delete Image',
            message: 'Delete this image? This cannot be undone.',
            confirmLabel: 'Delete',
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;

        try {
            const response = await fetch(`/weather-sat/images/${encodeURIComponent(filename)}`, { method: 'DELETE' });
            const data = await response.json();

            if (data.status === 'deleted') {
                images = images.filter(img => {
                    const imgFn = img.filename || img.url.split('/').pop();
                    return imgFn !== filename;
                });
                updateImageCount(images.length);
                renderGallery();
                closeImage();
            } else {
                showNotification('Weather Sat', data.message || 'Failed to delete image');
            }
        } catch (err) {
            console.error('Failed to delete image:', err);
            reportActionableError('Delete Image', err);
        }
    }

    /**
     * Delete all images
     */
    async function deleteAllImages() {
        if (images.length === 0) return;
        const deletableCount = images.filter(img => img.deletable !== false).length;
        if (deletableCount === 0) {
            showNotification('Weather Sat', 'Only shared ground-station imagery is available here');
            return;
        }
        const confirmed = await AppFeedback.confirmAction({
            title: 'Delete All Images',
            message: `Delete all ${deletableCount} local decoded images? Shared ground-station outputs will be kept.`,
            confirmLabel: 'Delete All',
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;

        try {
            const response = await fetch('/weather-sat/images', { method: 'DELETE' });
            const data = await response.json();

            if (data.status === 'ok') {
                images = images.filter(img => img.deletable === false);
                updateImageCount(images.length);
                renderGallery();
                showNotification('Weather Sat', `Deleted ${data.deleted} images`);
            } else {
                showNotification('Weather Sat', 'Failed to delete images');
            }
        } catch (err) {
            console.error('Failed to delete all images:', err);
            reportActionableError('Delete All Images', err);
        }
    }

    /**
     * Format timestamp
     */
    function formatTimestamp(isoString) {
        if (!isoString) return '--';
        return formatDateTime(isoString) + getTZLabel();
    }

    function ensureImageRefresh() {
        if (imageRefreshInterval) return;
        imageRefreshInterval = setInterval(() => {
            const mode = document.getElementById('weatherSatMode');
            if (!mode || !mode.classList.contains('active')) return;
            loadImages();
            loadLatestDecodeJob();
        }, 30000);
    }

    function getSelectedMeteorNorad() {
        const satSelect = document.getElementById('weatherSatSelect');
        const satellite = satSelect?.value || '';
        return METEOR_NORAD_IDS[satellite] || null;
    }

    async function loadLatestDecodeJob() {
        const norad = getSelectedMeteorNorad();
        if (!norad) return;
        const satSelect = document.getElementById('weatherSatSelect');
        const satellite = satSelect?.value || null;

        if (satellite !== lastDecodeSatellite) {
            lastDecodeSatellite = satellite;
            lastDecodeJobSignature = null;
        }

        try {
            const response = await fetch(`/ground_station/decode-jobs?norad_id=${encodeURIComponent(norad)}&backend=meteor_lrpt&limit=1`);
            const jobs = await response.json();
            if (!Array.isArray(jobs) || !jobs.length) {
                resetDecodeJobDisplay();
                return;
            }

            const job = jobs[0];
            const details = job.details || {};
            const signature = `${job.id}:${job.status}:${job.error_message || ''}`;
            const captureStatus = document.getElementById('wxsatCaptureStatus');
            const captureMsg = document.getElementById('wxsatCaptureMsg');
            const captureElapsed = document.getElementById('wxsatCaptureElapsed');
            const summary = formatDecodeJobSummary(job, details);

            if (!isRunning) {
                if (job.status === 'queued') {
                    updateStatusUI('idle', 'Decode queued');
                    if (captureMsg) captureMsg.textContent = summary;
                    if (captureElapsed) captureElapsed.textContent = '--';
                    if (captureStatus) captureStatus.classList.add('active');
                } else if (job.status === 'decoding') {
                    updateStatusUI('decoding', 'Ground-station decode running');
                    if (captureMsg) captureMsg.textContent = summary;
                    if (captureStatus) captureStatus.classList.add('active');
                } else if (job.status === 'failed') {
                    updateStatusUI('idle', 'Last decode failed');
                    if (captureMsg) captureMsg.textContent = summary;
                    if (captureElapsed) captureElapsed.textContent = formatDecodeJobMeta(details);
                    if (captureStatus) captureStatus.classList.remove('active');
                    if (signature !== lastDecodeJobSignature) {
                        showConsole(true);
                        addConsoleEntry(summary, 'error');
                        const context = formatDecodeJobContext(details);
                        if (context) addConsoleEntry(context, 'warning');
                    }
                } else if (job.status === 'complete') {
                    const count = details.output_count;
                    updateStatusUI('idle', count ? `Last decode: ${count} image${count === 1 ? '' : 's'}` : 'Last decode complete');
                    if (captureMsg) captureMsg.textContent = summary;
                    if (captureElapsed) captureElapsed.textContent = formatDecodeJobMeta(details);
                    if (captureStatus) captureStatus.classList.remove('active');
                    if (signature !== lastDecodeJobSignature) {
                        addConsoleEntry(
                            count ? `Ground-station decode complete: ${count} image${count === 1 ? '' : 's'} produced`
                                  : 'Ground-station decode complete',
                            'signal'
                        );
                    }
                }
            }

            lastDecodeJobSignature = signature;
        } catch (err) {
            console.error('Failed to load latest decode job:', err);
        }
    }

    function resetDecodeJobDisplay() {
        if (isRunning) return;
        const captureStatus = document.getElementById('wxsatCaptureStatus');
        const captureMsg = document.getElementById('wxsatCaptureMsg');
        const captureElapsed = document.getElementById('wxsatCaptureElapsed');
        if (captureStatus) captureStatus.classList.remove('active');
        if (captureMsg) captureMsg.textContent = '--';
        if (captureElapsed) captureElapsed.textContent = '--';
        updateStatusUI('idle', 'Idle');
    }

    function formatDecodeJobSummary(job, details) {
        if (job.status === 'queued') return 'Ground-station decode queued';
        if (job.status === 'decoding') return details.message || 'Ground-station decode in progress';
        if (job.status === 'complete') {
            const count = details.output_count;
            return count ? `Ground-station decode complete: ${count} image${count === 1 ? '' : 's'} produced`
                         : 'Ground-station decode complete';
        }
        if (job.status === 'failed') {
            const reasonLabels = {
                sample_rate_too_low: 'Sample rate too low for Meteor LRPT',
                invalid_sample_rate: 'Sample rate rejected by decoder',
                recording_too_small: 'Recording too small for useful decode',
                satdump_failed: 'SatDump decode failed',
                permission_error: 'Decoder could not access recording/output path',
                input_missing: 'Input recording was not accessible',
                missing_recording: 'Recording was missing when decode started',
                no_imagery_produced: 'Decode produced no imagery',
            };
            return job.error_message || reasonLabels[details.reason] || details.message || 'Last decode failed';
        }
        return details.message || 'Decode status unavailable';
    }

    function formatDecodeJobMeta(details) {
        const parts = [];
        if (details.sample_rate) parts.push(`${Number(details.sample_rate).toLocaleString()} Hz`);
        if (details.file_size_human) parts.push(details.file_size_human);
        return parts.join(' / ') || '--';
    }

    function formatDecodeJobContext(details) {
        const parts = [];
        if (details.reason) parts.push(`Reason: ${String(details.reason).replace(/_/g, ' ')}`);
        if (details.sample_rate) parts.push(`Sample rate ${Number(details.sample_rate).toLocaleString()} Hz`);
        if (details.file_size_human) parts.push(`Recording ${details.file_size_human}`);
        if (details.last_returncode !== undefined && details.last_returncode !== null) {
            parts.push(`Exit code ${details.last_returncode}`);
        }
        return parts.join(' | ');
    }

    /**
     * Escape HTML
     */
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Invalidate ground map size (call after container becomes visible)
     */
    function invalidateMap() {
        setTimeout(() => {
            if (!groundMap) {
                initGroundMap();
                return;
            }
            groundMap.invalidateSize(false);
            updateGroundTrack(getSelectedPass());
        }, 100);
    }

    // ========================
    // Decoder Console
    // ========================

    /**
     * Add an entry to the decoder console log
     */
    function addConsoleEntry(message, logType) {
        const log = document.getElementById('wxsatConsoleLog');
        if (!log) return;

        const type = logType || 'info';
        const now = new Date();
        const ts = typeof InterceptTime !== 'undefined'
            ? InterceptTime.fullTime(now)
            : now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });

        const entry = document.createElement('div');
        entry.className = `wxsat-console-entry wxsat-log-${type}`;
        entry.dataset.logType = type;
        entry.innerHTML = `<span class="wxsat-console-ts">${ts}</span>${escapeHtml(message)}`;

        // Apply current filter visibility
        if (consoleFilter !== 'all' && type !== consoleFilter) {
            entry.style.display = 'none';
        }

        log.appendChild(entry);
        consoleEntries.push(entry);

        // Cap at 200 entries
        while (consoleEntries.length > 200) {
            const old = consoleEntries.shift();
            if (old.parentNode) old.parentNode.removeChild(old);
        }

        // Auto-scroll to bottom
        log.scrollTop = log.scrollHeight;
    }

    /**
     * Filter console entries by log type.
     */
    function filterConsole(filter) {
        consoleFilter = filter;
        // Update filter button states
        document.querySelectorAll('.wxsat-console-filter').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.filter === filter);
        });
        // Show/hide entries
        consoleEntries.forEach(entry => {
            if (filter === 'all') {
                entry.style.display = '';
            } else {
                entry.style.display = entry.dataset.logType === filter ? '' : 'none';
            }
        });
        // Scroll to bottom
        const log = document.getElementById('wxsatConsoleLog');
        if (log) log.scrollTop = log.scrollHeight;
    }

    /**
     * Export console contents to clipboard.
     */
    function exportConsole() {
        const text = consoleEntries.map(e => e.textContent).join('\n');
        navigator.clipboard.writeText(text).then(() => {
            showNotification('Weather Sat', 'Console log copied to clipboard');
        }).catch(() => {
            showNotification('Weather Sat', 'Failed to copy console log');
        });
    }

    /**
     * Update the phase indicator steps
     */
    function updatePhaseIndicator(phase) {
        if (!phase || phase === currentPhase) return;
        currentPhase = phase;

        const phases = ['tuning', 'listening', 'signal_detected', 'decoding', 'complete'];
        const phaseIndex = phases.indexOf(phase);
        const isError = phase === 'error';

        document.querySelectorAll('#wxsatPhaseIndicator .wxsat-phase-step').forEach(step => {
            const stepPhase = step.dataset.phase;
            const stepIndex = phases.indexOf(stepPhase);

            step.classList.remove('active', 'completed', 'error');

            if (isError) {
                if (stepPhase === currentPhase || stepIndex === phaseIndex) {
                    step.classList.add('error');
                }
            } else if (stepIndex === phaseIndex) {
                step.classList.add('active');
            } else if (stepIndex < phaseIndex && phaseIndex >= 0) {
                step.classList.add('completed');
            }
        });
    }

    /**
     * Show or hide the decoder console
     */
    function showConsole(visible) {
        const el = document.getElementById('wxsatSignalConsole');
        if (el) el.classList.toggle('active', visible);

        if (consoleAutoHideTimer) {
            clearTimeout(consoleAutoHideTimer);
            consoleAutoHideTimer = null;
        }
    }

    /**
     * Toggle console body collapsed state
     */
    function toggleConsole() {
        const body = document.getElementById('wxsatConsoleBody');
        const btn = document.getElementById('wxsatConsoleToggle');
        if (!body) return;

        consoleCollapsed = !consoleCollapsed;
        body.classList.toggle('collapsed', consoleCollapsed);
        if (btn) btn.classList.toggle('collapsed', consoleCollapsed);
    }

    /**
     * Clear console entries and reset phase indicator
     */
    function clearConsole() {
        const log = document.getElementById('wxsatConsoleLog');
        if (log) log.innerHTML = '';
        consoleEntries = [];
        consoleFilter = 'all';
        currentPhase = 'idle';

        // Reset filter buttons
        document.querySelectorAll('.wxsat-console-filter').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.filter === 'all');
        });

        document.querySelectorAll('#wxsatPhaseIndicator .wxsat-phase-step').forEach(step => {
            step.classList.remove('active', 'completed', 'error');
        });

        if (consoleAutoHideTimer) {
            clearTimeout(consoleAutoHideTimer);
            consoleAutoHideTimer = null;
        }
    }

    /**
     * Suspend background activity when leaving the mode.
     * Closes the SSE stream and stops the countdown interval so they don't
     * keep running while another mode is active.  The stream is re-opened
     * by init() or startStream() when the mode is next entered.
     */
    function suspend() {
        if (countdownInterval) {
            clearInterval(countdownInterval);
            countdownInterval = null;
        }
        // Only close the stream if nothing is actively capturing/scheduling —
        // if a capture or scheduler is running we want it to continue on the
        // server and the stream will reconnect on next init().
        if (!isRunning && !schedulerEnabled) {
            stopStream();
        }
    }

    /**
     * Unconditionally tear down the SSE stream on mode switch so we don't
     * leak browser connections.  The server-side capture/scheduler keeps
     * running independently — the stream will reconnect on next init().
     */
    function destroy() {
        if (countdownInterval) {
            clearInterval(countdownInterval);
            countdownInterval = null;
        }
        stopStream();
    }

    /**
     * Load demo/sample data for UI testing without a live satellite pass.
     * Populates passes, console, and analysis bar with realistic fake data.
     */
    function loadDemoData() {
        const now = new Date();

        // Generate sample passes over next 24h
        const demoSats = ['METEOR-M2-3', 'METEOR-M2-4'];
        const demoPasses = [];

        const offsets = [25, 95, 200, 340, 510, 720, 880, 1020];
        const elevations = [72, 45, 28, 63, 18, 55, 82, 35];
        const durations = [840, 720, 480, 780, 360, 660, 900, 600]; // seconds
        const riseAzs = [350, 15, 200, 310, 170, 40, 280, 90];
        const setAzs = [170, 195, 20, 130, 350, 220, 100, 270];

        offsets.forEach((offset, i) => {
            const start = new Date(now.getTime() + offset * 60000);
            const end = new Date(start.getTime() + durations[i] * 1000);
            const sat = demoSats[i % 2];
            const el = elevations[i];
            const quality = el >= 60 ? 'excellent' : el >= 30 ? 'good' : 'fair';

            demoPasses.push({
                id: `${sat}_demo_${i}`,
                satellite: sat,
                name: sat === 'METEOR-M2-3' ? 'Meteor-M2-3' : 'Meteor-M2-4',
                frequency: 137.9,
                mode: 'LRPT',
                startTime: start.toISOString().replace('T', ' ').slice(0, 16) + ' UTC',
                startTimeISO: start.toISOString(),
                endTimeISO: end.toISOString(),
                maxEl: el,
                maxElAz: (riseAzs[i] + setAzs[i]) / 2,
                riseAz: riseAzs[i],
                setAz: setAzs[i],
                duration: durations[i],
                quality: quality,
                trajectory: [],
                groundTrack: [],
            });
        });

        allPasses = demoPasses;
        applyPassFilter();

        // Simulate console output
        clearConsole();
        showConsole(true);
        const demoLogs = [
            ['SatDump v1.2.2 initialized', 'info'],
            ['Pipeline: meteor_m2-x_lrpt', 'info'],
            ['Frequency: 137.900 MHz | Sample rate: 2.4 MHz', 'info'],
            ['RTL-SDR device 0 (SN: 00000101) opened', 'info'],
            ['Tuning to 137900000 Hz...', 'info'],
            ['Gain set to 40.0 dB', 'debug'],
            ['Waiting for signal...', 'info'],
            ['LRPT signal detected! SNR: 8.2 dB', 'signal'],
            ['Viterbi lock acquired', 'signal'],
            ['Frame sync OK - decoding frames', 'signal'],
            ['Decoding LRPT... 15%', 'progress'],
            ['Decoding LRPT... 30%', 'progress'],
            ['Decoding LRPT... 45%', 'progress'],
            ['Channel 1 (visible) - 1540 lines', 'info'],
            ['Channel 2 (infrared) - 1540 lines', 'info'],
            ['Decoding LRPT... 60%', 'progress'],
            ['Decoding LRPT... 75%', 'progress'],
            ['Decoding LRPT... 90%', 'progress'],
            ['Image saved: meteor_m2-3_rgb_composite.png (2.4 MB)', 'save'],
            ['Image saved: meteor_m2-3_channel_1.png (1.1 MB)', 'save'],
            ['Image saved: meteor_m2-3_thermal.png (1.3 MB)', 'save'],
            ['Decoding complete - 3 images produced', 'info'],
            ['Signal lost - satellite below horizon', 'warning'],
            ['Pass duration: 13m 42s', 'info'],
        ];

        demoLogs.forEach((entry, i) => {
            setTimeout(() => addConsoleEntry(entry[0], entry[1]), i * 120);
        });

        showNotification('Weather Sat', 'Demo data loaded - showing sample passes and console output');
    }

    // Public API
    return {
        init,
        suspend,
        destroy,
        start,
        stop,
        preSelect,
        startPass,
        selectPass,
        testDecode,
        loadImages,
        loadPasses,
        showImage,
        closeImage,
        deleteImage,
        deleteAllImages,
        useGPS,
        toggleScheduler,
        invalidateMap,
        toggleConsole,
        setTimezone,
        filterConsole,
        exportConsole,
        clearConsole,
        loadDemoData,
        _getModalFilename: () => currentModalFilename,
    };
})();

document.addEventListener('DOMContentLoaded', function() {
    // Initialization happens via selectMode when weather-satellite mode is activated
});
