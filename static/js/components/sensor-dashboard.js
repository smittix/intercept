const SensorDashboard = (function () {
    'use strict';

    const STORAGE_KEY   = 'sensorView';
    const MAX_SPARK_PTS = 30;

    // Map<deviceKey, { card: HTMLElement, history: number[], primaryColor: string }>
    const devices = new Map();

    // ---- Helpers ----

    function esc(str) {
        return String(str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function isRecent(timestamp) {
        if (!timestamp) return false;
        const ts = typeof timestamp === 'string' ? new Date(timestamp).getTime() : Number(timestamp);
        return (Date.now() - ts) < 10000;
    }

    // ---- Primary value for sparkline ----

    function getPrimary(msg) {
        if (msg.temperature !== undefined)
            return { value: msg.temperature, color: '#f59e0b' };
        if (msg.pressure !== undefined)
            return { value: msg.pressure, color: '#a78bfa' };
        if (msg.wind_speed !== undefined)
            return { value: msg.wind_speed, color: '#4aa3ff' };
        return null;
    }

    function getFlashClass(msg) {
        return msg.temperature !== undefined ? 'sdb-card--flash-blue' : 'sdb-card--flash-purple';
    }

    // ---- HTML builders ----

    function buildReadingsHTML(msg) {
        // State-only device (no continuous numeric field)
        if (msg.state !== undefined && msg.temperature === undefined
                && msg.pressure === undefined && msg.wind_speed === undefined) {
            const raw = String(msg.state);
            const isOn = raw === '1' || raw === 'true' || raw === 'on' || raw === 'active';
            return `<div class="sdb-state">
                <span class="sdb-state-dot ${isOn ? 'sdb-state-dot--on' : 'sdb-state-dot--off'}"></span>
                <span class="sdb-state-label">${esc(raw.toUpperCase())}</span>
            </div>`;
        }

        const parts = [];
        if (msg.temperature !== undefined)
            parts.push({ val: msg.temperature, unit: `°${msg.temperature_unit || 'C'}`, label: 'Temp',   color: '#f59e0b' });
        if (msg.humidity !== undefined)
            parts.push({ val: msg.humidity,    unit: '%',                                label: 'Humid',  color: '#38bdf8' });
        if (msg.pressure !== undefined)
            parts.push({ val: msg.pressure,    unit: msg.pressure_unit || 'hPa',         label: 'Press',  color: '#a78bfa' });
        if (msg.wind_speed !== undefined)
            parts.push({ val: msg.wind_speed,  unit: msg.wind_unit || 'km/h',            label: 'Wind',   color: '#4aa3ff' });
        if (msg.rain !== undefined)
            parts.push({ val: msg.rain,        unit: msg.rain_unit || 'mm',              label: 'Rain',   color: '#38bdf8' });

        if (parts.length === 0)
            return `<div class="sdb-no-readings">No numeric data</div>`;

        return parts.map(p => `
            <div class="sdb-reading">
                <div class="sdb-reading-val" style="color:${p.color}">${esc(String(p.val))}</div>
                <div class="sdb-reading-unit">${esc(p.unit)}</div>
                <div class="sdb-reading-label">${p.label}</div>
            </div>`).join('');
    }

    function buildSparklineHTML(history, color) {
        if (history.length < 2)
            return `<div class="sdb-spark-placeholder">Collecting data…</div>`;

        const W = 120, H = 22, PAD = 2;
        const min = Math.min(...history);
        const max = Math.max(...history);
        const range = max - min || 1;
        const pts = history.map((v, i) => {
            const x = (i / (history.length - 1)) * (W - PAD * 2) + PAD;
            const y = H - PAD - ((v - min) / range) * (H - PAD * 2);
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');
        const last = pts.split(' ').pop().split(',');
        return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
            <rect fill="var(--bg-secondary)" width="${W}" height="${H}"/>
            <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" opacity="0.85"/>
            <circle cx="${last[0]}" cy="${last[1]}" r="2" fill="${color}"/>
        </svg>`;
    }

    function buildCardHTML(msg, history, primaryColor) {
        const age     = InterceptTime.relTimeHtml(msg.timestamp);
        const fresh   = isRecent(msg.timestamp);
        const batLow  = msg.battery === 'LOW';
        const sparkHTML = history.length > 0
            ? buildSparklineHTML(history, primaryColor || '#4aa3ff')
            : `<div class="sdb-spark-placeholder">Waiting for data…</div>`;

        return `
            <div class="sdb-card-header">
                <div>
                    <div class="sdb-name">${esc(msg.model || 'Unknown')}</div>
                    <div class="sdb-id">ID ${esc(String(msg.id || 'N/A'))}${msg.channel ? ` · Ch ${esc(String(msg.channel))}` : ''}</div>
                </div>
                <div class="sdb-age${fresh ? ' sdb-age--fresh' : ''}">${age}</div>
            </div>
            <div class="sdb-readings">${buildReadingsHTML(msg)}</div>
            <div class="sdb-spark">${sparkHTML}</div>
            <div class="sdb-footer">
                ${msg.battery ? `<span class="sdb-bat ${batLow ? 'sdb-bat--low' : 'sdb-bat--ok'}">● BAT ${esc(msg.battery)}</span>` : '<span></span>'}
                ${msg.snr !== undefined ? `<span class="sdb-snr">SNR ${esc(String(msg.snr))} dB</span>` : '<span></span>'}
                ${msg.frequency ? `<span class="sdb-freq">${esc(String(msg.frequency))}</span>` : '<span></span>'}
            </div>`;
    }

    // ---- Public: reading hook ----

    function addReading(msg) {
        const key = `${msg.model || 'Unknown'}_${msg.id || msg.channel || '0'}`;
        const primary = getPrimary(msg);

        if (devices.has(key)) {
            const dev = devices.get(key);
            if (primary) {
                dev.history.push(primary.value);
                if (dev.history.length > MAX_SPARK_PTS) dev.history.shift();
                dev.primaryColor = primary.color;
            }
            dev.card.innerHTML = buildCardHTML(msg, dev.history, dev.primaryColor);
            const cls = getFlashClass(msg);
            dev.card.classList.add(cls);
            setTimeout(() => dev.card.classList.remove(cls), 820);
        } else {
            const history = primary ? [primary.value] : [];
            const grid = document.getElementById('sensorDashboardGrid');
            if (!grid) return;
            const card = document.createElement('div');
            card.className = 'sdb-card sdb-card--new';
            card.innerHTML = buildCardHTML(msg, history, primary ? primary.color : '#4aa3ff');
            grid.insertBefore(card, grid.firstChild);
            setTimeout(() => card.classList.remove('sdb-card--new'), 2000);
            devices.set(key, { card, history, primaryColor: primary ? primary.color : '#4aa3ff' });
        }
    }

    // ---- Show / hide / reset ----

    function applyViewState(mode) {
        const view    = document.getElementById('sensorDashboardView');
        const output  = document.getElementById('output');
        const feedCol = document.querySelector('.pdir-feed-col');

        if (mode === 'sensor') {
            const saved = localStorage.getItem(STORAGE_KEY) || 'dashboard';
            const isDash = saved === 'dashboard';
            if (view)    view.style.display    = isDash ? 'block' : 'none';
            if (output)  output.style.display  = isDash ? 'none'  : '';
            if (feedCol) feedCol.style.display = isDash ? 'none'  : '';
            _updateToggle(isDash);
        } else {
            if (view)    view.style.display    = 'none';
            if (feedCol) feedCol.style.display = '';
        }
    }

    function show() {
        localStorage.setItem(STORAGE_KEY, 'dashboard');
        applyViewState('sensor');
    }

    function hide() {
        localStorage.setItem(STORAGE_KEY, 'feed');
        applyViewState('sensor');
    }

    function _updateToggle(isDash) {
        document.getElementById('sensorToggleDash')?.classList.toggle('view-toggle-btn--active', isDash);
        document.getElementById('sensorToggleFeed')?.classList.toggle('view-toggle-btn--active', !isDash);
    }

    function reset() {
        devices.clear();
        const grid = document.getElementById('sensorDashboardGrid');
        if (grid) grid.innerHTML = '';
    }

    return { addReading, show, hide, reset, applyViewState };
})();
