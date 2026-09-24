/**
 * Space Weather Mode — IIFE module
 * Polls /space-weather/data every 5 min, renders dashboard with Chart.js
 */
const SpaceWeather = (function () {
    'use strict';

    let _initialized = false;
    let _pollTimer = null;
    let _autoRefresh = true;
    const POLL_INTERVAL = 5 * 60 * 1000; // 5 min

    // Chart.js instances
    let _kpChart = null;
    let _windChart = null;
    let _xrayChart = null;

    // Current image selections
    let _solarImageKey = 'sdo_193';
    let _drapFreq = 'drap_global';
    const SOLAR_IMAGE_FALLBACKS = {
        sdo_193: 'https://sdo.gsfc.nasa.gov/assets/img/latest/latest_512_0193.jpg',
        sdo_304: 'https://sdo.gsfc.nasa.gov/assets/img/latest/latest_512_0304.jpg',
        sdo_magnetogram: 'https://sdo.gsfc.nasa.gov/assets/img/latest/latest_512_HMIBC.jpg',
    };

    /** Stable cache-bust key that rotates every 5 minutes (matches backend max-age). */
    function _cacheBust() {
        return 'v=' + Math.floor(Date.now() / 300000);
    }

    // -------------------------------------------------------------------
    // Public API
    // -------------------------------------------------------------------

    function init() {
        if (!_initialized) {
            _initialized = true;
        }
        // Warm the backend image cache in parallel before rendering
        fetch('/space-weather/prefetch-images').catch(function () {});
        refresh();
        _startAutoRefresh();
    }

    function destroy() {
        _stopAutoRefresh();
        _destroyCharts();
        _initialized = false;
    }

    function refresh() {
        _fetchData();
    }

    function selectSolarImage(key) {
        _solarImageKey = key;
        _updateSolarImageTabs();
        const frame = document.getElementById('swSolarImageFrame');
        if (frame) {
            frame.innerHTML = '<div class="sw-loading">Loading</div>';
            _loadImageWithFallback(
                frame,
                ['/space-weather/image/' + key + '?' + _cacheBust(), _directImageUrlForKey(key)],
                key,
                '<div class="sw-empty">NASA SDO image is temporarily unavailable</div>'
            );
        }
    }

    function selectDrapFreq(key) {
        _drapFreq = key;
        _updateDrapTabs();
        const frame = document.getElementById('swDrapImageFrame');
        if (frame) {
            frame.innerHTML = '<div class="sw-loading">Loading</div>';
            _loadImageWithFallback(
                frame,
                ['/space-weather/image/' + key + '?' + _cacheBust()],
                key,
                '<div class="sw-empty">Failed to load image</div>'
            );
        }
    }

    function toggleAutoRefresh() {
        const cb = document.getElementById('swAutoRefresh');
        _autoRefresh = cb ? cb.checked : !_autoRefresh;
        if (_autoRefresh) _startAutoRefresh();
        else _stopAutoRefresh();
    }

    // -------------------------------------------------------------------
    // Polling
    // -------------------------------------------------------------------

    function _startAutoRefresh() {
        _stopAutoRefresh();
        if (_autoRefresh) {
            _pollTimer = setInterval(_fetchData, POLL_INTERVAL);
        }
    }

    function _stopAutoRefresh() {
        if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    }

    function _directImageUrlForKey(key) {
        const base = SOLAR_IMAGE_FALLBACKS[key];
        if (!base) return null;
        return base + '?' + _cacheBust();
    }

    function _loadImageWithFallback(frame, urls, alt, failureHtml) {
        const candidates = (urls || []).filter(Boolean);
        if (!frame || candidates.length === 0) {
            if (frame) frame.innerHTML = failureHtml;
            return;
        }

        let index = 0;
        const img = new Image();
        img.alt = alt;
        img.referrerPolicy = 'no-referrer';
        img.onload = function () {
            frame.innerHTML = '';
            frame.appendChild(img);
        };
        img.onerror = function () {
            index += 1;
            if (index < candidates.length) {
                img.src = candidates[index];
                return;
            }
            frame.innerHTML = failureHtml;
        };
        img.src = candidates[index];
    }

    function _fetchData() {
        fetch('/space-weather/data')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                _renderAll(data);
                _updateTimestamp();
            })
            .catch(function (err) {
                console.warn('SpaceWeather fetch error:', err);
            });
    }

    // -------------------------------------------------------------------
    // Master render
    // -------------------------------------------------------------------

    function _renderAll(data) {
        _renderHeaderStrip(data);
        _renderScales(data);
        _renderBandConditions(data);
        _renderKpChart(data);
        _renderWindChart(data);
        _renderXrayChart(data);
        _renderFlareProb(data);
        _renderSolarImage();
        _renderDrapImage();
        _renderAuroraImage();
        _renderAlerts(data);
        _renderRegions(data);
        _updateSidebar(data);
    }

    // -------------------------------------------------------------------
    // Header strip
    // -------------------------------------------------------------------

    function _renderHeaderStrip(data) {
        var sfi = '--', kp = '--', aIndex = '--', ssn = '--', wind = '--', bz = '--';

        // SFI from band_conditions (HamQSL) or flux
        if (data.band_conditions && data.band_conditions.sfi) {
            sfi = data.band_conditions.sfi;
        } else if (data.flux && data.flux.length > 1) {
            var last = data.flux[data.flux.length - 1];
            sfi = last[1] || '--';
        }

        // Kp from kp_index
        if (data.kp_index && data.kp_index.length > 1) {
            var lastKp = data.kp_index[data.kp_index.length - 1];
            kp = lastKp[1] || '--';
        }

        // A-index from band_conditions
        if (data.band_conditions && data.band_conditions.aindex) {
            aIndex = data.band_conditions.aindex;
        }

        // Sunspot number
        if (data.band_conditions && data.band_conditions.sunspots) {
            ssn = data.band_conditions.sunspots;
        }

        // Solar wind speed — last non-null entry
        if (data.solar_wind_plasma && data.solar_wind_plasma.length > 1) {
            for (var i = data.solar_wind_plasma.length - 1; i >= 1; i--) {
                if (data.solar_wind_plasma[i][2]) {
                    wind = Math.round(parseFloat(data.solar_wind_plasma[i][2]));
                    break;
                }
            }
        }

        // IMF Bz — last non-null entry
        if (data.solar_wind_mag && data.solar_wind_mag.length > 1) {
            for (var j = data.solar_wind_mag.length - 1; j >= 1; j--) {
                if (data.solar_wind_mag[j][3]) {
                    bz = parseFloat(data.solar_wind_mag[j][3]).toFixed(1);
                    break;
                }
            }
        }

        _renderGauges(kp, wind, bz);

        _setText('swStripSfi', sfi);
        _setText('swStripKp', kp);
        _setText('swStripA', aIndex);
        _setText('swStripSsn', ssn);
        _setText('swStripWind', wind !== '--' ? wind + ' km/s' : '--');
        _setText('swStripBz', bz !== '--' ? bz + ' nT' : '--');

        // Color Kp by severity
        var kpEl = document.getElementById('swStripKp');
        if (kpEl) {
            var kpNum = parseFloat(kp);
            kpEl.className = 'sw-header-value';
            if (kpNum >= 7) kpEl.classList.add('accent-red');
            else if (kpNum >= 5) kpEl.classList.add('accent-orange');
            else if (kpNum >= 4) kpEl.classList.add('accent-yellow');
            else kpEl.classList.add('accent-green');
        }

        // Color Bz — negative is bad
        var bzEl = document.getElementById('swStripBz');
        if (bzEl) {
            var bzNum = parseFloat(bz);
            bzEl.className = 'sw-header-value';
            if (bzNum < -10) bzEl.classList.add('accent-red');
            else if (bzNum < -5) bzEl.classList.add('accent-orange');
            else if (bzNum < 0) bzEl.classList.add('accent-yellow');
            else bzEl.classList.add('accent-green');
        }
    }

    // -------------------------------------------------------------------
    // NOAA Scales
    // -------------------------------------------------------------------

    function _renderScales(data) {
        if (!data.scales) return;
        var s = data.scales;
        // Structure: { "0": { R: {Scale, Text}, S: {Scale, Text}, G: {Scale, Text} }, ... }
        // Key "0" = current conditions
        var current = s['0'];
        if (!current) return;

        var scaleMap = {
            'G': { el: 'swScaleG', label: 'Geomagnetic Storms' },
            'S': { el: 'swScaleS', label: 'Solar Radiation' },
            'R': { el: 'swScaleR', label: 'Radio Blackouts' }
        };
        ['G', 'S', 'R'].forEach(function (k) {
            var info = scaleMap[k];
            var scaleData = current[k];
            var val = '0', text = info.label;
            if (scaleData) {
                val = String(scaleData.Scale || '0').replace(/[^0-9]/g, '') || '0';
                if (scaleData.Text && scaleData.Text !== 'none') {
                    text = scaleData.Text;
                }
            }
            var el = document.getElementById(info.el);
            if (el) {
                el.querySelector('.sw-scale-value').textContent = k + val;
                el.querySelector('.sw-scale-value').className = 'sw-scale-value sw-scale-' + val;
                var descEl = el.querySelector('.sw-scale-desc');
                if (descEl) descEl.textContent = text;
            }
        });
    }

    // -------------------------------------------------------------------
    // Band conditions
    // -------------------------------------------------------------------

    function _renderBandConditions(data) {
        var grid = document.getElementById('swBandGrid');
        if (!grid) return;
        if (!data.band_conditions || !data.band_conditions.bands || data.band_conditions.bands.length === 0) {
            grid.innerHTML = '<div class="sw-empty" style="grid-column:1/-1">No band data available</div>';
            return;
        }
        // Group by band name, collect day/night
        var bands = {};
        data.band_conditions.bands.forEach(function (b) {
            if (!bands[b.name]) bands[b.name] = {};
            bands[b.name][b.time.toLowerCase()] = b.condition;
        });

        var html = '<div class="sw-band-header">Band</div><div class="sw-band-header" style="text-align:center">Day</div><div class="sw-band-header" style="text-align:center">Night</div>';
        Object.keys(bands).forEach(function (name) {
            html += '<div class="sw-band-name">' + name + '</div>';
            ['day', 'night'].forEach(function (t) {
                var cond = bands[name][t] || '--';
                var cls = 'sw-band-cond';
                var cl = cond.toLowerCase();
                if (cl === 'good') cls += ' sw-band-good';
                else if (cl === 'fair') cls += ' sw-band-fair';
                else if (cl === 'poor') cls += ' sw-band-poor';
                html += '<div class="' + cls + '">' + cond + '</div>';
            });
        });
        grid.innerHTML = html;
    }

    // -------------------------------------------------------------------
    // Kp bar chart
    // -------------------------------------------------------------------

    function _renderKpChart(data) {
        var canvas = document.getElementById('swKpChart');
        if (!canvas) return;
        if (!data.kp_index || data.kp_index.length < 2) return;

        var rows = data.kp_index.slice(1); // skip header
        var labels = [];
        var values = [];
        var colors = [];

        // Take last 24 entries
        var subset = rows.slice(-24);
        subset.forEach(function (r) {
            var dt = r[0] || '';
            labels.push(dt.slice(5, 16)); // MM-DD HH:MM
            var v = parseFloat(r[1]) || 0;
            values.push(v);
            if (v >= 7) colors.push('#ff3366');
            else if (v >= 5) colors.push('#ff8800');
            else if (v >= 4) colors.push('#ffcc00');
            else colors.push('#00ff88');
        });

        if (_kpChart) { _kpChart.destroy(); _kpChart = null; }
        _kpChart = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: colors,
                    borderWidth: 0,
                    barPercentage: 0.8
                }]
            },
            options: _chartOpts('Kp', 0, 9, false)
        });
    }

    // -------------------------------------------------------------------
    // Solar wind chart
    // -------------------------------------------------------------------

    function _renderWindChart(data) {
        var canvas = document.getElementById('swWindChart');
        if (!canvas) return;
        if (!data.solar_wind_plasma || data.solar_wind_plasma.length < 2) return;

        var rows = data.solar_wind_plasma.slice(1);
        var labels = [];
        var speedData = [];
        var densityData = [];

        // Sample every 3rd point to avoid overcrowding
        for (var i = 0; i < rows.length; i += 3) {
            var r = rows[i];
            labels.push(r[0] ? r[0].slice(11, 16) : '');
            speedData.push(r[2] ? parseFloat(r[2]) : null);
            densityData.push(r[1] ? parseFloat(r[1]) : null);
        }

        if (_windChart) { _windChart.destroy(); _windChart = null; }
        const _accentCyan = getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00ccff';
        _windChart = new Chart(canvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Speed (km/s)',
                        data: speedData,
                        borderColor: _accentCyan,
                        backgroundColor: _accentCyan + '22',
                        borderWidth: 1.5,
                        pointRadius: 0,
                        fill: true,
                        tension: 0.3,
                        yAxisID: 'y'
                    },
                    {
                        label: 'Density (p/cm³)',
                        data: densityData,
                        borderColor: '#ff8800',
                        borderWidth: 1,
                        pointRadius: 0,
                        borderDash: [4, 2],
                        tension: 0.3,
                        yAxisID: 'y1'
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: true, position: 'top', labels: { color: '#888', font: { size: 10 }, boxWidth: 12, padding: 8 } }
                },
                scales: {
                    x: { display: true, ticks: { color: '#555', font: { size: 9 }, maxTicksLimit: 8 }, grid: { color: '#ffffff08' } },
                    y: { display: true, position: 'left', ticks: { color: _accentCyan, font: { size: 9 } }, grid: { color: '#ffffff08' }, title: { display: false } },
                    y1: { display: true, position: 'right', ticks: { color: '#ff8800', font: { size: 9 } }, grid: { drawOnChartArea: false } }
                },
                interaction: { mode: 'index', intersect: false }
            }
        });
    }

    // -------------------------------------------------------------------
    // X-ray flux chart
    // -------------------------------------------------------------------

    function _renderXrayChart(data) {
        var canvas = document.getElementById('swXrayChart');
        if (!canvas) return;
        if (!data.xrays || data.xrays.length < 2) return;

        // New format: array of objects with time_tag, flux, energy
        // Filter to short-wavelength (0.1-0.8nm) only
        var filtered = data.xrays.filter(function (r) {
            return r.energy && r.energy === '0.1-0.8nm';
        });
        if (filtered.length === 0) filtered = data.xrays;

        var labels = [];
        var values = [];

        // Sample every 3rd point
        for (var i = 0; i < filtered.length; i += 3) {
            var r = filtered[i];
            var tag = r.time_tag || '';
            labels.push(tag.slice(11, 16));
            values.push(r.flux ? parseFloat(r.flux) : null);
        }

        if (_xrayChart) { _xrayChart.destroy(); _xrayChart = null; }
        // M and X flares cause HF radio blackouts: tint their bands
        var flareBands = {
            id: 'flareBands',
            beforeDatasetsDraw: function (chart) {
                var y = chart.scales.y, area = chart.chartArea, ctx = chart.ctx;
                [[1e-5, 1e-4, 'rgba(255, 140, 0, 0.07)'], [1e-4, 1e-3, 'rgba(255, 51, 102, 0.10)']].forEach(function (band) {
                    var top = y.getPixelForValue(band[1]), bottom = y.getPixelForValue(band[0]);
                    ctx.save();
                    ctx.fillStyle = band[2];
                    ctx.fillRect(area.left, Math.max(area.top, top), area.right - area.left, Math.min(area.bottom, bottom) - Math.max(area.top, top));
                    ctx.restore();
                });
            }
        };

        _xrayChart = new Chart(canvas, {
            type: 'line',
            plugins: [flareBands],
            data: {
                labels: labels,
                datasets: [{
                    label: 'X-Ray Flux (W/m²)',
                    data: values,
                    borderColor: '#ff3366',
                    backgroundColor: '#ff336622',
                    borderWidth: 1.5,
                    pointRadius: 0,
                    fill: true,
                    tension: 0.3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    x: { display: true, ticks: { color: '#555', font: { size: 9 }, maxTicksLimit: 8 }, grid: { color: '#ffffff08' } },
                    y: {
                        display: true,
                        type: 'logarithmic',
                        min: 1e-9,
                        max: 1e-3,
                        // One tick per flare class, at the flux where it starts;
                        // a log axis otherwise ticks 2e-7, 3e-7, ... all labelled B
                        afterBuildTicks: function (axis) {
                            axis.ticks = [1e-8, 1e-7, 1e-6, 1e-5, 1e-4].map(function (v) { return { value: v }; });
                        },
                        ticks: {
                            color: '#888',
                            font: { size: 10, weight: 'bold' },
                            callback: function (v) {
                                return { 1e-8: 'A', 1e-7: 'B', 1e-6: 'C', 1e-5: 'M', 1e-4: 'X' }[v] || '';
                            }
                        },
                        grid: { color: '#ffffff14' }
                    }
                }
            }
        });
    }

    // -------------------------------------------------------------------
    // Gauges: Kp, solar wind speed, Bz
    // -------------------------------------------------------------------

    function _arcPoint(deg, r) {
        var a = deg * Math.PI / 180;
        return [60 + Math.sin(a) * r, 58 - Math.cos(a) * r];
    }

    function _arc(fromDeg, toDeg, r) {
        var p0 = _arcPoint(fromDeg, r), p1 = _arcPoint(toDeg, r);
        return 'M' + p0[0].toFixed(2) + ',' + p0[1].toFixed(2) + ' A' + r + ',' + r + ' 0 '
            + (toDeg - fromDeg > 180 ? 1 : 0) + ',1 ' + p1[0].toFixed(2) + ',' + p1[1].toFixed(2);
    }

    /** An arc gauge: value within [min, max], a colour and a one-line status. */
    function _arcGauge(el, title, value, min, max, text, unit, colour, status) {
        if (!el) return;
        var span = 240, start = -span / 2;
        var known = typeof value === 'number' && isFinite(value);
        var f = known ? Math.max(0, Math.min(1, (value - min) / (max - min))) : 0;
        el.innerHTML = '<div class="sw-gauge-title">' + title + '</div>'
            + '<svg viewBox="0 0 120 84" class="sw-gauge-svg">'
            + '<path d="' + _arc(start, -start, 46) + '" class="sw-gauge-track"/>'
            + (known ? '<path d="' + _arc(start, start + Math.max(0.5, f * span), 46) + '" class="sw-gauge-fill" style="stroke:' + colour + '"/>' : '')
            + '<text x="60" y="62" class="sw-gauge-value" style="fill:' + (known ? colour : 'var(--text-dim)') + '">' + text + '</text>'
            + '<text x="60" y="76" class="sw-gauge-unit">' + unit + '</text>'
            + '</svg>'
            + '<div class="sw-gauge-status" style="color:' + (known ? colour : 'var(--text-dim)') + '">' + status + '</div>';
    }

    /** Bz: a bar either side of zero. South (negative) lets the solar wind couple into Earth's field. */
    function _bzGauge(el, bz) {
        if (!el) return;
        var known = typeof bz === 'number' && isFinite(bz);
        var limit = 20;
        var f = known ? Math.max(-1, Math.min(1, bz / limit)) : 0;
        var colour = !known ? 'var(--text-dim)' : bz <= -5 ? 'var(--neon-red)' : bz < 0 ? 'var(--neon-orange)' : 'var(--neon-green)';
        var status = !known ? 'No data' : bz <= -5 ? 'Southward: storms more likely' : bz < 0 ? 'Slightly southward' : 'Northward: quiet';
        var left = f < 0 ? 50 + f * 50 : 50, width = Math.abs(f) * 50;
        el.innerHTML = '<div class="sw-gauge-title">IMF Bz</div>'
            + '<div class="sw-bz-value" style="color:' + colour + '">' + (known ? bz.toFixed(1) : '--') + '<span> nT</span></div>'
            + '<div class="sw-bz-bar"><div class="sw-bz-zero"></div>'
            + '<div class="sw-bz-fill" style="left:' + left + '%;width:' + width + '%;background:' + colour + '"></div></div>'
            + '<div class="sw-bz-scale"><span>S ' + limit + '</span><span>0</span><span>N ' + limit + '</span></div>'
            + '<div class="sw-gauge-status" style="color:' + colour + '">' + status + '</div>';
    }

    function _renderGauges(kp, wind, bz) {
        var kpNum = parseFloat(kp);
        var kpKnown = isFinite(kpNum);
        var kpColour = !kpKnown ? '' : kpNum >= 7 ? 'var(--neon-red)' : kpNum >= 5 ? 'var(--neon-orange)'
            : kpNum >= 4 ? 'var(--neon-yellow)' : 'var(--neon-green)';
        var kpStatus = !kpKnown ? 'No data' : kpNum >= 5 ? 'G' + Math.min(5, Math.floor(kpNum) - 4) + ' geomagnetic storm'
            : kpNum >= 4 ? 'Unsettled' : 'Quiet';
        _arcGauge(document.getElementById('swGaugeKp'), 'Planetary Kp', kpKnown ? kpNum : null, 0, 9,
            kpKnown ? kpNum.toFixed(kpNum % 1 ? 2 : 0) : '--', 'of 9', kpColour, kpStatus);

        var windNum = parseFloat(wind);
        var windKnown = isFinite(windNum);
        var windColour = !windKnown ? '' : windNum >= 700 ? 'var(--neon-red)' : windNum >= 500 ? 'var(--neon-orange)'
            : windNum >= 400 ? 'var(--neon-yellow)' : 'var(--neon-green)';
        var windStatus = !windKnown ? 'No data' : windNum >= 700 ? 'Very fast' : windNum >= 500 ? 'Fast'
            : windNum >= 400 ? 'Moderate' : 'Slow';
        _arcGauge(document.getElementById('swGaugeWind'), 'Solar wind', windKnown ? windNum : null, 250, 900,
            windKnown ? String(Math.round(windNum)) : '--', 'km/s', windColour, windStatus);

        _bzGauge(document.getElementById('swGaugeBz'), bz === '--' ? null : parseFloat(bz));
    }

    // -------------------------------------------------------------------
    // Flare probability
    // -------------------------------------------------------------------

    function _renderFlareProb(data) {
        var el = document.getElementById('swFlareProb');
        if (!el) return;
        if (!data.flare_probability || data.flare_probability.length === 0) {
            el.innerHTML = '<div class="sw-empty">No flare data</div>';
            return;
        }
        // New format: array of objects with date, c_class_1_day, m_class_1_day, x_class_1_day, etc.
        var latest = data.flare_probability.slice(-3);
        var html = '<table class="sw-prob-table"><thead><tr>';
        html += '<th>Date</th><th>C 1-day</th><th>M 1-day</th><th>X 1-day</th><th>Proton</th>';
        html += '</tr></thead><tbody>';
        latest.forEach(function (row) {
            html += '<tr>';
            html += '<td>' + _escHtml(row.date ? String(row.date).slice(0, 10) : '--') + '</td>';
            html += '<td>' + _escHtml(row.c_class_1_day || '--') + '%</td>';
            html += '<td>' + _escHtml(row.m_class_1_day || '--') + '%</td>';
            html += '<td>' + _escHtml(row.x_class_1_day || '--') + '%</td>';
            html += '<td>' + _escHtml(row['10mev_protons_1_day'] || '--') + '%</td>';
            html += '</tr>';
        });
        html += '</tbody></table>';
        el.innerHTML = html;
    }

    // -------------------------------------------------------------------
    // Images
    // -------------------------------------------------------------------

    function _renderSolarImage() {
        selectSolarImage(_solarImageKey);
    }

    function _renderDrapImage() {
        selectDrapFreq(_drapFreq);
    }

    function _renderAuroraImage() {
        var frame = document.getElementById('swAuroraFrame');
        if (!frame) return;
        var img = new Image();
        img.onload = function () { frame.innerHTML = ''; frame.appendChild(img); };
        img.onerror = function () { frame.innerHTML = '<div class="sw-empty">Failed to load aurora image</div>'; };
        img.src = '/space-weather/image/aurora_north?t=' + Date.now();
        img.alt = 'Aurora Forecast';
    }

    function _updateSolarImageTabs() {
        document.querySelectorAll('.sw-solar-tab').forEach(function (btn) {
            btn.classList.toggle('active', btn.dataset.key === _solarImageKey);
        });
    }

    function _updateDrapTabs() {
        document.querySelectorAll('.sw-drap-freq-btn').forEach(function (btn) {
            btn.classList.toggle('active', btn.dataset.key === _drapFreq);
        });
    }

    // -------------------------------------------------------------------
    // Alerts
    // -------------------------------------------------------------------

    function _renderAlerts(data) {
        var el = document.getElementById('swAlertsList');
        if (!el) return;
        if (!data.alerts || data.alerts.length === 0) {
            el.innerHTML = '<div class="sw-empty">No active alerts</div>';
            return;
        }
        var html = '';
        // Show latest 10
        var items = data.alerts.slice(0, 10);
        items.forEach(function (a) {
            var msg = a.message || a.product_text || '';
            // Truncate long messages
            if (msg.length > 300) msg = msg.substring(0, 300) + '...';
            html += '<div class="sw-alert-item">';
            html += '<div class="sw-alert-type">' + _escHtml(a.product_id || 'Alert') + '</div>';
            html += '<div class="sw-alert-time">' + _escHtml(a.issue_datetime || '') + '</div>';
            html += '<div class="sw-alert-msg">' + _escHtml(msg) + '</div>';
            html += '</div>';
        });
        el.innerHTML = html;
    }

    // -------------------------------------------------------------------
    // Active regions
    // -------------------------------------------------------------------

    function _renderRegions(data) {
        var el = document.getElementById('swRegionsBody');
        if (!el) return;
        if (!data.solar_regions || data.solar_regions.length === 0) {
            el.innerHTML = '<tr><td colspan="5" class="sw-empty">No active regions</td></tr>';
            return;
        }
        // New format: array of objects with region, observed_date, location, longitude, area, etc.
        // De-duplicate by region number (keep latest observed_date per region)
        var byRegion = {};
        data.solar_regions.forEach(function (r) {
            var key = r.region || '';
            if (!byRegion[key] || (r.observed_date > byRegion[key].observed_date)) {
                byRegion[key] = r;
            }
        });
        var regions = Object.values(byRegion);
        var html = '';
        regions.forEach(function (r) {
            html += '<tr>';
            html += '<td>' + _escHtml(String(r.region || '')) + '</td>';
            html += '<td>' + _escHtml(r.observed_date || '') + '</td>';
            html += '<td>' + _escHtml(r.location || '') + '</td>';
            html += '<td>' + _escHtml(String(r.longitude || '')) + '</td>';
            html += '<td>' + _escHtml(String(r.area || '')) + '</td>';
            html += '</tr>';
        });
        el.innerHTML = html;
    }

    // -------------------------------------------------------------------
    // Sidebar quick status
    // -------------------------------------------------------------------

    function _updateSidebar(data) {
        var sfi = '--', kp = '--', aIdx = '--', ssn = '--', wind = '--', bz = '--';

        if (data.band_conditions) {
            if (data.band_conditions.sfi) sfi = data.band_conditions.sfi;
            if (data.band_conditions.aindex) aIdx = data.band_conditions.aindex;
            if (data.band_conditions.sunspots) ssn = data.band_conditions.sunspots;
        }
        if (data.kp_index && data.kp_index.length > 1) {
            kp = data.kp_index[data.kp_index.length - 1][1] || '--';
        }
        if (data.solar_wind_plasma && data.solar_wind_plasma.length > 1) {
            for (var i = data.solar_wind_plasma.length - 1; i >= 1; i--) {
                if (data.solar_wind_plasma[i][2]) {
                    wind = Math.round(parseFloat(data.solar_wind_plasma[i][2])) + ' km/s';
                    break;
                }
            }
        }
        if (data.solar_wind_mag && data.solar_wind_mag.length > 1) {
            for (var j = data.solar_wind_mag.length - 1; j >= 1; j--) {
                if (data.solar_wind_mag[j][3]) {
                    bz = parseFloat(data.solar_wind_mag[j][3]).toFixed(1) + ' nT';
                    break;
                }
            }
        }

        _setText('swSidebarSfi', sfi);
        _setText('swSidebarKp', kp);
        _setText('swSidebarA', aIdx);
        _setText('swSidebarSsn', ssn);
        _setText('swSidebarWind', wind);
        _setText('swSidebarBz', bz);
    }

    // -------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------

    function _setText(id, text) {
        var el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    function _escHtml(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    function _updateTimestamp() {
        var el = document.getElementById('swLastUpdate');
        if (el) el.textContent = 'Updated: ' + new Date().toLocaleTimeString();
    }

    function _chartOpts(yLabel, yMin, yMax, showLegend) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: !!showLegend, labels: { color: '#888', font: { size: 10 } } }
            },
            scales: {
                x: { display: true, ticks: { color: '#555', font: { size: 9 }, maxRotation: 45, maxTicksLimit: 8 }, grid: { color: '#ffffff08' } },
                y: { display: true, min: yMin, max: yMax, ticks: { color: '#888', font: { size: 9 }, stepSize: 1 }, grid: { color: '#ffffff08' } }
            }
        };
    }

    function _destroyCharts() {
        if (_kpChart) { _kpChart.destroy(); _kpChart = null; }
        if (_windChart) { _windChart.destroy(); _windChart = null; }
        if (_xrayChart) { _xrayChart.destroy(); _xrayChart = null; }
    }

    // -------------------------------------------------------------------
    // Expose public API
    // -------------------------------------------------------------------

    return {
        init: init,
        destroy: destroy,
        refresh: refresh,
        selectSolarImage: selectSolarImage,
        selectDrapFreq: selectDrapFreq,
        toggleAutoRefresh: toggleAutoRefresh
    };
})();
