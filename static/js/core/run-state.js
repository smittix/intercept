const RunState = (function() {
    'use strict';

    const REFRESH_MS = 5000;
    const CHIP_MODES = ['pager', 'sensor', 'wifi', 'bluetooth', 'adsb', 'ais', 'acars', 'vdl2', 'aprs', 'dsc', 'subghz', 'radiosonde', 'morse', 'rtlamr', 'meshtastic', 'sstv', 'weathersat', 'wefax', 'sstv_general', 'tscm', 'gps', 'bt_locate', 'meteor'];
    const MODE_ALIASES = {
        bt: 'bluetooth',
        btlocate: 'bluetooth',
        aircraft: 'adsb',
        sonde: 'radiosonde',
        weather_sat: 'weathersat',
    };

    const modeLabels = {
        pager: 'Pager',
        sensor: '433',
        wifi: 'WiFi',
        bluetooth: 'BT',
        adsb: 'ADS-B',
        ais: 'AIS',
        acars: 'ACARS',
        vdl2: 'VDL2',
        aprs: 'APRS',
        dsc: 'DSC',
        subghz: 'SubGHz',
        radiosonde: 'Sonde',
        morse: 'Morse',
        rtlamr: 'Meter',
        meshtastic: 'Mesh',
        sstv: 'SSTV',
        weathersat: 'WxSat',
        wefax: 'WeFax',
        sstv_general: 'HF SSTV',
        tscm: 'TSCM',
        gps: 'GPS',
        bt_locate: 'BT Loc',
        meteor: 'Meteor',
    };

    let refreshTimer = null;
    let activeMode = null;
    let lastHealth = null;
    let lastErrorToastAt = 0;

    function init() {
        const root = document.getElementById('runStateStrip');
        if (!root) return;

        wireActions();
        wrapModeSwitch();
        activeMode = inferCurrentMode();
        renderHealth(null);
        refresh();

        if (!refreshTimer) {
            refreshTimer = VisibleInterval.set(refresh, REFRESH_MS);
        }

        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) refresh();
        });
    }

    function wireActions() {
        const refreshBtn = document.getElementById('runStateRefreshBtn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => refresh());
        }

        const settingsBtn = document.getElementById('runStateSettingsBtn');
        if (settingsBtn) {
            settingsBtn.addEventListener('click', () => {
                if (typeof showSettings === 'function') {
                    showSettings();
                    if (typeof switchSettingsTab === 'function') {
                        switchSettingsTab('tools');
                    }
                }
            });
        }
    }

    function wrapModeSwitch() {
        if (typeof window.switchMode !== 'function') return;
        if (window.switchMode.__runStateWrapped) return;

        const original = window.switchMode;
        const wrapped = function(mode) {
            if (mode) {
                activeMode = normalizeMode(String(mode));
            }
            const result = original.apply(this, arguments);
            markActiveChip();
            return result;
        };
        wrapped.__runStateWrapped = true;
        window.switchMode = wrapped;
    }

    async function refresh() {
        try {
            const response = await fetch('/health');
            const data = await response.json();
            lastHealth = data;
            renderHealth(data);
            // Shared with LiveEmptyState, so the page polls /health once.
            window.dispatchEvent(new CustomEvent('intercept:health', { detail: data }));
        } catch (err) {
            renderHealth(null, err);
            const transient = isTransientFailure(err);
            const now = Date.now();
            if (!transient && typeof reportActionableError === 'function' && (now - lastErrorToastAt) > 30000) {
                lastErrorToastAt = now;
                reportActionableError('Run State', err, { persistent: false });
            }
        }
    }

    function renderHealth(data, err) {
        const chipsContainer = document.getElementById('runStateChips');
        const summaryEl = document.getElementById('runStateSummary');
        if (!chipsContainer || !summaryEl) return;

        chipsContainer.innerHTML = '';

        if (!data || data.status !== 'healthy') {
            const offline = buildChip('API', false);
            offline.classList.add('active');
            chipsContainer.appendChild(offline);
            summaryEl.textContent = err ? `Health unavailable: ${extractMessage(err)}` : 'Health unavailable';
            return;
        }

        const processes = normalizeProcesses(data.processes || {});
        for (const mode of CHIP_MODES) {
            const isRunning = Boolean(processes[mode]);
            chipsContainer.appendChild(buildChip(modeLabels[mode] || mode.toUpperCase(), isRunning, mode));
        }

        const counts = data.data || {};
        summaryEl.textContent = `Aircraft ${counts.aircraft_count || 0} | Vessels ${counts.vessel_count || 0} | WiFi ${counts.wifi_networks_count || 0} | BT ${counts.bt_devices_count || 0}`;
        markActiveChip();
    }

    function buildChip(label, running, mode) {
        const chip = document.createElement('span');
        chip.className = `run-state-chip${running ? ' running' : ''}`;
        if (mode) {
            chip.dataset.mode = mode;
        }

        const dot = document.createElement('span');
        dot.className = 'dot';
        chip.appendChild(dot);

        const text = document.createElement('span');
        text.textContent = label;
        chip.appendChild(text);

        return chip;
    }

    function markActiveChip() {
        if (!activeMode) {
            activeMode = inferCurrentMode();
        }

        document.querySelectorAll('#runStateChips .run-state-chip').forEach((chip) => {
            chip.classList.remove('active');
            if (chip.dataset.mode && chip.dataset.mode === normalizeMode(activeMode)) {
                chip.classList.add('active');
            }
        });
    }

    function inferCurrentMode() {
        const modeParam = new URLSearchParams(window.location.search).get('mode');
        if (modeParam) return normalizeMode(modeParam);

        if (typeof window.currentMode === 'string' && window.currentMode) {
            return normalizeMode(window.currentMode);
        }

        const indicator = document.getElementById('activeModeIndicator');
        if (!indicator) return 'pager';

        const text = indicator.textContent || '';
        const normalized = text.toLowerCase();
        if (normalized.includes('wifi')) return 'wifi';
        if (normalized.includes('bluetooth')) return 'bluetooth';
        if (normalized.includes('bt locate')) return 'bluetooth';
        if (normalized.includes('ads-b')) return 'adsb';
        if (normalized.includes('ais')) return 'ais';
        if (normalized.includes('acars')) return 'acars';
        if (normalized.includes('vdl2')) return 'vdl2';
        if (normalized.includes('aprs')) return 'aprs';
        if (normalized.includes('dsc')) return 'dsc';
        if (normalized.includes('subghz')) return 'subghz';
        if (normalized.includes('radiosonde') || normalized.includes('sonde')) return 'radiosonde';
        if (normalized.includes('morse')) return 'morse';
        if (normalized.includes('meter') || normalized.includes('rtlamr')) return 'rtlamr';
        if (normalized.includes('meshtastic') || normalized.includes('mesh')) return 'meshtastic';
        if (normalized.includes('hf sstv') || normalized.includes('sstv general')) return 'sstv_general';
        if (normalized.includes('sstv')) return 'sstv';
        if (normalized.includes('weather') && normalized.includes('sat')) return 'weathersat';
        if (normalized.includes('wefax') || normalized.includes('weather fax')) return 'wefax';
        if (normalized.includes('tscm')) return 'tscm';
        if (normalized.includes('gps')) return 'gps';
        if (normalized.includes('bt loc')) return 'bt_locate';
        if (normalized.includes('433')) return 'sensor';
        return 'pager';
    }

    function normalizeMode(mode) {
        const value = String(mode || '').trim().toLowerCase();
        if (!value) return 'pager';
        return MODE_ALIASES[value] || value;
    }

    function normalizeProcesses(raw) {
        const processes = Object.assign({}, raw || {});
        processes.bluetooth = Boolean(
            processes.bluetooth ||
            processes.bt ||
            processes.bt_scan
        );
        processes.wifi = Boolean(
            processes.wifi ||
            processes.wifi_scan ||
            processes.wlan
        );
        return processes;
    }

    function extractMessage(err) {
        if (!err) return 'Unknown error';
        if (typeof err === 'string') return err;
        if (err.message) return err.message;
        return String(err);
    }

    function isTransientFailure(err) {
        if (typeof window.isTransientOrOffline === 'function' && window.isTransientOrOffline(err)) {
            return true;
        }
        if (typeof navigator !== 'undefined' && navigator.onLine === false) {
            return true;
        }
        const text = extractMessage(err).toLowerCase();
        return text.includes('failed to fetch') || text.includes('network') || text.includes('timeout');
    }

    function getLastHealth() {
        return lastHealth;
    }

    function destroy() {
        if (refreshTimer) {
            VisibleInterval.clear(refreshTimer);
            refreshTimer = null;
        }
    }

    return {
        init,
        refresh,
        destroy,
        getLastHealth,
    };
})();

document.addEventListener('DOMContentLoaded', () => {
    RunState.init();
});
