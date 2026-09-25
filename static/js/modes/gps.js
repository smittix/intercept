/**
 * GPS Mode
 * Live GPS data display with satellite sky view, signal strength bars,
 * position/velocity/DOP readout. Connects to gpsd via backend SSE stream.
 */

const GPS = (function() {
    let connected = false;
    let lastPosition = null;
    let lastSky = null;
    let skyPollTimer = null;
    let statusPollTimer = null;
    let themeObserver = null;
    let skyRenderer = null;
    let skyRendererInitAttempted = false;
    let skyRendererInitPromise = null;

    // Constellation color map
    const CONST_COLORS = {
        get GPS() { return getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00d4ff'; },
        'GLONASS': '#00ff88',
        'Galileo': '#ff8800',
        'BeiDou':  '#ff4466',
        'SBAS':    '#ffdd00',
        'QZSS':    '#cc66ff',
    };

    const CONST_ALTITUDES = {
        'GPS': 0.28,
        'GLONASS': 0.27,
        'Galileo': 0.29,
        'BeiDou': 0.30,
        'SBAS': 0.34,
        'QZSS': 0.31,
    };

    const GPS_GLOBE_SCRIPT_URLS = [
        '/static/vendor/globe/globe.gl-2.33.1.min.js',  // bundled, so it works offline
    ];
    const GPS_GLOBE_TEXTURE_URL = '/static/images/globe/earth-dark.jpg';
    // The sky plot needs neither WebGL nor the globe library (fetched from a
    // CDN), so it is the default; the 3D globe is a choice, remembered here.
    const SKY_VIEW_KEY = 'intercept.gps.skyView';
    const GPS_SATELLITE_ICON_URL = '/static/images/globe/satellite-icon.svg';

    function init() {
        const initPromise = initSkyRenderer();
        if (initPromise && typeof initPromise.then === 'function') {
            initPromise.then(() => {
                if (lastSky) drawSkyView(lastSky.satellites || []);
                else drawEmptySkyView();
            }).catch(() => {});
        }
        drawEmptySkyView();
        if (!connected) connect();

        // Redraw sky view when theme changes
        if (!themeObserver) {
            themeObserver = new MutationObserver(() => {
                if (skyRenderer && typeof skyRenderer.requestRender === 'function') {
                    skyRenderer.requestRender();
                }
                if (lastSky) {
                    drawSkyView(lastSky.satellites || []);
                } else {
                    drawEmptySkyView();
                }
            });
            themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
        }

        if (lastPosition) updatePositionUI(lastPosition);
        if (lastSky) updateSkyUI(lastSky);
    }

    function preferredSkyView() {
        try {
            return localStorage.getItem(SKY_VIEW_KEY) === 'globe' ? 'globe' : 'polar';
        } catch (err) {
            return 'polar';
        }
    }

    function setSkyView(view) {
        try { localStorage.setItem(SKY_VIEW_KEY, view === 'globe' ? 'globe' : 'polar'); } catch (err) { /* kept for this visit only */ }
        if (skyRenderer && typeof skyRenderer.destroy === 'function') skyRenderer.destroy();
        skyRenderer = null;
        skyRendererInitAttempted = false;
        skyRendererInitPromise = null;
        const pending = initSkyRenderer();
        Promise.resolve(pending).then(() => {
            if (lastSky) drawSkyView(lastSky.satellites || []);
            else drawEmptySkyView();
        }).catch(() => {});
    }

    function markSkyView(view) {
        const wrap = document.getElementById('gpsSkyViewWrap');
        if (wrap) wrap.classList.toggle('gps-sky-polar', view === 'polar');
        document.querySelectorAll('.gps-sky-toggle button').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.view === view);
        });
        const hint = document.getElementById('gpsSkyHint');
        if (hint) {
            hint.textContent = view === 'polar'
                ? 'North up | Centre is overhead, edge the horizon | Rings at 30\u00b0 and 60\u00b0 elevation'
                : 'Drag to orbit globe | Scroll to zoom | Hover satellites for details';
        }
    }

    /**
     * Sky plot: the satellites as seen from here. Centre is overhead, the
     * edge the horizon, north up. Filled and glowing when used in the fix,
     * hollow when not; sized by signal; a short trail of recent positions.
     */
    function createPolarSkyRenderer(container) {
        const ns = 'http://www.w3.org/2000/svg';
        const size = 400, c = size / 2, R = 180;
        const trails = new Map();   // prn -> [[x, y], ...]
        let latest = [];

        const place = (el, az) => {
            const r = R * (90 - Math.max(0, Math.min(90, el))) / 90;
            const a = az * Math.PI / 180;
            return [c + Math.sin(a) * r, c - Math.cos(a) * r];
        };

        container.innerHTML = `
            <svg viewBox="0 0 ${size} ${size}" class="proximity-radar-svg gps-polar-svg" role="img" aria-label="Satellites in the sky">
                ${typeof RadarFace !== 'undefined' ? RadarFace.markup({
                    id: 'gpssky', size, padding: c - R, sweepSeconds: 8,
                    rings: [{ radius: 1 / 3, label: '60\u00b0' }, { radius: 2 / 3, label: '30\u00b0' }],
                }) : ''}
                <g class="gps-polar-cardinals">
                    <text x="${c}" y="${c - R - 6}">N</text>
                    <text x="${c + R + 10}" y="${c + 4}">E</text>
                    <text x="${c}" y="${c + R + 16}">S</text>
                    <text x="${c - R - 10}" y="${c + 4}">W</text>
                </g>
                <g class="gps-polar-trails"></g>
                <g class="gps-polar-sats"></g>
            </svg>`;
        const trailsGroup = container.querySelector('.gps-polar-trails');
        const satsGroup = container.querySelector('.gps-polar-sats');

        function render() {
            trailsGroup.replaceChildren();
            satsGroup.replaceChildren();
            latest.forEach((sat) => {
                if (!Number.isFinite(sat.elevation) || !Number.isFinite(sat.azimuth) || sat.elevation < 0) return;
                const colour = CONST_COLORS[sat.constellation] || CONST_COLORS.GPS;
                const [x, y] = place(sat.elevation, sat.azimuth);

                const trail = trails.get(sat.prn) || [];
                if (trail.length > 1) {
                    const line = document.createElementNS(ns, 'polyline');
                    line.setAttribute('points', trail.map((p) => p.join(',')).join(' '));
                    line.setAttribute('stroke', colour);
                    line.setAttribute('class', 'gps-polar-trail');
                    trailsGroup.appendChild(line);
                }

                const g = document.createElementNS(ns, 'g');
                g.setAttribute('class', 'gps-polar-sat' + (sat.used ? ' used' : ''));
                const snr = Number.isFinite(sat.snr) ? sat.snr : 0;
                const r = 4 + Math.max(0, Math.min(1, snr / 50)) * 5;
                const dot = document.createElementNS(ns, 'circle');
                dot.setAttribute('cx', x.toFixed(1));
                dot.setAttribute('cy', y.toFixed(1));
                dot.setAttribute('r', r.toFixed(1));
                dot.setAttribute('stroke', colour);
                dot.setAttribute('fill', sat.used ? colour : 'none');
                if (sat.used) dot.setAttribute('filter', 'url(#gpssky-glow)');
                const label = document.createElementNS(ns, 'text');
                label.setAttribute('x', (x + r + 3).toFixed(1));
                label.setAttribute('y', (y + 3).toFixed(1));
                label.setAttribute('class', 'gps-polar-label');
                label.textContent = sat.prn;
                const title = document.createElementNS(ns, 'title');
                title.textContent = `${sat.constellation || 'GPS'} ${sat.prn}: elevation ${Math.round(sat.elevation)}\u00b0, `
                    + `azimuth ${Math.round(sat.azimuth)}\u00b0, SNR ${snr} dB-Hz, ${sat.used ? 'used in fix' : 'not used'}`;
                g.append(dot, label, title);
                satsGroup.appendChild(g);
            });
        }

        function setSatellites(satellites) {
            latest = Array.isArray(satellites) ? satellites : [];
            const seen = new Set();
            latest.forEach((sat) => {
                if (!Number.isFinite(sat.elevation) || !Number.isFinite(sat.azimuth)) return;
                seen.add(sat.prn);
                let trail = trails.get(sat.prn) || [];
                const point = place(sat.elevation, sat.azimuth).map((v) => Number(v.toFixed(1)));
                const last = trail[trail.length - 1];
                // Satellites move slowly; a jump (a dropped and regained
                // signal) starts a new trail rather than drawing a streak.
                if (last && Math.hypot(point[0] - last[0], point[1] - last[1]) > 30) trail = [];
                if (!last || last[0] !== point[0] || last[1] !== point[1]) trail.push(point);
                trails.set(sat.prn, trail.slice(-40));
            });
            trails.forEach((_, prn) => { if (!seen.has(prn)) trails.delete(prn); });
            render();
        }

        return {
            setSatellites,
            requestRender: render,
            destroy: () => { container.replaceChildren(); },
        };
    }

    function initSkyRenderer() {
        const view = preferredSkyView();
        markSkyView(view);
        if (skyRendererInitPromise) return skyRendererInitPromise;
        if (view === 'polar') {
            const polar = document.getElementById('gpsSkyPolar');
            skyRendererInitAttempted = true;
            skyRenderer = polar ? createPolarSkyRenderer(polar) : null;
            skyRendererInitPromise = Promise.resolve(skyRenderer);
            return skyRendererInitPromise;
        }
        if (skyRendererInitPromise) return skyRendererInitPromise;
        skyRendererInitAttempted = true;

        let fallbackRenderer = null;
        const fallbackCanvas = document.getElementById('gpsSkyCanvas');
        const fallbackOverlay = document.getElementById('gpsSkyOverlay');

        // Show an immediate fallback while the globe library loads.
        setSkyCanvasFallbackMode(true);
        if (fallbackCanvas) {
            try {
                fallbackRenderer = createWebGlSkyRenderer(fallbackCanvas, fallbackOverlay);
                skyRenderer = fallbackRenderer;
            } catch (err) {
                fallbackRenderer = null;
                skyRenderer = null;
                console.warn('GPS sky WebGL renderer failed, falling back to 2D', err);
            }
        }

        skyRendererInitPromise = (async function() {
            const globeContainer = document.getElementById('gpsSkyGlobe');
            if (globeContainer) {
                try {
                    const globeRenderer = await createGlobeSkyRenderer(globeContainer);
                    if (globeRenderer) {
                        if (fallbackRenderer && fallbackRenderer !== globeRenderer && typeof fallbackRenderer.destroy === 'function') {
                            fallbackRenderer.destroy();
                        }
                        setSkyCanvasFallbackMode(false);
                        skyRenderer = globeRenderer;
                        return skyRenderer;
                    }
                } catch (err) {
                    console.warn('GPS globe renderer failed, falling back to canvas renderer', err);
                }
            }

            setSkyCanvasFallbackMode(true);
            if (!fallbackRenderer && fallbackCanvas) {
                try {
                    fallbackRenderer = createWebGlSkyRenderer(fallbackCanvas, fallbackOverlay);
                } catch (err) {
                    fallbackRenderer = null;
                    console.warn('GPS sky WebGL renderer failed, falling back to 2D', err);
                }
            }

            skyRenderer = fallbackRenderer;
            return skyRenderer;
        })();

        return skyRendererInitPromise;
    }

    function setSkyCanvasFallbackMode(enabled) {
        const wrap = document.getElementById('gpsSkyViewWrap');
        if (wrap) {
            wrap.classList.toggle('gps-sky-fallback', !!enabled);
        }
    }

    function isSkyCanvasFallbackEnabled() {
        const wrap = document.getElementById('gpsSkyViewWrap');
        return !wrap || wrap.classList.contains('gps-sky-fallback');
    }

    function getObserverCoords() {
        const posLat = Number(lastPosition && lastPosition.latitude);
        const posLon = Number(lastPosition && lastPosition.longitude);
        if (Number.isFinite(posLat) && Number.isFinite(posLon)) {
            return { lat: posLat, lon: normalizeLon(posLon) };
        }

        if (typeof observerLocation === 'object' && observerLocation) {
            const obsLat = Number(observerLocation.lat);
            const obsLon = Number(observerLocation.lon);
            if (Number.isFinite(obsLat) && Number.isFinite(obsLon)) {
                return { lat: obsLat, lon: normalizeLon(obsLon) };
            }
        }

        return null;
    }

    async function ensureGpsGlobeLibrary() {
        if (typeof window.Globe === 'function') return true;

        const webglSupportFn = (typeof isWebglSupported === 'function') ? isWebglSupported : localWebglSupportCheck;
        if (!webglSupportFn()) return false;

        if (typeof ensureWebsdrGlobeLibrary === 'function') {
            try {
                const ready = await ensureWebsdrGlobeLibrary();
                if (ready && typeof window.Globe === 'function') return true;
            } catch (_) {}
        }

        for (const src of GPS_GLOBE_SCRIPT_URLS) {
            await loadGpsGlobeScript(src);
        }
        return typeof window.Globe === 'function';
    }

    function loadGpsGlobeScript(src) {
        const state = getSharedGlobeScriptState();
        if (!state.promises[src]) {
            state.promises[src] = loadSharedGlobeScript(src);
        }
        return state.promises[src].catch((error) => {
            delete state.promises[src];
            throw error;
        });
    }

    function getSharedGlobeScriptState() {
        const key = '__interceptGlobeScriptState';
        if (!window[key]) {
            window[key] = {
                promises: Object.create(null),
            };
        }
        return window[key];
    }

    function loadSharedGlobeScript(src) {
        return new Promise((resolve, reject) => {
            const selector = [
                `script[data-intercept-globe-src="${src}"]`,
                `script[data-websdr-src="${src}"]`,
                `script[data-gps-globe-src="${src}"]`,
                `script[src="${src}"]`,
            ].join(', ');
            const existing = document.querySelector(selector);

            if (existing) {
                if (existing.dataset.loaded === 'true') {
                    resolve();
                    return;
                }
                if (existing.dataset.failed === 'true') {
                    existing.remove();
                } else {
                    existing.addEventListener('load', () => resolve(), { once: true });
                    existing.addEventListener('error', () => reject(new Error(`Failed to load ${src}`)), { once: true });
                    return;
                }
            }

            const script = document.createElement('script');
            script.src = src;
            script.async = true;
            script.crossOrigin = 'anonymous';
            script.dataset.interceptGlobeSrc = src;
            script.dataset.gpsGlobeSrc = src;
            script.onload = () => {
                script.dataset.loaded = 'true';
                resolve();
            };
            script.onerror = () => {
                script.dataset.failed = 'true';
                reject(new Error(`Failed to load ${src}`));
            };
            document.head.appendChild(script);
        });
    }

    function localWebglSupportCheck() {
        try {
            const canvas = document.createElement('canvas');
            return !!(canvas.getContext('webgl') || canvas.getContext('experimental-webgl'));
        } catch (_) {
            return false;
        }
    }

    async function createGlobeSkyRenderer(container) {
        const ready = await ensureGpsGlobeLibrary();
        if (!ready || typeof window.Globe !== 'function') return null;

        let layoutAttempts = 0;
        while ((!container.clientWidth || !container.clientHeight) && layoutAttempts < 4) {
            await new Promise(resolve => requestAnimationFrame(resolve));
            layoutAttempts += 1;
        }
        if (!container.clientWidth || !container.clientHeight) return null;

        container.innerHTML = '';
        container.style.background = 'radial-gradient(circle at 32% 18%, rgba(16, 45, 70, 0.92), rgba(4, 9, 16, 0.96) 58%, rgba(2, 4, 9, 0.99) 100%)';
        container.style.cursor = 'grab';

        const globe = window.Globe()(container)
            .backgroundColor('rgba(0,0,0,0)')
            .globeImageUrl(GPS_GLOBE_TEXTURE_URL)
            .showAtmosphere(true)
            .atmosphereColor(getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#3bb9ff')
            .atmosphereAltitude(0.17)
            .pointRadius('radius')
            .pointAltitude('altitude')
            .pointColor('color')
            .pointLabel(point => point.label || '')
            .pointsTransitionDuration(0)
            .htmlAltitude('altitude')
            .htmlElementsData([])
            .htmlElement((sat) => createSatelliteIconElement(sat));

        const controls = globe.controls();
        if (controls) {
            controls.autoRotate = false;
            controls.enablePan = false;
            controls.minDistance = 130;
            controls.maxDistance = 420;
            controls.rotateSpeed = 0.8;
            controls.zoomSpeed = 0.8;
        }

        let destroyed = false;
        let lastSatellites = [];
        let hasInitialView = false;
        const resizeObserver = (typeof ResizeObserver !== 'undefined')
            ? new ResizeObserver(() => resizeGlobe())
            : null;

        if (resizeObserver) resizeObserver.observe(container);

        function resizeGlobe() {
            if (destroyed) return;
            const width = container.clientWidth;
            const height = container.clientHeight;
            if (!width || !height) return;
            globe.width(width);
            globe.height(height);
        }

        function renderGlobe() {
            if (destroyed) return;
            resizeGlobe();

            const observer = getObserverCoords();
            const points = [];
            const satelliteIcons = [];

            if (observer) {
                points.push({
                    lat: observer.lat,
                    lng: observer.lon,
                    altitude: 0.012,
                    radius: 0.34,
                    color: '#ffffff',
                    label: '<div style="padding:4px 6px; font-size:11px; background:rgba(5,13,20,0.92); border:1px solid rgba(255,255,255,0.28); border-radius:4px;">Observer</div>',
                });
            }

            lastSatellites.forEach((sat) => {
                const azimuth = Number(sat.azimuth);
                const elevation = Number(sat.elevation);
                if (!observer || !Number.isFinite(azimuth) || !Number.isFinite(elevation)) return;

                const color = CONST_COLORS[sat.constellation] || CONST_COLORS.GPS;
                const shellAltitude = getSatelliteShellAltitude(sat.constellation, elevation);
                const footprint = projectSkyTrackToEarth(observer.lat, observer.lon, azimuth, elevation);
                satelliteIcons.push({
                    lat: footprint.lat,
                    lng: footprint.lon,
                    altitude: shellAltitude,
                    color: color,
                    used: !!sat.used,
                    sizePx: sat.used ? 20 : 17,
                    title: buildSatelliteTitle(sat),
                    iconUrl: GPS_SATELLITE_ICON_URL,
                });
            });

            globe.pointsData(points);
            globe.htmlElementsData(satelliteIcons);

            if (observer && !hasInitialView) {
                globe.pointOfView({ lat: observer.lat, lng: observer.lon, altitude: 1.6 }, 950);
                hasInitialView = true;
            }
        }

        function createSatelliteIconElement(sat) {
            const marker = document.createElement('div');
            marker.className = `gps-globe-sat-icon ${sat.used ? 'used' : 'unused'}`;
            marker.style.setProperty('--sat-color', sat.color || '#9fb2c5');
            marker.style.setProperty('--sat-size', `${Math.max(12, Number(sat.sizePx) || 18)}px`);
            marker.title = sat.title || 'Satellite';

            const img = document.createElement('img');
            img.src = sat.iconUrl || GPS_SATELLITE_ICON_URL;
            img.alt = 'Satellite';
            img.decoding = 'async';
            img.draggable = false;

            marker.appendChild(img);
            return marker;
        }

        function setSatellites(satellites) {
            lastSatellites = Array.isArray(satellites) ? satellites : [];
            renderGlobe();
        }

        function requestRender() {
            renderGlobe();
        }

        function destroy() {
            destroyed = true;
            if (resizeObserver) {
                try {
                    resizeObserver.disconnect();
                } catch (_) {}
            }
            container.innerHTML = '';
        }

        setSatellites([]);

        return {
            setSatellites: setSatellites,
            requestRender: requestRender,
            destroy: destroy,
        };
    }

    function buildSatelliteTitle(sat) {
        const constellation = String(sat.constellation || 'GPS');
        const prn = String(sat.prn || '--');
        const elevation = Number.isFinite(Number(sat.elevation)) ? `${Number(sat.elevation).toFixed(1)}\u00b0` : '--';
        const azimuth = Number.isFinite(Number(sat.azimuth)) ? `${Number(sat.azimuth).toFixed(1)}\u00b0` : '--';
        const snr = Number.isFinite(Number(sat.snr)) ? `${Math.round(Number(sat.snr))} dB-Hz` : 'n/a';
        const used = sat.used ? 'USED IN FIX' : 'TRACKED';

        return `${constellation} PRN ${prn} | El ${elevation} | Az ${azimuth} | SNR ${snr} | ${used}`;
    }

    function getSatelliteShellAltitude(constellation, elevation) {
        const base = CONST_ALTITUDES[constellation] || CONST_ALTITUDES.GPS;
        const el = Math.max(0, Math.min(90, Number(elevation) || 0));
        const horizonFactor = 1 - (el / 90);
        return base + (horizonFactor * 0.04);
    }

    function projectSkyTrackToEarth(observerLat, observerLon, azimuth, elevation) {
        const el = Math.max(0, Math.min(90, Number(elevation) || 0));
        const horizonFactor = 1 - (el / 90);
        const angularDistance = 76 * Math.pow(horizonFactor, 1.08);
        return destinationPoint(observerLat, observerLon, azimuth, angularDistance);
    }

    function destinationPoint(latDeg, lonDeg, bearingDeg, distanceDeg) {
        const lat1 = degToRad(latDeg);
        const lon1 = degToRad(lonDeg);
        const bearing = degToRad(bearingDeg);
        const distance = degToRad(distanceDeg);

        const sinLat1 = Math.sin(lat1);
        const cosLat1 = Math.cos(lat1);
        const sinDist = Math.sin(distance);
        const cosDist = Math.cos(distance);

        const sinLat2 = (sinLat1 * cosDist) + (cosLat1 * sinDist * Math.cos(bearing));
        const lat2 = Math.asin(Math.max(-1, Math.min(1, sinLat2)));

        const y = Math.sin(bearing) * sinDist * cosLat1;
        const x = cosDist - (sinLat1 * Math.sin(lat2));
        const lon2 = lon1 + Math.atan2(y, x);

        return {
            lat: radToDeg(lat2),
            lon: normalizeLon(radToDeg(lon2)),
        };
    }

    function normalizeLon(lon) {
        let normalized = (lon + 540) % 360;
        normalized = normalized < 0 ? normalized + 360 : normalized;
        return normalized - 180;
    }

    function radToDeg(rad) {
        return rad * 180 / Math.PI;
    }

    function connect() {
        updateConnectionUI(false, false, 'connecting');
        fetch('/gps/auto-connect', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'connected') {
                    connected = true;
                    updateConnectionUI(true, data.has_fix);
                    if (data.position) {
                        lastPosition = data.position;
                        updatePositionUI(data.position);
                    }
                    if (data.sky) {
                        lastSky = data.sky;
                        updateSkyUI(data.sky);
                    }
                    subscribeToStream();
                    startSkyPolling();
                    startStatusPolling();
                    // Ensure the global GPS stream is running
                    const hasGlobalGpsStream = typeof gpsEventSource !== 'undefined' && !!gpsEventSource;
                    if (typeof startGpsStream === 'function' && !hasGlobalGpsStream) {
                        startGpsStream();
                    }
                } else {
                    connected = false;
                    updateConnectionUI(false, false, 'error', data.message || 'gpsd not available');
                }
            })
            .catch(() => {
                connected = false;
                updateConnectionUI(false, false, 'error', 'Connection failed — is the server running?');
            });
    }

    function disconnect() {
        unsubscribeFromStream();
        stopSkyPolling();
        stopStatusPolling();
        fetch('/gps/stop', { method: 'POST' })
            .then(() => {
                connected = false;
                updateConnectionUI(false);
            });
    }

    function onGpsStreamData(data) {
        if (!connected) return;
        if (data.type === 'position') {
            lastPosition = data;
            updatePositionUI(data);
            updateConnectionUI(true, true);
            if (lastSky && skyRenderer) {
                drawSkyView(lastSky.satellites || []);
            }
        } else if (data.type === 'sky') {
            lastSky = data;
            updateSkyUI(data);
        }
    }

    function startSkyPolling() {
        stopSkyPolling();
        // Poll satellite data every 5 seconds as a reliable fallback
        // SSE stream may miss sky updates due to queue contention with position messages
        pollSatellites();
        skyPollTimer = VisibleInterval.set(pollSatellites, 5000);
    }

    function stopSkyPolling() {
        if (skyPollTimer) {
            VisibleInterval.clear(skyPollTimer);
            skyPollTimer = null;
        }
    }

    function pollSatellites() {
        if (!connected) return;
        fetch('/gps/satellites')
            .then(r => r.json())
            .then(data => {
                if (data.status === 'ok' && data.sky) {
                    lastSky = data.sky;
                    updateSkyUI(data.sky);
                }
            })
            .catch(() => {});
    }

    function startStatusPolling() {
        stopStatusPolling();
        // Poll full status as a fallback when SSE is unavailable or blocked.
        pollStatus();
        statusPollTimer = VisibleInterval.set(pollStatus, 2000);
    }

    function stopStatusPolling() {
        if (statusPollTimer) {
            VisibleInterval.clear(statusPollTimer);
            statusPollTimer = null;
        }
    }

    function pollStatus() {
        if (!connected) return;
        fetch('/gps/status')
            .then(r => r.json())
            .then(data => {
                if (!connected || !data) return;
                if (data.running !== true) {
                    connected = false;
                    stopSkyPolling();
                    stopStatusPolling();
                    updateConnectionUI(false, false, 'error', data.message || 'GPS disconnected');
                    return;
                }

                if (data.position) {
                    lastPosition = data.position;
                    updatePositionUI(data.position);
                    updateConnectionUI(true, true);
                } else {
                    updateConnectionUI(true, false);
                }

                if (data.sky) {
                    lastSky = data.sky;
                    updateSkyUI(data.sky);
                }
            })
            .catch(() => {});
    }

    function subscribeToStream() {
        // Subscribe to the global GPS stream instead of opening a separate SSE connection
        if (typeof addGpsStreamSubscriber === 'function') {
            addGpsStreamSubscriber(onGpsStreamData);
        }
    }

    function unsubscribeFromStream() {
        if (typeof removeGpsStreamSubscriber === 'function') {
            removeGpsStreamSubscriber(onGpsStreamData);
        }
    }

    // ========================
    // UI Updates
    // ========================

    function updateConnectionUI(isConnected, hasFix, state, message) {
        const dot = document.getElementById('gpsStatusDot');
        const text = document.getElementById('gpsStatusText');
        const connectBtn = document.getElementById('gpsConnectBtn');
        const disconnectBtn = document.getElementById('gpsDisconnectBtn');
        const devicePath = document.getElementById('gpsDevicePath');

        if (dot) {
            dot.className = 'gps-status-dot';
            if (state === 'connecting') dot.classList.add('waiting');
            else if (state === 'error') dot.classList.add('error');
            else if (isConnected && hasFix) dot.classList.add('connected');
            else if (isConnected) dot.classList.add('waiting');
        }
        if (text) {
            if (state === 'connecting') text.textContent = 'Connecting...';
            else if (state === 'error') text.textContent = message || 'Connection failed';
            else if (isConnected && hasFix) text.textContent = 'Connected (Fix)';
            else if (isConnected) text.textContent = 'Connected (No Fix)';
            else text.textContent = 'Disconnected';
        }
        if (connectBtn) {
            connectBtn.style.display = isConnected ? 'none' : '';
            connectBtn.disabled = state === 'connecting';
        }
        if (disconnectBtn) disconnectBtn.style.display = isConnected ? '' : 'none';
        if (devicePath) devicePath.textContent = isConnected ? 'gpsd://localhost:2947' : '';
    }

    function updatePositionUI(pos) {
        // Sidebar fields
        setText('gpsLat', pos.latitude != null ? pos.latitude.toFixed(6) + '\u00b0' : '---');
        setText('gpsLon', pos.longitude != null ? pos.longitude.toFixed(6) + '\u00b0' : '---');
        setText('gpsAlt', pos.altitude != null ? pos.altitude.toFixed(1) + ' m' : '---');
        setText('gpsSpeed', pos.speed != null ? (pos.speed * 3.6).toFixed(1) + ' km/h' : '---');
        setText('gpsHeading', pos.heading != null ? pos.heading.toFixed(1) + '\u00b0' : '---');
        setText('gpsClimb', pos.climb != null ? pos.climb.toFixed(2) + ' m/s' : '---');

        // Fix type
        const fixEl = document.getElementById('gpsFixType');
        if (fixEl) {
            const fq = pos.fix_quality;
            if (fq === 3) fixEl.innerHTML = '<span class="gps-fix-badge fix-3d">3D FIX</span>';
            else if (fq === 2) fixEl.innerHTML = '<span class="gps-fix-badge fix-2d">2D FIX</span>';
            else fixEl.innerHTML = '<span class="gps-fix-badge no-fix">NO FIX</span>';
        }

        // Error estimates
        const eph = (pos.epx != null && pos.epy != null) ? Math.sqrt(pos.epx * pos.epx + pos.epy * pos.epy) : null;
        setText('gpsEph', eph != null ? eph.toFixed(1) + ' m' : '---');
        setText('gpsEpv', pos.epv != null ? pos.epv.toFixed(1) + ' m' : '---');
        setText('gpsEps', pos.eps != null ? pos.eps.toFixed(2) + ' m/s' : '---');

        // GPS time
        if (pos.timestamp) {
            const t = new Date(pos.timestamp);
            setText('gpsTime', t.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC'));
        }

        // Visuals: position panel
        setText('gpsVisPosLat', pos.latitude != null ? pos.latitude.toFixed(6) + '\u00b0' : '---');
        setText('gpsVisPosLon', pos.longitude != null ? pos.longitude.toFixed(6) + '\u00b0' : '---');
        setText('gpsVisPosAlt', pos.altitude != null ? pos.altitude.toFixed(1) + ' m' : '---');
        setText('gpsVisPosSpeed', pos.speed != null ? (pos.speed * 3.6).toFixed(1) + ' km/h' : '---');
        setText('gpsVisPosHeading', pos.heading != null ? pos.heading.toFixed(1) + '\u00b0' : '---');
        setText('gpsVisPosClimb', pos.climb != null ? pos.climb.toFixed(2) + ' m/s' : '---');

        // Visuals: fix badge
        const visFixEl = document.getElementById('gpsVisFixBadge');
        if (visFixEl) {
            const fq = pos.fix_quality;
            if (fq === 3) { visFixEl.textContent = '3D FIX'; visFixEl.className = 'gps-fix-badge fix-3d'; }
            else if (fq === 2) { visFixEl.textContent = '2D FIX'; visFixEl.className = 'gps-fix-badge fix-2d'; }
            else { visFixEl.textContent = 'NO FIX'; visFixEl.className = 'gps-fix-badge no-fix'; }
        }

        // Visuals: GPS time
        if (pos.timestamp) {
            const t = new Date(pos.timestamp);
            setText('gpsVisTime', t.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC'));
        }
    }

    function updateSkyUI(sky) {
        // Sidebar sat counts
        setText('gpsSatUsed', sky.usat != null ? sky.usat : '-');
        setText('gpsSatTotal', sky.nsat != null ? sky.nsat : '-');

        // DOP values
        setDop('gpsHdop', sky.hdop);
        setDop('gpsVdop', sky.vdop);
        setDop('gpsPdop', sky.pdop);
        setDop('gpsTdop', sky.tdop);
        setDop('gpsGdop', sky.gdop);

        // Visuals
        drawSkyView(sky.satellites || []);
        drawSignalBars(sky.satellites || []);
    }

    function setDop(id, val) {
        const el = document.getElementById(id);
        if (!el) return;
        if (val == null) { el.textContent = '---'; el.className = 'gps-info-value gps-mono'; return; }
        el.textContent = val.toFixed(1);
        let cls = 'gps-info-value gps-mono ';
        if (val <= 2) cls += 'gps-dop-good';
        else if (val <= 5) cls += 'gps-dop-moderate';
        else cls += 'gps-dop-poor';
        el.className = cls;
    }

    function setText(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    // ========================
    // Sky View Globe (WebGL with 2D fallback)
    // ========================

    function drawEmptySkyView() {
        if (!skyRendererInitAttempted) {
            initSkyRenderer();
        }

        if (skyRenderer) {
            skyRenderer.setSatellites([]);
            return;
        }

        if (!isSkyCanvasFallbackEnabled()) return;

        const canvas = document.getElementById('gpsSkyCanvas');
        if (!canvas) return;
        resize2DFallbackCanvas(canvas);
        drawSkyViewBase2D(canvas);
    }

    function drawSkyView(satellites) {
        if (!skyRendererInitAttempted) {
            initSkyRenderer();
        }

        const sats = Array.isArray(satellites) ? satellites : [];

        if (skyRenderer) {
            skyRenderer.setSatellites(sats);
            return;
        }

        if (!isSkyCanvasFallbackEnabled()) return;

        const canvas = document.getElementById('gpsSkyCanvas');
        if (!canvas) return;

        resize2DFallbackCanvas(canvas);
        drawSkyViewBase2D(canvas);

        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const w = canvas.width;
        const h = canvas.height;
        const cx = w / 2;
        const cy = h / 2;
        const r = Math.min(cx, cy) - 24;

        sats.forEach(sat => {
            if (sat.elevation == null || sat.azimuth == null) return;

            const elRad = (90 - sat.elevation) / 90;
            const azRad = (sat.azimuth - 90) * Math.PI / 180;
            const px = cx + r * elRad * Math.cos(azRad);
            const py = cy + r * elRad * Math.sin(azRad);

            const color = CONST_COLORS[sat.constellation] || CONST_COLORS.GPS;
            const dotSize = sat.used ? 6 : 4;

            ctx.beginPath();
            ctx.arc(px, py, dotSize, 0, Math.PI * 2);
            if (sat.used) {
                ctx.fillStyle = color;
                ctx.fill();
            } else {
                ctx.strokeStyle = color;
                ctx.lineWidth = 1.5;
                ctx.stroke();
            }

            ctx.fillStyle = color;
            ctx.font = '8px Roboto Condensed, monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            ctx.fillText(sat.prn, px, py - dotSize - 2);

            if (sat.snr != null) {
                ctx.fillStyle = 'rgba(255,255,255,0.4)';
                ctx.font = '7px Roboto Condensed, monospace';
                ctx.textBaseline = 'top';
                ctx.fillText(Math.round(sat.snr), px, py + dotSize + 1);
            }
        });
    }

    function drawSkyViewBase2D(canvas) {
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const w = canvas.width;
        const h = canvas.height;
        const cx = w / 2;
        const cy = h / 2;
        const r = Math.min(cx, cy) - 24;

        ctx.clearRect(0, 0, w, h);

        const cs = getComputedStyle(document.documentElement);
        const bgColor = cs.getPropertyValue('--bg-card').trim() || '#0d1117';
        const gridColor = cs.getPropertyValue('--border-color').trim() || '#2a3040';
        const dimColor = cs.getPropertyValue('--text-dim').trim() || '#555';
        const secondaryColor = cs.getPropertyValue('--text-secondary').trim() || '#888';

        ctx.fillStyle = bgColor;
        ctx.fillRect(0, 0, w, h);

        ctx.strokeStyle = gridColor;
        ctx.lineWidth = 0.5;
        [90, 60, 30].forEach(el => {
            const gr = r * (1 - el / 90);
            ctx.beginPath();
            ctx.arc(cx, cy, gr, 0, Math.PI * 2);
            ctx.stroke();

            ctx.fillStyle = dimColor;
            ctx.font = '9px Roboto Condensed, monospace';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(el + '\u00b0', cx + gr + 3, cy - 2);
        });

        ctx.strokeStyle = gridColor;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();

        ctx.fillStyle = secondaryColor;
        ctx.font = 'bold 11px Roboto Condensed, monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('N', cx, cy - r - 12);
        ctx.fillText('S', cx, cy + r + 12);
        ctx.fillText('E', cx + r + 12, cy);
        ctx.fillText('W', cx - r - 12, cy);

        ctx.strokeStyle = gridColor;
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.moveTo(cx, cy - r);
        ctx.lineTo(cx, cy + r);
        ctx.moveTo(cx - r, cy);
        ctx.lineTo(cx + r, cy);
        ctx.stroke();

        ctx.fillStyle = dimColor;
        ctx.beginPath();
        ctx.arc(cx, cy, 2, 0, Math.PI * 2);
        ctx.fill();
    }

    function resize2DFallbackCanvas(canvas) {
        const cssWidth = Math.max(1, Math.floor(canvas.clientWidth || 400));
        const cssHeight = Math.max(1, Math.floor(canvas.clientHeight || 400));
        if (canvas.width !== cssWidth || canvas.height !== cssHeight) {
            canvas.width = cssWidth;
            canvas.height = cssHeight;
        }
    }

    function createWebGlSkyRenderer(canvas, overlay) {
        const gl = canvas.getContext('webgl', { antialias: true, alpha: false, depth: true });
        if (!gl) return null;

        const lineProgram = createProgram(
            gl,
            [
                'attribute vec3 aPosition;',
                'uniform mat4 uMVP;',
                'void main(void) {',
                '  gl_Position = uMVP * vec4(aPosition, 1.0);',
                '}',
            ].join('\n'),
            [
                'precision mediump float;',
                'uniform vec4 uColor;',
                'void main(void) {',
                '  gl_FragColor = uColor;',
                '}',
            ].join('\n'),
        );

        const pointProgram = createProgram(
            gl,
            [
                'attribute vec3 aPosition;',
                'attribute vec4 aColor;',
                'attribute float aSize;',
                'attribute float aUsed;',
                'uniform mat4 uMVP;',
                'uniform float uDevicePixelRatio;',
                'uniform vec3 uCameraDir;',
                'varying vec4 vColor;',
                'varying float vUsed;',
                'varying float vFacing;',
                'void main(void) {',
                '  vec3 normPos = normalize(aPosition);',
                '  vFacing = dot(normPos, normalize(uCameraDir));',
                '  gl_Position = uMVP * vec4(aPosition, 1.0);',
                '  gl_PointSize = aSize * uDevicePixelRatio;',
                '  vColor = aColor;',
                '  vUsed = aUsed;',
                '}',
            ].join('\n'),
            [
                'precision mediump float;',
                'varying vec4 vColor;',
                'varying float vUsed;',
                'varying float vFacing;',
                'void main(void) {',
                '  if (vFacing <= 0.0) discard;',
                '  vec2 c = gl_PointCoord * 2.0 - 1.0;',
                '  float d = dot(c, c);',
                '  if (d > 1.0) discard;',
                '  if (vUsed < 0.5 && d < 0.45) discard;',
                '  float edge = smoothstep(1.0, 0.75, d);',
                '  gl_FragColor = vec4(vColor.rgb, vColor.a * edge);',
                '}',
            ].join('\n'),
        );

        if (!lineProgram || !pointProgram) return null;

        const lineLoc = {
            position: gl.getAttribLocation(lineProgram, 'aPosition'),
            mvp: gl.getUniformLocation(lineProgram, 'uMVP'),
            color: gl.getUniformLocation(lineProgram, 'uColor'),
        };

        const pointLoc = {
            position: gl.getAttribLocation(pointProgram, 'aPosition'),
            color: gl.getAttribLocation(pointProgram, 'aColor'),
            size: gl.getAttribLocation(pointProgram, 'aSize'),
            used: gl.getAttribLocation(pointProgram, 'aUsed'),
            mvp: gl.getUniformLocation(pointProgram, 'uMVP'),
            dpr: gl.getUniformLocation(pointProgram, 'uDevicePixelRatio'),
            cameraDir: gl.getUniformLocation(pointProgram, 'uCameraDir'),
        };

        const gridVertices = buildSkyGridVertices();
        const horizonVertices = buildSkyRingVertices(0, 4);

        const gridBuffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, gridBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, gridVertices, gl.STATIC_DRAW);

        const horizonBuffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, horizonBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, horizonVertices, gl.STATIC_DRAW);

        const satPosBuffer = gl.createBuffer();
        const satColorBuffer = gl.createBuffer();
        const satSizeBuffer = gl.createBuffer();
        const satUsedBuffer = gl.createBuffer();

        let satCount = 0;
        let satLabels = [];
        let cssWidth = 0;
        let cssHeight = 0;
        let devicePixelRatio = 1;
        let mvpMatrix = identityMat4();
        let cameraDir = [0, 1, 0];
        let yaw = 0.8;
        let pitch = 0.6;
        let distance = 2.7;
        let rafId = null;
        let destroyed = false;
        let activePointerId = null;
        let lastPointerX = 0;
        let lastPointerY = 0;

        const resizeObserver = (typeof ResizeObserver !== 'undefined')
            ? new ResizeObserver(() => {
                requestRender();
            })
            : null;
        if (resizeObserver) resizeObserver.observe(canvas);

        canvas.addEventListener('pointerdown', onPointerDown);
        canvas.addEventListener('pointermove', onPointerMove);
        canvas.addEventListener('pointerup', onPointerUp);
        canvas.addEventListener('pointercancel', onPointerUp);
        canvas.addEventListener('wheel', onWheel, { passive: false });

        requestRender();

        function onPointerDown(evt) {
            activePointerId = evt.pointerId;
            lastPointerX = evt.clientX;
            lastPointerY = evt.clientY;
            if (canvas.setPointerCapture) canvas.setPointerCapture(evt.pointerId);
        }

        function onPointerMove(evt) {
            if (activePointerId == null || evt.pointerId !== activePointerId) return;

            const dx = evt.clientX - lastPointerX;
            const dy = evt.clientY - lastPointerY;
            lastPointerX = evt.clientX;
            lastPointerY = evt.clientY;

            yaw += dx * 0.01;
            pitch += dy * 0.01;
            pitch = Math.max(0.1, Math.min(1.45, pitch));
            requestRender();
        }

        function onPointerUp(evt) {
            if (activePointerId == null || evt.pointerId !== activePointerId) return;
            if (canvas.releasePointerCapture) {
                try {
                    canvas.releasePointerCapture(evt.pointerId);
                } catch (_) {}
            }
            activePointerId = null;
        }

        function onWheel(evt) {
            evt.preventDefault();
            distance += evt.deltaY * 0.002;
            distance = Math.max(2.0, Math.min(5.0, distance));
            requestRender();
        }

        function setSatellites(satellites) {
            const positions = [];
            const colors = [];
            const sizes = [];
            const usedFlags = [];
            const labels = [];

            (satellites || []).forEach(sat => {
                if (sat.elevation == null || sat.azimuth == null) return;

                const xyz = skyToCartesian(sat.azimuth, sat.elevation);
                const hex = CONST_COLORS[sat.constellation] || CONST_COLORS.GPS;
                const rgb = hexToRgb01(hex);

                positions.push(xyz[0], xyz[1], xyz[2]);
                colors.push(rgb[0], rgb[1], rgb[2], sat.used ? 1 : 0.85);
                sizes.push(sat.used ? 8 : 7);
                usedFlags.push(sat.used ? 1 : 0);

                labels.push({
                    text: String(sat.prn),
                    point: xyz,
                    color: hex,
                    used: !!sat.used,
                });
            });

            satLabels = labels;
            satCount = positions.length / 3;

            gl.bindBuffer(gl.ARRAY_BUFFER, satPosBuffer);
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(positions), gl.DYNAMIC_DRAW);

            gl.bindBuffer(gl.ARRAY_BUFFER, satColorBuffer);
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(colors), gl.DYNAMIC_DRAW);

            gl.bindBuffer(gl.ARRAY_BUFFER, satSizeBuffer);
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(sizes), gl.DYNAMIC_DRAW);

            gl.bindBuffer(gl.ARRAY_BUFFER, satUsedBuffer);
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(usedFlags), gl.DYNAMIC_DRAW);

            requestRender();
        }

        function requestRender() {
            if (destroyed || rafId != null) return;
            rafId = requestAnimationFrame(render);
        }

        function render() {
            rafId = null;
            if (destroyed) return;

            resizeCanvas();
            updateCameraMatrices();

            const palette = getThemePalette();

            gl.viewport(0, 0, canvas.width, canvas.height);
            gl.clearColor(palette.bg[0], palette.bg[1], palette.bg[2], 1);
            gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

            gl.enable(gl.DEPTH_TEST);
            gl.depthFunc(gl.LEQUAL);
            gl.enable(gl.BLEND);
            gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

            gl.useProgram(lineProgram);
            gl.uniformMatrix4fv(lineLoc.mvp, false, mvpMatrix);
            gl.bindBuffer(gl.ARRAY_BUFFER, gridBuffer);
            gl.enableVertexAttribArray(lineLoc.position);
            gl.vertexAttribPointer(lineLoc.position, 3, gl.FLOAT, false, 0, 0);
            gl.uniform4fv(lineLoc.color, palette.grid);
            gl.drawArrays(gl.LINES, 0, gridVertices.length / 3);

            gl.bindBuffer(gl.ARRAY_BUFFER, horizonBuffer);
            gl.vertexAttribPointer(lineLoc.position, 3, gl.FLOAT, false, 0, 0);
            gl.uniform4fv(lineLoc.color, palette.horizon);
            gl.drawArrays(gl.LINES, 0, horizonVertices.length / 3);

            if (satCount > 0) {
                gl.useProgram(pointProgram);
                gl.uniformMatrix4fv(pointLoc.mvp, false, mvpMatrix);
                gl.uniform1f(pointLoc.dpr, devicePixelRatio);
                gl.uniform3fv(pointLoc.cameraDir, new Float32Array(cameraDir));

                gl.bindBuffer(gl.ARRAY_BUFFER, satPosBuffer);
                gl.enableVertexAttribArray(pointLoc.position);
                gl.vertexAttribPointer(pointLoc.position, 3, gl.FLOAT, false, 0, 0);

                gl.bindBuffer(gl.ARRAY_BUFFER, satColorBuffer);
                gl.enableVertexAttribArray(pointLoc.color);
                gl.vertexAttribPointer(pointLoc.color, 4, gl.FLOAT, false, 0, 0);

                gl.bindBuffer(gl.ARRAY_BUFFER, satSizeBuffer);
                gl.enableVertexAttribArray(pointLoc.size);
                gl.vertexAttribPointer(pointLoc.size, 1, gl.FLOAT, false, 0, 0);

                gl.bindBuffer(gl.ARRAY_BUFFER, satUsedBuffer);
                gl.enableVertexAttribArray(pointLoc.used);
                gl.vertexAttribPointer(pointLoc.used, 1, gl.FLOAT, false, 0, 0);

                gl.drawArrays(gl.POINTS, 0, satCount);
            }

            drawOverlayLabels();
        }

        function resizeCanvas() {
            cssWidth = Math.max(1, Math.floor(canvas.clientWidth || 400));
            cssHeight = Math.max(1, Math.floor(canvas.clientHeight || 400));
            devicePixelRatio = Math.min(window.devicePixelRatio || 1, 2);

            const renderWidth = Math.floor(cssWidth * devicePixelRatio);
            const renderHeight = Math.floor(cssHeight * devicePixelRatio);
            if (canvas.width !== renderWidth || canvas.height !== renderHeight) {
                canvas.width = renderWidth;
                canvas.height = renderHeight;
            }
        }

        function updateCameraMatrices() {
            const cosPitch = Math.cos(pitch);
            const eye = [
                distance * Math.sin(yaw) * cosPitch,
                distance * Math.sin(pitch),
                distance * Math.cos(yaw) * cosPitch,
            ];

            const eyeLen = Math.hypot(eye[0], eye[1], eye[2]) || 1;
            cameraDir = [eye[0] / eyeLen, eye[1] / eyeLen, eye[2] / eyeLen];

            const view = mat4LookAt(eye, [0, 0, 0], [0, 1, 0]);
            const proj = mat4Perspective(degToRad(48), Math.max(cssWidth / cssHeight, 0.01), 0.1, 20);
            mvpMatrix = mat4Multiply(proj, view);
        }

        function drawOverlayLabels() {
            if (!overlay) return;

            const fragment = document.createDocumentFragment();
            const cardinals = [
                { text: 'N', point: [0, 0, 1] },
                { text: 'E', point: [1, 0, 0] },
                { text: 'S', point: [0, 0, -1] },
                { text: 'W', point: [-1, 0, 0] },
                { text: 'Z', point: [0, 1, 0] },
            ];

            cardinals.forEach(entry => {
                addLabel(fragment, entry.text, entry.point, 'gps-sky-label gps-sky-label-cardinal');
            });

            satLabels.forEach(sat => {
                const cls = 'gps-sky-label gps-sky-label-sat' + (sat.used ? '' : ' unused');
                addLabel(fragment, sat.text, sat.point, cls, sat.color);
            });

            overlay.replaceChildren(fragment);
        }

        function addLabel(fragment, text, point, className, color) {
            const facing = point[0] * cameraDir[0] + point[1] * cameraDir[1] + point[2] * cameraDir[2];
            if (facing <= 0.02) return;

            const projected = projectPoint(point, mvpMatrix, cssWidth, cssHeight);
            if (!projected) return;

            const label = document.createElement('span');
            label.className = className;
            label.textContent = text;
            label.style.left = projected.x.toFixed(1) + 'px';
            label.style.top = projected.y.toFixed(1) + 'px';
            if (color) label.style.color = color;
            fragment.appendChild(label);
        }

        function getThemePalette() {
            const cs = getComputedStyle(document.documentElement);
            const bg = parseCssColor(cs.getPropertyValue('--bg-card').trim(), '#0d1117');
            const grid = parseCssColor(cs.getPropertyValue('--border-color').trim(), '#3a4254');
            const accent = parseCssColor(cs.getPropertyValue('--accent-cyan').trim(), '#4aa3ff');

            return {
                bg: bg,
                grid: [grid[0], grid[1], grid[2], 0.42],
                horizon: [accent[0], accent[1], accent[2], 0.56],
            };
        }

        function destroy() {
            destroyed = true;
            if (rafId != null) cancelAnimationFrame(rafId);
            canvas.removeEventListener('pointerdown', onPointerDown);
            canvas.removeEventListener('pointermove', onPointerMove);
            canvas.removeEventListener('pointerup', onPointerUp);
            canvas.removeEventListener('pointercancel', onPointerUp);
            canvas.removeEventListener('wheel', onWheel);
            if (resizeObserver) {
                try {
                    resizeObserver.disconnect();
                } catch (_) {}
            }
            if (overlay) overlay.replaceChildren();
        }

        return {
            setSatellites: setSatellites,
            requestRender: requestRender,
            destroy: destroy,
        };
    }

    function buildSkyGridVertices() {
        const vertices = [];

        [15, 30, 45, 60, 75].forEach(el => {
            appendLineStrip(vertices, buildRingPoints(el, 6));
        });

        for (let az = 0; az < 360; az += 30) {
            appendLineStrip(vertices, buildMeridianPoints(az, 5));
        }

        return new Float32Array(vertices);
    }

    function buildSkyRingVertices(elevation, stepAz) {
        const vertices = [];
        appendLineStrip(vertices, buildRingPoints(elevation, stepAz));
        return new Float32Array(vertices);
    }

    function buildRingPoints(elevation, stepAz) {
        const points = [];
        for (let az = 0; az <= 360; az += stepAz) {
            points.push(skyToCartesian(az, elevation));
        }
        return points;
    }

    function buildMeridianPoints(azimuth, stepEl) {
        const points = [];
        for (let el = 0; el <= 90; el += stepEl) {
            points.push(skyToCartesian(azimuth, el));
        }
        return points;
    }

    function appendLineStrip(target, points) {
        for (let i = 1; i < points.length; i += 1) {
            const a = points[i - 1];
            const b = points[i];
            target.push(a[0], a[1], a[2], b[0], b[1], b[2]);
        }
    }

    function skyToCartesian(azimuthDeg, elevationDeg) {
        const az = degToRad(azimuthDeg);
        const el = degToRad(elevationDeg);
        const cosEl = Math.cos(el);
        return [
            cosEl * Math.sin(az),
            Math.sin(el),
            cosEl * Math.cos(az),
        ];
    }

    function degToRad(deg) {
        return deg * Math.PI / 180;
    }

    function createProgram(gl, vertexSource, fragmentSource) {
        const vertexShader = compileShader(gl, gl.VERTEX_SHADER, vertexSource);
        const fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
        if (!vertexShader || !fragmentShader) return null;

        const program = gl.createProgram();
        gl.attachShader(program, vertexShader);
        gl.attachShader(program, fragmentShader);
        gl.linkProgram(program);

        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
            console.warn('WebGL program link failed:', gl.getProgramInfoLog(program));
            gl.deleteProgram(program);
            return null;
        }

        return program;
    }

    function compileShader(gl, type, source) {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);

        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
            console.warn('WebGL shader compile failed:', gl.getShaderInfoLog(shader));
            gl.deleteShader(shader);
            return null;
        }

        return shader;
    }

    function identityMat4() {
        return new Float32Array([
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1,
        ]);
    }

    function mat4Perspective(fovy, aspect, near, far) {
        const f = 1 / Math.tan(fovy / 2);
        const nf = 1 / (near - far);

        return new Float32Array([
            f / aspect, 0, 0, 0,
            0, f, 0, 0,
            0, 0, (far + near) * nf, -1,
            0, 0, (2 * far * near) * nf, 0,
        ]);
    }

    function mat4LookAt(eye, center, up) {
        const zx = eye[0] - center[0];
        const zy = eye[1] - center[1];
        const zz = eye[2] - center[2];
        const zLen = Math.hypot(zx, zy, zz) || 1;
        const znx = zx / zLen;
        const zny = zy / zLen;
        const znz = zz / zLen;

        const xx = up[1] * znz - up[2] * zny;
        const xy = up[2] * znx - up[0] * znz;
        const xz = up[0] * zny - up[1] * znx;
        const xLen = Math.hypot(xx, xy, xz) || 1;
        const xnx = xx / xLen;
        const xny = xy / xLen;
        const xnz = xz / xLen;

        const ynx = zny * xnz - znz * xny;
        const yny = znz * xnx - znx * xnz;
        const ynz = znx * xny - zny * xnx;

        return new Float32Array([
            xnx, ynx, znx, 0,
            xny, yny, zny, 0,
            xnz, ynz, znz, 0,
            -(xnx * eye[0] + xny * eye[1] + xnz * eye[2]),
            -(ynx * eye[0] + yny * eye[1] + ynz * eye[2]),
            -(znx * eye[0] + zny * eye[1] + znz * eye[2]),
            1,
        ]);
    }

    function mat4Multiply(a, b) {
        const out = new Float32Array(16);
        for (let col = 0; col < 4; col += 1) {
            for (let row = 0; row < 4; row += 1) {
                out[col * 4 + row] =
                    a[row] * b[col * 4] +
                    a[4 + row] * b[col * 4 + 1] +
                    a[8 + row] * b[col * 4 + 2] +
                    a[12 + row] * b[col * 4 + 3];
            }
        }
        return out;
    }

    function projectPoint(point, matrix, width, height) {
        const x = point[0];
        const y = point[1];
        const z = point[2];

        const clipX = matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12];
        const clipY = matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13];
        const clipW = matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15];
        if (clipW <= 0.0001) return null;

        const ndcX = clipX / clipW;
        const ndcY = clipY / clipW;
        if (Math.abs(ndcX) > 1.2 || Math.abs(ndcY) > 1.2) return null;

        return {
            x: (ndcX * 0.5 + 0.5) * width,
            y: (1 - (ndcY * 0.5 + 0.5)) * height,
        };
    }

    function parseCssColor(raw, fallbackHex) {
        const value = (raw || '').trim();

        if (value.startsWith('#')) {
            return hexToRgb01(value);
        }

        const match = value.match(/rgba?\(([^)]+)\)/i);
        if (match) {
            const parts = match[1].split(',').map(part => parseFloat(part.trim()));
            if (parts.length >= 3 && parts.every(n => Number.isFinite(n))) {
                return [parts[0] / 255, parts[1] / 255, parts[2] / 255];
            }
        }

        return hexToRgb01(fallbackHex || '#0d1117');
    }

    function hexToRgb01(hex) {
        let clean = (hex || '').trim().replace('#', '');
        if (clean.length === 3) {
            clean = clean.split('').map(ch => ch + ch).join('');
        }
        if (!/^[0-9a-fA-F]{6}$/.test(clean)) {
            return [0, 0, 0];
        }

        const num = parseInt(clean, 16);
        return [
            ((num >> 16) & 255) / 255,
            ((num >> 8) & 255) / 255,
            (num & 255) / 255,
        ];
    }

    // ========================
    // Signal Strength Bars
    // ========================

    function drawSignalBars(satellites) {
        const container = document.getElementById('gpsSignalBars');
        if (!container) return;

        container.innerHTML = '';

        if (satellites.length === 0) return;

        // Sort: used first, then by PRN
        const sorted = [...satellites].sort((a, b) => {
            if (a.used !== b.used) return a.used ? -1 : 1;
            return a.prn - b.prn;
        });

        const maxSnr = 50; // dB-Hz typical max for display

        sorted.forEach(sat => {
            const snr = sat.snr || 0;
            const heightPct = Math.min(snr / maxSnr * 100, 100);
            const color = CONST_COLORS[sat.constellation] || CONST_COLORS['GPS'];
            const constClass = 'gps-const-' + (sat.constellation || 'GPS').toLowerCase();

            const wrap = document.createElement('div');
            wrap.className = 'gps-signal-bar-wrap';

            const snrLabel = document.createElement('span');
            snrLabel.className = 'gps-signal-snr';
            snrLabel.textContent = snr > 0 ? Math.round(snr) : '';

            const bar = document.createElement('div');
            bar.className = 'gps-signal-bar ' + constClass + (sat.used ? '' : ' unused');
            bar.style.height = Math.max(heightPct, 2) + '%';
            bar.title = `PRN ${sat.prn} (${sat.constellation}) - ${Math.round(snr)} dB-Hz${sat.used ? ' [USED]' : ''}`;

            const prn = document.createElement('span');
            prn.className = 'gps-signal-prn';
            prn.textContent = sat.prn;

            wrap.appendChild(snrLabel);
            wrap.appendChild(bar);
            wrap.appendChild(prn);
            container.appendChild(wrap);
        });
    }

    // ========================
    // Cleanup
    // ========================

    function destroy() {
        unsubscribeFromStream();
        stopSkyPolling();
        stopStatusPolling();
        if (themeObserver) {
            themeObserver.disconnect();
            themeObserver = null;
        }
        if (skyRenderer) {
            skyRenderer.destroy();
            skyRenderer = null;
        }
        skyRendererInitAttempted = false;
        skyRendererInitPromise = null;
        setSkyCanvasFallbackMode(false);
    }

    return {
        setSkyView,
        init: init,
        connect: connect,
        disconnect: disconnect,
        destroy: destroy,
    };
})();
