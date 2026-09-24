/**
 * WiFi Mode Controller (v2)
 *
 * Unified WiFi scanning with dual-mode architecture:
 * - Quick Scan: System tools without monitor mode
 * - Deep Scan: airodump-ng with monitor mode
 *
 * Features:
 * - Proximity radar visualization
 * - Channel utilization analysis
 * - Hidden SSID correlation
 * - Real-time SSE streaming
 */

const WiFiMode = (function() {
    'use strict';

    // ==========================================================================
    // Configuration
    // ==========================================================================

    const CONFIG = {
        apiBase: '/wifi/v2',
        pollInterval: 5000,
        keepaliveTimeout: 30000,
        maxNetworks: 500,
        maxClients: 500,
        maxProbes: 1000,
    };

    // ==========================================================================
    // Agent Support
    // ==========================================================================

    /**
     * Get the current agent name for tagging data.
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
     * Check for agent mode conflicts before starting WiFi scan.
     */
    async function checkAgentConflicts() {
        if (typeof currentAgent === 'undefined' || currentAgent === 'local') {
            return true;
        }
        if (typeof checkAgentModeConflict === 'function') {
            return await checkAgentModeConflict('wifi');
        }
        return true;
    }

    function getChannelPresetList(preset) {
        switch (preset) {
            case '2.4-common':
                return '1,6,11';
            case '2.4-all':
                return '1,2,3,4,5,6,7,8,9,10,11,12,13';
            case '5-low':
                return '36,40,44,48';
            case '5-mid':
                return '52,56,60,64';
            case '5-high':
                return '149,153,157,161,165';
            default:
                return '';
        }
    }

    function buildChannelConfig() {
        const preset = document.getElementById('wifiChannelPreset')?.value || '';
        const listInput = document.getElementById('wifiChannelList')?.value || '';
        const singleInput = document.getElementById('wifiChannel')?.value || '';

        const listValue = listInput.trim();
        const presetValue = getChannelPresetList(preset);

        const channels = listValue || presetValue || '';
        const channel = channels ? null : (singleInput.trim() ? parseInt(singleInput.trim()) : null);

        return {
            channels: channels || null,
            channel: Number.isFinite(channel) ? channel : null,
        };
    }

    // ==========================================================================
    // State
    // ==========================================================================

    let isScanning = false;
    let scanMode = 'quick'; // 'quick' or 'deep'
    let eventSource = null;
    let pollTimer = null;
    let agentPollTimer = null;

    // Data stores
    let networks = new Map(); // bssid -> network
    let clients = new Map();  // mac -> client
    let probeRequests = [];
    let channelStats = [];
    let recommendations = [];
    let channelHistory = []; // max 10 entries, each { timestamp, channels: {1:N,...,11:N} }

    // UI state
    let selectedBssid = null;
    let currentFilter = 'all';
    let currentSort = { field: 'rssi', order: 'desc' };
    let renderFramePending = false;
    const pendingRender = {
        table: false,
        stats: false,
        radar: false,
        detail: false,
    };
    const listenersBound = {
        scanTabs: false,
        filters: false,
        sort: false,
    };

    // Agent state
    let showAllAgentsMode = false;  // Show combined results from all agents
    let lastAgentId = null;  // Track agent switches

    // Capabilities
    let capabilities = null;

    // Callbacks for external integration
    let onNetworkUpdate = null;
    let onClientUpdate = null;
    let onProbeRequest = null;

    // ==========================================================================
    // Initialization
    // ==========================================================================

    function init() {
        // Capabilities and one-time component setup only on first call.
        // Subsequent visits refresh scan state and re-render without redundant fetches.
        const firstInit = capabilities === null;

        cacheDOM();

        if (firstInit) {
            checkCapabilities();
            initScanModeTabs();
            initNetworkFilters();
            initSortControls();
            initHeatmap();
        }

        scheduleRender({ table: true, stats: true, radar: true });
        checkScanStatus();
    }

    // DOM element cache
    let elements = {};

    function cacheDOM() {
        elements = {
            // Scan controls
            quickScanBtn: document.getElementById('wifiQuickScanBtn'),
            deepScanBtn: document.getElementById('wifiDeepScanBtn'),
            stopScanBtn: document.getElementById('wifiStopScanBtn'),
            scanModeQuick: document.getElementById('wifiScanModeQuick'),
            scanModeDeep: document.getElementById('wifiScanModeDeep'),

            // Status bar
            scanIndicator: document.getElementById('wifiScanIndicator'),
            openCount: document.getElementById('wifiOpenCount'),
            networkCount: document.getElementById('wifiNetworkCount'),
            clientCount: document.getElementById('wifiClientCount'),
            hiddenCount: document.getElementById('wifiHiddenCount'),

            // Network list
            networkList: document.getElementById('wifiNetworkList'),
            networkFilters: document.getElementById('wifiNetworkFilters'),

            // Visualizations — heatmap & security ring
            heatmapGrid: document.getElementById('wifiHeatmapGrid'),
            heatmapChLabels: document.getElementById('wifiHeatmapChLabels'),
            heatmapCount: document.getElementById('wifiHeatmapCount'),
            securityRingSvg: document.getElementById('wifiSecurityRingSvg'),
            securityRingLegend: document.getElementById('wifiSecurityRingLegend'),
            heatmapView: document.getElementById('wifiHeatmapView'),
            detailView: document.getElementById('wifiDetailView'),
            rightPanelTitle: document.getElementById('wifiRightPanelTitle'),
            detailBackBtn: document.getElementById('wifiDetailBackBtn'),

            // Zone summary
            zoneImmediate: document.getElementById('wifiZoneImmediate'),
            zoneNear: document.getElementById('wifiZoneNear'),
            zoneFar: document.getElementById('wifiZoneFar'),

            // Detail panel
            detailSignalFill: document.getElementById('wifiDetailSignalFill'),
            detailEssid: document.getElementById('wifiDetailEssid'),
            detailBssid: document.getElementById('wifiDetailBssid'),
            detailRssi: document.getElementById('wifiDetailRssi'),
            detailChannel: document.getElementById('wifiDetailChannel'),
            detailBand: document.getElementById('wifiDetailBand'),
            detailSecurity: document.getElementById('wifiDetailSecurity'),
            detailCipher: document.getElementById('wifiDetailCipher'),
            detailVendor: document.getElementById('wifiDetailVendor'),
            detailClients: document.getElementById('wifiDetailClients'),
            detailFirstSeen: document.getElementById('wifiDetailFirstSeen'),
            detailClientList: document.getElementById('wifiDetailClientList'),

            // Interface select
            interfaceSelect: document.getElementById('wifiInterfaceSelect'),

            // Capability status
            capabilityStatus: document.getElementById('wifiCapabilityStatus'),

            // Export buttons
            exportCsvBtn: document.getElementById('wifiExportCsv'),
            exportJsonBtn: document.getElementById('wifiExportJson'),
        };
    }

    // ==========================================================================
    // Capabilities
    // ==========================================================================

    async function checkCapabilities() {
        const capBtn = document.getElementById('wifiQuickScanBtn');
        if (capBtn) capBtn.classList.add('btn-loading');
        try {
            const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
            let response;

            if (isAgentMode) {
                // Fetch capabilities from agent via controller proxy
                response = await fetch(`/controller/agents/${currentAgent}?refresh=true`);
                if (!response.ok) throw new Error('Failed to fetch agent capabilities');

                const data = await response.json();
                // Extract WiFi capabilities from agent data
                if (data.agent && data.agent.capabilities) {
                    const agentCaps = data.agent.capabilities;
                    const agentInterfaces = data.agent.interfaces || {};

                    // Build WiFi-compatible capabilities object
                    capabilities = {
                        can_quick_scan: agentCaps.wifi || false,
                        can_deep_scan: agentCaps.wifi || false,
                        interfaces: (agentInterfaces.wifi_interfaces || []).map(iface => ({
                            name: iface.name || iface,
                            supports_monitor: iface.supports_monitor !== false
                        })),
                        default_interface: agentInterfaces.default_wifi || null,
                        preferred_quick_tool: 'agent',
                        issues: []
                    };
                    console.log('[WiFiMode] Agent capabilities:', capabilities);
                } else {
                    throw new Error('Agent does not support WiFi mode');
                }
            } else {
                // Local capabilities
                response = await fetch(`${CONFIG.apiBase}/capabilities`);
                if (!response.ok) throw new Error('Failed to fetch capabilities');
                capabilities = await response.json();
                console.log('[WiFiMode] Local capabilities:', capabilities);
            }

            updateCapabilityUI();
            populateInterfaceSelect();
        } catch (error) {
            console.error('[WiFiMode] Capability check failed:', error);
            showCapabilityError('Failed to check WiFi capabilities');
        } finally {
            if (capBtn) capBtn.classList.remove('btn-loading');
        }
    }

    function updateCapabilityUI() {
        if (!capabilities || !elements.capabilityStatus) return;

        let html = '';

        if (!capabilities.can_quick_scan && !capabilities.can_deep_scan) {
            html = `
                <div class="wifi-capability-warning">
                    <strong>WiFi scanning not available</strong>
                    <ul>
                        ${capabilities.issues.map(i => `<li>${escapeHtml(i)}</li>`).join('')}
                    </ul>
                </div>
            `;
        } else {
            // Show available modes
            const modes = [];
            if (capabilities.can_quick_scan) modes.push('Quick Scan');
            if (capabilities.can_deep_scan) modes.push('Deep Scan');

            html = `
                <div class="wifi-capability-info">
                    Available modes: ${modes.join(', ')}
                    ${capabilities.preferred_quick_tool ? ` (using ${capabilities.preferred_quick_tool})` : ''}
                </div>
            `;

            if (capabilities.issues.length > 0) {
                html += `
                    <div class="wifi-capability-warning" style="margin-top: 8px;">
                        <small>${capabilities.issues.join('. ')}</small>
                    </div>
                `;
            }
        }

        elements.capabilityStatus.innerHTML = html;
        elements.capabilityStatus.style.display = html ? 'block' : 'none';

        // Enable/disable scan buttons based on capabilities
        if (elements.quickScanBtn) {
            elements.quickScanBtn.disabled = !capabilities.can_quick_scan;
        }
        if (elements.deepScanBtn) {
            elements.deepScanBtn.disabled = !capabilities.can_deep_scan;
        }
    }

    function showCapabilityError(message) {
        if (!elements.capabilityStatus) return;

        elements.capabilityStatus.innerHTML = `
            <div class="wifi-capability-error">${escapeHtml(message)}</div>
        `;
        elements.capabilityStatus.style.display = 'block';
    }

    function populateInterfaceSelect() {
        if (!elements.interfaceSelect || !capabilities) return;

        elements.interfaceSelect.innerHTML = '';

        if (capabilities.interfaces.length === 0) {
            elements.interfaceSelect.innerHTML = '<option value="">No interfaces found</option>';
            return;
        }

        capabilities.interfaces.forEach(iface => {
            const option = document.createElement('option');
            option.value = iface.name;
            option.textContent = `${iface.name}${iface.supports_monitor ? ' (monitor capable)' : ''}`;
            elements.interfaceSelect.appendChild(option);
        });

        // Select default
        if (capabilities.default_interface) {
            elements.interfaceSelect.value = capabilities.default_interface;
        }
    }

    // ==========================================================================
    // Scan Mode Tabs
    // ==========================================================================

    function initScanModeTabs() {
        if (listenersBound.scanTabs) return;
        if (elements.scanModeQuick) {
            elements.scanModeQuick.addEventListener('click', () => setScanMode('quick'));
        }
        if (elements.scanModeDeep) {
            elements.scanModeDeep.addEventListener('click', () => setScanMode('deep'));
        }
        // Arrow key navigation between tabs
        const tabContainer = document.querySelector('.wifi-scan-mode-tabs');
        if (tabContainer) {
            tabContainer.addEventListener('keydown', (e) => {
                const tabs = Array.from(tabContainer.querySelectorAll('[role="tab"]'));
                const idx = tabs.indexOf(document.activeElement);
                if (idx === -1) return;
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                    e.preventDefault();
                    const next = tabs[(idx + 1) % tabs.length];
                    next.focus();
                    next.click();
                } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                    e.preventDefault();
                    const prev = tabs[(idx - 1 + tabs.length) % tabs.length];
                    prev.focus();
                    prev.click();
                }
            });
        }
        listenersBound.scanTabs = true;
    }

    function setScanMode(mode) {
        scanMode = mode;

        // Update tab UI and ARIA states
        if (elements.scanModeQuick) {
            elements.scanModeQuick.classList.toggle('active', mode === 'quick');
            elements.scanModeQuick.setAttribute('aria-selected', mode === 'quick' ? 'true' : 'false');
        }
        if (elements.scanModeDeep) {
            elements.scanModeDeep.classList.toggle('active', mode === 'deep');
            elements.scanModeDeep.setAttribute('aria-selected', mode === 'deep' ? 'true' : 'false');
        }

        console.log('[WiFiMode] Scan mode set to:', mode);
    }

    // ==========================================================================
    // Scanning
    // ==========================================================================

    async function startQuickScan() {
        if (isScanning) return;

        // Check for agent mode conflicts
        if (!await checkAgentConflicts()) {
            return;
        }

        console.log('[WiFiMode] Starting quick scan...');
        if (elements.quickScanBtn) elements.quickScanBtn.classList.add('btn-loading');
        setScanning(true, 'quick');

        try {
            const iface = elements.interfaceSelect?.value || null;
            const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
            const agentName = getCurrentAgentName();

            let response;
            if (isAgentMode) {
                // Route through agent proxy
                response = await fetch(`/controller/agents/${currentAgent}/wifi/start`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ interface: iface, scan_type: 'quick' }),
                });
            } else {
                response = await fetch(`${CONFIG.apiBase}/scan/quick`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ interface: iface }),
                });
            }

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Quick scan failed');
            }

            const result = await response.json();
            console.log('[WiFiMode] Quick scan complete:', result);

            // Handle controller proxy response format (agent response is nested in 'result')
            const scanResult = isAgentMode && result.result ? result.result : result;

            // Check for error first
            if (scanResult.error || scanResult.status === 'error') {
                console.error('[WiFiMode] Quick scan error from server:', scanResult.error || scanResult.message);
                showError(scanResult.error || scanResult.message || 'Quick scan failed');
                setScanning(false);
                return;
            }

            // Handle agent response format
            let accessPoints = scanResult.access_points || scanResult.networks || [];

            // Check if we got results
            if (accessPoints.length === 0) {
                // No error but no results
                let msg = 'Quick scan found no networks in range.';
                if (scanResult.warnings && scanResult.warnings.length > 0) {
                    msg += ' Warnings: ' + scanResult.warnings.join('; ');
                }
                console.warn('[WiFiMode] ' + msg);
                showError(msg + ' Try Deep Scan with monitor mode.');
                setScanning(false);
                return;
            }

            // Tag results with agent source
            accessPoints.forEach(ap => {
                ap._agent = agentName;
            });

            // Show any warnings even on success
            if (scanResult.warnings && scanResult.warnings.length > 0) {
                console.warn('[WiFiMode] Quick scan warnings:', scanResult.warnings);
            }

            // Process results
            processQuickScanResult({ ...scanResult, access_points: accessPoints });

            // For quick scan, we're done after one scan
            // But keep polling if user wants continuous updates
            if (scanMode === 'quick') {
                startQuickScanPolling();
            }
        } catch (error) {
            console.error('[WiFiMode] Quick scan error:', error);
            showError(error.message + '. Try using Deep Scan instead.');
            setScanning(false);
        } finally {
            if (elements.quickScanBtn) elements.quickScanBtn.classList.remove('btn-loading');
        }
    }

    async function startDeepScan() {
        if (isScanning) return;

        // Check for agent mode conflicts
        if (!await checkAgentConflicts()) {
            return;
        }

        console.log('[WiFiMode] Starting deep scan...');
        if (elements.deepScanBtn) elements.deepScanBtn.classList.add('btn-loading');
        setScanning(true, 'deep');

        try {
            const iface = elements.interfaceSelect?.value || null;
            const band = document.getElementById('wifiBand')?.value || 'all';
            const channelConfig = buildChannelConfig();
            const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';

            let response;
            if (isAgentMode) {
                // Route through agent proxy
                response = await fetch(`/controller/agents/${currentAgent}/wifi/start`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        interface: iface,
                        scan_type: 'deep',
                        band: band === 'abg' ? 'all' : band === 'bg' ? '2.4' : '5',
                        channel: channelConfig.channel,
                        channels: channelConfig.channels,
                    }),
                });
            } else {
                response = await fetch(`${CONFIG.apiBase}/scan/start`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        interface: iface,
                        band: band === 'abg' ? 'all' : band === 'bg' ? '2.4' : '5',
                        channel: channelConfig.channel,
                        channels: channelConfig.channels,
                    }),
                });
            }

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to start deep scan');
            }

            // Check for agent error in response
            if (isAgentMode) {
                const result = await response.json();
                const scanResult = result.result || result;
                if (scanResult.status === 'error') {
                    throw new Error(scanResult.message || 'Agent failed to start deep scan');
                }
                console.log('[WiFiMode] Agent deep scan started:', scanResult);
            }

            // Start SSE stream for real-time updates (works with push-enabled agents)
            startEventStream();

            // Also start polling for agent data (works without push enabled)
            if (isAgentMode) {
                startAgentDeepScanPolling();
            }
        } catch (error) {
            console.error('[WiFiMode] Deep scan error:', error);
            showError(error.message);
            setScanning(false);
        } finally {
            if (elements.deepScanBtn) elements.deepScanBtn.classList.remove('btn-loading');
        }
    }

    async function stopScan() {
        console.log('[WiFiMode] Stopping scan...');

        // Stop polling
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }

        // Stop agent polling
        stopAgentDeepScanPolling();

        // Close event stream
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }

        // Update UI immediately so mode transitions are responsive even if the
        // backend needs extra time to terminate subprocesses.
        setScanning(false);

        // Stop scan on server (local or agent)
        const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
        const timeoutMs = isAgentMode ? 8000 : 2200;
        const controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
        const timeoutId = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;

        try {
            if (isAgentMode) {
                await fetch(`/controller/agents/${currentAgent}/wifi/stop`, {
                    method: 'POST',
                    ...(controller ? { signal: controller.signal } : {}),
                });
            } else if (scanMode === 'deep') {
                await fetch(`${CONFIG.apiBase}/scan/stop`, {
                    method: 'POST',
                    ...(controller ? { signal: controller.signal } : {}),
                });
            }
        } catch (error) {
            console.warn('[WiFiMode] Error stopping scan:', error);
        } finally {
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
        }
    }

    function setScanning(scanning, mode = null) {
        isScanning = scanning;
        if (mode) scanMode = mode;

        // Update buttons
        if (elements.quickScanBtn) {
            elements.quickScanBtn.style.display = scanning ? 'none' : 'inline-block';
        }
        if (elements.deepScanBtn) {
            elements.deepScanBtn.style.display = scanning ? 'none' : 'inline-block';
        }
        if (elements.stopScanBtn) {
            elements.stopScanBtn.style.display = scanning ? 'inline-block' : 'none';
        }

        // Update status
        const dot  = elements.scanIndicator?.querySelector('.wifi-scan-dot');
        const text = elements.scanIndicator?.querySelector('.wifi-scan-text');
        if (dot)  dot.style.display = scanning ? 'inline-block' : 'none';
        if (text) text.textContent  = scanning
            ? `SCANNING (${scanMode === 'quick' ? 'Quick' : 'Deep'})`
            : 'IDLE';
    }

    async function checkScanStatus() {
        try {
            const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
            const endpoint = isAgentMode
                ? `/controller/agents/${currentAgent}/wifi/status`
                : `${CONFIG.apiBase}/scan/status`;

            const response = await fetch(endpoint);
            if (!response.ok) return;

            const data = await response.json();
            // Handle agent response format (may be nested in 'result')
            const status = isAgentMode && data.result ? data.result : data;

            if (status.is_scanning || status.running) {
                // Agent returns scan_type in params, local returns scan_mode
                // Normalize: agent may return 'deepscan' or 'deep', UI expects 'deep' or 'quick'
                let detectedMode = status.scan_mode || (status.params && status.params.scan_type) || 'deep';
                if (detectedMode === 'deepscan') detectedMode = 'deep';

                setScanning(true, detectedMode);
                if (detectedMode === 'deep') {
                    startEventStream();
                    // Also start polling for agent mode (works without push enabled)
                    if (isAgentMode) {
                        startAgentDeepScanPolling();
                    }
                } else {
                    startQuickScanPolling();
                }
            }
        } catch (error) {
            console.debug('[WiFiMode] Status check failed:', error);
        }
    }

    // ==========================================================================
    // Quick Scan Polling
    // ==========================================================================

    function startQuickScanPolling() {
        if (pollTimer) return;

        pollTimer = setInterval(async () => {
            if (!isScanning || scanMode !== 'quick') {
                clearInterval(pollTimer);
                pollTimer = null;
                return;
            }

            try {
                const iface = elements.interfaceSelect?.value || null;
                const response = await fetch(`${CONFIG.apiBase}/scan/quick`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ interface: iface }),
                });

                if (response.ok) {
                    const result = await response.json();
                    processQuickScanResult(result);
                }
            } catch (error) {
                console.debug('[WiFiMode] Poll error:', error);
            }
        }, CONFIG.pollInterval);
    }

    function processQuickScanResult(result) {
        // Update networks
        result.access_points.forEach(ap => {
            networks.set(ap.bssid, ap);
        });

        // Update channel stats (calculate from networks if not provided by API)
        channelStats = result.channel_stats || [];
        recommendations = result.recommendations || [];

        // If no channel stats from API, calculate from networks
        if (channelStats.length === 0 && networks.size > 0) {
            channelStats = calculateChannelStats();
        }

        // Update UI
        scheduleRender({ table: true, stats: true, radar: true });

        // Callbacks
        result.access_points.forEach(ap => {
            if (onNetworkUpdate) onNetworkUpdate(ap);
        });
    }

    // ==========================================================================
    // Agent Deep Scan Polling (fallback when push is not enabled)
    // ==========================================================================

    function startAgentDeepScanPolling() {
        if (agentPollTimer) return;

        console.log('[WiFiMode] Starting agent deep scan polling...');

        agentPollTimer = setInterval(async () => {
            if (!isScanning || scanMode !== 'deep') {
                clearInterval(agentPollTimer);
                agentPollTimer = null;
                return;
            }

            const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
            if (!isAgentMode) {
                clearInterval(agentPollTimer);
                agentPollTimer = null;
                return;
            }

            try {
                const response = await fetch(`/controller/agents/${currentAgent}/wifi/data`);
                if (!response.ok) return;

                const result = await response.json();
                if (result.status !== 'success' || !result.data) return;

                const data = result.data.data || result.data;
                const agentName = result.agent_name || 'Remote';

                // Process networks
                if (data.networks && Array.isArray(data.networks)) {
                    data.networks.forEach(net => {
                        net._agent = agentName;
                        handleStreamEvent({
                            type: 'network_update',
                            network: net
                        });
                    });
                }

                // Process clients
                if (data.clients && Array.isArray(data.clients)) {
                    data.clients.forEach(client => {
                        client._agent = agentName;
                        handleStreamEvent({
                            type: 'client_update',
                            client: client
                        });
                    });
                }

                console.debug(`[WiFiMode] Agent poll: ${data.networks?.length || 0} networks, ${data.clients?.length || 0} clients`);

            } catch (error) {
                console.debug('[WiFiMode] Agent poll error:', error);
            }
        }, 2000); // Poll every 2 seconds
    }

    function stopAgentDeepScanPolling() {
        if (agentPollTimer) {
            clearInterval(agentPollTimer);
            agentPollTimer = null;
        }
    }

    // ==========================================================================
    // SSE Event Stream
    // ==========================================================================

    function startEventStream() {
        if (eventSource) {
            eventSource.close();
        }

        const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
        const agentName = getCurrentAgentName();
        let streamUrl;

        if (isAgentMode) {
            // Use multi-agent stream for remote agents
            streamUrl = '/controller/stream/all';
            console.log('[WiFiMode] Starting multi-agent event stream...');
        } else {
            streamUrl = `${CONFIG.apiBase}/stream`;
            console.log('[WiFiMode] Starting local event stream...');
        }

        eventSource = new EventSource(streamUrl);

        eventSource.onopen = () => {
            console.log('[WiFiMode] Event stream connected');
        };

        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                // For multi-agent stream, filter and transform data
                if (isAgentMode) {
                    // Skip keepalive and non-wifi data
                    if (data.type === 'keepalive') return;
                    if (data.scan_type !== 'wifi') return;

                    // Filter by current agent if not in "show all" mode
                    if (!showAllAgentsMode && typeof agents !== 'undefined') {
                        const currentAgentObj = agents.find(a => a.id == currentAgent);
                        if (currentAgentObj && data.agent_name && data.agent_name !== currentAgentObj.name) {
                            return;
                        }
                    }

                    // Transform multi-agent payload to stream event format
                    if (data.payload && data.payload.networks) {
                        data.payload.networks.forEach(net => {
                            net._agent = data.agent_name || 'Unknown';
                            handleStreamEvent({
                                type: 'network_update',
                                network: net
                            });
                        });
                    }
                    if (data.payload && data.payload.clients) {
                        data.payload.clients.forEach(client => {
                            client._agent = data.agent_name || 'Unknown';
                            handleStreamEvent({
                                type: 'client_update',
                                client: client
                            });
                        });
                    }
                } else {
                    // Local stream - tag with local
                    if (data.network) data.network._agent = 'Local';
                    if (data.client) data.client._agent = 'Local';
                    handleStreamEvent(data);
                }
            } catch (error) {
                console.debug('[WiFiMode] Event parse error:', error);
            }
        };

        eventSource.onerror = (error) => {
            console.warn('[WiFiMode] Event stream error:', error);
            if (isScanning) {
                // Attempt to reconnect
                setTimeout(() => {
                    if (isScanning && scanMode === 'deep') {
                        startEventStream();
                    }
                }, 3000);
            }
        };
    }

    function handleStreamEvent(event) {
        switch (event.type) {
            case 'network_update':
                handleNetworkUpdate(event.network);
                break;

            case 'client_update':
                handleClientUpdate(event.client);
                break;

            case 'probe_request':
                handleProbeRequest(event.probe);
                break;

            case 'hidden_revealed':
                handleHiddenRevealed(event.bssid, event.revealed_essid);
                break;

            case 'scan_started':
                console.log('[WiFiMode] Scan started:', event);
                break;

            case 'scan_stopped':
                console.log('[WiFiMode] Scan stopped');
                setScanning(false);
                break;

            case 'scan_error':
                console.error('[WiFiMode] Scan error:', event.error);
                showError(event.error);
                setScanning(false);
                break;

            case 'keepalive':
                // Ignore keepalives
                break;

            default:
                console.debug('[WiFiMode] Unknown event type:', event.type);
        }
    }

    function handleNetworkUpdate(network) {
        networks.set(network.bssid, network);
        scheduleRender({
            table: true,
            stats: true,
            radar: true,
            detail: selectedBssid === network.bssid,
        });

        if (onNetworkUpdate) onNetworkUpdate(network);
    }

    function handleClientUpdate(client) {
        clients.set(client.mac, client);
        scheduleRender({ stats: true });

        // Update client display if this client belongs to the selected network
        updateClientInList(client);

        if (onClientUpdate) onClientUpdate(client);
    }

    function handleProbeRequest(probe) {
        probeRequests.push(probe);
        if (probeRequests.length > CONFIG.maxProbes) {
            probeRequests.shift();
        }

        if (onProbeRequest) onProbeRequest(probe);
    }

    function handleHiddenRevealed(bssid, revealedSsid) {
        const network = networks.get(bssid);
        if (network) {
            network.revealed_essid = revealedSsid;
            network.display_name = `${revealedSsid} (revealed)`;
            scheduleRender({
                table: true,
                detail: selectedBssid === bssid,
            });

            // Show notification
            showInfo(`Hidden SSID revealed: ${revealedSsid}`);
        }
    }

    // ==========================================================================
    // Network Table
    // ==========================================================================

    function initNetworkFilters() {
        if (listenersBound.filters) return;
        if (!elements.networkFilters) return;

        elements.networkFilters.addEventListener('click', (e) => {
            if (e.target.matches('.wifi-filter-btn')) {
                const filter = e.target.dataset.filter;
                setNetworkFilter(filter);
            }
        });
        listenersBound.filters = true;
    }

    function setNetworkFilter(filter) {
        currentFilter = filter;

        // Update button states
        if (elements.networkFilters) {
            elements.networkFilters.querySelectorAll('.wifi-filter-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.filter === filter);
            });
        }

        renderNetworks();
    }

    function initSortControls() {
        if (listenersBound.sort) return;

        document.querySelectorAll('.wifi-sort-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const field = btn.dataset.sort;
                if (currentSort.field === field) {
                    currentSort.order = currentSort.order === 'desc' ? 'asc' : 'desc';
                } else {
                    currentSort.field = field;
                    currentSort.order = 'desc';
                }
                document.querySelectorAll('.wifi-sort-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                scheduleRender({ table: true });
            });
        });

        listenersBound.sort = true;
    }

    function scheduleRender(flags = {}) {
        pendingRender.table = pendingRender.table || Boolean(flags.table);
        pendingRender.stats = pendingRender.stats || Boolean(flags.stats);
        pendingRender.radar = pendingRender.radar || Boolean(flags.radar);
        pendingRender.detail = pendingRender.detail || Boolean(flags.detail);

        if (renderFramePending) return;
        renderFramePending = true;

        requestAnimationFrame(() => {
            renderFramePending = false;

            if (pendingRender.table) renderNetworks();
            if (pendingRender.stats) updateStats();
            if (pendingRender.radar) renderRadar(Array.from(networks.values()));
            if (pendingRender.detail && selectedBssid) {
                updateDetailPanel(selectedBssid, { refreshClients: false });
            }

            pendingRender.table = false;
            pendingRender.stats = false;
            pendingRender.radar = false;
            pendingRender.detail = false;
        });
    }

    function renderNetworks() {
        if (!elements.networkList) return;

        // Snapshot 2.4 GHz channel utilisation (use all networks, not filtered)
        const snapshot = { timestamp: Date.now(), channels: {} };
        for (let ch = 1; ch <= 11; ch++) snapshot.channels[ch] = 0;
        Array.from(networks.values())
            .filter(n => n.band && n.band.startsWith('2.4'))
            .forEach(n => {
                const ch = parseInt(n.channel);
                if (ch >= 1 && ch <= 11) snapshot.channels[ch]++;
            });
        channelHistory.unshift(snapshot);
        if (channelHistory.length > 10) channelHistory.pop();

        // Filter networks
        let filtered = Array.from(networks.values());

        switch (currentFilter) {
            case 'hidden':
                filtered = filtered.filter(n => n.is_hidden);
                break;
            case 'open':
                filtered = filtered.filter(n => n.security === 'Open');
                break;
            case 'strong':
                filtered = filtered.filter(n => n.rssi_current && n.rssi_current >= -60);
                break;
            case '2.4':
                filtered = filtered.filter(n => n.band === '2.4GHz');
                break;
            case '5':
                filtered = filtered.filter(n => n.band === '5GHz');
                break;
        }

        // Sort networks
        filtered.sort((a, b) => {
            let aVal, bVal;

            switch (currentSort.field) {
                case 'rssi':
                    aVal = a.rssi_current || -100;
                    bVal = b.rssi_current || -100;
                    break;
                case 'channel':
                    aVal = a.channel || 0;
                    bVal = b.channel || 0;
                    break;
                case 'essid':
                    aVal = (a.essid || '').toLowerCase();
                    bVal = (b.essid || '').toLowerCase();
                    break;
                case 'clients':
                    aVal = a.client_count || 0;
                    bVal = b.client_count || 0;
                    break;
                default:
                    aVal = a.rssi_current || -100;
                    bVal = b.rssi_current || -100;
            }

            if (currentSort.order === 'desc') {
                return bVal > aVal ? 1 : bVal < aVal ? -1 : 0;
            } else {
                return aVal > bVal ? 1 : aVal < bVal ? -1 : 0;
            }
        });

        if (filtered.length === 0) {
            let message = networks.size > 0
                ? 'No networks match current filters'
                : (isScanning ? 'Scanning for networks...' : 'Start scanning to discover networks');
            elements.networkList.innerHTML = `<div class="wifi-network-placeholder"><p>${escapeHtml(message)}</p></div>`;
            renderHeatmap();
            renderSecurityRing(Array.from(networks.values()));
            return;
        }

        // Render list
        elements.networkList.innerHTML = filtered.map(n => createNetworkRow(n)).join('');

        // Re-apply selected state after re-render
        if (selectedBssid) {
            const sel = elements.networkList.querySelector(`[data-bssid="${CSS.escape(selectedBssid)}"]`);
            if (sel) sel.classList.add('selected');
        }

        renderHeatmap();
        renderSecurityRing(Array.from(networks.values()));
    }

    function createNetworkRow(network) {
        const rssi = network.rssi_current;
        const security = network.security || 'Unknown';

        // Badge class
        const sec = security.toLowerCase();
        const badgeClass = sec === 'open' || sec === ''   ? 'open'
                         : sec.includes('wpa3')            ? 'wpa3'
                         : sec.includes('wpa')             ? 'wpa2'
                         : sec.includes('wep')             ? 'wep'
                         : 'unknown';

        // Threat class (left border)
        const threatClass = badgeClass === 'open' ? 'threat-open'
                          : badgeClass === 'wpa2' || badgeClass === 'wpa3' ? 'threat-safe'
                          : 'threat-hidden';

        // Signal bar width + class
        const pct = rssi != null ? Math.max(0, Math.min(100, (rssi + 100) / 80 * 100)) : 0;
        const fillClass = rssi == null ? 'weak' : rssi > -55 ? 'strong' : rssi > -70 ? 'medium' : 'weak';

        const displayName = escapeHtml(network.display_name || network.essid || '[Hidden]');
        const isHidden = network.is_hidden;
        const hiddenTag = isHidden ? '<span class="badge hidden-tag">HIDDEN</span>' : '';

        return `
            <div class="network-row ${threatClass}"
                 data-bssid="${escapeHtml(network.bssid)}"
                 data-band="${escapeHtml(network.band || '')}"
                 data-security="${escapeHtml(security)}"
                 onclick="WiFiMode.selectNetwork(this.dataset.bssid)">
                <div class="row-top">
                    <span class="row-ssid${isHidden ? ' hidden-net' : ''}">${displayName}</span>
                    <div class="row-badges">
                        <span class="badge ${badgeClass}">${escapeHtml(security)}</span>
                        ${hiddenTag}
                    </div>
                </div>
                <div class="row-bottom">
                    <div class="signal-bar-wrap">
                        <div class="signal-track">
                            <div class="signal-fill ${fillClass}" style="width:${pct.toFixed(1)}%"></div>
                        </div>
                    </div>
                    <div class="row-meta">
                        <span>ch ${network.channel || '?'}</span>
                        <span>${network.client_count || 0} ↔</span>
                        <span class="row-rssi">${rssi != null ? rssi : '?'}</span>
                    </div>
                </div>
            </div>
        `;
    }

    function updateNetworkRow(network) {
        scheduleRender({
            table: true,
            detail: selectedBssid === network.bssid,
        });
    }

    function selectNetwork(bssid) {
        selectedBssid = bssid;

        // Highlight selected row
        elements.networkList?.querySelectorAll('.network-row').forEach(row => {
            row.classList.toggle('selected', row.dataset.bssid === bssid);
        });

        // Show detail in right panel
        if (elements.heatmapView) elements.heatmapView.style.display = 'none';
        if (elements.detailView)  elements.detailView.style.display  = 'flex';
        if (elements.rightPanelTitle) elements.rightPanelTitle.textContent = 'Network Detail';
        if (elements.detailBackBtn)   elements.detailBackBtn.style.display = 'inline-block';

        updateDetailPanel(bssid);
    }

    // ==========================================================================
    // Detail Panel
    // ==========================================================================

    function updateDetailPanel(bssid, options = {}) {
        const { refreshClients = true } = options;

        const network = networks.get(bssid);
        if (!network) {
            closeDetail();
            return;
        }

        // Update detail header
        if (elements.detailEssid) {
            elements.detailEssid.textContent = network.display_name || network.essid || '[Hidden SSID]';
        }
        if (elements.detailBssid) {
            elements.detailBssid.innerHTML = CopyId.html(network.bssid) + DeviceNotes.html(network.bssid, 'wifi');
        }

        // Update detail stats
        if (elements.detailRssi) {
            elements.detailRssi.textContent = network.rssi_current ? `${network.rssi_current} dBm` : '--';
        }
        if (elements.detailChannel) {
            elements.detailChannel.textContent = network.channel || '--';
        }
        if (elements.detailBand) {
            elements.detailBand.textContent = network.band || '--';
        }
        if (elements.detailSecurity) {
            elements.detailSecurity.textContent = network.security || '--';
        }
        if (elements.detailCipher) {
            elements.detailCipher.textContent = network.cipher || '--';
        }
        if (elements.detailVendor) {
            elements.detailVendor.textContent = network.vendor || 'Unknown';
        }
        if (elements.detailClients) {
            elements.detailClients.textContent = network.client_count || '0';
        }
        if (elements.detailFirstSeen) {
            elements.detailFirstSeen.textContent = formatTime(network.first_seen);
        }

        // Update signal bar
        if (elements.detailSignalFill) {
            const rssi = network.rssi_current;
            const pct = rssi != null ? Math.max(0, Math.min(100, (rssi + 100) / 80 * 100)) : 0;
            elements.detailSignalFill.style.width = pct.toFixed(1) + '%';
        }

        // Fetch and display clients for this network
        if (refreshClients) {
            fetchClientsForNetwork(network.bssid);
        }
    }

    function closeDetail() {
        selectedBssid = null;

        // Deselect all rows
        elements.networkList?.querySelectorAll('.network-row').forEach(row => {
            row.classList.remove('selected');
        });

        // Restore heatmap in right panel
        if (elements.detailView)  elements.detailView.style.display  = 'none';
        if (elements.heatmapView) elements.heatmapView.style.display = 'flex';
        if (elements.rightPanelTitle) elements.rightPanelTitle.textContent = 'Channel Heatmap';
        if (elements.detailBackBtn)   elements.detailBackBtn.style.display = 'none';
    }

    // ==========================================================================
    // Client Display
    // ==========================================================================

    async function fetchClientsForNetwork(bssid) {
        if (!elements.detailClientList) return;
        const listContainer = elements.detailClientList.querySelector('.wifi-client-list');

        if (listContainer && typeof renderCollectionState === 'function') {
            renderCollectionState(listContainer, { type: 'loading', message: 'Loading clients...' });
            elements.detailClientList.style.display = 'block';
        }

        try {
            const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
            let response;

            if (isAgentMode) {
                // Route through agent proxy
                response = await fetch(`/controller/agents/${currentAgent}/proxy/wifi/v2/clients?bssid=${encodeURIComponent(bssid)}&associated=true`);
            } else {
                response = await fetch(`${CONFIG.apiBase}/clients?bssid=${encodeURIComponent(bssid)}&associated=true`);
            }

            if (!response.ok) {
                if (listContainer && typeof renderCollectionState === 'function') {
                    renderCollectionState(listContainer, { type: 'empty', message: 'Client list unavailable' });
                    elements.detailClientList.style.display = 'block';
                } else {
                    elements.detailClientList.style.display = 'none';
                }
                return;
            }

            const data = await response.json();
            // Handle agent response format (may be nested in 'result')
            const result = isAgentMode && data.result ? data.result : data;
            // /wifi/v2/clients returns a bare array; tolerate {clients: [...]} too
            const clientList = Array.isArray(result) ? result : (result.clients || []);

            if (clientList.length > 0) {
                renderClientList(clientList, bssid);
                elements.detailClientList.style.display = 'block';
            } else {
                const countBadge = document.getElementById('wifiClientCountBadge');
                if (countBadge) countBadge.textContent = '0';
                if (listContainer && typeof renderCollectionState === 'function') {
                    renderCollectionState(listContainer, { type: 'empty', message: 'No associated clients' });
                    elements.detailClientList.style.display = 'block';
                } else {
                    elements.detailClientList.style.display = 'none';
                }
            }
        } catch (error) {
            console.debug('[WiFiMode] Error fetching clients:', error);
            if (listContainer && typeof renderCollectionState === 'function') {
                renderCollectionState(listContainer, { type: 'empty', message: 'Client list unavailable' });
                elements.detailClientList.style.display = 'block';
            } else {
                elements.detailClientList.style.display = 'none';
            }
        }
    }

    function renderClientList(clientList, bssid) {
        const container = elements.detailClientList?.querySelector('.wifi-client-list');
        const countBadge = document.getElementById('wifiClientCountBadge');

        if (!container) return;

        // Update count badge
        if (countBadge) {
            countBadge.textContent = clientList.length;
        }

        // Render client cards
        container.innerHTML = clientList.map(client => {
            const rssi = client.rssi_current;
            const signalClass = rssi >= -50 ? 'signal-strong' :
                               rssi >= -70 ? 'signal-medium' :
                               rssi >= -85 ? 'signal-weak' : 'signal-very-weak';

            // Format last seen time
            const lastSeen = InterceptTime.relTimeHtml(client.last_seen) || '--';

            // Build probed SSIDs badges
            let probesHtml = '';
            if (client.probed_ssids && client.probed_ssids.length > 0) {
                const probes = client.probed_ssids.slice(0, 5); // Show max 5
                probesHtml = `
                    <div class="wifi-client-probes">
                        ${probes.map(ssid => `<span class="wifi-client-probe-badge">${escapeHtml(ssid)}</span>`).join('')}
                        ${client.probed_ssids.length > 5 ? `<span class="wifi-client-probe-badge">+${client.probed_ssids.length - 5}</span>` : ''}
                    </div>
                `;
            }

            return `
                <div class="wifi-client-card" data-mac="${escapeHtml(client.mac)}">
                    <div class="wifi-client-identity">
                        <span class="wifi-client-mac">${escapeHtml(client.mac)}</span>
                        <span class="wifi-client-vendor">${escapeHtml(client.vendor || 'Unknown vendor')}</span>
                        ${probesHtml}
                    </div>
                    <div class="wifi-client-signal">
                        <span class="wifi-client-rssi ${signalClass}">${rssi !== null && rssi !== undefined ? rssi + ' dBm' : '--'}</span>
                        <span class="wifi-client-lastseen">${lastSeen}</span>
                    </div>
                </div>
            `;
        }).join('');
    }

    function updateClientInList(client) {
        // Check if this client belongs to the currently selected network
        if (!selectedBssid || client.associated_bssid !== selectedBssid) {
            return;
        }

        const container = elements.detailClientList?.querySelector('.wifi-client-list');
        if (!container) return;

        const existingCard = container.querySelector(`[data-mac="${CSS.escape(client.mac)}"]`);

        if (existingCard) {
            // Update existing card's RSSI and last seen
            const rssiEl = existingCard.querySelector('.wifi-client-rssi');
            const lastSeenEl = existingCard.querySelector('.wifi-client-lastseen');

            if (rssiEl && client.rssi_current !== null && client.rssi_current !== undefined) {
                const rssi = client.rssi_current;
                const signalClass = rssi >= -50 ? 'signal-strong' :
                                   rssi >= -70 ? 'signal-medium' :
                                   rssi >= -85 ? 'signal-weak' : 'signal-very-weak';
                rssiEl.textContent = rssi + ' dBm';
                rssiEl.className = 'wifi-client-rssi ' + signalClass;
            }

            if (lastSeenEl && client.last_seen) {
                lastSeenEl.innerHTML = InterceptTime.relTimeHtml(client.last_seen);
            }
        } else {
            // New client for this network - re-fetch the full list
            fetchClientsForNetwork(selectedBssid);
        }
    }

    // ==========================================================================
    // Statistics
    // ==========================================================================

    function updateStats() {
        const networksList = Array.from(networks.values());

        // Update counts in status bar
        if (elements.networkCount) {
            elements.networkCount.textContent = networks.size;
        }
        if (elements.clientCount) {
            elements.clientCount.textContent = clients.size;
        }
        if (elements.hiddenCount) {
            const hidden = networksList.filter(n => n.is_hidden).length;
            elements.hiddenCount.textContent = hidden;
        }

        // Update security counts
        const securityCounts = { wpa3: 0, wpa2: 0, wep: 0, open: 0 };
        networksList.forEach(n => {
            const sec = (n.security || '').toLowerCase();
            if (sec.includes('wpa3')) securityCounts.wpa3++;
            else if (sec.includes('wpa2') || sec.includes('wpa')) securityCounts.wpa2++;
            else if (sec.includes('wep')) securityCounts.wep++;
            else if (sec === 'open' || sec === '') securityCounts.open++;
        });

        if (elements.openCount) elements.openCount.textContent = securityCounts.open;
    }

    // ==========================================================================
    // Proximity Radar
    // ==========================================================================

    // Simple hash of BSSID string → stable angle in radians
    function bssidToAngle(bssid) {
        let hash = 0;
        for (let i = 0; i < bssid.length; i++) {
            hash = (hash * 31 + bssid.charCodeAt(i)) & 0xffffffff;
        }
        return (hash >>> 0) / 0x100000000 * 2 * Math.PI;
    }

    function renderRadar(networksList) {
        const dotsGroup = document.getElementById('wifiRadarDots');
        if (!dotsGroup) return;

        const dots = [];
        const zoneCounts = { immediate: 0, near: 0, far: 0 };

        networksList.forEach(network => {
            const rssi = network.rssi_current ?? -100;
            const strength = Math.max(0, Math.min(1, (rssi + 100) / 80));
            const dotR = 5 + (1 - strength) * 90; // stronger = closer to centre
            const angle = bssidToAngle(network.bssid);
            const cx = 105 + dotR * Math.cos(angle);
            const cy = 105 + dotR * Math.sin(angle);

            // Zone counts
            if (dotR < 35)       zoneCounts.immediate++;
            else if (dotR < 70)  zoneCounts.near++;
            else                  zoneCounts.far++;

            // Visual radius by zone
            const vr = dotR < 35 ? 6 : dotR < 70 ? 4.5 : 3;

            // Colour by security
            const sec = (network.security || '').toLowerCase();
            const colour = sec === 'open' || sec === '' ? '#e25d5d'
                         : sec.includes('wpa')         ? '#38c180'
                         : sec.includes('wep')         ? '#d6a85e'
                         : '#484f58';

            dots.push(`
            <circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${vr * 1.5}"
                    fill="${colour}" opacity="0.12"/>
            <circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${vr}"
                    fill="${colour}" opacity="0.9" filter="url(#wifi-glow-sm)"/>
        `);
        });

        dotsGroup.innerHTML = dots.join('');

        if (elements.zoneImmediate) elements.zoneImmediate.textContent = zoneCounts.immediate;
        if (elements.zoneNear)      elements.zoneNear.textContent      = zoneCounts.near;
        if (elements.zoneFar)       elements.zoneFar.textContent       = zoneCounts.far;
    }

    // ==========================================================================
    // Channel Chart
    // ==========================================================================

    function initHeatmap() {
        if (!elements.heatmapChLabels) return;
        // Time-label placeholder + 11 channel labels
        elements.heatmapChLabels.innerHTML =
            '<div class="wifi-heatmap-ch-label"></div>' +
            [1,2,3,4,5,6,7,8,9,10,11].map(ch =>
                `<div class="wifi-heatmap-ch-label">${ch}</div>`
            ).join('');
    }

    function renderHeatmap() {
        if (!elements.heatmapGrid) return;

        if (channelHistory.length === 0) {
            elements.heatmapGrid.innerHTML =
                '<div class="wifi-heatmap-empty">Scan to populate channel history</div>';
            if (elements.heatmapCount) elements.heatmapCount.textContent = '0';
            return;
        }

        if (elements.heatmapCount) elements.heatmapCount.textContent = channelHistory.length;

        // Find max value for colour scale
        let maxVal = 1;
        channelHistory.forEach(snap => {
            Object.values(snap.channels).forEach(v => { if (v > maxVal) maxVal = v; });
        });

        const rows = channelHistory.map((snap, i) => {
            const timeLabel = i === 0 ? 'now' : '';
            const cells = [1,2,3,4,5,6,7,8,9,10,11].map(ch => {
                const v = snap.channels[ch] || 0;
                return `<div class="wifi-heatmap-cell" style="background:${congestionColor(v, maxVal)}"></div>`;
            });
            return `<div class="wifi-heatmap-time-label">${timeLabel}</div>${cells.join('')}`;
        });

        elements.heatmapGrid.innerHTML = rows.join('');
    }

    function congestionColor(value, maxValue) {
        if (value === 0 || maxValue === 0) return '#0d1117';
        const ratio = value / maxValue;
        if (ratio < 0.05)  return '#0d1117';
        if (ratio < 0.25)  return `rgba(13,74,110,${(ratio * 4).toFixed(2)})`;
        if (ratio < 0.5)   return `rgba(14,165,233,${ratio.toFixed(2)})`;
        if (ratio < 0.75)  return `rgba(249,115,22,${ratio.toFixed(2)})`;
        return                     `rgba(239,68,68,${ratio.toFixed(2)})`;
    }

    function renderSecurityRing(networksList) {
        const svg = elements.securityRingSvg;
        const legend = elements.securityRingLegend;
        if (!svg || !legend) return;

        const C = 2 * Math.PI * 15; // circumference ≈ 94.25
        const sec = networksList.reduce((acc, n) => {
            const s = (n.security || '').toLowerCase();
            if (s.includes('wpa3'))       acc.wpa3++;
            else if (s.includes('wpa'))   acc.wpa2++;
            else if (s.includes('wep'))   acc.wep++;
            else                          acc.open++;
            return acc;
        }, { wpa2: 0, open: 0, wpa3: 0, wep: 0 });

        const total = networksList.length || 1;
        const segments = [
            { label: 'WPA2', color: '#38c180', count: sec.wpa2 },
            { label: 'Open', color: '#e25d5d', count: sec.open },
            { label: 'WPA3', color: '#4aa3ff', count: sec.wpa3 },
            { label: 'WEP',  color: '#d6a85e', count: sec.wep  },
        ];

        let offset = 0;
        const arcs = segments.map(seg => {
            const arcLen = (seg.count / total) * C;
            const arc = `<circle cx="24" cy="24" r="15" fill="none"
                stroke="${seg.color}" stroke-width="7"
                stroke-dasharray="${arcLen.toFixed(2)} ${(C - arcLen).toFixed(2)}"
                stroke-dashoffset="${(-offset).toFixed(2)}"
                transform="rotate(-90 24 24)"/>`;
            offset += arcLen;
            return arc;
        });

        svg.innerHTML = arcs.join('') +
            '<circle cx="24" cy="24" r="9" fill="var(--bg-primary)"/>';

        legend.innerHTML = segments.map(seg => `
            <div class="wifi-security-ring-item">
                <div class="wifi-security-ring-dot" style="background:${seg.color}"></div>
                <span class="wifi-security-ring-name">${seg.label}</span>
                <span class="wifi-security-ring-count">${seg.count}</span>
            </div>
        `).join('');
    }

    // ==========================================================================
    // Export
    // ==========================================================================

    async function exportData(format) {
        try {
            const response = await fetch(`${CONFIG.apiBase}/export?format=${format}&type=all`);
            if (!response.ok) throw new Error('Export failed');

            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `wifi_scan_${new Date().toISOString().slice(0, 19).replace(/[:-]/g, '')}.${format}`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        } catch (error) {
            console.error('[WiFiMode] Export error:', error);
            showError('Export failed: ' + error.message);
        }
    }

    // ==========================================================================
    // Utilities
    // ==========================================================================

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function formatTime(isoString) {
        if (!isoString) return '-';
        const date = new Date(isoString);
        return date.toLocaleTimeString();
    }

    function showError(message) {
        // Use global notification if available
        if (typeof showNotification === 'function') {
            showNotification('WiFi Error', message, 'error');
        } else {
            console.error('[WiFiMode]', message);
        }
    }

    function showInfo(message) {
        if (typeof showNotification === 'function') {
            showNotification('WiFi', message, 'info');
        } else {
            console.log('[WiFiMode]', message);
        }
    }

    // ==========================================================================
    // Agent Handling
    // ==========================================================================

    /**
     * Handle agent change - refresh interfaces and optionally clear data.
     * Called when user selects a different agent.
     */
    function handleAgentChange() {
        const currentAgentId = typeof currentAgent !== 'undefined' ? currentAgent : 'local';

        // Check if agent actually changed
        if (lastAgentId === currentAgentId) return;

        console.log('[WiFiMode] Agent changed from', lastAgentId, 'to', currentAgentId);

        // Stop UI polling only - don't stop the actual scan on the agent
        // The agent should continue running independently
        if (isScanning) {
            stopAgentDeepScanPolling();
            if (pollTimer) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
            if (eventSource) {
                eventSource.close();
                eventSource = null;
            }
            setScanning(false);
        }

        // Clear existing data when switching agents (unless "Show All" is enabled)
        if (!showAllAgentsMode) {
            clearData();
            showInfo(`Switched to ${getCurrentAgentName()} - previous data cleared`);
        }

        // Refresh capabilities for new agent
        checkCapabilities();

        // Check if new agent already has a scan running
        checkScanStatus();

        lastAgentId = currentAgentId;
    }

    /**
     * Clear all collected data.
     */
    function clearData() {
        networks.clear();
        clients.clear();
        probeRequests = [];
        channelStats = [];
        recommendations = [];
        if (selectedBssid) {
            closeDetail();
        }
        scheduleRender({ table: true, stats: true, radar: true });
    }

    /**
     * Toggle "Show All Agents" mode.
     * When enabled, displays combined WiFi results from all agents.
     */
    function toggleShowAllAgents(enabled) {
        showAllAgentsMode = enabled;
        console.log('[WiFiMode] Show all agents mode:', enabled);

        if (enabled) {
            // If currently scanning, switch to multi-agent stream
            if (isScanning && eventSource) {
                eventSource.close();
                startEventStream();
            }
            showInfo('Showing WiFi networks from all agents');
        } else {
            // Filter to current agent only
            filterToCurrentAgent();
        }
    }

    /**
     * Filter networks to only show those from current agent.
     */
    function filterToCurrentAgent() {
        const agentName = getCurrentAgentName();
        const toRemove = [];

        networks.forEach((network, bssid) => {
            if (network._agent && network._agent !== agentName) {
                toRemove.push(bssid);
            }
        });

        toRemove.forEach(bssid => networks.delete(bssid));

        // Also filter clients
        const clientsToRemove = [];
        clients.forEach((client, mac) => {
            if (client._agent && client._agent !== agentName) {
                clientsToRemove.push(mac);
            }
        });
        clientsToRemove.forEach(mac => clients.delete(mac));
        if (selectedBssid && !networks.has(selectedBssid)) {
            closeDetail();
        }
        scheduleRender({ table: true, stats: true, radar: true });
    }

    /**
     * Refresh WiFi interfaces from current agent.
     * Called when agent changes.
     */
    async function refreshInterfaces() {
        await checkCapabilities();
    }

    // ==========================================================================
    // Public API
    // ==========================================================================

    return {
        init,
        startQuickScan,
        startDeepScan,
        stopScan,
        selectNetwork,
        closeDetail,
        setFilter: setNetworkFilter,
        exportData,
        checkCapabilities,

        // Agent handling
        handleAgentChange,
        clearData,
        toggleShowAllAgents,
        refreshInterfaces,

        // Getters
        getNetworks: () => Array.from(networks.values()),
        getClients: () => Array.from(clients.values()),
        getProbes: () => [...probeRequests],
        isScanning: () => isScanning,
        getScanMode: () => scanMode,
        isShowAllAgents: () => showAllAgentsMode,

        // Callbacks
        onNetworkUpdate: (cb) => { onNetworkUpdate = cb; },
        onClientUpdate: (cb) => { onClientUpdate = cb; },
        onProbeRequest: (cb) => { onProbeRequest = cb; },

        // Lifecycle
        destroy,
    };

    /**
     * Destroy — close SSE stream and clear polling timers for clean mode switching.
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
        if (agentPollTimer) {
            clearInterval(agentPollTimer);
            agentPollTimer = null;
        }
    }
})();

// Auto-initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Only init if we're in WiFi mode
    if (typeof currentMode !== 'undefined' && currentMode === 'wifi') {
        WiFiMode.init();
    }
});
