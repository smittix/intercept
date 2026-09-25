/**
 * TrackIcons: map markers for aircraft and vessels, and their heading lines.
 *
 *     marker.setIcon(TrackIcons.aircraft({ type: 'jet', color, heading, selected,
 *                                          ground, emergency }));
 *     const vectors = TrackIcons.vectors(map);
 *     vectors.update(id, lat, lon, headingDeg, speedKnots, color);  // or vectors.remove(id)
 *
 * Aircraft are top-down silhouettes per type (airliner, widebody, business
 * jet, turboprop, light aircraft, helicopter, military, glider), with a thin
 * dark outline so they read over satellite imagery; only the selected one
 * glows. On the ground they are smaller and grey; an emergency squawk draws
 * a pulsing red ring. A heading line runs to where the contact will be after
 * `seconds` (a minute by default) at its present speed.
 *
 * Labels (`label` option) sit beside the marker, shown by TrackIcons.labels
 * only when zoomed in and never on top of one another. TrackIcons.hulls draws
 * a ship's real outline once it is big enough to see at the current zoom.
 * Styles: .ti-* in static/css/core/map-utils.css.
 */
const TrackIcons = (function () {
    'use strict';

    // 24 x 24, nose up
    const AIRCRAFT = {
        jet: 'M12 2c.7 0 1 .8 1 1.8V9.5l7.5 4v1.7L13 13.2v4.9l2.3 1.7v1.2L12 20.3 8.7 21v-1.2l2.3-1.7v-4.9l-7.5 2v-1.7l7.5-4V3.8C11 2.8 11.3 2 12 2z',
        widebody: 'M12 1.5c.9 0 1.4.9 1.4 2.1V9L22 13.4v1.9l-8.6-2.4v5l2.6 1.9v1.4L12 20.5l-4 .8v-1.4l2.6-1.9v-5L2 15.3v-1.9L10.6 9V3.6c0-1.2.5-2.1 1.4-2.1zM6 12.3h1.5v2.2H6zm10.5 0H18v2.2h-1.5z',
        bizjet: 'M12 3c.6 0 .9.7.9 1.6V11l6.1 3.2v1.3l-6.1-1.5v3.3l2 1.5v1l-2.9-.6-2.9.6v-1l2-1.5V14L5 15.5v-1.3l6.1-3.2V4.6c0-.9.3-1.6.9-1.6zm-2.4 13h1.2v2H9.6zm3.6 0h1.2v2h-1.2z',
        turboprop: 'M12 3c.6 0 .9.6.9 1.4V9H21v2.2l-8.1.8v5.4l2.6 1.4v1.3L12 19.4l-3.5.7v-1.3l2.6-1.4V12L3 11.2V9h8.1V4.4c0-.8.3-1.4.9-1.4zM6.2 7.3h1.2v1.5H6.2zm10.4 0h1.2v1.5h-1.2z',
        prop: 'M12 4.5c.5 0 .8.5.8 1.2V9H20v1.8l-7.2.6v4.9l2.2.9v1.2L12 19l-3 .4v-1.2l2.2-.9v-4.9L4 10.8V9h7.2V5.7c0-.7.3-1.2.8-1.2zM10 3.6h4v.8h-4z',
        helicopter: 'M12 6.5c1.3 0 2.2 1.1 2.2 2.6 0 1.2-.5 2.2-1.2 2.7V18h2v1H9v-1h2v-6.2c-.7-.5-1.2-1.5-1.2-2.7 0-1.5.9-2.6 2.2-2.6zM9 20h6v1H9z',
        military: 'M12 1.5l1.3 4.2V10l7.7 6v1.6l-7.7-2.2v3.3l2.3 1.7v1.2L12 20.8l-3.6.8v-1.2l2.3-1.7v-3.3L3 17.6V16l7.7-6V5.7z',
        glider: 'M12 5c.4 0 .6.4.6 1v3.6h9.9v1.1l-9.9.6v6.4l2 .6v.9H9.4v-.9l2-.6v-6.4l-9.9-.6V9.6h9.9V6c0-.6.2-1 .6-1z',
    };
    // Drawn as outlines over the body
    const EXTRAS = {
        helicopter: '<circle cx="12" cy="9.2" r="7.2" fill="none" stroke="currentColor" stroke-width="1" opacity="0.7"/>',
    };
    const SIZES = { widebody: 28, jet: 24, bizjet: 22, turboprop: 24, prop: 20, helicopter: 22, military: 24, glider: 24 };
    const GROUND_COLOR = '#8a96a3';
    const EMERGENCY_COLOR = '#ff3b3b';

    function esc(text) {
        return String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
    }

    // Priority decides which of two overlapping labels stays
    function labelHtml(text, priority) {
        return text ? `<span class="ti-label" data-priority="${priority || 0}">${esc(text)}</span>` : '';
    }

    function aircraft(opts) {
        const type = AIRCRAFT[opts.type] ? opts.type : 'jet';
        const ground = !!opts.ground;
        const emergency = !!opts.emergency;
        const size = ground ? 16 : SIZES[type];
        const color = emergency ? EMERGENCY_COLOR : ground ? GROUND_COLOR : opts.color;
        const heading = Math.round((opts.heading || 0) / 5) * 5;
        const ring = opts.selected ? '<div class="tracking-ring"></div><div class="tracking-ring-inner"></div>' : '';
        const alert = emergency ? '<div class="ti-emergency"></div>' : '';
        return L.divIcon({
            className: 'aircraft-marker ti-marker aircraft-' + type + (opts.selected ? ' selected' : '') +
                (ground ? ' ti-ground' : '') + (emergency ? ' ti-alert' : ''),
            html: ring + alert +
                `<svg class="ti-shape" width="${size}" height="${size}" viewBox="0 0 24 24" style="transform: rotate(${heading}deg); color: ${color};">` +
                `<path fill="currentColor" d="${AIRCRAFT[type]}"/>${EXTRAS[type] || ''}</svg>` +
                labelHtml(opts.label, opts.selected ? 3 : emergency ? 2 : 0),
            iconSize: [size, size],
            iconAnchor: [size / 2, size / 2],
        });
    }

    /**
     * A vessel shape (from the page's own set) with the same outline treatment;
     * at anchor or moored (`moored`), a dot, since it points nowhere in particular.
     */
    function vessel(opts) {
        const size = opts.moored ? 14 : (opts.size || 24);
        const ring = opts.selected ? '<div class="tracking-ring"></div><div class="tracking-ring-inner"></div>' : '';
        const shape = opts.moored
            ? `<svg class="ti-shape" width="${size}" height="${size}" viewBox="0 0 24 24" style="color: ${opts.color};">` +
              '<circle cx="12" cy="12" r="7" fill="currentColor"/><circle cx="12" cy="12" r="2.5" fill="rgba(0,0,0,0.55)"/></svg>'
            : `<svg class="ti-shape" width="${size}" height="${size}" viewBox="0 0 24 24" style="transform: rotate(${opts.heading || 0}deg); color: ${opts.color};">` +
              `<path fill="currentColor" d="${opts.path}"/></svg>`;
        return L.divIcon({
            className: 'vessel-marker ti-marker' + (opts.selected ? ' selected' : '') + (opts.moored ? ' ti-moored' : ''),
            html: ring + shape + labelHtml(opts.label, opts.selected ? 3 : 0),
            iconSize: [size, size],
            iconAnchor: [size / 2, size / 2],
        });
    }

    // Where a contact will be after `seconds` at `knots` on `heading` (flat-earth, fine over a few km)
    function ahead(lat, lon, heading, knots, seconds) {
        const metres = knots * 0.514444 * seconds;
        const rad = heading * Math.PI / 180;
        const dLat = (metres * Math.cos(rad)) / 111320;
        const dLon = (metres * Math.sin(rad)) / (111320 * Math.cos(lat * Math.PI / 180));
        return [lat + dLat, lon + dLon];
    }

    function vectors(map, options) {
        const opts = Object.assign({ seconds: 60, minKnots: 3 }, options || {});
        const lines = {};
        return {
            update(id, lat, lon, heading, knots, color) {
                const moving = Number.isFinite(heading) && Number.isFinite(knots) && knots >= opts.minKnots;
                if (!moving || !Number.isFinite(lat) || !Number.isFinite(lon)) { this.remove(id); return; }
                const points = [[lat, lon], ahead(lat, lon, heading, knots, opts.seconds)];
                if (lines[id]) {
                    lines[id].setLatLngs(points);
                    lines[id].setStyle({ color });
                } else {
                    lines[id] = L.polyline(points, {
                        color, weight: 1.5, opacity: 0.85, interactive: false, className: 'ti-vector',
                    }).addTo(map);
                }
            },
            remove(id) {
                if (lines[id]) { map.removeLayer(lines[id]); delete lines[id]; }
            },
            clear() { Object.keys(lines).forEach((id) => this.remove(id)); },
        };
    }

    /**
     * Labels on or off (`setEnabled`) and shown only from `minZoom`; where two
     * overlap, the one with the lower priority (then the later one) hides.
     */
    function labels(map, options) {
        const opts = Object.assign({ minZoom: 8, enabled: true }, options || {});
        const container = map.getContainer();
        let enabled = opts.enabled;
        let pending = false;

        function declutter() {
            pending = false;
            const on = enabled && map.getZoom() >= opts.minZoom;
            container.classList.toggle('ti-labels-on', on);
            if (!on) return;
            const els = Array.from(container.querySelectorAll('.ti-label'));
            els.forEach((el) => el.classList.remove('ti-label-hidden'));
            const placed = [];
            els.map((el) => ({ el, p: Number(el.dataset.priority) || 0, r: el.getBoundingClientRect() }))
                .filter((l) => l.r.width > 0)
                .sort((a, b) => b.p - a.p)
                .forEach((l) => {
                    const clash = placed.some((r) => !(l.r.right < r.left || l.r.left > r.right || l.r.bottom < r.top || l.r.top > r.bottom));
                    if (clash) l.el.classList.add('ti-label-hidden');
                    else placed.push(l.r);
                });
        }

        function schedule() {
            if (pending) return;
            pending = true;
            requestAnimationFrame(declutter);
        }

        map.on('zoomend moveend', schedule);
        setInterval(schedule, 2000);  // markers move and relabel between map moves
        schedule();
        return {
            setEnabled(on) { enabled = !!on; schedule(); },
            refresh: schedule,
        };
    }

    // A point `east`, `north` metres from lat/lon (flat-earth, fine over a ship's length)
    function offset(lat, lon, east, north) {
        return [lat + north / 111320, lon + east / (111320 * Math.cos(lat * Math.PI / 180))];
    }

    /**
     * A ship's hull to scale, from its size and where its antenna is: to_bow,
     * to_stern, to_port, to_starboard (metres). Drawn only when the ship is at
     * least `minPixels` long at the current zoom; `update` says whether it drew,
     * so the page can hide the icon under it.
     */
    function hulls(map, options) {
        const opts = Object.assign({ minPixels: 20, onClick: null }, options || {});
        const shapes = {};
        const last = {};

        function outline(lat, lon, heading, d) {
            const L2 = d.to_bow + d.to_stern;
            const shoulder = d.to_bow - 0.18 * L2;  // where the bow begins to narrow
            const mid = (d.to_starboard - d.to_port) / 2;
            // Ship frame: x to starboard, y to the bow, antenna at the origin
            const pts = [[-d.to_port, -d.to_stern], [d.to_starboard, -d.to_stern],
                [d.to_starboard, shoulder], [mid, d.to_bow], [-d.to_port, shoulder]];
            const h = heading * Math.PI / 180;
            return pts.map(([x, y]) => offset(lat, lon, x * Math.cos(h) + y * Math.sin(h), -x * Math.sin(h) + y * Math.cos(h)));
        }

        function pixelsLong(lat, lon, heading, d) {
            const [b, s] = [offset(lat, lon, 0, 0), offset(lat, lon, Math.sin(heading * Math.PI / 180) * (d.to_bow + d.to_stern),
                Math.cos(heading * Math.PI / 180) * (d.to_bow + d.to_stern))];
            return map.latLngToLayerPoint(b).distanceTo(map.latLngToLayerPoint(s));
        }

        function draw(id) {
            const a = last[id];
            const known = a && Number.isFinite(a.heading) && ['to_bow', 'to_stern', 'to_port', 'to_starboard'].every((k) => Number.isFinite(a.dims[k]));
            if (!known || pixelsLong(a.lat, a.lon, a.heading, a.dims) < opts.minPixels) {
                if (shapes[id]) { map.removeLayer(shapes[id]); delete shapes[id]; }
                return false;
            }
            const points = outline(a.lat, a.lon, a.heading, a.dims);
            if (shapes[id]) {
                shapes[id].setLatLngs(points);
                shapes[id].setStyle({ color: a.color, fillColor: a.color });
            } else {
                shapes[id] = L.polygon(points, {
                    color: a.color, weight: 1.5, fillColor: a.color, fillOpacity: 0.35, className: 'ti-hull',
                }).addTo(map);
                if (opts.onClick) shapes[id].on('click', () => opts.onClick(id));
            }
            return true;
        }

        const api = {
            update(id, lat, lon, heading, dims, color) {
                last[id] = { lat, lon, heading, dims: dims || {}, color };
                return draw(id);
            },
            remove(id) {
                if (shapes[id]) { map.removeLayer(shapes[id]); delete shapes[id]; }
                delete last[id];
            },
            onZoom: null,
        };
        map.on('zoomend', () => {
            Object.keys(last).forEach((id) => {
                const drawn = draw(id);
                if (api.onZoom) api.onZoom(id, drawn);
            });
        });
        return api;
    }

    const EMERGENCY_SQUAWKS = new Set(['7500', '7600', '7700']);
    function isEmergencySquawk(squawk) {
        return EMERGENCY_SQUAWKS.has(String(squawk || '').trim());
    }

    return { aircraft, vessel, vectors, labels, hulls, isEmergencySquawk, AIRCRAFT };
})();

window.TrackIcons = TrackIcons;
