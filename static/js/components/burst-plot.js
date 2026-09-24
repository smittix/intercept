/**
 * BurstPlot: packets over the last minute, as they were received.
 *
 * Each packet is a mark at the time it arrived, as tall as its signal level,
 * coloured by its SNR (weak / fair / strong), and the newest are labelled
 * with what sent them. The noise floor is a shaded band. It replaces a
 * synthesised "audio waveform": rtl_433 reports levels per packet, not
 * audio, so this draws only what was measured.
 *
 *     const plot = BurstPlot.create(el, { title: 'Packets' });
 *     plot.add({ level: -12.3, snr: 14.1, noise: -26, label: 'Acurite-Tower' });
 *     plot.destroy();
 *
 * Levels are dB as rtl_433 gives them (dBFS: 0 is full scale, typical packets
 * sit between -30 and -3). Styles: static/css/components/burst-plot.css.
 */
const BurstPlot = (function () {
    'use strict';

    const FLOOR_DB = -40;
    const CEIL_DB = 0;
    const LABEL_GAP = 0.09;  // of the width, between labelled marks

    function create(container, options) {
        const opts = Object.assign({ title: 'Packets', windowSec: 60 }, options || {});
        const bursts = [];
        let noise = null;
        let timer = null;

        container.classList.add('bp');
        container.innerHTML = `
            <div class="bp-head">
                <span class="bp-title"></span>
                <span class="bp-readout"></span>
                <span class="bp-status">Quiet</span>
            </div>
            <div class="bp-plot">
                <svg viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">
                    <line class="bp-grid" x1="0" x2="1000" y1="25" y2="25"/>
                    <line class="bp-grid" x1="0" x2="1000" y1="50" y2="50"/>
                    <line class="bp-grid" x1="0" x2="1000" y1="75" y2="75"/>
                    <rect class="bp-noise" x="0" width="1000" y="100" height="0"/>
                    <g class="bp-marks"></g>
                </svg>
                <div class="bp-labels"></div>
                <div class="bp-empty">Waiting for packets</div>
            </div>
            <div class="bp-axis"><span></span><span></span><span>now</span></div>`;
        container.querySelector('.bp-title').textContent = opts.title + ' · last ' + opts.windowSec + ' s';
        const axis = container.querySelectorAll('.bp-axis span');
        axis[0].textContent = '-' + opts.windowSec + ' s';
        axis[1].textContent = '-' + opts.windowSec / 2 + ' s';
        const marks = container.querySelector('.bp-marks');
        const labels = container.querySelector('.bp-labels');
        const noiseRect = container.querySelector('.bp-noise');
        const readout = container.querySelector('.bp-readout');
        const status = container.querySelector('.bp-status');
        const empty = container.querySelector('.bp-empty');

        const y = (db) => 100 - Math.max(0, Math.min(1, (db - FLOOR_DB) / (CEIL_DB - FLOOR_DB))) * 100;
        const strength = (snr) => (snr == null ? 'unknown' : snr >= 15 ? 'strong' : snr >= 8 ? 'fair' : 'weak');

        function render() {
            const now = Date.now();
            const span = opts.windowSec * 1000;
            while (bursts.length && now - bursts[0].at > span) bursts.shift();

            const ns = 'http://www.w3.org/2000/svg';
            marks.replaceChildren();
            labels.replaceChildren();
            let lastLabelX = Infinity;
            for (let i = bursts.length - 1; i >= 0; i--) {
                const b = bursts[i];
                const frac = 1 - (now - b.at) / span;
                const top = y(b.level);
                const bottom = noise != null ? Math.max(top, y(noise)) : 100;
                const line = document.createElementNS(ns, 'rect');
                line.setAttribute('x', (frac * 1000 - 2).toFixed(1));
                line.setAttribute('width', 4);
                line.setAttribute('y', top.toFixed(1));
                line.setAttribute('height', Math.max(1.5, bottom - top).toFixed(1));
                line.setAttribute('class', 'bp-mark ' + strength(b.snr) + (now - b.at < 1500 ? ' fresh' : ''));
                marks.append(line);
                // Label the newest marks that have room
                if (b.label && lastLabelX - frac > LABEL_GAP) {
                    const tag = document.createElement('span');
                    tag.className = 'bp-label';
                    tag.style.left = (frac * 100).toFixed(2) + '%';
                    tag.style.top = top.toFixed(1) + '%';
                    tag.textContent = b.label;
                    labels.append(tag);
                    lastLabelX = frac;
                }
            }

            const floor = noise != null ? y(noise) : 100;
            noiseRect.setAttribute('y', floor.toFixed(1));
            noiseRect.setAttribute('height', (100 - floor).toFixed(1));

            const last = bursts[bursts.length - 1];
            empty.hidden = bursts.length > 0;
            const parts = [];
            if (last) parts.push('Last ' + last.level.toFixed(1) + ' dB');
            if (last && last.snr != null) parts.push('SNR ' + last.snr.toFixed(1) + ' dB');
            if (noise != null) parts.push('Noise ' + noise.toFixed(1) + ' dB');
            readout.textContent = parts.join(' · ');
            const recent = bursts.filter((b) => now - b.at < 10000).length;
            status.textContent = last && now - last.at < 1500 ? 'Packet' : recent ? recent + ' in 10 s' : 'Quiet';
            status.dataset.state = last && now - last.at < 1500 ? 'packet' : recent ? 'active' : 'quiet';
        }

        function add(packet) {
            const level = Number(packet.level);
            if (!Number.isFinite(level)) return;
            const snr = Number(packet.snr);
            const n = Number(packet.noise);
            if (Number.isFinite(n) && packet.noise !== null && packet.noise !== undefined) noise = n;
            bursts.push({ at: Date.now(), level, snr: Number.isFinite(snr) ? snr : null, label: packet.label || '' });
            render();
        }

        function clear() {
            bursts.length = 0;
            noise = null;
            render();
        }

        function destroy() {
            if (timer) clearInterval(timer);
            timer = null;
            container.replaceChildren();
            container.classList.remove('bp');
        }

        // Scroll with time; nothing to do while the plot is hidden
        timer = setInterval(() => { if (container.offsetParent !== null) render(); }, 500);
        render();
        return { add, clear, destroy };
    }

    return { create };
})();

window.BurstPlot = BurstPlot;
