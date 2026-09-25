/**
 * Sidebar polish: a status card, section icons, and sections that open
 * where you left them.
 *
 * - A status card at the top of the sidebar says, for the mode in view,
 *   whether it is running and for how long, and the SDR, frequency and gain
 *   it will use: what used to take opening three sections to find out.
 * - Each section header gets an icon chosen from its title.
 * - Sections were all collapsed on every visit. Now the sections you open in
 *   a mode are remembered for that mode, and a mode you have not arranged
 *   opens its first section.
 * - The card carries the mode's Start / Stop: it presses the mode's own
 *   button, so long sidebars need no scrolling to start or stop.
 * - A mode's opening section that only describes it (a heading and a
 *   paragraph, no controls) is folded behind an (i) on the status card, so
 *   the sidebar starts with something you can set.
 *
 * Reads the page as it is (the mode panels, #deviceSelect, the health
 * record RunState shares as 'intercept:health'); nothing here drives a mode.
 */
(function () {
    'use strict';

    const OPEN_KEY = 'intercept.sidebar.open.';
    const PROCESS_ALIASES = { bluetooth: ['bluetooth', 'bt', 'bt_scan'], wifi: ['wifi', 'wifi_scan', 'wlan'] };

    // Section icons, by the first pattern the title matches
    const ICON_PATHS = {
        antenna: '<path d="M12 12v10"/><path d="M5 3l7 9 7-9"/><path d="M8.5 7.5h7"/>',
        frequency: '<path d="M2 12h3l3-8 4 16 4-12 2 4h4"/>',
        device: '<rect x="4" y="7" width="16" height="10" rx="2"/><path d="M8 7V4M16 7V4M8 20v-3M16 20v-3"/>',
        settings: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
        source: '<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4"/>',
        export: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5M12 15V3"/>',
        info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
        status: '<path d="M3 12h4l3 7 4-14 3 7h4"/>',
        filter: '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>',
        target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
        schedule: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        list: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
    };
    const ICON_RULES = [
        [/antenna/i, 'antenna'],
        [/freq|tuning|tune|bookmark|band/i, 'frequency'],
        [/sdr|device|hackrf|adapter|interface|hardware|receiver/i, 'device'],
        [/source|agent|connection/i, 'source'],
        [/export|output|saved|capture|logging|record/i, 'export'],
        [/resource|help|about|guide|started|reference|playbook/i, 'info'],
        [/filter|protocol|ignore/i, 'filter'],
        [/target|locate|environment|proximity/i, 'target'],
        [/schedule|auto|station|pass|satellite/i, 'schedule'],
        [/status|stats|health|position|fix|time|dilution|error|activity/i, 'status'],
        [/setting|option|config|gain|threshold|timing|modulation|detect|mode|advanced|tools|scan/i, 'settings'],
    ];

    let health = null;
    let introShown = false;
    const HELP_TITLE = /getting started|about|guide|help|resources|reference|how to/i;

    // Modes whose start/stop buttons are not .run-btn / .stop-btn in their panel
    const ACTION_IDS = {
        aprs: ['aprsStripStartBtn', 'aprsStripStopBtn'],
        sstv: ['sstvStartBtn', 'sstvStopBtn'],
        sstv_general: ['sstvGeneralStartBtn', 'sstvGeneralStopBtn'],
        wefax: ['wefaxStartBtn', 'wefaxStopBtn'],
        weathersat: ['wxsatStartBtn', 'wxsatStopBtn'],
        subghz: ['subghzRxStartBtn', 'subghzRxStopBtn'],
    };

    function currentModeName() {
        try { return currentMode; } catch (err) { return null; }  // eslint-disable-line no-undef
    }

    function modePanel(mode) {
        const def = window.INTERCEPT_MODES && window.INTERCEPT_MODES[mode];
        return def ? document.getElementById(def.elementId) : null;
    }

    function svg(name) {
        return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
            'stroke-linejoin="round" aria-hidden="true">' + ICON_PATHS[name] + '</svg>';
    }

    // ------------------------------------------------------------ icons

    function decorateHeaders(root) {
        (root || document).querySelectorAll('.sidebar .section > h3').forEach((h3) => {
            if (h3.querySelector('.sb-icon')) return;
            const title = h3.textContent.trim();
            const rule = ICON_RULES.find(([pattern]) => pattern.test(title));
            const icon = document.createElement('span');
            icon.className = 'sb-icon';
            icon.innerHTML = svg(rule ? rule[1] : 'list');  // fixed markup from ICON_PATHS
            h3.prepend(icon);
        });
    }

    // ------------------------------------------------------------ open sections

    function ownSections(mode) {
        const panel = modePanel(mode);
        return panel ? Array.from(panel.querySelectorAll(':scope > .section'))
            .filter((s) => s.querySelector(':scope > h3') && !s.classList.contains('sb-intro')) : [];
    }

    // ------------------------------------------------------------ intro

    // The first section, if it has a heading, some text and nothing to operate
    function markIntro(mode) {
        const panel = modePanel(mode);
        if (!panel) return null;
        let intro = panel.querySelector(':scope > .section.sb-intro');
        if (intro) return intro;
        const first = panel.querySelector(':scope > .section');
        if (!first || !first.querySelector(':scope > h3')) return null;
        if (first.querySelector('input, select, button, textarea, a, canvas, [id]')) return null;
        first.classList.add('sb-intro');
        return first;
    }

    function syncIntro(mode) {
        const intro = markIntro(mode);
        if (intro) {
            intro.classList.toggle('sb-intro-open', introShown);
            intro.classList.remove('collapsed');
        }
        return intro;
    }

    function titleOf(section) {
        return section.querySelector(':scope > h3').textContent.trim();
    }

    function readOpen(mode) {
        try {
            const stored = JSON.parse(localStorage.getItem(OPEN_KEY + mode) || 'null');
            return Array.isArray(stored) ? stored : null;
        } catch (err) {
            return null;
        }
    }

    function applyOpenSections(mode) {
        const sections = ownSections(mode);
        if (!sections.length) return;
        const open = readOpen(mode);
        // By default, the first section you can operate: not a guide or an explainer
        const firstUseful = sections.find((section) => !HELP_TITLE.test(titleOf(section))) || sections[0];
        sections.forEach((section) => {
            const shouldOpen = open ? open.includes(titleOf(section)) : section === firstUseful;
            section.classList.toggle('collapsed', !shouldOpen);
        });
    }

    function rememberOpenSections(mode) {
        const sections = ownSections(mode);
        if (!sections.length) return;
        const open = sections.filter((s) => !s.classList.contains('collapsed')).map(titleOf);
        try { localStorage.setItem(OPEN_KEY + mode, JSON.stringify(open)); } catch (err) { /* this visit only */ }
    }

    // ------------------------------------------------------------ start / stop

    function shown(btn) {
        if (!btn || btn.disabled) return false;
        if (btn.style.display === 'none') return false;
        // In a collapsed section it is only folded away, not unavailable
        return btn.offsetParent !== null || !!btn.closest('.section.collapsed');
    }

    /** The mode's own Start or Stop button that is showing now, if any. */
    function actionButton(mode) {
        const panel = modePanel(mode);
        const candidates = [];
        (ACTION_IDS[mode] || []).forEach((id) => candidates.push(document.getElementById(id)));
        if (panel) candidates.push(...panel.querySelectorAll('.run-btn, .stop-btn'));
        // Only a start or stop: not "Rescan SDR", "Open TSCM mode" or "Find Receivers"
        return candidates.find((b) => shown(b) && START_STOP.test(b.textContent)) || null;
    }

    const START_STOP = /\b(start|stop|connect|disconnect)\b|\b(quick|deep) scan\b/i;

    function actionControl(mode) {
        const target = actionButton(mode);
        if (!target) return null;
        const label = target.textContent.replace(/[^\w\s-]/g, ' ').replace(/\s+/g, ' ').trim();
        const stop = /\b(stop|disconnect)\b/i.test(label) || target.classList.contains('stop-btn');
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'sb-status-action ' + (stop ? 'stop' : 'start');
        btn.textContent = label.length > 18 ? (stop ? 'Stop' : 'Start') : label;
        btn.title = label;
        btn.addEventListener('click', () => {
            target.click();
            [400, 1500].forEach((ms) => setTimeout(renderCard, ms));
        });
        return btn;
    }

    // ------------------------------------------------------------ status card

    function card() {
        let el = document.getElementById('sidebarStatusCard');
        if (el) return el;
        const sidebar = document.getElementById('mainSidebar');
        if (!sidebar) return null;
        el = document.createElement('div');
        el.id = 'sidebarStatusCard';
        el.className = 'sb-status';
        el.setAttribute('aria-live', 'polite');
        const anchor = document.getElementById('sidebarCollapseBtn');
        if (anchor) anchor.after(el); else sidebar.prepend(el);
        return el;
    }

    function processRunning(mode) {
        if (!health || !health.processes) return null;
        const keys = PROCESS_ALIASES[mode] || [mode];
        if (!keys.some((k) => k in health.processes)) return null;  // not a process-backed mode
        return keys.some((k) => health.processes[k]);
    }

    function elapsed(seconds) {
        const s = Math.max(0, Math.floor(seconds));
        if (s < 60) return s + ' s';
        const m = Math.floor(s / 60);
        return m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
    }

    function fieldValue(panel, pattern) {
        if (!panel) return null;
        const input = Array.from(panel.querySelectorAll('input, select'))
            .find((i) => pattern.test(i.id || '') && (i.type === 'text' || i.type === 'number' || i.tagName === 'SELECT'));
        if (!input) return null;
        const label = input.closest('.form-group')?.querySelector('label')?.textContent || '';
        const unit = (label.match(/\(([kMG]?Hz)\)/i) || [])[1] || '';
        const value = String(input.value || '').trim();
        // An empty field means the device's own setting (see utils/sdr/device_config)
        return { value: value || 'device default', unit: value ? unit : '' };
    }

    function row(label, value) {
        const r = document.createElement('div');
        r.className = 'sb-status-row';
        const k = document.createElement('span');
        k.textContent = label;
        const v = document.createElement('span');
        v.textContent = value;
        r.append(k, v);
        return r;
    }

    function renderCard() {
        const el = card();
        const mode = currentModeName();
        const def = mode && window.INTERCEPT_MODES && window.INTERCEPT_MODES[mode];
        if (!el || !def) { if (el) el.hidden = true; return; }
        el.hidden = false;

        const running = processRunning(mode);
        const record = (health && health.lifecycle && health.lifecycle[mode]) || {};
        const state = running === null ? '' : running
            ? 'Running' + (record.started_at ? ' · ' + elapsed(Date.now() / 1000 - record.started_at) : '')
            : 'Stopped';

        const top = document.createElement('div');
        top.className = 'sb-status-top';
        const light = document.createElement('span');
        light.className = 'sb-status-light' + (running ? ' on' : running === false ? ' off' : '');
        const name = document.createElement('strong');
        name.textContent = def.label;
        const stateEl = document.createElement('span');
        stateEl.className = 'sb-status-state';
        stateEl.textContent = state;
        top.append(light, name, stateEl);
        if (syncIntro(mode)) {
            const info = document.createElement('button');
            info.type = 'button';
            info.className = 'sb-status-info' + (introShown ? ' on' : '');
            info.title = introShown ? 'Hide the description' : 'About this mode';
            info.setAttribute('aria-expanded', String(introShown));
            info.textContent = 'i';
            info.addEventListener('click', () => {
                introShown = !introShown;
                renderCard();
            });
            top.append(info);
        }

        const rows = [];
        const sdr = document.getElementById('rtlDeviceSection');
        const device = document.getElementById('deviceSelect');
        if (sdr && sdr.offsetParent !== null && device && device.selectedIndex >= 0) {
            rows.push(row('SDR', device.options[device.selectedIndex].textContent.trim()));
        }
        const panel = modePanel(mode);
        const freq = fieldValue(panel, /freq/i);
        if (freq) rows.push(row('Frequency', freq.value + (freq.unit ? ' ' + freq.unit : '')));
        const gain = fieldValue(panel, /gain/i);
        if (gain) rows.push(row('Gain', gain.value === '0' ? 'auto' : gain.value));

        const body = document.createElement('div');
        body.className = 'sb-status-rows';
        body.append(...rows);
        // A tool the mode cannot run without, and how to install it (ToolReadiness)
        const needs = window.ToolReadiness ? ToolReadiness.forMode(mode) : null;
        let missing = null;
        if (needs) {
            missing = document.createElement('div');
            missing.className = 'sb-status-missing';
            const what = document.createElement('div');
            what.textContent = 'Needs ' + needs.missing.join(', ');
            missing.append(what);
            if (needs.hint) {
                const how = document.createElement('code');
                how.textContent = needs.hint;
                missing.append(how);
            }
        }
        const action = actionControl(mode);
        el.replaceChildren(top, ...(rows.length ? [body] : []), ...(missing ? [missing] : []), ...(action ? [action] : []));
    }

    // ------------------------------------------------------------ wiring

    function onModeShown() {
        const mode = currentModeName();
        if (mode) applyOpenSections(mode);
        renderCard();
    }

    function wrapSwitchMode() {
        if (typeof window.switchMode !== 'function' || window.switchMode.__sidebarPolish) return;
        const original = window.switchMode;
        const wrapped = function () {
            const result = original.apply(this, arguments);
            Promise.resolve(result).then(onModeShown, onModeShown);
            return result;
        };
        Object.assign(wrapped, original);
        wrapped.__sidebarPolish = true;
        window.switchMode = wrapped;
    }

    function start() {
        const sidebar = document.getElementById('mainSidebar');
        if (!sidebar) return;
        decorateHeaders(sidebar);
        wrapSwitchMode();

        // Remember what the operator opens (the page's own handler toggles first)
        sidebar.addEventListener('click', (event) => {
            const h3 = event.target.closest('.section > h3');
            if (!h3) return;
            const mode = currentModeName();
            setTimeout(() => { if (mode) rememberOpenSections(mode); }, 0);
        });
        sidebar.addEventListener('input', renderCard);
        sidebar.addEventListener('change', renderCard);
        window.addEventListener('intercept:health', (event) => {
            health = event.detail;
            renderCard();
        });
        (window.VisibleInterval ? VisibleInterval.set : setInterval)(renderCard, 5000);
        if (window.ToolReadiness) ToolReadiness.load().then(renderCard);

        // After the page's own start-up has collapsed everything and applied ?mode=
        setTimeout(onModeShown, 0);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
