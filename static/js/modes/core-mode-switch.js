/**
 * Main-page SPA core: activity timeline, mode catalog/selection, disclaimer,
 * global run-state, header clock/stats, observer & satellite-pass state,
 * sidebar and nav dropdowns, scan teardown, the switchMode() dispatcher, and
 * the message export / autoscroll / signal-meter / preset controls.
 *
 * Moved out of templates/index.html unchanged (only de-indented); a classic
 * script loaded after the inline block, sharing its top-level names as before.
 */
// ============================================
// ACTIVITY TIMELINE MANAGEMENT
// ============================================
const modeTimelines = {};

/**
 * Initialize timeline for a specific mode
 */
function initializeModeTimeline(mode) {
    // Skip if already initialized
    if (modeTimelines[mode]) return;

    const configs = {
        'pager': {
            container: 'pagerTimelineContainer',
            config: {
                title: 'Pager Activity',
                mode: 'pager',
                visualMode: 'enriched',
                collapsed: false,
                availableWindows: ['5m', '15m', '30m', '1h'],
                defaultWindow: '15m'
            }
        },
        'sensor': {
            container: 'sensorTimelineContainer',
            config: {
                title: 'Sensor Activity',
                mode: 'sensor',
                visualMode: 'enriched',
                collapsed: false,
                availableWindows: ['5m', '15m', '30m', '1h'],
                defaultWindow: '15m'
            }
        },
        'tscm': {
            container: 'tscmTimelineContainer',
            config: typeof RFTimelineAdapter !== 'undefined' ? RFTimelineAdapter.getTscmConfig() : {
                title: 'Signal Activity Timeline',
                mode: 'tscm',
                visualMode: 'enriched',
                collapsed: true
            }
        },
        'bluetooth': {
            container: 'bluetoothTimelineContainer',
            config: typeof BluetoothTimelineAdapter !== 'undefined' ? BluetoothTimelineAdapter.getBluetoothConfig() : {
                title: 'Device Activity',
                mode: 'bluetooth',
                visualMode: 'enriched',
                collapsed: false
            }
        },
        'wifi': {
            container: 'wifiTimelineContainer',
            config: typeof WiFiTimelineAdapter !== 'undefined' ? WiFiTimelineAdapter.getWiFiConfig() : {
                title: 'Network Activity',
                mode: 'wifi',
                visualMode: 'enriched',
                collapsed: false
            }
        }
    };

    const modeConfig = configs[mode];
    if (!modeConfig) return;

    const container = document.getElementById(modeConfig.container);
    if (!container) return;

    // Create timeline using new ActivityTimeline
    // For TSCM mode, use SignalTimeline.create() to ensure backward compatibility
    // with SignalTimeline.addEvent() calls used in TSCM event handlers
    if (mode === 'tscm' && typeof SignalTimeline !== 'undefined') {
        SignalTimeline.create(modeConfig.container, modeConfig.config);
        modeTimelines[mode] = { addEvent: (e) => SignalTimeline.addEvent(e.id, e.strength, e.duration, e.label) };
    } else if (typeof ActivityTimeline !== 'undefined') {
        modeTimelines[mode] = ActivityTimeline.create(modeConfig.container, modeConfig.config);
    }
}

/**
 * Add event to a mode's timeline
 */
function addTimelineEvent(mode, eventData) {
    const timeline = modeTimelines[mode];
    if (timeline) {
        timeline.addEvent(eventData);
    }
}

/**
 * Get timeline instance for a mode
 */
function getTimeline(mode) {
    return modeTimelines[mode] || null;
}

// Selected mode from welcome screen
const savedDefaultMode = localStorage.getItem('intercept.default_mode');
let selectedStartMode = savedDefaultMode === 'listening' ? 'waterfall' : (savedDefaultMode || 'pager');
if (savedDefaultMode === 'listening') {
    localStorage.setItem('intercept.default_mode', 'waterfall');
}

// Mode selection from welcome page
function selectMode(mode) {
    if (mode === 'satellite') {
        window.open('/satellite/dashboard', '_blank', 'noopener');
        return;
    }
    selectedStartMode = mode;
    const welcome = document.getElementById('welcomePage');
    welcome.classList.add('fade-out');

    // After fade out, hide welcome and switch to mode
    setTimeout(() => {
        welcome.style.display = 'none';
        switchMode(mode, { updateUrl: true });
    }, 400);
}

// Disclaimer handling - show on page load if not accepted
function showDisclaimer() {
    document.getElementById('disclaimerModal').style.display = 'flex';
}

// Mode from query string (e.g., /?mode=wifi)
if (!window.INTERCEPT_MODES) {
    throw new Error('mode-registry.js failed to load — the SPA cannot start');
}
let pendingStartMode = null;
const modeCatalog = {};
for (const [mode, def] of Object.entries(window.INTERCEPT_MODES)) {
    modeCatalog[mode] = {
        label: def.label,
        indicator: def.indicator,
        outputTitle: def.outputTitle,
        group: def.group,
    };
}
const validModes = new Set(Object.keys(modeCatalog));
window.interceptModeCatalog = Object.assign({}, modeCatalog);

function getModeFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const requestedMode = params.get('mode');
    const mode = requestedMode === 'listening' ? 'waterfall' : requestedMode;
    if (!mode || !validModes.has(mode)) return null;
    return mode;
}

function applyModeFromQuery() {
    const mode = getModeFromQuery();
    if (!mode) return;
    if (mode === 'satellite') {
        window.location.replace('/satellite/dashboard');
        return;
    }
    const accepted = localStorage.getItem('disclaimerAccepted') === 'true';
    if (accepted) {
        const welcome = document.getElementById('welcomePage');
        if (welcome) welcome.style.display = 'none';
        // Remove mode-gate style injected to prevent welcome flash
        const modeGate = document.getElementById('mode-gate');
        if (modeGate) modeGate.remove();
        switchMode(mode, { updateUrl: false });
        updateModeUrl(mode, true);
    } else {
        pendingStartMode = mode;
    }
}

function applySettingsFromQuery() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('settings') === '1') {
        // Remove settings param from URL to avoid reopening on refresh
        params.delete('settings');
        const newUrl = params.toString()
            ? window.location.pathname + '?' + params.toString()
            : window.location.pathname;
        window.history.replaceState({}, '', newUrl);
        // Open settings modal after a brief delay to ensure page is ready
        setTimeout(() => {
            if (typeof showSettings === 'function') {
                showSettings();
            }
        }, 100);
    }
}

function acceptDisclaimer() {
    localStorage.setItem('disclaimerAccepted', 'true');
    document.getElementById('disclaimerModal').classList.add('disclaimer-hidden');

    // After fade out, hide disclaimer and show welcome page
    setTimeout(() => {
        document.getElementById('disclaimerModal').style.display = 'none';
        // Remove the gate CSS that was hiding welcome page
        const gateStyle = document.getElementById('disclaimer-gate');
        if (gateStyle) gateStyle.remove();
        // Ensure welcome page is visible
        const welcome = document.getElementById('welcomePage');
        if (welcome) welcome.style.display = '';
        if (pendingStartMode) {
            // Bypass welcome and jump to requested mode
            welcome.style.display = 'none';
        switchMode(pendingStartMode, { updateUrl: true });
        pendingStartMode = null;
        }
    }, 300);
}

function declineDisclaimer() {
    document.getElementById('disclaimerModal').classList.add('disclaimer-hidden');
    document.getElementById('rejectionPage').classList.remove('disclaimer-hidden');
}

// Show disclaimer on page load if not yet accepted
document.addEventListener('DOMContentLoaded', function() {
    if (window._showDisclaimerOnLoad) {
        showDisclaimer();
    }
});

let eventSource = null;
let isRunning = false;
let isSensorRunning = false;
let isWifiRunning = false;
let isBtRunning = false;
let currentMode = 'pager';
let msgCount = 0;
let pocsagCount = 0;
let flexCount = 0;
let sensorCount = 0;
let filteredCount = 0;  // Count of filtered messages
let deviceList = window.INTERCEPT_INITIAL_DEVICES || [];

// Pager message filter settings
let pagerFilters = {
    hideToneOnly: false,
    keywords: []
};

// Clock Update (uses global InterceptTime for timezone/format)
function updateHeaderClock() {
    const now = new Date();
    const el = document.getElementById('headerUtcTime');
    const label = document.querySelector('.utc-label');
    if (typeof InterceptTime !== 'undefined') {
        if (el) el.textContent = InterceptTime.fullTime(now);
        if (label) label.textContent = InterceptTime.getLabel() || 'LOCAL';
    } else {
        if (el) el.textContent = now.toISOString().substring(11, 19);
    }
}

function setActiveModeIndicator(label) {
    const indicator = document.getElementById('activeModeIndicator');
    if (!indicator) return;

    indicator.textContent = '';
    const dot = document.createElement('span');
    dot.className = 'pulse-dot';
    indicator.appendChild(dot);
    indicator.appendChild(document.createTextNode(String(label || '')));
}

function applyKeyboardAccessibility(root = document) {
    const interactive = root.querySelectorAll('[onclick]:not(button):not(a):not(input):not(select):not(textarea)');
    interactive.forEach((el) => {
        if (!el.hasAttribute('role')) el.setAttribute('role', 'button');
        if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
        el.setAttribute('data-keyboard-activate', 'true');
    });
}

if (!window._keyboardActivationBound) {
    window._keyboardActivationBound = true;
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const target = event.target && event.target.closest ? event.target.closest('[data-keyboard-activate="true"]') : null;
        if (!target) return;
        event.preventDefault();
        target.click();
    });
}

// Update clock every second
window._navClockStarted = true;
VisibleInterval.set(updateHeaderClock, 1000);
updateHeaderClock();
if (typeof InterceptTime !== 'undefined' && InterceptTime.onChange) {
    InterceptTime.onChange(updateHeaderClock);
}
applyKeyboardAccessibility();

// Pager message filter functions
function loadPagerFilters() {
    const saved = localStorage.getItem('pagerFilters');
    if (saved) {
        try {
            const parsed = JSON.parse(saved);
            // Only persist keywords across sessions.
            // hideToneOnly defaults to false every session so users
            // always see the full traffic stream unless they opt-in.
            if (Array.isArray(parsed.keywords)) pagerFilters.keywords = parsed.keywords;
        } catch (e) {
            console.warn('Failed to load pager filters:', e);
        }
    }
    // Update UI
    document.getElementById('filterToneOnly').checked = pagerFilters.hideToneOnly;
    document.getElementById('filterKeywords').value = pagerFilters.keywords.join(', ');
}

function savePagerFilters() {
    pagerFilters.hideToneOnly = document.getElementById('filterToneOnly').checked;
    const keywordsInput = document.getElementById('filterKeywords').value;
    pagerFilters.keywords = keywordsInput
        .split(',')
        .map(k => k.trim().toLowerCase())
        .filter(k => k.length > 0);
    localStorage.setItem('pagerFilters', JSON.stringify(pagerFilters));
}

function shouldFilterMessage(msg) {
    // Check for Tone Only filter
    if (pagerFilters.hideToneOnly) {
        if (msg.message === '[Tone Only]' || msg.msg_type === 'Tone') {
            return true;
        }
    }
    // Check keyword filters
    if (pagerFilters.keywords.length > 0) {
        const msgLower = (msg.message || '').toLowerCase();
        for (const keyword of pagerFilters.keywords) {
            if (msgLower.includes(keyword)) {
                return true;
            }
        }
    }
    return false;
}

// Sync header stats with output panel stats
function syncHeaderStats() {
    // Pager stats
    const headerMsgCount = document.getElementById('headerMsgCount');
    const headerPocsagCount = document.getElementById('headerPocsagCount');
    const headerFlexCount = document.getElementById('headerFlexCount');
    if (headerMsgCount) headerMsgCount.textContent = msgCount;
    if (headerPocsagCount) headerPocsagCount.textContent = pocsagCount;
    if (headerFlexCount) headerFlexCount.textContent = flexCount;

    // Sensor stats
    const headerSensorCount = document.getElementById('headerSensorCount');
    const headerDeviceTypeCount = document.getElementById('headerDeviceTypeCount');
    if (headerSensorCount) headerSensorCount.textContent = document.getElementById('sensorCount')?.textContent || '0';
    if (headerDeviceTypeCount) headerDeviceTypeCount.textContent = document.getElementById('deviceCount')?.textContent || '0';

    // WiFi stats
    const headerApCount = document.getElementById('headerApCount');
    const headerClientCount = document.getElementById('headerClientCount');
    const headerHandshakeCount = document.getElementById('headerHandshakeCount');
    const headerDroneCount = document.getElementById('headerDroneCount');
    if (headerApCount) headerApCount.textContent = document.getElementById('apCount')?.textContent || '0';
    if (headerClientCount) headerClientCount.textContent = document.getElementById('clientCount')?.textContent || '0';
    if (headerHandshakeCount) headerHandshakeCount.textContent = document.getElementById('handshakeCount')?.textContent || '0';
    if (headerDroneCount) headerDroneCount.textContent = document.getElementById('droneCount')?.textContent || '0';

    // Satellite stats
    const headerPassCount = document.getElementById('headerPassCount');
    if (headerPassCount) headerPassCount.textContent = document.getElementById('passCount')?.textContent || '0';
}
// Sync stats periodically
VisibleInterval.set(syncHeaderStats, 500);

// Observer location for distance calculations (load from localStorage or default to London)
let observerLocation = (function () {
    if (window.ObserverLocation && ObserverLocation.getForModule) {
        return ObserverLocation.getForModule('observerLocation');
    }
    const saved = localStorage.getItem('observerLocation');
    if (saved) {
        try {
            const parsed = JSON.parse(saved);
            const lat = Number(parsed.lat);
            const lon = Number(parsed.lon);
            if (Number.isFinite(lat) && Number.isFinite(lon)) {
                return { lat, lon };
            }
        } catch (e) { }
    }
    return { lat: 51.5074, lon: -0.1278 };
})();

// GPS Dongle state
let gpsConnected = false;
let gpsEventSource = null;
let gpsAutoConnectTimer = null;
let gpsAutoConnectInFlight = null;
let gpsLastPosition = null;

// Satellite state
let satellitePasses = [];
let selectedPass = null;
let selectedPassIndex = 0;
let countdownInterval = null;

// Start satellite countdown timer
function startCountdownTimer() {
    if (countdownInterval) VisibleInterval.clear(countdownInterval);
    countdownInterval = VisibleInterval.set(updateSatelliteCountdown, 1000);
}

// Update satellite countdown display
function updateSatelliteCountdown() {
    // Update both main and popout countdowns
    updateCountdownDisplay('');
    updateCountdownDisplay('Popout');
}

// Helper to update countdown elements by suffix
function updateCountdownDisplay(suffix) {
    const container = document.getElementById('satelliteCountdown' + suffix);
    if (!container) return;

    // Use the globally selected pass
    if (!selectedPass || satellitePasses.length === 0) {
        container.style.display = 'none';
        return;
    }

    const now = new Date();
    const startTime = parsePassTime(selectedPass.startTime);
    const endTime = new Date(startTime.getTime() + selectedPass.duration * 60000);

    container.style.display = 'block';
    document.getElementById('countdownSatName' + suffix).textContent = selectedPass.satellite;

    if (now >= startTime && now <= endTime) {
        // Currently visible
        const remaining = Math.max(0, Math.floor((endTime - now) / 1000));
        const mins = Math.floor(remaining / 60);
        const secs = remaining % 60;

        document.getElementById('countdownToPass' + suffix).textContent = 'VISIBLE';
        document.getElementById('countdownToPass' + suffix).classList.add('active');
        document.getElementById('countdownPassTime' + suffix).textContent = 'Now overhead';

        document.getElementById('countdownVisibility' + suffix).textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
        document.getElementById('countdownVisLabel' + suffix).textContent = 'Remaining';

        document.getElementById('countdownMaxEl' + suffix).textContent = selectedPass.maxEl + '°';
        document.getElementById('countdownDirection' + suffix).textContent = selectedPass.direction || 'Pass';

        document.getElementById('countdownStatus' + suffix).textContent = 'SATELLITE CURRENTLY VISIBLE';
        document.getElementById('countdownStatus' + suffix).className = 'countdown-status visible';

    } else if (startTime > now) {
        // Upcoming pass
        const secsToPass = Math.max(0, Math.floor((startTime - now) / 1000));
        const hours = Math.floor(secsToPass / 3600);
        const mins = Math.floor((secsToPass % 3600) / 60);
        const secs = secsToPass % 60;

        let countdownStr;
        if (hours > 0) {
            countdownStr = `${hours}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        } else {
            countdownStr = `${mins}:${secs.toString().padStart(2, '0')}`;
        }

        document.getElementById('countdownToPass' + suffix).textContent = countdownStr;
        document.getElementById('countdownToPass' + suffix).classList.remove('active');
        document.getElementById('countdownPassTime' + suffix).textContent = selectedPass.startTime;

        document.getElementById('countdownVisibility' + suffix).textContent = selectedPass.duration + 'm';
        document.getElementById('countdownVisLabel' + suffix).textContent = 'Duration';

        document.getElementById('countdownMaxEl' + suffix).textContent = selectedPass.maxEl + '°';
        document.getElementById('countdownDirection' + suffix).textContent = selectedPass.direction || 'Pass';

        if (secsToPass < 300) {
            document.getElementById('countdownStatus' + suffix).textContent = 'PASS STARTING SOON';
            document.getElementById('countdownStatus' + suffix).className = 'countdown-status upcoming';
        } else {
            document.getElementById('countdownStatus' + suffix).textContent = 'Selected pass';
            document.getElementById('countdownStatus' + suffix).className = 'countdown-status';
        }

    } else {
        // Pass already happened
        document.getElementById('countdownToPass' + suffix).textContent = 'PASSED';
        document.getElementById('countdownToPass' + suffix).classList.remove('active');
        document.getElementById('countdownPassTime' + suffix).textContent = selectedPass.startTime;

        document.getElementById('countdownVisibility' + suffix).textContent = selectedPass.duration + 'm';
        document.getElementById('countdownVisLabel' + suffix).textContent = 'Duration';

        document.getElementById('countdownMaxEl' + suffix).textContent = selectedPass.maxEl + '°';
        document.getElementById('countdownDirection' + suffix).textContent = selectedPass.direction || 'Pass';

        document.getElementById('countdownStatus' + suffix).textContent = 'Pass has ended';
        document.getElementById('countdownStatus' + suffix).className = 'countdown-status';
    }
}

// Parse pass time string to Date object
function parsePassTime(timeStr) {
    // Expected format: "2025-12-21 14:32 UTC"
    // Remove "UTC" suffix and parse as ISO-like format
    const cleanTime = timeStr.replace(' UTC', '').replace(' ', 'T') + ':00Z';
    const parsed = new Date(cleanTime);

    // Fallback if that doesn't work
    if (isNaN(parsed.getTime())) {
        // Try parsing as-is
        return new Date(timeStr.replace(' UTC', ''));
    }
    return parsed;
}

const SIDEBAR_COLLAPSE_KEY = 'mainSidebarCollapsed';

function setMainSidebarCollapsed(collapsed) {
    const mainContent = document.querySelector('.main-content');
    const collapseBtn = document.getElementById('sidebarCollapseBtn');
    if (!mainContent) return;

    mainContent.classList.toggle('sidebar-collapsed', collapsed);
    if (collapseBtn) {
        collapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    }
    localStorage.setItem(SIDEBAR_COLLAPSE_KEY, collapsed ? 'true' : 'false');
}

function toggleMainSidebarCollapse(forceState = null) {
    const mainContent = document.querySelector('.main-content');
    if (!mainContent || window.innerWidth < 1024) return;
    const collapsed = mainContent.classList.contains('sidebar-collapsed');
    const nextState = forceState === null ? !collapsed : !!forceState;
    setMainSidebarCollapsed(nextState);
}

function applySidebarCollapsePreference() {
    const mainContent = document.querySelector('.main-content');
    if (!mainContent) return;
    if (window.innerWidth < 1024) {
        mainContent.classList.remove('sidebar-collapsed');
        return;
    }
    const savedCollapsed = localStorage.getItem(SIDEBAR_COLLAPSE_KEY) === 'true';
    setMainSidebarCollapsed(savedCollapsed);
}

window.addEventListener('resize', applySidebarCollapsePreference);

// Make sections collapsible
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.section h3').forEach(h3 => {
        h3.addEventListener('click', function () {
            this.parentElement.classList.toggle('collapsed');
        });
    });

    // Collapse sidebar menu sections by default, but skip headerless utility blocks.
    document.querySelectorAll('.sidebar .section').forEach((section) => {
        if (section.querySelector('h3')) {
            section.classList.add('collapsed');
        } else {
            section.classList.remove('collapsed');
        }
    });

    applySidebarCollapsePreference();

    // Load bias-T setting from localStorage
    loadBiasTSetting();

    // Initialize device list from server-provided data
    // This ensures currentDeviceList is populated on page load (fixes #99)
    if (typeof deviceList !== 'undefined' && deviceList.length > 0) {
        currentDeviceList = deviceList;
        const firstType = deviceList[0].sdr_type || 'rtlsdr';
        const sdrTypeSelect = document.getElementById('sdrTypeSelect');
        if (sdrTypeSelect) {
            sdrTypeSelect.value = firstType;
        }
        // Defer onSDRTypeChanged to ensure DOM is ready
        setTimeout(onSDRTypeChanged, 0);
    }

    // Initialize observer location input fields from saved location
    const obsLatInput = document.getElementById('obsLat');
    const obsLonInput = document.getElementById('obsLon');
    if (obsLatInput) obsLatInput.value = observerLocation.lat.toFixed(4);
    if (obsLonInput) obsLonInput.value = observerLocation.lon.toFixed(4);

    // Defer GPS auto-connect so it doesn't compete with initial dashboard navigation.
    scheduleGpsAutoConnect();

    // Load pager message filters
    loadPagerFilters();
    if (typeof SignalCards !== 'undefined') SignalCards.updateMutedIndicator();

    // Initialize dropdown nav active state
    updateDropdownActiveState();

    // Restore nav group open/closed state from localStorage
    initNavGroupState();

    // Start SDR device status polling
    startSdrStatusPolling();

    // Apply mode from URL query (e.g., /?mode=wifi)
    applyModeFromQuery();

    // Check for settings=1 query param (from dashboard settings button)
    applySettingsFromQuery();
});

// Toggle section collapse
function toggleSection(el) {
    el.closest('.section').classList.toggle('collapsed');
}

// Dropdown navigation
function toggleNavDropdown(group) {
    const dropdown = document.querySelector(`.mode-nav-dropdown[data-group="${group}"]`);
    const isOpen = dropdown.classList.contains('open');

    // Close all dropdowns first
    document.querySelectorAll('.mode-nav-dropdown').forEach(d => d.classList.remove('open'));

    // Open this one if it was closed
    if (!isOpen) {
        dropdown.classList.add('open');
    }
    saveNavGroupState();
}

function initNavGroupState() {
    const NAV_STATE_KEY = 'intercept_nav_groups';
    let savedState = {};
    try {
        savedState = JSON.parse(localStorage.getItem(NAV_STATE_KEY) || '{}');
    } catch (e) {
        savedState = {};
    }

    document.querySelectorAll('.mode-nav-dropdown[data-group]').forEach(dropdown => {
        const group = dropdown.dataset.group;
        // If saved state says closed AND this group has no active item, close it
        if (savedState[group] === false) {
            const hasActive = dropdown.classList.contains('has-active');
            if (!hasActive) {
                dropdown.classList.remove('open');
                const btn = dropdown.querySelector('.mode-nav-dropdown-btn');
                if (btn) btn.setAttribute('aria-expanded', 'false');
            }
        } else if (savedState[group] === true) {
            dropdown.classList.add('open');
            const btn = dropdown.querySelector('.mode-nav-dropdown-btn');
            if (btn) btn.setAttribute('aria-expanded', 'true');
        }
    });
}

function saveNavGroupState() {
    const NAV_STATE_KEY = 'intercept_nav_groups';
    const state = {};
    document.querySelectorAll('.mode-nav-dropdown[data-group]').forEach(dropdown => {
        state[dropdown.dataset.group] = dropdown.classList.contains('open');
    });
    try {
        localStorage.setItem(NAV_STATE_KEY, JSON.stringify(state));
    } catch (e) { /* storage full or unavailable */ }
}

function closeAllDropdowns() {
    document.querySelectorAll('.mode-nav-dropdown').forEach(d => d.classList.remove('open'));
}

function updateDropdownActiveState() {
    // Remove has-active from all dropdowns
    document.querySelectorAll('.mode-nav-dropdown').forEach(d => d.classList.remove('has-active'));

    // Add has-active to the dropdown containing the current mode
    const activeGroup = modeCatalog[currentMode] ? modeCatalog[currentMode].group : null;
    if (activeGroup) {
        const dropdown = document.querySelector(`.mode-nav-dropdown[data-group="${activeGroup}"]`);
        if (dropdown) dropdown.classList.add('has-active');
    }
}

// Close dropdowns when clicking outside
document.addEventListener('click', function (e) {
    if (!e.target.closest('.mode-nav-dropdown')) {
        closeAllDropdowns();
    }
});

function updateModeUrl(mode, replace = false) {
    if (!validModes.has(mode)) return;
    const url = new URL(window.location.href);
    url.searchParams.set('mode', mode);
    if (replace) {
        window.history.replaceState({ mode }, '', url);
    } else {
        window.history.pushState({ mode }, '', url);
    }
}

// Long enough for a stop to finish: the server signals a mode's
// processes together, and kills and waits for any that ignore it
// (2 s + 2 s; ADS-B allows dump1090 5 s). Giving up sooner showed
// "stopped" while a decoder was still running.
const LOCAL_STOP_TIMEOUT_MS = 8000;
const REMOTE_STOP_TIMEOUT_MS = 8000;
const DASHBOARD_NAV_PATHS = new Set([
    '/adsb/dashboard',
    '/ais/dashboard',
    '/satellite/dashboard',
]);

// Shared module destroy map — closes SSE EventSources, timers, etc.
// Used by both switchMode() and dashboard navigation cleanup.
function getModuleDestroyFn(mode) {
    const def = window.INTERCEPT_MODES[mode];
    if (!def) return null;
    if (def.destroy) return def.destroy;
    if (def.module) {
        return () => {
            const mod = window[def.module];
            if (mod && typeof mod.destroy === 'function') mod.destroy();
        };
    }
    return null;
}

function destroyCurrentMode() {
    if (!currentMode) return;
    const destroyFn = getModuleDestroyFn(currentMode);
    if (destroyFn) {
        try { destroyFn(); } catch(e) { console.warn(`[destroyCurrentMode] destroy ${currentMode} failed:`, e); }
    }
}

function getActiveScanSummary() {
    return {
        pager: Boolean(isRunning),
        sensor: Boolean(isSensorRunning),
        morse: Boolean(
            typeof MorseMode !== 'undefined'
            && typeof MorseMode.isActive === 'function'
            && MorseMode.isActive()
        ),
        wifi: Boolean(
            ((typeof WiFiMode !== 'undefined' && typeof WiFiMode.isScanning === 'function' && WiFiMode.isScanning()) || isWifiRunning)
        ),
        bluetooth: Boolean(
            ((typeof BluetoothMode !== 'undefined' && typeof BluetoothMode.isScanning === 'function' && BluetoothMode.isScanning()) || isBtRunning)
        ),
        aprs: Boolean(typeof isAprsRunning !== 'undefined' && isAprsRunning),
        tscm: Boolean(typeof isTscmRunning !== 'undefined' && isTscmRunning),
    };
}

function stopActiveLocalScansForNavigation() {
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
    if (isAgentMode) return;

    if (isRunning && typeof stopDecoding === 'function') {
        Promise.resolve(stopDecoding()).catch(() => { });
    }
    if (isSensorRunning && typeof stopSensorDecoding === 'function') {
        Promise.resolve(stopSensorDecoding()).catch(() => { });
    }
    const morseActive = typeof MorseMode !== 'undefined'
        && typeof MorseMode.isActive === 'function'
        && MorseMode.isActive();
    if (morseActive && typeof MorseMode.stop === 'function') {
        Promise.resolve(MorseMode.stop()).catch(() => { });
    }

    const wifiScanActive = (
        typeof WiFiMode !== 'undefined'
        && typeof WiFiMode.isScanning === 'function'
        && WiFiMode.isScanning()
    ) || isWifiRunning;
    if (wifiScanActive && typeof stopWifiScan === 'function') {
        Promise.resolve(stopWifiScan()).catch(() => { });
    }

    const btScanActive = (
        typeof BluetoothMode !== 'undefined'
        && typeof BluetoothMode.isScanning === 'function'
        && BluetoothMode.isScanning()
    ) || isBtRunning;
    if (btScanActive && typeof stopBtScan === 'function') {
        Promise.resolve(stopBtScan()).catch(() => { });
    }

    if (typeof isAprsRunning !== 'undefined' && isAprsRunning && typeof stopAprs === 'function') {
        Promise.resolve(stopAprs()).catch(() => { });
    }
    if (typeof isTscmRunning !== 'undefined' && isTscmRunning && typeof stopTscmSweep === 'function') {
        Promise.resolve(stopTscmSweep()).catch(() => { });
    }

    // Additional modes with server-side processes that need stopping
    if (typeof WeFax !== 'undefined' && typeof WeFax.stop === 'function') {
        Promise.resolve(WeFax.stop()).catch(() => { });
    }
    if (typeof WeatherSat !== 'undefined' && typeof WeatherSat.stop === 'function') {
        Promise.resolve(WeatherSat.stop()).catch(() => { });
    }
    if (typeof SSTV !== 'undefined' && typeof SSTV.stop === 'function') {
        Promise.resolve(SSTV.stop()).catch(() => { });
    }
    if (typeof SSTVGeneral !== 'undefined' && typeof SSTVGeneral.stop === 'function') {
        Promise.resolve(SSTVGeneral.stop()).catch(() => { });
    }
    if (typeof SubGhz !== 'undefined' && typeof SubGhz.stop === 'function') {
        Promise.resolve(SubGhz.stop()).catch(() => { });
    }
    if (typeof Meshtastic !== 'undefined' && typeof Meshtastic.stop === 'function') {
        Promise.resolve(Meshtastic.stop()).catch(() => { });
    }
    if (typeof GPS !== 'undefined' && typeof GPS.stop === 'function') {
        Promise.resolve(GPS.stop()).catch(() => { });
    }
}

if (!window._dashboardNavigationStopHookBound) {
    window._dashboardNavigationStopHookBound = true;
    document.addEventListener('click', (event) => {
        if (event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

        const link = event.target && event.target.closest
            ? event.target.closest('a[href]')
            : null;
        if (!link || link.target === '_blank') return;

        try {
            const href = new URL(link.href, window.location.href);
            if (href.origin !== window.location.origin) return;
            if (!DASHBOARD_NAV_PATHS.has(href.pathname)) return;
            if (window.InterceptNavPerf && typeof window.InterceptNavPerf.markStart === 'function') {
                window.InterceptNavPerf.markStart({
                    targetPath: href.pathname,
                    trigger: 'index-link',
                    sourceMode: currentMode,
                    activeScans: getActiveScanSummary(),
                });
            }
            // Let dedicated dashboards navigate immediately.
            // Pre-navigation stop requests from active modes like Pager
            // can stall same-tab navigation badly on some browsers.
            destroyCurrentMode();
        } catch (_) {
            // Ignore malformed hrefs.
        }
    });
}

if (!window._dashboardHomeBtnBound) {
    window._dashboardHomeBtnBound = true;
    document.addEventListener('click', (event) => {
        if (event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const link = event.target && event.target.closest
            ? event.target.closest('.nav-dashboard-btn')
            : null;
        if (!link) return;
        try {
            const href = new URL(link.href, window.location.href);
            if (href.origin !== window.location.origin || href.pathname !== '/') return;
        } catch (_) { return; }
        event.preventDefault();
        stopActiveLocalScansForNavigation();
        destroyCurrentMode();
        const welcome = document.getElementById('welcomePage');
        if (welcome) {
            welcome.classList.remove('fade-out');
            welcome.style.display = '';
        }
        window.history.pushState({}, '', '/');
    });
}

function postStopRequest(url, timeoutMs = LOCAL_STOP_TIMEOUT_MS) {
    const controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    const timeoutId = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
    const started = performance.now();
    return fetch(url, {
        method: 'POST',
        ...(controller ? { signal: controller.signal } : {}),
    })
        .then((response) => response.json().catch(() => ({ status: response.ok ? 'ok' : 'error' })))
        .catch((err) => {
            if (err && err.name === 'AbortError') {
                console.warn(`[Stop] ${url} timed out after ${timeoutMs}ms`);
                return { status: 'timeout', timed_out: true };
            }
            console.warn(`[Stop] ${url} failed: ${err?.message || err}`);
            return { status: 'error', message: err?.message || String(err) };
        })
        .finally(() => {
            if (timeoutId) clearTimeout(timeoutId);
            const elapsedMs = Math.round(performance.now() - started);
            console.debug(`[Stop] ${url} finished in ${elapsedMs}ms`);
        });
}

/**
 * After a local stop that did not confirm, ask the mode whether its
 * decoder is still running. If it is, show it running again and say
 * so, rather than showing "stopped" over a radio that is not.
 */
async function confirmStopped(label, statusUrl, result, onStillRunning) {
    if (result && (result.status === 'stopped' || result.status === 'not_running')) return true;
    try {
        const status = await (await fetch(statusUrl)).json();
        if (!status.running) return true;
    } catch (err) {
        // status unknown: say nothing rather than guess
        return true;
    }
    onStillRunning();
    showError(`${label} did not stop. Use "Stop all running processes" (⊗ in the toolbar) to stop it.`);
    return false;
}

async function awaitStopAction(name, action, timeoutMs = LOCAL_STOP_TIMEOUT_MS) {
    const started = performance.now();
    try {
        const result = action();
        const promise = (result && typeof result.then === 'function')
            ? result
            : Promise.resolve(result);
        await Promise.race([
            promise,
            new Promise((resolve) => setTimeout(resolve, timeoutMs)),
        ]);
    } catch (err) {
        console.warn(`[ModeSwitch] stop ${name} failed: ${err?.message || err}`);
    } finally {
        const elapsedMs = Math.round(performance.now() - started);
        console.debug(`[ModeSwitch] stop ${name} finished in ${elapsedMs}ms`);
    }
}

let modeSwitchRequestId = 0;

// Mode switching
async function switchMode(mode, options = {}) {
    const requestId = ++modeSwitchRequestId;
    const { updateUrl = true } = options;
    const switchStartMs = performance.now();
    const previousMode = currentMode;
    if (mode === 'listening') mode = 'waterfall';
    if (mode === 'satellite') {
        window.open('/satellite/dashboard', '_blank', 'noopener');
        return;
    }
    if (!validModes.has(mode)) mode = 'pager';
    const _modeAssetTimeout = (p) =>
        Promise.race([p, new Promise((r) => setTimeout(r, 5000))]);
    const styleReadyPromise = (typeof window.ensureModeStyles === 'function')
        ? _modeAssetTimeout(Promise.resolve(window.ensureModeStyles(mode)).catch((err) => {
            console.warn(`[ModeSwitch] style load failed for ${mode}: ${err?.message || err}`);
        }))
        : Promise.resolve();
    const scriptReadyPromise = (typeof window.ensureModeScript === 'function')
        ? _modeAssetTimeout(Promise.resolve(window.ensureModeScript(mode)).catch((err) => {
            console.warn(`[ModeSwitch] script load failed for ${mode}: ${err?.message || err}`);
        }))
        : Promise.resolve();
    // Only stop local scans if in local mode (not agent mode)
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
    const stopPhaseStartMs = performance.now();
    let stopTaskCount = 0;
    if (!isAgentMode) {
        const stopTasks = [];

        if (isRunning) {
            stopTasks.push(awaitStopAction('pager', () => stopDecoding(), LOCAL_STOP_TIMEOUT_MS));
        }
        if (isSensorRunning) {
            stopTasks.push(awaitStopAction('sensor', () => stopSensorDecoding(), LOCAL_STOP_TIMEOUT_MS));
        }
        const morseActive = typeof MorseMode !== 'undefined'
            && typeof MorseMode.isActive === 'function'
            && MorseMode.isActive();
        if (morseActive && typeof MorseMode.stop === 'function') {
            stopTasks.push(awaitStopAction('morse', () => MorseMode.stop(), LOCAL_STOP_TIMEOUT_MS));
        }
        const wifiScanActive = (
            typeof WiFiMode !== 'undefined'
            && typeof WiFiMode.isScanning === 'function'
            && WiFiMode.isScanning()
        ) || isWifiRunning;
        const isWifiModeTransition =
            (currentMode === 'wifi' && mode === 'wifi_locate') ||
            (currentMode === 'wifi_locate' && mode === 'wifi');
        if (wifiScanActive && !isWifiModeTransition) {
            stopTasks.push(awaitStopAction('wifi', () => stopWifiScan(), LOCAL_STOP_TIMEOUT_MS));
        }
        const btScanActive = (typeof BluetoothMode !== 'undefined' &&
            typeof BluetoothMode.isScanning === 'function' &&
            BluetoothMode.isScanning()) || isBtRunning;
        const isBtModeTransition =
            (currentMode === 'bluetooth' && mode === 'bt_locate') ||
            (currentMode === 'bt_locate' && mode === 'bluetooth');
        if (btScanActive && !isBtModeTransition && typeof stopBtScan === 'function') {
            stopTasks.push(awaitStopAction('bluetooth', () => stopBtScan(), LOCAL_STOP_TIMEOUT_MS));
        }
        if (isAprsRunning) {
            stopTasks.push(awaitStopAction('aprs', () => stopAprs(), LOCAL_STOP_TIMEOUT_MS));
        }
        if (isTscmRunning) {
            stopTasks.push(awaitStopAction('tscm', () => stopTscmSweep(), LOCAL_STOP_TIMEOUT_MS));
        }
        if (isDroneRunning) {
            stopTasks.push(awaitStopAction('drone', () => fetch('/drone/stop', { method: 'POST' }), LOCAL_STOP_TIMEOUT_MS));
        }
        if (isRtlamrRunning) {
            stopTasks.push(awaitStopAction('rtlamr', () => stopRtlamrDecoding(), LOCAL_STOP_TIMEOUT_MS));
        }

        if (stopTasks.length) {
            await Promise.allSettled(stopTasks);
        }
        stopTaskCount = stopTasks.length;
    }
    const stopPhaseMs = Math.round(performance.now() - stopPhaseStartMs);
    await styleReadyPromise;
    await scriptReadyPromise;
    if (requestId !== modeSwitchRequestId) return;

    // Generic module cleanup — destroy previous mode's timers, SSE, etc.
    if (previousMode && previousMode !== mode) {
        const destroyFn = getModuleDestroyFn(previousMode);
        if (destroyFn) {
            try { destroyFn(); } catch(e) { console.warn(`[switchMode] destroy ${previousMode} failed:`, e); }
        }
    }
    if (requestId !== modeSwitchRequestId) return;

    currentMode = mode;
    document.body.setAttribute('data-mode', mode);
    if (updateUrl) {
        updateModeUrl(mode);
    }

    // Sync mode state with current agent/local after switching
    if (isAgentMode && typeof syncAgentModeStates === 'function') {
        // Re-sync with agent to update this mode's UI state
        syncAgentModeStates(currentAgent);
    } else if (!isAgentMode && typeof syncLocalModeStates === 'function') {
        // Sync with local status
        syncLocalModeStates();
    }
    if (requestId !== modeSwitchRequestId) return;

    // Close dropdowns and update active state
    closeAllDropdowns();
    updateDropdownActiveState();

    // Remove active from all nav buttons, then add to the correct one
    document.querySelectorAll('.mode-nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    document.querySelectorAll('.mobile-nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    const activeMobileBtn = document.querySelector('.mobile-nav-btn.active');
    if (activeMobileBtn) {
        activeMobileBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    }
    for (const [m, def] of Object.entries(window.INTERCEPT_MODES)) {
        if (def.elementId) {
            document.getElementById(def.elementId)?.classList.toggle('active', mode === m);
        }
    }


    document.getElementById('pagerStats')?.classList.toggle('active', mode === 'pager');
    document.getElementById('sensorStats')?.classList.toggle('active', mode === 'sensor');
    document.getElementById('satelliteStats')?.classList.toggle('active', mode === 'satellite');
    document.getElementById('wifiStats')?.classList.toggle('active', mode === 'wifi');

    // Update header stats groups
    document.getElementById('headerPagerStats')?.classList.toggle('active', mode === 'pager');
    document.getElementById('headerSensorStats')?.classList.toggle('active', mode === 'sensor');
    document.getElementById('headerSatelliteStats')?.classList.toggle('active', mode === 'satellite');
    document.getElementById('headerWifiStats')?.classList.toggle('active', mode === 'wifi');

    // Show/hide dashboard buttons in nav bar
    const satelliteDashboardBtn = document.getElementById('satelliteDashboardBtn');
    if (satelliteDashboardBtn) satelliteDashboardBtn.style.display = mode === 'satellite' ? 'inline-flex' : 'none';

    // Update active mode indicator
    const modeMeta = modeCatalog[mode] || {};
    setActiveModeIndicator(modeMeta.indicator || mode.toUpperCase());
    const wifiLayoutContainer = document.getElementById('wifiLayoutContainer');
    const btLayoutContainer = document.getElementById('btLayoutContainer');
    const satelliteVisuals = document.getElementById('satelliteVisuals');
    const aprsVisuals = document.getElementById('aprsVisuals');
    const tscmVisuals = document.getElementById('tscmVisuals');
    const spyStationsVisuals = document.getElementById('spyStationsVisuals');
    const meshtasticVisuals = document.getElementById('meshtasticVisuals');
    const meshcoreVisuals = document.getElementById('meshcoreVisuals');
    const sstvVisuals = document.getElementById('sstvVisuals');
    const weatherSatVisuals = document.getElementById('weatherSatVisuals');
    const sstvGeneralVisuals = document.getElementById('sstvGeneralVisuals');
    const gpsVisuals = document.getElementById('gpsVisuals');
    const websdrVisuals = document.getElementById('websdrVisuals');
    const subghzVisuals = document.getElementById('subghzVisuals');
    const btLocateVisuals = document.getElementById('btLocateVisuals');
    const wflVisuals = document.getElementById('wflVisuals');
    const wefaxVisuals = document.getElementById('wefaxVisuals');
    const spaceWeatherVisuals = document.getElementById('spaceWeatherVisuals');
    const waterfallVisuals = document.getElementById('waterfallVisuals');
    const radiosondeVisuals = document.getElementById('radiosondeVisuals');
    const meteorVisuals = document.getElementById('meteorVisuals');
    const systemVisuals = document.getElementById('systemVisuals');
    const droneVisuals = document.getElementById('droneVisuals');
    if (wifiLayoutContainer) wifiLayoutContainer.classList.toggle('active', mode === 'wifi');
    if (btLayoutContainer) btLayoutContainer.classList.toggle('active', mode === 'bluetooth');
    if (satelliteVisuals) satelliteVisuals.style.display = mode === 'satellite' ? 'block' : 'none';
    const satFrame = document.getElementById('satelliteDashboardFrame');
    if (satFrame && mode === 'satellite') {
        const baseSrc = satFrame.dataset.src || ('/satellite/dashboard?embedded=true&v=' + (window.INTERCEPT_VERSION || ''));
        const currentSrc = satFrame.getAttribute('src') || '';
        if (!currentSrc || currentSrc === 'about:blank') {
            satFrame.src = `${baseSrc}&ts=${Date.now()}`;
        }
    } else if (satFrame) {
        const currentSrc = satFrame.getAttribute('src') || '';
        if (currentSrc && currentSrc !== 'about:blank') {
            satFrame.src = 'about:blank';
        }
    }
    if (satFrame && satFrame.contentWindow && satFrame.getAttribute('src') && satFrame.getAttribute('src') !== 'about:blank') {
        satFrame.contentWindow.postMessage({type: 'satellite-visibility', visible: mode === 'satellite'}, '*');
    }

    // Weather-sat handoff: when switching away from satellite mode, clear any pending handoff banner
    if (mode !== 'satellite' && mode !== 'weathersat') {
        const existing = document.getElementById('weatherSatHandoffBanner');
        if (existing) existing.remove();
    }
    if (aprsVisuals) aprsVisuals.style.display = mode === 'aprs' ? 'flex' : 'none';
    if (tscmVisuals) tscmVisuals.style.display = mode === 'tscm' ? 'flex' : 'none';
    if (spyStationsVisuals) spyStationsVisuals.style.display = mode === 'spystations' ? 'flex' : 'none';
    if (meshtasticVisuals) meshtasticVisuals.style.display = mode === 'meshtastic' ? 'flex' : 'none';
    if (meshcoreVisuals) meshcoreVisuals.style.display = mode === 'meshcore' ? 'flex' : 'none';
    if (sstvVisuals) sstvVisuals.style.display = mode === 'sstv' ? 'flex' : 'none';
    if (weatherSatVisuals) weatherSatVisuals.style.display = mode === 'weathersat' ? 'flex' : 'none';
    if (sstvGeneralVisuals) sstvGeneralVisuals.style.display = mode === 'sstv_general' ? 'flex' : 'none';
    if (gpsVisuals) gpsVisuals.style.display = mode === 'gps' ? 'flex' : 'none';
    if (websdrVisuals) websdrVisuals.style.display = mode === 'websdr' ? 'flex' : 'none';
    if (subghzVisuals) subghzVisuals.style.display = mode === 'subghz' ? 'flex' : 'none';
    if (btLocateVisuals) btLocateVisuals.style.display = mode === 'bt_locate' ? 'flex' : 'none';
    if (wflVisuals) wflVisuals.style.display = mode === 'wifi_locate' ? 'flex' : 'none';
    if (wefaxVisuals) wefaxVisuals.style.display = mode === 'wefax' ? 'flex' : 'none';
    if (spaceWeatherVisuals) spaceWeatherVisuals.style.display = mode === 'spaceweather' ? 'flex' : 'none';
    if (waterfallVisuals) waterfallVisuals.style.display = mode === 'waterfall' ? 'flex' : 'none';
    if (radiosondeVisuals) radiosondeVisuals.style.display = mode === 'radiosonde' ? 'flex' : 'none';
    if (meteorVisuals) meteorVisuals.style.display = mode === 'meteor' ? 'flex' : 'none';
    if (systemVisuals) systemVisuals.style.display = mode === 'system' ? 'flex' : 'none';
    if (droneVisuals) droneVisuals.style.display = mode === 'drone' ? 'flex' : 'none';

    // Hide the signal feed output for modes that have their own visuals
    const outputEl = document.getElementById('output');
    const signalViewWrapEl = document.getElementById('signalViewWrap');
    const modesWithVisuals = Object.keys(window.INTERCEPT_MODES)
        .filter((m) => window.INTERCEPT_MODES[m].visuals);
    if (modesWithVisuals.includes(mode)) {
        if (signalViewWrapEl) signalViewWrapEl.style.display = 'none';
        if (outputEl) outputEl.style.display = 'none';
    } else {
        if (signalViewWrapEl) signalViewWrapEl.style.display = '';
        if (outputEl) outputEl.style.display = 'block';
    }
    if (typeof PagerDirectory  !== 'undefined') PagerDirectory.applyViewState(mode);
    if (typeof SensorDashboard !== 'undefined') SensorDashboard.applyViewState(mode);

    // Prevent Leaflet heatmap redraws on hidden BT Locate map containers.
    if (typeof BtLocate !== 'undefined' && BtLocate.setActiveMode) {
        BtLocate.setActiveMode(mode === 'bt_locate');
    }
    if (typeof WiFiLocate !== 'undefined' && WiFiLocate.setActiveMode) {
        WiFiLocate.setActiveMode(mode === 'wifi_locate');
    }

    // Hide sidebar by default for Meshtastic mode, show for others
    const mainContent = document.querySelector('.main-content');
    if (mainContent) {
        if (mode === 'meshtastic') {
            mainContent.classList.add('mesh-sidebar-hidden');
        } else if (mode === 'meshcore') {
            mainContent.classList.add('mesh-sidebar-hidden');
        } else {
            mainContent.classList.remove('mesh-sidebar-hidden');
        }
    }

    // Show/hide mode-specific timeline containers
    const pagerTimelineContainer = document.getElementById('pagerTimelineContainer');
    const sensorTimelineContainer = document.getElementById('sensorTimelineContainer');
    if (pagerTimelineContainer) pagerTimelineContainer.style.display = mode === 'pager' ? 'block' : 'none';
    if (sensorTimelineContainer) sensorTimelineContainer.style.display = mode === 'sensor' ? 'block' : 'none';
    const pagerScopePanel = document.getElementById('pagerScopePanel');
    if (pagerScopePanel && mode !== 'pager') pagerScopePanel.style.display = 'none';
    const sensorScopePanel = document.getElementById('sensorScopePanel');
    if (sensorScopePanel && mode !== 'sensor') sensorScopePanel.style.display = 'none';
    const morseScopePanel = document.getElementById('morseScopePanel');
    const morseOutputPanel = document.getElementById('morseOutputPanel');
    if (morseScopePanel && mode !== 'morse') morseScopePanel.style.display = 'none';
    if (morseOutputPanel && mode !== 'morse') morseOutputPanel.style.display = 'none';
    const morseDiagLog = document.getElementById('morseDiagLog');
    if (morseDiagLog && mode !== 'morse') morseDiagLog.style.display = 'none';
    const ookOutputPanel = document.getElementById('ookOutputPanel');
    if (ookOutputPanel && mode !== 'ook') ookOutputPanel.style.display = 'none';

    // Update output panel title based on mode
    const outputTitle = document.getElementById('outputTitle');
    if (outputTitle) outputTitle.textContent = modeMeta.outputTitle || 'Signal Monitor';

    // Initialize mode-specific timelines
    initializeModeTimeline(mode);

    // Initialize TSCM mode when selected
    if (mode === 'tscm') {
        loadTscmBaselines();
        refreshTscmDevices();
        updateTscmIgnoreListUI();
    }

    // Initialize Drone mode when selected
    if (mode === 'drone') {
        refreshDroneDevices();
    }

    // Module destroy is now handled by the mode registry (static/js/mode-registry.js).

    // Show/hide Device Intelligence for modes that use it (not for satellite/aircraft/tscm)
    const reconBtn = document.getElementById('reconBtn');
    const intelBtn = document.querySelector('[onclick="exportDeviceDB()"]');
    const reconPanel = document.getElementById('reconPanel');
    const hideRecon = ['satellite', 'sstv', 'weathersat', 'sstv_general', 'wefax', 'gps', 'aprs', 'tscm', 'tscmsurvey', 'activity', 'spystations', 'meshtastic', 'meshcore', 'websdr', 'subghz', 'spaceweather', 'waterfall', 'meteor', 'system'].includes(mode);
    if (reconPanel) reconPanel.style.display = (!hideRecon && reconEnabled) ? 'block' : 'none';
    if (reconBtn) reconBtn.style.display = hideRecon ? 'none' : 'inline-block';
    if (intelBtn) intelBtn.style.display = hideRecon ? 'none' : 'inline-block';

    // Show agent selector for modes that support remote agents
    const agentSection = document.getElementById('agentSection');
    const agentModes = ['pager', 'sensor', 'rtlamr', 'aprs', 'wifi', 'bluetooth', 'aircraft', 'tscm'];
    if (agentSection) agentSection.style.display = agentModes.includes(mode) ? 'block' : 'none';

    // Show RTL-SDR device section for modes that use it
    const rtlDeviceSection = document.getElementById('rtlDeviceSection');
    if (rtlDeviceSection) {
        const showRtl = ['pager', 'sensor', 'rtlamr', 'aprs', 'sstv', 'weathersat', 'sstv_general', 'wefax', 'morse', 'radiosonde', 'meteor', 'ook'].includes(mode);
        rtlDeviceSection.classList.toggle('active', showRtl);
        // Save original sidebar position of SDR device section (once)
        if (!rtlDeviceSection._origParent) {
            rtlDeviceSection._origParent = rtlDeviceSection.parentNode;
            rtlDeviceSection._origNext = rtlDeviceSection.nextElementSibling;
        }
        // For morse/radiosonde/meteor/ook modes, move SDR device section inside the panel after the title
        const morsePanel = document.getElementById('morseMode');
        const radiosondePanel = document.getElementById('radiosondeMode');
        const meteorPanel = document.getElementById('meteorMode');
        const ookPanel = document.getElementById('ookMode');
        if (mode === 'morse' && morsePanel) {
            const firstSection = morsePanel.querySelector('.section');
            if (firstSection) firstSection.after(rtlDeviceSection);
        } else if (mode === 'radiosonde' && radiosondePanel) {
            const firstSection = radiosondePanel.querySelector('.section');
            if (firstSection) firstSection.after(rtlDeviceSection);
        } else if (mode === 'meteor' && meteorPanel) {
            const firstSection = meteorPanel.querySelector('.section');
            if (firstSection) firstSection.after(rtlDeviceSection);
        } else if (mode === 'ook' && ookPanel) {
            const firstSection = ookPanel.querySelector('.section');
            if (firstSection) firstSection.after(rtlDeviceSection);
        } else if (rtlDeviceSection._origParent && rtlDeviceSection.parentNode !== rtlDeviceSection._origParent) {
            // Restore to original sidebar position when leaving morse mode
            if (rtlDeviceSection._origNext) {
                rtlDeviceSection._origNext.before(rtlDeviceSection);
            } else {
                rtlDeviceSection._origParent.appendChild(rtlDeviceSection);
            }
        }
    }

    // Toggle mode-specific tool status displays
    document.getElementById('toolStatusPager')?.classList.toggle('active', mode === 'pager');
    document.getElementById('toolStatusSensor')?.classList.toggle('active', mode === 'sensor');

    // The bottom bar's controls (recon, mute, auto-scroll, export, clear) act on the
    // shared message feed, which only these modes fill; elsewhere they did nothing.
    const showStatusBar = ['pager', 'sensor', 'rtlamr', 'ook'].includes(mode);
    const statusBar = document.querySelector('.status-bar');
    if (statusBar) statusBar.style.display = showStatusBar ? 'flex' : 'none';

    // Restore sidebar when leaving Meshtastic mode (user may have collapsed it)
    if (mode !== 'meshtastic' && mode !== 'meshcore') {
        const mainContent = document.querySelector('.main-content');
        if (mainContent) {
            mainContent.classList.remove('mesh-sidebar-hidden');
        }
    }

    // Load interfaces and initialize visualizations when switching modes
    const modeDef = window.INTERCEPT_MODES[mode];
    if (modeDef && typeof modeDef.init === 'function') {
        const runInit = () => {
            try {
                modeDef.init();
            } catch (err) {
                console.error(`Mode init failed for ${mode}:`, err);
            }
        };
        // The switch waits at most 5 s for the mode's script. If it is still
        // loading (a slow device or connection), start the mode when it
        // arrives, rather than now without it ("X is not defined").
        const scriptSrc = window.INTERCEPT_MODE_SCRIPT_MAP && window.INTERCEPT_MODE_SCRIPT_MAP[mode];
        const stillLoading = scriptSrc && !window.INTERCEPT_MODE_SCRIPT_LOADED[scriptSrc]
            ? window.INTERCEPT_MODE_SCRIPT_PROMISES[scriptSrc] : null;
        if (stillLoading) {
            stillLoading.then(() => { if (requestId === modeSwitchRequestId) runInit(); }, () => {});
        } else {
            runInit();
        }
    }
    if (requestId !== modeSwitchRequestId) return;

    // Waterfall destroy is now handled by the mode registry (static/js/mode-registry.js).

    const totalMs = Math.round(performance.now() - switchStartMs);
    console.info(
        `[Perf] switchMode ${previousMode} -> ${mode}: stop=${stopPhaseMs}ms tasks=${stopTaskCount} total=${totalMs}ms`,
        {
            updateUrl,
            agentMode: isAgentMode,
        }
    );
    requestAnimationFrame(() => {
        const firstFrameMs = Math.round(performance.now() - switchStartMs);
        console.info(`[Perf] switchMode ${previousMode} -> ${mode}: first-frame=${firstFrameMs}ms`);
    });
}

// Handle window resize for maps (especially important on mobile orientation change)
window.addEventListener('resize', function () {
    if (aprsMap) aprsMap.invalidateSize();
    if (typeof Meshtastic !== 'undefined') Meshtastic.invalidateMap();
    if (typeof BtLocate !== 'undefined') BtLocate.invalidateMap();
    if (typeof SSTV !== 'undefined' && SSTV.invalidateMap) SSTV.invalidateMap();
});

window.addEventListener('popstate', function () {
    const mode = getModeFromQuery();
    if (mode && mode !== currentMode) {
        switchMode(mode, { updateUrl: false });
    } else if (!mode) {
        destroyCurrentMode();
        const welcome = document.getElementById('welcomePage');
        if (welcome) {
            welcome.classList.remove('fade-out');
            welcome.style.display = '';
        }
    }
});

// Also handle orientation changes explicitly for mobile
window.addEventListener('orientationchange', function () {
    setTimeout(() => {
        if (aprsMap) aprsMap.invalidateSize();
        if (typeof Meshtastic !== 'undefined') Meshtastic.invalidateMap();
        if (typeof SSTV !== 'undefined' && SSTV.invalidateMap) SSTV.invalidateMap();
    }, 200);
});


// NOTE: Audio alert settings moved to static/js/core/audio.js

// Message storage for export
let allMessages = [];

function exportCSV() {
    if (currentMode === 'ook') { OokMode.exportLog(); return; }
    if (allMessages.length === 0) {
        alert('No messages to export');
        return;
    }
    const headers = ['Timestamp', 'Protocol', 'Address', 'Function', 'Type', 'Message'];
    const csv = [headers.join(',')];
    allMessages.forEach(msg => {
        const row = [
            msg.timestamp || '',
            msg.protocol || '',
            msg.address || '',
            msg.function || '',
            msg.msg_type || '',
            '"' + (msg.message || '').replace(/"/g, '""') + '"'
        ];
        csv.push(row.join(','));
    });
    downloadFile(csv.join('\n'), 'intercept_messages.csv', 'text/csv');
}

function exportJSON() {
    if (currentMode === 'ook') { OokMode.exportJSON(); return; }
    if (allMessages.length === 0) {
        alert('No messages to export');
        return;
    }
    downloadFile(JSON.stringify(allMessages, null, 2), 'intercept_messages.json', 'application/json');
}

function downloadFile(content, filename, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

// Auto-scroll setting
let autoScroll = localStorage.getItem('autoScroll') !== 'false';

function toggleAutoScroll() {
    autoScroll = !autoScroll;
    localStorage.setItem('autoScroll', autoScroll);
    updateAutoScrollButton();
}

function updateAutoScrollButton() {
    const btn = document.getElementById('autoScrollBtn');
    if (btn) {
        btn.innerHTML = autoScroll ? '⬇ AUTO-SCROLL ON' : '⬇ AUTO-SCROLL OFF';
        btn.classList.toggle('active', autoScroll);
    }
}

// Signal activity meter
let signalActivity = 0;
let lastMessageTime = 0;

function updateSignalMeter() {
    const now = Date.now();
    const timeSinceLastMsg = now - lastMessageTime;

    // Decay signal activity over time
    if (timeSinceLastMsg > 1000) {
        signalActivity = Math.max(0, signalActivity - 0.05);
    }

    const meter = document.getElementById('signalMeter');
    const bars = meter?.querySelectorAll('.signal-bar');
    if (bars) {
        const activeBars = Math.ceil(signalActivity * bars.length);
        bars.forEach((bar, i) => {
            bar.classList.toggle('active', i < activeBars);
        });
    }
}

function pulseSignal() {
    signalActivity = Math.min(1, signalActivity + 0.4);
    lastMessageTime = Date.now();
}

// Relative timestamps: rendered by InterceptTime, whose one page-wide
// clock keeps every .msg-time, card and device timestamp current.
function getRelativeTime(timestamp) {
    return InterceptTime.relative(timestamp);
}

// Update timers
VisibleInterval.set(updateSignalMeter, 100);

// Default presets (UK frequencies)
const defaultPresets = ['153.350', '153.025'];

// Load presets from localStorage or use defaults
function loadPresets() {
    const saved = localStorage.getItem('pagerPresets');
    return saved ? JSON.parse(saved) : [...defaultPresets];
}

function savePresets(presets) {
    localStorage.setItem('pagerPresets', JSON.stringify(presets));
}

function renderPresets() {
    const presets = loadPresets();
    const container = document.getElementById('presetButtons');
    container.innerHTML = presets.map(freq =>
        `<button class="preset-btn" onclick="setFreq('${freq}')" oncontextmenu="removePreset('${freq}'); return false;" title="Right-click to remove">${freq}</button>`
    ).join('');
}

function addPreset() {
    const input = document.getElementById('newPresetFreq');
    const freq = input.value.trim();
    if (!freq || isNaN(parseFloat(freq))) {
        alert('Please enter a valid frequency');
        return;
    }
    const presets = loadPresets();
    if (!presets.includes(freq)) {
        presets.push(freq);
        savePresets(presets);
        renderPresets();
    }
    input.value = '';
}

async function removePreset(freq) {
    const confirmed = await AppFeedback.confirmAction({
        title: 'Remove Preset',
        message: 'Remove preset ' + freq + ' MHz?',
        confirmLabel: 'Remove',
        confirmClass: 'btn-danger'
    });
    if (confirmed) {
        let presets = loadPresets();
        presets = presets.filter(p => p !== freq);
        savePresets(presets);
        renderPresets();
    }
}

async function resetPresets() {
    const confirmed = await AppFeedback.confirmAction({
        title: 'Reset Presets',
        message: 'Reset to default presets?',
        confirmLabel: 'Reset',
        confirmClass: 'btn-danger'
    });
    if (confirmed) {
        savePresets([...defaultPresets]);
        renderPresets();
    }
}

// Initialize presets on load
renderPresets();

// Initialize button states on load
updateMuteButton();
updateAutoScrollButton();

// NOTE: Audio context initialization moved to static/js/core/audio.js

function setFreq(freq) {
    document.getElementById('frequency').value = freq;
    // Auto-restart decoder with new frequency if currently running
    if (isRunning) {
        fetch('/stop', { method: 'POST' })
            .then(() => {
                setTimeout(() => startDecoding(), 500);
            });
    }
}
