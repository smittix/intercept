/**
 * Activity feed: every sighting from every mode that reports them, newest
 * first, in one stream.
 *
 * The history comes from GET /observations and new sightings from the
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

    // A label and a note protocol per source; any other source is shown by name.
    const SOURCES = {
        adsb: { label: 'Aircraft', protocol: 'adsb' },
        ais: { label: 'Vessels', protocol: 'ais' },
        wifi: { label: 'Wi-Fi', protocol: 'wifi' },
        bluetooth: { label: 'Bluetooth', protocol: 'bluetooth' },
        sensor: { label: '433 MHz', protocol: 'rf' },
        dsc: { label: 'DSC', protocol: 'dsc' },
        aprs: { label: 'APRS', protocol: 'aprs' },
        meshtastic: { label: 'Meshtastic', protocol: 'meshtastic' },
        rtlamr: { label: 'Meters', protocol: 'rf' },
        pager: { label: 'Pager', protocol: 'rf' },
        acars: { label: 'ACARS', protocol: 'other' },
        vdl2: { label: 'VDL2', protocol: 'other' },
        subghz: { label: 'SubGHz', protocol: 'rf' },
        ook: { label: 'OOK', protocol: 'rf' },
    };

    let root = null;
    let list = null;
    let source = null;
    let paused = false;
    let held = [];
    let windowMinutes = 60;
    let identifier = null;
    let arriving = null;        // live sightings received while the history loads
    let loaded = new Set();     // the history's sightings, whose live copy may still arrive
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

    function label(src) {
        const known = SOURCES[src] || window.INTERCEPT_MODES?.[src];
        return known ? known.label : src;
    }

    function matches(obs) {
        if (hidden.has(obs.source)) return false;
        if (identifier && obs.identifier !== identifier && obs.entity !== identifier) return false;
        return true;
    }

    // ---------------------------------------------------------------- rendering

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
        if (obs.rssi !== null && obs.rssi !== undefined) details.push(el('span', null, obs.rssi + ' dBm'));
        if (obs.lat !== null && obs.lat !== undefined && obs.lon !== null && obs.lon !== undefined) {
            details.push(el('span', null, obs.lat.toFixed(4) + ', ' + obs.lon.toFixed(4)));
        }

        const time = el('span', { class: 'activity-time' });
        time.innerHTML = InterceptTime.relTimeHtml(new Date(obs.ts * 1000));

        const focus = obs.entity || obs.identifier;
        return el('li', { class: 'activity-row', 'data-source': obs.source },
            time,
            el('span', { class: 'activity-source' }, label(obs.source)),
            id,
            el('span', { class: 'activity-summary' }, obs.summary || ''),
            el('span', { class: 'activity-details' }, details),
            el('button', {
                type: 'button', class: 'activity-focus', title: 'Show only sightings of ' + focus,
                onclick: () => focusOn(focus),
            }, 'Only this'),
        );
    }

    function renderEmpty() {
        if (list.children.length) return;
        const scope = identifier ? 'of ' + identifier + ' ' : '';
        list.append(el('li', { class: 'activity-empty' },
            el('strong', null, 'No sightings ' + scope + 'in the last ' + windowText() + '.'),
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
    }

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
        banner.hidden = !identifier;
        if (identifier) {
            banner.append('Showing sightings of ', el('strong', null, identifier), ' ',
                el('button', { type: 'button', class: 'preset-btn', onclick: () => focusOn(null) }, 'Show all'));
        }
    }

    function renderSourceFilter() {
        const box = document.getElementById('activitySources');
        if (!box) return;
        if (!seen.size) {
            box.replaceChildren(el('span', { class: 'activity-muted' }, 'None yet'));
            return;
        }
        box.replaceChildren(...[...seen].sort().map((src) => el('label', { class: 'activity-source-toggle', 'data-source': src },
            el('input', {
                type: 'checkbox', checked: !hidden.has(src),
                onchange: (event) => {
                    if (event.target.checked) hidden.delete(src); else hidden.add(src);
                    load();
                },
            }),
            el('span', { class: 'activity-swatch' }),
            label(src),
        )));
    }

    // ---------------------------------------------------------------- data

    function key(obs) {
        return obs.ts + '|' + obs.source + '|' + obs.identifier;
    }

    function notice(obs) {
        if (seen.has(obs.source)) return;
        seen.add(obs.source);
        renderSourceFilter();
    }

    async function load() {
        if (!root) return;
        const params = new URLSearchParams({ window_minutes: windowMinutes, limit: MAX_ROWS });
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
            const history = data.observations.slice().reverse();  // the server sends newest first
            loaded = new Set(history.map(key));
            prepend(history.concat(arriving.filter((obs) => !loaded.has(key(obs)))));
            renderEmpty();
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
            // the filter fills in as sightings arrive
        }
        renderSourceFilter();
    }

    function onMessage(event) {
        let obs;
        try { obs = JSON.parse(event.data); } catch (err) { return; }
        if (!obs || obs.type !== 'observation') return;
        notice(obs);
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
    }

    function setWindow(minutes) {
        windowMinutes = Number(minutes) || 60;
        load();
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
                    el('button', { type: 'button', class: 'preset-btn activity-pause', onclick: togglePause }),
                ),
            ),
            el('div', { class: 'activity-focus-banner', hidden: true }),
            list,
        );
    }

    function init() {
        root = document.getElementById('activityVisuals');
        if (!root) return;
        root.style.display = 'flex';
        skeleton();
        renderStatus();
        loadSources().then(load);
        connect();
    }

    function destroy() {
        disconnect();
        paused = false;
        held = [];
        if (root) root.style.display = 'none';
    }

    return { init, destroy, togglePause, setWindow, focusOn };
})();

window.Activity = Activity;
