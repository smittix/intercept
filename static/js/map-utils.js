/**
 * MapUtils — shared Leaflet map initialisation and tactical overlays.
 *
 * Usage:
 *   const map = MapUtils.init('myMapDiv', { center: [51.5, -0.1], zoom: 8 });
 *   const overlays = MapUtils.addTacticalOverlays(map, {
 *       rangeRings: { center: [51.5, -0.1], intervals: [50, 100, 150, 200] },
 *       observerReticle: { latlng: [51.5, -0.1] },
 *       hudPanels: { modeName: 'ADS-B', getContactCount: () => 0 },
 *       scaleBar: true,
 *   });
 *   overlays.updateCount(42);
 */
const MapUtils = {

    /**
     * Initialise a Leaflet map with Settings-managed tile layer.
     * Adds a canvas fallback grid immediately, then upgrades to the
     * configured tile provider asynchronously without blocking.
     *
     * @param {string} containerId - DOM element id
     * @param {Object} [options]
     * @param {number[]} [options.center=[20,0]]
     * @param {number}   [options.zoom=4]
     * @param {number}   [options.minZoom=2]
     * @param {number}   [options.maxZoom=18]
     * @param {boolean}  [options.zoomControl=true]
     * @param {boolean}  [options.attributionControl=true]
     * @returns {L.Map|null}
     */
    init(containerId, options = {}) {
        const container = document.getElementById(containerId);
        if (!container) return null;
        // Guard against double init (e.g. back/forward cache restore)
        if (container._leaflet_id) return null;

        const map = L.map(containerId, {
            center:              options.center              || [20, 0],
            zoom:                options.zoom                ?? 4,
            minZoom:             options.minZoom             ?? 2,
            maxZoom:             options.maxZoom             ?? 18,
            zoomControl:         options.zoomControl         !== false,
            attributionControl:  options.attributionControl  !== false,
        });

        const fallback = this.createFallbackGridLayer().addTo(map);
        this._upgradeTiles(map, fallback);

        return map;
    },

    /**
     * Async: replace the fallback canvas grid with the Settings tile layer.
     * @private
     */
    async _upgradeTiles(map, fallback) {
        if (typeof Settings === 'undefined') return;
        try {
            await Settings.init();
            if (!map || map._removed) return;
            const layer = Settings.createTileLayer();
            let loaded = false;
            layer.once('load', () => {
                loaded = true;
                if (map.hasLayer(fallback)) map.removeLayer(fallback);
            });
            layer.on('tileerror', () => {
                if (!loaded) {
                    console.warn('MapUtils: tile error — keeping fallback grid');
                }
            });
            layer.addTo(map);
            Settings.registerMap(map);
        } catch (e) {
            console.warn('MapUtils: settings init failed, keeping fallback:', e);
        }
    },

    /**
     * Create a zero-network canvas fallback grid layer.
     * @returns {L.GridLayer}
     */
    createFallbackGridLayer() {
        const layer = L.gridLayer({
            tileSize: 256,
            updateWhenIdle: true,
            attribution: 'Local fallback grid',
        });
        layer.createTile = function (coords) {
            const tile = document.createElement('canvas');
            tile.width = 256;
            tile.height = 256;
            const ctx = tile.getContext('2d');

            ctx.fillStyle = '#07090e';
            ctx.fillRect(0, 0, 256, 256);

            // Major grid lines
            ctx.strokeStyle = 'rgba(74,163,255,0.12)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(0, 0); ctx.lineTo(256, 0);
            ctx.moveTo(0, 0); ctx.lineTo(0, 256);
            ctx.stroke();

            // Minor grid lines
            ctx.strokeStyle = 'rgba(74,163,255,0.06)';
            ctx.beginPath();
            ctx.moveTo(128, 0); ctx.lineTo(128, 256);
            ctx.moveTo(0, 128); ctx.lineTo(256, 128);
            ctx.stroke();

            ctx.fillStyle = 'rgba(74,163,255,0.25)';
            ctx.font = '10px "JetBrains Mono", monospace';
            ctx.fillText(`Z${coords.z} ${coords.x},${coords.y}`, 8, 18);
            return tile;
        };
        return layer;
    },

    /**
     * Add a graticule (lat/lon grid) toggle button control to any Leaflet map.
     *
     * @param {L.Map} map
     * @param {Object} [options]
     * @param {boolean} [options.defaultVisible=true]  Show grid on init.
     * @param {string}  [options.position='bottomleft'] Leaflet control position.
     * @returns {{ control: L.Control, show: Function, hide: Function }}
     */
    addGraticuleControl(map, options = {}) {
        const defaultVisible = options.defaultVisible !== false;
        const self = this;
        let graticuleLayer = null;
        let visible = false;
        let btnEl = null;
        let _onZoom = null;

        const _build = () => {
            if (graticuleLayer) map.removeLayer(graticuleLayer);
            graticuleLayer = self._buildGraticule(map);
            graticuleLayer.addTo(map);
        };
        const show = () => {
            visible = true;
            _build();
            _onZoom = _build;
            map.on('zoomend', _onZoom);
            if (btnEl) btnEl.classList.add('active');
        };
        const hide = () => {
            visible = false;
            if (_onZoom) { map.off('zoomend', _onZoom); _onZoom = null; }
            if (graticuleLayer) { map.removeLayer(graticuleLayer); graticuleLayer = null; }
            if (btnEl) btnEl.classList.remove('active');
        };

        const GraticuleControl = L.Control.extend({
            options: { position: options.position || 'bottomleft' },
            onAdd() {
                const btn = L.DomUtil.create('button', 'map-graticule-btn');
                btn.type = 'button';
                btn.title = 'Toggle coordinate grid';
                btn.setAttribute('aria-label', 'Toggle coordinate grid');
                btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                    <line x1="0" y1="4.67" x2="14" y2="4.67" stroke="currentColor" stroke-width="1"/>
                    <line x1="0" y1="9.33" x2="14" y2="9.33" stroke="currentColor" stroke-width="1"/>
                    <line x1="4.67" y1="0" x2="4.67" y2="14" stroke="currentColor" stroke-width="1"/>
                    <line x1="9.33" y1="0" x2="9.33" y2="14" stroke="currentColor" stroke-width="1"/>
                </svg>`;
                btnEl = btn;
                L.DomEvent.disableClickPropagation(btn);
                L.DomEvent.on(btn, 'click', () => { if (visible) hide(); else show(); });
                return btn;
            },
            onRemove() {
                hide();
                btnEl = null;
            },
        });

        const control = new GraticuleControl();
        control.addTo(map);
        if (defaultVisible) show();
        return { control, show, hide };
    },

    /**
     * Add tactical overlays to a map.
     *
     * @param {L.Map} map
     * @param {Object} [options]
     * @param {Object} [options.rangeRings]
     *   { center: [lat,lng], intervals: number[], unit: 'nm'|'km' }
     * @param {Object} [options.observerReticle]
     *   { latlng: [lat,lng] }
     * @param {Object} [options.hudPanels]
     *   { modeName: string, getContactCount: ()=>number, getSdrStatus: ()=>boolean }
     * @param {boolean} [options.graticule=true]  Pass false to start with grid hidden.
     * @param {boolean} [options.scaleBar]
     *
     * @returns {Object} handles
     *   { updateCount(n), updateStatus(online), showGraticule(), hideGraticule(),
     *     updateReticle(latlng), removeAll() }
     */
    addTacticalOverlays(map, options = {}) {
        const handles = {};
        const cleanupFns = [];

        // --- Scale bar ---
        if (options.scaleBar !== false) {
            const scale = L.control.scale({ imperial: true, metric: true, position: 'bottomright' });
            scale.addTo(map);
            cleanupFns.push(() => scale.remove());
        }

        // --- Range rings ---
        let rangeRingsLayer = null;
        if (options.rangeRings) {
            rangeRingsLayer = this._buildRangeRings(map, options.rangeRings);
        }
        handles.rangeRingsLayer = rangeRingsLayer;

        // --- Observer reticle ---
        let reticleMarker = null;
        if (options.observerReticle) {
            reticleMarker = this._buildReticle(options.observerReticle.latlng);
            reticleMarker.addTo(map);
            cleanupFns.push(() => map.removeLayer(reticleMarker));
        }
        handles.updateReticle = (latlng) => {
            if (reticleMarker) reticleMarker.setLatLng(latlng);
        };

        // --- HUD panels ---
        let hudHandles = { updateCount: () => {}, updateStatus: () => {} };
        if (options.hudPanels) {
            hudHandles = this._buildHudPanels(map, options.hudPanels);
            cleanupFns.push(() => hudHandles.remove());
        }
        handles.updateCount  = hudHandles.updateCount;
        handles.updateStatus = hudHandles.updateStatus;

        // --- Graticule toggle control (always added; defaultVisible via options.graticule) ---
        const grat = this.addGraticuleControl(map, {
            defaultVisible: options.graticule !== false,
        });
        handles.showGraticule = grat.show;
        handles.hideGraticule = grat.hide;
        cleanupFns.push(() => grat.control.remove());

        handles.removeAll = () => cleanupFns.forEach(fn => fn());

        // Auto-cleanup when Leaflet map is removed
        const autoCleanup = () => {
            cleanupFns.forEach(fn => fn());
            map.off('remove', autoCleanup);
        };
        map.on('remove', autoCleanup);
        const originalRemoveAll = handles.removeAll;
        handles.removeAll = () => {
            map.off('remove', autoCleanup);
            originalRemoveAll();
        };

        return handles;
    },

    /**
     * Build dashed range rings around a centre point.
     * @private
     */
    _buildRangeRings(map, opts) {
        const { center, intervals, unit = 'nm' } = opts;
        const metersPerUnit = unit === 'km' ? 1000 : 1852;
        const layer = L.layerGroup();

        intervals.forEach(dist => {
            const meters = dist * metersPerUnit;
            L.circle(center, {
                radius: meters,
                color: getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#4aa3ff',
                fillColor: 'transparent',
                fillOpacity: 0,
                weight: 1,
                opacity: 0.3,
                dashArray: '4 4',
                interactive: false,
            }).addTo(layer);

            // Label at accurate north point of ring (Leaflet handles earth curvature)
            const labelLat = L.circle(center, { radius: meters }).getBounds().getNorth();
            L.marker([labelLat, center[1]], {
                icon: L.divIcon({
                    className: 'map-range-label',
                    html: `<span>${Math.round(dist)} ${unit}</span>`,
                    iconSize: [50, 14],
                    iconAnchor: [25, 7],
                }),
                interactive: false,
            }).addTo(layer);
        });

        layer.addTo(map);
        return layer;
    },

    /**
     * Build a crosshair SVG marker.
     * @private
     */
    _buildReticle(latlng) {
        const icon = L.divIcon({
            className: 'map-reticle',
            html: `<svg width="28" height="28" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
                <circle cx="14" cy="14" r="4" style="stroke:var(--accent-cyan)" stroke-width="1.5"/>
                <line x1="14" y1="2"  x2="14" y2="9"  style="stroke:var(--accent-cyan)" stroke-width="1.5"/>
                <line x1="14" y1="19" x2="14" y2="26" style="stroke:var(--accent-cyan)" stroke-width="1.5"/>
                <line x1="2"  y1="14" x2="9"  y2="14" style="stroke:var(--accent-cyan)" stroke-width="1.5"/>
                <line x1="19" y1="14" x2="26" y2="14" style="stroke:var(--accent-cyan)" stroke-width="1.5"/>
            </svg>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
        });
        return L.marker(latlng, { icon, interactive: false, zIndexOffset: -100 });
    },

    /**
     * Build HUD corner panels and attach them to the map container.
     * Returns update handles.
     * @private
     */
    _buildHudPanels(map, opts) {
        const { modeName = '', getContactCount = () => 0, getSdrStatus = () => null } = opts;
        const container = map.getContainer();

        // Top-left: mode name + contact count
        const tl = document.createElement('div');
        tl.className = 'map-hud-panel map-hud-tl';
        tl.innerHTML = `
            <span class="map-hud-mode">${modeName}</span>
            <span class="map-hud-count">0</span>
        `;
        container.appendChild(tl);
        const countEl = tl.querySelector('.map-hud-count');

        // Top-right: UTC clock + SDR status dot
        const tr = document.createElement('div');
        tr.className = 'map-hud-panel map-hud-tr';
        tr.innerHTML = `
            <span class="map-hud-clock"></span>
            <span class="map-hud-dot"></span>
        `;
        container.appendChild(tr);
        const clockEl = tr.querySelector('.map-hud-clock');
        const dotEl   = tr.querySelector('.map-hud-dot');

        // Clock tick — shows the user's configured local timezone (default SAST)
        // alongside UTC, rather than UTC-only.
        const updateClock = () => {
            if (!document.body.contains(container)) return;
            const now = new Date();
            const utc = now.toISOString().substring(11, 19) + ' UTC';
            if (typeof InterceptTime !== 'undefined' && InterceptTime.getIANA()) {
                clockEl.textContent = InterceptTime.fullTime(now) + InterceptTime.tzSuffix() + '  ·  ' + utc;
            } else {
                clockEl.textContent = utc;
            }
        };
        updateClock();
        const clockInterval = setInterval(updateClock, 1000);

        return {
            updateCount(n) {
                countEl.textContent = n;
            },
            updateStatus(online) {
                dotEl.className = `map-hud-dot ${online === true ? 'online' : online === false ? 'offline' : ''}`;
            },
            remove() {
                clearInterval(clockInterval);
                tl.remove();
                tr.remove();
            },
        };
    },

    /**
     * Build a 10° lat/lon graticule as a Leaflet layer group.
     * Only draws lines visible in the current map bounds (+ 10% margin).
     * @private
     */
    _buildGraticule(map) {
        const layer = L.layerGroup();
        const bounds = map.getBounds().pad(0.1);
        const step = 10;
        const style = { color: 'rgba(74,163,255,0.12)', weight: 1, interactive: false };

        const latMin = Math.floor(bounds.getSouth() / step) * step;
        const latMax = Math.ceil(bounds.getNorth()  / step) * step;
        const lonMin = Math.floor(bounds.getWest()  / step) * step;
        const lonMax = Math.ceil(bounds.getEast()   / step) * step;

        for (let lat = latMin; lat <= latMax; lat += step) {
            if (lat < -90 || lat > 90) continue;
            L.polyline([[lat, lonMin], [lat, lonMax]], style).addTo(layer);
        }
        for (let lon = lonMin; lon <= lonMax; lon += step) {
            L.polyline([[-90, lon], [90, lon]], style).addTo(layer);
        }
        return layer;
    },

    /**
     * Return Leaflet popup options for dark-glass style.
     * @returns {Object}
     */
    glassPopupOptions() {
        return { className: 'map-glass-popup', maxWidth: 340 };
    },
};
