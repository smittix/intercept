/**
 * BT Locate — Bluetooth SAR Device Location Mode
 * GPS-tagged signal trail mapping with proximity audio alerts.
 */
const BtLocate = (function() {
    'use strict';

    let eventSource = null;
    let map = null;
    let mapMarkers = [];
    let trailPoints = [];
    let trailLine = null;
    let rssiHistory = [];
    const MAX_RSSI_POINTS = 60;
    let chartCanvas = null;
    let chartCtx = null;
    let currentEnvironment = 'OUTDOOR';
    let audioCtx = null;
    let audioEnabled = false;
    let beepTimer = null;
    let initialized = false;
    let handoffData = null;
    let pollTimer = null;
    let durationTimer = null;
    let sessionStartedAt = null;
    let lastDetectionCount = 0;
    let gpsLocked = false;
    let heatLayer = null;
    let heatPoints = [];
    let movementStartMarker = null;
    let movementHeadMarker = null;
    let strongestMarker = null;
    let heatmapEnabled = false;
    let movementEnabled = true;
    let autoFollowEnabled = true;
    let smoothingEnabled = true;
    let lastRenderedDetectionKey = null;
    let pendingHeatSync = false;
    let mapStabilizeTimer = null;
    let modeActive = false;
    let queuedDetection = null;
    let queuedDetectionOptions = null;
    let queuedDetectionTimer = null;
    let lastDetectionRenderAt = 0;
    let startRequestInFlight = false;
    let crosshairResetTimer = null;

    const MAX_HEAT_POINTS = 1200;
    const MAX_TRAIL_POINTS = 1200;
    const CONFIDENCE_WINDOW_POINTS = 8;
    const OUTLIER_HARD_JUMP_METERS = 2000;
    const OUTLIER_SOFT_JUMP_METERS = 450;
    const OUTLIER_MAX_SPEED_MPS = 50;
    const MAP_STABILIZE_INTERVAL_MS = 220;
    const MAP_STABILIZE_ATTEMPTS = 8;
    const MIN_DETECTION_RENDER_MS = 220;
    const OVERLAY_STORAGE_KEYS = {
        heatmap: 'btLocateHeatmapEnabled',
        movement: 'btLocateMovementEnabled',
        follow: 'btLocateFollowEnabled',
        smoothing: 'btLocateSmoothingEnabled',
    };

    const HEAT_LAYER_OPTIONS = {
        radius: 26,
        blur: 20,
        minOpacity: 0.25,
        maxZoom: 19,
        gradient: {
            0.15: '#2563eb',
            0.45: '#16a34a',
            0.75: '#f59e0b',
            1.0: '#ef4444',
        },
    };
    const BT_LOCATE_DEBUG = (() => {
        try {
            const params = new URLSearchParams(window.location.search || '');
            return params.get('btlocate_debug') === '1' ||
                localStorage.getItem('btLocateDebug') === 'true';
        } catch (_) {
            return false;
        }
    })();

    function debugLog() {
        if (!BT_LOCATE_DEBUG) return;
        console.log.apply(console, arguments);
    }

    function getMapContainer() {
        if (!map || typeof map.getContainer !== 'function') return null;
        return map.getContainer();
    }

    function isMapContainerVisible() {
        const container = getMapContainer();
        if (!container) return false;
        if (container.offsetWidth <= 0 || container.offsetHeight <= 0) return false;
        if (container.style && container.style.display === 'none') return false;
        if (typeof window.getComputedStyle === 'function') {
            const style = window.getComputedStyle(container);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
        }
        return true;
    }

    function statusUrl() {
        try {
            const params = new URLSearchParams(window.location.search || '');
            const debugFlag = params.get('btlocate_debug') === '1' ||
                localStorage.getItem('btLocateDebug') === 'true';
            return debugFlag ? '/bt_locate/status?debug=1' : '/bt_locate/status';
        } catch (_) {
            return '/bt_locate/status';
        }
    }

    function coerceLocation(lat, lon) {
        const nLat = Number(lat);
        const nLon = Number(lon);
        if (!isFinite(nLat) || !isFinite(nLon)) return null;
        if (nLat < -90 || nLat > 90 || nLon < -180 || nLon > 180) return null;
        return { lat: nLat, lon: nLon };
    }

    function resolveFallbackLocation() {
        try {
            if (typeof ObserverLocation !== 'undefined' && ObserverLocation.getShared) {
                const shared = ObserverLocation.getShared();
                const normalized = coerceLocation(shared?.lat, shared?.lon);
                if (normalized) return normalized;
            }
        } catch (_) {}

        try {
            const stored = localStorage.getItem('observerLocation');
            if (stored) {
                const parsed = JSON.parse(stored);
                const normalized = coerceLocation(parsed?.lat, parsed?.lon);
                if (normalized) return normalized;
            }
        } catch (_) {}

        try {
            const normalized = coerceLocation(
                localStorage.getItem('observerLat'),
                localStorage.getItem('observerLon')
            );
            if (normalized) return normalized;
        } catch (_) {}

        return coerceLocation(window.INTERCEPT_DEFAULT_LAT, window.INTERCEPT_DEFAULT_LON);
    }

    function setStartButtonBusy(busy) {
        const startBtn = document.getElementById('btLocateStartBtn');
        if (!startBtn) return;
        if (busy) {
            if (!startBtn.dataset.defaultLabel) {
                startBtn.dataset.defaultLabel = startBtn.textContent || 'Start Locate';
            }
            startBtn.disabled = true;
            startBtn.textContent = 'Starting...';
            return;
        }
        startBtn.disabled = false;
        startBtn.textContent = startBtn.dataset.defaultLabel || 'Start Locate';
    }

    function init() {
        modeActive = true;
        loadOverlayPreferences();
        syncOverlayControls();

        if (initialized) {
            // Re-invalidate map on re-entry and ensure tiles are present
            if (map) {
                setTimeout(() => {
                    safeInvalidateMap();
                    // Re-apply user's tile layer if tiles were lost
                    let hasTiles = false;
                    map.eachLayer(layer => {
                        if (layer instanceof L.TileLayer) hasTiles = true;
                    });
                    if (!hasTiles && typeof Settings !== 'undefined' && Settings.createTileLayer) {
                        Settings.createTileLayer().addTo(map);
                    }
                    flushPendingHeatSync();
                    scheduleMapStabilization(10);
                }, 150);
            }
            checkStatus();
            return;
        }

        // Init map
        const mapEl = document.getElementById('btLocateMap');
        if (mapEl && typeof L !== 'undefined' && typeof MapUtils !== 'undefined') {
            map = MapUtils.init('btLocateMap', {
                center: [0, 0],
                zoom: 2,
            });
            ensureHeatLayer();
            syncMovementLayer();
            syncHeatLayer();
            map.on('resize moveend zoomend', () => {
                flushPendingHeatSync();
            });
            requestAnimationFrame(() => {
                safeInvalidateMap();
                flushPendingHeatSync();
                scheduleMapStabilization();
            });
            if (typeof MapUtils !== 'undefined') MapUtils.addGraticuleControl(map);
        }

        // Init RSSI chart canvas
        chartCanvas = document.getElementById('btLocateRssiChart');
        if (chartCanvas) {
            chartCtx = chartCanvas.getContext('2d');
        }

        checkStatus();
        initialized = true;
    }

    function checkStatus() {
        fetch(statusUrl())
            .then(r => r.json())
            .then(data => {
                if (data.active) {
                    sessionStartedAt = data.started_at ? new Date(data.started_at).getTime() : Date.now();
                    showActiveUI();
                    updateScanStatus(data);
                    if (!eventSource) connectSSE();
                    restoreTrail();
                }
            })
            .catch(() => {});
    }

    function normalizeMacInput(value) {
        const raw = (value || '').trim().toUpperCase().replace(/-/g, ':');
        if (!raw) return '';
        const compact = raw.replace(/[^0-9A-F]/g, '');
        if (compact.length === 12) {
            return compact.match(/.{1,2}/g).join(':');
        }
        return raw;
    }

    function start() {
        if (startRequestInFlight) {
            return;
        }
        const mac = normalizeMacInput(document.getElementById('btLocateMac')?.value);
        const namePattern = document.getElementById('btLocateNamePattern')?.value.trim();
        const irk = document.getElementById('btLocateIrk')?.value.trim();

        const body = { environment: currentEnvironment };
        if (mac) body.mac_address = mac;
        if (namePattern) body.name_pattern = namePattern;
        if (irk) body.irk_hex = irk;
        if (handoffData?.device_id) body.device_id = handoffData.device_id;
        if (handoffData?.device_key) body.device_key = handoffData.device_key;
        if (handoffData?.fingerprint_id) body.fingerprint_id = handoffData.fingerprint_id;
        if (handoffData?.known_name) body.known_name = handoffData.known_name;
        if (handoffData?.known_manufacturer) body.known_manufacturer = handoffData.known_manufacturer;
        if (handoffData?.last_known_rssi) body.last_known_rssi = handoffData.last_known_rssi;

        // Include user location as fallback when GPS unavailable
        const fallbackLocation = resolveFallbackLocation();
        if (fallbackLocation) {
            body.fallback_lat = fallbackLocation.lat;
            body.fallback_lon = fallbackLocation.lon;
        }

        debugLog('[BtLocate] Starting with body:', body);

        if (!body.mac_address && !body.name_pattern && !body.irk_hex &&
            !body.device_id && !body.device_key && !body.fingerprint_id) {
            alert('Please provide at least one target identifier or use hand-off from Bluetooth mode.');
            return;
        }

        startRequestInFlight = true;
        setStartButtonBusy(true);

        fetch('/bt_locate/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
            .then(async (r) => {
                let data = null;
                try {
                    data = await r.json();
                } catch (_) {
                    data = {};
                }
                if (!r.ok || data.status !== 'started') {
                    const message = data.error || data.message || ('HTTP ' + r.status);
                    throw new Error(message);
                }
                return data;
            })
            .then(data => {
                if (data.status === 'started') {
                    sessionStartedAt = data.session?.started_at ? new Date(data.session.started_at).getTime() : Date.now();
                    showActiveUI();
                    connectSSE();
                    rssiHistory = [];
                    gpsLocked = false;
                    lastRenderedDetectionKey = null;
                    updateScanStatus(data.session);
                    // Restore any existing trail (e.g. from a stop/start cycle)
                    restoreTrail();
                    pollStatus();
                }
            })
            .catch(err => {
                console.error('[BtLocate] Start error:', err);
                alert('BT Locate failed to start: ' + (err?.message || 'Unknown error'));
                showIdleUI();
            })
            .finally(() => {
                startRequestInFlight = false;
                setStartButtonBusy(false);
            });
    }

    function stop() {
        // Update UI immediately — don't wait for the backend response.
        if (queuedDetectionTimer) {
            clearTimeout(queuedDetectionTimer);
            queuedDetectionTimer = null;
        }
        queuedDetection = null;
        queuedDetectionOptions = null;
        showIdleUI();
        disconnectSSE();
        stopAudio();
        // Notify backend asynchronously.
        fetch('/bt_locate/stop', { method: 'POST' })
            .catch(err => console.error('[BtLocate] Stop error:', err));
    }

    function showActiveUI() {
        setStartButtonBusy(false);
        resetGauge();
        const startBtn = document.getElementById('btLocateStartBtn');
        const stopBtn = document.getElementById('btLocateStopBtn');
        if (startBtn) startBtn.style.display = 'none';
        if (stopBtn) stopBtn.style.display = 'inline-block';
        show('btLocateHud');
    }

    function showIdleUI() {
        startRequestInFlight = false;
        setStartButtonBusy(false);
        if (queuedDetectionTimer) {
            clearTimeout(queuedDetectionTimer);
            queuedDetectionTimer = null;
        }
        queuedDetection = null;
        queuedDetectionOptions = null;
        const startBtn = document.getElementById('btLocateStartBtn');
        const stopBtn = document.getElementById('btLocateStopBtn');
        if (startBtn) startBtn.style.display = 'inline-block';
        if (stopBtn) stopBtn.style.display = 'none';
        hide('btLocateHud');
        hide('btLocateScanStatus');
    }

    function updateScanStatus(statusData) {
        const el = document.getElementById('btLocateScanStatus');
        const dot = document.getElementById('btLocateScanDot');
        const text = document.getElementById('btLocateScanText');
        if (!el) return;

        el.style.display = '';
        if (statusData && statusData.scanner_running) {
            if (dot) dot.style.background = '#22c55e';
            if (text) text.textContent = 'BT scanner active';
        } else {
            if (dot) dot.style.background = '#f97316';
            if (text) text.textContent = 'BT scanner not running — waiting...';
        }
    }

    function show(id) { const el = document.getElementById(id); if (el) el.style.display = ''; }
    function hide(id) { const el = document.getElementById(id); if (el) el.style.display = 'none'; }

    function connectSSE() {
        if (eventSource) eventSource.close();
        debugLog('[BtLocate] Connecting SSE stream');
        eventSource = new EventSource('/bt_locate/stream');

        eventSource.addEventListener('detection', function(e) {
            try {
                const event = JSON.parse(e.data);
                debugLog('[BtLocate] Detection event:', event);
                handleDetection(event);
            } catch (err) {
                console.error('[BtLocate] Parse error:', err);
            }
        });

        eventSource.addEventListener('session_ended', function() {
            showIdleUI();
            disconnectSSE();
        });

        eventSource.onerror = function() {
            debugLog('[BtLocate] SSE error, polling fallback active');
            if (eventSource && eventSource.readyState === EventSource.CLOSED) {
                eventSource = null;
            }
        };

        // Start polling fallback (catches data even if SSE fails)
        startPolling();
        pollStatus();
    }

    function disconnectSSE() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
        stopPolling();
    }

    function startPolling() {
        stopPolling();
        lastDetectionCount = 0;
        pollTimer = setInterval(pollStatus, 3000);
        startDurationTimer();
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
        stopDurationTimer();
    }

    function startDurationTimer() {
        stopDurationTimer();
        durationTimer = VisibleInterval.set(updateDuration, 1000);
    }

    function stopDurationTimer() {
        if (durationTimer) {
            VisibleInterval.clear(durationTimer);
            durationTimer = null;
        }
    }

    function updateDuration() {
        if (!sessionStartedAt) return;
        const elapsed = Math.round((Date.now() - sessionStartedAt) / 1000);
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        const timeEl = document.getElementById('btLocateSessionTime');
        if (timeEl) timeEl.textContent = mins + ':' + String(secs).padStart(2, '0');
    }

    function pollStatus() {
        fetch(statusUrl())
            .then(r => r.json())
            .then(data => {
                if (!data.active) {
                    showIdleUI();
                    disconnectSSE();
                    return;
                }

                updateScanStatus(data);
                updateHudInfo(data);

                // Recover live stream if browser closed SSE connection.
                if (!eventSource || eventSource.readyState === EventSource.CLOSED) {
                    connectSSE();
                }

                // Show diagnostics
                const diagEl = document.getElementById('btLocateDiag');
                if (diagEl) {
                    let diag = 'Polls: ' + (data.poll_count || 0) +
                        (data.poll_thread_alive === false ? ' DEAD' : '') +
                        ' | Scan: ' + (data.scanner_running ? 'Y' : 'N') +
                        ' | Devices: ' + (data.scanner_device_count || 0) +
                        ' | Det: ' + (data.detection_count || 0);
                    // Show debug device sample if no detections
                    if (data.detection_count === 0 && data.debug_devices && data.debug_devices.length > 0) {
                        const matched = data.debug_devices.filter(d => d.match);
                        const sample = data.debug_devices.slice(0, 3).map(d =>
                            (d.name || '?') + '|' + (d.id || '').substring(0, 12) + ':' + (d.match ? 'Y' : 'N')
                        ).join(', ');
                        diag += ' | Match:' + matched.length + '/' + data.debug_devices.length + ' [' + sample + ']';
                    }
                    diagEl.textContent = diag;
                }

                // If detection count increased, fetch new trail points
                if (data.detection_count > lastDetectionCount) {
                    lastDetectionCount = data.detection_count;
                    fetch('/bt_locate/trail')
                        .then(r => r.json())
                        .then(trail => {
                            if (trail.trail && trail.trail.length > 0) {
                                const latest = trail.trail[trail.trail.length - 1];
                                handleDetection({ data: latest }, { skipStatsIncrement: true });
                            }
                            updateStats(data.detection_count, data.gps_trail_count);
                        });
                }
            })
            .catch(() => {});
    }

    function updateHudInfo(data) {
        // Target info
        const targetEl = document.getElementById('btLocateTargetInfo');
        if (targetEl && data.target) {
            const t = data.target;
            const name = t.known_name || t.name_pattern || '';
            const addr = t.mac_address || t.device_id || '';
            const addrDisplay = formatAddr(addr);
            targetEl.textContent = name ? (name + (addrDisplay ? ' (' + addrDisplay + ')' : '')) : addrDisplay || '--';
        }

        // Environment info
        const envEl = document.getElementById('btLocateEnvInfo');
        if (envEl) {
            const envNames = { FREE_SPACE: 'Open Field', OUTDOOR: 'Outdoor', INDOOR: 'Indoor', CUSTOM: 'Custom' };
            envEl.textContent = (envNames[data.environment] || data.environment) + ' n=' + (data.path_loss_exponent || '?');
        }

        // GPS status
        const gpsEl = document.getElementById('btLocateGpsStatus');
        if (gpsEl) {
            const src = data.gps_source || 'none';
            if (src === 'live') gpsEl.textContent = 'GPS: Live';
            else if (src === 'manual') gpsEl.textContent = 'GPS: Manual';
            else gpsEl.textContent = 'GPS: None';
        }

        // Last seen
        const lastEl = document.getElementById('btLocateLastSeen');
        if (lastEl) {
            if (data.last_detection) {
                const ago = Math.round((Date.now() - new Date(data.last_detection).getTime()) / 1000);
                lastEl.textContent = 'Last: ' + (ago < 60 ? ago + 's ago' : Math.floor(ago / 60) + 'm ago');
            } else {
                lastEl.textContent = 'Last: --';
            }
        }

        // Session start time (duration handled by 1s timer)
        if (data.started_at && !sessionStartedAt) {
            sessionStartedAt = new Date(data.started_at).getTime();
        }
    }

    function flushQueuedDetection() {
        if (!queuedDetection) return;
        const event = queuedDetection;
        const options = queuedDetectionOptions || {};
        queuedDetection = null;
        queuedDetectionOptions = null;
        queuedDetectionTimer = null;
        renderDetection(event, options);
    }

    function handleDetection(event, options = {}) {
        if (!modeActive) {
            return;
        }
        const now = Date.now();
        if (options.force || (now - lastDetectionRenderAt) >= MIN_DETECTION_RENDER_MS) {
            if (queuedDetectionTimer) {
                clearTimeout(queuedDetectionTimer);
                queuedDetectionTimer = null;
            }
            queuedDetection = null;
            queuedDetectionOptions = null;
            renderDetection(event, options);
            return;
        }

        // Keep only the freshest event while throttled.
        queuedDetection = event;
        queuedDetectionOptions = options;
        if (!queuedDetectionTimer) {
            queuedDetectionTimer = setTimeout(flushQueuedDetection, MIN_DETECTION_RENDER_MS);
        }
    }

    function renderDetection(event, options = {}) {
        lastDetectionRenderAt = Date.now();
        const d = event?.data || event;
        if (!d) return;
        const detectionKey = buildDetectionKey(d);
        if (!options.allowDuplicate && detectionKey && detectionKey === lastRenderedDetectionKey) {
            return;
        }
        if (detectionKey) {
            lastRenderedDetectionKey = detectionKey;
        }

        updateDetectionHud(d);

        // RSSI sparkline
        if (typeof d.rssi === 'number' && isFinite(d.rssi)) {
            rssiHistory.push(d.rssi);
            if (rssiHistory.length > MAX_RSSI_POINTS) rssiHistory.shift();
            drawRssiChart();
        }

        // Map marker
        let mapPointAdded = false;
        if (d.lat != null && d.lon != null) {
            try {
                mapPointAdded = addMapMarker(d, { suppressFollow: options.suppressFollow === true });
            } catch (error) {
                debugLog('[BtLocate] Map update skipped:', error);
                mapPointAdded = false;
            }
        }

        // Update stats
        if (!options.skipStatsIncrement) {
            const detCountEl = document.getElementById('btLocateDetectionCount');
            const gpsCountEl = document.getElementById('btLocateGpsCount');
            if (detCountEl) {
                const cur = parseInt(detCountEl.textContent) || 0;
                detCountEl.textContent = cur + 1;
            }
            if (gpsCountEl && mapPointAdded) {
                const cur = parseInt(gpsCountEl.textContent) || 0;
                gpsCountEl.textContent = cur + 1;
            }
        }

        // Audio
        if (audioEnabled) playProximityTone(d.rssi);
    }

    function updateDetectionHud(d) {
        const rssiEl = document.getElementById('btLocateRssi');
        if (rssiEl) rssiEl.textContent = d.rssi != null ? d.rssi : '--';
        const smoothed = typeof d.rssi_ema === 'number' && isFinite(d.rssi_ema) ? d.rssi_ema : d.rssi;
        if (typeof smoothed === 'number' && isFinite(smoothed)) updateGauge(smoothed);
    }

    // ------------------------------------------------------------------
    // Signal gauge: hotter / colder. Signal strength cannot be turned into
    // a distance with one receiver, but whether it is rising or falling is
    // exactly what a search steers by, and the best reading so far says
    // when the target has been passed.
    // ------------------------------------------------------------------

    const GAUGE_MIN = -100;
    const GAUGE_MAX = -30;
    const GAUGE_SPAN = 240;      // degrees of arc, opening at the bottom
    const TREND_DB = 1.5;        // change that counts as stronger or weaker
    let gaugeHistory = [];       // [{t, v}] smoothed dBm, last 8 s
    let gaugeBest = null;

    function gaugeAngle(dbm) {
        const f = (Math.max(GAUGE_MIN, Math.min(GAUGE_MAX, dbm)) - GAUGE_MIN) / (GAUGE_MAX - GAUGE_MIN);
        return -GAUGE_SPAN / 2 + f * GAUGE_SPAN;
    }

    function gaugePoint(deg, r) {
        const a = deg * Math.PI / 180;
        return [60 + Math.sin(a) * r, 60 - Math.cos(a) * r];
    }

    function gaugeArc(fromDeg, toDeg, r) {
        const [x0, y0] = gaugePoint(fromDeg, r);
        const [x1, y1] = gaugePoint(toDeg, r);
        const large = toDeg - fromDeg > 180 ? 1 : 0;
        return `M${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${large},1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
    }

    function gaugeColour(dbm) {
        return dbm > -55 ? 'var(--accent-green)' : dbm > -70 ? 'var(--accent-amber)' : 'var(--accent-red)';
    }

    function buildGauge() {
        const el = document.getElementById('btLocateGauge');
        if (!el) return;
        const start = -GAUGE_SPAN / 2;
        el.innerHTML = `
            <svg viewBox="0 0 120 108" class="btl-gauge-svg">
                <path d="${gaugeArc(start, GAUGE_SPAN / 2, 48)}" class="btl-gauge-track"/>
                <path d="${gaugeArc(start, start + 0.01, 48)}" class="btl-gauge-fill" id="btLocateGaugeFill"/>
                <line id="btLocateGaugeBest" class="btl-gauge-best" x1="60" y1="6" x2="60" y2="18" style="display:none"/>
                <text x="60" y="58" class="btl-gauge-value" id="btLocateGaugeValue">--</text>
                <text x="60" y="72" class="btl-gauge-unit">dBm</text>
                <text x="60" y="100" class="btl-gauge-trend" id="btLocateGaugeTrend">WAITING</text>
            </svg>`;
    }

    function resetGauge() {
        gaugeHistory = [];
        gaugeBest = null;
        buildGauge();
        const bestEl = document.getElementById('btLocateBest');
        if (bestEl) bestEl.textContent = '--';
    }

    function gaugeTrend(now) {
        const mean = (points) => points.reduce((sum, p) => sum + p.v, 0) / points.length;
        const recent = gaugeHistory.filter((p) => now - p.t <= 2000);
        const before = gaugeHistory.filter((p) => now - p.t > 3000);
        if (!recent.length || !before.length) return 'steady';
        const change = mean(recent) - mean(before);
        return change > TREND_DB ? 'stronger' : change < -TREND_DB ? 'weaker' : 'steady';
    }

    function updateGauge(dbm) {
        if (!document.getElementById('btLocateGaugeFill')) buildGauge();
        const now = Date.now();
        gaugeHistory.push({ t: now, v: dbm });
        gaugeHistory = gaugeHistory.filter((p) => now - p.t <= 8000);
        if (gaugeBest === null || dbm > gaugeBest) gaugeBest = dbm;

        const start = -GAUGE_SPAN / 2;
        const colour = gaugeColour(dbm);
        const fill = document.getElementById('btLocateGaugeFill');
        if (fill) {
            fill.setAttribute('d', gaugeArc(start, Math.max(start + 0.01, gaugeAngle(dbm)), 48));
            fill.style.stroke = colour;
        }
        const value = document.getElementById('btLocateGaugeValue');
        if (value) {
            value.textContent = Math.round(dbm);
            value.style.fill = colour;
        }
        const best = document.getElementById('btLocateGaugeBest');
        if (best) {
            const [x1, y1] = gaugePoint(gaugeAngle(gaugeBest), 42);
            const [x2, y2] = gaugePoint(gaugeAngle(gaugeBest), 55);
            best.setAttribute('x1', x1.toFixed(2)); best.setAttribute('y1', y1.toFixed(2));
            best.setAttribute('x2', x2.toFixed(2)); best.setAttribute('y2', y2.toFixed(2));
            best.style.display = '';
        }
        const bestEl = document.getElementById('btLocateBest');
        if (bestEl) bestEl.textContent = Math.round(gaugeBest);

        const trend = gaugeTrend(now);
        const trendEl = document.getElementById('btLocateGaugeTrend');
        if (trendEl) {
            trendEl.textContent = { stronger: '▲ STRONGER', weaker: '▼ WEAKER', steady: 'STEADY' }[trend];
            trendEl.setAttribute('class', 'btl-gauge-trend ' + trend);
        }
    }

    function updateStats(detections, gpsPoints) {
        const detCountEl = document.getElementById('btLocateDetectionCount');
        const gpsCountEl = document.getElementById('btLocateGpsCount');
        if (detCountEl) detCountEl.textContent = detections || 0;
        if (gpsCountEl) gpsCountEl.textContent = gpsPoints || 0;
    }

    function triggerCrosshairAnimation(lat, lon) {
        if (!map) return;
        const overlay = document.getElementById('btLocateCrosshairOverlay');
        if (!overlay) return;
        const size = map.getSize();
        const point = map.latLngToContainerPoint([lat, lon]);
        const targetX = Math.max(0, Math.min(size.x, point.x));
        const targetY = Math.max(0, Math.min(size.y, point.y));
        const startX = size.x + 8;
        const startY = size.y + 8;
        const duration = 1500;
        overlay.style.setProperty('--btl-crosshair-x-start', `${startX}px`);
        overlay.style.setProperty('--btl-crosshair-y-start', `${startY}px`);
        overlay.style.setProperty('--btl-crosshair-x-end', `${targetX}px`);
        overlay.style.setProperty('--btl-crosshair-y-end', `${targetY}px`);
        overlay.style.setProperty('--btl-crosshair-duration', `${duration}ms`);
        overlay.classList.remove('active');
        void overlay.offsetWidth;
        overlay.classList.add('active');
        if (crosshairResetTimer) clearTimeout(crosshairResetTimer);
        crosshairResetTimer = setTimeout(() => {
            overlay.classList.remove('active');
            crosshairResetTimer = null;
        }, duration + 100);
    }

    function addMapMarker(point, options = {}) {
        if (!map || point.lat == null || point.lon == null) return false;
        const lat = Number(point.lat);
        const lon = Number(point.lon);
        if (!isFinite(lat) || !isFinite(lon)) return false;
        if (!shouldAcceptMapPoint(point, lat, lon)) return false;
        const suppressFollow = options.suppressFollow === true;
        const bulkLoad = options.bulkLoad === true;

        const trailPoint = normalizeTrailPoint(point, lat, lon);
        const band = (trailPoint.proximity_band || 'FAR').toLowerCase();
        const colors = { immediate: '#ef4444', near: '#f97316', far: '#eab308' };
        const sizes = { immediate: 8, near: 6, far: 5 };
        const color = colors[band] || '#eab308';
        const radius = sizes[band] || 5;

        const marker = L.circleMarker([lat, lon], {
            radius: radius,
            fillColor: color,
            color: '#fff',
            weight: 1,
            opacity: 0.9,
            fillOpacity: 0.8,
            btLocateMeta: trailPoint,
        }).addTo(map);

        marker.bindPopup(
            '<div style="font-family:monospace;font-size:11px;">' +
            '<b>' + (trailPoint.proximity_band || 'Unknown') + '</b><br>' +
            'RSSI: ' + (trailPoint.rssi != null ? trailPoint.rssi : '--') + ' dBm<br>' +
            'Distance: ~' + formatDistanceForPopup(trailPoint.estimated_distance) + ' m<br>' +
            'Time: ' + formatPointTimestamp(trailPoint.timestamp) +
            '</div>'
        );
        marker.on('click', () => triggerCrosshairAnimation(lat, lon));

        trailPoints.push(trailPoint);
        mapMarkers.push(marker);
        heatPoints.push([lat, lon, rssiToHeatWeight(trailPoint.rssi)]);

        while (trailPoints.length > MAX_TRAIL_POINTS) {
            trailPoints.shift();
            const oldMarker = mapMarkers.shift();
            if (oldMarker && map) map.removeLayer(oldMarker);
        }
        if (heatPoints.length > MAX_HEAT_POINTS) {
            heatPoints.splice(0, heatPoints.length - MAX_HEAT_POINTS);
        }
        if (bulkLoad) {
            pendingHeatSync = true;
            return true;
        }
        syncHeatLayer();

        if (!isMapRenderable()) {
            safeInvalidateMap();
        }
        const canFollowMap = isMapRenderable();
        if (autoFollowEnabled && !suppressFollow && canFollowMap) {
            if (!gpsLocked) {
                gpsLocked = true;
                map.setView([lat, lon], Math.max(map.getZoom(), 16));
            } else {
                map.panTo([lat, lon], { animate: true, duration: 0.35 });
            }
        } else {
            gpsLocked = true;
        }

        syncMovementLayer();
        syncStrongestMarker();
        updateConfidenceLayer();
        updateMovementStats();
        return true;
    }

    function normalizeTrailPoint(point, lat, lon) {
        const rssiVal = Number(point.rssi);
        const rssiEmaVal = Number(point.rssi_ema);
        const distVal = Number(point.estimated_distance);
        return {
            lat: lat,
            lon: lon,
            rssi: isFinite(rssiVal) ? rssiVal : null,
            rssi_ema: isFinite(rssiEmaVal) ? rssiEmaVal : null,
            estimated_distance: isFinite(distVal) ? distVal : null,
            proximity_band: point.proximity_band || 'FAR',
            timestamp: point.timestamp || null,
        };
    }

    function shouldAcceptMapPoint(point, lat, lon) {
        if (trailPoints.length === 0) return true;
        const prev = trailPoints[trailPoints.length - 1];
        if (!prev) return true;

        const distanceMeters = map
            ? map.distance([prev.lat, prev.lon], [lat, lon])
            : L.latLng(prev.lat, prev.lon).distanceTo(L.latLng(lat, lon));

        if (!isFinite(distanceMeters)) return true;
        if (distanceMeters > OUTLIER_HARD_JUMP_METERS) return false;

        const prevTs = getTimestampMs(prev.timestamp);
        const currTs = getTimestampMs(point.timestamp);
        if (prevTs != null && currTs != null && currTs > prevTs) {
            const elapsedSec = (currTs - prevTs) / 1000;
            if (elapsedSec > 0) {
                const speedMps = distanceMeters / elapsedSec;
                if (distanceMeters > OUTLIER_SOFT_JUMP_METERS && speedMps > OUTLIER_MAX_SPEED_MPS) {
                    return false;
                }
            }
        } else if (distanceMeters > OUTLIER_SOFT_JUMP_METERS) {
            return false;
        }

        return true;
    }

    function getTimestampMs(value) {
        if (!value) return null;
        const ts = new Date(value).getTime();
        return isNaN(ts) ? null : ts;
    }

    function restoreTrail() {
        fetch('/bt_locate/trail')
            .then(r => r.json())
            .then(trail => {
                clearMapMarkers();

                const gpsTrail = Array.isArray(trail.gps_trail) ? trail.gps_trail : [];
                const allTrail = Array.isArray(trail.trail) ? trail.trail : [];
                const recentGpsTrail = gpsTrail.slice(-MAX_TRAIL_POINTS);

                recentGpsTrail.forEach(p => addMapMarker(p, {
                    suppressFollow: true,
                    bulkLoad: true,
                }));
                syncHeatLayer();

                if (allTrail.length > 0) {
                    rssiHistory = allTrail.map(p => p.rssi).filter(v => typeof v === 'number' && isFinite(v)).slice(-MAX_RSSI_POINTS);
                    drawRssiChart();
                    const latest = allTrail[allTrail.length - 1];
                    updateDetectionHud(latest);
                    lastRenderedDetectionKey = buildDetectionKey(latest);
                } else {
                    rssiHistory = [];
                    drawRssiChart();
                }

                updateStats(allTrail.length, recentGpsTrail.length);

                if (trailPoints.length > 0 && map) {
                    const latestGps = trailPoints[trailPoints.length - 1];
                    gpsLocked = true;
                    const targetZoom = Math.max(map.getZoom(), 15);
                    if (isMapRenderable()) {
                        map.setView([latestGps.lat, latestGps.lon], targetZoom);
                    } else {
                        pendingHeatSync = true;
                    }
                }
                syncMovementLayer();
                syncStrongestMarker();
                updateConfidenceLayer();
                updateMovementStats();
                scheduleMapStabilization(12);
            })
            .catch(() => {});
    }

    function clearMapMarkers() {
        mapMarkers.forEach(m => map?.removeLayer(m));
        mapMarkers = [];
        trailPoints = [];
        heatPoints = [];
        if (trailLine) {
            map?.removeLayer(trailLine);
            trailLine = null;
        }
        if (movementStartMarker) {
            map?.removeLayer(movementStartMarker);
            movementStartMarker = null;
        }
        if (movementHeadMarker) {
            map?.removeLayer(movementHeadMarker);
            movementHeadMarker = null;
        }
        if (strongestMarker) {
            map?.removeLayer(strongestMarker);
            strongestMarker = null;
        }
        if (heatLayer) {
            try {
                if (isMapRenderable()) {
                    heatLayer.setLatLngs([]);
                } else {
                    pendingHeatSync = true;
                }
            } catch (error) {
                pendingHeatSync = true;
            }
        }
        updateStrongestInfo(null);
        updateConfidenceInfo(null);
        updateMovementStats();
    }

    function syncStrongestMarker() {
        if (!map) return;
        const strongest = getStrongestTrailPoint();
        if (!strongest) {
            if (strongestMarker) {
                map.removeLayer(strongestMarker);
                strongestMarker = null;
            }
            updateStrongestInfo(null);
            return;
        }

        const latlng = [strongest.lat, strongest.lon];
        if (!strongestMarker) {
            strongestMarker = L.circleMarker(latlng, {
                radius: 7,
                fillColor: '#f59e0b',
                color: '#ffffff',
                weight: 2,
                fillOpacity: 0.9,
            }).addTo(map).bindTooltip('Best RSSI', { direction: 'top' });
        } else {
            strongestMarker.setLatLng(latlng);
            if (!map.hasLayer(strongestMarker)) {
                strongestMarker.addTo(map);
            }
        }

        strongestMarker.bindPopup(
            '<div style="font-family:monospace;font-size:11px;">' +
            '<b>Strongest Signal</b><br>' +
            'RSSI: ' + strongest.rssi + ' dBm<br>' +
            'Time: ' + formatPointTimestamp(strongest.timestamp) +
            '</div>'
        );
        updateStrongestInfo(strongest);
    }

    function getStrongestTrailPoint() {
        let best = null;
        for (const p of trailPoints) {
            if (typeof p.rssi !== 'number' || !isFinite(p.rssi)) continue;
            if (!best || p.rssi > best.rssi) {
                best = p;
            }
        }
        return best;
    }

    function updateStrongestInfo(strongest) {
        const strongestEl = document.getElementById('btLocateBestSignal');
        if (!strongestEl) return;
        if (!strongest || typeof strongest.rssi !== 'number' || !isFinite(strongest.rssi)) {
            strongestEl.textContent = 'Best: --';
            return;
        }
        strongestEl.textContent = 'Best: ' + strongest.rssi + ' dBm';
    }

    /**
     * How much the signal is fluctuating over the last few readings: the
     * spread (standard deviation) of RSSI, in dB. It says how far to trust
     * a single reading. It replaced a "Confidence: +/- N m" radius and map
     * circle computed from distance estimates, which signal strength
     * cannot give.
     */
    function updateConfidenceLayer() {
        const sample = trailPoints.slice(-CONFIDENCE_WINDOW_POINTS);
        const rssis = sample.map(p => p.rssi).filter(v => typeof v === 'number' && isFinite(v));
        updateConfidenceInfo(rssis.length >= 3 ? standardDeviation(rssis) : null);
    }

    function updateConfidenceInfo(spreadDb) {
        const confidenceEl = document.getElementById('btLocateConfidenceInfo');
        if (!confidenceEl) return;
        confidenceEl.textContent = spreadDb == null || !isFinite(spreadDb)
            ? 'Signal spread: --'
            : 'Signal spread: \u00b1' + spreadDb.toFixed(1) + ' dB';
        confidenceEl.title = 'How much the signal varies between readings. A large spread means a single reading is unreliable.';
    }

    function buildDetectionKey(detection) {
        if (!detection) return '';
        const timestamp = detection.timestamp || '';
        const lat = detection.lat != null ? Number(detection.lat).toFixed(6) : '';
        const lon = detection.lon != null ? Number(detection.lon).toFixed(6) : '';
        const rssi = detection.rssi != null ? String(detection.rssi) : '';
        return [timestamp, lat, lon, rssi].join('|');
    }

    function rssiToHeatWeight(rssi) {
        const value = Number(rssi);
        if (!isFinite(value)) return 0.2;
        const min = -100;
        const max = -35;
        const clamped = Math.max(min, Math.min(max, value));
        return 0.1 + ((clamped - min) / (max - min)) * 0.9;
    }

    function ensureHeatLayer() {
        if (!map || !heatmapEnabled || typeof L === 'undefined' || typeof L.heatLayer !== 'function') return;
        if (!heatLayer) {
            heatLayer = L.heatLayer([], HEAT_LAYER_OPTIONS);
        }
    }

    function syncHeatLayer() {
        if (!map) return;
        if (!heatmapEnabled) {
            if (heatLayer && map.hasLayer(heatLayer)) {
                map.removeLayer(heatLayer);
            }
            pendingHeatSync = false;
            return;
        }
        ensureHeatLayer();
        if (!heatLayer) return;
        if (!modeActive || !isMapContainerVisible()) {
            if (map.hasLayer(heatLayer)) {
                map.removeLayer(heatLayer);
            }
            pendingHeatSync = true;
            return;
        }
        if (!isMapRenderable()) {
            safeInvalidateMap();
            if (!isMapRenderable()) {
                pendingHeatSync = true;
                return;
            }
        }
        if (!Array.isArray(heatPoints) || heatPoints.length === 0) {
            if (map.hasLayer(heatLayer)) {
                map.removeLayer(heatLayer);
            }
            pendingHeatSync = false;
            return;
        }
        try {
            heatLayer.setLatLngs(heatPoints);
            if (heatmapEnabled) {
                if (!map.hasLayer(heatLayer)) {
                    heatLayer.addTo(map);
                }
            } else if (map.hasLayer(heatLayer)) {
                map.removeLayer(heatLayer);
            }
            pendingHeatSync = false;
        } catch (error) {
            pendingHeatSync = true;
            if (map.hasLayer(heatLayer)) {
                map.removeLayer(heatLayer);
            }
            debugLog('[BtLocate] Heatmap redraw deferred:', error);
        }
    }

    function setActiveMode(active) {
        modeActive = !!active;
        if (!map) return;

        if (!modeActive) {
            stopMapStabilization();
            if (queuedDetectionTimer) {
                clearTimeout(queuedDetectionTimer);
                queuedDetectionTimer = null;
            }
            queuedDetection = null;
            queuedDetectionOptions = null;
            // Pause BT Locate frontend work when mode is hidden.
            disconnectSSE();
            if (heatLayer && map.hasLayer(heatLayer)) {
                map.removeLayer(heatLayer);
            }
            pendingHeatSync = true;
            return;
        }

        setTimeout(() => {
            if (!modeActive) return;
            safeInvalidateMap();
            flushPendingHeatSync();
            syncHeatLayer();
            syncMovementLayer();
            syncStrongestMarker();
            updateConfidenceLayer();
            scheduleMapStabilization(8);
            checkStatus();
        }, 80);

        // A second pass after layout settles (sidebar/visual transitions).
        setTimeout(() => {
            if (!modeActive) return;
            safeInvalidateMap();
            flushPendingHeatSync();
            syncHeatLayer();
        }, 260);
    }

    function isMapRenderable() {
        if (!map || !isMapContainerVisible()) return false;
        if (typeof map.getSize === 'function') {
            const size = map.getSize();
            if (!size || size.x <= 0 || size.y <= 0) return false;
        }
        return true;
    }

    function safeInvalidateMap() {
        if (!map || !isMapContainerVisible()) return false;
        map.invalidateSize({ pan: false, animate: false });
        return true;
    }

    function stopMapStabilization() {
        if (mapStabilizeTimer) {
            clearInterval(mapStabilizeTimer);
            mapStabilizeTimer = null;
        }
    }

    function scheduleMapStabilization(attempts = MAP_STABILIZE_ATTEMPTS) {
        if (!map) return;
        stopMapStabilization();
        let remaining = Math.max(1, Number(attempts) || MAP_STABILIZE_ATTEMPTS);

        const tick = () => {
            if (!map) {
                stopMapStabilization();
                return;
            }
            if (safeInvalidateMap()) {
                flushPendingHeatSync();
                syncMovementLayer();
                syncStrongestMarker();
                updateConfidenceLayer();
                if (isMapRenderable()) {
                    stopMapStabilization();
                    return;
                }
            }
            remaining -= 1;
            if (remaining <= 0) {
                stopMapStabilization();
            }
        };

        tick();
        if (map && !mapStabilizeTimer && !isMapRenderable()) {
            mapStabilizeTimer = setInterval(tick, MAP_STABILIZE_INTERVAL_MS);
        }
    }

    function flushPendingHeatSync() {
        if (!pendingHeatSync) return;
        syncHeatLayer();
    }

    function syncMovementLayer() {
        if (!map) return;
        const rawLatlngs = trailPoints.map(p => L.latLng(p.lat, p.lon));
        const latlngs = smoothingEnabled ? smoothLatLngs(rawLatlngs) : rawLatlngs;

        if (!movementEnabled || latlngs.length < 2) {
            if (trailLine) {
                map.removeLayer(trailLine);
                trailLine = null;
            }
        } else if (!trailLine) {
            trailLine = L.polyline(latlngs, {
                color: '#00ff88',
                weight: 3,
                opacity: 0.65,
                smoothFactor: smoothingEnabled ? 1.0 : 0.2,
            }).addTo(map);
        } else {
            trailLine.setLatLngs(latlngs);
            trailLine.options.smoothFactor = smoothingEnabled ? 1.0 : 0.2;
            if (!map.hasLayer(trailLine)) {
                trailLine.addTo(map);
            }
        }

        if (!movementEnabled || latlngs.length === 0) {
            if (movementStartMarker) {
                map.removeLayer(movementStartMarker);
                movementStartMarker = null;
            }
            if (movementHeadMarker) {
                map.removeLayer(movementHeadMarker);
                movementHeadMarker = null;
            }
            return;
        }

        const start = rawLatlngs[0];
        const latest = rawLatlngs[rawLatlngs.length - 1];

        if (!movementStartMarker) {
            movementStartMarker = L.circleMarker(start, {
                radius: 4,
                fillColor: '#38bdf8',
                color: '#ffffff',
                weight: 1,
                fillOpacity: 0.9,
            }).addTo(map).bindTooltip('Start', { direction: 'top' });
        } else {
            movementStartMarker.setLatLng(start);
            if (!map.hasLayer(movementStartMarker)) {
                movementStartMarker.addTo(map);
            }
        }

        if (!movementHeadMarker) {
            movementHeadMarker = L.circleMarker(latest, {
                radius: 6,
                fillColor: '#22c55e',
                color: '#ffffff',
                weight: 1,
                fillOpacity: 1,
            }).addTo(map).bindTooltip('Latest', { direction: 'top' });
        } else {
            movementHeadMarker.setLatLng(latest);
            if (!map.hasLayer(movementHeadMarker)) {
                movementHeadMarker.addTo(map);
            }
        }
    }

    function updateMovementStats() {
        const statsEl = document.getElementById('btLocateTrackStats');
        if (!statsEl) return;

        const points = trailPoints.map(p => L.latLng(p.lat, p.lon));
        if (points.length < 2) {
            statsEl.textContent = 'Track: 0 m | ' + points.length + ' pts';
            return;
        }

        let totalMeters = 0;
        for (let i = 1; i < points.length; i++) {
            totalMeters += points[i - 1].distanceTo(points[i]);
        }

        let speedSuffix = '';
        const firstMeta = trailPoints[0] || null;
        const lastMeta = trailPoints[points.length - 1] || null;
        if (firstMeta?.timestamp && lastMeta?.timestamp) {
            const elapsedSec = (new Date(lastMeta.timestamp).getTime() - new Date(firstMeta.timestamp).getTime()) / 1000;
            if (elapsedSec > 5 && isFinite(elapsedSec)) {
                const avgKmh = (totalMeters / elapsedSec) * 3.6;
                if (isFinite(avgKmh)) {
                    speedSuffix = ' | avg ' + avgKmh.toFixed(avgKmh < 10 ? 1 : 0) + ' km/h';
                }
            }
        }

        statsEl.textContent = 'Track: ' + humanDistance(totalMeters) + ' | ' + points.length + ' pts' + speedSuffix;
    }

    function smoothLatLngs(latlngs) {
        if (!Array.isArray(latlngs) || latlngs.length < 3) return latlngs;
        const smoothed = [];
        for (let i = 0; i < latlngs.length; i++) {
            const start = Math.max(0, i - 1);
            const end = Math.min(latlngs.length - 1, i + 1);
            let latSum = 0;
            let lngSum = 0;
            let count = 0;
            for (let j = start; j <= end; j++) {
                latSum += latlngs[j].lat;
                lngSum += latlngs[j].lng;
                count += 1;
            }
            smoothed.push(L.latLng(latSum / count, lngSum / count));
        }
        return smoothed;
    }

    function humanDistance(meters) {
        if (!isFinite(meters) || meters <= 0) return '0 m';
        if (meters >= 1000) {
            return (meters / 1000).toFixed(meters >= 10000 ? 1 : 2) + ' km';
        }
        return Math.round(meters) + ' m';
    }

    function formatDistanceForPopup(value) {
        const dist = Number(value);
        if (!isFinite(dist)) return '--';
        return dist.toFixed(1);
    }

    function formatPointTimestamp(value) {
        if (!value) return '--';
        const ts = new Date(value);
        if (isNaN(ts.getTime())) return '--';
        return ts.toLocaleTimeString();
    }

    function average(values) {
        if (!Array.isArray(values) || values.length === 0) return 0;
        return values.reduce((sum, val) => sum + val, 0) / values.length;
    }

    function standardDeviation(values) {
        if (!Array.isArray(values) || values.length < 2) return 0;
        const mean = average(values);
        const variance = values.reduce((sum, val) => {
            const delta = val - mean;
            return sum + (delta * delta);
        }, 0) / values.length;
        return Math.sqrt(variance);
    }

    function loadOverlayPreferences() {
        const heatmapPref = localStorage.getItem(OVERLAY_STORAGE_KEYS.heatmap);
        const movementPref = localStorage.getItem(OVERLAY_STORAGE_KEYS.movement);
        const followPref = localStorage.getItem(OVERLAY_STORAGE_KEYS.follow);
        const smoothingPref = localStorage.getItem(OVERLAY_STORAGE_KEYS.smoothing);
        if (heatmapPref !== null) heatmapEnabled = heatmapPref === 'true';
        if (movementPref !== null) movementEnabled = movementPref === 'true';
        if (followPref !== null) autoFollowEnabled = followPref === 'true';
        if (smoothingPref !== null) smoothingEnabled = smoothingPref === 'true';
    }

    function syncOverlayControls() {
        const heatmapCb = document.getElementById('btLocateHeatmapEnable');
        const movementCb = document.getElementById('btLocateMovementEnable');
        const followCb = document.getElementById('btLocateFollowEnable');
        const smoothCb = document.getElementById('btLocateSmoothEnable');
        const legend = document.getElementById('btLocateHeatLegend');
        const heatAvailable = typeof L !== 'undefined' && typeof L.heatLayer === 'function';

        if (heatmapCb) {
            heatmapCb.checked = heatAvailable ? heatmapEnabled : false;
            heatmapCb.disabled = !heatAvailable;
        }
        if (movementCb) movementCb.checked = movementEnabled;
        if (followCb) followCb.checked = autoFollowEnabled;
        if (smoothCb) smoothCb.checked = smoothingEnabled;
        if (legend) legend.style.display = heatmapEnabled && heatAvailable ? '' : 'none';
    }

    function toggleHeatmap() {
        const cb = document.getElementById('btLocateHeatmapEnable');
        heatmapEnabled = cb ? cb.checked : !heatmapEnabled;
        localStorage.setItem(OVERLAY_STORAGE_KEYS.heatmap, String(heatmapEnabled));
        syncOverlayControls();
        syncHeatLayer();
    }

    function toggleMovement() {
        const cb = document.getElementById('btLocateMovementEnable');
        movementEnabled = cb ? cb.checked : !movementEnabled;
        localStorage.setItem(OVERLAY_STORAGE_KEYS.movement, String(movementEnabled));
        syncOverlayControls();
        syncMovementLayer();
        updateMovementStats();
    }

    function toggleFollow() {
        const cb = document.getElementById('btLocateFollowEnable');
        autoFollowEnabled = cb ? cb.checked : !autoFollowEnabled;
        localStorage.setItem(OVERLAY_STORAGE_KEYS.follow, String(autoFollowEnabled));
        syncOverlayControls();
    }

    function toggleSmoothing() {
        const cb = document.getElementById('btLocateSmoothEnable');
        smoothingEnabled = cb ? cb.checked : !smoothingEnabled;
        localStorage.setItem(OVERLAY_STORAGE_KEYS.smoothing, String(smoothingEnabled));
        syncOverlayControls();
        syncMovementLayer();
    }

    function exportTrail(format) {
        const formatSel = document.getElementById('btLocateExportFormat');
        const exportFormat = String(format || formatSel?.value || 'csv').toLowerCase();
        fetch('/bt_locate/trail')
            .then(r => r.json())
            .then(data => {
                const allTrail = Array.isArray(data.trail) ? data.trail : [];
                if (allTrail.length === 0) {
                    notifyExport('No data', 'No trail data to export yet.');
                    return;
                }

                let payload = '';
                let mime = 'text/plain;charset=utf-8';
                let ext = exportFormat;

                if (exportFormat === 'csv') {
                    payload = buildTrailCsv(allTrail);
                    mime = 'text/csv;charset=utf-8';
                    ext = 'csv';
                } else if (exportFormat === 'gpx') {
                    payload = buildTrailGpx(allTrail);
                    mime = 'application/gpx+xml;charset=utf-8';
                    ext = 'gpx';
                } else if (exportFormat === 'kml') {
                    payload = buildTrailKml(allTrail);
                    mime = 'application/vnd.google-earth.kml+xml;charset=utf-8';
                    ext = 'kml';
                } else {
                    notifyExport('Export failed', 'Unsupported export format: ' + exportFormat);
                    return;
                }

                const downloaded = downloadTrailFile('bt-locate-' + buildExportStamp() + '.' + ext, payload, mime);
                if (downloaded) {
                    notifyExport('Export ready', 'Downloaded BT Locate trail as ' + ext.toUpperCase());
                }
            })
            .catch(err => {
                console.error('[BtLocate] Export failed:', err);
                notifyExport('Export failed', 'Could not export trail data.');
            });
    }

    function buildTrailCsv(trail) {
        const header = [
            'timestamp',
            'lat',
            'lon',
            'rssi',
            'rssi_ema',
            'estimated_distance',
            'proximity_band',
        ];
        const rows = trail.map(p => [
            csvEscape(p.timestamp || ''),
            csvEscape(p.lat),
            csvEscape(p.lon),
            csvEscape(p.rssi),
            csvEscape(p.rssi_ema),
            csvEscape(p.estimated_distance),
            csvEscape(p.proximity_band || ''),
        ].join(','));
        return [header.join(','), ...rows].join('\n');
    }

    function buildTrailGpx(trail) {
        const pts = trail.filter(p => p.lat != null && p.lon != null);
        if (pts.length === 0) return '';
        const trkPts = pts.map(p => {
            const rssi = p.rssi != null ? '<extensions><rssi>' + escapeXml(String(p.rssi)) + '</rssi></extensions>' : '';
            const isoTime = toIsoStringSafe(p.timestamp);
            const time = isoTime ? '<time>' + escapeXml(isoTime) + '</time>' : '';
            return (
                '<trkpt lat="' + escapeXml(String(Number(p.lat).toFixed(6))) + '" lon="' + escapeXml(String(Number(p.lon).toFixed(6))) + '">' +
                time +
                rssi +
                '</trkpt>'
            );
        }).join('');

        return (
            '<?xml version="1.0" encoding="UTF-8"?>' +
            '<gpx version="1.1" creator="iNTERCEPT BT Locate" xmlns="http://www.topografix.com/GPX/1/1">' +
            '<trk><name>BT Locate Trail</name><trkseg>' +
            trkPts +
            '</trkseg></trk>' +
            '</gpx>'
        );
    }

    function buildTrailKml(trail) {
        const pts = trail.filter(p => p.lat != null && p.lon != null);
        if (pts.length === 0) return '';
        const lineCoords = pts.map(p => Number(p.lon).toFixed(6) + ',' + Number(p.lat).toFixed(6) + ',0').join(' ');
        const pointPlacemarks = pts.map((p, idx) => {
            const label = 'Point ' + (idx + 1) + ' | RSSI ' + (p.rssi != null ? p.rssi : '--') + ' dBm';
            const desc = 'Time: ' + (toIsoStringSafe(p.timestamp) || '--');
            return (
                '<Placemark>' +
                '<name>' + escapeXml(label) + '</name>' +
                '<description>' + escapeXml(desc) + '</description>' +
                '<Point><coordinates>' + Number(p.lon).toFixed(6) + ',' + Number(p.lat).toFixed(6) + ',0</coordinates></Point>' +
                '</Placemark>'
            );
        }).join('');

        return (
            '<?xml version="1.0" encoding="UTF-8"?>' +
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>' +
            '<name>BT Locate Trail</name>' +
            '<Placemark><name>Trail</name><LineString><tessellate>1</tessellate><coordinates>' + lineCoords + '</coordinates></LineString></Placemark>' +
            pointPlacemarks +
            '</Document></kml>'
        );
    }

    function csvEscape(value) {
        if (value == null) return '';
        const text = String(value);
        if (/[",\n]/.test(text)) {
            return '"' + text.replace(/"/g, '""') + '"';
        }
        return text;
    }

    function escapeXml(value) {
        if (value == null) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&apos;');
    }

    function toIsoStringSafe(value) {
        if (!value) return '';
        const ts = new Date(value);
        if (isNaN(ts.getTime())) return '';
        return ts.toISOString();
    }

    function buildExportStamp() {
        const now = new Date();
        const y = now.getFullYear();
        const m = String(now.getMonth() + 1).padStart(2, '0');
        const d = String(now.getDate()).padStart(2, '0');
        const hh = String(now.getHours()).padStart(2, '0');
        const mm = String(now.getMinutes()).padStart(2, '0');
        const ss = String(now.getSeconds()).padStart(2, '0');
        return '' + y + m + d + '-' + hh + mm + ss;
    }

    function downloadTrailFile(filename, content, mimeType) {
        if (!content) {
            notifyExport('No data', 'No GPS points available for this export format.');
            return false;
        }
        const blob = new Blob([content], { type: mimeType || 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        return true;
    }

    function notifyExport(title, message) {
        if (typeof showNotification === 'function') {
            showNotification(title, message);
        } else {
            debugLog('[BtLocate] ' + title + ': ' + message);
        }
    }

    function drawRssiChart() {
        if (!chartCtx || !chartCanvas) return;

        const w = chartCanvas.width = chartCanvas.parentElement.clientWidth - 16;
        const h = chartCanvas.height = chartCanvas.parentElement.clientHeight - 24;
        chartCtx.clearRect(0, 0, w, h);

        if (rssiHistory.length < 2) return;

        // RSSI range: -100 to -20
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

    // Audio proximity tone (Web Audio API)
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
        // Stronger signal = higher pitch and shorter beep
        const strength = Math.max(0, Math.min(1, (rssi + 100) / 70));
        const freq = 400 + strength * 800;  // 400-1200 Hz
        const duration = 0.06 + (1 - strength) * 0.12;
        playTone(freq, duration);
    }

    function toggleAudio() {
        const cb = document.getElementById('btLocateAudioEnable');
        audioEnabled = cb?.checked || false;
        if (audioEnabled) {
            // Create AudioContext on user gesture (required by browser policy)
            if (!audioCtx) {
                try {
                    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                } catch (e) {
                    console.error('[BtLocate] AudioContext creation failed:', e);
                    return;
                }
            }
            // Resume must happen within a user gesture handler
            const ctx = audioCtx;
            ctx.resume().then(() => {
                debugLog('[BtLocate] AudioContext state:', ctx.state);
                // Confirmation beep so user knows audio is working
                playTone(600, 0.08);
            });
        } else {
            stopAudio();
        }
    }

    function stopAudio() {
        audioEnabled = false;
        const cb = document.getElementById('btLocateAudioEnable');
        if (cb) cb.checked = false;
    }

    function setEnvironment(env) {
        currentEnvironment = env;
        document.querySelectorAll('.btl-env-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.env === env);
        });
        // Push to running session if active
        fetch(statusUrl()).then(r => r.json()).then(data => {
            if (data.active) {
                fetch('/bt_locate/environment', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ environment: env }),
                }).then(r => r.json()).then(res => {
                    debugLog('[BtLocate] Environment updated:', res);
                });
            }
        }).catch(() => {});
    }

    function isUuid(addr) {
        return addr && /^[0-9A-F]{8}-[0-9A-F]{4}-/i.test(addr);
    }

    function formatAddr(addr) {
        if (!addr) return '';
        if (isUuid(addr)) return addr.substring(0, 8) + '-...' + addr.slice(-4);
        return addr;
    }

    function handoff(deviceInfo) {
        debugLog('[BtLocate] Handoff received:', deviceInfo);
        handoffData = deviceInfo;

        // Populate fields
        if (deviceInfo.mac_address) {
            const macInput = document.getElementById('btLocateMac');
            if (macInput) macInput.value = deviceInfo.mac_address;
        }

        // Show handoff card
        const card = document.getElementById('btLocateHandoffCard');
        const nameEl = document.getElementById('btLocateHandoffName');
        const metaEl = document.getElementById('btLocateHandoffMeta');
        if (card) card.style.display = '';
        if (nameEl) nameEl.textContent = deviceInfo.known_name || formatAddr(deviceInfo.mac_address) || 'Unknown';
        if (metaEl) {
            const parts = [];
            if (deviceInfo.mac_address) parts.push(formatAddr(deviceInfo.mac_address));
            if (deviceInfo.known_manufacturer) parts.push(deviceInfo.known_manufacturer);
            if (deviceInfo.last_known_rssi != null) parts.push(deviceInfo.last_known_rssi + ' dBm');
            metaEl.textContent = parts.join(' \u00b7 ');
        }

        // Auto-fill IRK if available from scanner
        if (deviceInfo.irk_hex) {
            const irkInput = document.getElementById('btLocateIrk');
            if (irkInput) irkInput.value = deviceInfo.irk_hex;
        }
    }

    function clearHandoff() {
        handoffData = null;
        const card = document.getElementById('btLocateHandoffCard');
        if (card) card.style.display = 'none';
    }

    function fetchPairedIrks() {
        const picker = document.getElementById('btLocateIrkPicker');
        const status = document.getElementById('btLocateIrkPickerStatus');
        const list = document.getElementById('btLocateIrkPickerList');
        const btn = document.getElementById('btLocateDetectIrkBtn');
        if (!picker || !status || !list) return;

        // Toggle off if already visible
        if (picker.style.display !== 'none') {
            picker.style.display = 'none';
            return;
        }

        picker.style.display = '';
        list.innerHTML = '';
        status.textContent = 'Scanning paired devices...';
        status.style.display = '';
        if (btn) btn.disabled = true;

        fetch('/bt_locate/paired_irks')
            .then(r => r.json())
            .then(data => {
                if (btn) btn.disabled = false;
                const devices = data.devices || [];

                if (devices.length === 0) {
                    status.textContent = 'No paired devices with IRKs found';
                    return;
                }

                status.style.display = 'none';
                list.innerHTML = '';

                devices.forEach(dev => {
                    const item = document.createElement('div');
                    item.className = 'btl-irk-picker-item';
                    item.innerHTML =
                        '<div class="btl-irk-picker-name">' + (dev.name || 'Unknown Device') + '</div>' +
                        '<div class="btl-irk-picker-meta">' + dev.address + ' \u00b7 ' + (dev.address_type || '') + '</div>';
                    item.addEventListener('click', function() {
                        selectPairedIrk(dev);
                    });
                    list.appendChild(item);
                });
            })
            .catch(err => {
                if (btn) btn.disabled = false;
                console.error('[BtLocate] Failed to fetch paired IRKs:', err);
                status.textContent = 'Failed to read paired devices';
            });
    }

    function selectPairedIrk(dev) {
        const irkInput = document.getElementById('btLocateIrk');
        const nameInput = document.getElementById('btLocateNamePattern');
        const picker = document.getElementById('btLocateIrkPicker');

        if (irkInput) irkInput.value = dev.irk_hex;
        if (nameInput && dev.name && !nameInput.value) nameInput.value = dev.name;
        if (picker) picker.style.display = 'none';
    }

    function clearTrail() {
        fetch('/bt_locate/clear_trail', { method: 'POST' })
            .then(r => r.json())
            .then(() => {
                clearMapMarkers();
                rssiHistory = [];
                gpsLocked = false;
                lastRenderedDetectionKey = null;
                drawRssiChart();
                updateStats(0, 0);
            })
            .catch(err => console.error('[BtLocate] Clear trail error:', err));
    }

    function invalidateMap() {
        if (safeInvalidateMap()) {
            flushPendingHeatSync();
            syncMovementLayer();
            syncStrongestMarker();
            updateConfidenceLayer();
        }
        scheduleMapStabilization(8);
    }

    return {
        init,
        setActiveMode,
        start,
        stop,
        handoff,
        clearHandoff,
        setEnvironment,
        toggleAudio,
        toggleHeatmap,
        toggleMovement,
        toggleFollow,
        toggleSmoothing,
        exportTrail,
        clearTrail,
        handleDetection,
        invalidateMap,
        fetchPairedIrks,
        destroy,
    };

    /**
     * Destroy — close SSE stream and clear all timers for clean mode switching.
     */
    function destroy() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
        if (durationTimer) {
            VisibleInterval.clear(durationTimer);
            durationTimer = null;
        }
        if (mapStabilizeTimer) {
            clearInterval(mapStabilizeTimer);
            mapStabilizeTimer = null;
        }
        if (queuedDetectionTimer) {
            clearTimeout(queuedDetectionTimer);
            queuedDetectionTimer = null;
        }
        if (crosshairResetTimer) {
            clearTimeout(crosshairResetTimer);
            crosshairResetTimer = null;
        }
        if (beepTimer) {
            clearInterval(beepTimer);
            beepTimer = null;
        }
    }
})();

window.BtLocate = BtLocate;
