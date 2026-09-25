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

    // Idle modes are folded behind a "+N idle" chip unless expanded.
    const EXPANDED_KEY = 'intercept.runState.expanded';
    const DASHBOARDS = { adsb: '/adsb/dashboard', ais: '/ais/dashboard' };

    let refreshTimer = null;
    let activeMode = null;
    let expanded = readExpanded();
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
            // The mode now in view is always shown, so redraw rather than re-mark
            if (lastHealth) renderHealth(lastHealth);
            else markActiveChip();
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

        // Running modes and the mode in view; the rest behind "+N idle"
        const processes = normalizeProcesses(data.processes || {});
        const current = normalizeMode(activeMode || inferCurrentMode());
        const running = CHIP_MODES.filter((mode) => processes[mode]);
        const shown = running.concat(CHIP_MODES.includes(current) && !running.includes(current) ? [current] : []);
        const idle = CHIP_MODES.filter((mode) => !shown.includes(mode));

        if (!running.length) {
            const note = document.createElement('span');
            note.className = 'run-state-note';
            note.textContent = 'Nothing running';
            chipsContainer.appendChild(note);
        }
        shown.forEach((mode) => {
            chipsContainer.appendChild(buildChip(modeLabels[mode] || mode.toUpperCase(), Boolean(processes[mode]), mode));
        });
        if (expanded) {
            idle.forEach((mode) => {
                const chip = buildChip(modeLabels[mode] || mode.toUpperCase(), false, mode);
                chip.classList.add('idle');
                chipsContainer.appendChild(chip);
            });
        }
        if (idle.length) {
            const more = document.createElement('button');
            more.type = 'button';
            more.className = 'run-state-chip run-state-more';
            more.setAttribute('aria-expanded', String(expanded));
            more.textContent = expanded ? 'Show less' : `+${idle.length} idle`;
            more.addEventListener('click', () => {
                expanded = !expanded;
                try { localStorage.setItem(EXPANDED_KEY, expanded ? '1' : '0'); } catch (err) { /* this visit only */ }
                renderHealth(lastHealth);
            });
            chipsContainer.appendChild(more);
        }

        renderCounts(summaryEl, data.data || {});
        markActiveChip();
    }

    // What is in view right now, as chips that open their mode; zeros are left out.
    const COUNT_CHIPS = [
        { key: 'aircraft_count', mode: 'adsb', label: 'aircraft', icon: '<path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/>' },
        { key: 'vessel_count', mode: 'ais', label: 'vessels', icon: '<path d="M3 18l2 2h14l2-2"/><path d="M5 18v-4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v4"/><path d="M12 12V6"/>' },
        { key: 'wifi_networks_count', mode: 'wifi', label: 'Wi-Fi networks', icon: '<path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1"/>' },
        { key: 'bt_devices_count', mode: 'bluetooth', label: 'Bluetooth devices', icon: '<path d="M7 7l10 10-5 5V2l5 5L7 17"/>' },
    ];

    function renderCounts(el, counts) {
        const chips = COUNT_CHIPS.filter((c) => Number(counts[c.key]) > 0).map((c) => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'run-state-count';
            chip.title = `${counts[c.key]} ${c.label} in view. Open.`;
            chip.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
                'stroke-linejoin="round" aria-hidden="true">' + c.icon + '</svg>';  // fixed markup from COUNT_CHIPS
            chip.append(document.createTextNode(String(counts[c.key])));
            chip.addEventListener('click', () => openMode(c.mode));
            return chip;
        });
        el.replaceChildren(...chips);
        el.hidden = !chips.length;
    }

    function readExpanded() {
        try { return localStorage.getItem(EXPANDED_KEY) === '1'; } catch (err) { return false; }
    }

    /** Go to a mode from its chip: ADS-B and AIS have their own dashboards. */
    function openMode(mode) {
        if (DASHBOARDS[mode]) {
            window.location.href = DASHBOARDS[mode];
        } else if (window.INTERCEPT_MODES && window.INTERCEPT_MODES[mode] && typeof window.switchMode === 'function') {
            window.switchMode(mode);
        }
    }

    function buildChip(label, running, mode) {
        const chip = document.createElement('span');
        chip.className = `run-state-chip${running ? ' running' : ''}`;
        if (mode) {
            chip.dataset.mode = mode;
            if (DASHBOARDS[mode] || (window.INTERCEPT_MODES && window.INTERCEPT_MODES[mode])) {
                chip.classList.add('link');
                chip.setAttribute('role', 'button');
                chip.tabIndex = 0;
                chip.title = running ? `${label} is running. Open it.` : `Open ${label}`;
                chip.addEventListener('click', () => openMode(mode));
                chip.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        openMode(mode);
                    }
                });
            }
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
