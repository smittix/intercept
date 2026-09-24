/**
 * Bluetooth Mode Controller
 * Uses the new unified Bluetooth API at /api/bluetooth/
 */

const BluetoothMode = (function() {
    'use strict';

    const LOCATE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="10" r="3"/><path d="M12 21.7C17.3 17 20 13 20 10a8 8 0 1 0-16 0c0 3 2.7 7 8 11.7z"/></svg>';

    // State
    let isScanning = false;
    let eventSource = null;
    let agentPollTimer = null;  // Polling fallback for agent mode
    let devices = new Map();
    let baselineSet = false;
    let baselineCount = 0;

    // DOM elements (cached)
    let startBtn, stopBtn, messageContainer, deviceContainer;
    let adapterSelect, scanModeSelect, transportSelect, durationInput, minRssiInput;
    let baselineStatusEl, capabilityStatusEl;

    // Stats tracking
    let deviceStats = {
        strong: 0,
        medium: 0,
        weak: 0,
        trackers: []
    };

    // Zone counts for proximity display
    let zoneCounts = { immediate: 0, near: 0, far: 0 };

    // New visualization components
    let radarInitialized = false;
    let radarPaused = false;

    // Device list filter
    let currentDeviceFilter = 'all';
    let sortBy = 'rssi';
    let sortListenersBound = false;
    let currentSearchTerm = '';
    let visibleDeviceCount = 0;
    let pendingDeviceFlush = false;
    let selectedDeviceNeedsRefresh = false;
    let filterListenersBound = false;
    let listListenersBound = false;
    const pendingDeviceIds = new Set();

    // Agent support
    let showAllAgentsMode = false;
    let lastAgentId = null;

    /**
     * Get API base URL, routing through agent proxy if agent is selected.
     */
    function getApiBase() {
        if (typeof currentAgent !== 'undefined' && currentAgent !== 'local') {
            return `/controller/agents/${currentAgent}`;
        }
        return '';
    }

    /**
     * Get current agent name for tagging data.
     */
    function getCurrentAgentName() {
        if (typeof currentAgent === 'undefined' || currentAgent === 'local') {
            return 'Local';
        }
        if (typeof agents !== 'undefined') {
            const agent = agents.find(a => a.id == currentAgent);
            return agent ? agent.name : `Agent ${currentAgent}`;
        }
        return `Agent ${currentAgent}`;
    }

    /**
     * Check for agent mode conflicts before starting scan.
     */
    async function checkAgentConflicts() {
        if (typeof currentAgent === 'undefined' || currentAgent === 'local') {
            return true;
        }
        if (typeof checkAgentModeConflict === 'function') {
            return await checkAgentModeConflict('bluetooth');
        }
        return true;
    }

    /**
     * Initialize the Bluetooth mode
     */
    function init() {
        console.log('[BT] Initializing BluetoothMode');

        // Cache DOM elements
        startBtn = document.getElementById('startBtBtn');
        stopBtn = document.getElementById('stopBtBtn');
        messageContainer = document.getElementById('btMessageContainer');
        deviceContainer = document.getElementById('btDeviceListContent');
        adapterSelect = document.getElementById('btAdapterSelect');
        scanModeSelect = document.getElementById('btScanMode');
        transportSelect = document.getElementById('btTransport');
        durationInput = document.getElementById('btScanDuration');
        minRssiInput = document.getElementById('btMinRssi');
        baselineStatusEl = document.getElementById('btBaselineStatus');
        capabilityStatusEl = document.getElementById('btCapabilityStatus');

        // Check capabilities on load
        checkCapabilities();

        // Check scan status (in case page was reloaded during scan)
        checkScanStatus();

        // Initialize proximity visualization
        initProximityRadar();

        // Initialize legacy heatmap (zone counts)
        initHeatmap();

        // Initialize device list filters
        initDeviceFilters();
        initSortControls();
        initListInteractions();

        // Set initial panel states
        updateVisualizationPanels();
    }

    /**
     * Initialize device list filter buttons
     */
    function initDeviceFilters() {
        if (filterListenersBound) return;
        const filterContainer = document.getElementById('btFilterGroup');
        if (filterContainer) {
            filterContainer.addEventListener('click', (e) => {
                const btn = e.target.closest('.bt-filter-btn');
                if (!btn) return;

                const filter = btn.dataset.filter;
                if (!filter) return;

                // Update active state
                filterContainer.querySelectorAll('.bt-filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');

                // Apply filter
                currentDeviceFilter = filter;
                applyDeviceFilter();
            });
        }

        const searchInput = document.getElementById('btDeviceSearch');
        if (searchInput) {
            searchInput.addEventListener('input', () => {
                currentSearchTerm = searchInput.value.trim().toLowerCase();
                applyDeviceFilter();
            });
        }
        filterListenersBound = true;
    }

    function initSortControls() {
        if (sortListenersBound) return;
        sortListenersBound = true;
        const sortGroup = document.getElementById('btSortGroup');
        if (!sortGroup) return;
        sortGroup.addEventListener('click', (e) => {
            const btn = e.target.closest('.bt-sort-btn');
            if (!btn) return;
            const sort = btn.dataset.sort;
            if (!sort) return;
            sortBy = sort;
            sortGroup.querySelectorAll('.bt-sort-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderAllDevices();
        });
    }

    function initListInteractions() {
        if (listListenersBound) return;
        if (deviceContainer) {
            deviceContainer.addEventListener('click', (event) => {
                const row = event.target.closest('.bt-device-row[data-bt-device-id]');
                if (!row) return;
                // The row's Locate button goes straight to locating, without selecting
                if (event.target.closest('.bt-row-locate')) {
                    event.stopPropagation();
                    locateById(row.dataset.btDeviceId);
                    return;
                }
                selectDevice(row.dataset.btDeviceId);
            });
        }

        const trackerList = document.getElementById('btTrackerList');
        if (trackerList) {
            trackerList.addEventListener('click', (event) => {
                const row = event.target.closest('.bt-tracker-item[data-device-id]');
                if (!row) return;
                selectDevice(row.dataset.deviceId);
            });
        }
        listListenersBound = true;
    }

    /**
     * Apply current filter to device list
     */
    function applyDeviceFilter() {
        if (!deviceContainer) return;

        const cards = deviceContainer.querySelectorAll('[data-bt-device-id]');
        let visibleCount = 0;
        cards.forEach(card => {
            const isNew = card.dataset.isNew === 'true';
            const hasName = card.dataset.hasName === 'true';
            const rssi = parseInt(card.dataset.rssi) || -100;
            const isTracker = card.dataset.isTracker === 'true';
            const searchHaystack = (card.dataset.search || '').toLowerCase();

            let matchesFilter = true;
            switch (currentDeviceFilter) {
                case 'new':
                    matchesFilter = isNew;
                    break;
                case 'named':
                    matchesFilter = hasName;
                    break;
                case 'strong':
                    matchesFilter = rssi >= -70;
                    break;
                case 'trackers':
                    matchesFilter = isTracker;
                    break;
                case 'all':
                default:
                    matchesFilter = true;
            }

            const matchesSearch = !currentSearchTerm || searchHaystack.includes(currentSearchTerm);
            const visible = matchesFilter && matchesSearch;
            card.style.display = visible ? '' : 'none';
            if (visible) visibleCount++;
        });

        visibleDeviceCount = visibleCount;

        let stateEl = deviceContainer.querySelector('.bt-device-filter-state');
        if (visibleCount === 0 && devices.size > 0) {
            if (!stateEl) {
                stateEl = document.createElement('div');
                stateEl.className = 'bt-device-filter-state app-collection-state is-empty';
                deviceContainer.appendChild(stateEl);
            }
            stateEl.textContent = 'No devices match current filters';
        } else if (stateEl) {
            stateEl.remove();
        }

        // Update visible count
        updateFilteredCount();
    }

    /**
     * Update the device count display based on visible devices
     */
    function updateFilteredCount() {
        const countEl = document.getElementById('btDeviceListCount');
        if (!countEl || !deviceContainer) return;

        const hasFilter = currentDeviceFilter !== 'all' || currentSearchTerm.length > 0;
        countEl.textContent = hasFilter ? `${visibleDeviceCount}/${devices.size}` : devices.size;
    }

    /**
     * Initialize the new proximity radar component
     */
    function initProximityRadar() {
        const radarContainer = document.getElementById('btProximityRadar');
        if (!radarContainer) return;

        if (typeof ProximityRadar !== 'undefined') {
            ProximityRadar.init('btProximityRadar', {
                onDeviceClick: (deviceKey) => {
                    // Find device by key and show modal
                    const device = Array.from(devices.values()).find(d => d.device_key === deviceKey);
                    if (device) {
                        selectDevice(device.device_id);
                    }
                }
            });
            radarInitialized = true;

            // Setup radar controls
            setupRadarControls();
        }
    }

    /**
     * Setup radar control button handlers
     */
    function setupRadarControls() {
        // Filter buttons
        document.querySelectorAll('#btRadarControls button[data-filter]').forEach(btn => {
            btn.addEventListener('click', () => {
                const filter = btn.getAttribute('data-filter');
                if (typeof ProximityRadar !== 'undefined') {
                    ProximityRadar.setFilter(filter);

                    // Update button states
                    document.querySelectorAll('#btRadarControls button[data-filter]').forEach(b => {
                        b.classList.remove('active');
                    });
                    if (ProximityRadar.getFilter() === filter) {
                        btn.classList.add('active');
                    }
                }
            });
        });

        // Pause button
        const pauseBtn = document.getElementById('btRadarPauseBtn');
        if (pauseBtn) {
            pauseBtn.addEventListener('click', () => {
                radarPaused = !radarPaused;
                if (typeof ProximityRadar !== 'undefined') {
                    ProximityRadar.setPaused(radarPaused);
                }
                pauseBtn.textContent = radarPaused ? 'Resume' : 'Pause';
                pauseBtn.classList.toggle('active', radarPaused);
            });
        }
    }

    /**
     * Update the proximity radar with current devices
     */
    function updateRadar() {
        if (!radarInitialized || typeof ProximityRadar === 'undefined') return;

        // Convert devices map to array for radar
        const deviceList = Array.from(devices.values()).map(d => ({
            device_key: d.device_key || d.device_id,
            device_id: d.device_id,
            name: d.name,
            address: d.address,
            rssi_current: d.rssi_current,
            rssi_ema: d.rssi_ema,
            estimated_distance_m: d.estimated_distance_m,
            proximity_band: d.proximity_band || 'unknown',
            distance_confidence: d.distance_confidence || 0.5,
            is_new: d.is_new || !d.in_baseline,
            is_randomized_mac: d.is_randomized_mac,
            in_baseline: d.in_baseline,
            heuristic_flags: d.heuristic_flags || [],
            age_seconds: d.age_seconds || 0,
        }));

        ProximityRadar.updateDevices(deviceList);

        // Update zone counts from radar
        const counts = ProximityRadar.getZoneCounts();
        updateProximityZoneCounts(counts);
    }

    /**
     * Update proximity zone counts display (new system)
     */
    function updateProximityZoneCounts(counts) {
        const immediateEl = document.getElementById('btZoneImmediate');
        const nearEl = document.getElementById('btZoneNear');
        const farEl = document.getElementById('btZoneFar');

        if (immediateEl) immediateEl.textContent = counts.immediate || 0;
        if (nearEl) nearEl.textContent = counts.near || 0;
        if (farEl) farEl.textContent = counts.far || 0;
    }

    /**
     * Initialize proximity zones display
     */
    function initHeatmap() {
        updateProximityZones();
    }

    /**
     * Update proximity zone counts (simple HTML, no canvas)
     */
    function updateProximityZones() {
        zoneCounts = { immediate: 0, near: 0, far: 0 };

        devices.forEach(device => {
            const rssi = device.rssi_current;
            if (rssi == null) return;

            if (rssi >= -50) zoneCounts.immediate++;
            else if (rssi >= -70) zoneCounts.near++;
            else zoneCounts.far++;
        });

        updateProximityZoneCounts(zoneCounts);
    }

    // Currently selected device
    let selectedDeviceId = null;

    /**
     * Show device detail panel
     */
    function showDeviceDetail(deviceId) {
        const device = devices.get(deviceId);
        if (!device) return;

        selectedDeviceId = deviceId;

        const placeholder = document.getElementById('btDetailPlaceholder');
        const content = document.getElementById('btDetailContent');
        if (!placeholder || !content) return;

        const rssi = device.rssi_current;
        const rssiColor = getRssiColor(rssi);
        const flags = device.heuristic_flags || [];
        const protocol = device.protocol || 'ble';

        // Update panel elements
        document.getElementById('btDetailName').textContent = device.name || formatDeviceId(device.address);
        document.getElementById('btDetailAddress').textContent = isUuidAddress(device)
            ? 'CB: ' + device.address
            : device.address;

        // RSSI
        const rssiEl = document.getElementById('btDetailRssi');
        rssiEl.textContent = rssi != null ? rssi : '--';
        rssiEl.style.color = rssiColor;

        // Badges
        const badgesEl = document.getElementById('btDetailBadges');
        let badgesHtml = `<span class="bt-detail-badge ${protocol}">${protocol.toUpperCase()}</span>`;
        badgesHtml += `<span class="bt-detail-badge ${device.in_baseline ? 'baseline' : 'new'}">${device.in_baseline ? '✓ KNOWN' : '● NEW'}</span>`;
        if (device.seen_before) {
            badgesHtml += `<span class="bt-detail-badge flag">SEEN BEFORE</span>`;
        }

        // Tracker badge
        if (device.is_tracker) {
            const conf = device.tracker_confidence || 'low';
            const confClass = conf === 'high' ? 'tracker-high' : conf === 'medium' ? 'tracker-medium' : 'tracker-low';
            const typeLabel = device.tracker_name || device.tracker_type || 'TRACKER';
            badgesHtml += `<span class="bt-detail-badge ${confClass}">${escapeHtml(typeLabel)}</span>`;
        }

        flags.forEach(f => {
            badgesHtml += `<span class="bt-detail-badge flag">${f.replace(/_/g, ' ').toUpperCase()}</span>`;
        });
        badgesEl.innerHTML = badgesHtml;

        // Tracker analysis section
        const trackerSection = document.getElementById('btDetailTrackerAnalysis');
        if (trackerSection) {
            if (device.is_tracker) {
                const confidence = device.tracker_confidence || 'low';
                const confScore = device.tracker_confidence_score || 0;
                const riskScore = device.risk_score || 0;
                const evidence = device.tracker_evidence || [];
                const riskFactors = device.risk_factors || [];

                let trackerHtml = '<div class="bt-tracker-analysis">';
                trackerHtml += '<div class="bt-analysis-header">Tracker Detection Analysis</div>';

                // Confidence
                const confColor = confidence === 'high' ? '#ef4444' : confidence === 'medium' ? '#f97316' : '#eab308';
                trackerHtml += '<div class="bt-analysis-row"><span class="bt-analysis-label">Confidence:</span><span style="color:' + confColor + ';font-weight:600;">' + confidence.toUpperCase() + ' (' + Math.round(confScore * 100) + '%)</span></div>';

                // Evidence
                if (evidence.length > 0) {
                    trackerHtml += '<div class="bt-analysis-section"><div class="bt-analysis-label">Evidence:</div><ul class="bt-evidence-list">';
                    evidence.forEach(e => {
                        trackerHtml += '<li>' + escapeHtml(e) + '</li>';
                    });
                    trackerHtml += '</ul></div>';
                }

                // Risk analysis
                if (riskScore >= 0.1 || riskFactors.length > 0) {
                    const riskColor = riskScore >= 0.5 ? '#ef4444' : riskScore >= 0.3 ? '#f97316' : '#888';
                    trackerHtml += '<div class="bt-analysis-row"><span class="bt-analysis-label">Risk Score:</span><span style="color:' + riskColor + ';font-weight:600;">' + Math.round(riskScore * 100) + '%</span></div>';
                    if (riskFactors.length > 0) {
                        trackerHtml += '<div class="bt-analysis-section"><div class="bt-analysis-label">Risk Factors:</div><ul class="bt-evidence-list">';
                        riskFactors.forEach(f => {
                            trackerHtml += '<li>' + escapeHtml(f) + '</li>';
                        });
                        trackerHtml += '</ul></div>';
                    }
                }

                trackerHtml += '<div class="bt-analysis-warning">Note: Detection is heuristic-based. Results indicate patterns consistent with tracking devices but cannot prove intent.</div>';
                trackerHtml += '</div>';

                trackerSection.style.display = 'block';
                trackerSection.innerHTML = trackerHtml;
            } else {
                trackerSection.style.display = 'none';
                trackerSection.innerHTML = '';
            }
        }

        // Stats grid
        document.getElementById('btDetailMfr').textContent = device.manufacturer_name || '--';
        document.getElementById('btDetailMfrId').textContent = device.manufacturer_id != null
            ? '0x' + device.manufacturer_id.toString(16).toUpperCase().padStart(4, '0')
            : '--';
        document.getElementById('btDetailAddrType').textContent = device.address_type || '--';
        document.getElementById('btDetailSeen').textContent = (device.seen_count || 0) + '×';
        document.getElementById('btDetailRange').textContent = device.range_band || '--';

        // Min/Max combined
        const minMax = [];
        if (device.rssi_min != null) minMax.push(device.rssi_min);
        if (device.rssi_max != null) minMax.push(device.rssi_max);
        document.getElementById('btDetailRssiRange').textContent = minMax.length === 2
            ? minMax[0] + '/' + minMax[1]
            : '--';

        document.getElementById('btDetailFirstSeen').textContent = device.first_seen
            ? new Date(device.first_seen).toLocaleTimeString()
            : '--';
        document.getElementById('btDetailLastSeen').textContent = device.last_seen
            ? new Date(device.last_seen).toLocaleTimeString()
            : '--';

        // New stat cells
        document.getElementById('btDetailTxPower').textContent = device.tx_power != null
            ? device.tx_power + ' dBm' : '--';
        document.getElementById('btDetailSeenRate').textContent = device.seen_rate != null
            ? device.seen_rate.toFixed(1) + '/min' : '--';

        // Stability from variance
        const stabilityEl = document.getElementById('btDetailStability');
        if (device.rssi_variance != null) {
            let stabLabel, stabColor;
            if (device.rssi_variance <= 5) { stabLabel = 'Stable'; stabColor = '#22c55e'; }
            else if (device.rssi_variance <= 25) { stabLabel = 'Moderate'; stabColor = '#eab308'; }
            else { stabLabel = 'Unstable'; stabColor = '#ef4444'; }
            stabilityEl.textContent = stabLabel;
            stabilityEl.style.color = stabColor;
        } else {
            stabilityEl.textContent = '--';
            stabilityEl.style.color = '';
        }

        // Distance with confidence
        const distEl = document.getElementById('btDetailDistance');
        if (device.estimated_distance_m != null) {
            const confPct = Math.round((device.distance_confidence || 0) * 100);
            distEl.textContent = device.estimated_distance_m.toFixed(1) + 'm ±' + confPct + '%';
        } else {
            distEl.textContent = '--';
        }

        // Appearance badge
        if (device.appearance_name) {
            badgesHtml += '<span class="bt-detail-badge flag">' + escapeHtml(device.appearance_name) + '</span>';
            badgesEl.innerHTML = badgesHtml;
        }

        // MAC cluster indicator
        const macClusterEl = document.getElementById('btDetailMacCluster');
        if (macClusterEl) {
            if (device.mac_cluster_count > 1) {
                macClusterEl.textContent = device.mac_cluster_count + ' MACs';
                macClusterEl.style.display = '';
            } else {
                macClusterEl.style.display = 'none';
            }
        }

        // Service data inspector
        const inspectorEl = document.getElementById('btDetailServiceInspector');
        const inspectorContent = document.getElementById('btInspectorContent');
        if (inspectorEl && inspectorContent) {
            const hasData = device.manufacturer_bytes || device.appearance != null ||
                (device.service_data && Object.keys(device.service_data).length > 0);
            if (hasData) {
                inspectorEl.style.display = '';
                let inspHtml = '';
                if (device.appearance != null) {
                    const name = device.appearance_name || '';
                    inspHtml += '<div class="bt-inspector-row"><span class="bt-inspector-label">Appearance</span><span class="bt-inspector-value">0x' + device.appearance.toString(16).toUpperCase().padStart(4, '0') + (name ? ' (' + escapeHtml(name) + ')' : '') + '</span></div>';
                }
                if (device.manufacturer_bytes) {
                    inspHtml += '<div class="bt-inspector-row"><span class="bt-inspector-label">Mfr Data</span><span class="bt-inspector-value">' + escapeHtml(device.manufacturer_bytes) + '</span></div>';
                }
                if (device.service_data) {
                    Object.entries(device.service_data).forEach(([uuid, hex]) => {
                        inspHtml += '<div class="bt-inspector-row"><span class="bt-inspector-label">' + escapeHtml(uuid) + '</span><span class="bt-inspector-value">' + escapeHtml(hex) + '</span></div>';
                    });
                }
                inspectorContent.innerHTML = inspHtml;
            } else {
                inspectorEl.style.display = 'none';
            }
        }

        updateWatchlistButton(device);

        // IRK
        const irkContainer = document.getElementById('btDetailIrk');
        if (irkContainer) {
            if (device.has_irk) {
                irkContainer.style.display = 'block';
                const irkVal = document.getElementById('btDetailIrkValue');
                if (irkVal) {
                    const label = device.irk_source_name
                        ? device.irk_source_name + ' — ' + device.irk_hex
                        : device.irk_hex;
                    irkVal.textContent = label;
                }
            } else {
                irkContainer.style.display = 'none';
            }
        }

        // Services
        const servicesContainer = document.getElementById('btDetailServices');
        const servicesList = document.getElementById('btDetailServicesList');
        if (device.service_uuids && device.service_uuids.length > 0) {
            servicesContainer.style.display = 'block';
            servicesList.textContent = device.service_uuids.join(', ');
        } else {
            servicesContainer.style.display = 'none';
        }

        // Show content, hide placeholder
        placeholder.style.display = 'none';
        content.style.display = 'block';

        // Highlight selected device in list
        highlightSelectedDevice(deviceId);
    }

    /**
     * Update watchlist button state
     */
    function updateWatchlistButton(device) {
        const btn = document.getElementById('btDetailWatchBtn');
        if (!btn) return;
        if (typeof AlertCenter === 'undefined') {
            btn.style.display = 'none';
            return;
        }
        btn.style.display = '';
        const watchlisted = AlertCenter.isWatchlisted(device.address);
        btn.textContent = watchlisted ? 'Watching' : 'Watchlist';
        btn.classList.toggle('active', watchlisted);
    }

    /**
     * Clear device selection
     */
    function clearSelection() {
        selectedDeviceId = null;

        const placeholder = document.getElementById('btDetailPlaceholder');
        const content = document.getElementById('btDetailContent');
        if (placeholder) placeholder.style.display = 'flex';
        if (content) content.style.display = 'none';

        // Remove highlight from device list
        if (deviceContainer) {
            deviceContainer.querySelectorAll('.bt-device-row.selected').forEach(el => {
                el.classList.remove('selected');
            });
        }

        // Clear radar highlight
        if (typeof ProximityRadar !== 'undefined') {
            ProximityRadar.clearHighlight();
        }
    }

    /**
     * Highlight selected device in the list
     */
    function highlightSelectedDevice(deviceId) {
        if (!deviceContainer) return;

        // Remove existing highlights
        deviceContainer.querySelectorAll('.bt-device-row.selected').forEach(el => {
            el.classList.remove('selected');
        });

        // Add highlight to selected device
        const escapedId = CSS.escape(deviceId);
        const card = deviceContainer.querySelector(`[data-bt-device-id="${escapedId}"]`);
        if (card) {
            card.classList.add('selected');
        }

        // Also highlight on the radar
        const device = devices.get(deviceId);
        if (device && typeof ProximityRadar !== 'undefined') {
            ProximityRadar.highlightDevice(device.device_key || device.device_id);
        }
    }

    /**
     * Copy selected device address to clipboard
     */
    function copyAddress() {
        if (!selectedDeviceId) return;
        const device = devices.get(selectedDeviceId);
        if (!device) return;

        navigator.clipboard.writeText(device.address).then(() => {
            const btn = document.getElementById('btDetailCopyBtn');
            if (btn) {
                const originalText = btn.textContent;
                btn.textContent = 'Copied!';
                btn.style.background = '#22c55e';
                setTimeout(() => {
                    btn.textContent = originalText;
                    btn.style.background = '';
                }, 1500);
            }
        });
    }

    /**
     * Toggle Bluetooth watchlist for selected device
     */
    function toggleWatchlist() {
        if (!selectedDeviceId) return;
        const device = devices.get(selectedDeviceId);
        if (!device || typeof AlertCenter === 'undefined') return;

        if (AlertCenter.isWatchlisted(device.address)) {
            AlertCenter.removeBluetoothWatchlist(device.address);
            showInfo('Removed from watchlist');
        } else {
            AlertCenter.addBluetoothWatchlist(device.address, device.name || device.address);
            showInfo('Added to watchlist');
        }

        setTimeout(() => updateWatchlistButton(device), 200);
    }

    /**
     * Select a device - opens modal with details
     */
    function selectDevice(deviceId) {
        showDeviceDetail(deviceId);
    }

    /**
     * Format device ID for display (when no name available)
     */
    function formatDeviceId(address) {
        if (!address) return 'Unknown Device';
        const parts = address.split(':');
        if (parts.length === 6) {
            return parts[0] + ':' + parts[1] + ':...:' + parts[4] + ':' + parts[5];
        }
        // CoreBluetooth UUID format (8-4-4-4-12)
        if (/^[0-9A-F]{8}-[0-9A-F]{4}-/i.test(address)) {
            return address.substring(0, 8) + '...';
        }
        return address;
    }

    function isUuidAddress(device) {
        return device.address_type === 'uuid';
    }

    function formatAddress(device) {
        if (!device || !device.address) return '--';
        if (isUuidAddress(device)) {
            return device.address.substring(0, 8) + '-...' + device.address.slice(-4);
        }
        return device.address;
    }

    /**
     * Check system capabilities
     */
    async function checkCapabilities() {
        try {
            const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
            let data;

            if (isAgentMode) {
                // Fetch capabilities from agent via controller proxy
                const response = await fetch(`/controller/agents/${currentAgent}?refresh=true`);
                const agentData = await response.json();

                if (agentData.agent && agentData.agent.capabilities) {
                    const agentCaps = agentData.agent.capabilities;
                    const agentInterfaces = agentData.agent.interfaces || {};

                    // Build BT-compatible capabilities object
                    data = {
                        available: agentCaps.bluetooth || false,
                        adapters: (agentInterfaces.bt_adapters || []).map(adapter => ({
                            id: adapter.id || adapter.name || adapter,
                            name: adapter.name || adapter,
                            powered: adapter.powered !== false
                        })),
                        issues: [],
                        preferred_backend: 'auto'
                    };
                    console.log('[BT] Agent capabilities:', data);
                } else {
                    data = { available: false, adapters: [], issues: ['Agent does not support Bluetooth'] };
                }
            } else {
                const response = await fetch('/api/bluetooth/capabilities');
                data = await response.json();
            }

            if (!data.available) {
                showCapabilityWarning(['Bluetooth not available on this system']);
                return;
            }

            if (adapterSelect && data.adapters && data.adapters.length > 0) {
                adapterSelect.innerHTML = data.adapters.map(a => {
                    const status = a.powered ? 'UP' : 'DOWN';
                    return `<option value="${a.id}">${a.id} - ${a.name || 'Bluetooth Adapter'} [${status}]</option>`;
                }).join('');
            } else if (adapterSelect) {
                adapterSelect.innerHTML = '<option value="">No adapters found</option>';
            }

            if (data.issues && data.issues.length > 0) {
                showCapabilityWarning(data.issues);
            } else {
                hideCapabilityWarning();
            }

            // Show/hide Ubertooth option based on capabilities
            const ubertoothOption = document.getElementById('btScanModeUbertooth');
            if (ubertoothOption) {
                ubertoothOption.style.display = data.has_ubertooth ? '' : 'none';
            }

            if (scanModeSelect && data.preferred_backend) {
                const option = scanModeSelect.querySelector(`option[value="${data.preferred_backend}"]`);
                if (option) option.selected = true;
            }

        } catch (err) {
            console.error('Failed to check capabilities:', err);
            showCapabilityWarning(['Failed to check Bluetooth capabilities']);
        }
    }

    function showCapabilityWarning(issues) {
        if (!capabilityStatusEl) return;
        capabilityStatusEl.style.display = 'block';
        capabilityStatusEl.innerHTML = `
            <div style="color: #f59e0b; padding: 10px; background: rgba(245,158,11,0.1); border-radius: 6px; font-size: 12px;">
                ${issues.map(i => `<div>⚠ ${i}</div>`).join('')}
            </div>
        `;
    }

    function hideCapabilityWarning() {
        if (capabilityStatusEl) {
            capabilityStatusEl.style.display = 'none';
            capabilityStatusEl.innerHTML = '';
        }
    }

    async function checkScanStatus() {
        try {
            const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
            const endpoint = isAgentMode
                ? `/controller/agents/${currentAgent}/bluetooth/status`
                : '/api/bluetooth/scan/status';

            const response = await fetch(endpoint);
            const responseData = await response.json();
            // Handle agent response format (may be nested in 'result')
            const data = isAgentMode && responseData.result ? responseData.result : responseData;

            if (data.is_scanning || data.running) {
                setScanning(true);
                startEventStream();
            }

            if (data.baseline_count > 0) {
                baselineSet = true;
                baselineCount = data.baseline_count;
                updateBaselineStatus();
            }

        } catch (err) {
            console.error('Failed to check scan status:', err);
        }
    }

    async function startScan() {
        // Check for agent mode conflicts
        if (!await checkAgentConflicts()) {
            return;
        }

        const adapter = adapterSelect?.value || '';
        const mode = scanModeSelect?.value || 'auto';
        const transport = transportSelect?.value || 'auto';
        const duration = parseInt(durationInput?.value || '0', 10);
        const minRssi = parseInt(minRssiInput?.value || '-100', 10);

        const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';

        if (startBtn) startBtn.classList.add('btn-loading');
        try {
            let response;
            if (isAgentMode) {
                // Route through agent proxy
                response = await fetch(`/controller/agents/${currentAgent}/bluetooth/start`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        mode: mode,
                        adapter_id: adapter || undefined,
                        duration_s: duration > 0 ? duration : undefined,
                        transport: transport,
                        rssi_threshold: minRssi
                    })
                });
            } else {
                response = await fetch('/api/bluetooth/scan/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        mode: mode,
                        adapter_id: adapter || undefined,
                        duration_s: duration > 0 ? duration : undefined,
                        transport: transport,
                        rssi_threshold: minRssi
                    })
                });
            }

            const data = await response.json();

            // Handle controller proxy response format (agent response is nested in 'result')
            const scanResult = isAgentMode && data.result ? data.result : data;

            if (scanResult.status === 'started' || scanResult.status === 'already_scanning') {
                setScanning(true);
                startEventStream();
            } else if (scanResult.status === 'error') {
                showErrorMessage(scanResult.message || 'Failed to start scan');
            } else {
                showErrorMessage(scanResult.message || 'Failed to start scan');
            }

        } catch (err) {
            console.error('Failed to start scan:', err);
            reportActionableError('Start Bluetooth Scan', err, {
                onRetry: () => startScan()
            });
        } finally {
            if (startBtn) startBtn.classList.remove('btn-loading');
        }
    }

    async function stopScan() {
        const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
        const timeoutMs = isAgentMode ? 8000 : 2200;
        const controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
        const timeoutId = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;

        // Optimistic UI teardown keeps mode changes responsive.
        setScanning(false);
        stopEventStream();

        try {
            if (isAgentMode) {
                await fetch(`/controller/agents/${currentAgent}/bluetooth/stop`, {
                    method: 'POST',
                    ...(controller ? { signal: controller.signal } : {}),
                });
            } else {
                await fetch('/api/bluetooth/scan/stop', {
                    method: 'POST',
                    ...(controller ? { signal: controller.signal } : {}),
                });
            }
        } catch (err) {
            console.error('Failed to stop scan:', err);
            reportActionableError('Stop Bluetooth Scan', err);
        } finally {
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
        }
    }

    function setScanning(scanning) {
        isScanning = scanning;

        if (startBtn) startBtn.style.display = scanning ? 'none' : 'block';
        if (stopBtn) stopBtn.style.display = scanning ? 'block' : 'none';

        if (scanning && deviceContainer) {
            pendingDeviceIds.clear();
            selectedDeviceNeedsRefresh = false;
            pendingDeviceFlush = false;
            if (typeof renderCollectionState === 'function') {
                renderCollectionState(deviceContainer, { type: 'loading', message: 'Scanning for Bluetooth devices...' });
            } else {
                deviceContainer.innerHTML = '';
            }
            devices.clear();
            resetStats();
        } else if (!scanning && deviceContainer && devices.size === 0) {
            if (typeof renderCollectionState === 'function') {
                renderCollectionState(deviceContainer, { type: 'empty', message: 'Start scanning to discover Bluetooth devices' });
            }
        }

        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        if (statusDot) statusDot.classList.toggle('running', scanning);
        if (statusText) statusText.textContent = scanning ? 'Scanning...' : 'Idle';

        // Drive the per-panel scan indicator
        const scanDot  = document.getElementById('btScanIndicator')?.querySelector('.bt-scan-dot');
        const scanText = document.getElementById('btScanIndicator')?.querySelector('.bt-scan-text');
        if (scanDot)  scanDot.style.display  = scanning ? 'inline-block' : 'none';
        if (scanText) {
            scanText.textContent = scanning ? 'SCANNING' : 'IDLE';
            scanText.classList.toggle('active', scanning);
        }
    }

    function resetStats() {
        deviceStats = {
            strong: 0,
            medium: 0,
            weak: 0,
            trackers: []
        };
        visibleDeviceCount = 0;
        updateVisualizationPanels();
        updateProximityZones();
        updateFilteredCount();

        // Clear radar
        if (radarInitialized && typeof ProximityRadar !== 'undefined') {
            ProximityRadar.clear();
        }
    }

    function startEventStream() {
        if (eventSource) eventSource.close();

        const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
        const agentName = getCurrentAgentName();
        let streamUrl;

        if (isAgentMode) {
            // Use multi-agent stream for remote agents
            streamUrl = '/controller/stream/all';
            console.log('[BT] Starting multi-agent event stream...');
        } else {
            streamUrl = '/api/bluetooth/stream';
            console.log('[BT] Starting local event stream...');
        }

        eventSource = new EventSource(streamUrl);

        if (isAgentMode) {
            // Handle multi-agent stream
            eventSource.onmessage = (e) => {
                try {
                    const data = JSON.parse(e.data);

                    // Skip keepalive and non-bluetooth data
                    if (data.type === 'keepalive') return;
                    if (data.scan_type !== 'bluetooth') return;

                    // Filter by current agent if not in "show all" mode
                    if (!showAllAgentsMode && typeof agents !== 'undefined') {
                        const currentAgentObj = agents.find(a => a.id == currentAgent);
                        if (currentAgentObj && data.agent_name && data.agent_name !== currentAgentObj.name) {
                            return;
                        }
                    }

                    // Transform multi-agent payload to device updates
                    if (data.payload && data.payload.devices) {
                        Object.values(data.payload.devices).forEach(device => {
                            device._agent = data.agent_name || 'Unknown';
                            handleDeviceUpdate(device);
                        });
                    }
                } catch (err) {
                    console.error('Failed to parse multi-agent event:', err);
                }
            };

            // Also start polling as fallback (in case push isn't enabled on agent)
            startAgentPolling();
        } else {
            // Handle local stream
            eventSource.addEventListener('device_update', (e) => {
                try {
                    const device = JSON.parse(e.data);
                    device._agent = 'Local';
                    handleDeviceUpdate(device);
                } catch (err) {
                    console.error('Failed to parse device update:', err);
                }
            });

            eventSource.addEventListener('scan_started', (e) => {
                setScanning(true);
            });

            eventSource.addEventListener('scan_stopped', (e) => {
                setScanning(false);
            });
        }

        eventSource.onerror = () => {
            console.warn('Bluetooth SSE connection error');
            if (isScanning) {
                // Attempt to reconnect
                setTimeout(() => {
                    if (isScanning) {
                        startEventStream();
                    }
                }, 3000);
            }
        };
    }

    function stopEventStream() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
        if (agentPollTimer) {
            clearInterval(agentPollTimer);
            agentPollTimer = null;
        }
    }

    /**
     * Start polling agent data as fallback when push isn't enabled.
     * This polls the controller proxy endpoint for agent data.
     */
    function startAgentPolling() {
        if (agentPollTimer) return;

        const pollInterval = 3000;  // 3 seconds
        console.log('[BT] Starting agent polling fallback...');

        agentPollTimer = setInterval(async () => {
            if (!isScanning) {
                clearInterval(agentPollTimer);
                agentPollTimer = null;
                return;
            }

            try {
                const response = await fetch(`/controller/agents/${currentAgent}/bluetooth/data`);
                if (!response.ok) return;

                const result = await response.json();
                const data = result.data || result;

                // Process devices from polling response
                if (data && data.devices) {
                    const agentName = getCurrentAgentName();
                    Object.values(data.devices).forEach(device => {
                        device._agent = agentName;
                        handleDeviceUpdate(device);
                    });
                } else if (data && Array.isArray(data)) {
                    const agentName = getCurrentAgentName();
                    data.forEach(device => {
                        device._agent = agentName;
                        handleDeviceUpdate(device);
                    });
                }
            } catch (err) {
                console.debug('[BT] Agent poll error:', err);
            }
        }, pollInterval);
    }

    function handleDeviceUpdate(device) {
        devices.set(device.device_id, device);
        pendingDeviceIds.add(device.device_id);
        if (selectedDeviceId === device.device_id) {
            selectedDeviceNeedsRefresh = true;
        }
        scheduleDeviceFlush();
    }

    function scheduleDeviceFlush() {
        if (pendingDeviceFlush) return;
        pendingDeviceFlush = true;

        requestAnimationFrame(() => {
            pendingDeviceFlush = false;

            pendingDeviceIds.forEach((deviceId) => {
                const device = devices.get(deviceId);
                if (device) {
                    renderDevice(device, false);
                }
            });
            pendingDeviceIds.clear();

            applyDeviceFilter();
            updateDeviceCount();
            updateStatsFromDevices();
            updateVisualizationPanels();
            updateProximityZones();
            updateRadar();

            if (selectedDeviceNeedsRefresh && selectedDeviceId && devices.has(selectedDeviceId)) {
                showDeviceDetail(selectedDeviceId);
            }
            selectedDeviceNeedsRefresh = false;
        });
    }

    /**
     * Update stats from all devices
     */
    function updateStatsFromDevices() {
        // Reset counts
        deviceStats.strong = 0;
        deviceStats.medium = 0;
        deviceStats.weak = 0;
        deviceStats.trackers = [];

        devices.forEach(d => {
            const rssi = d.rssi_current;

            // Signal strength classification
            if (rssi != null) {
                if (rssi >= -50) deviceStats.strong++;
                else if (rssi >= -70) deviceStats.medium++;
                else deviceStats.weak++;
            }

            // Use actual tracker detection from backend (v2)
            // The is_tracker field comes from the TrackerSignatureEngine
            if (d.is_tracker === true) {
                if (!deviceStats.trackers.find(t => t.address === d.address)) {
                    deviceStats.trackers.push(d);
                }
            }
        });
    }

    /**
     * Update visualization panels
     */
    function updateVisualizationPanels() {
        // Signal Distribution
        const total = devices.size || 1;
        const strongBar = document.getElementById('btSignalStrong');
        const mediumBar = document.getElementById('btSignalMedium');
        const weakBar = document.getElementById('btSignalWeak');
        const strongCount = document.getElementById('btSignalStrongCount');
        const mediumCount = document.getElementById('btSignalMediumCount');
        const weakCount = document.getElementById('btSignalWeakCount');

        if (strongBar) strongBar.style.width = (deviceStats.strong / total * 100) + '%';
        if (mediumBar) mediumBar.style.width = (deviceStats.medium / total * 100) + '%';
        if (weakBar) weakBar.style.width = (deviceStats.weak / total * 100) + '%';
        if (strongCount) strongCount.textContent = deviceStats.strong;
        if (mediumCount) mediumCount.textContent = deviceStats.medium;
        if (weakCount) weakCount.textContent = deviceStats.weak;

        // Device summary strip
        const totalEl = document.getElementById('btSummaryTotal');
        const newEl = document.getElementById('btSummaryNew');
        const trackersEl = document.getElementById('btSummaryTrackers');
        const strongestEl = document.getElementById('btSummaryStrongest');
        if (totalEl || newEl || trackersEl || strongestEl) {
            let newCount = 0;
            let strongest = null;
            devices.forEach(d => {
                if (!d.in_baseline) newCount++;
                if (d.rssi_current != null) {
                    strongest = strongest == null ? d.rssi_current : Math.max(strongest, d.rssi_current);
                }
            });
            if (totalEl) totalEl.textContent = devices.size;
            if (newEl) newEl.textContent = newCount;
            if (trackersEl) trackersEl.textContent = deviceStats.trackers.length;
            if (strongestEl) strongestEl.textContent = strongest == null ? '--' : `${strongest} dBm`;
        }

        // Tracker Detection - Enhanced display with confidence and evidence
        const trackerList = document.getElementById('btTrackerList');
        if (trackerList) {
            if (devices.size === 0) {
                if (typeof renderCollectionState === 'function') {
                    renderCollectionState(trackerList, { type: 'empty', message: 'Start scanning to detect trackers' });
                } else {
                    trackerList.innerHTML = '<div class="app-collection-state is-empty">Start scanning to detect trackers</div>';
                }
            } else if (deviceStats.trackers.length === 0) {
                if (typeof renderCollectionState === 'function') {
                    renderCollectionState(trackerList, { type: 'empty', message: 'No trackers detected' });
                } else {
                    trackerList.innerHTML = '<div class="app-collection-state is-empty">No trackers detected</div>';
                }
            } else {
                // Sort by risk score (highest first), then confidence
                const sortedTrackers = [...deviceStats.trackers].sort((a, b) => {
                    const riskA = a.risk_score || 0;
                    const riskB = b.risk_score || 0;
                    if (riskB !== riskA) return riskB - riskA;
                    const confA = a.tracker_confidence_score || 0;
                    const confB = b.tracker_confidence_score || 0;
                    return confB - confA;
                });

                trackerList.innerHTML = sortedTrackers.map((t) => {
                    const confidence = t.tracker_confidence || 'low';
                    const riskScore = t.risk_score || 0;
                    const trackerType = t.tracker_name || t.tracker_type || 'Unknown Tracker';
                    const evidence = (t.tracker_evidence || []).slice(0, 2);
                    const evidenceHtml = evidence.length > 0
                        ? `<div class="bt-tracker-evidence">${evidence.map((e) => `• ${escapeHtml(e)}`).join('<br>')}</div>`
                        : '';
                    const riskClass = riskScore >= 0.5 ? 'high' : riskScore >= 0.3 ? 'medium' : 'low';
                    const riskHtml = riskScore >= 0.3
                        ? `<span class="bt-tracker-risk bt-risk-${riskClass}">RISK ${Math.round(riskScore * 100)}%</span>`
                        : '';

                    return `
                        <div class="bt-tracker-item bt-tracker-confidence-${escapeHtml(confidence)}" data-device-id="${escapeAttr(t.device_id)}" role="button" tabindex="0" data-keyboard-activate="true">
                            <div class="bt-tracker-row-top">
                                <div class="bt-tracker-left">
                                    <span class="bt-tracker-confidence">${escapeHtml(confidence.toUpperCase())}</span>
                                    <span class="bt-tracker-type">${escapeHtml(trackerType)}</span>
                                </div>
                                <div class="bt-tracker-right">
                                    ${riskHtml}
                                    <span class="bt-tracker-rssi">${t.rssi_current != null ? t.rssi_current : '--'} dBm</span>
                                </div>
                            </div>
                            <div class="bt-tracker-row-bottom">
                                <span class="bt-tracker-address">${escapeHtml(t.address_type === 'uuid' ? formatAddress(t) : (t.address || '--'))}</span>
                                <span class="bt-tracker-seen">Seen ${t.seen_count || 0}x</span>
                            </div>
                            ${evidenceHtml}
                        </div>
                    `;
                }).join('');
            }
        }

    }

    function updateDeviceCount() {
        updateFilteredCount();
    }

    function renderDevice(device, reapplyFilter = true) {
        if (!deviceContainer) {
            deviceContainer = document.getElementById('btDeviceListContent');
            if (!deviceContainer) return;
        }

        deviceContainer.querySelectorAll('.app-collection-state, .bt-device-filter-state').forEach((el) => el.remove());

        const escapedId = CSS.escape(device.device_id);
        const existingCard = deviceContainer.querySelector('[data-bt-device-id="' + escapedId + '"]');
        const cardHtml = createSimpleDeviceCard(device);

        if (existingCard) {
            existingCard.outerHTML = cardHtml;
        } else {
            deviceContainer.insertAdjacentHTML('afterbegin', cardHtml);
        }

        if (reapplyFilter) {
            applyDeviceFilter();
        }
    }

    /**
     * Re-render all devices in the current sort order, then re-apply the active filter.
     */
    function renderAllDevices() {
        if (!deviceContainer) return;
        if (devices.size === 0) return;
        deviceContainer.innerHTML = '';

        const sorted = [...devices.values()].sort((a, b) => {
            if (sortBy === 'rssi')     return (b.rssi_current ?? -100) - (a.rssi_current ?? -100);
            if (sortBy === 'name')     return (a.name || '\uFFFF').localeCompare(b.name || '\uFFFF');
            if (sortBy === 'seen')     return (b.seen_count || 0) - (a.seen_count || 0);
            if (sortBy === 'distance') return (a.estimated_distance_m ?? 9999) - (b.estimated_distance_m ?? 9999);
            return 0;
        });

        sorted.forEach(device => renderDevice(device, false));
        applyDeviceFilter();
        if (selectedDeviceId) highlightSelectedDevice(selectedDeviceId);
    }

    function createSimpleDeviceCard(device) {
        const protocol = device.protocol || 'ble';
        const rssi = device.rssi_current;
        const inBaseline = device.in_baseline || false;
        const isNew = !inBaseline;
        const hasName = !!device.name;
        const isTracker = device.is_tracker === true;
        const trackerType = device.tracker_type;
        const trackerConfidence = device.tracker_confidence;
        const riskScore = device.risk_score || 0;
        const agentName = device._agent || 'Local';
        const seenBefore = device.seen_before === true;

        // Signal bar
        const rssiPercent = rssi != null ? Math.max(0, Math.min(100, ((rssi + 100) / 70) * 100)) : 0;
        const fillClass = rssi == null ? 'weak'
                        : rssi >= -60 ? 'strong'
                        : rssi >= -75 ? 'medium' : 'weak';

        const displayName = device.name || formatDeviceId(device.address);
        const name = escapeHtml(displayName);
        const addr = escapeHtml(isUuidAddress(device) ? formatAddress(device) : (device.address || 'Unknown'));
        const mfr = device.manufacturer_name ? escapeHtml(device.manufacturer_name) : '';
        const searchIndex = [
            displayName, device.address, device.manufacturer_name,
            device.tracker_name, device.tracker_type, agentName
        ].filter(Boolean).join(' ').toLowerCase();

        // Protocol badge
        const protoBadge = protocol === 'ble'
            ? '<span class="bt-proto-badge ble">BLE</span>'
            : '<span class="bt-proto-badge classic">CLASSIC</span>';

        // Tracker badge
        let trackerBadge = '';
        if (isTracker) {
            const confColor = trackerConfidence === 'high' ? '#ef4444'
                            : trackerConfidence === 'medium' ? '#f97316' : '#eab308';
            const confBg = trackerConfidence === 'high' ? 'rgba(239,68,68,0.15)'
                         : trackerConfidence === 'medium' ? 'rgba(249,115,22,0.15)' : 'rgba(234,179,8,0.15)';
            const typeLabel = trackerType === 'airtag' ? 'AirTag'
                            : trackerType === 'tile' ? 'Tile'
                            : trackerType === 'samsung_smarttag' ? 'SmartTag'
                            : trackerType === 'findmy_accessory' ? 'FindMy'
                            : trackerType === 'chipolo' ? 'Chipolo' : 'TRACKER';
            trackerBadge = '<span class="bt-tracker-badge" style="background:' + confBg + ';color:' + confColor
                + ';font-size:9px;padding:1px 5px;border-radius:3px;font-weight:600;">' + typeLabel + '</span>';
        }

        // IRK badge
        const irkBadge = device.has_irk ? '<span class="bt-irk-badge">IRK</span>' : '';

        // Risk badge
        let riskBadge = '';
        if (riskScore >= 0.3) {
            const riskColor = riskScore >= 0.5 ? '#ef4444' : '#f97316';
            riskBadge = '<span class="bt-risk-badge" style="color:' + riskColor
                + ';font-size:8px;font-weight:600;">' + Math.round(riskScore * 100) + '% RISK</span>';
        }

        // MAC cluster badge
        const clusterBadge = device.mac_cluster_count > 1
            ? '<span class="bt-mac-cluster-badge">' + device.mac_cluster_count + ' MACs</span>'
            : '';

        // Flag badges (top-right, before status dot)
        const hFlags = device.heuristic_flags || [];
        let flagBadges = '';
        if (device.is_persistent || hFlags.includes('persistent'))
            flagBadges += '<span class="bt-flag-badge persistent">PERSIST</span>';
        if (device.is_beacon_like || hFlags.includes('beacon_like'))
            flagBadges += '<span class="bt-flag-badge beacon-like">BEACON</span>';
        if (device.is_strong_stable || hFlags.includes('strong_stable'))
            flagBadges += '<span class="bt-flag-badge strong-stable">STABLE</span>';

        // Status dot
        let statusDot;
        if (isTracker && trackerConfidence === 'high') {
            statusDot = '<span class="bt-status-dot tracker" style="background:#ef4444;"></span>';
        } else if (isNew) {
            statusDot = '<span class="bt-status-dot new"></span>';
        } else {
            statusDot = '<span class="bt-status-dot known"></span>';
        }

        // Bottom meta
        const metaLabel = mfr || addr;  // already HTML-escaped
        // No distance: one receiver cannot measure it from signal strength
        let metaHtml = '<span>' + metaLabel + '</span>';
        metaHtml += '<span class="bt-row-rssi ' + fillClass + '">' + (rssi != null ? rssi : '—') + '</span>';
        if (seenBefore) metaHtml += '<span class="bt-history-badge">SEEN</span>';
        if (agentName !== 'Local')
            metaHtml += '<span class="agent-badge agent-remote" style="font-size:8px;padding:1px 4px;">'
                + escapeHtml(agentName) + '</span>';

        // Left border colour
        const borderColor = isTracker && trackerConfidence === 'high' ? '#ef4444'
                          : isTracker ? '#f97316'
                          : rssi != null && rssi >= -60 ? 'var(--accent-green)'
                          : rssi != null && rssi >= -75 ? 'var(--accent-amber, #eab308)'
                          : 'var(--accent-red)';

        return '<div class="bt-device-row' + (isTracker ? ' is-tracker' : '') + '"'
            + ' data-bt-device-id="' + escapeAttr(device.device_id) + '"'
            + ' data-is-new="' + isNew + '"'
            + ' data-has-name="' + hasName + '"'
            + ' data-rssi="' + (rssi ?? -100) + '"'
            + ' data-is-tracker="' + isTracker + '"'
            + ' data-search="' + escapeAttr(searchIndex) + '"'
            + ' role="button" tabindex="0" data-keyboard-activate="true"'
            + ' style="border-left-color:' + borderColor + ';">'
            // Top line
            + '<div class="bt-row-top">'
                + '<div class="bt-row-top-left">'
                    + protoBadge
                    + '<span class="bt-row-name' + (hasName ? '' : ' bt-unnamed') + '">' + name + '</span>'
                    + trackerBadge + irkBadge + riskBadge + clusterBadge
                + '</div>'
                + '<div class="bt-row-top-right">'
                    + flagBadges + statusDot
                    + '<button type="button" class="locate-action bt-row-locate" title="Locate this device">'
                        + LOCATE_ICON + 'Locate</button>'
                + '</div>'
            + '</div>'
            // Bottom line
            + '<div class="bt-row-bottom">'
                + '<div class="bt-signal-bar-wrap">'
                    + '<div class="bt-signal-track">'
                        + '<div class="bt-signal-fill ' + fillClass + '" style="width:' + rssiPercent.toFixed(1) + '%"></div>'
                    + '</div>'
                + '</div>'
                + '<div class="bt-row-meta">' + metaHtml + '</div>'
            + '</div>'
        + '</div>';
    }

    function getRssiColor(rssi) {
        if (rssi == null) return '#666';
        if (rssi >= -50) return '#22c55e';
        if (rssi >= -60) return '#84cc16';
        if (rssi >= -70) return '#eab308';
        if (rssi >= -80) return '#f97316';
        return '#ef4444';
    }

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    function escapeAttr(text) {
        return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    async function setBaseline() {
        try {
            const response = await fetch('/api/bluetooth/baseline/set', { method: 'POST' });
            const data = await response.json();

            if (data.status === 'success') {
                baselineSet = true;
                baselineCount = data.device_count;
                updateBaselineStatus();
            }
        } catch (err) {
            console.error('Failed to set baseline:', err);
            reportActionableError('Set Baseline', err, {
                onRetry: () => setBaseline()
            });
        }
    }

    async function clearBaseline() {
        try {
            const response = await fetch('/api/bluetooth/baseline/clear', { method: 'POST' });
            const data = await response.json();

            if (data.status === 'success') {
                baselineSet = false;
                baselineCount = 0;
                updateBaselineStatus();
            }
        } catch (err) {
            console.error('Failed to clear baseline:', err);
            reportActionableError('Clear Baseline', err, {
                onRetry: () => clearBaseline()
            });
        }
    }

    function updateBaselineStatus() {
        if (!baselineStatusEl) return;

        if (baselineSet) {
            baselineStatusEl.textContent = `Baseline: ${baselineCount} devices`;
            baselineStatusEl.style.color = '#22c55e';
        } else {
            baselineStatusEl.textContent = 'No baseline';
            baselineStatusEl.style.color = '';
        }
    }

    function exportData(format) {
        window.open(`/api/bluetooth/export?format=${format}`, '_blank');
    }

    function showErrorMessage(message) {
        console.error('[BT] Error:', message);
        if (typeof showNotification === 'function') {
            showNotification('Bluetooth Error', message, 'error');
        }
    }

    function showInfo(message) {
        console.log('[BT]', message);
        if (typeof showNotification === 'function') {
            showNotification('Bluetooth', message, 'info');
        }
    }

    /**
     * Toggle the service data inspector panel
     */
    function toggleServiceInspector() {
        const content = document.getElementById('btInspectorContent');
        const arrow = document.getElementById('btInspectorArrow');
        if (!content) return;
        const open = content.style.display === 'none';
        content.style.display = open ? '' : 'none';
        if (arrow) arrow.classList.toggle('open', open);
    }

    // ==========================================================================
    // Agent Handling
    // ==========================================================================

    /**
     * Handle agent change - refresh adapters and optionally clear data.
     */
    function handleAgentChange() {
        const currentAgentId = typeof currentAgent !== 'undefined' ? currentAgent : 'local';

        // Check if agent actually changed
        if (lastAgentId === currentAgentId) return;

        console.log('[BT] Agent changed from', lastAgentId, 'to', currentAgentId);

        // Stop any running scan
        if (isScanning) {
            stopScan();
        }

        // Clear existing data when switching agents (unless "Show All" is enabled)
        if (!showAllAgentsMode) {
            clearData();
            showInfo(`Switched to ${getCurrentAgentName()} - previous data cleared`);
        }

        // Refresh capabilities for new agent
        checkCapabilities();

        lastAgentId = currentAgentId;
    }

    /**
     * Clear all collected data.
     */
    function clearData() {
        devices.clear();
        pendingDeviceIds.clear();
        pendingDeviceFlush = false;
        selectedDeviceNeedsRefresh = false;
        resetStats();
        clearSelection();

        if (deviceContainer) {
            if (typeof renderCollectionState === 'function') {
                renderCollectionState(deviceContainer, { type: 'empty', message: 'Start scanning to discover Bluetooth devices' });
            } else {
                deviceContainer.innerHTML = '';
            }
        }
    }

    /**
     * Toggle "Show All Agents" mode.
     */
    function toggleShowAllAgents(enabled) {
        showAllAgentsMode = enabled;
        console.log('[BT] Show all agents mode:', enabled);

        if (enabled) {
            // If currently scanning, switch to multi-agent stream
            if (isScanning && eventSource) {
                eventSource.close();
                startEventStream();
            }
            showInfo('Showing Bluetooth devices from all agents');
        } else {
            // Filter to current agent only
            filterToCurrentAgent();
        }
    }

    /**
     * Filter devices to only show those from current agent.
     */
    function filterToCurrentAgent() {
        const agentName = getCurrentAgentName();
        const toRemove = [];

        devices.forEach((device, deviceId) => {
            if (device._agent && device._agent !== agentName) {
                toRemove.push(deviceId);
            }
        });

        toRemove.forEach(deviceId => devices.delete(deviceId));

        // Re-render device list
        if (deviceContainer) {
            deviceContainer.innerHTML = '';
            devices.forEach(device => renderDevice(device, false));
            applyDeviceFilter();
            if (devices.size === 0 && typeof renderCollectionState === 'function') {
                renderCollectionState(deviceContainer, { type: 'empty', message: 'No devices for current agent' });
            }
        }

        if (selectedDeviceId && !devices.has(selectedDeviceId)) {
            clearSelection();
        }

        updateDeviceCount();
        updateStatsFromDevices();
        updateVisualizationPanels();
        updateProximityZones();
        updateRadar();
    }

    /**
     * Hand off a device to BT Locate mode by device_id lookup.
     */
    function locateById(deviceId) {
        console.log('[BT] locateById called with:', deviceId);
        const device = devices.get(deviceId);
        if (!device) {
            console.warn('[BT] Device not found in map for id:', deviceId);
            return;
        }
        doLocateHandoff(device);
    }

    /**
     * Hand off the currently selected device to BT Locate mode.
     */
    function locateDevice() {
        if (!selectedDeviceId) return;
        const device = devices.get(selectedDeviceId);
        if (!device) return;
        doLocateHandoff(device);
    }

    function doLocateHandoff(device) {
        const payload = {
            device_id: device.device_id,
            device_key: device.device_key || null,
            mac_address: device.address,
            address_type: device.address_type || null,
            irk_hex: device.irk_hex || null,
            known_name: device.name || null,
            known_manufacturer: device.manufacturer_name || null,
            last_known_rssi: device.rssi_current,
            tx_power: device.tx_power || null,
            appearance_name: device.appearance_name || null,
            fingerprint_id: device.fingerprint_id || device.fingerprint?.id || null,
            mac_cluster_count: device.mac_cluster_count || 0
        };

        // Always switch to bt_locate mode first (loads script + styles if needed,
        // initializes the module), then hand off device data and start: pressing
        // Locate means locate, not "go and press Start".
        if (typeof switchMode === 'function') {
            switchMode('bt_locate').then(function() {
                if (typeof BtLocate !== 'undefined') {
                    BtLocate.handoff(payload);
                    BtLocate.start();
                }
            });
        }
    }

    // Public API
    return {
        init,
        startScan,
        stopScan,
        checkCapabilities,
        setBaseline,
        clearBaseline,
        exportData,
        selectDevice,
        clearSelection,
        copyAddress,
        toggleWatchlist,
        locateDevice,
        locateById,
        toggleServiceInspector,

        // Agent handling
        handleAgentChange,
        clearData,
        toggleShowAllAgents,

        // Getters
        getDevices: () => Array.from(devices.values()),
        isScanning: () => isScanning,
        isShowAllAgents: () => showAllAgentsMode,

        // Lifecycle
        destroy
    };

    /**
     * Destroy — close SSE stream and clear polling timers for clean mode switching.
     */
    function destroy() {
        stopEventStream();
        devices.clear();
        pendingDeviceIds.clear();
        if (deviceContainer) {
            deviceContainer.innerHTML = '';
        }
        const countEl = document.getElementById('btDeviceListCount');
        if (countEl) countEl.textContent = '0';
    }
})();

// Global functions for onclick handlers
function btStartScan() { BluetoothMode.startScan(); }
function btStopScan() { BluetoothMode.stopScan(); }
function btCheckCapabilities() { BluetoothMode.checkCapabilities(); }
function btSetBaseline() { BluetoothMode.setBaseline(); }
function btClearBaseline() { BluetoothMode.clearBaseline(); }
function btExport(format) { BluetoothMode.exportData(format); }

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        if (document.getElementById('bluetoothMode')) {
            BluetoothMode.init();
        }
    });
} else {
    if (document.getElementById('bluetoothMode')) {
        BluetoothMode.init();
    }
}

window.BluetoothMode = BluetoothMode;
