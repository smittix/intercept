/**
 * Meshtastic Mode
 * Mesh network monitoring and configuration
 */

const Meshtastic = (function() {
    // State
    let isConnected = false;
    let eventSource = null;
    let messages = [];
    let channels = [];
    let nodeInfo = null;
    let uniqueNodes = new Set();
    let currentFilter = '';
    let editingChannelIndex = null;

    // Map state
    let meshMap = null;
    let meshMarkers = {};  // nodeId -> marker
    let localNodeId = null;
    let clickDelegationAttached = false;

    /**
     * Initialize the Meshtastic mode
     */
    function init() {
        initMap();
        loadPorts();
        checkStatus();
        setupEventDelegation();
    }

    /**
     * Setup event delegation for dynamically created elements
     */
    function setupEventDelegation() {
        if (clickDelegationAttached) return;
        clickDelegationAttached = true;

        // Handle button clicks in Leaflet popups and elsewhere
        document.addEventListener('click', function(e) {
            const tracerouteBtn = e.target.closest('.mesh-traceroute-btn');
            if (tracerouteBtn) {
                const nodeId = tracerouteBtn.dataset.nodeId;
                if (nodeId) {
                    sendTraceroute(nodeId);
                }
            }

            const positionBtn = e.target.closest('.mesh-position-btn');
            if (positionBtn) {
                const nodeId = positionBtn.dataset.nodeId;
                if (nodeId) {
                    requestPosition(nodeId);
                }
            }

            const qrBtn = e.target.closest('.mesh-qr-btn');
            if (qrBtn) {
                const channelIndex = qrBtn.dataset.channelIndex;
                if (channelIndex !== undefined) {
                    showChannelQR(parseInt(channelIndex, 10));
                }
            }
        });
    }

    /**
     * Load available serial ports and populate dropdown
     */
    async function loadPorts() {
        try {
            const response = await fetch('/meshtastic/ports');
            const data = await response.json();

            const select = document.getElementById('meshStripDevice');
            if (!select) return;

            // Clear existing options except auto-detect
            select.innerHTML = '<option value="">Auto-detect</option>';

            if (data.status === 'ok' && data.ports && data.ports.length > 0) {
                data.ports.forEach(port => {
                    const option = document.createElement('option');
                    option.value = port;
                    option.textContent = port;
                    select.appendChild(option);
                });

                // If multiple ports, select the first one by default to avoid auto-detect failure
                if (data.ports.length > 1) {
                    select.value = data.ports[0];
                    showStatusMessage(`Multiple ports detected. Selected ${data.ports[0]}`, 'warning');
                }
            }
        } catch (err) {
            console.error('Failed to load ports:', err);
        }
    }

    /**
     * Initialize the Leaflet map
     */
    async function initMap() {
        if (meshMap) return;

        const mapContainer = document.getElementById('meshMap');
        if (!mapContainer) return;

        // Default to center of US
        const defaultLat = 39.8283;
        const defaultLon = -98.5795;

        meshMap = L.map('meshMap').setView([defaultLat, defaultLon], 4);
        window.meshMap = meshMap;

        // Add fallback tiles immediately so the map is visible instantly. If Settings
        // already loaded elsewhere this session, use it directly instead — avoids a
        // flash of the unkeyed CartoDB URL when a CARTO API key has been configured.
        const fallbackTiles = (typeof Settings !== 'undefined' && Settings._initialized && Settings.createTileLayer)
            ? Settings.createTileLayer().addTo(meshMap)
            : L.tileLayer('https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png', {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
                maxZoom: 19,
                subdomains: 'abcd',
                className: 'tile-layer-cyan'
            }).addTo(meshMap);

        // Upgrade tiles in background via Settings (with timeout fallback)
        if (typeof Settings !== 'undefined') {
            try {
                await Promise.race([
                    Settings.init(),
                    new Promise((_, reject) => setTimeout(() => reject(new Error('Settings timeout')), 5000))
                ]);
                meshMap.removeLayer(fallbackTiles);
                Settings.createTileLayer().addTo(meshMap);
                Settings.registerMap(meshMap);
            } catch (e) {
                console.warn('Meshtastic: Settings init failed/timed out, using fallback tiles:', e);
            }
        }

        if (typeof MapUtils !== 'undefined') MapUtils.addGraticuleControl(meshMap);

        // Handle resize
        setTimeout(() => {
            if (meshMap) meshMap.invalidateSize();
        }, 100);
    }

    /**
     * Check current connection status
     */
    async function checkStatus() {
        try {
            const response = await fetch('/meshtastic/status');
            const data = await response.json();

            if (!data.available) {
                showStatusMessage('SDK not installed. Install with: pip install meshtastic', 'warning');
                return;
            }

            if (data.running) {
                isConnected = true;
                updateConnectionUI(true, data.device, data.connection_type);
                if (data.node_info) {
                    updateNodeInfo(data.node_info);
                    localNodeId = data.node_info.num;
                }
                loadChannels();
                loadMessages();
                loadNodes();
                startStream();
            }
        } catch (err) {
            console.error('Failed to check Meshtastic status:', err);
        }
    }

    /**
     * Handle connection type change (serial vs TCP)
     */
    function onConnectionTypeChange() {
        const connTypeSelect = document.getElementById('meshStripConnType');
        const deviceSelect = document.getElementById('meshStripDevice');
        const hostnameInput = document.getElementById('meshStripHostname');

        if (!connTypeSelect) return;

        const connType = connTypeSelect.value;

        if (connType === 'tcp') {
            // Show hostname input, hide device select
            if (deviceSelect) deviceSelect.style.display = 'none';
            if (hostnameInput) hostnameInput.style.display = 'block';
        } else {
            // Show device select, hide hostname input
            if (deviceSelect) deviceSelect.style.display = 'block';
            if (hostnameInput) hostnameInput.style.display = 'none';
        }
    }

    /**
     * Start Meshtastic connection
     */
    async function start() {
        // Get connection type
        const connTypeSelect = document.getElementById('meshStripConnType');
        const connectionType = connTypeSelect?.value || 'serial';

        // Get connection parameters based on type
        let device = null;
        let hostname = null;

        if (connectionType === 'tcp') {
            // TCP connection - get hostname
            const hostnameInput = document.getElementById('meshStripHostname');
            hostname = hostnameInput?.value?.trim() || null;

            if (!hostname) {
                showStatusMessage('Please enter a hostname or IP address for TCP connection', 'error');
                updateStatusIndicator('disconnected', 'Enter hostname');
                return;
            }
        } else {
            // Serial connection - get device
            const stripDeviceSelect = document.getElementById('meshStripDevice');
            const sidebarDeviceSelect = document.getElementById('meshDeviceSelect');
            device = stripDeviceSelect?.value || sidebarDeviceSelect?.value || null;

            // Check if auto-detect is selected but multiple ports exist
            if (!device && stripDeviceSelect && stripDeviceSelect.options.length > 2) {
                // Multiple ports available - prompt user to select one
                showStatusMessage('Multiple ports detected. Please select a specific device from the dropdown.', 'warning');
                updateStatusIndicator('disconnected', 'Select a device');
                return;
            }
        }

        updateStatusIndicator('connecting', 'Connecting...');

        // Update strip status
        const stripDot = document.getElementById('meshStripDot');
        const stripStatus = document.getElementById('meshStripStatus');
        if (stripDot) stripDot.className = 'mesh-strip-dot connecting';
        if (stripStatus) stripStatus.textContent = 'Connecting...';

        try {
            const requestBody = {
                connection_type: connectionType
            };

            if (connectionType === 'tcp') {
                requestBody.hostname = hostname;
            } else if (device) {
                requestBody.device = device;
            }

            const response = await fetch('/meshtastic/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody)
            });

            const data = await response.json();

            if (data.status === 'started' || data.status === 'already_running') {
                isConnected = true;
                updateConnectionUI(true, data.device, data.connection_type);
                if (data.node_info) {
                    updateNodeInfo(data.node_info);
                    localNodeId = data.node_info.num;
                }
                loadChannels();
                loadNodes();
                startStream();
                const connLabel = data.connection_type === 'tcp' ? 'TCP' : 'Serial';
                showNotification('Meshtastic', `Connected via ${connLabel}`);
            } else {
                updateStatusIndicator('disconnected', data.message || 'Connection failed');
                showStatusMessage(data.message || 'Failed to connect', 'error');
            }
        } catch (err) {
            console.error('Failed to start Meshtastic:', err);
            reportActionableError('Start Meshtastic', err, {
                onRetry: () => start()
            });
            updateStatusIndicator('disconnected', 'Connection error');
        }
    }

    /**
     * Stop Meshtastic connection
     */
    async function stop() {
        try {
            await fetch('/meshtastic/stop', { method: 'POST' });
            isConnected = false;
            stopStream();
            updateConnectionUI(false);
            showNotification('Meshtastic', 'Disconnected');
        } catch (err) {
            console.error('Failed to stop Meshtastic:', err);
            reportActionableError('Stop Meshtastic', err);
        }
    }

    /**
     * Update connection UI state
     */
    function updateConnectionUI(connected, device, connectionType) {
        const connectBtn = document.getElementById('meshConnectBtn');
        const disconnectBtn = document.getElementById('meshDisconnectBtn');
        const nodeSection = document.getElementById('meshNodeSection');
        const channelsSection = document.getElementById('meshChannelsSection');
        const statsSection = document.getElementById('meshStatsSection');
        const filterSection = document.getElementById('meshFilterSection');
        const composeBox = document.getElementById('meshCompose');

        // Strip controls
        const stripConnectBtn = document.getElementById('meshStripConnectBtn');
        const stripDisconnectBtn = document.getElementById('meshStripDisconnectBtn');
        const stripDot = document.getElementById('meshStripDot');
        const stripStatus = document.getElementById('meshStripStatus');

        if (connected) {
            const connLabel = connectionType === 'tcp' ? 'TCP' : 'Serial';
            const statusText = device ? `${device} (${connLabel})` : `Connected (${connLabel})`;
            updateStatusIndicator('connected', statusText);
            if (connectBtn) connectBtn.style.display = 'none';
            if (disconnectBtn) disconnectBtn.style.display = 'block';
            if (nodeSection) nodeSection.style.display = 'block';
            if (channelsSection) channelsSection.style.display = 'block';
            if (statsSection) statsSection.style.display = 'block';
            if (filterSection) filterSection.style.display = 'block';
            if (composeBox) composeBox.style.display = 'block';

            // Update strip
            if (stripConnectBtn) stripConnectBtn.style.display = 'none';
            if (stripDisconnectBtn) stripDisconnectBtn.style.display = 'inline-block';
            if (stripDot) {
                stripDot.className = 'mesh-strip-dot connected';
            }
            if (stripStatus) stripStatus.textContent = statusText;
        } else {
            updateStatusIndicator('disconnected', 'Disconnected');
            if (connectBtn) connectBtn.style.display = 'block';
            if (disconnectBtn) disconnectBtn.style.display = 'none';
            if (nodeSection) nodeSection.style.display = 'none';
            if (channelsSection) channelsSection.style.display = 'none';
            if (statsSection) statsSection.style.display = 'none';
            if (filterSection) filterSection.style.display = 'none';
            if (composeBox) composeBox.style.display = 'none';

            // Reset strip
            if (stripConnectBtn) stripConnectBtn.style.display = 'inline-block';
            if (stripDisconnectBtn) stripDisconnectBtn.style.display = 'none';
            if (stripDot) {
                stripDot.className = 'mesh-strip-dot disconnected';
            }
            if (stripStatus) stripStatus.textContent = 'Disconnected';

            // Reset strip node info
            const stripNodeName = document.getElementById('meshStripNodeName');
            const stripNodeId = document.getElementById('meshStripNodeId');
            const stripModel = document.getElementById('meshStripModel');
            if (stripNodeName) stripNodeName.textContent = '--';
            if (stripNodeId) stripNodeId.textContent = '--';
            if (stripModel) stripModel.textContent = '--';
        }
    }

    /**
     * Update status indicator
     */
    function updateStatusIndicator(status, text) {
        const dot = document.querySelector('.mesh-status-dot');
        const textEl = document.getElementById('meshStatusText');

        if (dot) {
            dot.classList.remove('connected', 'connecting', 'disconnected');
            dot.classList.add(status);
        }
        if (textEl) {
            textEl.textContent = text;
        }
    }

    /**
     * Update node info display
     */
    function updateNodeInfo(info) {
        nodeInfo = info;

        // Sidebar elements
        const nameEl = document.getElementById('meshNodeName');
        const idEl = document.getElementById('meshNodeId');
        const modelEl = document.getElementById('meshNodeModel');
        const posRow = document.getElementById('meshNodePosRow');
        const posEl = document.getElementById('meshNodePosition');

        // Strip elements
        const stripNodeName = document.getElementById('meshStripNodeName');
        const stripNodeId = document.getElementById('meshStripNodeId');
        const stripModel = document.getElementById('meshStripModel');
        const stripSatsRow = document.getElementById('meshStripSatsRow');
        const stripSats = document.getElementById('meshStripSats');

        const nodeName = info.long_name || info.short_name || '--';
        const nodeId = info.user_id || formatNodeId(info.num) || '--';
        const hwModel = info.hw_model || '--';

        // Update sidebar
        if (nameEl) nameEl.textContent = nodeName;
        if (idEl) idEl.textContent = nodeId;
        if (modelEl) modelEl.textContent = hwModel;

        // Update strip
        if (stripNodeName) stripNodeName.textContent = nodeName;
        if (stripNodeId) stripNodeId.textContent = nodeId;
        if (stripModel) stripModel.textContent = hwModel;

        // Position is nested in the response
        const pos = info.position;
        if (pos && pos.latitude !== undefined && pos.latitude !== null && pos.longitude !== undefined && pos.longitude !== null) {
            if (posRow) posRow.style.display = 'flex';
            if (posEl) posEl.textContent = `${pos.latitude.toFixed(5)}, ${pos.longitude.toFixed(5)}`;
        } else {
            if (posRow) posRow.style.display = 'none';
        }

        const sats = pos && pos.sats_in_view;
        if (sats !== undefined && sats !== null) {
            if (stripSatsRow) stripSatsRow.style.display = '';
            if (stripSats) stripSats.textContent = String(sats);
        } else if (stripSatsRow) {
            stripSatsRow.style.display = 'none';
        }
    }

    /**
     * Load channels from device
     */
    async function loadChannels() {
        try {
            const response = await fetch('/meshtastic/channels');
            const data = await response.json();

            if (data.status === 'ok') {
                channels = data.channels;
                renderChannels();
                updateChannelFilter();
                updateComposeChannels();
            }
        } catch (err) {
            console.error('Failed to load channels:', err);
        }
    }

    /**
     * Render channel list
     */
    function renderChannels() {
        const container = document.getElementById('meshChannelsList');
        if (!container) return;

        if (channels.length === 0) {
            container.innerHTML = '<p style="color: var(--text-dim); font-size: 11px; text-align: center; padding: 20px;">No channels configured</p>';
            return;
        }

        container.innerHTML = channels.map(ch => {
            const isDisabled = !ch.name && ch.role === 'DISABLED';
            const roleBadge = ch.role === 'PRIMARY' ? 'mesh-badge-primary' : 'mesh-badge-secondary';
            const encBadge = ch.encrypted ? 'mesh-badge-encrypted' : 'mesh-badge-unencrypted';
            const encText = ch.encrypted ? (ch.psk_length === 32 ? 'AES-256' : ch.psk_length === 16 ? 'AES-128' : 'ENCRYPTED') : 'NONE';

            return `
                <div class="mesh-channel-item ${isDisabled ? 'disabled' : ''}">
                    <div class="mesh-channel-info">
                        <span class="mesh-channel-index">${ch.index}</span>
                        <span class="mesh-channel-name">${ch.name || (isDisabled ? '(disabled)' : '(unnamed)')}</span>
                    </div>
                    <div class="mesh-channel-badges">
                        <span class="mesh-channel-badge ${roleBadge}">${ch.role || 'SECONDARY'}</span>
                        <span class="mesh-channel-badge ${encBadge}">${encText}</span>
                        <button class="mesh-qr-btn" data-channel-index="${ch.index}" title="Generate QR Code">QR</button>
                        <button class="mesh-channel-configure" onclick="Meshtastic.openChannelModal(${ch.index})">Configure</button>
                    </div>
                </div>
            `;
        }).join('');
    }

    /**
     * Refresh channels
     */
    function refreshChannels() {
        loadChannels();
    }

    /**
     * Open channel configuration modal
     */
    function openChannelModal(index) {
        editingChannelIndex = index;
        const channel = channels.find(ch => ch.index === index);

        const modal = document.getElementById('meshChannelModal');
        const indexEl = document.getElementById('meshModalChannelIndex');
        const nameInput = document.getElementById('meshModalChannelName');
        const pskFormat = document.getElementById('meshModalPskFormat');

        if (indexEl) indexEl.textContent = index;
        if (nameInput) nameInput.value = channel?.name || '';
        if (pskFormat) pskFormat.value = 'keep';

        onPskFormatChange();

        if (modal) modal.classList.add('show');
    }

    /**
     * Close channel configuration modal
     */
    function closeChannelModal() {
        const modal = document.getElementById('meshChannelModal');
        if (modal) modal.classList.remove('show');
        editingChannelIndex = null;
    }

    /**
     * Handle PSK format change
     */
    function onPskFormatChange() {
        const format = document.getElementById('meshModalPskFormat')?.value;
        const inputContainer = document.getElementById('meshModalPskInputContainer');
        const pskInput = document.getElementById('meshModalPskValue');
        const warning = document.getElementById('meshModalPskWarning');

        // Show input for formats that need a value
        const needsInput = ['simple', 'base64', 'hex'].includes(format);
        if (inputContainer) inputContainer.style.display = needsInput ? 'block' : 'none';

        // Update placeholder based on format
        if (pskInput) {
            const placeholders = {
                'simple': 'Enter passphrase...',
                'base64': 'Enter base64 key...',
                'hex': 'Enter hex key (0x...)...'
            };
            pskInput.placeholder = placeholders[format] || '';
            pskInput.value = '';
        }

        // Show warning for default key
        if (warning) warning.style.display = format === 'default' ? 'block' : 'none';
    }

    /**
     * Save channel configuration
     */
    async function saveChannelConfig() {
        if (editingChannelIndex === null) return;

        const nameInput = document.getElementById('meshModalChannelName');
        const pskFormat = document.getElementById('meshModalPskFormat')?.value;
        const pskValue = document.getElementById('meshModalPskValue')?.value;

        const body = {};
        const name = nameInput?.value.trim();
        if (name) body.name = name;

        // Build PSK value based on format
        if (pskFormat && pskFormat !== 'keep') {
            switch (pskFormat) {
                case 'none':
                    body.psk = 'none';
                    break;
                case 'default':
                    body.psk = 'default';
                    break;
                case 'random':
                    body.psk = 'random';
                    break;
                case 'simple':
                    if (pskValue) body.psk = 'simple:' + pskValue;
                    break;
                case 'base64':
                    if (pskValue) body.psk = 'base64:' + pskValue;
                    break;
                case 'hex':
                    if (pskValue) body.psk = pskValue.startsWith('0x') ? pskValue : '0x' + pskValue;
                    break;
            }
        }

        if (Object.keys(body).length === 0) {
            closeChannelModal();
            return;
        }

        try {
            const response = await fetch(`/meshtastic/channels/${editingChannelIndex}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            const data = await response.json();

            if (data.status === 'ok') {
                showNotification('Meshtastic', 'Channel configured successfully');
                closeChannelModal();
                loadChannels();
            } else {
                showStatusMessage(data.message || 'Failed to configure channel', 'error');
            }
        } catch (err) {
            console.error('Failed to configure channel:', err);
            reportActionableError('Configure Channel', err, {
                onRetry: () => saveChannel()
            });
        }
    }

    /**
     * Load message history
     */
    async function loadMessages(limit) {
        try {
            let url = '/meshtastic/messages';
            const params = new URLSearchParams();
            if (limit) params.set('limit', limit);
            if (currentFilter) params.set('channel', currentFilter);
            if (params.toString()) url += '?' + params.toString();

            const response = await fetch(url);
            const data = await response.json();

            if (data.status === 'ok') {
                messages = data.messages;
                data.messages.forEach(msg => {
                    if (msg.from) uniqueNodes.add(msg.from);
                });
                updateStats();
                renderMessages();
            }
        } catch (err) {
            console.error('Failed to load messages:', err);
        }
    }

    /**
     * Load nodes and update map
     */
    async function loadNodes() {
        try {
            const response = await fetch('/meshtastic/nodes');
            const data = await response.json();

            if (data.status === 'ok') {
                updateMapStats(data.count, data.with_position_count);

                // Update markers for all nodes with positions
                data.nodes.forEach(node => {
                    // Track node in uniqueNodes set for stats
                    if (node.num) uniqueNodes.add(node.num);

                    if (node.has_position) {
                        updateNodeMarker(node);
                    }
                });

                // Update stats to reflect loaded nodes
                updateStats();

                // Fit map to show all nodes if we have any
                const nodesWithPos = data.nodes.filter(n => n.has_position);
                if (nodesWithPos.length > 0 && meshMap) {
                    const bounds = nodesWithPos.map(n => [n.latitude, n.longitude]);
                    if (bounds.length === 1) {
                        meshMap.setView(bounds[0], 12);
                    } else {
                        meshMap.fitBounds(bounds, { padding: [50, 50] });
                    }
                }
            }
        } catch (err) {
            console.error('Failed to load nodes:', err);
        }
    }

    /**
     * Update or create a node marker on the map
     */
    function updateNodeMarker(node) {
        if (!meshMap || !node.latitude || !node.longitude) return;

        const nodeId = node.id || `!${node.num.toString(16).padStart(8, '0')}`;
        const isLocal = node.num === localNodeId;

        // Determine if node is stale (no update in 30 minutes)
        let isStale = false;
        if (node.last_heard) {
            const lastHeard = new Date(node.last_heard);
            const now = new Date();
            isStale = (now - lastHeard) > 30 * 60 * 1000;
        }

        // Create marker icon
        const markerClass = `mesh-node-marker ${isLocal ? 'local' : ''} ${isStale ? 'stale' : ''}`;
        const shortName = node.short_name || nodeId.slice(-4);

        const icon = L.divIcon({
            className: 'mesh-marker-wrapper',
            html: `<div class="${markerClass}">${shortName.slice(0, 2).toUpperCase()}</div>`,
            iconSize: [32, 32],
            iconAnchor: [16, 16],
            popupAnchor: [0, -16]
        });

        // Build telemetry section
        let telemetryHtml = '';
        if (node.voltage !== null || node.channel_utilization !== null || node.air_util_tx !== null) {
            telemetryHtml += '<div style="margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--border-color);">';
            telemetryHtml += '<span style="color: var(--text-dim); font-size: 9px; text-transform: uppercase;">Device Telemetry</span><br>';
            if (node.voltage !== null) {
                telemetryHtml += `<span style="color: var(--text-dim);">Voltage:</span> ${node.voltage.toFixed(2)}V<br>`;
            }
            if (node.channel_utilization !== null) {
                telemetryHtml += `<span style="color: var(--text-dim);">Ch Util:</span> ${node.channel_utilization.toFixed(1)}%<br>`;
            }
            if (node.air_util_tx !== null) {
                telemetryHtml += `<span style="color: var(--text-dim);">Air TX:</span> ${node.air_util_tx.toFixed(1)}%<br>`;
            }
            telemetryHtml += '</div>';
        }

        // Build environment section
        let envHtml = '';
        if (node.temperature !== null || node.humidity !== null || node.barometric_pressure !== null) {
            envHtml += '<div style="margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--border-color);">';
            envHtml += '<span style="color: var(--text-dim); font-size: 9px; text-transform: uppercase;">Environment</span><br>';
            if (node.temperature !== null) {
                telemetryHtml += `<span style="color: var(--text-dim);">Temp:</span> ${node.temperature.toFixed(1)}°C<br>`;
            }
            if (node.humidity !== null) {
                envHtml += `<span style="color: var(--text-dim);">Humidity:</span> ${node.humidity.toFixed(1)}%<br>`;
            }
            if (node.barometric_pressure !== null) {
                envHtml += `<span style="color: var(--text-dim);">Pressure:</span> ${node.barometric_pressure.toFixed(1)} hPa<br>`;
            }
            envHtml += '</div>';
        }

        // Build popup content with action buttons
        let actionButtons = '';
        if (!isLocal) {
            actionButtons = `
                <div style="margin-top: 8px; display: flex; gap: 4px; flex-wrap: wrap;">
                    <button class="mesh-traceroute-btn" data-node-id="${nodeId}">Traceroute</button>
                    <button class="mesh-position-btn" data-node-id="${nodeId}">Request Position</button>
                    <button class="mesh-telemetry-btn" onclick="Meshtastic.showTelemetryChart('${nodeId}')">Telemetry</button>
                </div>
            `;
        }

        const popupContent = `
            <div style="min-width: 150px;">
                <strong style="color: var(--accent-cyan);">${node.long_name || shortName}</strong><br>
                <span style="color: var(--text-dim);">ID:</span> ${nodeId}<br>
                <span style="color: var(--text-dim);">Model:</span> ${node.hw_model || 'Unknown'}<br>
                <span style="color: var(--text-dim);">Position:</span> ${node.latitude.toFixed(5)}, ${node.longitude.toFixed(5)}<br>
                ${node.altitude ? `<span style="color: var(--text-dim);">Altitude:</span> ${node.altitude}m<br>` : ''}
                ${node.sats_in_view !== null && node.sats_in_view !== undefined ? `<span style="color: var(--text-dim);">Sats:</span> ${node.sats_in_view}<br>` : ''}
                ${node.battery_level !== null ? `<span style="color: var(--text-dim);">Battery:</span> ${node.battery_level}%<br>` : ''}
                ${node.snr !== null ? `<span style="color: var(--text-dim);">SNR:</span> ${node.snr.toFixed(1)} dB<br>` : ''}
                ${node.last_heard ? `<span style="color: var(--text-dim);">Last heard:</span> ${new Date(node.last_heard).toLocaleTimeString()}<br>` : ''}
                ${telemetryHtml}
                ${envHtml}
                ${actionButtons}
            </div>
        `;

        // Update or create marker
        if (meshMarkers[nodeId]) {
            meshMarkers[nodeId].setLatLng([node.latitude, node.longitude]);
            meshMarkers[nodeId].setIcon(icon);
            meshMarkers[nodeId].setPopupContent(popupContent);
        } else {
            const marker = L.marker([node.latitude, node.longitude], { icon })
                .bindPopup(popupContent)
                .addTo(meshMap);
            meshMarkers[nodeId] = marker;
        }
    }

    /**
     * Update map stats display
     */
    function updateMapStats(total, withGps) {
        const totalEl = document.getElementById('meshMapNodeCount');
        const gpsEl = document.getElementById('meshMapGpsCount');
        if (totalEl) totalEl.textContent = total;
        if (gpsEl) gpsEl.textContent = withGps;
    }

    /**
     * Start SSE stream
     */
    function startStream() {
        if (eventSource) {
            eventSource.close();
        }

        eventSource = new EventSource('/meshtastic/stream');

        eventSource.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'meshtastic') {
                    handleMessage(data);
                }
            } catch (err) {
                console.error('Failed to parse SSE message:', err);
            }
        };

        eventSource.onerror = () => {
            console.warn('Meshtastic SSE error, will reconnect...');
            setTimeout(() => {
                if (isConnected) startStream();
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
     * Handle incoming message
     */
    function handleMessage(msg) {
        console.log('Received message:', msg);
        console.log('from_name:', msg.from_name, 'timestamp:', msg.timestamp, 'type:', typeof msg.timestamp);
        messages.push(msg);
        if (msg.from) uniqueNodes.add(msg.from);

        // Keep messages limited
        if (messages.length > 500) {
            messages.shift();
        }

        updateStats();

        // Only render if passes filter
        if (!currentFilter || msg.channel == currentFilter) {
            prependMessage(msg);
        }

        // Refresh nodes if we got position or nodeinfo data
        const portnum = msg.portnum || msg.app_type || '';
        if (portnum.includes('POSITION') || portnum.includes('NODEINFO')) {
            // Debounce node refresh to avoid too many requests
            clearTimeout(handleMessage._nodeRefreshTimeout);
            handleMessage._nodeRefreshTimeout = setTimeout(() => {
                loadNodes();
            }, 2000);
        }
    }

    /**
     * Update statistics display
     */
    function updateStats() {
        // Sidebar stats
        const msgCountEl = document.getElementById('meshMsgCount');
        const nodeCountEl = document.getElementById('meshNodeCount');

        // Strip stats
        const stripMsgCount = document.getElementById('meshStripMsgCount');
        const stripNodeCount = document.getElementById('meshStripNodeCount');

        const msgCount = messages.length;
        const nodeCount = uniqueNodes.size;

        if (msgCountEl) msgCountEl.textContent = msgCount;
        if (nodeCountEl) nodeCountEl.textContent = nodeCount;
        if (stripMsgCount) stripMsgCount.textContent = msgCount;
        if (stripNodeCount) stripNodeCount.textContent = nodeCount;
    }

    /**
     * Render all messages
     */
    function renderMessages() {
        const container = document.getElementById('meshMessagesGrid');
        if (!container) return;

        const filtered = currentFilter
            ? messages.filter(m => m.channel == currentFilter)
            : messages;

        if (filtered.length === 0) {
            container.innerHTML = `
                <div class="mesh-messages-empty">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"/>
                        <circle cx="12" cy="12" r="3"/>
                        <path d="M12 2v4m0 12v4M2 12h4m12 0h4"/>
                    </svg>
                    <p>No messages received yet</p>
                </div>
            `;
            return;
        }

        container.innerHTML = filtered
            .slice()
            .reverse()
            .map(msg => renderMessageCard(msg))
            .join('');
    }

    /**
     * Prepend a single message to the feed
     */
    function prependMessage(msg) {
        const container = document.getElementById('meshMessagesGrid');
        if (!container) return;

        // Remove empty state if present
        const empty = container.querySelector('.mesh-messages-empty');
        if (empty) empty.remove();

        const card = document.createElement('div');
        card.innerHTML = renderMessageCard(msg);
        container.insertBefore(card.firstElementChild, container.firstChild);

        // Limit displayed messages
        while (container.children.length > 100) {
            container.lastElementChild.remove();
        }
    }

    /**
     * Render a single message card
     */
    function renderMessageCard(msg) {
        const typeClass = getMessageTypeClass(msg.app_type || msg.portnum);
        // Use name if available, fall back to ID
        const fromDisplay = msg.from_name || formatNodeId(msg.from);
        const toDisplay = msg.to === 'broadcast' || msg.to === '^all'
            ? '<span class="broadcast">^all</span>'
            : (msg.to_name || formatNodeId(msg.to));

        const time = msg.timestamp
            ? new Date(msg.timestamp * 1000).toLocaleTimeString()
            : '--:--:--';

        let body;
        if (msg.text) {
            body = `<div class="mesh-message-body">${escapeHtml(msg.text)}</div>`;
        } else {
            body = `<div class="mesh-message-body app-type">[${msg.app_type || msg.portnum || 'UNKNOWN'}]</div>`;
        }

        let signalInfo = '';
        if (msg.rssi != null || msg.snr != null) {
            const rssiHtml = msg.rssi != null
                ? `<div class="mesh-signal-item"><span class="mesh-signal-label">RSSI</span><span class="mesh-signal-value rssi">${msg.rssi}dBm</span></div>`
                : '';
            const snrClass = msg.snr != null ? (msg.snr < 0 ? 'bad' : msg.snr < 5 ? 'poor' : '') : '';
            const snrHtml = msg.snr != null
                ? `<div class="mesh-signal-item"><span class="mesh-signal-label">SNR</span><span class="mesh-signal-value snr ${snrClass}">${msg.snr.toFixed(1)}</span></div>`
                : '';
            signalInfo = `<div class="mesh-message-signal">${rssiHtml}${snrHtml}</div>`;
        }

        // Handle pending/sent messages
        const isPending = msg._pending;
        const isFailed = msg._failed;
        const pendingClass = isPending ? 'pending' : (isFailed ? 'failed' : '');
        const pendingAttr = isPending ? 'data-pending="true"' : '';

        // Status indicator for sent messages
        let statusIndicator = '';
        if (isPending) {
            statusIndicator = '<span class="mesh-message-status sending">Sending...</span>';
        } else if (isFailed) {
            statusIndicator = '<span class="mesh-message-status failed">Failed</span>';
        }

        return `
            <div class="mesh-message-card ${typeClass} ${pendingClass}" ${pendingAttr}>
                <div class="mesh-message-header">
                    <div class="mesh-message-route">
                        <span class="mesh-message-from">${fromDisplay}</span>
                        <span class="mesh-message-arrow">-></span>
                        <span class="mesh-message-to">${toDisplay}</span>
                        ${statusIndicator}
                    </div>
                    <div class="mesh-message-meta">
                        <span class="mesh-message-channel">[CH${msg.channel !== undefined ? msg.channel : '?'}]</span>
                        <span class="mesh-message-time">${time}</span>
                    </div>
                </div>
                ${body}
                ${signalInfo}
            </div>
        `;
    }

    /**
     * Get message type CSS class
     */
    function getMessageTypeClass(appType) {
        if (!appType) return '';
        const type = appType.toLowerCase();
        if (type.includes('text')) return 'text-message';
        if (type.includes('position')) return 'position-message';
        if (type.includes('telemetry')) return 'telemetry-message';
        if (type.includes('nodeinfo')) return 'nodeinfo-message';
        return '';
    }

    /**
     * Format node ID for display
     */
    function formatNodeId(id) {
        if (!id) return '--';
        if (typeof id === 'number') {
            return '!' + id.toString(16).padStart(8, '0');
        }
        if (typeof id === 'string' && !id.startsWith('!') && !id.startsWith('^')) {
            // Try to format as hex if it's a numeric string
            const num = parseInt(id, 10);
            if (!isNaN(num)) {
                return '!' + num.toString(16).padStart(8, '0');
            }
        }
        return id;
    }

    /**
     * Apply message filter
     */
    function applyFilter() {
        // Read from either filter dropdown (sidebar or visuals header)
        const sidebarFilter = document.getElementById('meshChannelFilter');
        const visualsFilter = document.getElementById('meshVisualsFilter');

        // Use whichever one has a value, preferring the one that was just changed
        const value = sidebarFilter?.value || visualsFilter?.value || '';
        currentFilter = value;

        // Sync both dropdowns
        if (sidebarFilter) sidebarFilter.value = value;
        if (visualsFilter) visualsFilter.value = value;

        renderMessages();
    }

    /**
     * Update channel filter dropdowns
     */
    function updateChannelFilter() {
        const selects = [
            document.getElementById('meshChannelFilter'),
            document.getElementById('meshVisualsFilter')
        ];

        selects.forEach(select => {
            if (!select) return;
            const currentValue = select.value;
            select.innerHTML = '<option value="">All Channels</option>';

            channels.forEach(ch => {
                if (ch.name || ch.role === 'PRIMARY') {
                    const option = document.createElement('option');
                    option.value = ch.index;
                    option.textContent = `[${ch.index}] ${ch.name || 'Primary'}`;
                    select.appendChild(option);
                }
            });

            select.value = currentValue;
        });
    }

    /**
     * Escape HTML for safe display
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Show status message
     */
    function showStatusMessage(message, type) {
        if (typeof showNotification === 'function') {
            showNotification('Meshtastic', message);
        } else {
            console.log(`[Meshtastic ${type}] ${message}`);
        }
    }

    /**
     * Show help modal
     */
    function showHelp() {
        let modal = document.getElementById('meshtasticHelpModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'meshtasticHelpModal';
            modal.className = 'signal-details-modal';
            document.body.appendChild(modal);
        }

        modal.innerHTML = `
            <div class="signal-details-modal-backdrop" onclick="Meshtastic.closeHelp()"></div>
            <div class="signal-details-modal-content">
                <div class="signal-details-modal-header">
                    <h3>About Meshtastic</h3>
                    <button class="signal-details-modal-close" onclick="Meshtastic.closeHelp()">&times;</button>
                </div>
                <div class="signal-details-modal-body">
                    <div class="signal-details-section">
                        <div class="signal-details-title">What is Meshtastic?</div>
                        <p style="color: var(--text-secondary); font-size: 12px; line-height: 1.6;">
                            Meshtastic is an open-source mesh networking platform for LoRa radios. It enables
                            long-range, low-power communication between devices without requiring cellular or WiFi
                            infrastructure. Messages hop through the mesh to reach their destination.
                        </p>
                    </div>
                    <div class="signal-details-section">
                        <div class="signal-details-title">Supported Hardware</div>
                        <p style="color: var(--text-secondary); font-size: 12px; line-height: 1.6;">
                            Common Meshtastic devices include Heltec LoRa32, LILYGO T-Beam, RAK WisBlock, and
                            many others. Connect your device via USB to start monitoring the mesh.
                        </p>
                    </div>
                    <div class="signal-details-section">
                        <div class="signal-details-title">Channel Encryption</div>
                        <ul style="color: var(--text-secondary); font-size: 12px; line-height: 1.6; padding-left: 20px;">
                            <li><strong>None:</strong> Messages are unencrypted (not recommended)</li>
                            <li><strong>Default:</strong> Uses a known public key (NOT SECURE)</li>
                            <li><strong>Random:</strong> Generates a new AES-256 key</li>
                            <li><strong>Passphrase:</strong> Derives a key from a passphrase</li>
                            <li><strong>Base64/Hex:</strong> Use your own pre-shared key</li>
                        </ul>
                    </div>
                    <div class="signal-details-section">
                        <div class="signal-details-title">Requirements</div>
                        <p style="color: var(--text-secondary); font-size: 12px; line-height: 1.6;">
                            Install the Meshtastic Python SDK: <code style="background: var(--bg-secondary); padding: 2px 6px; border-radius: 3px;">pip install meshtastic</code>
                        </p>
                    </div>
                </div>
            </div>
        `;

        modal.classList.add('show');
    }

    /**
     * Close help modal
     */
    function closeHelp() {
        const modal = document.getElementById('meshtasticHelpModal');
        if (modal) modal.classList.remove('show');
    }

    /**
     * Handle keydown in compose input
     */
    function handleComposeKeydown(event) {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    }

    /**
     * Send a message to the mesh
     */
    async function sendMessage() {
        const textInput = document.getElementById('meshComposeText');
        const channelSelect = document.getElementById('meshComposeChannel');
        const toInput = document.getElementById('meshComposeTo');
        const sendBtn = document.querySelector('.mesh-compose-send');

        const text = textInput?.value.trim();
        if (!text) return;

        const channel = parseInt(channelSelect?.value || '0', 10);
        const toValue = toInput?.value.trim();
        // Convert empty or "^all" to null for broadcast
        const to = (toValue && toValue !== '^all') ? toValue : null;

        // Show sending state immediately
        if (sendBtn) {
            sendBtn.disabled = true;
            sendBtn.classList.add('sending');
        }

        // Optimistically add message to feed immediately
        const localNodeName = nodeInfo?.short_name || nodeInfo?.long_name || null;
        const localNodeIdStr = nodeInfo ? formatNodeId(nodeInfo.num) : '!local';
        const optimisticMsg = {
            type: 'meshtastic',
            from: localNodeIdStr,
            from_name: localNodeName,
            to: to || '^all',
            text: text,
            channel: channel,
            timestamp: Date.now() / 1000,
            portnum: 'TEXT_MESSAGE_APP',
            _pending: true  // Mark as pending
        };

        // Add to messages and render
        messages.push(optimisticMsg);
        prependMessage(optimisticMsg);

        // Clear input immediately for snappy feel
        const sentText = text;
        textInput.value = '';
        updateCharCount();

        try {
            console.log('Sending message:', { text: sentText, channel, to });
            const response = await fetch('/meshtastic/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: sentText, channel, to: to || undefined })
            });

            console.log('Send response status:', response.status);

            if (!response.ok) {
                // HTTP error
                let errorMsg = `HTTP ${response.status}`;
                try {
                    const errData = await response.json();
                    errorMsg = errData.message || errorMsg;
                } catch (e) {
                    // Response wasn't JSON
                }
                throw new Error(errorMsg);
            }

            const data = await response.json();
            console.log('Send response data:', data);

            if (data.status === 'sent') {
                // Mark optimistic message as confirmed
                optimisticMsg._pending = false;
                updatePendingMessage(optimisticMsg, false);
            } else {
                // Mark as failed
                optimisticMsg._failed = true;
                updatePendingMessage(optimisticMsg, true);
                if (typeof showNotification === 'function') {
                    showNotification('Meshtastic', data.message || 'Failed to send');
                }
            }
        } catch (err) {
            console.error('Failed to send message:', err);
            reportActionableError('Send Message', err, {
                onRetry: () => sendMessage()
            });
            optimisticMsg._failed = true;
            updatePendingMessage(optimisticMsg, true);
        } finally {
            if (sendBtn) {
                sendBtn.disabled = false;
                sendBtn.classList.remove('sending');
            }
            textInput?.focus();
        }
    }

    /**
     * Update a pending message's visual state
     */
    function updatePendingMessage(msg, failed) {
        // Find the message card and update its state
        const cards = document.querySelectorAll('.mesh-message-card');
        cards.forEach(card => {
            if (card.dataset.pending === 'true') {
                card.classList.remove('pending');
                card.dataset.pending = 'false';

                // Update the status indicator
                const statusEl = card.querySelector('.mesh-message-status');
                if (statusEl) {
                    if (failed) {
                        statusEl.className = 'mesh-message-status failed';
                        statusEl.textContent = 'Failed';
                    } else {
                        // Remove the status indicator on success
                        statusEl.remove();
                    }
                }

                if (failed) {
                    card.classList.add('failed');
                } else {
                    card.classList.add('sent');
                    // Remove sent indicator after a moment
                    setTimeout(() => card.classList.remove('sent'), 2000);
                }
            }
        });
    }

    /**
     * Update character count display
     */
    function updateCharCount() {
        const input = document.getElementById('meshComposeText');
        const counter = document.getElementById('meshComposeCount');
        if (input && counter) {
            counter.textContent = input.value.length;
        }
    }

    /**
     * Update compose channel dropdown
     */
    function updateComposeChannels() {
        const select = document.getElementById('meshComposeChannel');
        if (!select) return;

        select.innerHTML = channels.map(ch => {
            if (ch.role === 'DISABLED') return '';
            const name = ch.name || (ch.role === 'PRIMARY' ? 'Primary' : `CH ${ch.index}`);
            return `<option value="${ch.index}">${name}</option>`;
        }).filter(Boolean).join('');

        // Default to first channel (usually primary)
        if (channels.length > 0) {
            select.value = channels[0].index;
        }
    }

    // Public API
    /**
     * Toggle main sidebar collapsed state
     */
    /**
     * Toggle the main application sidebar visibility
     */
    function toggleSidebar() {
        const mainContent = document.querySelector('.main-content');
        if (mainContent) {
            mainContent.classList.toggle('mesh-sidebar-hidden');
            // Resize map after sidebar toggle
            setTimeout(() => {
                if (meshMap) meshMap.invalidateSize();
            }, 100);
        }
    }

    /**
     * Toggle the Meshtastic options panel within the sidebar
     */
    function toggleOptionsPanel() {
        const modePanel = document.getElementById('meshtasticMode');
        const icon = document.getElementById('meshSidebarIcon');
        if (modePanel) {
            modePanel.classList.toggle('mesh-sidebar-collapsed');
            if (icon) {
                icon.textContent = modePanel.classList.contains('mesh-sidebar-collapsed') ? '▶' : '▼';
            }
        }
    }

    /**
     * Send traceroute to a node
     */
    async function sendTraceroute(destination) {
        if (!destination) return;

        // Show traceroute modal with loading state
        showTracerouteModal(destination, null, true);

        try {
            const response = await fetch('/meshtastic/traceroute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ destination, hop_limit: 7 })
            });

            const data = await response.json();

            if (data.status === 'sent') {
                // Start polling for results
                pollTracerouteResults(destination);
            } else {
                showTracerouteModal(destination, { error: data.message || 'Failed to send traceroute' }, false);
            }
        } catch (err) {
            console.error('Traceroute error:', err);
            reportActionableError('Send Traceroute', err, {
                onRetry: () => sendTraceroute(destination)
            });
            showTracerouteModal(destination, { error: err.message }, false);
        }
    }

    /**
     * Poll for traceroute results
     */
    async function pollTracerouteResults(destination, attempts = 0) {
        const maxAttempts = 30;  // 30 seconds timeout
        const pollInterval = 1000;

        if (attempts >= maxAttempts) {
            showTracerouteModal(destination, { error: 'Traceroute timeout - no response received' }, false);
            return;
        }

        try {
            const response = await fetch('/meshtastic/traceroute/results?limit=5');
            const data = await response.json();

            if (data.status === 'ok' && data.results) {
                // Find result matching our destination
                const result = data.results.find(r => r.destination_id === destination);
                if (result) {
                    showTracerouteModal(destination, result, false);
                    return;
                }
            }

            // Continue polling
            setTimeout(() => pollTracerouteResults(destination, attempts + 1), pollInterval);
        } catch (err) {
            console.error('Error polling traceroute:', err);
            setTimeout(() => pollTracerouteResults(destination, attempts + 1), pollInterval);
        }
    }

    /**
     * Show traceroute modal
     */
    function showTracerouteModal(destination, result, loading) {
        let modal = document.getElementById('meshTracerouteModal');
        if (!modal) return;

        const destEl = document.getElementById('meshTracerouteDest');
        const contentEl = document.getElementById('meshTracerouteContent');

        if (destEl) destEl.textContent = destination;

        if (loading) {
            contentEl.innerHTML = `
                <div class="mesh-traceroute-loading">
                    <div class="mesh-traceroute-spinner"></div>
                    <p>Waiting for traceroute response...</p>
                </div>
            `;
        } else if (result && result.error) {
            contentEl.innerHTML = `
                <div class="mesh-traceroute-error">
                    <p>Error: ${escapeHtml(result.error)}</p>
                </div>
            `;
        } else if (result) {
            contentEl.innerHTML = renderTracerouteVisualization(result);
        }

        modal.classList.add('show');
    }

    /**
     * Close traceroute modal
     */
    function closeTracerouteModal() {
        const modal = document.getElementById('meshTracerouteModal');
        if (modal) modal.classList.remove('show');
    }

    /**
     * Render traceroute visualization
     */
    function renderTracerouteVisualization(result) {
        if (!result.route || result.route.length === 0) {
            if (result.route_back && result.route_back.length > 0) {
                // Only have return path - show it
                return renderRoutePath('Return Path', result.route_back, result.snr_back);
            }
            return '<p style="color: var(--text-dim);">Direct connection (no intermediate hops)</p>';
        }

        let html = '';

        // Forward route
        if (result.route && result.route.length > 0) {
            html += renderRoutePath('Forward Path', result.route, result.snr_towards);
        }

        // Return route
        if (result.route_back && result.route_back.length > 0) {
            html += renderRoutePath('Return Path', result.route_back, result.snr_back);
        }

        // Timestamp
        if (result.timestamp) {
            html += `<div class="mesh-traceroute-timestamp">Completed: ${new Date(result.timestamp).toLocaleString()}</div>`;
        }

        return html;
    }

    /**
     * Render a single route path
     */
    function renderRoutePath(label, route, snrValues) {
        let html = `<div class="mesh-traceroute-section">
            <div class="mesh-traceroute-label">${label}</div>
            <div class="mesh-traceroute-path">`;

        route.forEach((nodeId, index) => {
            // Look up node name if available
            const nodeName = lookupNodeName(nodeId) || nodeId.slice(-4);
            const snr = snrValues && snrValues[index] !== undefined ? snrValues[index] : null;
            const snrClass = snr !== null ? getSnrClass(snr) : '';

            html += `<div class="mesh-traceroute-hop">
                <div class="mesh-traceroute-hop-node">${escapeHtml(nodeName)}</div>
                <div class="mesh-traceroute-hop-id">${nodeId}</div>
                ${snr !== null ? `<div class="mesh-traceroute-snr ${snrClass}">${snr.toFixed(1)} dB</div>` : ''}
            </div>`;

            // Add arrow between hops
            if (index < route.length - 1) {
                html += '<div class="mesh-traceroute-arrow">→</div>';
            }
        });

        html += '</div></div>';
        return html;
    }

    /**
     * Get SNR quality class
     */
    function getSnrClass(snr) {
        if (snr >= 10) return 'snr-good';
        if (snr >= 0) return 'snr-ok';
        if (snr >= -10) return 'snr-poor';
        return 'snr-bad';
    }

    /**
     * Look up node name from our tracked nodes
     */
    function lookupNodeName(nodeId) {
        // This would ideally look up from our cached nodes
        // For now, return null to use ID
        return null;
    }

    /**
     * Request position from a specific node
     */
    async function requestPosition(nodeId) {
        if (!nodeId) return;

        try {
            const response = await fetch('/meshtastic/position/request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ node_id: nodeId })
            });

            const data = await response.json();

            if (data.status === 'sent') {
                showNotification('Meshtastic', `Position requested from ${nodeId}`);
                // Refresh nodes after a delay to get updated position
                setTimeout(loadNodes, 5000);
            } else {
                showStatusMessage(data.message || 'Failed to request position', 'error');
            }
        } catch (err) {
            console.error('Position request error:', err);
            reportActionableError('Request Position', err, {
                onRetry: () => requestPosition(nodeId)
            });
        }
    }

    /**
     * Check firmware version and show update status
     */
    async function checkFirmware() {
        try {
            const response = await fetch('/meshtastic/firmware/check');
            const data = await response.json();

            if (data.status === 'ok') {
                showFirmwareModal(data);
            } else {
                showStatusMessage(data.message || 'Failed to check firmware', 'error');
            }
        } catch (err) {
            console.error('Firmware check error:', err);
            showStatusMessage('Error checking firmware: ' + err.message, 'error');
        }
    }

    /**
     * Show firmware information modal
     */
    function showFirmwareModal(info) {
        let modal = document.getElementById('meshFirmwareModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'meshFirmwareModal';
            modal.className = 'signal-details-modal';
            document.body.appendChild(modal);
        }

        const updateBadge = info.update_available
            ? '<span class="mesh-badge mesh-badge-warning">Update Available</span>'
            : '<span class="mesh-badge mesh-badge-success">Up to Date</span>';

        modal.innerHTML = `
            <div class="signal-details-modal-backdrop" onclick="Meshtastic.closeFirmwareModal()"></div>
            <div class="signal-details-modal-content">
                <div class="signal-details-modal-header">
                    <h3>Firmware Information</h3>
                    <button class="signal-details-modal-close" onclick="Meshtastic.closeFirmwareModal()">&times;</button>
                </div>
                <div class="signal-details-modal-body">
                    <div class="signal-details-section">
                        <div class="signal-details-title">Current Version</div>
                        <p style="color: var(--text-secondary); font-size: 14px;">
                            ${info.current_version || 'Unknown'}
                        </p>
                    </div>
                    <div class="signal-details-section">
                        <div class="signal-details-title">Latest Version</div>
                        <p style="color: var(--text-secondary); font-size: 14px;">
                            ${info.latest_version || 'Unknown'} ${updateBadge}
                        </p>
                    </div>
                    ${info.release_url ? `
                    <div class="signal-details-section">
                        <a href="${info.release_url}" target="_blank" rel="noopener" class="preset-btn" style="display: inline-block; text-decoration: none;">
                            View Release Notes
                        </a>
                    </div>
                    ` : ''}
                    ${info.error ? `
                    <div class="signal-details-section">
                        <p style="color: var(--status-error); font-size: 12px;">
                            Note: ${info.error}
                        </p>
                    </div>
                    ` : ''}
                </div>
            </div>
        `;

        modal.classList.add('show');
    }

    /**
     * Close firmware modal
     */
    function closeFirmwareModal() {
        const modal = document.getElementById('meshFirmwareModal');
        if (modal) modal.classList.remove('show');
    }

    /**
     * Show QR code for a channel
     */
    async function showChannelQR(channelIndex) {
        let modal = document.getElementById('meshQRModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'meshQRModal';
            modal.className = 'signal-details-modal';
            document.body.appendChild(modal);
        }

        const channel = channels.find(ch => ch.index === channelIndex);
        const channelName = channel?.name || `Channel ${channelIndex}`;

        // Show loading state
        modal.innerHTML = `
            <div class="signal-details-modal-backdrop" onclick="Meshtastic.closeQRModal()"></div>
            <div class="signal-details-modal-content">
                <div class="signal-details-modal-header">
                    <h3>Channel QR Code</h3>
                    <button class="signal-details-modal-close" onclick="Meshtastic.closeQRModal()">&times;</button>
                </div>
                <div class="signal-details-modal-body">
                    <div style="text-align: center; padding: 20px;">
                        <div class="mesh-traceroute-spinner"></div>
                        <p style="color: var(--text-dim); margin-top: 10px;">Generating QR code...</p>
                    </div>
                </div>
            </div>
        `;
        modal.classList.add('show');

        try {
            const response = await fetch(`/meshtastic/channels/${channelIndex}/qr`);

            if (response.ok) {
                const blob = await response.blob();
                const imageUrl = URL.createObjectURL(blob);

                modal.innerHTML = `
                    <div class="signal-details-modal-backdrop" onclick="Meshtastic.closeQRModal()"></div>
                    <div class="signal-details-modal-content">
                        <div class="signal-details-modal-header">
                            <h3>Channel QR Code</h3>
                            <button class="signal-details-modal-close" onclick="Meshtastic.closeQRModal()">&times;</button>
                        </div>
                        <div class="signal-details-modal-body" style="text-align: center;">
                            <p style="color: var(--text-secondary); font-size: 14px; margin-bottom: 15px;">
                                ${escapeHtml(channelName)}
                            </p>
                            <img src="${imageUrl}" alt="Channel QR Code" style="max-width: 256px; background: white; padding: 10px; border-radius: 8px;">
                            <p style="color: var(--text-dim); font-size: 11px; margin-top: 15px;">
                                Scan with the Meshtastic app to join this channel
                            </p>
                        </div>
                    </div>
                `;
            } else {
                const data = await response.json();
                throw new Error(data.message || 'Failed to generate QR code');
            }
        } catch (err) {
            console.error('QR generation error:', err);
            modal.innerHTML = `
                <div class="signal-details-modal-backdrop" onclick="Meshtastic.closeQRModal()"></div>
                <div class="signal-details-modal-content">
                    <div class="signal-details-modal-header">
                        <h3>Channel QR Code</h3>
                        <button class="signal-details-modal-close" onclick="Meshtastic.closeQRModal()">&times;</button>
                    </div>
                    <div class="signal-details-modal-body">
                        <p style="color: var(--status-error);">
                            Error: ${escapeHtml(err.message)}
                        </p>
                        <p style="color: var(--text-dim); font-size: 11px; margin-top: 10px;">
                            Make sure the qrcode library is installed: pip install qrcode[pil]
                        </p>
                    </div>
                </div>
            `;
        }
    }

    /**
     * Close QR modal
     */
    function closeQRModal() {
        const modal = document.getElementById('meshQRModal');
        if (modal) modal.classList.remove('show');
    }

    /**
     * Load and display telemetry history for a node
     */
    async function showTelemetryChart(nodeId, hours = 24) {
        let modal = document.getElementById('meshTelemetryModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'meshTelemetryModal';
            modal.className = 'signal-details-modal';
            document.body.appendChild(modal);
        }

        // Show loading
        modal.innerHTML = `
            <div class="signal-details-modal-backdrop" onclick="Meshtastic.closeTelemetryModal()"></div>
            <div class="signal-details-modal-content" style="max-width: 600px;">
                <div class="signal-details-modal-header">
                    <h3>Telemetry History</h3>
                    <button class="signal-details-modal-close" onclick="Meshtastic.closeTelemetryModal()">&times;</button>
                </div>
                <div class="signal-details-modal-body">
                    <div style="text-align: center; padding: 20px;">
                        <div class="mesh-traceroute-spinner"></div>
                        <p style="color: var(--text-dim); margin-top: 10px;">Loading telemetry data...</p>
                    </div>
                </div>
            </div>
        `;
        modal.classList.add('show');

        try {
            const response = await fetch(`/meshtastic/telemetry/history?node_id=${encodeURIComponent(nodeId)}&hours=${hours}`);
            const data = await response.json();

            if (data.status === 'ok') {
                renderTelemetryCharts(modal, nodeId, data.data, hours);
            } else {
                throw new Error(data.message || 'Failed to load telemetry');
            }
        } catch (err) {
            console.error('Telemetry load error:', err);
            modal.querySelector('.signal-details-modal-body').innerHTML = `
                <p style="color: var(--status-error);">Error: ${escapeHtml(err.message)}</p>
            `;
        }
    }

    /**
     * Render telemetry charts
     */
    function renderTelemetryCharts(modal, nodeId, data, hours) {
        if (!data || data.length === 0) {
            modal.querySelector('.signal-details-modal-body').innerHTML = `
                <p style="color: var(--text-dim); text-align: center; padding: 20px;">
                    No telemetry data available for this node in the last ${hours} hours.
                </p>
            `;
            return;
        }

        // Build charts for available metrics
        let chartsHtml = `
            <div class="mesh-telemetry-header">
                <span>Node: ${escapeHtml(nodeId)}</span>
                <span style="color: var(--text-dim);">${data.length} data points</span>
            </div>
        `;

        // Battery chart
        const batteryData = data.filter(p => p.battery_level !== null);
        if (batteryData.length > 0) {
            chartsHtml += renderSimpleChart('Battery Level', batteryData, 'battery_level', '%', 0, 100);
        }

        // Voltage chart
        const voltageData = data.filter(p => p.voltage !== null);
        if (voltageData.length > 0) {
            chartsHtml += renderSimpleChart('Voltage', voltageData, 'voltage', 'V', null, null);
        }

        // Temperature chart
        const tempData = data.filter(p => p.temperature !== null);
        if (tempData.length > 0) {
            chartsHtml += renderSimpleChart('Temperature', tempData, 'temperature', '°C', null, null);
        }

        // Humidity chart
        const humidityData = data.filter(p => p.humidity !== null);
        if (humidityData.length > 0) {
            chartsHtml += renderSimpleChart('Humidity', humidityData, 'humidity', '%', 0, 100);
        }

        modal.querySelector('.signal-details-modal-body').innerHTML = chartsHtml;
    }

    /**
     * Render a simple SVG line chart
     */
    function renderSimpleChart(title, data, field, unit, minY, maxY) {
        if (data.length < 2) {
            return `
                <div class="mesh-telemetry-chart">
                    <div class="mesh-telemetry-chart-title">${title}</div>
                    <p style="color: var(--text-dim); font-size: 11px;">Not enough data points</p>
                </div>
            `;
        }

        // Extract values
        const values = data.map(p => p[field]);
        const timestamps = data.map(p => new Date(p.timestamp));

        // Calculate bounds
        const min = minY !== null ? minY : Math.min(...values) * 0.95;
        const max = maxY !== null ? maxY : Math.max(...values) * 1.05;
        const range = max - min || 1;

        // Chart dimensions
        const width = 500;
        const height = 100;
        const padding = { left: 40, right: 10, top: 10, bottom: 20 };
        const chartWidth = width - padding.left - padding.right;
        const chartHeight = height - padding.top - padding.bottom;

        // Build path
        const points = values.map((v, i) => {
            const x = padding.left + (i / (values.length - 1)) * chartWidth;
            const y = padding.top + chartHeight - ((v - min) / range) * chartHeight;
            return `${x},${y}`;
        });
        const pathD = 'M' + points.join(' L');

        // Current value
        const currentValue = values[values.length - 1];

        return `
            <div class="mesh-telemetry-chart">
                <div class="mesh-telemetry-chart-title">
                    ${title}
                    <span class="mesh-telemetry-current">${currentValue.toFixed(1)}${unit}</span>
                </div>
                <svg viewBox="0 0 ${width} ${height}" class="mesh-telemetry-svg">
                    <!-- Y axis labels -->
                    <text x="${padding.left - 5}" y="${padding.top + 5}" class="mesh-chart-label" text-anchor="end">${max.toFixed(0)}</text>
                    <text x="${padding.left - 5}" y="${height - padding.bottom}" class="mesh-chart-label" text-anchor="end">${min.toFixed(0)}</text>
                    <!-- Grid lines -->
                    <line x1="${padding.left}" y1="${padding.top}" x2="${width - padding.right}" y2="${padding.top}" class="mesh-chart-grid"/>
                    <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" class="mesh-chart-grid"/>
                    <!-- Data line -->
                    <path d="${pathD}" class="mesh-chart-line" fill="none"/>
                </svg>
            </div>
        `;
    }

    /**
     * Close telemetry modal
     */
    function closeTelemetryModal() {
        const modal = document.getElementById('meshTelemetryModal');
        if (modal) modal.classList.remove('show');
    }

    /**
     * Show network topology (neighbors)
     */
    async function showNetworkTopology() {
        let modal = document.getElementById('meshNetworkModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'meshNetworkModal';
            modal.className = 'signal-details-modal';
            document.body.appendChild(modal);
        }

        // Show loading
        modal.innerHTML = `
            <div class="signal-details-modal-backdrop" onclick="Meshtastic.closeNetworkModal()"></div>
            <div class="signal-details-modal-content" style="max-width: 700px;">
                <div class="signal-details-modal-header">
                    <h3>Network Topology</h3>
                    <button class="signal-details-modal-close" onclick="Meshtastic.closeNetworkModal()">&times;</button>
                </div>
                <div class="signal-details-modal-body">
                    <div style="text-align: center; padding: 20px;">
                        <div class="mesh-traceroute-spinner"></div>
                        <p style="color: var(--text-dim); margin-top: 10px;">Loading neighbor data...</p>
                    </div>
                </div>
            </div>
        `;
        modal.classList.add('show');

        try {
            const response = await fetch('/meshtastic/neighbors');
            const data = await response.json();

            if (data.status === 'ok') {
                renderNetworkTopology(modal, data.neighbors);
            } else {
                throw new Error(data.message || 'Failed to load neighbors');
            }
        } catch (err) {
            console.error('Network topology error:', err);
            modal.querySelector('.signal-details-modal-body').innerHTML = `
                <p style="color: var(--status-error);">Error: ${escapeHtml(err.message)}</p>
            `;
        }
    }

    /**
     * Render network topology visualization
     */
    function renderNetworkTopology(modal, neighbors) {
        if (!neighbors || Object.keys(neighbors).length === 0) {
            modal.querySelector('.signal-details-modal-body').innerHTML = `
                <p style="color: var(--text-dim); text-align: center; padding: 20px;">
                    No neighbor information available yet.<br>
                    <span style="font-size: 11px;">Neighbor data is collected from NEIGHBOR_INFO_APP packets.</span>
                </p>
            `;
            return;
        }

        // Build a simple list view of neighbors
        let html = '<div class="mesh-network-list">';

        for (const [nodeId, neighborList] of Object.entries(neighbors)) {
            html += `
                <div class="mesh-network-node">
                    <div class="mesh-network-node-header">
                        <span class="mesh-network-node-id">${escapeHtml(nodeId)}</span>
                        <span class="mesh-network-node-count">${neighborList.length} neighbors</span>
                    </div>
                    <div class="mesh-network-neighbors">
            `;

            neighborList.forEach(neighbor => {
                const snrClass = getSnrClass(neighbor.snr);
                html += `
                    <div class="mesh-network-neighbor">
                        <span class="mesh-network-neighbor-id">${escapeHtml(neighbor.neighbor_id)}</span>
                        <span class="mesh-network-neighbor-snr ${snrClass}">${neighbor.snr.toFixed(1)} dB</span>
                    </div>
                `;
            });

            html += '</div></div>';
        }

        html += '</div>';
        modal.querySelector('.signal-details-modal-body').innerHTML = html;
    }

    /**
     * Close network modal
     */
    function closeNetworkModal() {
        const modal = document.getElementById('meshNetworkModal');
        if (modal) modal.classList.remove('show');
    }

    /**
     * Show range test modal
     */
    function showRangeTestModal() {
        let modal = document.getElementById('meshRangeTestModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'meshRangeTestModal';
            modal.className = 'signal-details-modal';
            document.body.appendChild(modal);
        }

        modal.innerHTML = `
            <div class="signal-details-modal-backdrop" onclick="Meshtastic.closeRangeTestModal()"></div>
            <div class="signal-details-modal-content">
                <div class="signal-details-modal-header">
                    <h3>Range Test</h3>
                    <button class="signal-details-modal-close" onclick="Meshtastic.closeRangeTestModal()">&times;</button>
                </div>
                <div class="signal-details-modal-body">
                    <div class="form-group">
                        <label>Number of Packets</label>
                        <input type="number" id="rangeTestCount" value="10" min="1" max="100" style="width: 100%; padding: 8px; background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: 4px; color: var(--text-primary);">
                    </div>
                    <div class="form-group" style="margin-top: 12px;">
                        <label>Interval (seconds)</label>
                        <input type="number" id="rangeTestInterval" value="5" min="1" max="60" style="width: 100%; padding: 8px; background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: 4px; color: var(--text-primary);">
                    </div>
                    <div style="margin-top: 16px; display: flex; gap: 8px;">
                        <button id="rangeTestStartBtn" class="run-btn" onclick="Meshtastic.startRangeTest()">Start Test</button>
                        <button id="rangeTestStopBtn" class="run-btn" style="display: none; background: var(--accent-red);" onclick="Meshtastic.stopRangeTest()">Stop Test</button>
                    </div>
                    <div id="rangeTestStatus" style="margin-top: 16px; display: none;">
                        <div class="mesh-traceroute-spinner" style="margin: 0 auto;"></div>
                        <p style="color: var(--text-dim); text-align: center; margin-top: 10px;">Sending packets...</p>
                    </div>
                </div>
            </div>
        `;

        modal.classList.add('show');
    }

    /**
     * Start range test
     */
    async function startRangeTest() {
        const countInput = document.getElementById('rangeTestCount');
        const intervalInput = document.getElementById('rangeTestInterval');
        const startBtn = document.getElementById('rangeTestStartBtn');
        const stopBtn = document.getElementById('rangeTestStopBtn');
        const statusDiv = document.getElementById('rangeTestStatus');

        const count = parseInt(countInput?.value || '10', 10);
        const interval = parseInt(intervalInput?.value || '5', 10);

        try {
            const response = await fetch('/meshtastic/range-test/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ count, interval })
            });

            const data = await response.json();

            if (data.status === 'started') {
                if (startBtn) startBtn.style.display = 'none';
                if (stopBtn) stopBtn.style.display = 'inline-block';
                if (statusDiv) statusDiv.style.display = 'block';

                showNotification('Meshtastic', `Range test started: ${count} packets`);

                // Poll for completion
                pollRangeTestStatus();
            } else {
                showStatusMessage(data.message || 'Failed to start range test', 'error');
            }
        } catch (err) {
            console.error('Range test error:', err);
            reportActionableError('Start Range Test', err, {
                onRetry: () => startRangeTest()
            });
        }
    }

    /**
     * Stop range test
     */
    async function stopRangeTest() {
        try {
            await fetch('/meshtastic/range-test/stop', { method: 'POST' });
            resetRangeTestUI();
            showNotification('Meshtastic', 'Range test stopped');
        } catch (err) {
            console.error('Error stopping range test:', err);
            reportActionableError('Stop Range Test', err);
        }
    }

    /**
     * Poll range test status
     */
    async function pollRangeTestStatus() {
        try {
            const response = await fetch('/meshtastic/range-test/status');
            const data = await response.json();

            if (data.running) {
                setTimeout(pollRangeTestStatus, 1000);
            } else {
                resetRangeTestUI();
                showNotification('Meshtastic', 'Range test complete');
            }
        } catch (err) {
            console.error('Error polling range test:', err);
            resetRangeTestUI();
        }
    }

    /**
     * Reset range test UI
     */
    function resetRangeTestUI() {
        const startBtn = document.getElementById('rangeTestStartBtn');
        const stopBtn = document.getElementById('rangeTestStopBtn');
        const statusDiv = document.getElementById('rangeTestStatus');

        if (startBtn) startBtn.style.display = 'inline-block';
        if (stopBtn) stopBtn.style.display = 'none';
        if (statusDiv) statusDiv.style.display = 'none';
    }

    /**
     * Close range test modal
     */
    function closeRangeTestModal() {
        const modal = document.getElementById('meshRangeTestModal');
        if (modal) modal.classList.remove('show');
    }

    /**
     * Show Store & Forward modal
     */
    async function showStoreForwardModal() {
        let modal = document.getElementById('meshStoreForwardModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'meshStoreForwardModal';
            modal.className = 'signal-details-modal';
            document.body.appendChild(modal);
        }

        // Show loading state
        modal.innerHTML = `
            <div class="signal-details-modal-backdrop" onclick="Meshtastic.closeStoreForwardModal()"></div>
            <div class="signal-details-modal-content">
                <div class="signal-details-modal-header">
                    <h3>Store & Forward</h3>
                    <button class="signal-details-modal-close" onclick="Meshtastic.closeStoreForwardModal()">&times;</button>
                </div>
                <div class="signal-details-modal-body">
                    <div style="text-align: center; padding: 20px;">
                        <div class="mesh-traceroute-spinner"></div>
                        <p style="color: var(--text-dim); margin-top: 10px;">Checking for S&F router...</p>
                    </div>
                </div>
            </div>
        `;
        modal.classList.add('show');

        try {
            const response = await fetch('/meshtastic/store-forward/status');
            const data = await response.json();

            if (data.available) {
                modal.querySelector('.signal-details-modal-body').innerHTML = `
                    <div class="mesh-sf-info">
                        <p style="color: var(--accent-green); margin-bottom: 12px;">
                            ✓ Store & Forward router found
                        </p>
                        <p style="color: var(--text-secondary); font-size: 12px;">
                            Router: ${escapeHtml(data.router_name || data.router_id || 'Unknown')}
                        </p>
                    </div>
                    <div class="form-group" style="margin-top: 16px;">
                        <label>Request history for:</label>
                        <select id="sfWindowMinutes" style="width: 100%; padding: 8px; background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: 4px; color: var(--text-primary);">
                            <option value="15">Last 15 minutes</option>
                            <option value="60" selected>Last hour</option>
                            <option value="240">Last 4 hours</option>
                            <option value="1440">Last 24 hours</option>
                        </select>
                    </div>
                    <button class="run-btn" style="margin-top: 16px; width: 100%;" onclick="Meshtastic.requestStoreForward()">
                        Fetch Missed Messages
                    </button>
                `;
            } else {
                modal.querySelector('.signal-details-modal-body').innerHTML = `
                    <p style="color: var(--text-dim); text-align: center; padding: 20px;">
                        No Store & Forward router found on the mesh.<br><br>
                        <span style="font-size: 11px;">
                            S&F requires a node with ROUTER role running the<br>
                            Store & Forward module with history enabled.
                        </span>
                    </p>
                `;
            }
        } catch (err) {
            console.error('S&F status error:', err);
            modal.querySelector('.signal-details-modal-body').innerHTML = `
                <p style="color: var(--status-error);">Error: ${escapeHtml(err.message)}</p>
            `;
        }
    }

    /**
     * Request Store & Forward history
     */
    async function requestStoreForward() {
        const select = document.getElementById('sfWindowMinutes');
        const windowMinutes = parseInt(select?.value || '60', 10);

        try {
            const response = await fetch('/meshtastic/store-forward/request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ window_minutes: windowMinutes })
            });

            const data = await response.json();

            if (data.status === 'sent') {
                showNotification('Meshtastic', `Requested ${windowMinutes} minutes of history`);
                closeStoreForwardModal();
            } else {
                showStatusMessage(data.message || 'Failed to request S&F history', 'error');
            }
        } catch (err) {
            console.error('S&F request error:', err);
            reportActionableError('Request Store & Forward History', err, {
                onRetry: () => requestStoreForwardHistory()
            });
        }
    }

    /**
     * Close Store & Forward modal
     */
    function closeStoreForwardModal() {
        const modal = document.getElementById('meshStoreForwardModal');
        if (modal) modal.classList.remove('show');
    }

    return {
        init,
        start,
        stop,
        onConnectionTypeChange,
        loadPorts,
        refreshChannels,
        openChannelModal,
        closeChannelModal,
        onPskFormatChange,
        saveChannelConfig,
        applyFilter,
        showHelp,
        closeHelp,
        sendMessage,
        updateCharCount,
        invalidateMap,
        handleComposeKeydown,
        toggleSidebar,
        toggleOptionsPanel,
        sendTraceroute,
        closeTracerouteModal,
        // New features
        requestPosition,
        checkFirmware,
        closeFirmwareModal,
        showChannelQR,
        closeQRModal,
        showTelemetryChart,
        closeTelemetryModal,
        showNetworkTopology,
        closeNetworkModal,
        // Range test
        showRangeTestModal,
        startRangeTest,
        stopRangeTest,
        closeRangeTestModal,
        // Store & Forward
        showStoreForwardModal,
        requestStoreForward,
        closeStoreForwardModal,
        destroy
    };

    /**
     * Invalidate the map size (call after container resize)
     */
    function invalidateMap() {
        if (meshMap) {
            setTimeout(() => meshMap.invalidateSize(), 100);
        }
    }

    /**
     * Destroy — tear down SSE, timers, and event listeners for clean mode switching.
     */
    function destroy() {
        stopStream();
    }
})();

// Initialize when DOM is ready (will be called by selectMode)
document.addEventListener('DOMContentLoaded', function() {
    // Initialization happens via selectMode when Meshtastic mode is activated
});
