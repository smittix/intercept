/**
 * Intercept - Agent Manager
 * Handles remote agent selection and API routing
 */

// ============== AGENT STATE ==============

let agents = [];
let currentAgent = 'local';
let agentEventSource = null;
let multiAgentMode = false;  // Show combined results from all agents
let multiAgentPollInterval = null;
let agentRunningModes = [];  // Track agent's running modes for conflict detection
let agentRunningModesDetail = {};  // Track device info per mode (for multi-SDR agents)
let healthCheckInterval = null;  // Health monitoring interval
let agentHealthStatus = {};  // Cache of health status per agent ID
let healthCheckKickoffTimer = null;

// ============== AGENT HEALTH MONITORING ==============

/**
 * Start periodic health monitoring for all agents.
 * Runs every 30 seconds to check agent health status.
 */
function startHealthMonitoring() {
    // Don't start if already running
    if (healthCheckInterval) return;

    // Defer the first probe so heavy dashboards can finish initial render
    // before we start contacting remote agents.
    if (healthCheckKickoffTimer) {
        clearTimeout(healthCheckKickoffTimer);
    }
    healthCheckKickoffTimer = setTimeout(() => {
        healthCheckKickoffTimer = null;
        checkAllAgentsHealth();
    }, 5000);

    // Start periodic checks every 30 seconds
    healthCheckInterval = VisibleInterval.set(checkAllAgentsHealth, 30000);
    console.log('[AgentManager] Health monitoring started (30s interval)');
}

/**
 * Stop health monitoring.
 */
function stopHealthMonitoring() {
    if (healthCheckKickoffTimer) {
        clearTimeout(healthCheckKickoffTimer);
        healthCheckKickoffTimer = null;
    }
    if (healthCheckInterval) {
        VisibleInterval.clear(healthCheckInterval);
        healthCheckInterval = null;
        console.log('[AgentManager] Health monitoring stopped');
    }
}

/**
 * Check health of all registered agents in one efficient call.
 */
async function checkAllAgentsHealth() {
    if (agents.length === 0) return;

    try {
        const response = await fetch('/controller/agents/health');
        const data = await response.json();

        if (data.status === 'success' && data.agents) {
            // Update health status cache and UI
            data.agents.forEach(agentHealth => {
                const previousHealth = agentHealthStatus[agentHealth.id];
                agentHealthStatus[agentHealth.id] = agentHealth;

                // Update agent in local list
                const agent = agents.find(a => a.id === agentHealth.id);
                if (agent) {
                    const wasHealthy = agent.healthy !== false;
                    agent.healthy = agentHealth.healthy;
                    agent.response_time_ms = agentHealth.response_time_ms;
                    agent.running_modes = agentHealth.running_modes || [];
                    agent.running_modes_detail = agentHealth.running_modes_detail || {};

                    // Log status change
                    if (wasHealthy !== agentHealth.healthy) {
                        console.log(`[AgentManager] ${agent.name} is now ${agentHealth.healthy ? 'ONLINE' : 'OFFLINE'}`);

                        // Show notification for status change
                        if (!agentHealth.healthy && typeof showNotification === 'function') {
                            showNotification(`Agent "${agent.name}" went offline`, 'warning');
                        }
                    }
                }
            });

            // Update UI
            updateAgentHealthUI();

            // If current agent is selected, sync mode warnings
            if (currentAgent !== 'local') {
                const currentHealth = agentHealthStatus[currentAgent];
                if (currentHealth) {
                    agentRunningModes = currentHealth.running_modes || [];
                    agentRunningModesDetail = currentHealth.running_modes_detail || {};
                    showAgentModeWarnings(agentRunningModes, agentRunningModesDetail);
                }
            }
        }
    } catch (error) {
        console.error('[AgentManager] Health check failed:', error);
    }
}

/**
 * Update the UI to reflect current health status.
 */
function updateAgentHealthUI() {
    const selector = document.getElementById('agentSelect');
    if (!selector) return;

    // Update each option in selector with status and latency
    agents.forEach(agent => {
        const option = selector.querySelector(`option[value="${agent.id}"]`);
        if (option) {
            const health = agentHealthStatus[agent.id];
            const isHealthy = health ? health.healthy : agent.healthy !== false;
            const status = isHealthy ? '●' : '○';
            const latency = health?.response_time_ms ? ` (${health.response_time_ms}ms)` : '';
            option.textContent = `${status} ${agent.name}${latency}`;
            option.dataset.healthy = isHealthy;
        }
    });

    // Update status display for current agent
    updateAgentStatus();

    // Update health panel if it exists
    updateHealthPanel();
}

/**
 * Update the optional health panel showing all agents.
 */
function updateHealthPanel() {
    const panel = document.getElementById('agentHealthPanel');
    if (!panel) return;

    if (agents.length === 0) {
        panel.innerHTML = '<div style="color: var(--text-muted); font-size: 11px;">No agents registered</div>';
        return;
    }

    const html = agents.map(agent => {
        const health = agentHealthStatus[agent.id];
        const isHealthy = health ? health.healthy : agent.healthy !== false;
        const latency = health?.response_time_ms ? `${health.response_time_ms}ms` : '--';
        const modes = health?.running_modes?.length || 0;
        const statusColor = isHealthy ? 'var(--accent-green)' : 'var(--accent-red)';
        const statusIcon = isHealthy ? '●' : '○';

        return `<div style="display: flex; justify-content: space-between; align-items: center; padding: 4px 0; border-bottom: 1px solid var(--border-color);">
            <span style="color: ${statusColor}; font-size: 12px;">${statusIcon} ${agent.name}</span>
            <span style="font-size: 10px; color: var(--text-muted);">
                ${latency} ${modes > 0 ? `| ${modes} mode${modes > 1 ? 's' : ''}` : ''}
            </span>
        </div>`;
    }).join('');

    panel.innerHTML = html;
}

// ============== AGENT LOADING ==============

async function loadAgents() {
    try {
        const response = await fetch('/controller/agents');
        const data = await response.json();
        agents = data.agents || [];
        updateAgentSelector();
        return agents;
    } catch (error) {
        console.error('Failed to load agents:', error);
        agents = [];
        updateAgentSelector();
        return [];
    }
}

function updateAgentSelector() {
    const selector = document.getElementById('agentSelect');
    if (!selector) return;

    // Keep current selection if possible
    const currentValue = selector.value;

    // Clear and rebuild options
    selector.innerHTML = '<option value="local">Local (This Device)</option>';

    agents.forEach(agent => {
        const option = document.createElement('option');
        option.value = agent.id;
        const status = agent.healthy !== false ? '●' : '○';
        option.textContent = `${status} ${agent.name}`;
        option.dataset.baseUrl = agent.base_url;
        option.dataset.healthy = agent.healthy !== false;
        selector.appendChild(option);
    });

    // Restore selection if still valid
    if (currentValue && selector.querySelector(`option[value="${currentValue}"]`)) {
        selector.value = currentValue;
    }

    updateAgentStatus();

    // Show/hide "Show All Agents" options based on whether agents exist
    updateShowAllAgentsVisibility();
}

/**
 * Show or hide the "Show All Agents" checkboxes in mode panels.
 */
function updateShowAllAgentsVisibility() {
    const hasAgents = agents.length > 0;

    // WiFi "Show All Agents" container
    const wifiContainer = document.getElementById('wifiShowAllAgentsContainer');
    if (wifiContainer) {
        wifiContainer.style.display = hasAgents ? 'block' : 'none';
    }

    // Bluetooth "Show All Agents" container
    const btContainer = document.getElementById('btShowAllAgentsContainer');
    if (btContainer) {
        btContainer.style.display = hasAgents ? 'block' : 'none';
    }
}

function updateAgentStatus() {
    const selector = document.getElementById('agentSelect');
    const statusDot = document.getElementById('agentStatusDot');
    const statusText = document.getElementById('agentStatusText');
    const latencyText = document.getElementById('agentLatencyText');

    if (!selector || !statusDot) return;

    if (currentAgent === 'local') {
        statusDot.className = 'agent-status-dot online';
        if (statusText) statusText.textContent = 'Local';
        if (latencyText) latencyText.textContent = '';
    } else {
        const agent = agents.find(a => a.id == currentAgent);
        if (agent) {
            const health = agentHealthStatus[agent.id];
            const isOnline = health ? health.healthy : agent.healthy !== false;
            statusDot.className = `agent-status-dot ${isOnline ? 'online' : 'offline'}`;

            if (statusText) {
                statusText.textContent = isOnline ? 'Connected' : 'Offline';
            }

            // Show latency if available
            if (latencyText) {
                if (health?.response_time_ms) {
                    latencyText.textContent = `${health.response_time_ms}ms`;
                } else {
                    latencyText.textContent = '';
                }
            }
        }
    }
}

// ============== RESPONSE UTILITIES ==============

/**
 * Unwrap agent response from controller proxy format.
 * Controller returns: {status: 'success', result: {...agent response...}}
 * This extracts the actual agent response.
 *
 * @param {Object} response - Response from fetch
 * @param {boolean} isAgentMode - Whether this is an agent (vs local) request
 * @returns {Object} - Unwrapped response
 * @throws {Error} - If response indicates an error
 */
function unwrapAgentResponse(response, isAgentMode = false) {
    if (!response) return null;

    // Check for error status first
    if (response.status === 'error') {
        throw new Error(response.message || response.error || 'Unknown error');
    }

    // If agent mode and has nested result, unwrap it
    if (isAgentMode && response.status === 'success' && response.result !== undefined) {
        const result = response.result;

        // Check if the nested result itself is an error
        if (result.status === 'error') {
            throw new Error(result.message || result.error || 'Agent operation failed');
        }

        return result;
    }

    // Return as-is for local mode or already-unwrapped responses
    return response;
}

/**
 * Check if currently operating in agent mode.
 * @returns {boolean}
 */
function isAgentMode() {
    return currentAgent !== 'local';
}

/**
 * Get the current agent's name for display.
 * @returns {string}
 */
function getCurrentAgentName() {
    if (currentAgent === 'local') return 'Local';
    const agent = agents.find(a => a.id == currentAgent);
    return agent ? agent.name : 'Unknown';
}

// ============== AGENT SELECTION ==============

function selectAgent(agentId) {
    currentAgent = agentId;
    updateAgentStatus();

    // Update device list based on selected agent
    if (agentId === 'local') {
        // Use local devices - call refreshDevices if it exists (defined in main page)
        if (typeof refreshDevices === 'function') {
            refreshDevices();
        }
        // Refresh TSCM devices if function exists
        if (typeof refreshTscmDevices === 'function') {
            refreshTscmDevices();
        }
        // Sync mode UI to local status (reset modes that aren't running locally)
        syncLocalModeStates();
        // Notify WiFi mode of agent change
        if (typeof WiFiMode !== 'undefined' && WiFiMode.handleAgentChange) {
            WiFiMode.handleAgentChange();
        }
        // Notify Bluetooth mode of agent change
        if (typeof BluetoothMode !== 'undefined' && BluetoothMode.handleAgentChange) {
            BluetoothMode.handleAgentChange();
        }
        // Re-enable listen button for local mode
        const listenBtn = document.getElementById('radioListenBtn');
        console.log('[agents.js] Enabling listen button, found:', listenBtn);
        if (listenBtn) {
            listenBtn.disabled = false;
            listenBtn.style.opacity = '1';
            listenBtn.style.cursor = 'pointer';
            listenBtn.title = 'Listen to current frequency';
        }
        if (typeof updateListenButtonState === 'function') {
            updateListenButtonState(false);
        }
        console.log('Agent selected: Local');
    } else {
        // Fetch devices from remote agent
        refreshAgentDevices(agentId);
        // Sync mode states with agent's actual running state
        syncAgentModeStates(agentId);
        // Refresh TSCM devices for agent
        if (typeof refreshTscmDevices === 'function') {
            refreshTscmDevices();
        }
        // Notify WiFi mode of agent change
        if (typeof WiFiMode !== 'undefined' && WiFiMode.handleAgentChange) {
            WiFiMode.handleAgentChange();
        }
        // Notify Bluetooth mode of agent change
        if (typeof BluetoothMode !== 'undefined' && BluetoothMode.handleAgentChange) {
            BluetoothMode.handleAgentChange();
        }
        // Disable listen button for agent mode (audio can't stream over HTTP)
        const listenBtn = document.getElementById('radioListenBtn');
        console.log('[agents.js] Disabling listen button, found:', listenBtn);
        if (listenBtn) {
            listenBtn.disabled = true;
            listenBtn.style.opacity = '0.5';
            listenBtn.style.cursor = 'not-allowed';
            listenBtn.title = 'Audio listening not available for remote agents';
        }
        if (typeof updateListenButtonState === 'function') {
            updateListenButtonState(true);
        }
        const agentName = agents.find(a => a.id == agentId)?.name || 'Unknown';
        console.log(`Agent selected: ${agentName}`);

        // Show visual feedback
        const statusText = document.getElementById('agentStatusText');
        if (statusText) {
            statusText.textContent = `Loading ${agentName}...`;
            setTimeout(() => updateAgentStatus(), 2000);
        }
    }
}

/**
 * Sync UI state with agent's actual running modes.
 * This ensures UI reflects reality when agent was started externally
 * or when user navigates away and back.
 */
async function syncAgentModeStates(agentId) {
    try {
        const response = await fetch(`/controller/agents/${agentId}/status`, {
            credentials: 'same-origin'
        });
        const data = await response.json();

        if (data.status === 'success' && data.agent_status) {
            agentRunningModes = data.agent_status.running_modes || [];
            agentRunningModesDetail = data.agent_status.running_modes_detail || {};
            console.log(`Agent ${agentId} running modes:`, agentRunningModes);
            console.log(`Agent ${agentId} mode details:`, agentRunningModesDetail);

            // IMPORTANT: Only sync UI if this agent is currently selected
            // Otherwise we'd start streams for an agent the user hasn't selected
            const isSelectedAgent = currentAgent == agentId;  // Use == for string/number comparison
            console.log(`Agent ${agentId} is selected: ${isSelectedAgent} (currentAgent=${currentAgent})`);

            if (isSelectedAgent) {
                // Update UI for each mode based on agent state
                agentRunningModes.forEach(mode => {
                    syncModeUI(mode, true, agentId);
                });

                // Also check modes that might need to be marked as stopped
                const allModes = ['sensor', 'pager', 'adsb', 'wifi', 'bluetooth', 'ais', 'dsc', 'acars', 'vdl2', 'aprs', 'rtlamr', 'tscm', 'satellite', 'listening_post'];
                allModes.forEach(mode => {
                    if (!agentRunningModes.includes(mode)) {
                        syncModeUI(mode, false, agentId);
                    }
                });
            }

            // Show warning if SDR modes are running (always show, regardless of selection)
            showAgentModeWarnings(agentRunningModes, agentRunningModesDetail);
        }
    } catch (error) {
        console.error('Failed to sync agent mode states:', error);
    }
}

/**
 * Sync UI state with local mode statuses.
 * Called when switching back to local to ensure UI reflects local state.
 */
async function syncLocalModeStates() {
    console.log('[AgentManager] Syncing local mode states...');

    // Check each mode's local status endpoint
    const modeChecks = [
        { mode: 'pager', endpoint: '/status', runningKey: 'running' },
        { mode: 'sensor', endpoint: '/sensor/status', runningKey: 'running' },
        { mode: 'adsb', endpoint: '/adsb/status', runningKey: 'running' },
        { mode: 'tscm', endpoint: '/tscm/status', runningKey: 'running' },
    ];

    for (const check of modeChecks) {
        try {
            const response = await fetch(check.endpoint);
            if (response.ok) {
                const data = await response.json();
                const isRunning = data[check.runningKey] || false;
                syncModeUI(check.mode, isRunning, null);
            } else {
                // Endpoint not available or error - assume not running
                syncModeUI(check.mode, false, null);
            }
        } catch (error) {
            // Network error or endpoint doesn't exist - assume not running
            syncModeUI(check.mode, false, null);
        }
    }

    // Clear agent mode warnings when switching to local
    const warning = document.getElementById('agentModeWarning');
    if (warning) {
        warning.style.display = 'none';
    }
}

/**
 * Show warnings about running modes that may cause conflicts.
 * @param {string[]} runningModes - List of running mode names
 * @param {Object} modesDetail - Detail info including device per mode
 */
function showAgentModeWarnings(runningModes, modesDetail = {}) {
    // SDR modes that can't run simultaneously on same device
    const sdrModes = ['sensor', 'pager', 'adsb', 'ais', 'acars', 'vdl2', 'aprs', 'rtlamr', 'listening_post', 'tscm', 'dsc'];
    const runningSdrModes = runningModes.filter(m => sdrModes.includes(m));

    let warning = document.getElementById('agentModeWarning');

    if (runningSdrModes.length > 0) {
        if (!warning) {
            // Create warning element if it doesn't exist
            const agentSection = document.getElementById('agentSection');
            if (agentSection) {
                warning = document.createElement('div');
                warning.id = 'agentModeWarning';
                warning.style.cssText = 'color: #f0ad4e; font-size: 10px; padding: 4px 8px; background: rgba(240,173,78,0.1); border-radius: 4px; margin-top: 4px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;';
                agentSection.appendChild(warning);
            }
        }
        if (warning) {
            // Build mode buttons with device info
            const modeButtons = runningSdrModes.map(m => {
                const detail = modesDetail[m] || {};
                const deviceNum = detail.device !== undefined ? detail.device : '?';
                return `<button onclick="stopAgentModeWithRefresh('${m}')" style="background:#ff6b6b;color:#fff;border:none;padding:2px 6px;border-radius:3px;font-size:9px;cursor:pointer;" title="Stop ${m} on agent (SDR ${deviceNum})">${m} (SDR ${deviceNum})</button>`;
            }).join(' ');
            warning.innerHTML = `<span>⚠️ Running:</span> ${modeButtons} <button onclick="refreshAgentState()" style="background:#555;color:#fff;border:none;padding:2px 6px;border-radius:3px;font-size:9px;cursor:pointer;" title="Refresh agent state">↻</button>`;
            warning.style.display = 'flex';
        }
    } else if (warning) {
        warning.style.display = 'none';
    }
}

/**
 * Stop a mode on the agent and refresh state.
 */
async function stopAgentModeWithRefresh(mode) {
    if (currentAgent === 'local') return;

    try {
        const response = await fetch(`/controller/agents/${currentAgent}/${mode}/stop`, {
            method: 'POST',
            credentials: 'same-origin'
        });
        const data = await response.json();
        console.log(`Stop ${mode} response:`, data);

        // Refresh agent state to update UI
        await refreshAgentState();
    } catch (error) {
        console.error(`Failed to stop ${mode} on agent:`, error);
        alert(`Failed to stop ${mode}: ${error.message}`);
    }
}

/**
 * Refresh agent state from server.
 */
async function refreshAgentState() {
    if (currentAgent === 'local') return;

    console.log('Refreshing agent state...');
    await syncAgentModeStates(currentAgent);
}

/**
 * Check if a mode requires audio streaming (not supported via agents).
 * @param {string} mode - Mode name
 * @returns {boolean} - True if mode requires audio
 */
function isAudioMode(mode) {
    const audioModes = ['airband', 'listening_post'];
    return audioModes.includes(mode);
}

/**
 * Get the IP/hostname from an agent's base URL.
 * @param {number|string} agentId - Agent ID
 * @returns {string|null} - Hostname or null
 */
function getAgentHost(agentId) {
    const agent = agents.find(a => a.id == agentId);
    if (!agent || !agent.base_url) return null;
    try {
        const url = new URL(agent.base_url);
        return url.hostname;
    } catch (e) {
        return null;
    }
}

/**
 * Check if trying to start an audio mode on a remote agent.
 * Offers rtl_tcp option instead of just blocking.
 * @param {string} modeToStart - Mode to start
 * @returns {boolean} - True if OK to proceed
 */
function checkAgentAudioMode(modeToStart) {
    if (currentAgent === 'local') return true;

    if (isAudioMode(modeToStart)) {
        const agentHost = getAgentHost(currentAgent);
        const agentName = agents.find(a => a.id == currentAgent)?.name || 'remote agent';

        alert(
            `Audio streaming is not supported via remote agents.\n\n` +
            `"${modeToStart}" requires real-time audio.\n\n` +
            `To use audio from a remote SDR:\n\n` +
            `1. On the agent (${agentName}):\n` +
            `   Run: rtl_tcp -a 0.0.0.0\n\n` +
            `2. On the Main Dashboard (/):\n` +
            `   - Select "Local" mode\n` +
            `   - Check "Use Remote SDR (rtl_tcp)"\n` +
            `   - Enter host: ${agentHost || '[agent IP]'}\n` +
            `   - Port: 1234\n\n` +
            `Note: rtl_tcp config is on the Main Dashboard,\n` +
            `not on specialized dashboards like ADS-B/AIS.`
        );

        return false;  // Don't proceed with agent mode
    }
    return true;
}

/**
 * Check if trying to start a mode that conflicts with running modes.
 * Returns true if OK to proceed, false if conflict exists.
 * @param {string} modeToStart - Mode to start
 * @param {number} deviceToUse - Device index to use (optional, for smarter conflict detection)
 */
async function checkAgentModeConflict(modeToStart, deviceToUse = null) {
    if (currentAgent === 'local') return true;  // No conflict checking for local

    // First check if this is an audio mode
    if (!checkAgentAudioMode(modeToStart)) {
        return false;
    }

    const sdrModes = ['sensor', 'pager', 'adsb', 'ais', 'acars', 'vdl2', 'aprs', 'rtlamr', 'listening_post', 'tscm', 'dsc'];

    // If we're trying to start an SDR mode
    if (sdrModes.includes(modeToStart)) {
        // Check for conflicts - if device is specified, only check that device
        let conflictingModes = [];

        if (deviceToUse !== null && Object.keys(agentRunningModesDetail).length > 0) {
            // Smart conflict detection: only flag modes using the same device
            conflictingModes = agentRunningModes.filter(m => {
                if (!sdrModes.includes(m) || m === modeToStart) return false;
                const detail = agentRunningModesDetail[m];
                return detail && detail.device === deviceToUse;
            });
        } else {
            // Fallback: warn about all running SDR modes
            conflictingModes = agentRunningModes.filter(m =>
                sdrModes.includes(m) && m !== modeToStart
            );
        }

        if (conflictingModes.length > 0) {
            const modeList = conflictingModes.map(m => {
                const detail = agentRunningModesDetail[m];
                return detail ? `${m} (SDR ${detail.device})` : m;
            }).join(', ');

            const proceed = await AppFeedback.confirmAction({
                title: 'SDR Device Conflict',
                message: `The agent's SDR device is currently running: ${modeList}. Starting ${modeToStart} on the same device will fail. Do you want to stop the conflicting mode(s) first?`,
                confirmLabel: 'Stop & Continue',
                confirmClass: 'btn-danger'
            });

            if (proceed) {
                // Stop conflicting modes
                conflictingModes.forEach(mode => {
                    stopAgentModeQuiet(mode);
                });
                return true;
            }
            return false;
        }
    }

    return true;
}

/**
 * Stop a mode on the current agent (without UI feedback).
 */
async function stopAgentModeQuiet(mode) {
    if (currentAgent === 'local') return;

    try {
        await fetch(`/controller/agents/${currentAgent}/${mode}/stop`, {
            method: 'POST',
            credentials: 'same-origin'
        });
        console.log(`Stopped ${mode} on agent ${currentAgent}`);
        // Remove from running modes
        agentRunningModes = agentRunningModes.filter(m => m !== mode);
        syncModeUI(mode, false);
        showAgentModeWarnings(agentRunningModes);
    } catch (error) {
        console.error(`Failed to stop ${mode} on agent:`, error);
    }
}

/**
 * Update UI elements for a specific mode based on running state.
 * @param {string} mode - Mode name (adsb, wifi, etc.)
 * @param {boolean} isRunning - Whether the mode is running
 * @param {string|number|null} agentId - Agent ID if running on agent, null for local
 */
function syncModeUI(mode, isRunning, agentId = null) {
    // Map mode names to UI setter functions (if they exist)
    const uiSetters = {
        'sensor': 'setSensorRunning',
        'pager': 'setPagerRunning',
        'adsb': 'setADSBRunning',
        'wifi': 'setWiFiRunning',
        'bluetooth': 'setBluetoothRunning',
        'acars': 'setAcarsRunning',
        'vdl2': 'setVdl2Running',
        'listening_post': 'setListeningPostRunning'
    };

    const setterName = uiSetters[mode];
    if (setterName && typeof window[setterName] === 'function') {
        // Pass agent ID as source for functions that support it (like setADSBRunning)
        window[setterName](isRunning, agentId);
        console.log(`Synced ${mode} UI state: ${isRunning ? 'running' : 'stopped'} (agent: ${agentId || 'local'})`);
    }
}

async function refreshAgentDevices(agentId) {
    console.log(`Refreshing devices for agent ${agentId}...`);
    try {
        const response = await fetch(`/controller/agents/${agentId}?refresh=true`, {
            credentials: 'same-origin'
        });
        const data = await response.json();
        console.log('Agent data received:', data);

        if (data.agent && data.agent.interfaces) {
            // Agent stores SDR devices in interfaces.sdr_devices (matching local mode)
            const devices = data.agent.interfaces.sdr_devices || data.agent.interfaces.devices || [];
            console.log(`Found ${devices.length} devices on agent`);

            // Auto-select SDR type if devices found
            if (devices.length > 0) {
                const firstType = devices[0].sdr_type || 'rtlsdr';
                const sdrTypeSelect = document.getElementById('sdrTypeSelect');
                if (sdrTypeSelect) {
                    sdrTypeSelect.value = firstType;
                }
            }

            // Directly populate device dropdown for agent mode
            // (Don't use onSDRTypeChanged since currentDeviceList is template-scoped)
            populateDeviceSelect(devices);
        } else {
            console.warn('No interfaces found in agent data:', data);
            // Show empty devices
            populateDeviceSelect([]);
        }
    } catch (error) {
        console.error('Failed to refresh agent devices:', error);
        populateDeviceSelect([]);
    }
}

function populateDeviceSelect(devices) {
    const select = document.getElementById('deviceSelect');
    if (!select) return;

    select.innerHTML = '';

    if (devices.length === 0) {
        const option = document.createElement('option');
        option.value = '0';
        option.textContent = 'No devices found';
        select.appendChild(option);
    } else {
        devices.forEach(device => {
            const option = document.createElement('option');
            option.value = device.index;
            option.dataset.sdrType = device.sdr_type || 'rtlsdr';
            option.textContent = `${device.index}: ${device.name}`;
            select.appendChild(option);
        });
    }
}

// ============== API ROUTING ==============

/**
 * Route an API call to local or remote agent based on current selection.
 * @param {string} localPath - Local API path (e.g., '/sensor/start')
 * @param {Object} options - Fetch options
 * @returns {Promise<Response>}
 */
async function agentFetch(localPath, options = {}) {
    if (currentAgent === 'local') {
        return fetch(localPath, options);
    }

    // Route through controller proxy
    const proxyPath = `/controller/agents/${currentAgent}${localPath}`;
    return fetch(proxyPath, options);
}

/**
 * Start a mode on the selected agent.
 * @param {string} mode - Mode name (pager, sensor, adsb, wifi, etc.)
 * @param {Object} params - Mode parameters
 * @returns {Promise<Object>}
 */
async function agentStartMode(mode, params = {}) {
    const path = `/${mode}/start`;
    const options = {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
    };

    try {
        const response = await agentFetch(path, options);
        return await response.json();
    } catch (error) {
        console.error(`Failed to start ${mode} on agent:`, error);
        throw error;
    }
}

/**
 * Stop a mode on the selected agent.
 * @param {string} mode - Mode name
 * @returns {Promise<Object>}
 */
async function agentStopMode(mode) {
    const path = `/${mode}/stop`;
    const options = { method: 'POST' };

    try {
        const response = await agentFetch(path, options);
        return await response.json();
    } catch (error) {
        console.error(`Failed to stop ${mode} on agent:`, error);
        throw error;
    }
}

/**
 * Get data from a mode on the selected agent.
 * @param {string} mode - Mode name
 * @returns {Promise<Object>}
 */
async function agentGetData(mode) {
    const path = `/${mode}/data`;

    try {
        const response = await agentFetch(path);
        return await response.json();
    } catch (error) {
        console.error(`Failed to get ${mode} data from agent:`, error);
        throw error;
    }
}

// ============== SSE STREAM ==============

/**
 * Connect to SSE stream (local or multi-agent).
 * @param {string} mode - Mode name for the stream
 * @param {function} onMessage - Callback for messages
 * @returns {EventSource}
 */
function connectAgentStream(mode, onMessage) {
    // Close existing connection
    if (agentEventSource) {
        agentEventSource.close();
    }

    let streamUrl;
    if (currentAgent === 'local') {
        streamUrl = `/${mode}/stream`;
    } else {
        // For remote agents, proxy SSE through controller
        streamUrl = `/controller/agents/${currentAgent}/${mode}/stream`;
    }

    agentEventSource = new EventSource(streamUrl);

    agentEventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);

            onMessage(data);
        } catch (e) {
            console.error('Error parsing SSE message:', e);
        }
    };

    agentEventSource.onerror = (error) => {
        console.error('SSE connection error:', error);
    };

    return agentEventSource;
}

function disconnectAgentStream() {
    if (agentEventSource) {
        agentEventSource.close();
        agentEventSource = null;
    }
}

// ============== INITIALIZATION ==============

function initAgentManager() {
    // Load agents on page load
    loadAgents().then(() => {
        // Start health monitoring after agents are loaded
        if (agents.length > 0) {
            startHealthMonitoring();
        }
    });

    // Set up agent selector change handler
    const selector = document.getElementById('agentSelect');
    if (selector) {
        selector.addEventListener('change', (e) => {
            selectAgent(e.target.value);
        });
    }

    // Refresh agent list periodically (less often since health monitor is active)
    setInterval(async () => {
        await loadAgents();
        // Start health monitoring if we now have agents
        if (agents.length > 0 && !healthCheckInterval) {
            startHealthMonitoring();
        }
    }, 60000);  // Refresh list every 60s (health checks every 30s)
}

// ============== MULTI-AGENT MODE ==============

/**
 * Toggle multi-agent mode to show combined results from all agents.
 */
function toggleMultiAgentMode() {
    const checkbox = document.getElementById('showAllAgents');
    multiAgentMode = checkbox ? checkbox.checked : false;

    const selector = document.getElementById('agentSelect');
    const statusText = document.getElementById('agentStatusText');

    if (multiAgentMode) {
        // Disable individual agent selection
        if (selector) selector.disabled = true;
        if (statusText) statusText.textContent = 'All Agents';

        // Connect to multi-agent stream
        connectMultiAgentStream();

        console.log('Multi-agent mode enabled - showing all agents');
    } else {
        // Re-enable individual selection
        if (selector) selector.disabled = false;
        updateAgentStatus();

        // Disconnect multi-agent stream
        disconnectMultiAgentStream();

        console.log('Multi-agent mode disabled');
    }
}

/**
 * Connect to the combined multi-agent SSE stream.
 */
function connectMultiAgentStream() {
    disconnectMultiAgentStream();

    agentEventSource = new EventSource('/controller/stream/all');

    agentEventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);

            // Skip keepalive messages
            if (data.type === 'keepalive') return;

            // Route to appropriate handler based on scan_type
            handleMultiAgentData(data);
        } catch (e) {
            console.error('Error parsing multi-agent SSE:', e);
        }
    };

    agentEventSource.onerror = (error) => {
        console.error('Multi-agent SSE error:', error);
    };
}

function disconnectMultiAgentStream() {
    if (agentEventSource) {
        agentEventSource.close();
        agentEventSource = null;
    }
    if (multiAgentPollInterval) {
        clearInterval(multiAgentPollInterval);
        multiAgentPollInterval = null;
    }
}

/**
 * Handle data from multi-agent stream and route to display.
 */
function handleMultiAgentData(data) {
    const agentName = data.agent_name || 'Unknown';
    const scanType = data.scan_type;
    const payload = data.payload;

    // Add agent badge to the data for display
    if (payload) {
        payload._agent = agentName;
    }

    // Route based on scan type
    switch (scanType) {
        case 'sensor':
            if (payload && payload.sensors) {
                payload.sensors.forEach(sensor => {
                    sensor._agent = agentName;
                    if (typeof displaySensorMessage === 'function') {
                        displaySensorMessage(sensor);
                    }
                });
            }
            break;

        case 'pager':
            if (payload && payload.messages) {
                payload.messages.forEach(msg => {
                    msg._agent = agentName;
                    // Display pager message if handler exists
                    if (typeof addPagerMessage === 'function') {
                        addPagerMessage(msg);
                    }
                });
            }
            break;

        case 'adsb':
            if (payload && payload.aircraft) {
                Object.values(payload.aircraft).forEach(ac => {
                    ac._agent = agentName;
                    // Update aircraft display if handler exists
                    if (typeof updateAircraft === 'function') {
                        updateAircraft(ac);
                    }
                });
            }
            break;

        case 'wifi':
            // WiFi mode handles its own multi-agent stream processing
            // This is a fallback for legacy display or when WiFi mode isn't active
            if (payload && payload.networks) {
                Object.values(payload.networks).forEach(net => {
                    net._agent = agentName;
                    // Use legacy display if available
                    if (typeof handleWifiNetworkImmediate === 'function') {
                        handleWifiNetworkImmediate(net);
                    }
                });
            }
            if (payload && payload.clients) {
                Object.values(payload.clients).forEach(client => {
                    client._agent = agentName;
                    if (typeof handleWifiClientImmediate === 'function') {
                        handleWifiClientImmediate(client);
                    }
                });
            }
            break;

        case 'bluetooth':
            if (payload && payload.devices) {
                Object.values(payload.devices).forEach(device => {
                    device._agent = agentName;
                    // Update Bluetooth display if handler exists
                    if (typeof addBluetoothDevice === 'function') {
                        addBluetoothDevice(device);
                    }
                });
            }
            break;

        default:
            console.log(`Multi-agent data from ${agentName}: ${scanType}`, payload);
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', initAgentManager);
