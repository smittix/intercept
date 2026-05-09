/**
 * Intercept - Core Application Logic
 * Global state, mode switching, and shared functionality
 */

// ============== GLOBAL STATE ==============

// Mode state flags
let eventSource = null;
let isRunning = false;
let isSensorRunning = false;
let isAdsbRunning = false;
let isWifiRunning = false;
let isBtRunning = false;
let currentMode = 'pager';

// Message counters
let msgCount = 0;
let pocsagCount = 0;
let flexCount = 0;
let sensorCount = 0;
let filteredCount = 0;

// Device list (populated from server via Jinja2)
let deviceList = [];

// Auto-scroll setting
let autoScroll = localStorage.getItem('autoScroll') !== 'false';

// Mute setting
let muted = localStorage.getItem('audioMuted') === 'true';

// Observer location (load from localStorage or default to London)
let observerLocation = (function() {
    if (window.ObserverLocation && ObserverLocation.getForModule) {
        return ObserverLocation.getForModule('observerLocation');
    }
    const saved = localStorage.getItem('observerLocation');
            if (saved) {
                try {
                    const parsed = JSON.parse(saved);
                    if (parsed.lat !== undefined && parsed.lat !== null && parsed.lon !== undefined && parsed.lon !== null) return parsed;
                } catch (e) {}
            }
    return { lat: 51.5074, lon: -0.1278 };
})();

// Message storage for export
let allMessages = [];

// Track unique sensor devices
let uniqueDevices = new Set();

// SDR device usage tracking
let sdrDeviceUsage = {};

// ============== DISCLAIMER HANDLING ==============

function checkDisclaimer() {
    const accepted = localStorage.getItem('disclaimerAccepted');
    if (accepted === 'true') {
        document.getElementById('disclaimerModal').classList.add('disclaimer-hidden');
    }
}

function acceptDisclaimer() {
    localStorage.setItem('disclaimerAccepted', 'true');
    document.getElementById('disclaimerModal').classList.add('disclaimer-hidden');
}

function declineDisclaimer() {
    document.getElementById('disclaimerModal').classList.add('disclaimer-hidden');
    document.getElementById('rejectionPage').classList.remove('disclaimer-hidden');
}

// ============== HEADER CLOCK ==============

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

function initTimeSettings() {
    const tzSelect = document.getElementById('globalTimezoneSelect');
    const fmtSelect = document.getElementById('globalTimeFormatSelect');
    if (typeof InterceptTime !== 'undefined') {
        if (tzSelect) tzSelect.value = InterceptTime.getTimezone();
        if (fmtSelect) fmtSelect.value = InterceptTime.getHour12() ? '12' : '24';
    }
}

// ============== MODE SWITCHING ==============

function switchMode(mode) {
    // Stop any running scans when switching modes
    if (isRunning && typeof stopDecoding === 'function') stopDecoding();
    if (isSensorRunning && typeof stopSensorDecoding === 'function') stopSensorDecoding();
    if (isWifiRunning && typeof stopWifiScan === 'function') stopWifiScan();
    if (isBtRunning && typeof stopBtScan === 'function') stopBtScan();
    if (isAdsbRunning && typeof stopAdsbScan === 'function') stopAdsbScan();

    currentMode = mode;

    // Remove active from all nav buttons, then add to the correct one
    document.querySelectorAll('.mode-nav-btn').forEach(btn => btn.classList.remove('active'));
    const modeMap = {
        'pager': 'pager', 'sensor': '433', 'aircraft': 'aircraft',
        'satellite': 'satellite', 'wifi': 'wifi', 'bluetooth': 'bluetooth',
        'meshtastic': 'meshtastic'
    };
    document.querySelectorAll('.mode-nav-btn').forEach(btn => {
        const label = btn.querySelector('.nav-label');
        if (label && label.textContent.toLowerCase().includes(modeMap[mode])) {
            btn.classList.add('active');
        }
    });

    // Toggle mode content visibility
    document.getElementById('pagerMode').classList.toggle('active', mode === 'pager');
    document.getElementById('sensorMode').classList.toggle('active', mode === 'sensor');
    document.getElementById('aircraftMode')?.classList.toggle('active', mode === 'aircraft');
    document.getElementById('satelliteMode').classList.toggle('active', mode === 'satellite');
    document.getElementById('wifiMode').classList.toggle('active', mode === 'wifi');
    document.getElementById('bluetoothMode').classList.toggle('active', mode === 'bluetooth');
    document.getElementById('aprsMode')?.classList.toggle('active', mode === 'aprs');
    document.getElementById('tscmMode')?.classList.toggle('active', mode === 'tscm');
    document.getElementById('rtlamrMode')?.classList.toggle('active', mode === 'rtlamr');
    document.getElementById('spystationsMode')?.classList.toggle('active', mode === 'spystations');
    document.getElementById('meshtasticMode')?.classList.toggle('active', mode === 'meshtastic');

    // Toggle stats visibility via class
    document.getElementById('pagerStats')?.classList.toggle('active', mode === 'pager');
    document.getElementById('sensorStats')?.classList.toggle('active', mode === 'sensor');
    document.getElementById('aircraftStats')?.classList.toggle('active', mode === 'aircraft');
    document.getElementById('satelliteStats')?.classList.toggle('active', mode === 'satellite');
    document.getElementById('wifiStats')?.classList.toggle('active', mode === 'wifi');

    // Hide signal meter
    document.getElementById('signalMeter')?.classList.remove('active');

    // Show/hide dashboard buttons in nav bar
    document.getElementById('adsbDashboardBtn')?.classList.toggle('active', mode === 'aircraft');
    document.getElementById('satelliteDashboardBtn')?.classList.toggle('active', mode === 'satellite');

    // Update active mode indicator
    const modeNames = {
        'pager': 'PAGER',
        'sensor': '433MHZ',
        'aircraft': 'AIRCRAFT',
        'satellite': 'SATELLITE',
        'wifi': 'WIFI',
        'bluetooth': 'BLUETOOTH',
        'tscm': 'TSCM',
        'aprs': 'APRS',
        'meshtastic': 'MESHTASTIC'
    };
    document.getElementById('activeModeIndicator').innerHTML = '<span class="pulse-dot"></span>' + modeNames[mode];

    // Update mobile nav buttons
    updateMobileNavButtons(mode);

    // Close mobile drawer when mode is switched (on mobile)
    if (window.innerWidth < 1024 && typeof window.closeMobileDrawer === 'function') {
        window.closeMobileDrawer();
    }

    // Toggle layout containers via class
    document.getElementById('wifiLayoutContainer')?.classList.toggle('active', mode === 'wifi');
    document.getElementById('btLayoutContainer')?.classList.toggle('active', mode === 'bluetooth');

    // Respect the "Show Radar Display" checkbox for aircraft mode
    const showRadar = document.getElementById('adsbEnableMap')?.checked;
    document.getElementById('aircraftVisuals')?.classList.toggle('active', mode === 'aircraft' && showRadar);
    document.getElementById('satelliteVisuals')?.classList.toggle('active', mode === 'satellite');

    // Update output panel title based on mode
    const titles = {
        'pager': 'Pager Decoder',
        'sensor': '433MHz Sensor Monitor',
        'aircraft': 'ADS-B Aircraft Tracker',
        'satellite': 'Satellite Monitor',
        'wifi': 'WiFi Scanner',
        'bluetooth': 'Bluetooth Scanner',
        'meshtastic': 'Meshtastic Mesh Monitor'
    };
    document.getElementById('outputTitle').textContent = titles[mode] || 'Signal Monitor';

    // Show/hide Device Intelligence for modes that use it
    const hideRecon = (mode === 'satellite' || mode === 'aircraft');
    const reconBtn = document.getElementById('reconBtn');
    const intelBtn = document.querySelector('[onclick="exportDeviceDB()"]');
    document.getElementById('reconPanel')?.classList.toggle('active', !hideRecon && typeof reconEnabled !== 'undefined' && reconEnabled);
    if (reconBtn) reconBtn.classList.toggle('hidden', hideRecon);
    if (intelBtn) intelBtn.classList.toggle('hidden', hideRecon);

    // Show RTL-SDR device section for modes that use it
    const showRtl = (mode === 'pager' || mode === 'sensor' || mode === 'aircraft');
    document.getElementById('rtlDeviceSection')?.classList.toggle('active', showRtl);

    // Toggle mode-specific tool status displays
    document.getElementById('toolStatusPager')?.classList.toggle('active', mode === 'pager');
    document.getElementById('toolStatusSensor')?.classList.toggle('active', mode === 'sensor');
    document.getElementById('toolStatusAircraft')?.classList.toggle('active', mode === 'aircraft');

    // Hide waterfall and output console for modes with their own visualizations
    const fullVisualModes = ['satellite', 'aircraft', 'wifi', 'bluetooth', 'meshtastic', 'aprs', 'tscm', 'spystations'];
    const hideConsole = fullVisualModes.includes(mode);
    document.querySelector('.waterfall-container')?.classList.toggle('active', !hideConsole);
    document.getElementById('output')?.classList.toggle('active', !hideConsole);

    const hideStatusBar = ['satellite', 'tscm', 'meshtastic', 'aprs', 'spystations'].includes(mode);
    document.querySelector('.status-bar')?.classList.toggle('active', !hideStatusBar);

    // Load interfaces and initialize visualizations when switching modes
    if (mode === 'wifi') {
        if (typeof refreshWifiInterfaces === 'function') refreshWifiInterfaces();
        if (typeof initRadar === 'function') initRadar();
        if (typeof initWatchList === 'function') initWatchList();
    } else if (mode === 'bluetooth') {
        if (typeof refreshBtInterfaces === 'function') refreshBtInterfaces();
        if (typeof initBtRadar === 'function') initBtRadar();
    } else if (mode === 'aircraft') {
        if (typeof checkAdsbTools === 'function') checkAdsbTools();
        if (typeof initAircraftRadar === 'function') initAircraftRadar();
    } else if (mode === 'satellite') {
        if (typeof initPolarPlot === 'function') initPolarPlot();
        if (typeof initSatelliteList === 'function') initSatelliteList();
    } else if (mode === 'meshtastic') {
        if (typeof Meshtastic !== 'undefined' && Meshtastic.init) Meshtastic.init();
    }
}

// ============== SECTION COLLAPSE ==============

function toggleSection(el) {
    el.closest('.section').classList.toggle('collapsed');
}

// ============== THEME MANAGEMENT ==============

function toggleTheme() {
    const html = document.documentElement;
    const currentTheme = html.getAttribute('data-theme') || 'dark';
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';
    html.setAttribute('data-theme', newTheme);
    localStorage.setItem('intercept-theme', newTheme);

    // Update button text
    const btn = document.getElementById('themeToggle');
    if (btn) {
        btn.textContent = newTheme === 'light' ? '🌙' : '☀️';
    }
}

function loadTheme() {
    const savedTheme = localStorage.getItem('intercept-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);
    const btn = document.getElementById('themeToggle');
    if (btn) {
        btn.textContent = savedTheme === 'light' ? '🌙' : '☀️';
    }
}

// ============== AUTO-SCROLL ==============

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

// ============== SDR DEVICE MANAGEMENT ==============

function getSelectedDevice() {
    return document.getElementById('deviceSelect').value;
}

function getSelectedSDRType() {
    return document.getElementById('sdrTypeSelect').value;
}

function reserveDevice(deviceIndex, modeId) {
    sdrDeviceUsage[modeId] = deviceIndex;
}

function releaseDevice(modeId) {
    delete sdrDeviceUsage[modeId];
}

function checkDeviceAvailability(requestingMode) {
    const selectedDevice = parseInt(getSelectedDevice());
    for (const [mode, device] of Object.entries(sdrDeviceUsage)) {
        if (mode !== requestingMode && device === selectedDevice) {
            alert(`Device ${selectedDevice} is currently in use by ${mode} mode. Please select a different device or stop the other scan first.`);
            return false;
        }
    }
    return true;
}

// ============== BIAS-T SETTINGS ==============

function saveBiasTSetting() {
    const enabled = document.getElementById('biasT')?.checked || false;
    localStorage.setItem('biasTEnabled', enabled);
}

function getBiasTEnabled() {
    return document.getElementById('biasT')?.checked || false;
}

function loadBiasTSetting() {
    const saved = localStorage.getItem('biasTEnabled');
    if (saved === 'true') {
        const checkbox = document.getElementById('biasT');
        if (checkbox) checkbox.checked = true;
    }
}

// ============== REMOTE SDR ==============

function toggleRemoteSDR() {
    const useRemote = document.getElementById('useRemoteSDR').checked;
    const configDiv = document.getElementById('remoteSDRConfig');
    const localControls = document.querySelectorAll('#sdrTypeSelect, #deviceSelect');

    if (useRemote) {
        configDiv.style.display = 'block';
        localControls.forEach(el => el.disabled = true);
    } else {
        configDiv.style.display = 'none';
        localControls.forEach(el => el.disabled = false);
    }
}

function getRemoteSDRConfig() {
    const useRemote = document.getElementById('useRemoteSDR')?.checked;
    if (!useRemote) return null;

    const host = document.getElementById('rtlTcpHost')?.value || 'localhost';
    const port = parseInt(document.getElementById('rtlTcpPort')?.value || '1234');

    if (!host || isNaN(port)) {
        alert('Please enter valid rtl_tcp host and port');
        return false;
    }

    return { host, port };
}

// ============== OUTPUT DISPLAY ==============

function showInfo(text) {
    const output = document.getElementById('output');
    if (!output) return;

    const placeholder = output.querySelector('.placeholder');
    if (placeholder) placeholder.remove();

    const infoEl = document.createElement('div');
    infoEl.className = 'info-msg';
    infoEl.style.cssText = 'padding: 12px 15px; margin-bottom: 8px; background: #0a0a0a; border: 1px solid #1a1a1a; border-left: 2px solid #00d4ff; font-family: "Roboto Condensed", "Arial Narrow", sans-serif; font-size: 11px; color: #888; word-break: break-all;';
    infoEl.textContent = text;
    output.insertBefore(infoEl, output.firstChild);
}

function showError(text) {
    const output = document.getElementById('output');
    if (!output) return;

    const placeholder = output.querySelector('.placeholder');
    if (placeholder) placeholder.remove();

    const errorEl = document.createElement('div');
    errorEl.className = 'error-msg';
    errorEl.style.cssText = 'padding: 12px 15px; margin-bottom: 8px; background: #1a0a0a; border: 1px solid #2a1a1a; border-left: 2px solid #ff3366; font-family: "Roboto Condensed", "Arial Narrow", sans-serif; font-size: 11px; color: #ff6688; word-break: break-all;';
    errorEl.textContent = '⚠ ' + text;
    output.insertBefore(errorEl, output.firstChild);
}

// ============== INITIALIZATION ==============

// ============== MOBILE NAVIGATION ==============

function initMobileNav() {
    const hamburgerBtn = document.getElementById('hamburgerBtn');
    const sidebar = document.getElementById('mainSidebar');
    const overlay = document.getElementById('drawerOverlay');

    if (!hamburgerBtn || !sidebar || !overlay) return;

    function openDrawer() {
        sidebar.classList.add('open');
        overlay.classList.add('visible');
        hamburgerBtn.classList.add('active');
        document.body.style.overflow = 'hidden';
    }

    function closeDrawer() {
        sidebar.classList.remove('open');
        overlay.classList.remove('visible');
        hamburgerBtn.classList.remove('active');
        document.body.style.overflow = '';
    }

    function toggleDrawer() {
        if (sidebar.classList.contains('open')) {
            closeDrawer();
        } else {
            openDrawer();
        }
    }

    hamburgerBtn.addEventListener('click', toggleDrawer);
    overlay.addEventListener('click', closeDrawer);

    // Close drawer when resizing to desktop
    window.addEventListener('resize', () => {
        if (window.innerWidth >= 1024) {
            closeDrawer();
        }
    });

    // Expose for external use
    window.toggleMobileDrawer = toggleDrawer;
    window.closeMobileDrawer = closeDrawer;
}

function setViewportHeight() {
    // Fix for iOS Safari address bar height
    const vh = window.innerHeight * 0.01;
    document.documentElement.style.setProperty('--vh', `${vh}px`);
}

function updateMobileNavButtons(mode) {
    // Update mobile nav bar buttons
    document.querySelectorAll('.mobile-nav-btn').forEach(btn => {
        const btnMode = btn.getAttribute('data-mode');
        btn.classList.toggle('active', btnMode === mode);
    });
}

function initApp() {
    // Check disclaimer
    checkDisclaimer();

    // Load theme
    loadTheme();

    // Start clock and init time settings
    initTimeSettings();
    updateHeaderClock();
    window._navClockStarted = true;  // Prevent nav.html from starting a duplicate interval
    setInterval(updateHeaderClock, 1000);
    if (typeof InterceptTime !== 'undefined' && InterceptTime.onChange) {
        InterceptTime.onChange(updateHeaderClock);
    }

    // Load bias-T setting
    loadBiasTSetting();

    // Initialize observer location inputs
    const adsbLatInput = document.getElementById('adsbObsLat');
    const adsbLonInput = document.getElementById('adsbObsLon');
    const obsLatInput = document.getElementById('obsLat');
    const obsLonInput = document.getElementById('obsLon');
    if (adsbLatInput) adsbLatInput.value = observerLocation.lat.toFixed(4);
    if (adsbLonInput) adsbLonInput.value = observerLocation.lon.toFixed(4);
    if (obsLatInput) obsLatInput.value = observerLocation.lat.toFixed(4);
    if (obsLonInput) obsLonInput.value = observerLocation.lon.toFixed(4);

    // Update UI state
    updateAutoScrollButton();

    // Make sections collapsible
    document.querySelectorAll('.section h3').forEach(h3 => {
        h3.addEventListener('click', function() {
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

    // Initialize mobile navigation
    initMobileNav();

    // Set viewport height for mobile browsers
    setViewportHeight();
    window.addEventListener('resize', setViewportHeight);
}

// Run initialization when DOM is ready
document.addEventListener('DOMContentLoaded', initApp);
