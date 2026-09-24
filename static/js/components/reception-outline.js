/**
 * ReceptionOutline: how far you actually hear, in each direction.
 *
 * Keeps the furthest contact seen in each 10° of bearing from the observer
 * and draws them as one outline on the map: the shape of your reception,
 * which shows at a glance where the antenna is blocked or reaching far.
 *
 *     const outline = ReceptionOutline.create(map, {
 *         key: 'adsb', observer: () => observerLocation, color: '#4a9eff',
 *     });
 *     outline.add(lat, lon);          // each contact position
 *     outline.setVisible(checkbox.checked);
 *     outline.reset();
 *
 * The outline is kept per observer location (to about 5 km) in this
 * browser's storage, so it builds up across sessions; moving the observer
 * shows the outline for the new place.
 */
const ReceptionOutline = (function () {
    'use strict';

    const BINS = 36;
    const REDRAW_MS = 2000;

    function toRad(d) { return d * Math.PI / 180; }

    function distanceKm(lat1, lon1, lat2, lon2) {
        const dLat = toRad(lat2 - lat1);
        const dLon = toRad(lon2 - lon1);
        const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
        return 6371 * 2 * Math.asin(Math.sqrt(a));
    }

    function bearing(lat1, lon1, lat2, lon2) {
        const y = Math.sin(toRad(lon2 - lon1)) * Math.cos(toRad(lat2));
        const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
            Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(toRad(lon2 - lon1));
        return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
    }

    function create(map, options) {
        const opts = Object.assign({ key: 'contacts', color: '#4a9eff', observer: () => null, visible: true }, options || {});
        let bins = [];
        let storeKey = null;
        let layer = null;
        let visible = opts.visible;
        let dirty = false;

        function observer() {
            const o = opts.observer();
            if (!o || !Number.isFinite(o.lat) || !Number.isFinite(o.lon) || (o.lat === 0 && o.lon === 0)) return null;
            return o;
        }

        function keyFor(o) {
            return `intercept.reach.${opts.key}.${(Math.round(o.lat * 20) / 20).toFixed(2)},${(Math.round(o.lon * 20) / 20).toFixed(2)}`;
        }

        // Follow the observer: load the outline kept for where it now is
        function sync() {
            const o = observer();
            const key = o ? keyFor(o) : null;
            if (key === storeKey) return o;
            storeKey = key;
            bins = new Array(BINS).fill(null);
            if (key) {
                try {
                    const saved = JSON.parse(localStorage.getItem(key) || 'null');
                    if (Array.isArray(saved) && saved.length === BINS) bins = saved;
                } catch (err) { /* start afresh */ }
            }
            dirty = true;
            return o;
        }

        function save() {
            if (!storeKey) return;
            try { localStorage.setItem(storeKey, JSON.stringify(bins)); } catch (err) { /* this visit only */ }
        }

        function add(lat, lon) {
            if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
            const o = sync();
            if (!o) return;
            const km = distanceKm(o.lat, o.lon, lat, lon);
            if (km < 0.5 || km > 1500) return;  // on top of you, or a bad position
            const bin = Math.floor(bearing(o.lat, o.lon, lat, lon) / (360 / BINS)) % BINS;
            if (!bins[bin] || km > bins[bin][2]) {
                bins[bin] = [+lat.toFixed(4), +lon.toFixed(4), +km.toFixed(1)];
                dirty = true;
            }
        }

        function draw() {
            if (!dirty) return;
            dirty = false;
            save();
            if (layer) { map.removeLayer(layer); layer = null; }
            const points = bins.filter(Boolean);
            if (!visible || points.length < 3) return;
            const furthest = Math.max(...points.map((p) => p[2]));
            layer = L.polygon(points.map((p) => [p[0], p[1]]), {
                color: opts.color,
                weight: 1.5,
                opacity: 0.8,
                dashArray: '6 4',
                fillColor: opts.color,
                fillOpacity: 0.06,
                interactive: true,
            }).bindTooltip(`Reception: the furthest contact in each direction · up to ${Math.round(furthest)} km` +
                ` · ${points.length} of ${BINS} directions heard`, { sticky: true });
            layer.addTo(map);
        }

        function setVisible(show) {
            visible = !!show;
            dirty = true;
            draw();
        }

        function reset() {
            bins = new Array(BINS).fill(null);
            if (storeKey) { try { localStorage.removeItem(storeKey); } catch (err) { /* nothing kept */ } }
            dirty = true;
            draw();
        }

        sync();
        draw();
        setInterval(draw, REDRAW_MS);
        return { add, setVisible, reset, redraw: () => { sync(); dirty = true; draw(); } };
    }

    return { create, _distanceKm: distanceKm, _bearing: bearing };
})();

window.ReceptionOutline = ReceptionOutline;
