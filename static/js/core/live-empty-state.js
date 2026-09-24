/**
 * LiveEmptyState: say why a live list is empty.
 *
 * An empty list is ambiguous: broken, idle, or not started? Attached to a
 * list, this shows, while the list has no items, one of:
 *
 *     rtl_433 running · 0 messages in 32 s
 *     433 MHz is stopped. Start it to see messages here.
 *     Failed to start: rtl_433 not found. Install with: …
 *
 * from the server's own record in /health: which decoders are running, when
 * each last started, and why a start last failed (start errors name the
 * missing tool or the busy device).
 *
 *     LiveEmptyState.attach('wifiNetworkList', {
 *         mode: 'wifi', decoder: 'Wi-Fi scan', things: 'networks', label: 'Wi-Fi',
 *     });
 *
 * Any option may be a function, evaluated at each render; `mode` returning
 * null hides the state (a shared feed whose current mode is not tracked).
 * `items` is a selector for the list's items; by default any child that is
 * not a placeholder counts. `running` overrides the server's process flag,
 * for a view fed by something other than a local decoder.
 */
const LiveEmptyState = (function () {
    'use strict';

    const HEALTH_POLL_MS = 5000;
    const targets = [];
    let health = null;
    let healthAt = 0;
    let started = false;

    function value(option) {
        return typeof option === 'function' ? option() : option;
    }

    function duration(seconds) {
        const s = Math.max(0, Math.floor(seconds));
        if (s < 60) return s + ' s';
        const m = Math.floor(s / 60);
        if (m < 60) return m + ' min ' + (s % 60) + ' s';
        return Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
    }

    function isPlaceholder(el) {
        return /placeholder|empty/i.test(el.className || '') || el.dataset.placeholder !== undefined;
    }

    function itemCount(target) {
        const selector = value(target.options.items);
        if (selector) return target.container.querySelectorAll(selector).length;
        return Array.from(target.container.children).filter(
            (el) => el !== target.slot && !isPlaceholder(el) && el.offsetParent !== null
        ).length;
    }

    function describe(target) {
        const mode = value(target.options.mode);
        if (!mode) return null;
        const decoder = value(target.options.decoder) || mode;
        const things = value(target.options.things) || 'messages';
        const label = value(target.options.label) || decoder;
        if (!health) return { kind: 'unknown', text: 'Checking ' + label + ' status…' };

        const override = value(target.options.running);
        const running = override === undefined || override === null ? !!(health.processes || {})[mode] : !!override;
        const record = (health.lifecycle || {})[mode] || {};
        if (running) {
            const since = record.started_at;
            const elapsed = since ? ' in ' + duration(Date.now() / 1000 - since) : '';
            return { kind: 'running', text: decoder + ' running · 0 ' + things + elapsed };
        }
        const error = record.error;
        if (error && (!record.started_at || error.at >= record.started_at)) {
            return { kind: 'failed', text: 'Failed to start: ' + error.message };
        }
        return { kind: 'stopped', text: label + ' is stopped. Start it to see ' + things + ' here.' };
    }

    function render() {
        targets.forEach((target) => {
            if (!document.body.contains(target.container)) {
                const again = document.getElementById(target.id);
                if (!again) return;
                target.container = again;  // the list was replaced; follow it
            }
            const state = itemCount(target) === 0 ? describe(target) : null;
            if (!state) {
                if (target.slot) target.slot.hidden = true;
                return;
            }
            if (!target.slot || target.slot.parentNode !== target.container) {
                target.slot = document.createElement('div');
                target.slot.className = 'live-empty-state';
                target.slot.setAttribute('role', 'status');
                target.container.prepend(target.slot);
            }
            // Our state supersedes the list's own static placeholder. With an
            // explicit item selector, anything that is not an item is one.
            const itemSelector = value(target.options.items);
            Array.from(target.container.children).forEach((el) => {
                if (el === target.slot) return;
                if (isPlaceholder(el) || (itemSelector && !el.matches(itemSelector))) el.style.display = 'none';
            });
            target.slot.hidden = false;
            paint(target.slot, state);
        });
    }

    const HEADLINES = { running: 'Listening', failed: "Couldn't start", stopped: 'Stopped', unknown: 'Checking' };

    /**
     * An icon and two lines: what is happening, then the detail. Rebuilt only
     * when the state changes, so the pulse animation is not restarted every
     * second. Text (it can carry a server's error message) only ever goes in
     * through textContent.
     */
    function paint(slot, state) {
        if (slot.dataset.state !== state.kind || !slot.querySelector('.les-detail')) {
            slot.dataset.state = state.kind;
            const icon = document.createElement('span');
            icon.className = 'les-icon';
            icon.setAttribute('aria-hidden', 'true');
            const headline = document.createElement('strong');
            headline.className = 'les-headline';
            headline.textContent = HEADLINES[state.kind] || '';
            const detail = document.createElement('span');
            detail.className = 'les-detail';
            const text = document.createElement('span');
            text.className = 'les-text';
            text.append(headline, detail);
            slot.replaceChildren(icon, text);
        }
        const detail = slot.querySelector('.les-detail');
        if (detail.textContent !== state.text) detail.textContent = state.text;
    }

    async function poll() {
        // run-state.js already polls /health on the main page; do not double it.
        if (Date.now() - healthAt < HEALTH_POLL_MS * 1.5) return;
        try {
            const response = await fetch('/health');
            health = await response.json();
            healthAt = Date.now();
            render();
        } catch (err) {
            // keep the last known state; the next poll will retry
        }
    }

    const STYLE = '.live-empty-state{display:flex;align-items:center;justify-content:center;gap:14px;padding:16px 18px;' +
        'margin:8px;border:1px dashed var(--border-color,#263246);border-radius:8px;color:var(--text-secondary,#9fb0c7);' +
        'font-size:12px;text-align:left;grid-column:1/-1}' +
        '.live-empty-state .les-text{display:flex;flex-direction:column;gap:2px;min-width:0}' +
        '.live-empty-state .les-headline{font-size:11px;letter-spacing:.12em;text-transform:uppercase}' +
        '.live-empty-state .les-detail{overflow-wrap:anywhere}' +
        '.live-empty-state .les-icon{position:relative;flex:0 0 auto;width:26px;height:26px}' +
        '.live-empty-state .les-icon::before,.live-empty-state .les-icon::after{content:"";position:absolute;inset:0;' +
        'margin:auto;border-radius:50%}' +
        // running: a dot with a ripple, like the radar's centre
        '.live-empty-state[data-state=running]{color:var(--accent-cyan,#4aa3ff);border-color:rgba(74,163,255,.35)}' +
        '.live-empty-state[data-state=running] .les-icon::before{width:8px;height:8px;background:currentColor;' +
        'box-shadow:0 0 8px currentColor}' +
        '.live-empty-state[data-state=running] .les-icon::after{width:8px;height:8px;border:1.5px solid currentColor;' +
        'animation:les-ripple 2s ease-out infinite}' +
        '@keyframes les-ripple{from{width:8px;height:8px;opacity:.8}to{width:26px;height:26px;opacity:0}}' +
        // stopped: a hollow ring; failed: a solid warning mark
        '.live-empty-state[data-state=stopped] .les-icon::before,.live-empty-state[data-state=unknown] .les-icon::before{' +
        'width:14px;height:14px;border:1.5px solid currentColor;opacity:.6}' +
        '.live-empty-state[data-state=failed]{color:var(--accent-red,#e25d5d);border-style:solid}' +
        '.live-empty-state[data-state=failed] .les-icon::before{width:18px;height:18px;background:currentColor;opacity:.18}' +
        '.live-empty-state[data-state=failed] .les-icon::after{content:"!";width:18px;height:18px;line-height:18px;' +
        'text-align:center;font-weight:700;font-size:12px}' +
        '@media (prefers-reduced-motion:reduce){.live-empty-state .les-icon::after{animation:none}}';

    function start() {
        if (started) return;
        started = true;
        const style = document.createElement('style');
        style.textContent = STYLE;
        document.head.appendChild(style);
        window.addEventListener('intercept:health', (event) => {
            health = event.detail;
            healthAt = Date.now();
            render();
        });
        const every = window.VisibleInterval ? VisibleInterval.set : setInterval;
        every(render, 1000);
        every(poll, HEALTH_POLL_MS);
        poll();
    }

    function attach(container, options) {
        const el = typeof container === 'string' ? document.getElementById(container) : container;
        if (!el) return;
        targets.push({ id: el.id, container: el, options: options || {}, slot: null });
        start();
        render();
    }

    return { attach, _describe: describe };
})();

window.LiveEmptyState = LiveEmptyState;

// Every live list, by element id. A list is attached only on the page that has it.
(function () {
    'use strict';
    // index.html keeps these as top-level `let`s: visible by name, not on window.
    const current = () => {
        try { return currentMode; } catch (err) { return null; }  // eslint-disable-line no-undef
    };
    const local = () => {
        try { return typeof currentAgent === 'undefined' || currentAgent === 'local'; } catch (err) { return true; }  // eslint-disable-line no-undef
    };
    const onlyLocal = (mode) => () => (local() ? mode : null);  // an agent's decoder is not in /health
    const tscm = () => {
        try {
            if (Object.values(tscmFilters).some((v) => v !== 'all')) return null;  // eslint-disable-line no-undef
        } catch (err) { /* not the main page */ }
        return local() ? 'tscm' : null;
    };

    const FEED = {
        pager: { decoder: 'rtl_fm → multimon-ng', things: 'messages', label: 'Pager' },
        sensor: { decoder: 'rtl_433', things: 'readings', label: '433 MHz' },
        rtlamr: { decoder: 'rtlamr', things: 'meter readings', label: 'Meters' },
    };

    const LISTS = {
        output: {
            mode: () => (local() && FEED[current()] ? current() : null),
            decoder: () => (FEED[current()] || {}).decoder,
            things: () => (FEED[current()] || {}).things,
            label: () => (FEED[current()] || {}).label,
        },
        // The 433 MHz view opens on its dashboard grid, not the shared feed.
        sensorDashboardGrid: { mode: onlyLocal('sensor'), decoder: 'rtl_433', things: 'readings', label: '433 MHz', items: '.sdb-card' },
        aprsStationList: { mode: onlyLocal('aprs'), decoder: 'direwolf', things: 'stations', label: 'APRS' },
        wifiNetworkList: { mode: onlyLocal('wifi'), decoder: 'Wi-Fi scan', things: 'networks', label: 'Wi-Fi' },
        btDeviceListContent: { mode: onlyLocal('bluetooth'), decoder: 'Bluetooth scan', things: 'devices', label: 'Bluetooth' },
        meshMessagesGrid: { mode: 'meshtastic', decoder: 'Meshtastic', things: 'messages', label: 'Meshtastic' },
        ookOutput: { mode: 'ook', decoder: 'rtl_433', things: 'frames', label: 'OOK' },
        droneContactList: { mode: 'drone', decoder: 'Drone detection', things: 'contacts', label: 'Drone detection' },
        radiosondeCardContainer: { mode: 'radiosonde', decoder: 'radiosonde_auto_rx', things: 'sondes', label: 'Radiosonde' },
        sstvGallery: { mode: 'sstv', decoder: 'SSTV decoder', things: 'images', label: 'ISS SSTV' },
        sstvGeneralGallery: { mode: 'sstv_general', decoder: 'SSTV decoder', things: 'images', label: 'HF SSTV' },
        wefaxGallery: { mode: 'wefax', decoder: 'WeFax decoder', things: 'images', label: 'WeFax' },
        wxsatGallery: { mode: 'weathersat', decoder: 'SatDump', things: 'images', label: 'Weather satellite' },
        // ADS-B dashboard: tracking may come from a remote SBS feed with no local dump1090.
        aircraftList: {
            mode: 'adsb', decoder: 'dump1090', things: 'aircraft', label: 'ADS-B', items: '.aircraft-item',
            running: () => {
                try { return isTracking; } catch (err) { return undefined; }  // eslint-disable-line no-undef
            },
        },
        acarsMessages: { mode: 'acars', decoder: 'acarsdec', things: 'messages', label: 'ACARS' },
        vesselList: { mode: 'ais', decoder: 'AIS-catcher', things: 'vessels', label: 'AIS', items: '.vessel-item' },
        dscMessageList: { mode: 'dsc', decoder: 'DSC decoder', things: 'messages', label: 'DSC' },
        // TSCM sweep panels; with a filter set, the panel's own "no match" message stands.
        tscmWifiList: { mode: tscm, decoder: 'TSCM sweep', things: 'networks', label: 'TSCM sweep' },
        tscmWifiClientList: { mode: tscm, decoder: 'TSCM sweep', things: 'clients', label: 'TSCM sweep' },
        tscmBtList: { mode: tscm, decoder: 'TSCM sweep', things: 'devices', label: 'TSCM sweep' },
        tscmRfList: { mode: tscm, decoder: 'TSCM sweep', things: 'signals', label: 'TSCM sweep' },
    };

    function attachAll() {
        Object.entries(LISTS).forEach(([id, options]) => LiveEmptyState.attach(id, options));
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attachAll);
    } else {
        attachAll();
    }
})();
