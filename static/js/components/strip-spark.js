/**
 * StripSpark: a small trend line inside a dashboard stats-strip tile.
 *
 *     StripSpark.attach('stripAircraftNow', { title: 'Aircraft in view, last 15 min' });
 *
 * Samples the tile's number every `everyMs` (default 10 s) and keeps
 * `samples` of them (default 90, 15 minutes), drawn beside the value. It
 * reads the number the page already shows, so the page needs no other change.
 * Styles: .strip-spark in the dashboard's stylesheet.
 */
const StripSpark = (function () {
    'use strict';

    function attach(valueId, options) {
        const valueEl = document.getElementById(valueId);
        const stat = valueEl && valueEl.closest('.strip-stat');
        if (!stat) return;
        const opts = Object.assign({ everyMs: 10000, samples: 90, title: '' }, options || {});
        const values = [];

        const ns = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('class', 'strip-spark');
        svg.setAttribute('viewBox', '0 0 60 20');
        svg.setAttribute('preserveAspectRatio', 'none');
        svg.setAttribute('aria-hidden', 'true');
        const line = document.createElementNS(ns, 'polyline');
        svg.append(line);
        stat.classList.add('has-spark');
        stat.append(svg);
        if (opts.title) stat.title = opts.title;

        function sample() {
            const n = parseFloat(valueEl.textContent);
            values.push(Number.isFinite(n) ? n : 0);
            if (values.length > opts.samples) values.shift();
            const max = Math.max(1, ...values);
            const step = 60 / Math.max(1, opts.samples - 1);
            const offset = (opts.samples - values.length) * step;  // newest at the right
            line.setAttribute('points', values
                .map((v, i) => `${(offset + i * step).toFixed(1)},${(19 - v / max * 17).toFixed(1)}`).join(' '));
        }

        sample();
        (window.VisibleInterval ? VisibleInterval.set : setInterval)(sample, opts.everyMs);
    }

    return { attach };
})();

window.StripSpark = StripSpark;
