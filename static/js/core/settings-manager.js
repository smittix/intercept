/**
 * Settings Manager - Handles offline mode and application settings
 */

const Settings = {
    // Default settings
    defaults: {
        'offline.enabled': false,
        'offline.assets_source': 'local',
        'offline.fonts_source': 'local',
        'offline.tile_provider': 'esri_world',
        'offline.tile_server_url': '',
        'offline.stadia_key': '',
        'offline.carto_key': '',
    },

    // Tile provider configurations
    tileProviders: {
        openstreetmap: {
            url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            subdomains: 'abc'
        },
        cartodb_dark: {
            url: 'https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png',
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
            subdomains: 'abcd',
            mapTheme: 'cyber',
            options: {},
            supportsKey: true
        },
        cartodb_dark_cyan: {
            url: 'https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png',
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
            subdomains: 'abcd',
            mapTheme: 'cyber',
            options: {},
            supportsKey: true
        },
        cartodb_light: {
            url: 'https://cartodb-basemaps-{s}.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png',
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
            subdomains: 'abcd',
            supportsKey: true
        },
        esri_world: {
            url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attribution: 'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
            subdomains: null,
            // Imagery beyond zoom 18 is patchy, and a missing tile comes back as
            // a "Map data not yet available" image; upscale zoom 18 instead.
            options: { maxNativeZoom: 18 }
        },
        stadia_dark: {
            url: 'https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png',
            attribution: '&copy; <a href="https://stadiamaps.com/" target="_blank">Stadia Maps</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
            subdomains: null,
            requiresKey: true,
        },
        tactical: {
            url: 'https://tiles.stadiamaps.com/tiles/stamen_toner_background/{z}/{x}/{y}{r}.png',
            attribution: '&copy; <a href="https://stadiamaps.com/" target="_blank">Stadia Maps</a>',
            subdomains: null,
            requiresKey: true,
        },
    },

    // Registry of maps that can be updated
    _registeredMaps: [],

    // Current settings cache
    _cache: {},

    // Init guard to prevent concurrent fetch races across pages/modes
    _initialized: false,
    _initPromise: null,
    _themeObserver: null,
    _themeObserverStarted: false,
    _themeObserverRaf: null,

    /**
     * Check if a tile provider key is valid.
     * @param {string} provider
     * @returns {boolean}
     */
    _isKnownTileProvider(provider) {
        if (typeof provider !== 'string') return false;
        const key = provider.trim();
        return key === 'custom' || Object.prototype.hasOwnProperty.call(this.tileProviders, key);
    },

    /**
     * Normalize tile provider values from storage/UI.
     * @param {string} provider
     * @returns {string}
     */
    _normalizeTileProvider(provider) {
        if (typeof provider !== 'string') return this.defaults['offline.tile_provider'];
        const key = provider.trim();
        if (this._isKnownTileProvider(key)) return key;
        return this.defaults['offline.tile_provider'];
    },

    /**
     * Persist and retrieve preferred map theme behavior for dark Carto tiles.
     * Helps keep Cyber style enabled even if server-side tile provider drifts.
     */
    _getMapThemePreference() {
        if (typeof localStorage === 'undefined') return 'cyber';
        const pref = localStorage.getItem('intercept_map_theme_pref');
        if (pref === 'none' || pref === 'cyber') return pref;
        return 'cyber';
    },

    _setMapThemePreference(pref) {
        if (typeof localStorage === 'undefined') return;
        if (pref !== 'none' && pref !== 'cyber') return;
        localStorage.setItem('intercept_map_theme_pref', pref);
    },

    /**
     * Toggle root class used for hard global Leaflet theming.
     * @param {Object} [config]
     */
    _syncRootMapThemeClass(config) {
        if (typeof document === 'undefined' || !document.documentElement) return;
        const resolvedConfig = config || this.getTileConfig();
        const themeClass = this._getMapThemeClass(resolvedConfig);
        document.documentElement.classList.toggle('map-cyber-enabled', themeClass === 'map-theme-cyber');
    },

    /**
     * Prefer localStorage tile settings when available to avoid stale server values.
     */
    _applyLocalTileOverrides() {
        const stored = localStorage.getItem('intercept_settings');
        if (!stored) return;

        try {
            const local = JSON.parse(stored) || {};
            const localProvider = this._normalizeTileProvider(local['offline.tile_provider']);
            if (localProvider) {
                this._cache['offline.tile_provider'] = localProvider;
            }
            if (typeof local['offline.tile_server_url'] === 'string') {
                this._cache['offline.tile_server_url'] = local['offline.tile_server_url'];
            }
        } catch (e) {
            // Ignore malformed local settings and keep current cache.
        }
    },

    /**
     * Initialize settings - load from server/localStorage
     */
    async init(options = {}) {
        const force = Boolean(options && options.force);

        if (!force && this._initialized) {
            return this._cache;
        }

        if (!force && this._initPromise) {
            return this._initPromise;
        }

        this._initPromise = (async () => {
            try {
                const response = await fetch('/offline/settings');
                if (response.ok) {
                    const data = await response.json();
                    this._cache = { ...this.defaults, ...data.settings };
                } else {
                    // Fall back to localStorage
                    this._loadFromLocalStorage();
                }
            } catch (e) {
                console.warn('Failed to load settings from server, using localStorage:', e);
                this._loadFromLocalStorage();
            }

            this._applyLocalTileOverrides();
            this._cache['offline.tile_provider'] = this._normalizeTileProvider(this._cache['offline.tile_provider']);

            // If dark Carto was restored by stale server settings but user prefers Cyber,
            // keep the visible provider aligned with Cyber selection.
            if (this._cache['offline.tile_provider'] === 'cartodb_dark' && this._getMapThemePreference() === 'cyber') {
                this._cache['offline.tile_provider'] = 'cartodb_dark_cyan';
            }
            this._updateUI();

            // Re-apply map theme to already-registered maps in case init happened after map creation.
            const allMaps = this._collectMaps();
            if (allMaps.length > 0) {
                const config = this.getTileConfig();
                allMaps.forEach((map) => this._applyMapTheme(map, config));
            }
            const activeConfig = this.getTileConfig();
            this._syncRootMapThemeClass(activeConfig);
            this._applyThemeToAllContainers(activeConfig);
            this._ensureThemeObserver();

            this._initialized = true;
            return this._cache;
        })();

        try {
            return await this._initPromise;
        } finally {
            this._initPromise = null;
        }
    },

    /**
     * Load settings from localStorage
     */
    _loadFromLocalStorage() {
        const stored = localStorage.getItem('intercept_settings');
        if (stored) {
            try {
                this._cache = { ...this.defaults, ...JSON.parse(stored) };
            } catch (e) {
                this._cache = { ...this.defaults };
            }
        } else {
            this._cache = { ...this.defaults };
        }
    },

    /**
     * Save a setting to server and localStorage
     */
    async _save(key, value) {
        this._cache[key] = value;

        // Save to localStorage as backup (exclude sensitive keys)
        const SENSITIVE_KEYS = ['offline.stadia_key', 'offline.carto_key'];
        const toStore = Object.fromEntries(
            Object.entries(this._cache).filter(([k]) => !SENSITIVE_KEYS.includes(k))
        );
        localStorage.setItem('intercept_settings', JSON.stringify(toStore));

        // Save to server
        try {
            const response = await fetch('/offline/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key, value })
            });
            if (!response.ok) {
                throw new Error(`Save failed (${response.status})`);
            }
        } catch (e) {
            console.warn('Failed to save setting to server:', e);
        }
    },

    /**
     * Get a setting value
     */
    get(key) {
        return this._cache[key] ?? this.defaults[key];
    },

    /**
     * Toggle offline mode master switch
     */
    async toggleOfflineMode(enabled) {
        await this._save('offline.enabled', enabled);

        if (enabled) {
            // When enabling offline mode, also switch assets and fonts to local
            await this._save('offline.assets_source', 'local');
            await this._save('offline.fonts_source', 'local');
        }

        this._updateUI();
        this._showReloadPrompt();
    },

    /**
     * Set asset source (cdn or local)
     */
    async setAssetSource(source) {
        await this._save('offline.assets_source', source);
        this._showReloadPrompt();
    },

    /**
     * Set fonts source (cdn or local)
     */
    async setFontsSource(source) {
        await this._save('offline.fonts_source', source);
        this._showReloadPrompt();
    },

    /**
     * Set tile provider
     */
    async setTileProvider(provider) {
        provider = this._normalizeTileProvider(provider);

        if (provider === 'cartodb_dark_cyan') {
            this._setMapThemePreference('cyber');
        } else if (provider === 'cartodb_dark') {
            this._setMapThemePreference('none');
        } else {
            this._setMapThemePreference('none');
        }

        await this._save('offline.tile_provider', provider);

        // Show/hide custom URL input
        const customRow = document.getElementById('customTileUrlRow');
        if (customRow) {
            customRow.style.display = provider === 'custom' ? 'block' : 'none';
        }

        // Show/hide Stadia API key row
        const stadiaKeyRow = document.getElementById('stadiaKeyRow');
        if (stadiaKeyRow) {
            stadiaKeyRow.style.display =
                (provider === 'stadia_dark' || provider === 'tactical') ? 'block' : 'none';
        }

        // Show/hide CARTO API key row
        const cartoKeyRow = document.getElementById('cartoKeyRow');
        if (cartoKeyRow) {
            cartoKeyRow.style.display =
                (this.tileProviders[provider] && this.tileProviders[provider].supportsKey) ? 'block' : 'none';
        }

        // Update tiles immediately for all providers.
        this._updateMapTiles();
        const activeConfig = this.getTileConfig();
        this._syncRootMapThemeClass(activeConfig);
        this._applyThemeToAllContainers(activeConfig);
    },

    /**
     * Set custom tile server URL
     */
    async setCustomTileUrl(url) {
        await this._save('offline.tile_server_url', url);
        this._updateMapTiles();
    },

    /**
     * Save Stadia Maps API key and refresh tiles.
     * @param {string} key
     */
    async setStadiaKey(key) {
        await this._save('offline.stadia_key', (key || '').trim());
        this._updateMapTiles();
    },

    /**
     * Save CARTO API key and refresh tiles. CartoDB tiles work without a key
     * but CARTO now watermarks unauthenticated raster tile requests.
     * @param {string} key
     */
    async setCartoKey(key) {
        await this._save('offline.carto_key', (key || '').trim());
        this._updateMapTiles();
    },

    /**
     * Get current tile configuration
     */
    getTileConfig() {
        const provider = this._normalizeTileProvider(this.get('offline.tile_provider'));

        if (provider === 'custom') {
            const customUrl = this.get('offline.tile_server_url');
            return {
                url: customUrl || 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
                attribution: 'Custom Tile Server',
                subdomains: 'abc'
            };
        }

        const baseConfig = this.tileProviders[provider] || this.tileProviders.cartodb_dark;

        if (baseConfig.requiresKey) {
            const key = (this.get('offline.stadia_key') || '').trim();
            if (!key) {
                // No key — fall back to CartoDB dark so the map isn't broken
                return this.tileProviders.cartodb_dark;
            }
            return {
                ...baseConfig,
                url: baseConfig.url + '?api_key=' + encodeURIComponent(key),
            };
        }

        let resolvedConfig = baseConfig;
        if (baseConfig.supportsKey) {
            // CartoDB raster tiles work without a key but CARTO now watermarks
            // unauthenticated requests; a free key (carto.com/basemaps/apikey) removes it.
            const cartoKey = (this.get('offline.carto_key') || '').trim();
            if (cartoKey) {
                resolvedConfig = { ...baseConfig, url: baseConfig.url + '?key=' + encodeURIComponent(cartoKey) };
            }
        }

        // Robust fallback: keep Cyber theme when CartoDB dark is active and Cyber preferred.
        if (provider === 'cartodb_dark' && this._getMapThemePreference() === 'cyber') {
            return { ...resolvedConfig, mapTheme: 'cyber' };
        }

        return resolvedConfig;
    },

    /**
     * Resolve map theme class from tile config.
     * @param {Object} config
     * @returns {string|null}
     */
    _getMapThemeClass(config) {
        if (!config || !config.mapTheme) return null;
        if (config.mapTheme === 'cyber') return 'map-theme-cyber';
        return null;
    },

    /**
     * Apply or clear map theme styles for a Leaflet container.
     * @param {HTMLElement} container
     * @param {Object} [config]
     */
    _applyThemeToContainer(container, config) {
        if (!container || !container.classList) return;
        const tilePane = container.querySelector('.leaflet-tile-pane');

        container.querySelectorAll('.intercept-map-theme-overlay').forEach((el) => el.remove());

        if (tilePane && tilePane.style) {
            tilePane.style.filter = '';
            tilePane.style.opacity = '';
            tilePane.style.willChange = '';
        }
        if (container.style) {
            container.style.background = '';
        }

        container.classList.remove('map-theme-cyber');

        const resolvedConfig = config || this.getTileConfig();
        const themeClass = this._getMapThemeClass(resolvedConfig);
        if (!themeClass) return;

        container.classList.add(themeClass);

        if (themeClass === 'map-theme-cyber') {
            if (container.style) {
                container.style.background = '#020813';
            }
            if (tilePane && tilePane.style) {
                tilePane.style.filter = 'sepia(0.74) hue-rotate(176deg) saturate(1.72) brightness(1.05) contrast(1.08)';
                tilePane.style.opacity = '1';
                tilePane.style.willChange = 'filter';
            }
        }

        // Map overlays are rendered via CSS pseudo elements on
        // `html.map-*-enabled .leaflet-container` for consistent stacking.
    },

    /**
     * Apply/remove map theme class on a Leaflet map container.
     * @param {L.Map} map
     * @param {Object} [config]
     */
    _applyMapTheme(map, config) {
        if (!map || typeof map.getContainer !== 'function') return;
        const container = map.getContainer();
        this._applyThemeToContainer(container, config);
    },

    /**
     * Apply current map theme to all rendered Leaflet containers.
     * Covers maps that were not explicitly registered with Settings.
     * @param {Object} [config]
     */
    _applyThemeToAllContainers(config) {
        if (typeof document === 'undefined') return;
        const containers = document.querySelectorAll('.leaflet-container');
        if (!containers.length) return;

        const resolvedConfig = config || this.getTileConfig();
        this._syncRootMapThemeClass(resolvedConfig);
        containers.forEach((container) => this._applyThemeToContainer(container, resolvedConfig));
    },

    /**
     * Watch the DOM for new Leaflet maps and apply current theme automatically.
     */
    _ensureThemeObserver() {
        if (this._themeObserverStarted || typeof MutationObserver === 'undefined') return;
        if (typeof document === 'undefined' || !document.body) return;

        const scheduleApply = () => {
            if (this._themeObserverRaf && typeof cancelAnimationFrame === 'function') {
                cancelAnimationFrame(this._themeObserverRaf);
            }
            if (typeof requestAnimationFrame === 'function') {
                this._themeObserverRaf = requestAnimationFrame(() => {
                    this._themeObserverRaf = null;
                    this._applyThemeToAllContainers(this.getTileConfig());
                });
            } else {
                this._applyThemeToAllContainers(this.getTileConfig());
            }
        };

        this._themeObserver = new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                if (!mutation.addedNodes || mutation.addedNodes.length === 0) continue;
                for (const node of mutation.addedNodes) {
                    if (!(node instanceof Element)) continue;
                    if (node.classList.contains('leaflet-container') || node.querySelector('.leaflet-container')) {
                        scheduleApply();
                        return;
                    }
                }
            }
        });

        this._themeObserver.observe(document.body, {
            childList: true,
            subtree: true
        });

        this._themeObserverStarted = true;
    },

    /**
     * Collect all known map instances.
     * @returns {L.Map[]}
     */
    _collectMaps() {
        const windowMaps = [
            window.map,
            window.leafletMap,
            window.aprsMap,
            window.radarMap,
            window.vesselMap,
            window.groundMap,
            window.groundTrackMap,
            window.meshMap,
            window.issMap
        ].filter(m => m && typeof m.eachLayer === 'function');

        return [...new Set([...this._registeredMaps, ...windowMaps])];
    },

    /**
     * Keep map theme stable if map internals or layers are refreshed.
     * @param {L.Map} map - Leaflet map instance
     */
    _attachMapThemeHooks(map) {
        if (!map || typeof map.on !== 'function' || map._interceptThemeHookBound) return;

        const reapplyTheme = () => this._applyMapTheme(map);
        const hookEvents = ['layeradd', 'layerremove', 'zoomend', 'resize', 'load'];
        hookEvents.forEach((eventName) => map.on(eventName, reapplyTheme));

        map._interceptThemeHookBound = true;
        map._interceptThemeHookHandler = reapplyTheme;
    },

    /**
     * Register a map to receive tile updates when settings change
     * @param {L.Map} map - Leaflet map instance
     */
    registerMap(map) {
        if (map && typeof map.eachLayer === 'function' && !this._registeredMaps.includes(map)) {
            this._registeredMaps.push(map);
        }
        this._ensureThemeObserver();
        this._attachMapThemeHooks(map);
        this._applyMapTheme(map);
        this._applyThemeToAllContainers(this.getTileConfig());

        // Some maps create tile DOM asynchronously; re-apply after first paint.
        if (typeof window !== 'undefined' && typeof window.setTimeout === 'function') {
            window.setTimeout(() => {
                this._applyMapTheme(map);
                this._applyThemeToAllContainers(this.getTileConfig());
            }, 120);
        }
    },

    /**
     * Unregister a map
     * @param {L.Map} map - Leaflet map instance
     */
    unregisterMap(map) {
        const idx = this._registeredMaps.indexOf(map);
        if (idx > -1) {
            this._registeredMaps.splice(idx, 1);
        }

        if (map && map._interceptThemeHookBound && typeof map.off === 'function') {
            const handler = map._interceptThemeHookHandler;
            ['layeradd', 'layerremove', 'zoomend', 'resize', 'load'].forEach((eventName) => {
                map.off(eventName, handler);
            });
            delete map._interceptThemeHookBound;
            delete map._interceptThemeHookHandler;
        }
    },

    /**
     * Create a tile layer using current settings
     * @returns {L.TileLayer} Configured tile layer
     */
    createTileLayer() {
        const config = this.getTileConfig();
        const options = {
            attribution: config.attribution,
            maxZoom: 19,
            ...(config.options || {})
        };
        if (config.subdomains) {
            options.subdomains = config.subdomains;
        }
        return L.tileLayer(config.url, options);
    },

    /**
     * Check if local assets are available
     */
    async checkAssets() {
        const assets = {
            leaflet: [
                '/static/vendor/leaflet/leaflet.js',
                '/static/vendor/leaflet/leaflet.css'
            ],
            chartjs: [
                '/static/vendor/chartjs/chart.umd.min.js'
            ],
            inter: [
                '/static/vendor/fonts/Inter-Regular.woff2'
            ],
            jetbrains: [
                '/static/vendor/fonts/JetBrainsMono-Regular.woff2'
            ]
        };

        const results = {};

        for (const [name, urls] of Object.entries(assets)) {
            const statusEl = document.getElementById(`status${name.charAt(0).toUpperCase() + name.slice(1)}`);
            if (statusEl) {
                statusEl.textContent = 'Checking...';
                statusEl.className = 'asset-badge checking';
            }

            let available = true;
            for (const url of urls) {
                try {
                    const response = await fetch(url, { method: 'HEAD' });
                    if (!response.ok) {
                        available = false;
                        break;
                    }
                } catch (e) {
                    available = false;
                    break;
                }
            }

            results[name] = available;

            if (statusEl) {
                statusEl.textContent = available ? 'Available' : 'Missing';
                statusEl.className = `asset-badge ${available ? 'available' : 'missing'}`;
            }
        }

        return results;
    },

    /**
     * Update UI elements to reflect current settings
     */
    _updateUI() {
        // Offline mode toggle
        const offlineEnabled = document.getElementById('offlineEnabled');
        if (offlineEnabled) {
            offlineEnabled.checked = this.get('offline.enabled');
        }

        // Assets source
        const assetsSource = document.getElementById('assetsSource');
        if (assetsSource) {
            assetsSource.value = this.get('offline.assets_source');
        }

        // Fonts source
        const fontsSource = document.getElementById('fontsSource');
        if (fontsSource) {
            fontsSource.value = this.get('offline.fonts_source');
        }

        // Tile provider
        const tileProvider = document.getElementById('tileProvider');
        if (tileProvider) {
            tileProvider.value = this.get('offline.tile_provider');
        }

        // Custom tile URL
        const customTileUrl = document.getElementById('customTileUrl');
        if (customTileUrl) {
            customTileUrl.value = this.get('offline.tile_server_url') || '';
        }

        // Show/hide custom URL row
        const customRow = document.getElementById('customTileUrlRow');
        if (customRow) {
            customRow.style.display = this.get('offline.tile_provider') === 'custom' ? 'block' : 'none';
        }

        // Stadia key input
        const stadiaKeyInput = document.getElementById('stadiaKeyInput');
        if (stadiaKeyInput) {
            stadiaKeyInput.value = this.get('offline.stadia_key') || '';
        }
        const stadiaKeyRow = document.getElementById('stadiaKeyRow');
        if (stadiaKeyRow) {
            const currentProvider = this.get('offline.tile_provider');
            stadiaKeyRow.style.display =
                (currentProvider === 'stadia_dark' || currentProvider === 'tactical') ? 'block' : 'none';
        }

        // CARTO API key input
        const cartoKeyInput = document.getElementById('cartoKeyInput');
        if (cartoKeyInput) {
            cartoKeyInput.value = this.get('offline.carto_key') || '';
        }
        const cartoKeyRow = document.getElementById('cartoKeyRow');
        if (cartoKeyRow) {
            const currentProvider = this.get('offline.tile_provider');
            cartoKeyRow.style.display =
                (this.tileProviders[currentProvider] && this.tileProviders[currentProvider].supportsKey) ? 'block' : 'none';
        }

        // Theme select
        const themeSelect = document.getElementById('themeSelect');
        if (themeSelect) {
            themeSelect.value = localStorage.getItem('intercept-theme') || 'dark';
        }

        const uiTierSelect = document.getElementById('uiTierSelect');
        if (uiTierSelect) {
            uiTierSelect.value = localStorage.getItem('intercept-ui-tier') || 'enhanced';
        }
    },

    /**
     * Update map tiles on all known maps
     */
    _updateMapTiles() {
        const allMaps = this._collectMaps();
        if (allMaps.length === 0) return;

        const config = this.getTileConfig();
        this._syncRootMapThemeClass(config);

        allMaps.forEach(map => {
            // Remove existing tile layers
            map.eachLayer(layer => {
                if (layer instanceof L.TileLayer) {
                    map.removeLayer(layer);
                }
            });

            // Add new tile layer
            const options = {
                attribution: config.attribution,
                maxZoom: 19,
                ...(config.options || {})
            };
            if (config.subdomains) {
                options.subdomains = config.subdomains;
            }

            L.tileLayer(config.url, options).addTo(map);
            this._applyMapTheme(map, config);
        });

        this._applyThemeToAllContainers(config);
    },

    /**
     * Show reload prompt
     */
    _showReloadPrompt() {
        // Create or update reload prompt
        let prompt = document.getElementById('settingsReloadPrompt');
        if (!prompt) {
            prompt = document.createElement('div');
            prompt.id = 'settingsReloadPrompt';
            prompt.style.cssText = `
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: var(--bg-dark, #0a0a0f);
                border: 1px solid var(--accent-cyan, #00d4ff);
                border-radius: 8px;
                padding: 12px 16px;
                display: flex;
                align-items: center;
                gap: 12px;
                z-index: 10001;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
            `;
            prompt.innerHTML = `
                <span style="color: var(--text-primary, #e0e0e0); font-size: 13px;">
                    Reload to apply changes
                </span>
                <button onclick="location.reload()" style="
                    background: var(--accent-cyan, #00d4ff);
                    border: none;
                    color: #000;
                    padding: 6px 12px;
                    border-radius: 4px;
                    font-size: 12px;
                    font-weight: 500;
                    cursor: pointer;
                ">Reload</button>
                <button onclick="this.parentElement.remove()" style="
                    background: none;
                    border: none;
                    color: var(--text-muted, #666);
                    font-size: 18px;
                    cursor: pointer;
                    padding: 0 4px;
                ">&times;</button>
            `;
            document.body.appendChild(prompt);
        }
    }
};

// Settings modal functions
let lastSettingsFocusEl = null;

function showSettings() {
    const modal = document.getElementById('settingsModal');
    if (modal) {
        lastSettingsFocusEl = document.activeElement;
        modal.classList.add('active');
        modal.setAttribute('aria-hidden', 'false');
        const content = modal.querySelector('.settings-content');
        if (content) content.focus();
        Settings.init().then(() => {
            Settings.checkAssets();
        });
    }
}

function hideSettings() {
    const modal = document.getElementById('settingsModal');
    if (modal) {
        modal.classList.remove('active');
        modal.setAttribute('aria-hidden', 'true');
        if (lastSettingsFocusEl && typeof lastSettingsFocusEl.focus === 'function') {
            lastSettingsFocusEl.focus();
        }
    }
}

function switchSettingsTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.settings-tab').forEach(tab => {
        const isActive = tab.dataset.tab === tabName;
        tab.classList.toggle('active', isActive);
        tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });

    // Update sections
    document.querySelectorAll('.settings-section').forEach(section => {
        const isActive = section.id === `settings-${tabName}`;
        section.classList.toggle('active', isActive);
        section.hidden = !isActive;
        section.setAttribute('role', 'tabpanel');
    });

    // Load tools/dependencies when that tab is selected
    if (tabName === 'tools') {
        loadSettingsTools();
    }
}

/**
 * Load tool dependencies into settings modal
 */
function loadSettingsTools() {
    const content = document.getElementById('settingsToolsContent');
    if (!content) return;

    content.innerHTML = '<div style="text-align: center; padding: 30px; color: var(--text-dim);">Loading dependencies...</div>';

    fetch('/dependencies')
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') {
                content.innerHTML = '<div style="color: var(--accent-red);">Error loading dependencies</div>';
                return;
            }

            let html = '';
            let totalMissing = 0;

            for (const [modeKey, mode] of Object.entries(data.modes)) {
                const statusColor = mode.ready ? 'var(--accent-green)' : 'var(--accent-red)';
                const statusIcon = mode.ready ? '✓' : '✗';

                html += `
                    <div style="background: var(--bg-tertiary); border-radius: 6px; padding: 12px; margin-bottom: 10px; border-left: 3px solid ${statusColor};">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                            <span style="font-weight: 600; color: var(--accent-cyan); font-size: 13px;">${mode.name}</span>
                            <span style="color: ${statusColor}; font-size: 11px; font-weight: bold;">${statusIcon} ${mode.ready ? 'Ready' : 'Missing'}</span>
                        </div>
                        <div style="display: grid; gap: 6px;">
                `;

                for (const [toolName, tool] of Object.entries(mode.tools)) {
                    const installed = tool.installed;
                    const dotColor = installed ? 'var(--accent-green)' : 'var(--accent-red)';
                    const requiredBadge = tool.required ? '<span style="background: var(--accent-orange); color: #000; padding: 1px 4px; border-radius: 3px; font-size: 9px; margin-left: 4px;">REQ</span>' : '';

                    if (!installed) totalMissing++;

                    let installCmd = '';
                    if (tool.install) {
                        if (tool.install.pip) {
                            installCmd = tool.install.pip;
                        } else if (data.pkg_manager && tool.install[data.pkg_manager]) {
                            installCmd = tool.install[data.pkg_manager];
                        } else if (tool.install.manual) {
                            installCmd = tool.install.manual;
                        }
                    }

                    html += `
                        <div style="display: flex; align-items: center; gap: 8px; padding: 6px 8px; background: var(--bg-secondary); border-radius: 4px; font-size: 11px;">
                            <span style="color: ${dotColor}; font-size: 12px;">●</span>
                            <div style="flex: 1; min-width: 0;">
                                <span style="font-weight: 500;">${toolName}${requiredBadge}</span>
                                <div style="font-size: 10px; color: var(--text-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${tool.description}</div>
                                ${!installed && installCmd ? `
                                    <code style="display: block; margin-top: 4px; font-size: 10px; background: var(--bg-tertiary); padding: 3px 6px; border-radius: 3px; overflow-wrap: anywhere; user-select: all;" title="Click to select">${installCmd}</code>
                                ` : ''}
                            </div>
                            <span style="font-size: 10px; color: ${dotColor}; font-weight: bold; min-width: 45px; text-align: right;">${installed ? 'OK' : 'MISSING'}</span>
                        </div>
                    `;
                }

                html += '</div></div>';
            }

            // Summary at top
            const summaryHtml = `
                <div style="background: ${totalMissing > 0 ? 'rgba(255, 100, 0, 0.1)' : 'rgba(0, 255, 100, 0.1)'}; border: 1px solid ${totalMissing > 0 ? 'var(--accent-orange)' : 'var(--accent-green)'}; border-radius: 6px; padding: 10px 12px; margin-bottom: 12px;">
                    <div style="font-size: 13px; font-weight: bold; color: ${totalMissing > 0 ? 'var(--accent-orange)' : 'var(--accent-green)'};">
                        ${totalMissing > 0 ? '⚠️ ' + totalMissing + ' tool(s) not found' : '✓ All tools installed'}
                    </div>
                    <div style="font-size: 11px; color: var(--text-dim); margin-top: 3px;">
                        OS: ${data.os} | Package Manager: ${data.pkg_manager}
                    </div>
                </div>
            `;

            content.innerHTML = summaryHtml + html;
        })
        .catch(err => {
            content.innerHTML = '<div style="color: var(--accent-red);">Error loading dependencies: ' + err.message + '</div>';
        });
}

// =============================================================================
// Location Settings Functions
// =============================================================================

/**
 * Load and display current observer location
 */
function loadObserverLocation() {
    let lat = localStorage.getItem('observerLat');
    let lon = localStorage.getItem('observerLon');
    if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
        const shared = ObserverLocation.getShared();
        lat = shared.lat.toString();
        lon = shared.lon.toString();
    }

    const hasLat = lat !== undefined && lat !== null && lat !== '';
    const hasLon = lon !== undefined && lon !== null && lon !== '';

    const latInput = document.getElementById('observerLatInput');
    const lonInput = document.getElementById('observerLonInput');
    const currentLatDisplay = document.getElementById('currentLatDisplay');
    const currentLonDisplay = document.getElementById('currentLonDisplay');

    if (latInput && hasLat) latInput.value = lat;
    if (lonInput && hasLon) lonInput.value = lon;

    if (currentLatDisplay) {
        currentLatDisplay.textContent = hasLat ? parseFloat(lat).toFixed(4) + '°' : 'Not set';
    }
    if (currentLonDisplay) {
        currentLonDisplay.textContent = hasLon ? parseFloat(lon).toFixed(4) + '°' : 'Not set';
    }

    // Sync dashboard-specific location keys for backward compatibility
    if (hasLat && hasLon) {
        const locationObj = JSON.stringify({ lat: parseFloat(lat), lon: parseFloat(lon) });
        if (!localStorage.getItem('observerLocation')) {
            localStorage.setItem('observerLocation', locationObj);
        }
        if (!localStorage.getItem('ais_observerLocation')) {
            localStorage.setItem('ais_observerLocation', locationObj);
        }
    }
}

/**
 * Detect location using gpsd (USB GPS) or browser geolocation as fallback
 */
function detectLocationGPS(btn) {
    const latInput = document.getElementById('observerLatInput');
    const lonInput = document.getElementById('observerLonInput');

    // Show loading state with visual feedback
    const originalText = btn.innerHTML;
    btn.innerHTML = '<span class="detecting-spinner"></span> Detecting...';
    btn.disabled = true;
    btn.style.opacity = '0.7';

    // Helper to restore button state
    function restoreButton() {
        btn.innerHTML = originalText;
        btn.disabled = false;
        btn.style.opacity = '';
    }

    // Helper to set location values
    function setLocation(lat, lon, source) {
        if (latInput) latInput.value = parseFloat(lat).toFixed(4);
        if (lonInput) lonInput.value = parseFloat(lon).toFixed(4);
        restoreButton();
        if (typeof showNotification === 'function') {
            showNotification('Location', `Coordinates set from ${source}`);
        }
    }

    // First, try gpsd (USB GPS device)
    fetch('/gps/position')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'ok' && data.position && data.position.latitude != null) {
                // Got valid position from gpsd
                setLocation(data.position.latitude, data.position.longitude, 'GPS device');
            } else if (data.status === 'waiting') {
                // gpsd connected but no fix yet - show message and try browser
                if (typeof showNotification === 'function') {
                    showNotification('GPS', 'GPS device connected but no fix yet. Trying browser location...');
                }
                useBrowserGeolocation();
            } else {
                // gpsd not available, try browser geolocation
                useBrowserGeolocation();
            }
        })
        .catch(() => {
            // gpsd request failed, try browser geolocation
            useBrowserGeolocation();
        });

    // Fallback to browser geolocation
    function useBrowserGeolocation() {
        if (!navigator.geolocation) {
            restoreButton();
            if (typeof showNotification === 'function') {
                showNotification('Location', 'No GPS available (gpsd not running, browser GPS unavailable)');
            } else {
                alert('No GPS available');
            }
            return;
        }

        navigator.geolocation.getCurrentPosition(
            (pos) => {
                setLocation(pos.coords.latitude, pos.coords.longitude, 'browser');
            },
            (err) => {
                restoreButton();
                let message = 'Failed to get location';
                if (err.code === 1) message = 'Location access denied';
                else if (err.code === 2) message = 'Location unavailable';
                else if (err.code === 3) message = 'Location request timed out';

                if (typeof showNotification === 'function') {
                    showNotification('Location', message);
                } else {
                    alert(message);
                }
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );
    }
}

/**
 * Save observer location to localStorage and persist defaults to .env
 */
async function saveObserverLocation() {
    const latInput = document.getElementById('observerLatInput');
    const lonInput = document.getElementById('observerLonInput');

    const lat = parseFloat(latInput?.value);
    const lon = parseFloat(lonInput?.value);

    if (isNaN(lat) || lat < -90 || lat > 90) {
        if (typeof showNotification === 'function') {
            showNotification('Location', 'Invalid latitude (must be -90 to 90)');
        } else {
            alert('Invalid latitude (must be -90 to 90)');
        }
        return;
    }

    if (isNaN(lon) || lon < -180 || lon > 180) {
        if (typeof showNotification === 'function') {
            showNotification('Location', 'Invalid longitude (must be -180 to 180)');
        } else {
            alert('Invalid longitude (must be -180 to 180)');
        }
        return;
    }

    if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
        ObserverLocation.setShared({ lat, lon });
    } else {
        localStorage.setItem('observerLat', lat.toString());
        localStorage.setItem('observerLon', lon.toString());
    }

    // Also update dashboard-specific location keys for ADS-B and AIS
    const locationObj = JSON.stringify({ lat: lat, lon: lon });
    localStorage.setItem('observerLocation', locationObj);      // ADS-B dashboard
    localStorage.setItem('ais_observerLocation', locationObj);  // AIS dashboard

    // Update display
    const currentLatDisplay = document.getElementById('currentLatDisplay');
    const currentLonDisplay = document.getElementById('currentLonDisplay');
    if (currentLatDisplay) currentLatDisplay.textContent = lat.toFixed(4) + '°';
    if (currentLonDisplay) currentLonDisplay.textContent = lon.toFixed(4) + '°';

    if (window.observerLocation) {
        window.observerLocation.lat = lat;
        window.observerLocation.lon = lon;
    }

    let notificationMessage = 'Observer location saved';

    try {
        const response = await fetch('/settings/observer-location', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ lat, lon }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status === 'error') {
            throw new Error(data.message || 'Failed to save observer location to .env');
        }
        window.INTERCEPT_DEFAULT_LAT = lat;
        window.INTERCEPT_DEFAULT_LON = lon;
        notificationMessage = 'Observer location saved to settings and .env';
    } catch (error) {
        notificationMessage = `Observer location saved for this browser, but .env update failed: ${error.message}`;
    }

    // Refresh SSTV ISS schedule if available
    if (typeof SSTV !== 'undefined' && typeof SSTV.loadIssSchedule === 'function') {
        SSTV.loadIssSchedule();
    }

    // Update APRS user location if function is available
    if (typeof updateAprsUserLocation === 'function') {
        updateAprsUserLocation({ latitude: lat, longitude: lon });
    }

    // Notify all listeners (any mode can subscribe)
    window.dispatchEvent(new CustomEvent('observer-location-changed', { detail: { lat, lon } }));

    if (typeof showNotification === 'function') {
        showNotification('Location', notificationMessage);
    }
}

// =============================================================================
// Update Settings Functions
// =============================================================================

/**
 * Check for updates manually from settings panel
 */
async function checkForUpdatesManual() {
    const content = document.getElementById('updateStatusContent');
    if (!content) return;

    if (typeof Updater === 'undefined') {
        content.innerHTML = `<div style="color: var(--text-dim); padding: 10px;">Update checking is unavailable. If you use a content blocker, try allowing <code>updater.js</code> to load.</div>`;
        return;
    }

    content.innerHTML = '<div style="text-align: center; padding: 20px; color: var(--text-dim);">Checking for updates...</div>';

    try {
        const data = await Updater.checkNow();
        renderUpdateStatus(data);
    } catch (error) {
        content.innerHTML = `<div style="color: var(--accent-red); padding: 10px;">Error checking for updates: ${error.message}</div>`;
    }
}

/**
 * Load update status when tab is opened
 */
async function loadUpdateStatus() {
    const content = document.getElementById('updateStatusContent');
    if (!content) return;

    if (typeof Updater === 'undefined') {
        content.innerHTML = `<div style="color: var(--text-dim); padding: 10px;">Update checking is unavailable. If you use a content blocker, try allowing <code>updater.js</code> to load.</div>`;
        return;
    }

    try {
        const data = await Updater.getStatus();
        renderUpdateStatus(data);
    } catch (error) {
        content.innerHTML = `<div style="color: var(--accent-red); padding: 10px;">Error loading update status: ${error.message}</div>`;
    }
}

/**
 * Render update status in settings panel
 */
function renderUpdateStatus(data) {
    const content = document.getElementById('updateStatusContent');
    if (!content) return;

    if (!data.success) {
        content.innerHTML = `<div style="color: var(--accent-red); padding: 10px;">Error: ${data.error || 'Unknown error'}</div>`;
        return;
    }

    if (data.disabled) {
        content.innerHTML = `
            <div style="padding: 15px; background: var(--bg-tertiary); border-radius: 6px; text-align: center;">
                <div style="color: var(--text-dim); font-size: 13px;">Update checking is disabled</div>
            </div>
        `;
        return;
    }

    if (!data.checked) {
        content.innerHTML = `
            <div style="padding: 15px; background: var(--bg-tertiary); border-radius: 6px; text-align: center;">
                <div style="color: var(--text-dim); font-size: 13px;">No update check performed yet</div>
                <div style="font-size: 11px; color: var(--text-dim); margin-top: 5px;">Click "Check Now" to check for updates</div>
            </div>
        `;
        return;
    }

    const statusColor = data.update_available ? 'var(--accent-green)' : 'var(--text-dim)';
    const statusText = data.update_available ? 'Update Available' : 'Up to Date';
    const statusIcon = data.update_available
        ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';

    let html = `
        <div style="padding: 15px; background: var(--bg-tertiary); border-radius: 6px; border-left: 3px solid ${statusColor};">
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px;">
                <span style="color: ${statusColor};">${statusIcon}</span>
                <span style="font-weight: 600; color: ${statusColor};">${statusText}</span>
            </div>
            <div style="display: grid; gap: 8px; font-size: 12px;">
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: var(--text-dim);">Current Version</span>
                    <span style="font-family: 'Roboto Condensed', 'Arial Narrow', sans-serif; color: var(--text-primary);">v${data.current_version}</span>
                </div>
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: var(--text-dim);">Latest Version</span>
                    <span style="font-family: 'Roboto Condensed', 'Arial Narrow', sans-serif; color: ${data.update_available ? 'var(--accent-green)' : 'var(--text-primary)'};">v${data.latest_version}</span>
                </div>
                ${data.last_check ? `
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: var(--text-dim);">Last Checked</span>
                    <span style="color: var(--text-secondary);">${formatLastCheck(data.last_check)}</span>
                </div>
                ` : ''}
            </div>
            ${data.update_available ? `
            <button onclick="Updater.showUpdateModal()" style="
                margin-top: 12px;
                width: 100%;
                padding: 8px;
                background: var(--accent-green);
                color: #000;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: 500;
                cursor: pointer;
            ">View Update Details</button>
            ` : ''}
        </div>
    `;

    content.innerHTML = html;
}

/**
 * Format last check timestamp
 */
function formatLastCheck(isoString) {
    try {
        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);

        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins} min ago`;
        if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
        return date.toLocaleDateString();
    } catch (e) {
        return isoString;
    }
}

/**
 * Toggle update checking
 */
async function toggleUpdateCheck(enabled) {
    // This would require adding a setting to disable update checks
    // For now, just store in localStorage
    localStorage.setItem('intercept_update_check_enabled', enabled ? 'true' : 'false');

    if (!enabled && typeof Updater !== 'undefined') {
        Updater.destroy();
    } else if (enabled && typeof Updater !== 'undefined') {
        Updater.init();
    }
}

// Extend switchSettingsTab to load update status
const _originalSwitchSettingsTab = typeof switchSettingsTab !== 'undefined' ? switchSettingsTab : null;

function switchSettingsTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.settings-tab').forEach(tab => {
        const isActive = tab.dataset.tab === tabName;
        tab.classList.toggle('active', isActive);
        tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });

    // Update sections
    document.querySelectorAll('.settings-section').forEach(section => {
        const isActive = section.id === `settings-${tabName}`;
        section.classList.toggle('active', isActive);
        section.hidden = !isActive;
        section.setAttribute('role', 'tabpanel');
    });

    // Load content based on tab
    if (tabName === 'tools') {
        loadSettingsTools();
    } else if (tabName === 'updates') {
        loadUpdateStatus();
    } else if (tabName === 'location') {
        loadObserverLocation();
    } else if (tabName === 'sdr') {
        loadSdrDeviceSettings();
    } else if (tabName === 'alerts') {
        loadVoiceAlertConfig();
        if (typeof AlertCenter !== 'undefined') {
            AlertCenter.init();
        }
    } else if (tabName === 'recording') {
        if (typeof RecordingUI !== 'undefined') {
            RecordingUI.init();
        }
    } else if (tabName === 'apikeys') {
        loadApiKeyStatus();
    }
}

/**
 * Load voice alert configuration into Settings > Alerts tab
 */
function loadVoiceAlertConfig() {
    if (typeof VoiceAlerts === 'undefined') return;
    const cfg = VoiceAlerts.getConfig();

    const pager   = document.getElementById('voiceCfgPager');
    const tscm    = document.getElementById('voiceCfgTscm');
    const tracker = document.getElementById('voiceCfgTracker');
    const military = document.getElementById('voiceCfgAdsbMilitary');
    const squawk  = document.getElementById('voiceCfgSquawk');
    const rate    = document.getElementById('voiceCfgRate');
    const pitch   = document.getElementById('voiceCfgPitch');
    const rateVal = document.getElementById('voiceCfgRateVal');
    const pitchVal = document.getElementById('voiceCfgPitchVal');

    if (pager)    pager.checked    = cfg.streams.pager !== false;
    if (tscm)     tscm.checked     = cfg.streams.tscm !== false;
    if (tracker)  tracker.checked   = cfg.streams.bluetooth !== false;
    if (military) military.checked  = cfg.streams.adsb_military !== false;
    if (squawk)   squawk.checked    = cfg.streams.squawks !== false;
    if (rate)     rate.value        = cfg.rate;
    if (pitch)    pitch.value       = cfg.pitch;
    if (rateVal)  rateVal.textContent  = cfg.rate;
    if (pitchVal) pitchVal.textContent = cfg.pitch;

    // Populate voice dropdown
    VoiceAlerts.getAvailableVoices().then(function (voices) {
        var sel = document.getElementById('voiceCfgVoice');
        if (!sel) return;
        sel.innerHTML = '<option value="">Default</option>' +
            voices.filter(function (v) { return v.lang.startsWith('en'); }).map(function (v) {
                return '<option value="' + v.name + '"' + (v.name === cfg.voiceName ? ' selected' : '') + '>' + v.name + '</option>';
            }).join('');
    });
}

function saveVoiceAlertConfig() {
    if (typeof VoiceAlerts === 'undefined') return;
    VoiceAlerts.setConfig({
        rate:      parseFloat(document.getElementById('voiceCfgRate')?.value) || 1.1,
        pitch:     parseFloat(document.getElementById('voiceCfgPitch')?.value) || 0.9,
        voiceName: document.getElementById('voiceCfgVoice')?.value || '',
        streams: {
            pager:         !!document.getElementById('voiceCfgPager')?.checked,
            tscm:          !!document.getElementById('voiceCfgTscm')?.checked,
            bluetooth:     !!document.getElementById('voiceCfgTracker')?.checked,
            adsb_military: !!document.getElementById('voiceCfgAdsbMilitary')?.checked,
            squawks:       !!document.getElementById('voiceCfgSquawk')?.checked,
        },
    });
}

function testVoiceAlert() {
    if (typeof VoiceAlerts !== 'undefined') VoiceAlerts.testVoice();
}

/**
 * Load API key status into the API Keys settings tab
 */
function loadApiKeyStatus() {
    const badge = document.getElementById('apiKeyStatusBadge');
    const desc = document.getElementById('apiKeyStatusDesc');
    const usage = document.getElementById('apiKeyUsageCount');
    const bar = document.getElementById('apiKeyUsageBar');

    if (!badge) return;

    badge.textContent = 'Not available';
        badge.className = 'asset-badge missing';
        desc.textContent = 'GSM feature removed';
}

/**
 * Save API key from the settings input
 */
function saveApiKey() {
    const input = document.getElementById('apiKeyInput');
    const result = document.getElementById('apiKeySaveResult');
    if (!input || !result) return;

    const key = input.value.trim();
    if (!key) {
        result.style.display = 'block';
        result.style.color = 'var(--accent-red)';
        result.textContent = 'Please enter an API key.';
        return;
    }

    result.style.display = 'block';
    result.style.color = 'var(--text-dim)';
    result.textContent = 'Saving...';

    result.style.color = 'var(--accent-red)';
    result.textContent = 'GSM feature has been removed.';
}

/**
 * Toggle API key input visibility
 */
function toggleApiKeyVisibility() {
    const input = document.getElementById('apiKeyInput');
    if (!input) return;
    input.type = input.type === 'password' ? 'text' : 'password';
}

/**
 * Set theme preference from the Display settings tab
 */
function setThemePreference(value) {
    document.documentElement.setAttribute('data-theme', value);
    localStorage.setItem('intercept-theme', value);

    const btn = document.getElementById('themeToggle');
    if (btn) {
        btn.textContent = value === 'light' ? '🌙' : '☀️';
    }

    fetch('/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: value })
    }).catch(() => {});
}

/**
 * Load per-device SDR configuration into the SDR settings tab.
 * Built with DOM APIs throughout: names are user-supplied.
 */
function loadSdrDeviceSettings() {
    const container = document.getElementById('settingsSdrDevices');
    if (!container) return;

    const message = (text) => {
        const div = document.createElement('div');
        div.style.cssText = 'text-align: center; padding: 30px; color: var(--text-dim);';
        div.textContent = text;
        container.replaceChildren(div);
    };

    fetch('/devices')
        .then(r => r.json())
        .then(devices => {
            if (!Array.isArray(devices) || devices.length === 0) {
                message('No SDR devices detected');
                return;
            }
            container.replaceChildren(...devices.map(renderSdrDeviceRow));
        })
        .catch(() => message('Could not load devices'));
}

function renderSdrDeviceRow(device) {
    const config = device.config || {};
    const row = document.createElement('div');
    row.className = 'settings-row';
    row.style.cssText = 'flex-direction: column; align-items: stretch; gap: 8px;';

    const title = document.createElement('div');
    title.className = 'settings-label-text';
    const serial = device.serial && !['N/A', 'Unknown'].includes(device.serial) ? ` (SN: ${device.serial})` : '';
    title.textContent = `${(device.sdr_type || 'sdr').toUpperCase()} #${device.index}: ${device.hardware_name || device.name}${serial}`;
    row.appendChild(title);

    if (!device.keyed_by_serial) {
        const note = document.createElement('div');
        note.className = 'settings-label-desc';
        note.style.color = 'var(--accent-orange, #f0a030)';
        note.textContent = 'No unique serial number: these settings belong to USB position ' +
            `#${device.index}, not to this dongle, and follow whatever is plugged in there. ` +
            'Give the dongle its own serial with rtl_eeprom -s to make them follow it.';
        row.appendChild(note);
    }

    const field = (label, input) => {
        const wrap = document.createElement('label');
        wrap.style.cssText = 'display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 12px;';
        const span = document.createElement('span');
        span.textContent = label;
        wrap.append(span, input);
        return wrap;
    };
    const input = (type, value, placeholder, attrs = {}) => {
        const el = document.createElement('input');
        el.type = type;
        el.className = 'settings-input';
        el.value = value ?? '';
        el.placeholder = placeholder;
        Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
        return el;
    };

    const name = input('text', config.name, device.hardware_name || 'Display name', { maxlength: 48 });
    const ppm = input('number', config.ppm, 'Not set (0)', { step: 1, min: -1000, max: 1000 });
    const gain = input('number', config.gain, 'Not set (mode default)', { step: 0.1, min: 0, max: 102 });
    const biasT = document.createElement('select');
    biasT.className = 'settings-input';
    [['', 'Not set (off)'], ['true', 'On'], ['false', 'Off']].forEach(([value, label]) => {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = label;
        biasT.appendChild(opt);
    });
    biasT.value = config.bias_t === undefined ? '' : String(config.bias_t);

    row.append(
        field('Name', name),
        field('PPM correction', ppm),
        field('Default gain (dB)', gain),
        field('Bias-T default', biasT),
    );

    const actions = document.createElement('div');
    actions.style.cssText = 'display: flex; align-items: center; gap: 10px;';
    const status = document.createElement('span');
    status.style.cssText = 'font-size: 11px; color: var(--text-dim);';
    const save = document.createElement('button');
    save.type = 'button';
    save.className = 'check-assets-btn';
    save.textContent = 'Save';
    save.addEventListener('click', () => {
        const body = {
            name: name.value,
            ppm: ppm.value,
            gain: gain.value,
            bias_t: biasT.value === '' ? null : biasT.value === 'true',
        };
        save.disabled = true;
        fetch(`/devices/config/${encodeURIComponent(device.config_key)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
            .then(r => r.json().then(data => ({ ok: r.ok, data })))
            .then(({ ok, data }) => {
                status.style.color = ok ? 'var(--accent-green)' : 'var(--accent-red)';
                status.textContent = ok ? 'Saved' : (data.message || 'Save failed');
                if (ok && typeof refreshDevices === 'function') refreshDevices();
            })
            .catch(() => {
                status.style.color = 'var(--accent-red)';
                status.textContent = 'Save failed';
            })
            .finally(() => { save.disabled = false; });
    });
    actions.append(save, status);
    row.appendChild(actions);
    return row;
}

/**
 * Set UI tier preference from the Display settings tab
 */
function setUiTierPreference(tier) {
    document.documentElement.setAttribute('data-ui-tier', tier);
    localStorage.setItem('intercept-ui-tier', tier);
}

if (!window._settingsEscapeHandlerBound) {
    window._settingsEscapeHandlerBound = true;
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        const modal = document.getElementById('settingsModal');
        if (modal && modal.classList.contains('active')) {
            hideSettings();
        }
    });
}
