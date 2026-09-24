/**
 * Activity feed: every sighting from every mode that reports them, newest
 * first, in one stream.
 *
 * The history comes from GET /observations, a timeline of the whole window
 * from /observations/histogram, and new sightings from the
 * /observations/stream SSE. Pausing freezes the list, not the stream: new
 * sightings are held and shown on resume, so nothing is lost while reading.
 *
 * Wording is deliberately modest. Two sightings close in time were seen in
 * the same window, and that is all the feed says. A Bluetooth identity is
 * the TSCM engine's judgement about MAC randomisation, and is labelled so.
 */
const Activity = (function () {
    'use strict';

    const MAX_ROWS = 500;
    const MAX_HELD = 1000;
    const BUCKETS = 60;
    const MAX_MAP_POINTS = 300;

    // Icons: 24x24 stroke paths, in the nav's style
    const ICONS = {
        plane: '<path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/>',
        ship: '<path d="M3 18l2 2h14l2-2"/><path d="M5 18v-4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v4"/><path d="M12 12V6"/>',
        wifi: '<path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1"/>',
        bluetooth: '<path d="M7 7l10 10-5 5V2l5 5L7 17"/>',
        sensor: '<path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z"/>',
        pin: '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
        mesh: '<circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M7 6h10M6 8l5 8M18 8l-5 8"/>',
        pager: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h6M7 14h10"/>',
        meter: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
        radio: '<circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49"/>',
        dot: '<circle cx="12" cy="12" r="4"/>',
    };

    // A label, icon and note protocol per source; any other source is shown by name.
    const SOURCES = {
        adsb: { label: 'Aircraft', icon: 'plane', protocol: 'adsb' },
        ais: { label: 'Vessels', icon: 'ship', protocol: 'ais' },
        wifi: { label: 'Wi-Fi', icon: 'wifi', protocol: 'wifi' },
        bluetooth: { label: 'Bluetooth', icon: 'bluetooth', protocol: 'bluetooth' },
        sensor: { label: '433 MHz', icon: 'sensor', protocol: 'rf' },
        dsc: { label: 'DSC', icon: 'ship', protocol: 'dsc' },
        aprs: { label: 'APRS', icon: 'pin', protocol: 'aprs' },
        meshtastic: { label: 'Meshtastic', icon: 'mesh', protocol: 'meshtastic' },
        rtlamr: { label: 'Meters', icon: 'meter', protocol: 'rf' },
        pager: { label: 'Pager', icon: 'pager', protocol: 'rf' },
        acars: { label: 'ACARS', icon: 'plane', protocol: 'other' },
        vdl2: { label: 'VDL2', icon: 'plane', protocol: 'other' },
        subghz: { label: 'SubGHz', icon: 'radio', protocol: 'rf' },
        ook: { label: 'OOK', icon: 'radio', protocol: 'rf' },
    };

    let root = null;
    let list = null;
    let source = null;
    let paused = false;
    let held = [];
    let windowMinutes = 60;
    let identifier = null;
    let range = null;           // {since, until} chosen on the timeline, or null for the window
    let arriving = null;        // live sightings received while the history loads
    let loaded = new Set();     // the history's sightings, whose live copy may still arrive
    let hist = null;            // the last /observations/histogram
    let histTimer = null;
    let map = null;
    let mapLayer = null;
    let mapOpen = false;
    const positioned = [];      // recent sightings with a position, for the map
    const hidden = new Set();   // sources the operator has filtered out
    const seen = new Set();     // sources with at least one sighting

    // ---------------------------------------------------------------- helpers

    /** Build an element. Children are nodes or text; text is never parsed as HTML. */
    function el(tag, attrs, ...children) {
        const node = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([key, value]) => {
            if (value === undefined || value === null || value === false) return;
            if (key === 'class') node.className = value;
            else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
            else node.setAttribute(key, value === true ? '' : value);
        });
        children.flat().forEach((child) => {
            if (child === null || child === undefined || child === false) return;
            node.append(child instanceof Node ? child : String(child));
        });
        return node;
    }

    function icon(src) {
        const name = (SOURCES[src] || {}).icon || 'dot';
        const span = el('span', { class: 'activity-icon', 'aria-hidden': 'true' });
        // Fixed markup from ICONS above; no data reaches it
        span.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            'stroke-linecap="round" stroke-linejoin="round">' + ICONS[name] + '</svg>';
        return span;
    }

    function label(src) {
        const known = SOURCES[src] || window.INTERCEPT_MODES?.[src];
        return known ? known.label : src;
    }

    /** A source's colour, as the stylesheet sets it ([data-source] --source-color). */
    function colourOf(src) {
        const probe = el('span', { 'data-source': src, style: 'display:none' });
        document.body.appendChild(probe);
        const colour = getComputedStyle(probe).getPropertyValue('--source-color').trim() || '#8b95a5';
        probe.remove();
        return colour;
    }

    function clock(ts) {
        return InterceptTime.shortTime(new Date(ts * 1000));
    }

    function matches(obs) {
        if (hidden.has(obs.source)) return false;
        if (identifier && obs.identifier !== identifier && obs.entity !== identifier) return false;
        if (range && (obs.ts < range.since || obs.ts >= range.until)) return false;
        return true;
    }

    // ---------------------------------------------------------------- rows

    function signal(rssi) {
        // -100 dBm empty, -30 dBm full; banded as on the radars
        const f = Math.max(0, Math.min(1, (rssi + 100) / 70));
        const band = rssi > -55 ? 'strong' : rssi > -70 ? 'medium' : 'weak';
        return el('span', { class: 'activity-signal', title: rssi + ' dBm' },
            el('span', { class: 'activity-signal-track' },
                el('span', { class: 'activity-signal-fill ' + band, style: 'width:' + (f * 100).toFixed(0) + '%' })),
            el('span', { class: 'activity-signal-value' }, Math.round(rssi) + ' dBm'));
    }

    function row(obs) {
        const id = el('span', { class: 'activity-id' });
        // CopyId and DeviceNotes escape what they are given.
        id.innerHTML = CopyId.html(obs.identifier) +
            DeviceNotes.html(obs.identifier, (SOURCES[obs.source] || {}).protocol || 'other');

        const details = [];
        if (obs.entity && obs.entity !== obs.identifier) {
            details.push(el('span', {
                class: 'activity-entity',
                title: 'The TSCM identity engine groups addresses it judges to be one device changing its ' +
                    'MAC address. A judgement from advertising patterns, not a certainty.',
            }, 'identity ' + obs.entity));
        }
        if (obs.lat !== null && obs.lat !== undefined && obs.lon !== null && obs.lon !== undefined) {
            details.push(el('span', { class: 'activity-pos' }, obs.lat.toFixed(4) + ', ' + obs.lon.toFixed(4)));
        }
        if (obs.rssi !== null && obs.rssi !== undefined) details.push(signal(obs.rssi));

        const time = el('span', { class: 'activity-time' });
        time.innerHTML = InterceptTime.relTimeHtml(new Date(obs.ts * 1000));

        const focus = obs.entity || obs.identifier;
        return el('li', { class: 'activity-row', 'data-source': obs.source },
            time,
            el('span', { class: 'activity-source' }, icon(obs.source), label(obs.source)),
            id,
            el('span', { class: 'activity-summary' }, obs.summary || ''),
            el('span', { class: 'activity-details' }, details),
            el('button', {
                type: 'button', class: 'activity-focus', title: 'Show only sightings of ' + focus,
                onclick: () => focusOn(focus),
            }, 'Only this'),
        );
    }

    function describeScope() {
        const parts = [];
        if (identifier) parts.push('of ' + identifier);
        parts.push(range ? 'between ' + clock(range.since) + ' and ' + clock(range.until) : 'in the last ' + windowText());
        return parts.join(' ');
    }

    function renderEmpty() {
        if (list.children.length) return;
        list.append(el('li', { class: 'activity-empty' },
            el('strong', null, 'No sightings ' + describeScope() + '.'),
            el('span', null, ' Sightings appear here while modes that report them are running, ' +
                'whether or not their own view is open.'),
        ));
    }

    function windowText() {
        return windowMinutes >= 60 ? (windowMinutes / 60) + ' h' : windowMinutes + ' min';
    }

    /** Put sightings, given oldest first, at the top: the newest ends up first. */
    function prepend(observations) {
        list.querySelector('.activity-empty')?.remove();
        const fragment = document.createDocumentFragment();
        observations.slice().reverse().forEach((obs) => fragment.append(row(obs)));
        list.prepend(fragment);
        while (list.children.length > MAX_ROWS) list.lastElementChild.remove();
        observations.forEach(remember);
    }

    // ---------------------------------------------------------------- tiles

    function totals() {
        const counts = {};
        if (hist) Object.entries(hist.sources).forEach(([src, buckets]) => {
            counts[src] = buckets.reduce((a, b) => a + b, 0);
        });
        return counts;
    }

    function renderTiles() {
        const box = root && root.querySelector('.activity-tiles');
        if (!box) return;
        const counts = totals();
        const sources = [...new Set([...seen, ...Object.keys(counts)])]
            .sort((a, b) => (counts[b] || 0) - (counts[a] || 0) || label(a).localeCompare(label(b)));
        if (!sources.length) {
            box.replaceChildren(el('span', { class: 'activity-muted' }, 'No source has reported yet.'));
            return;
        }
        box.replaceChildren(...sources.map((src) => el('button', {
            type: 'button',
            class: 'activity-tile' + (hidden.has(src) ? ' off' : ''),
            'data-source': src,
            'aria-pressed': String(!hidden.has(src)),
            title: (hidden.has(src) ? 'Show ' : 'Hide ') + label(src),
            onclick: () => {
                if (hidden.has(src)) hidden.delete(src); else hidden.add(src);
                renderTiles();
                renderTimeline();
                load();
            },
        }, icon(src),
        el('span', { class: 'activity-tile-label' }, label(src)),
        el('span', { class: 'activity-tile-count' }, String(counts[src] || 0)))));
    }

    // ---------------------------------------------------------------- timeline

    function renderTimeline() {
        const box = root && root.querySelector('.activity-timeline');
        if (!box || !hist) return;
        const width = 600, height = 64, gap = 1;
        const barWidth = width / hist.buckets;
        const visible = Object.keys(hist.sources).filter((src) => !hidden.has(src));
        const stacks = Array.from({ length: hist.buckets }, (_, i) =>
            visible.map((src) => [src, hist.sources[src][i]]).filter(([, n]) => n > 0));
        const peak = Math.max(1, ...stacks.map((stack) => stack.reduce((sum, [, n]) => sum + n, 0)));

        const ns = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
        svg.setAttribute('preserveAspectRatio', 'none');
        svg.setAttribute('class', 'activity-timeline-svg');
        stacks.forEach((stack, i) => {
            const start = hist.since + i * hist.bucket_seconds;
            const end = start + hist.bucket_seconds;
            const g = document.createElementNS(ns, 'g');
            const chosen = range && start >= range.since - 1e-6 && end <= range.until + 1e-6;
            g.setAttribute('class', 'activity-bucket' + (chosen ? ' chosen' : ''));
            const hit = document.createElementNS(ns, 'rect');
            hit.setAttribute('x', (i * barWidth).toFixed(2));
            hit.setAttribute('y', 0);
            hit.setAttribute('width', barWidth.toFixed(2));
            hit.setAttribute('height', height);
            hit.setAttribute('class', 'activity-bucket-hit');
            g.appendChild(hit);
            let y = height;
            stack.forEach(([src, n]) => {
                const h = (n / peak) * (height - 4);
                y -= h;
                const bar = document.createElementNS(ns, 'rect');
                bar.setAttribute('x', (i * barWidth + gap / 2).toFixed(2));
                bar.setAttribute('y', y.toFixed(2));
                bar.setAttribute('width', Math.max(0.5, barWidth - gap).toFixed(2));
                bar.setAttribute('height', h.toFixed(2));
                bar.setAttribute('data-source', src);
                bar.setAttribute('class', 'activity-bar');
                g.appendChild(bar);
            });
            const title = document.createElementNS(ns, 'title');
            const total = stack.reduce((sum, [, n]) => sum + n, 0);
            title.textContent = `${clock(start)}–${clock(end)}: ${total} sighting${total === 1 ? '' : 's'}` +
                stack.map(([src, n]) => `\n${label(src)}: ${n}`).join('');
            g.appendChild(title);
            g.addEventListener('click', () => chooseRange(start, end));
            svg.appendChild(g);
        });
        box.replaceChildren(svg,
            el('div', { class: 'activity-timeline-axis' },
                el('span', null, clock(hist.since)),
                el('span', null, 'Click a bar to show that period'),
                el('span', null, 'now')));
    }

    async function loadTimeline() {
        if (!root) return;
        const params = new URLSearchParams({ window_minutes: windowMinutes, buckets: BUCKETS });
        if (identifier) params.set('identifier', identifier);
        try {
            const data = await (await fetch('/observations/histogram?' + params)).json();
            if (data.status !== 'success') return;
            hist = data;
            Object.keys(hist.sources).forEach((src) => seen.add(src));
            renderTiles();
            renderTimeline();
        } catch (err) {
            // the list still works; the timeline returns with the next refresh
        }
    }

    /** A live sighting counts in the timeline's newest bucket, and its tile. */
    function countLive(obs) {
        if (!hist) return;
        const i = Math.floor((obs.ts - hist.since) / hist.bucket_seconds);
        if (i < 0 || i >= hist.buckets) return;
        if (!hist.sources[obs.source]) hist.sources[obs.source] = new Array(hist.buckets).fill(0);
        hist.sources[obs.source][i] += 1;
        renderTiles();
        renderTimeline();
    }

    function chooseRange(since, until) {
        range = { since, until };
        renderTimeline();
        load();
    }

    // ---------------------------------------------------------------- map

    function remember(obs) {
        if (obs.lat === null || obs.lat === undefined || obs.lon === null || obs.lon === undefined) return;
        positioned.push(obs);
        if (positioned.length > MAX_MAP_POINTS) positioned.shift();
        if (mapOpen) drawMap(false);
    }

    function drawMap(fit) {
        if (!map || !mapLayer) return;
        mapLayer.clearLayers();
        const colours = {};
        const points = positioned.filter(matches);
        points.forEach((obs) => {
            colours[obs.source] = colours[obs.source] || colourOf(obs.source);
            L.circleMarker([obs.lat, obs.lon], {
                radius: 5, weight: 1.5, color: colours[obs.source], fillColor: colours[obs.source], fillOpacity: 0.55,
            }).bindTooltip(`${label(obs.source)} ${obs.identifier}${obs.summary ? ' · ' + obs.summary : ''}`)
                .addTo(mapLayer);
        });
        const note = root.querySelector('.activity-map-note');
        if (note) {
            note.textContent = points.length
                ? `${points.length} sighting${points.length === 1 ? '' : 's'} with a position`
                : 'No sightings with a position yet: aircraft, vessels and APRS stations report one.';
        }
        if (fit && points.length) {
            map.fitBounds(L.latLngBounds(points.map((obs) => [obs.lat, obs.lon])).pad(0.2), { maxZoom: 11 });
        }
    }

    function toggleMap() {
        mapOpen = !mapOpen;
        const panel = root.querySelector('.activity-map');
        const button = root.querySelector('.activity-map-toggle');
        panel.hidden = !mapOpen;
        button.classList.toggle('active', mapOpen);
        button.setAttribute('aria-pressed', String(mapOpen));
        if (!mapOpen) return;
        if (!map && typeof L !== 'undefined') {
            const holder = panel.querySelector('.activity-map-canvas');
            holder.id = holder.id || 'activityMapCanvas';
            map = typeof MapUtils !== 'undefined'
                ? MapUtils.init(holder.id, { center: [30, 0], zoom: 2, maxZoom: 18 })
                : L.map(holder, { center: [30, 0], zoom: 2 });
            mapLayer = L.layerGroup().addTo(map);
        }
        setTimeout(() => {
            if (map) map.invalidateSize();
            drawMap(true);
        }, 60);
    }

    // ---------------------------------------------------------------- status

    function renderStatus() {
        const status = root.querySelector('.activity-status');
        const button = root.querySelector('.activity-pause');
        status.textContent = paused
            ? 'Paused' + (held.length ? ' · ' + held.length + ' new' : '')
            : 'Live';
        status.classList.toggle('paused', paused);
        button.textContent = paused ? 'Resume' : 'Pause';

        const banner = root.querySelector('.activity-focus-banner');
        banner.replaceChildren();
        banner.hidden = !identifier && !range;
        const parts = [];
        if (identifier) parts.push('sightings of ', el('strong', null, identifier));
        if (range) parts.push(identifier ? ' ' : 'sightings ', 'between ',
            el('strong', null, clock(range.since) + '–' + clock(range.until)));
        if (parts.length) {
            banner.append('Showing ', ...parts, ' ',
                el('button', { type: 'button', class: 'preset-btn', onclick: showAll }, 'Show all'));
        }
    }

    // ---------------------------------------------------------------- data

    function key(obs) {
        return obs.ts + '|' + obs.source + '|' + obs.identifier;
    }

    function notice(obs) {
        if (seen.has(obs.source)) return;
        seen.add(obs.source);
        renderTiles();
    }

    async function load() {
        if (!root) return;
        const params = new URLSearchParams({ limit: MAX_ROWS });
        if (range) {
            params.set('since', range.since);
            params.set('until', range.until);
        } else {
            params.set('window_minutes', windowMinutes);
        }
        if (identifier) params.set('identifier', identifier);
        const shown = [...seen].filter((src) => !hidden.has(src));
        if (hidden.size) params.set('source', shown.join(',') || '-');
        arriving = [];
        try {
            const resp = await fetch('/observations?' + params);
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.message || 'Request failed (' + resp.status + ')');
            data.observations.forEach(notice);
            held = [];
            list.replaceChildren();
            positioned.length = 0;
            const history = data.observations.slice().reverse();  // the server sends newest first
            loaded = new Set(history.map(key));
            prepend(history.concat(arriving.filter((obs) => !loaded.has(key(obs)))));
            renderEmpty();
            if (mapOpen) drawMap(true);
        } catch (err) {
            list.replaceChildren(el('li', { class: 'activity-empty' },
                el('strong', null, 'Could not load the activity feed: '), err.message));
        }
        arriving = null;
        renderStatus();
    }

    /** Every source that has reported since the server started, even outside the time window. */
    async function loadSources() {
        try {
            const data = await (await fetch('/observations/stats')).json();
            Object.keys(data.sources || {}).forEach((src) => seen.add(src));
        } catch (err) {
            // the tiles fill in as sightings arrive
        }
        renderTiles();
    }

    function onMessage(event) {
        let obs;
        try { obs = JSON.parse(event.data); } catch (err) { return; }
        if (!obs || obs.type !== 'observation') return;
        notice(obs);
        if (identifier && obs.identifier !== identifier && obs.entity !== identifier) return;
        countLive(obs);
        if (!matches(obs) || loaded.has(key(obs))) return;
        if (arriving) {
            arriving.push(obs);
            return;
        }
        if (paused) {
            held.push(obs);
            if (held.length > MAX_HELD) held.shift();
            renderStatus();
            return;
        }
        prepend([obs]);
    }

    function connect() {
        disconnect();
        source = new EventSource('/observations/stream');
        source.onmessage = onMessage;
    }

    function disconnect() {
        if (source) source.close();
        source = null;
    }

    // ---------------------------------------------------------------- actions

    function togglePause() {
        paused = !paused;
        if (!paused && held.length) {
            prepend(held);
            held = [];
        }
        renderStatus();
    }

    function focusOn(id) {
        identifier = id;
        load();
        loadTimeline();
    }

    function showAll() {
        identifier = null;
        range = null;
        load();
        loadTimeline();
    }

    function setWindow(minutes) {
        windowMinutes = Number(minutes) || 60;
        range = null;
        load();
        loadTimeline();
    }

    function skeleton() {
        list = el('ol', { class: 'activity-list', 'aria-live': 'polite' });
        root.replaceChildren(
            el('div', { class: 'activity-header' },
                el('div', null,
                    el('h3', null, 'Activity'),
                    el('p', { class: 'activity-muted' },
                        'Sightings from every mode that reports them, newest first. Sightings close together ' +
                        'were seen in the same window; that alone does not mean they are related.'),
                ),
                el('div', { class: 'activity-controls' },
                    el('span', { class: 'activity-status' }),
                    el('button', {
                        type: 'button', class: 'preset-btn activity-map-toggle', 'aria-pressed': 'false', onclick: toggleMap,
                    }, 'Map'),
                    el('button', { type: 'button', class: 'preset-btn activity-pause', onclick: togglePause }),
                ),
            ),
            el('div', { class: 'activity-tiles', role: 'group', 'aria-label': 'Sources: click to show or hide' }),
            el('div', { class: 'activity-timeline', 'aria-label': 'Sightings over time' }),
            el('div', { class: 'activity-map', hidden: true },
                el('div', { class: 'activity-map-canvas' }),
                el('div', { class: 'activity-map-note activity-muted' })),
            el('div', { class: 'activity-focus-banner', hidden: true }),
            list,
        );
    }

    function init() {
        root = document.getElementById('activityVisuals');
        if (!root) return;
        root.style.display = 'flex';
        if (map) { map.remove(); }
        map = null;
        mapLayer = null;
        mapOpen = false;
        skeleton();
        renderStatus();
        loadSources().then(() => Promise.all([load(), loadTimeline()]));
        connect();
        // Roll the timeline forward; live sightings fill it in between
        const every = window.VisibleInterval || { set: setInterval, clear: clearInterval };
        if (histTimer) every.clear(histTimer);
        histTimer = every.set(() => { if (!range) loadTimeline(); }, 60000);
    }

    function destroy() {
        disconnect();
        paused = false;
        held = [];
        const every = window.VisibleInterval || { clear: clearInterval };
        if (histTimer) every.clear(histTimer);
        histTimer = null;
        if (map) { map.remove(); map = null; mapLayer = null; }
        if (root) root.style.display = 'none';
    }

    return { init, destroy, togglePause, setWindow, focusOn, showAll };
})();

window.Activity = Activity;
