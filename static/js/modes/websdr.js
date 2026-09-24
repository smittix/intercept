/**
 * Intercept - WebSDR Mode
 * HF/Shortwave KiwiSDR Network Integration with In-App Audio
 */

// ============== STATE ==============
let websdrMap = null;
let websdrMarkers = [];
let websdrReceivers = [];
let websdrInitialized = false;
let websdrSpyStationsLoaded = false;
let websdrMapType = null;
let websdrGlobe = null;
let websdrGlobePopup = null;
let websdrSelectedReceiverIndex = null;
let websdrGlobeScriptPromise = null;
let websdrResizeObserver = null;
let websdrResizeHooked = false;
let websdrGlobeFallbackNotified = false;

const WEBSDR_GLOBE_SCRIPT_URLS = [
    'https://cdn.jsdelivr.net/npm/globe.gl@2.33.1/dist/globe.gl.min.js',
];
const WEBSDR_GLOBE_TEXTURE_URL = '/static/images/globe/earth-dark.jpg';

// KiwiSDR audio state
let kiwiWebSocket = null;
let kiwiAudioContext = null;
let kiwiScriptProcessor = null;
let kiwiGainNode = null;
let kiwiAudioBuffer = [];
let kiwiConnected = false;
let kiwiCurrentFreq = 0;
let kiwiCurrentMode = 'am';
let kiwiSmeter = 0;
let kiwiSmeterInterval = null;
let kiwiReceiverName = '';

const KIWI_SAMPLE_RATE = 12000;

// ============== INITIALIZATION ==============

async function initWebSDR() {
    if (websdrInitialized) {
        setTimeout(invalidateWebSDRViewport, 100);
        return;
    }

    const mapEl = document.getElementById('websdrMap');
    if (!mapEl) return;

    const globeReady = await ensureWebsdrGlobeLibrary();

    // Wait for a paint frame so the browser computes layout after the
    // display:flex change in switchMode.  Without this, Globe()(mapEl) can
    // run before clientWidth/clientHeight are non-zero (especially when
    // scripts are served from cache and resolve before the first layout pass).
    await new Promise(resolve => requestAnimationFrame(resolve));

    // If the mode was switched away while scripts were loading, abort so
    // websdrInitialized stays false and we retry cleanly next time.
    if (!mapEl.clientWidth || !mapEl.clientHeight) return;

    if (globeReady && initWebsdrGlobe(mapEl)) {
        websdrMapType = 'globe';
    } else if (typeof L !== 'undefined' && await initWebsdrLeaflet(mapEl)) {
        websdrMapType = 'leaflet';
        if (!websdrGlobeFallbackNotified && typeof showNotification === 'function') {
            showNotification('WebSDR', '3D globe unavailable, using fallback map');
            websdrGlobeFallbackNotified = true;
        }
    } else {
        console.error('[WEBSDR] Unable to initialize globe or map renderer');
        return;
    }

    websdrInitialized = true;

    if (!websdrSpyStationsLoaded) {
        loadSpyStationPresets();
    }

    setupWebsdrResizeHandling(mapEl);
    if (websdrReceivers.length > 0) {
        plotReceiversOnMap(websdrReceivers);
    }
    [100, 300, 600, 1000].forEach(delay => {
        setTimeout(invalidateWebSDRViewport, delay);
    });
}

// ============== RECEIVER SEARCH ==============

function searchReceivers(refresh) {
    const freqKhz = parseFloat(document.getElementById('websdrFrequency')?.value || 0);

    let url = '/websdr/receivers?available=true';
    if (freqKhz > 0) url += `&freq_khz=${freqKhz}`;
    if (refresh) url += '&refresh=true';

    fetch(url)
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                websdrReceivers = data.receivers || [];
                websdrSelectedReceiverIndex = null;
                hideWebsdrGlobePopup();
                renderReceiverList(websdrReceivers);
                plotReceiversOnMap(websdrReceivers);

                const countEl = document.getElementById('websdrReceiverCount');
                if (countEl) countEl.textContent = `${websdrReceivers.length} found`;
            }
        })
        .catch(err => console.error('[WEBSDR] Search error:', err));
}

// ============== MAP ==============

function plotReceiversOnMap(receivers) {
    if (websdrMapType === 'globe' && websdrGlobe) {
        plotReceiversOnGlobe(receivers);
        return;
    }

    if (!websdrMap) return;

    websdrMarkers.forEach(m => websdrMap.removeLayer(m));
    websdrMarkers = [];

    receivers.forEach((rx, idx) => {
        if (rx.lat == null || rx.lon == null) return;

        const marker = L.circleMarker([rx.lat, rx.lon], {
            radius: 6,
            fillColor: rx.available ? (getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00d4ff') : '#666',
            color: rx.available ? (getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00d4ff') : '#666',
            weight: 1,
            opacity: 0.8,
            fillOpacity: 0.6,
        });

        marker.bindPopup(`
            <div style="font-size: 12px; min-width: 200px;">
                <strong>${escapeHtmlWebsdr(rx.name)}</strong><br>
                ${rx.location ? `<span style="color: #aaa;">${escapeHtmlWebsdr(rx.location)}</span><br>` : ''}
                <span style="color: #888;">Antenna: ${escapeHtmlWebsdr(rx.antenna || 'Unknown')}</span><br>
                <span style="color: #888;">Users: ${rx.users}/${rx.users_max}</span><br>
                <button onclick="selectReceiver(${idx})" style="margin-top: 6px; padding: 4px 12px; background: var(--accent-cyan); color: #000; border: none; border-radius: 3px; cursor: pointer; font-weight: bold;">Listen</button>
            </div>
        `);

        marker.addTo(websdrMap);
        websdrMarkers.push(marker);
    });

    if (websdrMarkers.length > 0) {
        const group = L.featureGroup(websdrMarkers);
        websdrMap.fitBounds(group.getBounds(), { padding: [30, 30] });
    }
}

async function ensureWebsdrGlobeLibrary() {
    if (typeof window.Globe === 'function') return true;
    if (!isWebglSupported()) return false;

    if (!websdrGlobeScriptPromise) {
        websdrGlobeScriptPromise = WEBSDR_GLOBE_SCRIPT_URLS
            .reduce(
                (promise, src) => promise.then(() => loadWebsdrScript(src)),
                Promise.resolve()
            )
            .then(() => typeof window.Globe === 'function')
            .catch((error) => {
                console.warn('[WEBSDR] Failed to load globe scripts:', error);
                return false;
            });
    }

    const loaded = await websdrGlobeScriptPromise;
    if (!loaded) {
        websdrGlobeScriptPromise = null;
    }
    return loaded;
}

function loadWebsdrScript(src) {
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
        script.dataset.websdrSrc = src;
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

function isWebglSupported() {
    try {
        const canvas = document.createElement('canvas');
        return !!(canvas.getContext('webgl') || canvas.getContext('experimental-webgl'));
    } catch (_) {
        return false;
    }
}

function initWebsdrGlobe(mapEl) {
    if (typeof window.Globe !== 'function' || !isWebglSupported()) return false;

    mapEl.innerHTML = '';
    const _wsdrTier = document.documentElement.getAttribute('data-ui-tier') || 'enhanced';
    mapEl.style.background = _wsdrTier === 'enhanced'
        ? 'radial-gradient(circle at 30% 20%, rgba(4, 18, 22, 0.92), rgba(2, 8, 10, 0.96) 58%, rgba(0, 2, 2, 0.99) 100%)'
        : 'radial-gradient(circle at 30% 20%, rgba(14, 42, 68, 0.9), rgba(4, 9, 16, 0.95) 58%, rgba(2, 4, 9, 0.98) 100%)';
    mapEl.style.cursor = 'grab';

    const _wsdrAccent = getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#3bb9ff';
    websdrGlobe = window.Globe()(mapEl)
        .backgroundColor('rgba(0,0,0,0)')
        .globeImageUrl(WEBSDR_GLOBE_TEXTURE_URL)
        .showAtmosphere(true)
        .atmosphereColor(_wsdrAccent)
        .atmosphereAltitude(0.17)
        .pointRadius('radius')
        .pointAltitude('altitude')
        .pointColor('color')
        .pointsTransitionDuration(250)
        .pointLabel(point => point.label || '')
        .onPointHover(point => {
            mapEl.style.cursor = point ? 'pointer' : 'grab';
        })
        .onPointClick((point, event) => {
            if (!point) return;
            showWebsdrGlobePopup(point, event);
        });

    const controls = websdrGlobe.controls();
    if (controls) {
        controls.autoRotate = true;
        controls.autoRotateSpeed = 0.25;
        controls.enablePan = false;
        controls.minDistance = 140;
        controls.maxDistance = 380;
        controls.rotateSpeed = 0.7;
        controls.zoomSpeed = 0.8;
    }

    ensureWebsdrGlobePopup(mapEl);
    resizeWebsdrGlobe();
    // Grid layout may not have settled on the first rAF; re-sync after one frame.
    requestAnimationFrame(() => resizeWebsdrGlobe());
    return true;
}

async function initWebsdrLeaflet(mapEl) {
    if (typeof L === 'undefined') return false;

    mapEl.innerHTML = '';
    const mapHeight = mapEl.clientHeight || 500;
    const minZoom = Math.ceil(Math.log2(mapHeight / 256));

    websdrMap = L.map('websdrMap', {
        center: [20, 0],
        zoom: Math.max(minZoom, 2),
        minZoom: Math.max(minZoom, 2),
        zoomControl: true,
        maxBounds: [[-85, -360], [85, 360]],
        maxBoundsViscosity: 1.0,
    });

    // Add fallback tiles immediately so the map is visible instantly. If Settings
    // already loaded elsewhere this session, use it directly instead — avoids a
    // flash of the unkeyed CartoDB URL when a CARTO API key has been configured.
    const fallbackTiles = (typeof Settings !== 'undefined' && Settings._initialized && Settings.createTileLayer)
        ? Settings.createTileLayer().addTo(websdrMap)
        : L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
            subdomains: 'abcd',
            maxZoom: 19,
            className: 'tile-layer-cyan',
        }).addTo(websdrMap);

    // Upgrade tiles in background via Settings (with timeout fallback)
    if (typeof Settings !== 'undefined' && Settings.createTileLayer) {
        try {
            await Promise.race([
                Settings.init(),
                new Promise((_, reject) => setTimeout(() => reject(new Error('Settings timeout')), 5000))
            ]);
            websdrMap.removeLayer(fallbackTiles);
            Settings.createTileLayer().addTo(websdrMap);
            Settings.registerMap(websdrMap);
        } catch (e) {
            console.warn('WebSDR: Settings init failed/timed out, using fallback tiles:', e);
        }
    }

    if (typeof MapUtils !== 'undefined') MapUtils.addGraticuleControl(websdrMap);
    mapEl.style.background = '#1a1d29';
    return true;
}

function setupWebsdrResizeHandling(mapEl) {
    if (typeof ResizeObserver !== 'undefined') {
        if (websdrResizeObserver) {
            websdrResizeObserver.disconnect();
        }
        websdrResizeObserver = new ResizeObserver(() => invalidateWebSDRViewport());
        websdrResizeObserver.observe(mapEl);
    }

    if (!websdrResizeHooked) {
        window.addEventListener('resize', invalidateWebSDRViewport);
        window.addEventListener('orientationchange', () => setTimeout(invalidateWebSDRViewport, 120));
        websdrResizeHooked = true;
    }
}

function invalidateWebSDRViewport() {
    if (websdrMapType === 'globe') {
        resizeWebsdrGlobe();
        return;
    }
    if (websdrMap && typeof websdrMap.invalidateSize === 'function') {
        websdrMap.invalidateSize({ pan: false, animate: false });
    }
}

function resizeWebsdrGlobe() {
    if (!websdrGlobe) return;
    const mapEl = document.getElementById('websdrMap');
    if (!mapEl) return;

    const width = mapEl.clientWidth;
    const height = mapEl.clientHeight;
    if (!width || !height) return;

    websdrGlobe.width(width);
    websdrGlobe.height(height);
}

function plotReceiversOnGlobe(receivers) {
    if (!websdrGlobe) return;

    const points = [];
    receivers.forEach((rx, idx) => {
        const lat = Number(rx.lat);
        const lon = Number(rx.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

        const selected = idx === websdrSelectedReceiverIndex;
        points.push({
            lat: lat,
            lng: lon,
            receiverIndex: idx,
            radius: selected ? 0.52 : 0.38,
            altitude: selected ? 0.1 : 0.04,
            color: selected ? '#00ff88' : (rx.available ? (getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00d4ff') : '#5f6976'),
            label: buildWebsdrPointLabel(rx, idx),
        });
    });

    websdrGlobe.pointsData(points);

    if (points.length > 0) {
        if (websdrSelectedReceiverIndex != null) {
            const selectedPoint = points.find(point => point.receiverIndex === websdrSelectedReceiverIndex);
            if (selectedPoint) {
                websdrGlobe.pointOfView({ lat: selectedPoint.lat, lng: selectedPoint.lng, altitude: 1.45 }, 900);
                return;
            }
        }

        const center = computeWebsdrGlobeCenter(points);
        websdrGlobe.pointOfView(center, 900);
    }
}

function computeWebsdrGlobeCenter(points) {
    if (!points.length) return { lat: 20, lng: 0, altitude: 2.1 };

    let x = 0;
    let y = 0;
    let z = 0;
    points.forEach(point => {
        const latRad = point.lat * Math.PI / 180;
        const lonRad = point.lng * Math.PI / 180;
        x += Math.cos(latRad) * Math.cos(lonRad);
        y += Math.cos(latRad) * Math.sin(lonRad);
        z += Math.sin(latRad);
    });

    const count = points.length;
    x /= count;
    y /= count;
    z /= count;

    const hyp = Math.sqrt((x * x) + (y * y));
    const centerLat = Math.atan2(z, hyp) * 180 / Math.PI;
    const centerLng = Math.atan2(y, x) * 180 / Math.PI;

    let meanAngularDistance = 0;
    const centerLatRad = centerLat * Math.PI / 180;
    const centerLngRad = centerLng * Math.PI / 180;
    points.forEach(point => {
        const latRad = point.lat * Math.PI / 180;
        const lonRad = point.lng * Math.PI / 180;
        const cosAngle = (
            (Math.sin(centerLatRad) * Math.sin(latRad)) +
            (Math.cos(centerLatRad) * Math.cos(latRad) * Math.cos(lonRad - centerLngRad))
        );
        const safeCos = Math.max(-1, Math.min(1, cosAngle));
        meanAngularDistance += Math.acos(safeCos) * 180 / Math.PI;
    });
    meanAngularDistance /= count;

    const altitude = Math.min(2.9, Math.max(1.35, 1.35 + (meanAngularDistance / 45)));
    return { lat: centerLat, lng: centerLng, altitude: altitude };
}

function ensureWebsdrGlobePopup(mapEl) {
    if (websdrGlobePopup) {
        if (websdrGlobePopup.parentElement !== mapEl) {
            mapEl.appendChild(websdrGlobePopup);
        }
        return;
    }

    websdrGlobePopup = document.createElement('div');
    websdrGlobePopup.id = 'websdrGlobePopup';
    websdrGlobePopup.style.position = 'absolute';
    websdrGlobePopup.style.minWidth = '220px';
    websdrGlobePopup.style.maxWidth = '260px';
    websdrGlobePopup.style.padding = '10px';
    websdrGlobePopup.style.borderRadius = '8px';
    const _wsdrPopupRgb = getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan-rgb').trim() || '0, 212, 255';
    const _wsdrPopupTier = document.documentElement.getAttribute('data-ui-tier') || 'enhanced';
    websdrGlobePopup.style.border = `1px solid rgba(${_wsdrPopupRgb}, 0.35)`;
    websdrGlobePopup.style.background = _wsdrPopupTier === 'enhanced' ? 'rgba(2, 8, 10, 0.94)' : 'rgba(5, 13, 20, 0.92)';
    websdrGlobePopup.style.backdropFilter = 'blur(4px)';
    websdrGlobePopup.style.boxShadow = '0 8px 24px rgba(0, 0, 0, 0.4)';
    websdrGlobePopup.style.color = 'var(--text-primary)';
    websdrGlobePopup.style.display = 'none';
    websdrGlobePopup.style.zIndex = '20';
    mapEl.appendChild(websdrGlobePopup);

    if (!mapEl.dataset.websdrPopupHooked) {
        mapEl.addEventListener('click', (event) => {
            if (!websdrGlobePopup || websdrGlobePopup.style.display === 'none') return;
            if (event.target.closest('#websdrGlobePopup')) return;
            hideWebsdrGlobePopup();
        });
        mapEl.dataset.websdrPopupHooked = 'true';
    }
}

function showWebsdrGlobePopup(point, event) {
    if (!websdrGlobePopup || !point || point.receiverIndex == null) return;
    const rx = websdrReceivers[point.receiverIndex];
    if (!rx) return;

    const mapEl = document.getElementById('websdrMap');
    if (!mapEl) return;

    websdrSelectedReceiverIndex = point.receiverIndex;
    renderReceiverList(websdrReceivers);
    plotReceiversOnGlobe(websdrReceivers);

    websdrGlobePopup.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: start; gap: 10px; margin-bottom: 6px;">
            <strong style="font-size: 12px; color: var(--accent-cyan);">${escapeHtmlWebsdr(rx.name)}</strong>
            <button type="button" data-websdr-popup-close style="border: none; background: transparent; color: var(--text-muted); cursor: pointer; font-size: 14px; line-height: 1;">&times;</button>
        </div>
        ${rx.location ? `<div style="font-size: 10px; color: var(--text-secondary); margin-bottom: 3px;">${escapeHtmlWebsdr(rx.location)}</div>` : ''}
        <div style="font-size: 10px; color: var(--text-muted); margin-bottom: 2px;">Antenna: ${escapeHtmlWebsdr(rx.antenna || 'Unknown')}</div>
        <div style="font-size: 10px; color: var(--text-muted); margin-bottom: 10px;">Users: ${rx.users}/${rx.users_max}</div>
        <button type="button" data-websdr-listen style="width: 100%; padding: 5px 10px; background: var(--accent-cyan); color: #041018; border: none; border-radius: 4px; cursor: pointer; font-weight: 700;">Listen</button>
    `;
    websdrGlobePopup.style.display = 'block';

    const rect = mapEl.getBoundingClientRect();
    const x = event && Number.isFinite(event.clientX) ? (event.clientX - rect.left) : (rect.width / 2);
    const y = event && Number.isFinite(event.clientY) ? (event.clientY - rect.top) : (rect.height / 2);
    const popupWidth = 260;
    const popupHeight = 155;
    const left = Math.max(12, Math.min(rect.width - popupWidth - 12, x + 12));
    const top = Math.max(12, Math.min(rect.height - popupHeight - 12, y + 12));
    websdrGlobePopup.style.left = `${left}px`;
    websdrGlobePopup.style.top = `${top}px`;

    const closeBtn = websdrGlobePopup.querySelector('[data-websdr-popup-close]');
    if (closeBtn) {
        closeBtn.onclick = () => hideWebsdrGlobePopup();
    }
    const listenBtn = websdrGlobePopup.querySelector('[data-websdr-listen]');
    if (listenBtn) {
        listenBtn.onclick = () => selectReceiver(point.receiverIndex);
    }

    if (event && typeof event.stopPropagation === 'function') {
        event.stopPropagation();
    }
}

function hideWebsdrGlobePopup() {
    if (websdrGlobePopup) {
        websdrGlobePopup.style.display = 'none';
    }
}

function buildWebsdrPointLabel(rx, idx) {
    const location = rx.location ? escapeHtmlWebsdr(rx.location) : 'Unknown location';
    const antenna = escapeHtmlWebsdr(rx.antenna || 'Unknown antenna');
    return `
        <div style="padding: 4px 6px; font-size: 11px; background: rgba(4, 12, 19, 0.9); border: 1px solid var(--border-glow); border-radius: 4px;">
            <div style="color: var(--accent-cyan); font-weight: 600;">${escapeHtmlWebsdr(rx.name)}</div>
            <div style="color: #a5b1c3;">${location}</div>
            <div style="color: #8f9fb3;">${antenna} · ${rx.users}/${rx.users_max}</div>
            <div style="color: #7a899b; margin-top: 2px;">Receiver #${idx + 1}</div>
        </div>
    `;
}

// ============== RECEIVER LIST ==============

function renderReceiverList(receivers) {
    const container = document.getElementById('websdrReceiverList');
    if (!container) return;

    if (receivers.length === 0) {
        container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 20px;">No receivers found</div>';
        return;
    }

    container.innerHTML = receivers.slice(0, 50).map((rx, idx) => {
        const selected = idx === websdrSelectedReceiverIndex;
        const _wsdrRxRgb = getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan-rgb').trim() || '0, 212, 255';
        const baseBg = selected ? `rgba(${_wsdrRxRgb},0.14)` : 'transparent';
        const hoverBg = selected ? `rgba(${_wsdrRxRgb},0.18)` : `rgba(${_wsdrRxRgb},0.05)`;
        return `
        <div style="padding: 8px 8px 8px 10px; border-bottom: 1px solid rgba(255,255,255,0.05); cursor: pointer; transition: background 0.2s; border-left: 2px solid ${selected ? 'var(--accent-cyan)' : 'transparent'}; background: ${baseBg};"
             onmouseover="this.style.background='${hoverBg}'" onmouseout="this.style.background='${baseBg}'"
             onclick="selectReceiver(${idx})">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <strong style="font-size: 11px; color: ${selected ? 'var(--accent-cyan)' : 'var(--text-primary)'};">${escapeHtmlWebsdr(rx.name)}</strong>
                <span style="font-size: 9px; padding: 1px 6px; background: ${rx.available ? 'rgba(0,230,118,0.15)' : 'rgba(158,158,158,0.15)'}; color: ${rx.available ? '#00e676' : '#9e9e9e'}; border-radius: 3px;">${rx.users}/${rx.users_max}</span>
            </div>
            <div style="font-size: 9px; color: var(--text-muted); margin-top: 2px;">
                ${rx.location ? escapeHtmlWebsdr(rx.location) + ' · ' : ''}${escapeHtmlWebsdr(rx.antenna || '')}
                ${rx.distance_km !== undefined ? ` · ${rx.distance_km} km` : ''}
            </div>
        </div>
    `;
    }).join('');
}

// ============== SELECT RECEIVER ==============

function selectReceiver(index) {
    const rx = websdrReceivers[index];
    if (!rx) return;

    const freqKhz = parseFloat(document.getElementById('websdrFrequency')?.value || 7000);
    const mode = document.getElementById('websdrMode_select')?.value || 'am';

    websdrSelectedReceiverIndex = index;
    renderReceiverList(websdrReceivers);
    focusReceiverOnMap(rx);
    hideWebsdrGlobePopup();

    kiwiReceiverName = rx.name;

    // Connect via backend proxy
    connectToReceiver(rx.url, freqKhz, mode);
}

function focusReceiverOnMap(rx) {
    const lat = Number(rx.lat);
    const lon = Number(rx.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

    if (websdrMapType === 'globe' && websdrGlobe) {
        plotReceiversOnGlobe(websdrReceivers);
        websdrGlobe.pointOfView({ lat: lat, lng: lon, altitude: 1.4 }, 900);
        return;
    }

    if (websdrMap) {
        websdrMap.setView([lat, lon], 6);
    }
}

// ============== KIWISDR AUDIO CONNECTION ==============

function connectToReceiver(receiverUrl, freqKhz, mode) {
    // Disconnect if already connected
    if (kiwiWebSocket) {
        disconnectFromReceiver();
    }

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${location.host}/ws/kiwi-audio`;

    kiwiWebSocket = new WebSocket(wsUrl);
    kiwiWebSocket.binaryType = 'arraybuffer';

    kiwiWebSocket.onopen = () => {
        kiwiWebSocket.send(JSON.stringify({
            cmd: 'connect',
            url: receiverUrl,
            freq_khz: freqKhz,
            mode: mode,
        }));
        updateKiwiUI('connecting');
    };

    kiwiWebSocket.onmessage = (event) => {
        if (typeof event.data === 'string') {
            const msg = JSON.parse(event.data);
            handleKiwiStatus(msg);
        } else {
            handleKiwiAudio(event.data);
        }
    };

    kiwiWebSocket.onclose = () => {
        kiwiConnected = false;
        updateKiwiUI('disconnected');
    };

    kiwiWebSocket.onerror = () => {
        updateKiwiUI('disconnected');
    };
}

function handleKiwiStatus(msg) {
    switch (msg.type) {
        case 'connected':
            kiwiConnected = true;
            kiwiCurrentFreq = msg.freq_khz;
            kiwiCurrentMode = msg.mode;
            initKiwiAudioContext(msg.sample_rate || KIWI_SAMPLE_RATE);
            updateKiwiUI('connected');
            break;
        case 'tuned':
            kiwiCurrentFreq = msg.freq_khz;
            kiwiCurrentMode = msg.mode;
            updateKiwiUI('connected');
            break;
        case 'error':
            console.error('[KIWI] Error:', msg.message);
            if (typeof showNotification === 'function') {
                showNotification('WebSDR', msg.message);
            }
            updateKiwiUI('error');
            break;
        case 'disconnected':
            kiwiConnected = false;
            cleanupKiwiAudio();
            updateKiwiUI('disconnected');
            break;
    }
}

function handleKiwiAudio(arrayBuffer) {
    if (arrayBuffer.byteLength < 4) return;

    // First 2 bytes: S-meter (big-endian int16)
    const view = new DataView(arrayBuffer);
    kiwiSmeter = view.getInt16(0, false);

    // Remaining bytes: PCM 16-bit signed LE
    const pcmData = new Int16Array(arrayBuffer, 2);

    // Convert to float32 [-1, 1] for Web Audio API
    const float32 = new Float32Array(pcmData.length);
    for (let i = 0; i < pcmData.length; i++) {
        float32[i] = pcmData[i] / 32768.0;
    }

    // Add to playback buffer (limit buffer size to ~2s)
    kiwiAudioBuffer.push(float32);
    const maxChunks = Math.ceil((KIWI_SAMPLE_RATE * 2) / 512);
    while (kiwiAudioBuffer.length > maxChunks) {
        kiwiAudioBuffer.shift();
    }
}

function initKiwiAudioContext(sampleRate) {
    cleanupKiwiAudio();

    kiwiAudioContext = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: sampleRate,
    });

    // Resume if suspended (autoplay policy)
    if (kiwiAudioContext.state === 'suspended') {
        kiwiAudioContext.resume();
    }

    // ScriptProcessorNode: pulls audio from buffer
    kiwiScriptProcessor = kiwiAudioContext.createScriptProcessor(2048, 0, 1);
    kiwiScriptProcessor.onaudioprocess = (e) => {
        const output = e.outputBuffer.getChannelData(0);
        let offset = 0;

        while (offset < output.length && kiwiAudioBuffer.length > 0) {
            const chunk = kiwiAudioBuffer[0];
            const needed = output.length - offset;
            const available = chunk.length;

            if (available <= needed) {
                output.set(chunk, offset);
                offset += available;
                kiwiAudioBuffer.shift();
            } else {
                output.set(chunk.subarray(0, needed), offset);
                kiwiAudioBuffer[0] = chunk.subarray(needed);
                offset += needed;
            }
        }

        // Fill remaining with silence
        while (offset < output.length) {
            output[offset++] = 0;
        }
    };

    // Volume control
    kiwiGainNode = kiwiAudioContext.createGain();
    const savedVol = localStorage.getItem('kiwiVolume');
    kiwiGainNode.gain.value = savedVol !== null ? parseFloat(savedVol) / 100 : 0.8;
    const volValue = Math.round(kiwiGainNode.gain.value * 100);
    ['kiwiVolume', 'kiwiBarVolume'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = volValue;
    });

    kiwiScriptProcessor.connect(kiwiGainNode);
    kiwiGainNode.connect(kiwiAudioContext.destination);

    // S-meter display updates
    if (kiwiSmeterInterval) VisibleInterval.clear(kiwiSmeterInterval);
    kiwiSmeterInterval = VisibleInterval.set(updateSmeterDisplay, 200);
}

function disconnectFromReceiver() {
    if (kiwiWebSocket && kiwiWebSocket.readyState === WebSocket.OPEN) {
        kiwiWebSocket.send(JSON.stringify({ cmd: 'disconnect' }));
    }
    cleanupKiwiAudio();
    if (kiwiWebSocket) {
        kiwiWebSocket.close();
        kiwiWebSocket = null;
    }
    kiwiConnected = false;
    kiwiReceiverName = '';
    updateKiwiUI('disconnected');
}

function cleanupKiwiAudio() {
    if (kiwiSmeterInterval) {
        VisibleInterval.clear(kiwiSmeterInterval);
        kiwiSmeterInterval = null;
    }
    if (kiwiScriptProcessor) {
        kiwiScriptProcessor.disconnect();
        kiwiScriptProcessor = null;
    }
    if (kiwiGainNode) {
        kiwiGainNode.disconnect();
        kiwiGainNode = null;
    }
    if (kiwiAudioContext) {
        kiwiAudioContext.close().catch(() => {});
        kiwiAudioContext = null;
    }
    kiwiAudioBuffer = [];
    kiwiSmeter = 0;
}

function tuneKiwi(freqKhz, mode) {
    if (!kiwiWebSocket || !kiwiConnected) return;
    kiwiWebSocket.send(JSON.stringify({
        cmd: 'tune',
        freq_khz: freqKhz,
        mode: mode || kiwiCurrentMode,
    }));
}

function tuneFromBar() {
    const freq = parseFloat(document.getElementById('kiwiBarFrequency')?.value || 0);
    const mode = document.getElementById('kiwiBarMode')?.value || kiwiCurrentMode;
    if (freq > 0) {
        tuneKiwi(freq, mode);
        // Also update sidebar frequency
        const freqInput = document.getElementById('websdrFrequency');
        if (freqInput) freqInput.value = freq;
    }
}

function setKiwiVolume(value) {
    if (kiwiGainNode) {
        kiwiGainNode.gain.value = value / 100;
        localStorage.setItem('kiwiVolume', value);
    }
    // Sync both volume sliders
    ['kiwiVolume', 'kiwiBarVolume'].forEach(id => {
        const el = document.getElementById(id);
        if (el && el.value !== String(value)) el.value = value;
    });
}

// ============== S-METER ==============

function updateSmeterDisplay() {
    // KiwiSDR S-meter: value in 0.1 dBm units (e.g., -730 = -73 dBm = S9)
    const dbm = kiwiSmeter / 10;
    let sUnit;
    if (dbm >= -73) {
        const over = Math.round((dbm + 73));
        sUnit = over > 0 ? `S9+${over}` : 'S9';
    } else {
        sUnit = `S${Math.max(0, Math.round((dbm + 127) / 6))}`;
    }

    const pct = Math.min(100, Math.max(0, (dbm + 127) / 1.27));

    // Update both sidebar and bar S-meter displays
    ['kiwiSmeterBar', 'kiwiBarSmeter'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.width = pct + '%';
    });
    ['kiwiSmeterValue', 'kiwiBarSmeterValue'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = sUnit;
    });
}

// ============== UI UPDATES ==============

function updateKiwiUI(state) {
    const statusEl = document.getElementById('kiwiStatus');
    const controlsBar = document.getElementById('kiwiAudioControls');
    const disconnectBtn = document.getElementById('kiwiDisconnectBtn');
    const receiverNameEl = document.getElementById('kiwiReceiverName');
    const freqDisplay = document.getElementById('kiwiFreqDisplay');
    const barReceiverName = document.getElementById('kiwiBarReceiverName');
    const barFreq = document.getElementById('kiwiBarFrequency');
    const barMode = document.getElementById('kiwiBarMode');

    if (state === 'connected') {
        if (statusEl) {
            statusEl.textContent = 'CONNECTED';
            statusEl.style.color = 'var(--accent-green)';
        }
        if (controlsBar) controlsBar.style.display = 'block';
        if (disconnectBtn) disconnectBtn.style.display = 'block';
        if (receiverNameEl) {
            receiverNameEl.textContent = kiwiReceiverName;
            receiverNameEl.style.display = 'block';
        }
        if (freqDisplay) freqDisplay.textContent = kiwiCurrentFreq + ' kHz';
        if (barReceiverName) barReceiverName.textContent = kiwiReceiverName;
        if (barFreq) barFreq.value = kiwiCurrentFreq;
        if (barMode) barMode.value = kiwiCurrentMode;
    } else if (state === 'connecting') {
        if (statusEl) {
            statusEl.textContent = 'CONNECTING...';
            statusEl.style.color = 'var(--accent-orange)';
        }
    } else if (state === 'error') {
        if (statusEl) {
            statusEl.textContent = 'ERROR';
            statusEl.style.color = 'var(--accent-red)';
        }
    } else {
        // disconnected
        if (statusEl) {
            statusEl.textContent = 'DISCONNECTED';
            statusEl.style.color = 'var(--text-muted)';
        }
        if (controlsBar) controlsBar.style.display = 'none';
        if (disconnectBtn) disconnectBtn.style.display = 'none';
        if (receiverNameEl) receiverNameEl.style.display = 'none';
        if (freqDisplay) freqDisplay.textContent = '--- kHz';
        // Reset both S-meter displays (sidebar + bar)
        ['kiwiSmeterBar', 'kiwiBarSmeter'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.style.width = '0%';
        });
        ['kiwiSmeterValue', 'kiwiBarSmeterValue'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = 'S0';
        });
    }
}

// ============== SPY STATION PRESETS ==============

function loadSpyStationPresets() {
    fetch('/spy-stations/stations')
        .then(r => r.json())
        .then(data => {
            websdrSpyStationsLoaded = true;
            const container = document.getElementById('websdrSpyPresets');
            if (!container) return;

            const stations = data.stations || data || [];
            if (!Array.isArray(stations) || stations.length === 0) {
                container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 10px;">No stations available</div>';
                return;
            }

            const _wsdrSpyRgb = getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan-rgb').trim() || '0, 212, 255';
            container.innerHTML = stations.slice(0, 30).map(s => {
                const primaryFreq = s.frequencies?.find(f => f.primary) || s.frequencies?.[0];
                const freqKhz = primaryFreq?.freq_khz || 0;
                return `
                    <div style="padding: 6px 4px; border-bottom: 1px solid rgba(255,255,255,0.05); cursor: pointer; display: flex; justify-content: space-between; align-items: center;"
                         onclick="tuneToSpyStation('${escapeHtmlWebsdr(s.id)}', ${freqKhz})"
                         onmouseover="this.style.background='rgba(${_wsdrSpyRgb},0.05)'" onmouseout="this.style.background='transparent'">
                        <div>
                            <span style="color: var(--accent-cyan); font-weight: bold;">${escapeHtmlWebsdr(s.name)}</span>
                            <span style="color: var(--text-muted); font-size: 9px; margin-left: 4px;">${escapeHtmlWebsdr(s.nickname || '')}</span>
                        </div>
                        <span style="color: var(--accent-orange); font-family: var(--font-mono); font-size: 10px;">${freqKhz} kHz</span>
                    </div>
                `;
            }).join('');
        })
        .catch(err => {
            console.error('[WEBSDR] Failed to load spy station presets:', err);
        });
}

function tuneToSpyStation(stationId, freqKhz) {
    const freqInput = document.getElementById('websdrFrequency');
    if (freqInput) freqInput.value = freqKhz;

    // If already connected, just retune
    if (kiwiConnected) {
        const mode = document.getElementById('websdrMode_select')?.value || kiwiCurrentMode;
        tuneKiwi(freqKhz, mode);
        return;
    }

    // Otherwise, search for receivers at this frequency
    fetch(`/websdr/spy-station/${encodeURIComponent(stationId)}/receivers`)
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                websdrReceivers = data.receivers || [];
                websdrSelectedReceiverIndex = null;
                hideWebsdrGlobePopup();
                renderReceiverList(websdrReceivers);
                plotReceiversOnMap(websdrReceivers);

                const countEl = document.getElementById('websdrReceiverCount');
                if (countEl) countEl.textContent = `${websdrReceivers.length} for ${data.station?.name || stationId}`;

                if (typeof showNotification === 'function' && data.station) {
                    showNotification('WebSDR', `Found ${websdrReceivers.length} receivers for ${data.station.name} at ${freqKhz} kHz`);
                }
            }
        })
        .catch(err => console.error('[WEBSDR] Spy station receivers error:', err));
}

// ============== UTILITIES ==============

function escapeHtmlWebsdr(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ============== EXPORTS ==============

/**
 * Destroy — disconnect audio and clear S-meter timer for clean mode switching.
 */
function destroyWebSDR() {
    disconnectFromReceiver();
}

const WebSDR = { destroy: destroyWebSDR };

window.initWebSDR = initWebSDR;
window.searchReceivers = searchReceivers;
window.selectReceiver = selectReceiver;
window.tuneToSpyStation = tuneToSpyStation;
window.loadSpyStationPresets = loadSpyStationPresets;
window.connectToReceiver = connectToReceiver;
window.disconnectFromReceiver = disconnectFromReceiver;
window.tuneKiwi = tuneKiwi;
window.tuneFromBar = tuneFromBar;
window.setKiwiVolume = setKiwiVolume;
window.WebSDR = WebSDR;
