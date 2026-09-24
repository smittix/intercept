/**
 * SSTV Mode
 * ISS Slow-Scan Television decoder interface
 */

const SSTV = (function() {
    // State
    let isRunning = false;
    let eventSource = null;
    let images = [];
    let currentMode = null;
    let progress = 0;
    let issMap = null;
    let issMarker = null;
    let issTrackLine = null;
    let issTrackPast = null;
    let issPosition = null;
    let issUpdateInterval = null;
    let issTrackInterval = null;
    let countdownInterval = null;
    let nextPassData = null;
    let pendingMapInvalidate = false;
    let locationListenersAttached = false;

    // ISS frequency
    const ISS_FREQ = 145.800;
    const ISS_MODULATION = 'fm';

    // Signal scope state
    let sstvScopeCtx = null;
    let sstvScopeAnim = null;
    let sstvScopeHistory = [];
    const SSTV_SCOPE_LEN = 200;
    let sstvScopeRms = 0;
    let sstvScopePeak = 0;
    let sstvScopeTargetRms = 0;
    let sstvScopeTargetPeak = 0;
    let sstvScopeMsgBurst = 0;
    let sstvScopeTone = null;

    /**
     * Initialize the SSTV mode
     */
    function init() {
        // Load location inputs first (sync localStorage read needed for lat/lon params)
        loadLocationInputs();

        // Fire all API calls in parallel — schedule is the slowest, don't let it block
        Promise.all([
            checkStatus(),
            loadImages(),
            loadIssSchedule(),
            updateIssPosition(),
        ]).catch(err => console.error('SSTV init error:', err));

        // DOM-only setup (no network, fast)
        initMap();
        startCountdown();
        // ISS tracking interval (first call already in Promise.all above)
        if (issUpdateInterval) clearInterval(issUpdateInterval);
        issUpdateInterval = setInterval(updateIssPosition, 5000);
        // Ensure Leaflet recomputes dimensions after the SSTV pane becomes visible.
        setTimeout(() => invalidateMap(), 80);
        setTimeout(() => invalidateMap(), 260);
    }

    function isMapContainerVisible() {
        if (!issMap || typeof issMap.getContainer !== 'function') return false;
        const container = issMap.getContainer();
        if (!container) return false;
        if (container.offsetWidth <= 0 || container.offsetHeight <= 0) return false;
        if (container.style && container.style.display === 'none') return false;
        if (typeof window.getComputedStyle === 'function') {
            const style = window.getComputedStyle(container);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
        }
        return true;
    }

    /**
     * Load location into input fields
     */
    function loadLocationInputs() {
        const latInput = document.getElementById('sstvObsLat');
        const lonInput = document.getElementById('sstvObsLon');

        let storedLat = localStorage.getItem('observerLat');
        let storedLon = localStorage.getItem('observerLon');
        if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
            const shared = ObserverLocation.getShared();
            storedLat = shared.lat.toString();
            storedLon = shared.lon.toString();
        }

        if (latInput && storedLat) latInput.value = storedLat;
        if (lonInput && storedLon) lonInput.value = storedLon;

        if (!locationListenersAttached) {
            if (latInput) latInput.addEventListener('change', saveLocationFromInputs);
            if (lonInput) lonInput.addEventListener('change', saveLocationFromInputs);
            locationListenersAttached = true;
        }
    }

    /**
     * Save location from input fields
     */
    function saveLocationFromInputs() {
        const latInput = document.getElementById('sstvObsLat');
        const lonInput = document.getElementById('sstvObsLon');

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
            loadIssSchedule(); // Refresh pass predictions
        }
    }

    /**
     * Use GPS to get location
     */
    function useGPS(btn) {
        if (!navigator.geolocation) {
            showNotification('SSTV', 'GPS not available in this browser');
            return;
        }

        const originalText = btn.innerHTML;
        btn.innerHTML = '<span style="opacity: 0.7;">...</span>';
        btn.disabled = true;

        navigator.geolocation.getCurrentPosition(
            (pos) => {
                const latInput = document.getElementById('sstvObsLat');
                const lonInput = document.getElementById('sstvObsLon');

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

                showNotification('SSTV', 'Location updated from GPS');
                loadIssSchedule();
            },
            (err) => {
                btn.innerHTML = originalText;
                btn.disabled = false;

                let msg = 'Failed to get location';
                if (err.code === 1) msg = 'Location access denied';
                else if (err.code === 2) msg = 'Location unavailable';
                showNotification('SSTV', msg);
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );
    }

    /**
     * Update TLE data from CelesTrak
     */
    async function updateTLE(btn) {
        const originalText = btn.innerHTML;
        btn.innerHTML = '<span style="opacity: 0.7;">Updating...</span>';
        btn.disabled = true;

        try {
            const response = await fetch('/satellite/update-tle', { method: 'POST' });
            const data = await response.json();

            if (data.status === 'success') {
                showNotification('SSTV', `TLE updated: ${data.updated?.length || 0} satellites`);
                loadIssSchedule(); // Refresh predictions with new TLE
            } else {
                showNotification('SSTV', data.message || 'TLE update failed');
            }
        } catch (err) {
            console.error('TLE update error:', err);
            showNotification('SSTV', 'Failed to update TLE');
        }

        btn.innerHTML = originalText;
        btn.disabled = false;
    }

    /**
     * Initialize Leaflet map for ISS tracking
     */
    async function initMap() {
        const mapContainer = document.getElementById('sstvIssMap');
        if (!mapContainer || issMap) return;

        // Create map
        issMap = L.map('sstvIssMap', {
            center: [0, 0],
            zoom: 1,
            minZoom: 1,
            maxZoom: 6,
            zoomControl: true,
            attributionControl: false,
            worldCopyJump: true
        });
        window.issMap = issMap;

        // Add fallback tiles immediately so the map is visible instantly. If Settings
        // already loaded elsewhere this session, use it directly instead — avoids a
        // flash of the unkeyed CartoDB URL when a CARTO API key has been configured.
        const fallbackTiles = (typeof Settings !== 'undefined' && Settings._initialized && Settings.createTileLayer)
            ? Settings.createTileLayer().addTo(issMap)
            : L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                maxZoom: 19,
                className: 'tile-layer-cyan'
            }).addTo(issMap);

        // Upgrade tiles in background via Settings (with timeout fallback)
        if (typeof Settings !== 'undefined') {
            try {
                await Promise.race([
                    Settings.init(),
                    new Promise((_, reject) => setTimeout(() => reject(new Error('Settings timeout')), 5000))
                ]);
                issMap.removeLayer(fallbackTiles);
                Settings.createTileLayer().addTo(issMap);
                Settings.registerMap(issMap);
            } catch (e) {
                console.warn('SSTV: Settings init failed/timed out, using fallback tiles:', e);
            }
        }

        if (typeof MapUtils !== 'undefined') MapUtils.addGraticuleControl(issMap);

        // Create ISS icon
        const issIcon = L.divIcon({
            className: 'sstv-iss-marker',
            html: `<div class="sstv-iss-dot"></div><div class="sstv-iss-label">ISS</div>`,
            iconSize: [40, 40],
            iconAnchor: [20, 20]
        });

        // Create ISS marker (will be positioned when we get data)
        issMarker = L.marker([0, 0], { icon: issIcon }).addTo(issMap);

        // Past track (dimmer, solid)
        issTrackPast = L.polyline([], {
            color: getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00d4ff',
            weight: 1.5,
            opacity: 0.3,
        }).addTo(issMap);

        // Future track (brighter, dashed)
        issTrackLine = L.polyline([], {
            color: getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00d4ff',
            weight: 2,
            opacity: 0.7,
            dashArray: '6, 4'
        }).addTo(issMap);

        issMap.on('resize moveend zoomend', () => {
            if (pendingMapInvalidate) invalidateMap();
        });

        // Initial layout passes for first-time mode load.
        setTimeout(() => invalidateMap(), 40);
        setTimeout(() => invalidateMap(), 180);
    }

    /**
     * Start ISS position tracking
     */
    function startIssTracking() {
        updateIssPosition();
        updateIssTrack();
        if (issUpdateInterval) clearInterval(issUpdateInterval);
        issUpdateInterval = setInterval(updateIssPosition, 5000);
        // Track refreshes every 5 minutes — one orbit is ~93 min so this keeps it current
        if (issTrackInterval) clearInterval(issTrackInterval);
        issTrackInterval = setInterval(updateIssTrack, 5 * 60 * 1000);
    }

    /**
     * Stop ISS tracking
     */
    function stopIssTracking() {
        if (issUpdateInterval) {
            clearInterval(issUpdateInterval);
            issUpdateInterval = null;
        }
        if (issTrackInterval) {
            clearInterval(issTrackInterval);
            issTrackInterval = null;
        }
    }

    /**
     * Fetch and render the ISS ground track from the backend (TLE-propagated).
     */
    async function updateIssTrack() {
        try {
            const response = await fetch('/sstv/iss-track');
            const data = await response.json();
            if (data.status !== 'ok' || !issTrackLine || !issTrackPast) return;

            const pastPts = [], futurePts = [];
            for (const pt of data.track) {
                (pt.past ? pastPts : futurePts).push([pt.lat, pt.lon]);
            }

            // Split future track at antimeridian crossings to avoid long horizontal lines
            const futureSegments = _splitAtAntimeridian(futurePts);
            const pastSegments = _splitAtAntimeridian(pastPts);

            issTrackLine.setLatLngs(futureSegments);
            issTrackPast.setLatLngs(pastSegments);
        } catch (err) {
            console.error('Failed to fetch ISS track:', err);
        }
    }

    /**
     * Split an array of [lat, lon] points into segments at antimeridian crossings.
     */
    function _splitAtAntimeridian(points) {
        const segments = [];
        let current = [];
        for (let i = 0; i < points.length; i++) {
            if (i > 0 && Math.abs(points[i][1] - points[i - 1][1]) > 180) {
                if (current.length > 1) segments.push(current);
                current = [];
            }
            current.push(points[i]);
        }
        if (current.length > 1) segments.push(current);
        return segments;
    }

    /**
     * Start countdown timer
     */
    function startCountdown() {
        if (countdownInterval) VisibleInterval.clear(countdownInterval);
        countdownInterval = VisibleInterval.set(updateCountdown, 1000);
        updateCountdown();
    }

    /**
     * Stop countdown timer
     */
    function stopCountdown() {
        if (countdownInterval) {
            VisibleInterval.clear(countdownInterval);
            countdownInterval = null;
        }
    }

    /**
     * Update countdown display
     */
    function updateCountdown() {
        const valueEl = document.getElementById('sstvCountdownValue');
        const labelEl = document.getElementById('sstvCountdownLabel');
        const statusEl = document.getElementById('sstvCountdownStatus');

        if (!nextPassData || !nextPassData.startTimestamp) {
            if (valueEl) {
                valueEl.textContent = '--:--:--';
                valueEl.className = 'sstv-countdown-value';
            }
            if (labelEl) {
                const hasLocation = localStorage.getItem('observerLat') !== null;
                labelEl.textContent = hasLocation ? 'No passes in 48h' : 'Set location';
            }
            if (statusEl) {
                statusEl.className = 'sstv-countdown-status';
                statusEl.innerHTML = '<span class="sstv-status-dot"></span><span>Waiting for pass data...</span>';
            }
            return;
        }

        const now = Date.now();
        const startTime = nextPassData.startTimestamp;
        const endTime = nextPassData.endTimestamp || (startTime + (nextPassData.durationMinutes || 10) * 60 * 1000);
        const diff = startTime - now;

        if (now >= startTime && now < endTime) {
            // Pass is currently active
            const remaining = endTime - now;
            const mins = Math.floor(remaining / 60000);
            const secs = Math.floor((remaining % 60000) / 1000);

            if (valueEl) {
                valueEl.textContent = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
                valueEl.className = 'sstv-countdown-value active';
            }
            if (labelEl) labelEl.textContent = 'Pass in progress!';
            if (statusEl) {
                statusEl.className = 'sstv-countdown-status active';
                statusEl.innerHTML = '<span class="sstv-status-dot"></span><span>ISS overhead now!</span>';
            }
        } else if (diff > 0) {
            // Countdown to next pass
            const hours = Math.floor(diff / 3600000);
            const mins = Math.floor((diff % 3600000) / 60000);
            const secs = Math.floor((diff % 60000) / 1000);

            if (valueEl) {
                if (hours > 0) {
                    valueEl.textContent = `${hours}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
                } else {
                    valueEl.textContent = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
                }

                // Highlight when pass is imminent (< 5 minutes)
                if (diff < 300000) {
                    valueEl.className = 'sstv-countdown-value imminent';
                } else {
                    valueEl.className = 'sstv-countdown-value';
                }
            }

            if (labelEl) {
                if (diff < 60000) {
                    labelEl.textContent = 'Starting soon!';
                } else if (diff < 300000) {
                    labelEl.textContent = 'Get ready!';
                } else if (diff < 3600000) {
                    labelEl.textContent = 'Until next pass';
                } else {
                    labelEl.textContent = 'Until next pass';
                }
            }

            if (statusEl) {
                if (diff < 300000) {
                    statusEl.className = 'sstv-countdown-status imminent';
                    statusEl.innerHTML = '<span class="sstv-status-dot"></span><span>Pass imminent!</span>';
                } else {
                    statusEl.className = 'sstv-countdown-status has-pass';
                    statusEl.innerHTML = '<span class="sstv-status-dot"></span><span>Next pass scheduled</span>';
                }
            }
        } else {
            // Pass has ended, need to refresh schedule
            loadIssSchedule();
        }
    }

    /**
     * Update countdown panel details
     */
    function updateCountdownDetails(pass) {
        const startEl = document.getElementById('sstvPassStart');
        const maxElEl = document.getElementById('sstvPassMaxEl');
        const durationEl = document.getElementById('sstvPassDuration');
        const directionEl = document.getElementById('sstvPassDirection');

        if (!pass) {
            if (startEl) startEl.textContent = '--:--';
            if (maxElEl) maxElEl.textContent = '--°';
            if (durationEl) durationEl.textContent = '-- min';
            if (directionEl) directionEl.textContent = '--';
            return;
        }

        if (startEl) startEl.textContent = pass.startTime || '--:--';
        if (maxElEl) maxElEl.textContent = (pass.maxEl || '--') + '°';
        if (durationEl) durationEl.textContent = (pass.duration || '--') + ' min';
        if (directionEl) directionEl.textContent = pass.direction || (pass.azStart ? getDirection(pass.azStart) : '--');
    }

    /**
     * Get compass direction from azimuth
     */
    function getDirection(azimuth) {
        if (azimuth === undefined || azimuth === null) return '--';
        const directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
        const index = Math.round(azimuth / 22.5) % 16;
        return directions[index];
    }

    /**
     * Fetch current ISS position
     */
    async function updateIssPosition() {
        const storedLat = localStorage.getItem('observerLat') || '51.5074';
        const storedLon = localStorage.getItem('observerLon') || '-0.1278';

        try {
            const url = `/sstv/iss-position?latitude=${storedLat}&longitude=${storedLon}`;
            const response = await fetch(url);
            const data = await response.json();

            if (data.status === 'ok') {
                issPosition = data;
                updateIssDisplay();
                updateMap();
                console.log('ISS position updated:', data.lat.toFixed(1), data.lon.toFixed(1));
            } else {
                console.warn('ISS position error:', data.message);
            }
        } catch (err) {
            console.error('Failed to get ISS position:', err);
        }
    }

    /**
     * Update ISS position display
     */
    function updateIssDisplay() {
        if (!issPosition) return;

        const latEl = document.getElementById('sstvIssLat');
        const lonEl = document.getElementById('sstvIssLon');
        const altEl = document.getElementById('sstvIssAlt');

        if (latEl) latEl.textContent = issPosition.lat.toFixed(1) + '°';
        if (lonEl) lonEl.textContent = issPosition.lon.toFixed(1) + '°';
        if (altEl) altEl.textContent = Math.round(issPosition.altitude);
    }

    /**
     * Update map with ISS position
     */
    function updateMap() {
        if (!issMap || !issPosition) return;
        if (pendingMapInvalidate) invalidateMap();

        const lat = issPosition.lat;
        const lon = issPosition.lon;

        // Update marker position
        if (issMarker) {
            issMarker.setLatLng([lat, lon]);
        }

        // Track is fetched separately by updateIssTrack() via /sstv/iss-track

        // Pan map to follow ISS only when the map pane is currently renderable.
        if (isMapContainerVisible()) {
            issMap.panTo([lat, lon], { animate: true, duration: 0.5 });
        } else {
            pendingMapInvalidate = true;
        }
    }

    /**
     * Check current decoder status
     */
    async function checkStatus() {
        try {
            const response = await fetch('/sstv/status');
            const data = await response.json();

            if (!data.available) {
                updateStatusUI('unavailable', 'Decoder not installed');
                showStatusMessage('SSTV decoder not available. Install numpy and Pillow: pip install numpy Pillow', 'warning');
                return;
            }

            if (data.running) {
                isRunning = true;
                updateStatusUI('listening', 'Listening...');
                startStream();
            } else {
                updateStatusUI('idle', 'Idle');
            }

            // Update image count
            updateImageCount(data.image_count || 0);
        } catch (err) {
            console.error('Failed to check SSTV status:', err);
        }
    }

    /**
     * Start SSTV decoder
     */
    async function start() {
        const freqInput = document.getElementById('sstvFrequency');
        // Use the global SDR device selector
        const deviceSelect = document.getElementById('deviceSelect');

        const frequency = parseFloat(freqInput?.value || ISS_FREQ);
        const device = parseInt(deviceSelect?.value || '0', 10);

        // Check if device is available
        if (typeof checkDeviceAvailability === 'function' && !checkDeviceAvailability('sstv')) {
            return;
        }

        updateStatusUI('connecting', 'Starting...');

        try {
            const response = await fetch('/sstv/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ frequency, modulation: ISS_MODULATION, device })
            });

            const data = await response.json();

            if (data.status === 'started' || data.status === 'already_running') {
                isRunning = true;
                if (typeof reserveDevice === 'function') {
                    reserveDevice(device, 'sstv');
                }
                const tunedFrequency = Number(data.frequency || frequency);
                const modulationText = String(data.modulation || ISS_MODULATION).toUpperCase();
                updateStatusUI('listening', `${tunedFrequency.toFixed(3)} MHz ${modulationText}`);
                startStream();
                showNotification('SSTV', `Listening on ${tunedFrequency.toFixed(3)} MHz ${modulationText}`);
            } else {
                updateStatusUI('idle', 'Start failed');
                showStatusMessage(data.message || 'Failed to start decoder', 'error');
            }
        } catch (err) {
            console.error('Failed to start SSTV:', err);
            reportActionableError('Start SSTV', err, {
                onRetry: () => start()
            });
            updateStatusUI('idle', 'Error');
        }
    }

    /**
     * Stop SSTV decoder
     */
    async function stop() {
        try {
            await fetch('/sstv/stop', { method: 'POST' });
            isRunning = false;
            if (typeof releaseDevice === 'function') {
                releaseDevice('sstv');
            }
            stopStream();
            updateStatusUI('idle', 'Stopped');
            showNotification('SSTV', 'Decoder stopped');
        } catch (err) {
            console.error('Failed to stop SSTV:', err);
            reportActionableError('Stop SSTV', err);
        }
    }

    /**
     * Update status UI elements
     */
    function updateStatusUI(status, text) {
        const dot = document.getElementById('sstvStripDot');
        const statusText = document.getElementById('sstvStripStatus');
        const startBtn = document.getElementById('sstvStartBtn');
        const stopBtn = document.getElementById('sstvStopBtn');

        if (dot) {
            dot.className = 'sstv-strip-dot';
            if (status === 'listening' || status === 'detecting') {
                dot.classList.add('listening');
            } else if (status === 'decoding') {
                dot.classList.add('decoding');
            } else {
                dot.classList.add('idle');
            }
        }

        if (statusText) {
            statusText.textContent = text || status;
        }

        if (startBtn && stopBtn) {
            if (status === 'listening' || status === 'decoding') {
                startBtn.style.display = 'none';
                stopBtn.style.display = 'inline-block';
            } else {
                startBtn.style.display = 'inline-block';
                stopBtn.style.display = 'none';
            }
        }

        // Update live content area
        const liveContent = document.getElementById('sstvLiveContent');
        if (liveContent) {
            if (status === 'idle' || status === 'unavailable') {
                liveContent.innerHTML = renderIdleState();
            }
        }
    }

    /**
     * Render idle state HTML
     */
    function renderIdleState() {
        return `
            <div class="sstv-idle-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <rect x="3" y="3" width="18" height="18" rx="2"/>
                    <circle cx="12" cy="12" r="3"/>
                    <path d="M3 9h2M19 9h2M3 15h2M19 15h2"/>
                </svg>
                <h4>ISS SSTV Decoder</h4>
                <p>Click Start to listen for SSTV transmissions on 145.800 MHz</p>
            </div>
        `;
    }

    /**
     * Initialize signal scope canvas
     */
    function initSstvScope() {
        const canvas = document.getElementById('sstvScopeCanvas');
        if (!canvas) return;
        const rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * (window.devicePixelRatio || 1);
        canvas.height = rect.height * (window.devicePixelRatio || 1);
        sstvScopeCtx = canvas.getContext('2d');
        sstvScopeHistory = new Array(SSTV_SCOPE_LEN).fill(0);
        sstvScopeRms = 0;
        sstvScopePeak = 0;
        sstvScopeTargetRms = 0;
        sstvScopeTargetPeak = 0;
        sstvScopeMsgBurst = 0;
        sstvScopeTone = null;
        drawSstvScope();
    }

    /**
     * Draw signal scope animation frame
     */
    function drawSstvScope() {
        const ctx = sstvScopeCtx;
        if (!ctx) return;
        const W = ctx.canvas.width;
        const H = ctx.canvas.height;
        const midY = H / 2;

        // Phosphor persistence
        ctx.fillStyle = 'rgba(5, 5, 16, 0.3)';
        ctx.fillRect(0, 0, W, H);

        // Smooth towards target
        sstvScopeRms += (sstvScopeTargetRms - sstvScopeRms) * 0.25;
        sstvScopePeak += (sstvScopeTargetPeak - sstvScopePeak) * 0.15;

        // Push to history
        sstvScopeHistory.push(Math.min(sstvScopeRms / 32768, 1.0));
        if (sstvScopeHistory.length > SSTV_SCOPE_LEN) sstvScopeHistory.shift();

        // Grid lines
        ctx.strokeStyle = 'rgba(60, 40, 80, 0.4)';
        ctx.lineWidth = 0.5;
        for (let i = 1; i < 4; i++) {
            const y = (H / 4) * i;
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
        }
        for (let i = 1; i < 8; i++) {
            const x = (W / 8) * i;
            ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
        }

        // Waveform
        const stepX = W / (SSTV_SCOPE_LEN - 1);
        ctx.strokeStyle = '#c080ff';
        ctx.lineWidth = 1.5;
        ctx.shadowColor = '#c080ff';
        ctx.shadowBlur = 4;

        // Upper half
        ctx.beginPath();
        for (let i = 0; i < sstvScopeHistory.length; i++) {
            const x = i * stepX;
            const amp = sstvScopeHistory[i] * midY * 0.9;
            const y = midY - amp;
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();

        // Lower half (mirror)
        ctx.beginPath();
        for (let i = 0; i < sstvScopeHistory.length; i++) {
            const x = i * stepX;
            const amp = sstvScopeHistory[i] * midY * 0.9;
            const y = midY + amp;
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.shadowBlur = 0;

        // Peak indicator
        const peakNorm = Math.min(sstvScopePeak / 32768, 1.0);
        if (peakNorm > 0.01) {
            const peakY = midY - peakNorm * midY * 0.9;
            ctx.strokeStyle = 'rgba(255, 68, 68, 0.6)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 4]);
            ctx.beginPath(); ctx.moveTo(0, peakY); ctx.lineTo(W, peakY); ctx.stroke();
            ctx.setLineDash([]);
        }

        // Image decode flash
        if (sstvScopeMsgBurst > 0.01) {
            ctx.fillStyle = `rgba(0, 255, 100, ${sstvScopeMsgBurst * 0.15})`;
            ctx.fillRect(0, 0, W, H);
            sstvScopeMsgBurst *= 0.88;
        }

        // Update labels
        const rmsLabel = document.getElementById('sstvScopeRmsLabel');
        const peakLabel = document.getElementById('sstvScopePeakLabel');
        const toneLabel = document.getElementById('sstvScopeToneLabel');
        const statusLabel = document.getElementById('sstvScopeStatusLabel');
        if (rmsLabel) rmsLabel.textContent = Math.round(sstvScopeRms);
        if (peakLabel) peakLabel.textContent = Math.round(sstvScopePeak);
        if (toneLabel) {
            if (sstvScopeTone === 'leader') { toneLabel.textContent = 'LEADER'; toneLabel.style.color = '#0f0'; }
            else if (sstvScopeTone === 'sync') { toneLabel.textContent = 'SYNC'; toneLabel.style.color = '#0ff'; }
            else if (sstvScopeTone === 'decoding') { toneLabel.textContent = 'DECODING'; toneLabel.style.color = '#fa0'; }
            else if (sstvScopeTone === 'noise') { toneLabel.textContent = 'NOISE'; toneLabel.style.color = 'var(--text-dim)'; }
            else { toneLabel.textContent = 'QUIET'; toneLabel.style.color = 'var(--text-dim)'; }
        }
        if (statusLabel) {
            if (sstvScopeRms > 500) { statusLabel.textContent = 'SIGNAL'; statusLabel.style.color = '#0f0'; }
            else { statusLabel.textContent = 'MONITORING'; statusLabel.style.color = 'var(--text-dim)'; }
        }

        sstvScopeAnim = requestAnimationFrame(drawSstvScope);
    }

    /**
     * Stop signal scope
     */
    function stopSstvScope() {
        if (sstvScopeAnim) { cancelAnimationFrame(sstvScopeAnim); sstvScopeAnim = null; }
        sstvScopeCtx = null;
    }

    /**
     * Start SSE stream
     */
    function startStream() {
        if (eventSource) {
            eventSource.close();
        }

        // Show and init scope
        const scopePanel = document.getElementById('sstvScopePanel');
        if (scopePanel) scopePanel.style.display = 'block';
        initSstvScope();

        eventSource = new EventSource('/sstv/stream');

        eventSource.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'sstv_progress') {
                    handleProgress(data);
                } else if (data.type === 'sstv_scope') {
                    sstvScopeTargetRms = data.rms;
                    sstvScopeTargetPeak = data.peak;
                    if (data.tone !== undefined) sstvScopeTone = data.tone;
                }
            } catch (err) {
                console.error('Failed to parse SSE message:', err);
            }
        };

        eventSource.onerror = () => {
            console.warn('SSTV SSE error, will reconnect...');
            setTimeout(() => {
                if (isRunning) startStream();
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
        stopSstvScope();
        const scopePanel = document.getElementById('sstvScopePanel');
        if (scopePanel) scopePanel.style.display = 'none';
    }

    /**
     * Handle progress update
     */
    function handleProgress(data) {
        currentMode = data.mode || currentMode;
        progress = data.progress || 0;

        // Update status based on decode state
        if (data.status === 'decoding') {
            updateStatusUI('decoding', `Decoding ${currentMode || 'image'}...`);
            renderDecodeProgress(data);
        } else if (data.status === 'complete' && data.image) {
            // New image decoded
            images.unshift(data.image);
            updateImageCount(images.length);
            renderGallery();
            showNotification('SSTV', 'New image decoded!');
            updateStatusUI('listening', 'Listening...');
            sstvScopeMsgBurst = 1.0;
            // Clear decode progress so signal monitor can take over
            const liveContent = document.getElementById('sstvLiveContent');
            if (liveContent) liveContent.innerHTML = '';
        } else if (data.status === 'detecting') {
            // Ignore detecting events if currently decoding (e.g. Doppler updates)
            const dot = document.getElementById('sstvStripDot');
            if (dot && dot.classList.contains('decoding')) return;

            updateStatusUI('listening', data.message || 'Listening...');
            if (data.signal_level !== undefined) {
                renderSignalMonitor(data);
            }
        }
    }

    /**
     * Render signal monitor in live area during detecting mode
     */
    function renderSignalMonitor(data) {
        const container = document.getElementById('sstvLiveContent');
        if (!container) return;

        const level = data.signal_level || 0;
        const tone = data.sstv_tone;

        let barColor, statusText;
        if (tone === 'leader') {
            barColor = 'var(--accent-green)';
            statusText = 'SSTV leader tone detected';
        } else if (tone === 'sync') {
            barColor = 'var(--accent-cyan)';
            statusText = 'SSTV sync pulse detected';
        } else if (tone === 'noise') {
            barColor = 'var(--text-dim)';
            statusText = 'Audio signal present';
        } else if (level > 10) {
            barColor = 'var(--text-dim)';
            statusText = 'Audio signal present';
        } else {
            barColor = 'var(--text-dim)';
            statusText = 'No signal';
        }

        let monitor = container.querySelector('.sstv-signal-monitor');
        if (!monitor) {
            container.innerHTML = `
                <div class="sstv-signal-monitor">
                    <div class="sstv-signal-monitor-header">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M2 12L5 12M5 12C5 12 6 3 12 3C18 3 19 12 19 12M19 12L22 12"/>
                            <circle cx="12" cy="18" r="2"/>
                            <path d="M12 16V12"/>
                        </svg>
                        Signal Monitor
                    </div>
                    <div class="sstv-signal-level-row">
                        <span class="sstv-signal-level-label">LEVEL</span>
                        <div class="sstv-signal-bar-track">
                            <div class="sstv-signal-bar-fill" style="width: 0%"></div>
                        </div>
                        <span class="sstv-signal-level-value">0</span>
                    </div>
                    <div class="sstv-signal-status-text">No signal</div>
                    <div class="sstv-signal-vis-state">VIS: idle</div>
                </div>`;
            monitor = container.querySelector('.sstv-signal-monitor');
        }

        const fill = monitor.querySelector('.sstv-signal-bar-fill');
        fill.style.width = level + '%';
        fill.style.background = barColor;
        monitor.querySelector('.sstv-signal-status-text').textContent = statusText;
        monitor.querySelector('.sstv-signal-level-value').textContent = level;

        const visStateEl = monitor.querySelector('.sstv-signal-vis-state');
        if (visStateEl && data.vis_state) {
            const stateLabels = {
                'idle': 'Idle',
                'leader_1': 'Leader',
                'break': 'Break',
                'leader_2': 'Leader 2',
                'start_bit': 'Start bit',
                'data_bits': 'Data bits',
                'parity': 'Parity',
                'stop_bit': 'Stop bit',
            };
            const label = stateLabels[data.vis_state] || data.vis_state;
            visStateEl.textContent = 'VIS: ' + label;
            visStateEl.className = 'sstv-signal-vis-state' +
                (data.vis_state !== 'idle' ? ' active' : '');
        }
    }

    /**
     * Render decode progress in live area
     */
    function renderDecodeProgress(data) {
        const liveContent = document.getElementById('sstvLiveContent');
        if (!liveContent) return;

        let container = liveContent.querySelector('.sstv-decode-container');
        if (!container) {
            liveContent.innerHTML = `
                <div class="sstv-decode-container">
                    <div class="sstv-canvas-container">
                        <img id="sstvDecodeImg" width="320" height="256" alt="Decoding..." style="display:block;background:#000;">
                    </div>
                    <div class="sstv-decode-info">
                        <div class="sstv-mode-label"></div>
                        <div class="sstv-progress-bar">
                            <div class="progress" style="width: 0%"></div>
                        </div>
                        <div class="sstv-status-message"></div>
                    </div>
                </div>
            `;
            container = liveContent.querySelector('.sstv-decode-container');
        }

        container.querySelector('.sstv-mode-label').textContent = data.mode || 'Detecting mode...';
        container.querySelector('.progress').style.width = (data.progress || 0) + '%';
        container.querySelector('.sstv-status-message').textContent = data.message || 'Decoding...';

        if (data.partial_image) {
            const img = container.querySelector('#sstvDecodeImg');
            if (img) img.src = data.partial_image;
        }
    }

    /**
     * Load decoded images
     */
    async function loadImages() {
        try {
            const response = await fetch('/sstv/images');
            const data = await response.json();

            if (data.status === 'ok') {
                images = data.images || [];
                updateImageCount(images.length);
                renderGallery();
            }
        } catch (err) {
            console.error('Failed to load SSTV images:', err);
        }
    }

    /**
     * Update image count display
     */
    function updateImageCount(count) {
        const countEl = document.getElementById('sstvImageCount');
        const stripCount = document.getElementById('sstvStripImageCount');

        if (countEl) countEl.textContent = count;
        if (stripCount) stripCount.textContent = count;
    }

    /**
     * Render image gallery
     */
    function renderGallery() {
        const gallery = document.getElementById('sstvGallery');
        if (!gallery) return;

        if (images.length === 0) {
            gallery.innerHTML = `
                <div class="sstv-gallery-empty">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="3" y="3" width="18" height="18" rx="2"/>
                        <circle cx="8.5" cy="8.5" r="1.5"/>
                        <polyline points="21 15 16 10 5 21"/>
                    </svg>
                    <p>No images decoded yet</p>
                </div>
            `;
            return;
        }

        gallery.innerHTML = images.map(img => `
            <div class="sstv-image-card">
                <div class="sstv-image-card-inner" onclick="SSTV.showImage('${escapeHtml(img.url)}', '${escapeHtml(img.filename)}')">
                    <img src="${escapeHtml(img.url)}" alt="SSTV Image" class="sstv-image-preview" loading="lazy">
                </div>
                <div class="sstv-image-info">
                    <div class="sstv-image-mode">${escapeHtml(img.mode || 'Unknown')}</div>
                    <div class="sstv-image-timestamp">${formatTimestamp(img.timestamp)}</div>
                </div>
                <div class="sstv-image-actions">
                    <button onclick="event.stopPropagation(); SSTV.downloadImage('${escapeHtml(img.url)}', '${escapeHtml(img.filename)}')" title="Download">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    </button>
                    <button onclick="event.stopPropagation(); SSTV.deleteImage('${escapeHtml(img.filename)}')" title="Delete">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                    </button>
                </div>
            </div>
        `).join('');
    }

    /**
     * Load ISS pass schedule
     */
    async function loadIssSchedule() {
        // Try to get user's location from settings
        const storedLat = localStorage.getItem('observerLat');
        const storedLon = localStorage.getItem('observerLon');

        // Check if location is actually set
        const hasLocation = storedLat !== null && storedLon !== null;
        const lat = storedLat || 51.5074;
        const lon = storedLon || -0.1278;

        try {
            const response = await fetch(`/sstv/iss-schedule?latitude=${lat}&longitude=${lon}&hours=48`);
            const data = await response.json();

            if (data.status === 'ok' && data.passes && data.passes.length > 0) {
                const pass = data.passes[0];
                // Parse the pass data to get timestamps
                nextPassData = parsePassData(pass);
                updateCountdownDetails(pass);
                updateCountdown();
            } else {
                nextPassData = null;
                updateCountdownDetails(null);
                updateCountdown();
            }
        } catch (err) {
            console.error('Failed to load ISS schedule:', err);
            nextPassData = null;
            updateCountdownDetails(null);
            updateCountdown();
        }
    }

    /**
     * Parse pass data to extract timestamps
     */
    function parsePassData(pass) {
        if (!pass) return null;

        let startTimestamp = null;
        let endTimestamp = null;
        const durationMinutes = parseInt(pass.duration) || 10;

        // Try to parse the startTime
        if (pass.startTimestamp) {
            // If timestamp is provided directly
            startTimestamp = pass.startTimestamp;
        } else if (pass.startTime) {
            // Parse time string (format: "HH:MM" or "HH:MM:SS" or with date)
            startTimestamp = parseTimeString(pass.startTime, pass.date);
        }

        if (startTimestamp) {
            endTimestamp = startTimestamp + durationMinutes * 60 * 1000;
        }

        return {
            startTimestamp,
            endTimestamp,
            durationMinutes,
            maxEl: pass.maxEl,
            azStart: pass.azStart
        };
    }

    /**
     * Parse time string to timestamp
     */
    function parseTimeString(timeStr, dateStr) {
        if (!timeStr) return null;

        // Try to parse as a full datetime string first (e.g., "2026-01-30 03:01 UTC")
        // Remove UTC suffix for parsing
        const cleanedStr = timeStr.replace(' UTC', '').replace('UTC', '');

        // Try full datetime parse
        let parsed = new Date(cleanedStr);
        if (!isNaN(parsed.getTime())) {
            return parsed.getTime();
        }

        // Try with T separator (ISO format)
        parsed = new Date(cleanedStr.replace(' ', 'T'));
        if (!isNaN(parsed.getTime())) {
            return parsed.getTime();
        }

        // Fallback: parse as time only (HH:MM or HH:MM:SS)
        const now = new Date();
        let targetDate = new Date();

        // If a date string is provided
        if (dateStr) {
            const parsedDate = new Date(dateStr);
            if (!isNaN(parsedDate)) {
                targetDate = parsedDate;
            }
        }

        // Parse time (HH:MM or HH:MM:SS format)
        const timeParts = cleanedStr.split(':');
        if (timeParts.length >= 2) {
            const hours = parseInt(timeParts[0]);
            const minutes = parseInt(timeParts[1]);
            const seconds = timeParts.length > 2 ? parseInt(timeParts[2]) : 0;

            if (!isNaN(hours) && !isNaN(minutes)) {
                targetDate.setHours(hours, minutes, seconds, 0);

                // If the time is in the past, assume it's tomorrow
                if (targetDate.getTime() < now.getTime() && !dateStr) {
                    targetDate.setDate(targetDate.getDate() + 1);
                }

                return targetDate.getTime();
            }
        }

        return null;
    }

    /**
     * Show full-size image in modal
     */
    let currentModalUrl = null;
    let currentModalFilename = null;

    function showImage(url, filename) {
        currentModalUrl = url;
        currentModalFilename = filename || null;

        let modal = document.getElementById('sstvImageModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'sstvImageModal';
            modal.className = 'sstv-image-modal';
            modal.innerHTML = `
                <div class="sstv-modal-toolbar">
                    <button class="sstv-modal-btn" id="sstvModalDownload" title="Download">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        Download
                    </button>
                    <button class="sstv-modal-btn delete" id="sstvModalDelete" title="Delete">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                        Delete
                    </button>
                </div>
                <button class="sstv-modal-close" onclick="SSTV.closeImage()">&times;</button>
                <img src="" alt="SSTV Image">
            `;
            modal.addEventListener('click', (e) => {
                if (e.target === modal) closeImage();
            });
            modal.querySelector('#sstvModalDownload').addEventListener('click', () => {
                if (currentModalUrl && currentModalFilename) {
                    downloadImage(currentModalUrl, currentModalFilename);
                }
            });
            modal.querySelector('#sstvModalDelete').addEventListener('click', () => {
                if (currentModalFilename) {
                    deleteImage(currentModalFilename);
                }
            });
            document.body.appendChild(modal);
        }

        modal.querySelector('img').src = url;
        modal.classList.add('show');
    }

    /**
     * Close image modal
     */
    function closeImage() {
        const modal = document.getElementById('sstvImageModal');
        if (modal) modal.classList.remove('show');
    }

    /**
     * Format timestamp for display
     */
    function formatTimestamp(isoString) {
        if (!isoString) return '--';
        try {
            const date = new Date(isoString);
            return date.toLocaleString();
        } catch {
            return isoString;
        }
    }

    /**
     * Escape HTML for safe display
     */
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Delete a single image
     */
    async function deleteImage(filename) {
        const confirmed = await AppFeedback.confirmAction({
            title: 'Delete Image',
            message: 'Delete this image? This cannot be undone.',
            confirmLabel: 'Delete',
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;
        try {
            const response = await fetch(`/sstv/images/${encodeURIComponent(filename)}`, { method: 'DELETE' });
            const data = await response.json();
            if (data.status === 'ok') {
                images = images.filter(img => img.filename !== filename);
                updateImageCount(images.length);
                renderGallery();
                closeImage();
                showNotification('SSTV', 'Image deleted');
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
        const confirmed = await AppFeedback.confirmAction({
            title: 'Delete All Images',
            message: 'Delete all decoded images? This cannot be undone.',
            confirmLabel: 'Delete All',
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;
        try {
            const response = await fetch('/sstv/images', { method: 'DELETE' });
            const data = await response.json();
            if (data.status === 'ok') {
                images = [];
                updateImageCount(0);
                renderGallery();
                showNotification('SSTV', `${data.deleted} image${data.deleted !== 1 ? 's' : ''} deleted`);
            }
        } catch (err) {
            console.error('Failed to delete images:', err);
            reportActionableError('Delete All Images', err);
        }
    }

    /**
     * Download an image
     */
    function downloadImage(url, filename) {
        const a = document.createElement('a');
        a.href = url + '/download';
        a.download = filename;
        a.click();
    }

    /**
     * Show status message
     */
    function showStatusMessage(message, type) {
        if (typeof showNotification === 'function') {
            showNotification('SSTV', message);
        } else {
            console.log(`[SSTV ${type}] ${message}`);
        }
    }

    /**
     * Invalidate ISS map size after pane/layout changes.
     */
    function invalidateMap() {
        if (!issMap) return false;
        if (!isMapContainerVisible()) {
            pendingMapInvalidate = true;
            return false;
        }
        issMap.invalidateSize({ pan: false, animate: false });
        pendingMapInvalidate = false;
        return true;
    }

    // Public API
    return {
        init,
        start,
        stop,
        loadImages,
        loadIssSchedule,
        showImage,
        closeImage,
        deleteImage,
        deleteAllImages,
        downloadImage,
        useGPS,
        updateTLE,
        stopIssTracking,
        stopCountdown,
        invalidateMap,
        destroy
    };

    /**
     * Destroy — close SSE stream and clear ISS tracking/countdown timers for clean mode switching.
     */
    function destroy() {
        if (isRunning) {
            stop().catch(() => {});
        } else {
            if (eventSource) {
                eventSource.close();
                eventSource = null;
            }
        }
        stopIssTracking();
        stopCountdown();
    }
})();

// Initialize when DOM is ready (will be called by selectMode)
document.addEventListener('DOMContentLoaded', function() {
    // Initialization happens via selectMode when SSTV mode is activated
});
