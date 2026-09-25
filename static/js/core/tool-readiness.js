/**
 * ToolReadiness: which modes are missing a tool they need, from /dependencies.
 *
 *     ToolReadiness.load().then(() => ToolReadiness.forMode('sensor'));
 *     // -> { missing: ['rtl_433'], hint: <this system's install command> } or null
 *
 * Fetched once per page. Only modes that cannot run at all without the
 * tool are reported: Wi-Fi and TSCM list tools that only their deeper
 * scans need, and their quick scans work without them, so they are left out.
 */
const ToolReadiness = (function () {
    'use strict';

    // Mode (as the page names it) -> its entry in /dependencies
    const DEPENDENCY_KEYS = {
        pager: 'pager',
        sensor: 'sensor',
        adsb: 'aircraft',
        ais: 'ais',
        aprs: 'aprs',
        bluetooth: 'bluetooth',
        radiosonde: 'radiosonde',
        subghz: 'subghz',
    };

    let data = null;
    let pending = null;

    function load() {
        if (data) return Promise.resolve(data);
        if (!pending) {
            pending = fetch('/dependencies')
                .then((r) => (r.ok ? r.json() : null))
                .then((json) => { data = json; return data; })
                .catch(() => null);
        }
        return pending;
    }

    function forMode(mode) {
        const key = DEPENDENCY_KEYS[mode];
        const entry = key && data && data.modes && data.modes[key];
        if (!entry || entry.ready || !entry.missing_required || !entry.missing_required.length) return null;
        const first = entry.tools && entry.tools[entry.missing_required[0]];
        const install = first && first.install ? (first.install[data.pkg_manager] || first.install.manual || '') : '';
        return { missing: entry.missing_required.slice(), hint: install };
    }

    return { load, forMode };
})();

window.ToolReadiness = ToolReadiness;
