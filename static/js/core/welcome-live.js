/**
 * Welcome page, live: what is running, the SDRs, and the last 24 hours.
 *
 * The welcome page was a static menu. A card at the top of its left column
 * now says what is running (each a link back to it), how many SDRs are
 * attached and claimed, and shows the last day's observations as a stacked
 * strip by source (click it for the Activity feed). Mode tiles whose decoder
 * is running get a light.
 *
 * Reads the health record RunState shares as 'intercept:health', and
 * /observations/histogram while the welcome page is showing.
 */
(function () {
    'use strict';

    const HOURS = 24;
    const BUCKETS = 48;
    const REFRESH_MS = 60000;
    const PROCESS_ALIASES = { bluetooth: ['bluetooth', 'bt_scan'], wifi: ['wifi', 'wifi_scan'] };
    // Decoders that live on a dashboard rather than a mode
    const DASHBOARDS = {
        adsb: { label: 'Aircraft', href: '/adsb/dashboard' },
        acars: { label: 'ACARS', href: '/adsb/dashboard' },
        vdl2: { label: 'VDL2', href: '/adsb/dashboard' },
        ais: { label: 'Vessels', href: '/ais/dashboard' },
        dsc: { label: 'DSC', href: '/ais/dashboard' },
    };
    const SOURCE_LABELS = {
        adsb: 'Aircraft', ais: 'Vessels', wifi: 'Wi-Fi', bluetooth: 'Bluetooth', sensor: '433 MHz',
        dsc: 'DSC', aprs: 'APRS', meshtastic: 'Meshtastic', rtlamr: 'Meters', pager: 'Pager',
    };

    let health = null;
    let histogram = null;

    function welcome() {
        return document.getElementById('welcomePage');
    }

    function showing() {
        const el = welcome();
        return !!el && getComputedStyle(el).display !== 'none';
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function elapsed(seconds) {
        const m = Math.max(0, Math.floor(seconds / 60));
        if (m < 60) return m + ' min';
        const h = Math.floor(m / 60);
        return h < 48 ? h + ' h ' + (m % 60) + ' min' : Math.floor(h / 24) + ' d';
    }

    function compact(n) {
        return n >= 10000 ? Math.round(n / 1000) + 'k' : n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
    }

    // ------------------------------------------------------------ running

    function running() {
        if (!health || !health.processes) return [];
        const modes = window.INTERCEPT_MODES || {};
        const seen = new Set();
        const out = [];
        Object.entries(health.processes).forEach(([key, on]) => {
            if (!on) return;
            const mode = Object.keys(PROCESS_ALIASES).find((m) => PROCESS_ALIASES[m].includes(key)) || key;
            if (seen.has(mode)) return;
            seen.add(mode);
            const record = (health.lifecycle && health.lifecycle[mode]) || {};
            if (DASHBOARDS[mode]) out.push({ mode, label: DASHBOARDS[mode].label, href: DASHBOARDS[mode].href, since: record.started_at });
            else if (modes[mode]) out.push({ mode, label: modes[mode].label, since: record.started_at });
        });
        return out;
    }

    function runningChip(item) {
        const chip = el(item.href ? 'a' : 'button', 'wl-chip');
        if (item.href) chip.href = item.href;
        else {
            chip.type = 'button';
            chip.addEventListener('click', () => window.selectMode && window.selectMode(item.mode));
        }
        chip.append(el('span', 'wl-light'), el('span', 'wl-chip-name', item.label));
        if (item.since) chip.append(el('span', 'wl-chip-time', elapsed(Date.now() / 1000 - item.since)));
        return chip;
    }

    // ------------------------------------------------------------ last 24 h

    function strip() {
        const sources = (histogram && histogram.sources) || {};
        const keys = Object.keys(sources);
        const totals = keys.map((k) => [k, sources[k].reduce((a, b) => a + b, 0)]).sort((a, b) => b[1] - a[1]);
        const total = totals.reduce((a, [, n]) => a + n, 0);

        const wrap = el('button', 'wl-activity');
        wrap.type = 'button';
        wrap.title = 'Open the Activity feed';
        wrap.addEventListener('click', () => window.selectMode && window.selectMode('activity'));

        const head = el('div', 'wl-row');
        head.append(el('span', 'wl-label', 'Last ' + HOURS + ' h'), el('span', 'wl-value', total ? compact(total) + ' observations' : 'Nothing heard yet'));
        wrap.append(head);

        const ns = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('viewBox', `0 0 ${BUCKETS} 20`);
        svg.setAttribute('preserveAspectRatio', 'none');
        svg.setAttribute('class', 'wl-bars');
        svg.setAttribute('aria-hidden', 'true');
        const perBucket = Array.from({ length: BUCKETS }, (_, i) => keys.reduce((a, k) => a + (sources[k][i] || 0), 0));
        const peak = Math.max(1, ...perBucket);
        for (let i = 0; i < BUCKETS; i++) {
            const base = document.createElementNS(ns, 'rect');
            base.setAttribute('x', i + 0.12);
            base.setAttribute('y', 19);
            base.setAttribute('width', 0.76);
            base.setAttribute('height', 1);
            base.setAttribute('class', 'wl-bar-base');
            svg.append(base);
            let y = 20;
            totals.forEach(([k]) => {
                const h = (sources[k][i] || 0) / peak * 19;
                if (!h) return;
                y -= h;
                const rect = document.createElementNS(ns, 'rect');
                rect.setAttribute('x', i + 0.12);
                rect.setAttribute('y', y.toFixed(2));
                rect.setAttribute('width', 0.76);
                rect.setAttribute('height', h.toFixed(2));
                rect.dataset.source = k;
                svg.append(rect);
            });
        }
        wrap.append(svg);

        const axis = el('div', 'wl-axis');
        axis.append(el('span', '', '-' + HOURS + ' h'), el('span', '', '-' + HOURS / 2 + ' h'), el('span', '', 'now'));
        wrap.append(axis);

        if (totals.length) {
            const legend = el('div', 'wl-legend');
            totals.filter(([, n]) => n).slice(0, 5).forEach(([k, n]) => {
                const item = el('span', 'wl-legend-item');
                item.dataset.source = k;
                item.append(el('i'), document.createTextNode((SOURCE_LABELS[k] || k) + ' ' + compact(n)));
                legend.append(item);
            });
            wrap.append(legend);
        }
        return wrap;
    }

    // ------------------------------------------------------------ render

    function card() {
        let node = document.getElementById('welcomeLive');
        if (node) return node;
        const column = welcome() && welcome().querySelector('.welcome-changelog');
        if (!column) return null;
        node = el('section', 'wl-card');
        node.id = 'welcomeLive';
        node.setAttribute('aria-label', 'Live status');
        column.prepend(node);
        return node;
    }

    function render() {
        const node = card();
        if (!node) return;
        const items = running();

        const top = el('div', 'wl-top');
        const light = el('span', 'wl-light' + (items.length ? '' : ' idle'));
        top.append(light, el('h2', 'wl-title', 'Live'));
        if (health && typeof health.uptime_seconds === 'number') {
            top.append(el('span', 'wl-uptime', 'up ' + elapsed(health.uptime_seconds)));
        }

        const runRow = el('div', 'wl-running');
        if (items.length) items.forEach((item) => runRow.append(runningChip(item)));
        else runRow.append(el('span', 'wl-none', health ? 'Nothing running. Pick a mode to start.' : 'Checking…'));

        const facts = el('div', 'wl-row');
        if (health) {
            const sdrs = health.sdr_devices || 0;
            const claimed = Object.keys(health.sdr_claims || {}).length;
            facts.append(
                el('span', 'wl-label', 'SDR'),
                el('span', 'wl-value', sdrs ? sdrs + ' attached' + (claimed ? ' · ' + claimed + ' in use' : ' · free') : 'none detected'),
            );
        }

        node.replaceChildren(top, runRow, ...(health ? [facts] : []), strip());
        markTiles();
    }

    function markTiles() {
        const processes = (health && health.processes) || {};
        const on = (key) => (PROCESS_ALIASES[key] || [key]).some((k) => processes[k]);
        document.querySelectorAll('#welcomePage .mode-card').forEach((tile) => {
            const call = (tile.getAttribute('onclick') || '').match(/selectMode\('(\w+)'\)/);
            const href = tile.getAttribute('href') || '';
            const key = call ? call[1] : href.includes('/adsb/') ? 'adsb' : href.includes('/ais/') ? 'ais' : null;
            tile.classList.toggle('is-running', !!key && on(key));

            // A tool the mode cannot run without (ToolReadiness)
            const needs = key && window.ToolReadiness ? ToolReadiness.forMode(key) : null;
            let badge = tile.querySelector('.wl-needs');
            if (needs) {
                if (!badge) {
                    badge = el('span', 'wl-needs');
                    tile.append(badge);
                }
                badge.textContent = 'needs ' + needs.missing[0] + (needs.missing.length > 1 ? ' +' + (needs.missing.length - 1) : '');
                tile.title = 'Needs ' + needs.missing.join(', ') + (needs.hint ? '. Install: ' + needs.hint : '');
            } else if (badge) {
                badge.remove();
            }
            tile.classList.toggle('needs-tool', !!needs);
        });
    }

    async function refreshHistogram() {
        if (!showing()) return;
        try {
            const response = await fetch(`/observations/histogram?window_minutes=${HOURS * 60}&buckets=${BUCKETS}`);
            if (!response.ok) return;
            histogram = await response.json();
            render();
        } catch (err) {
            // Keep the last strip
        }
    }

    function start() {
        if (!welcome()) return;
        render();
        refreshHistogram();
        if (window.ToolReadiness) ToolReadiness.load().then(markTiles);
        window.addEventListener('intercept:health', (event) => {
            health = event.detail;
            if (showing()) render();
        });
        (window.VisibleInterval ? VisibleInterval.set : setInterval)(refreshHistogram, REFRESH_MS);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
