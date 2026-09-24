/**
 * RadarFace: the face of a radar display, shared by the Bluetooth and Wi-Fi
 * radars. Rings with band labels, crosshairs and bearing ticks, a sweep with
 * a fading trail, and a rippling centre. Returns SVG markup for the inside of
 * an <svg> of viewBox "0 0 size size"; whoever uses it draws its own blips.
 *
 *     svg.insertAdjacentHTML('afterbegin', RadarFace.markup({ id: 'wf', size: 210, padding: 5,
 *         rings: [{ radius: 0.35, label: 'STRONG' }, { radius: 0.7, label: 'MEDIUM' }] }));
 *
 * `id` prefixes the gradient and glow filter ids, so two radars can share a
 * page: blips use filter="url(#<id>-glow)". The sweep and ripple animate
 * inside the SVG (SMIL), so they need no CSS transform origin, and are left
 * out when the viewer prefers reduced motion. Styles: .pr-* in
 * static/css/components/proximity-viz.css.
 */
const RadarFace = (function () {
    'use strict';

    function markup(options) {
        const id = options.id || 'radar';
        const size = options.size;
        const c = size / 2;
        const R = c - options.padding;
        const rings = options.rings || [];
        const sweepSeconds = options.sweepSeconds || 4;
        const centerRadius = options.centerRadius || Math.max(3, size / 80);
        const labelSize = Math.max(6, size / 44);
        const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const at = (deg, r) => {
            const a = deg * Math.PI / 180;
            return [c + Math.sin(a) * r, c - Math.cos(a) * r];
        };

        // Bearing ticks every 10 degrees, longer every 30
        const tickLen = size / 50;
        const ticks = Array.from({ length: 36 }, (_, i) => {
            const len = i % 3 === 0 ? tickLen : tickLen / 2;
            const [x1, y1] = at(i * 10, R);
            const [x2, y2] = at(i * 10, R - len);
            return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"
                          class="pr-tick${i % 3 === 0 ? ' major' : ''}"/>`;
        }).join('');

        // Sweep: a wedge of thin slices fading behind the leading edge
        const slices = 24, wedge = 60;
        const sweep = Array.from({ length: slices }, (_, i) => {
            const [x0, y0] = at(-(i + 1) * wedge / slices, R);
            const [x1, y1] = at(-i * wedge / slices, R);
            const opacity = (0.22 * Math.pow(1 - i / slices, 2)).toFixed(3);
            return `<path d="M${c},${c} L${x0.toFixed(1)},${y0.toFixed(1)} A${R},${R} 0 0,1 ${x1.toFixed(1)},${y1.toFixed(1)} Z"
                          fill="var(--accent-cyan)" fill-opacity="${opacity}"/>`;
        }).join('');
        const spin = reduceMotion ? '' : `<animateTransform attributeName="transform" type="rotate"
                     from="0 ${c} ${c}" to="360 ${c} ${c}" dur="${sweepSeconds}s" repeatCount="indefinite"/>`;
        const ripple = reduceMotion ? '' : `<circle cx="${c}" cy="${c}" r="${centerRadius}" class="pr-ripple">
                <animate attributeName="r" from="${centerRadius}" to="${centerRadius * 5}" dur="2.4s" repeatCount="indefinite"/>
                <animate attributeName="stroke-opacity" from="0.6" to="0" dur="2.4s" repeatCount="indefinite"/>
            </circle>`;

        return `
            <defs>
                <radialGradient id="${id}-bg" cx="50%" cy="50%" r="50%">
                    <stop offset="0%" stop-color="var(--accent-cyan)" stop-opacity="0.10"/>
                    <stop offset="70%" stop-color="var(--accent-cyan)" stop-opacity="0.03"/>
                    <stop offset="100%" stop-color="var(--accent-cyan)" stop-opacity="0"/>
                </radialGradient>
                <filter id="${id}-glow" x="-100%" y="-100%" width="300%" height="300%">
                    <feGaussianBlur stdDeviation="${(size / 160).toFixed(2)}" result="blur"/>
                    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
                </filter>
            </defs>
            <circle cx="${c}" cy="${c}" r="${R}" class="pr-face" fill="url(#${id}-bg)"/>
            <g class="radar-rings">
                ${rings.map((ring) => `
                    <circle cx="${c}" cy="${c}" r="${(ring.radius * R).toFixed(1)}" class="pr-ring"/>
                    <text x="${(c + labelSize * 0.6).toFixed(1)}" y="${(c - ring.radius * R + labelSize * 1.3).toFixed(1)}"
                          class="pr-ring-label" font-size="${labelSize.toFixed(1)}">${ring.label}</text>
                `).join('')}
                <circle cx="${c}" cy="${c}" r="${R}" class="pr-ring outer"/>
                <line x1="${c - R}" y1="${c}" x2="${c + R}" y2="${c}" class="pr-axis"/>
                <line x1="${c}" y1="${c - R}" x2="${c}" y2="${c + R}" class="pr-axis"/>
                ${ticks}
            </g>
            <g class="pr-sweep">
                ${sweep}
                <line x1="${c}" y1="${c}" x2="${c}" y2="${c - R}" class="pr-sweep-edge" filter="url(#${id}-glow)"/>
                ${spin}
            </g>
            <g class="pr-center">
                ${ripple}
                <circle cx="${c}" cy="${c}" r="${centerRadius}" class="pr-you" filter="url(#${id}-glow)"/>
            </g>
        `;
    }

    return { markup };
})();

window.RadarFace = RadarFace;
