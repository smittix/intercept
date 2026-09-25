/**
 * System Health – Enhanced Dashboard IIFE module
 *
 * Streams real-time system metrics via SSE with rich visualizations:
 * SVG arc gauge, per-core bars, temperature sparkline, network bandwidth,
 * disk I/O, 3D globe, weather, and process grid.
 */
const SystemHealth = (function () {
    'use strict';

    let eventSource = null;
    let connected = false;
    let lastMetrics = null;

    // Sparkline ring buffers: temperature, CPU and RAM (one reading per 3 s update)
    const SPARKLINE_SIZE = 20;
    const HISTORY_SIZE = 100;
    let tempHistory = [];
    let cpuHistory = [];
    let ramHistory = [];

    // Network I/O delta tracking
    let prevNetIo = null;
    let prevNetTimestamp = null;

    // Disk I/O delta tracking
    let prevDiskIo = null;
    let prevDiskTimestamp = null;

    // Location & weather state
    let locationData = null;
    let weatherData = null;
    let weatherTimer = null;
    let globeInstance = null;
    let globeDestroyed = false;

    const GLOBE_SCRIPT_URL = '/static/vendor/globe/globe.gl-2.33.1.min.js';  // bundled, so it works offline
    const GLOBE_TEXTURE_URL = '/static/images/globe/earth-dark.jpg';

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    function formatBytes(bytes) {
        if (bytes == null) return '--';
        var units = ['B', 'KB', 'MB', 'GB', 'TB'];
        var i = 0;
        var val = bytes;
        while (val >= 1024 && i < units.length - 1) { val /= 1024; i++; }
        return val.toFixed(1) + ' ' + units[i];
    }

    function formatRate(bytesPerSec) {
        if (bytesPerSec == null) return '--';
        return formatBytes(bytesPerSec) + '/s';
    }

    function barClass(pct) {
        if (pct >= 85) return 'crit';
        if (pct >= 60) return 'warn';
        return 'ok';
    }

    function barHtml(pct, label) {
        if (pct == null) return '<span class="sys-metric-na">N/A</span>';
        var cls = barClass(pct);
        var rounded = Math.round(pct);
        return '<div class="sys-metric-bar-wrap">' +
            (label ? '<span class="sys-metric-bar-label">' + label + '</span>' : '') +
            '<div class="sys-metric-bar"><div class="sys-metric-bar-fill ' + cls + '" style="width:' + rounded + '%"></div></div>' +
            '<span class="sys-metric-bar-value">' + rounded + '%</span>' +
            '</div>';
    }

    function escHtml(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    // -----------------------------------------------------------------------
    // SVG Arc Gauge
    // -----------------------------------------------------------------------

    function arcGaugeSvg(pct) {
        var radius = 36;
        var cx = 45, cy = 45;
        var startAngle = -225;
        var endAngle = 45;
        var totalAngle = endAngle - startAngle; // 270 degrees
        var fillAngle = startAngle + (totalAngle * Math.min(pct, 100) / 100);

        function polarToCart(angle) {
            var r = angle * Math.PI / 180;
            return { x: cx + radius * Math.cos(r), y: cy + radius * Math.sin(r) };
        }

        var bgStart = polarToCart(startAngle);
        var bgEnd = polarToCart(endAngle);
        var fillEnd = polarToCart(fillAngle);
        var largeArcBg = totalAngle > 180 ? 1 : 0;
        var fillArc = (fillAngle - startAngle) > 180 ? 1 : 0;
        var cls = barClass(pct);

        return '<svg viewBox="0 0 90 90">' +
            '<path class="arc-bg" d="M ' + bgStart.x + ' ' + bgStart.y +
            ' A ' + radius + ' ' + radius + ' 0 ' + largeArcBg + ' 1 ' + bgEnd.x + ' ' + bgEnd.y + '"/>' +
            '<path class="arc-fill ' + cls + '" d="M ' + bgStart.x + ' ' + bgStart.y +
            ' A ' + radius + ' ' + radius + ' 0 ' + fillArc + ' 1 ' + fillEnd.x + ' ' + fillEnd.y + '"/>' +
            '</svg>';
    }

    // -----------------------------------------------------------------------
    // Temperature Sparkline
    // -----------------------------------------------------------------------

    function sparklineSvg(values, lo, hi) {
        if (!values || values.length < 2) return '';
        var w = 200, h = 40;
        var min = lo != null ? lo : Math.min.apply(null, values);
        var max = hi != null ? hi : Math.max.apply(null, values);
        var range = max - min || 1;
        var step = w / (values.length - 1);

        var points = values.map(function (v, i) {
            var x = Math.round(i * step);
            var y = Math.round(h - ((v - min) / range) * (h - 4) - 2);
            return x + ',' + y;
        });

        var areaPoints = points.join(' ') + ' ' + w + ',' + h + ' 0,' + h;

        return '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
            '<defs><linearGradient id="sparkGradient" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="var(--accent-cyan, #00d4ff)" stop-opacity="0.3"/>' +
            '<stop offset="100%" stop-color="var(--accent-cyan, #00d4ff)" stop-opacity="0.0"/>' +
            '</linearGradient></defs>' +
            '<polygon class="sys-sparkline-area" points="' + areaPoints + '"/>' +
            '<polyline class="sys-sparkline-line" points="' + points.join(' ') + '"/>' +
            '</svg>';
    }

    // -----------------------------------------------------------------------
    // Rendering — CPU Card
    // -----------------------------------------------------------------------

    function renderCpuCard(m) {
        var el = document.getElementById('sysCardCpu');
        if (!el) return;
        var cpu = m.cpu;
        if (!cpu) { el.innerHTML = '<div class="sys-card-body"><span class="sys-metric-na">psutil not available</span></div>'; return; }

        var pct = Math.round(cpu.percent);
        pushHistory(cpuHistory, cpu.percent);
        var coreHtml = '';
        if (cpu.per_core && cpu.per_core.length) {
            coreHtml = '<div class="sys-core-bars">';
            cpu.per_core.forEach(function (c) {
                var cls = barClass(c);
                var h = Math.max(3, Math.round(c / 100 * 48));
                coreHtml += '<div class="sys-core-bar"><div class="sys-core-bar-fill ' + cls +
                    '" style="height:' + h + 'px;background:var(--accent-' +
                    (cls === 'ok' ? 'green' : cls === 'warn' ? 'yellow' : 'red') +
                    ', #00ff88)"></div></div>';
            });
            coreHtml += '</div>';
        }

        var freqHtml = '';
        if (cpu.freq) {
            var freqGhz = (cpu.freq.current / 1000).toFixed(2);
            freqHtml = '<div class="sys-card-detail">Freq: ' + freqGhz + ' GHz</div>';
        }

        el.innerHTML =
            '<div class="sys-card-header">CPU</div>' +
            '<div class="sys-card-body">' +
            '<div class="sys-gauge-wrap">' +
            '<div class="sys-gauge-arc">' + arcGaugeSvg(pct) +
            '<div class="sys-gauge-label">' + pct + '%</div></div>' +
            '<div class="sys-gauge-details">' +
            '<div class="sys-card-detail">Load: ' + cpu.load_1 + ' / ' + cpu.load_5 + ' / ' + cpu.load_15 + '</div>' +
            '<div class="sys-card-detail">Cores: ' + cpu.count + '</div>' +
            freqHtml +
            '</div></div>' +
            coreHtml +
            historyHtml(cpuHistory, 'CPU') +
            '</div>';
    }

    function pushHistory(buffer, value) {
        buffer.push(value);
        if (buffer.length > HISTORY_SIZE) buffer.shift();
    }

    // A 0-100 % sparkline with its time span, for CPU and RAM
    function historyHtml(buffer, label) {
        if (buffer.length < 2) return '';
        var minutes = Math.max(1, Math.round(buffer.length * 3 / 60));
        return '<div class="sys-history">' +
            '<div class="sys-history-head"><span>' + label + ' history</span><span>last ' + minutes + ' min</span></div>' +
            '<div class="sys-sparkline-wrap">' + sparklineSvg(buffer, 0, 100) + '</div>' +
            '</div>';
    }

    function gaugeHtml(pct, label, detail) {
        var rounded = Math.round(pct);
        return '<div class="sys-mini-gauge">' +
            '<div class="sys-gauge-arc">' + arcGaugeSvg(rounded) +
            '<div class="sys-gauge-label">' + rounded + '%</div></div>' +
            '<div class="sys-mini-gauge-name">' + label + '</div>' +
            '<div class="sys-card-detail">' + detail + '</div>' +
            '</div>';
    }

    // Heat tint for a sensor tile: cool, normal, warm, hot
    function heatClass(c) {
        if (c >= 80) return 'hot';
        if (c >= 65) return 'warm';
        if (c >= 40) return 'normal';
        return 'cool';
    }

    // -----------------------------------------------------------------------
    // Memory Card
    // -----------------------------------------------------------------------

    function renderMemoryCard(m) {
        var el = document.getElementById('sysCardMemory');
        if (!el) return;
        var mem = m.memory;
        if (!mem) { el.innerHTML = '<div class="sys-card-body"><span class="sys-metric-na">N/A</span></div>'; return; }
        var swap = m.swap || {};
        pushHistory(ramHistory, mem.percent);
        el.innerHTML =
            '<div class="sys-card-header">Memory</div>' +
            '<div class="sys-card-body">' +
            '<div class="sys-mini-gauges">' +
            gaugeHtml(mem.percent, 'RAM', formatBytes(mem.used) + ' / ' + formatBytes(mem.total)) +
            (swap.total > 0 ? gaugeHtml(swap.percent, 'Swap', formatBytes(swap.used) + ' / ' + formatBytes(swap.total)) : '') +
            '</div>' +
            historyHtml(ramHistory, 'RAM') +
            '</div>';
    }

    // -----------------------------------------------------------------------
    // Temperature & Power Card
    // -----------------------------------------------------------------------

    function _extractPrimaryTemp(temps) {
        if (!temps) return null;
        var preferred = ['cpu_thermal', 'coretemp', 'k10temp', 'acpitz', 'soc_thermal'];
        for (var i = 0; i < preferred.length; i++) {
            if (temps[preferred[i]] && temps[preferred[i]].length) return temps[preferred[i]][0];
        }
        for (var key in temps) {
            if (temps[key] && temps[key].length) return temps[key][0];
        }
        return null;
    }

    function renderTempCard(m) {
        var el = document.getElementById('sysCardTemp');
        if (!el) return;

        var temp = _extractPrimaryTemp(m.temperatures);
        var html = '<div class="sys-card-header">Temperature &amp; Power</div><div class="sys-card-body">';

        if (temp) {
            // Update sparkline history
            tempHistory.push(temp.current);
            if (tempHistory.length > SPARKLINE_SIZE) tempHistory.shift();

            html += '<div class="sys-temp-big">' + Math.round(temp.current) + '&deg;C' +
                (temp.label ? '<span class="sys-temp-source">' + escHtml(temp.label) + '</span>' : '') + '</div>';
            html += '<div class="sys-sparkline-wrap">' + sparklineSvg(tempHistory) + '</div>';

            // Every sensor as a tile tinted by its heat
            if (m.temperatures) {
                html += '<div class="sys-heat-grid">';
                for (var chip in m.temperatures) {
                    m.temperatures[chip].forEach(function (s) {
                        var name = s.label && s.label !== chip ? s.label : chip;
                        html += '<div class="sys-heat-tile ' + heatClass(s.current) + '" title="' + escHtml(chip + ' ' + (s.label || '')) + '">' +
                            '<span class="sys-heat-name">' + escHtml(name) + '</span>' +
                            '<span class="sys-heat-value">' + Math.round(s.current) + '&deg;</span></div>';
                    });
                }
                html += '</div>';
            }
        } else {
            html += '<span class="sys-metric-na">No temperature sensors</span>';
        }

        // Fans and battery, as chips
        var chips = [];
        if (m.fans) {
            for (var fChip in m.fans) {
                m.fans[fChip].forEach(function (f) {
                    chips.push('Fan ' + escHtml(f.label || fChip) + ' <b>' + f.current + ' RPM</b>');
                });
            }
        }
        if (m.battery) {
            chips.push('Battery <b>' + Math.round(m.battery.percent) + '%</b>' + (m.battery.plugged ? ' plugged in' : ''));
        }
        if (chips.length) {
            html += '<div class="sys-chips">' + chips.map(function (c) { return '<span class="sys-chip">' + c + '</span>'; }).join('') + '</div>';
        }

        // Throttle flags (Pi)
        if (m.power && m.power.throttled) {
            html += '<div class="sys-card-detail" style="color:var(--accent-yellow,#ffcc00)">Throttle: 0x' + m.power.throttled + '</div>';
        }

        // Power draw
        if (m.power && m.power.draw_watts != null) {
            html += '<div class="sys-card-detail">Power: ' + m.power.draw_watts + ' W</div>';
        }

        html += '</div>';
        el.innerHTML = html;
    }

    // -----------------------------------------------------------------------
    // Disk Card
    // -----------------------------------------------------------------------

    function renderDiskCard(m) {
        var el = document.getElementById('sysCardDisk');
        if (!el) return;
        var disk = m.disk;
        if (!disk) { el.innerHTML = '<div class="sys-card-header">Disk &amp; Storage</div><div class="sys-card-body"><span class="sys-metric-na">N/A</span></div>'; return; }

        var html = '<div class="sys-card-header">Disk &amp; Storage</div><div class="sys-card-body">';
        html += barHtml(disk.percent, '');
        html += '<div class="sys-card-detail">' + formatBytes(disk.used) + ' / ' + formatBytes(disk.total) + '</div>';

        // Disk I/O rates
        if (m.disk_io && prevDiskIo && prevDiskTimestamp) {
            var dt = (m.timestamp - prevDiskTimestamp);
            if (dt > 0) {
                var readRate = (m.disk_io.read_bytes - prevDiskIo.read_bytes) / dt;
                var writeRate = (m.disk_io.write_bytes - prevDiskIo.write_bytes) / dt;
                var readIops = Math.round((m.disk_io.read_count - prevDiskIo.read_count) / dt);
                var writeIops = Math.round((m.disk_io.write_count - prevDiskIo.write_count) / dt);
                html += '<div class="sys-disk-io">' +
                    '<span class="sys-disk-io-read">R: ' + formatRate(Math.max(0, readRate)) + '</span>' +
                    '<span class="sys-disk-io-write">W: ' + formatRate(Math.max(0, writeRate)) + '</span>' +
                    '</div>';
                html += '<div class="sys-card-detail">IOPS: ' + Math.max(0, readIops) + 'r / ' + Math.max(0, writeIops) + 'w</div>';
            }
        }

        if (m.disk_io) {
            prevDiskIo = m.disk_io;
            prevDiskTimestamp = m.timestamp;
        }

        html += '</div>';
        el.innerHTML = html;
    }

    // -----------------------------------------------------------------------
    // Network Card
    // -----------------------------------------------------------------------

    function renderNetworkCard(m) {
        var el = document.getElementById('sysCardNetwork');
        if (!el) return;
        var net = m.network;
        if (!net) { el.innerHTML = '<div class="sys-card-header">Network</div><div class="sys-card-body"><span class="sys-metric-na">N/A</span></div>'; return; }

        var html = '<div class="sys-card-header">Network</div><div class="sys-card-body">';

        // Interfaces
        var ifaces = net.interfaces || [];
        if (ifaces.length === 0) {
            html += '<span class="sys-metric-na">No interfaces</span>';
        } else {
            ifaces.forEach(function (iface) {
                html += '<div class="sys-net-iface">';
                html += '<div class="sys-net-iface-name">' + escHtml(iface.name) +
                    (iface.is_up ? '' : ' <span style="color:var(--text-dim)">(down)</span>') + '</div>';
                if (iface.ipv4) html += '<div class="sys-net-iface-ip">' + escHtml(iface.ipv4) + '</div>';
                var details = [];
                if (iface.mac) details.push('MAC: ' + iface.mac);
                if (iface.speed) details.push(iface.speed + ' Mbps');
                if (details.length) html += '<div class="sys-net-iface-detail">' + escHtml(details.join(' | ')) + '</div>';

                // Bandwidth for this interface
                if (net.io && net.io[iface.name] && prevNetIo && prevNetIo[iface.name] && prevNetTimestamp) {
                    var dt = (m.timestamp - prevNetTimestamp);
                    if (dt > 0) {
                        var prev = prevNetIo[iface.name];
                        var cur = net.io[iface.name];
                        var upRate = (cur.bytes_sent - prev.bytes_sent) / dt;
                        var downRate = (cur.bytes_recv - prev.bytes_recv) / dt;
                        html += '<div class="sys-bandwidth">' +
                            '<span class="sys-bw-up">&uarr; ' + formatRate(Math.max(0, upRate)) + '</span>' +
                            '<span class="sys-bw-down">&darr; ' + formatRate(Math.max(0, downRate)) + '</span>' +
                            '</div>';
                    }
                }

                html += '</div>';
            });
        }

        // Connection count
        if (net.connections != null) {
            html += '<div class="sys-card-detail" style="margin-top:8px">Connections: ' + net.connections + '</div>';
        }

        // Save for next delta
        if (net.io) {
            prevNetIo = net.io;
            prevNetTimestamp = m.timestamp;
        }

        html += '</div>';
        el.innerHTML = html;
    }

    // -----------------------------------------------------------------------
    // Location & Weather Card
    // -----------------------------------------------------------------------

    function renderLocationCard() {
        var el = document.getElementById('sysCardLocation');
        if (!el) return;

        // Preserve the globe DOM node if it already has a canvas
        var existingGlobe = document.getElementById('sysGlobeContainer');
        var savedGlobe = null;
        if (existingGlobe && existingGlobe.querySelector('canvas')) {
            savedGlobe = existingGlobe;
            existingGlobe.parentNode.removeChild(existingGlobe);
        }

        var html = '<div class="sys-card-header">Location &amp; Weather</div><div class="sys-card-body">';
        html += '<div class="sys-location-inner">';

        // Globe placeholder (will be replaced with saved node or initialized fresh)
        if (!savedGlobe) {
            html += '<div class="sys-globe-wrap" id="sysGlobeContainer"></div>';
        } else {
            html += '<div id="sysGlobePlaceholder"></div>';
        }

        // Details below globe
        html += '<div class="sys-location-details">';

        if (locationData && locationData.lat != null) {
            html += '<div class="sys-location-coords">' +
                locationData.lat.toFixed(4) + '&deg;' + (locationData.lat >= 0 ? 'N' : 'S') + ', ' +
                locationData.lon.toFixed(4) + '&deg;' + (locationData.lon >= 0 ? 'E' : 'W') + '</div>';

            // GPS status indicator
            if (locationData.source === 'gps' && locationData.gps) {
                var gps = locationData.gps;
                var fixLabel = gps.fix_quality === 3 ? '3D Fix' : '2D Fix';
                var dotCls = gps.fix_quality === 3 ? 'fix-3d' : 'fix-2d';
                html += '<div class="sys-gps-status">' +
                    '<span class="sys-gps-dot ' + dotCls + '"></span> ' + fixLabel;
                if (gps.satellites != null) html += ' &middot; ' + gps.satellites + ' sats';
                if (gps.accuracy != null) html += ' &middot; &plusmn;' + gps.accuracy + 'm';
                html += '</div>';
            } else {
                html += '<div class="sys-location-source">Source: ' + escHtml(locationData.source || 'unknown') + '</div>';
            }
        } else {
            html += '<div class="sys-location-coords" style="color:var(--text-dim)">No location</div>';
        }

        // Weather
        if (weatherData && !weatherData.error) {
            html += '<div class="sys-weather">';
            html += '<div class="sys-weather-temp">' + (weatherData.temp_c || '--') + '&deg;C</div>';
            html += '<div class="sys-weather-condition">' + escHtml(weatherData.condition || '') + '</div>';
            var details = [];
            if (weatherData.humidity) details.push('Humidity: ' + weatherData.humidity + '%');
            if (weatherData.wind_mph) details.push('Wind: ' + weatherData.wind_mph + ' mph ' + (weatherData.wind_dir || ''));
            if (weatherData.feels_like_c) details.push('Feels like: ' + weatherData.feels_like_c + '°C');
            details.forEach(function (d) {
                html += '<div class="sys-weather-detail">' + escHtml(d) + '</div>';
            });
            html += '</div>';
        } else if (weatherData && weatherData.error) {
            html += '<div class="sys-weather"><div class="sys-weather-condition" style="color:var(--text-dim)">Weather unavailable</div></div>';
        }

        html += '</div>'; // .sys-location-details
        html += '</div>'; // .sys-location-inner
        html += '</div>';
        el.innerHTML = html;

        // Re-insert saved globe or initialize fresh
        if (savedGlobe) {
            var placeholder = document.getElementById('sysGlobePlaceholder');
            if (placeholder) placeholder.parentNode.replaceChild(savedGlobe, placeholder);
        } else {
            requestAnimationFrame(function () { initGlobe(); });
        }
    }

    // -----------------------------------------------------------------------
    // Globe (reuses globe.gl from GPS mode)
    // -----------------------------------------------------------------------

    function ensureGlobeLibrary() {
        return new Promise(function (resolve, reject) {
            if (typeof window.Globe === 'function') { resolve(true); return; }

            // Check if script already exists
            var existing = document.querySelector(
                'script[data-intercept-globe-src="' + GLOBE_SCRIPT_URL + '"], ' +
                'script[src="' + GLOBE_SCRIPT_URL + '"]'
            );
            if (existing) {
                if (existing.dataset.loaded === 'true') { resolve(true); return; }
                if (existing.dataset.failed === 'true') { resolve(false); return; }
                existing.addEventListener('load', function () { resolve(true); }, { once: true });
                existing.addEventListener('error', function () { resolve(false); }, { once: true });
                return;
            }

            var script = document.createElement('script');
            script.src = GLOBE_SCRIPT_URL;
            script.async = true;
            script.crossOrigin = 'anonymous';
            script.dataset.interceptGlobeSrc = GLOBE_SCRIPT_URL;
            script.onload = function () { script.dataset.loaded = 'true'; resolve(true); };
            script.onerror = function () { script.dataset.failed = 'true'; resolve(false); };
            document.head.appendChild(script);
        });
    }

    function initGlobe() {
        var container = document.getElementById('sysGlobeContainer');
        if (!container || globeDestroyed) return;

        // Don't reinitialize if globe canvas is still alive in this container
        if (globeInstance && container.querySelector('canvas')) return;

        // Clear stale reference if canvas was destroyed by innerHTML replacement
        if (globeInstance && !container.querySelector('canvas')) {
            globeInstance = null;
        }

        ensureGlobeLibrary().then(function (ready) {
            if (!ready || typeof window.Globe !== 'function' || globeDestroyed) return;

            // Wait for layout — container may have 0 dimensions right after
            // display:none is removed by switchMode(). Use RAF retry like GPS mode.
            var attempts = 0;
            function tryInit() {
                if (globeDestroyed) return;
                container = document.getElementById('sysGlobeContainer');
                if (!container) return;

                if ((!container.clientWidth || !container.clientHeight) && attempts < 8) {
                    attempts++;
                    requestAnimationFrame(tryInit);
                    return;
                }
                if (!container.clientWidth || !container.clientHeight) return;

                container.innerHTML = '';
                container.style.background = 'radial-gradient(circle, rgba(10,20,40,0.9), rgba(2,4,8,0.98) 70%)';

                try {
                    const accentColor = getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00d4ff';
                    globeInstance = window.Globe()(container)
                        .backgroundColor('rgba(0,0,0,0)')
                        .globeImageUrl(GLOBE_TEXTURE_URL)
                        .showAtmosphere(true)
                        .atmosphereColor(accentColor)
                        .atmosphereAltitude(0.12)
                        .pointsData([])
                        .pointRadius(0.8)
                        .pointAltitude(0.01)
                        .pointColor(function () { return accentColor; });

                    var controls = globeInstance.controls();
                    if (controls) {
                        controls.autoRotate = true;
                        controls.autoRotateSpeed = 0.5;
                        controls.enablePan = false;
                        controls.minDistance = 120;
                        controls.maxDistance = 300;
                    }

                    // Size the globe
                    globeInstance.width(container.clientWidth);
                    globeInstance.height(container.clientHeight);

                    updateGlobePosition();
                } catch (e) {
                    // Globe.gl / WebGL init failed — show static fallback
                    container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:var(--text-dim);font-size:11px;">Globe unavailable</div>';
                }
            }
            requestAnimationFrame(tryInit);
        });
    }

    function updateGlobePosition() {
        if (!globeInstance || !locationData || locationData.lat == null) return;

        // Observer point
        globeInstance.pointsData([{
            lat: locationData.lat,
            lng: locationData.lon,
            size: 0.8,
            color: getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00d4ff',
        }]);

        // Snap view
        globeInstance.pointOfView({ lat: locationData.lat, lng: locationData.lon, altitude: 2.0 }, 1000);

        // Stop auto-rotate when we have a fix
        var controls = globeInstance.controls();
        if (controls) controls.autoRotate = false;
    }

    function destroyGlobe() {
        globeDestroyed = true;
        if (globeInstance) {
            var container = document.getElementById('sysGlobeContainer');
            if (container) container.innerHTML = '';
            globeInstance = null;
        }
    }

    // -----------------------------------------------------------------------
    // SDR Card
    // -----------------------------------------------------------------------

    function renderSdrCard(devices) {
        var el = document.getElementById('sysCardSdr');
        if (!el) return;
        var html = '<div class="sys-card-header">SDR Devices <button class="sys-rescan-btn" onclick="SystemHealth.refreshSdr()">Rescan</button></div>';
        html += '<div class="sys-card-body">';
        if (!devices || !devices.length) {
            html += '<span class="sys-metric-na">No devices found</span>';
        } else {
            devices.forEach(function (d) {
                html += '<div class="sys-sdr-device">' +
                    '<span class="sys-process-dot running"></span> ' +
                    '<strong>' + escHtml(d.type) + ' #' + d.index + '</strong>' +
                    '<div class="sys-card-detail">' + escHtml(d.name || 'Unknown') + '</div>' +
                    (d.serial ? '<div class="sys-card-detail">S/N: ' + escHtml(d.serial) + '</div>' : '') +
                    '</div>';
            });
        }
        html += '</div>';
        el.innerHTML = html;
    }

    // -----------------------------------------------------------------------
    // Process Card
    // -----------------------------------------------------------------------

    function renderProcessCard(m) {
        var el = document.getElementById('sysCardProcesses');
        if (!el) return;
        var procs = m.processes || {};
        var keys = Object.keys(procs).sort();
        var html = '<div class="sys-card-header">Active Processes</div><div class="sys-card-body">';
        if (!keys.length) {
            html += '<span class="sys-metric-na">No data</span>';
        } else {
            var running = 0, stopped = 0;
            html += '<div class="sys-process-grid">';
            keys.forEach(function (k) {
                var isRunning = procs[k];
                if (isRunning) running++; else stopped++;
                var dotCls = isRunning ? 'running' : 'stopped';
                var label = k.charAt(0).toUpperCase() + k.slice(1);
                html += '<div class="sys-process-item">' +
                    '<span class="sys-process-dot ' + dotCls + '"></span> ' +
                    '<span class="sys-process-name">' + escHtml(label) + '</span>' +
                    '</div>';
            });
            html += '</div>';
            html += '<div class="sys-process-summary">' + running + ' running / ' + stopped + ' idle</div>';
        }
        html += '</div>';
        el.innerHTML = html;
    }

    // -----------------------------------------------------------------------
    // System Info Card
    // -----------------------------------------------------------------------

    function renderSystemInfoCard(m) {
        var el = document.getElementById('sysCardInfo');
        if (!el) return;
        var sys = m.system || {};
        var html = '<div class="sys-card-header">System Info</div><div class="sys-card-body"><div class="sys-info-grid">';

        html += '<div class="sys-info-item"><strong>Host</strong><span>' + escHtml(sys.hostname || '--') + '</span></div>';
        html += '<div class="sys-info-item"><strong>OS</strong><span>' + escHtml((sys.platform || '--').replace(/-with-glibc[\d.]+/, '')) + '</span></div>';
        html += '<div class="sys-info-item"><strong>Python</strong><span>' + escHtml(sys.python || '--') + '</span></div>';
        html += '<div class="sys-info-item"><strong>App</strong><span>v' + escHtml(sys.version || '--') + '</span></div>';
        html += '<div class="sys-info-item"><strong>Uptime</strong><span>' + escHtml(sys.uptime_human || '--') + '</span></div>';

        if (m.boot_time) {
            var bootDate = new Date(m.boot_time * 1000);
            html += '<div class="sys-info-item"><strong>Boot</strong><span>' + escHtml(bootDate.toLocaleString()) + '</span></div>';
        }

        if (m.network && m.network.connections != null) {
            html += '<div class="sys-info-item"><strong>Connections</strong><span>' + m.network.connections + '</span></div>';
        }

        html += '</div></div>';
        el.innerHTML = html;
    }

    // -----------------------------------------------------------------------
    // Sidebar Updates
    // -----------------------------------------------------------------------

    function updateSidebarQuickStats(m) {
        var cpuEl = document.getElementById('sysQuickCpu');
        var tempEl = document.getElementById('sysQuickTemp');
        var ramEl = document.getElementById('sysQuickRam');
        var diskEl = document.getElementById('sysQuickDisk');

        if (cpuEl) cpuEl.textContent = m.cpu ? Math.round(m.cpu.percent) + '%' : '--';
        if (ramEl) ramEl.textContent = m.memory ? Math.round(m.memory.percent) + '%' : '--';
        if (diskEl) diskEl.textContent = m.disk ? Math.round(m.disk.percent) + '%' : '--';

        var temp = _extractPrimaryTemp(m.temperatures);
        if (tempEl) tempEl.innerHTML = temp ? Math.round(temp.current) + '&deg;C' : '--';

        // Color-code values
        [cpuEl, ramEl, diskEl].forEach(function (el) {
            if (!el) return;
            var val = parseInt(el.textContent);
            el.classList.remove('sys-val-ok', 'sys-val-warn', 'sys-val-crit');
            if (!isNaN(val)) el.classList.add('sys-val-' + barClass(val));
        });
    }

    function updateSidebarProcesses(m) {
        var el = document.getElementById('sysProcessList');
        if (!el) return;
        var procs = m.processes || {};
        var keys = Object.keys(procs).sort();
        if (!keys.length) { el.textContent = 'No data'; return; }
        var running = keys.filter(function (k) { return procs[k]; });
        var stopped = keys.filter(function (k) { return !procs[k]; });
        el.innerHTML =
            (running.length ? '<span style="color: var(--accent-green, #00ff88);">' + running.length + ' running</span>' : '') +
            (running.length && stopped.length ? ' &middot; ' : '') +
            (stopped.length ? '<span style="color: var(--text-dim);">' + stopped.length + ' stopped</span>' : '');
    }

    function updateSidebarNetwork(m) {
        var el = document.getElementById('sysQuickNet');
        if (!el || !m.network) return;
        var ifaces = m.network.interfaces || [];
        var ips = [];
        ifaces.forEach(function (iface) {
            if (iface.ipv4 && iface.is_up) {
                ips.push(iface.name + ': ' + iface.ipv4);
            }
        });
        el.textContent = ips.length ? ips.join(', ') : '--';
    }

    function updateSidebarBattery(m) {
        var section = document.getElementById('sysQuickBatterySection');
        var el = document.getElementById('sysQuickBattery');
        if (!section || !el) return;
        if (m.battery) {
            section.style.display = '';
            el.textContent = Math.round(m.battery.percent) + '%' + (m.battery.plugged ? ' (plugged)' : '');
        } else {
            section.style.display = 'none';
        }
    }

    function updateSidebarLocation() {
        var el = document.getElementById('sysQuickLocation');
        if (!el) return;
        if (locationData && locationData.lat != null) {
            el.textContent = locationData.lat.toFixed(4) + ', ' + locationData.lon.toFixed(4) + ' (' + locationData.source + ')';
        } else {
            el.textContent = 'No location';
        }
    }

    // -----------------------------------------------------------------------
    // Render all
    // -----------------------------------------------------------------------

    function renderAll(m) {
        renderCpuCard(m);
        renderMemoryCard(m);
        renderTempCard(m);
        renderDiskCard(m);
        renderNetworkCard(m);
        renderProcessCard(m);
        renderSystemInfoCard(m);
        updateSidebarQuickStats(m);
        updateSidebarProcesses(m);
        updateSidebarNetwork(m);
        updateSidebarBattery(m);
    }

    // -----------------------------------------------------------------------
    // Location & Weather Fetching
    // -----------------------------------------------------------------------

    function fetchLocation() {
        fetch('/system/location')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                // If server only has default/none, check client-side saved location
                if ((data.source === 'default' || data.source === 'none') &&
                    window.ObserverLocation && ObserverLocation.getShared) {
                    var shared = ObserverLocation.getShared();
                    if (shared && shared.lat && shared.lon) {
                        data.lat = shared.lat;
                        data.lon = shared.lon;
                        data.source = 'manual';
                    }
                }
                locationData = data;
                updateSidebarLocation();
                renderLocationCard();
                if (data.lat != null) fetchWeather();
            })
            .catch(function () {
                renderLocationCard();
            });
    }

    function fetchWeather() {
        if (!locationData || locationData.lat == null) return;
        fetch('/system/weather?lat=' + locationData.lat + '&lon=' + locationData.lon)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                weatherData = data;
                renderLocationCard();
            })
            .catch(function () {});
    }

    // -----------------------------------------------------------------------
    // SSE Connection
    // -----------------------------------------------------------------------

    function connect() {
        if (eventSource) return;
        eventSource = new EventSource('/system/stream');
        eventSource.onmessage = function (e) {
            try {
                var data = JSON.parse(e.data);
                if (data.type === 'keepalive') return;
                lastMetrics = data;
                renderAll(data);
            } catch (_) { /* ignore parse errors */ }
        };
        eventSource.onopen = function () {
            connected = true;
        };
        eventSource.onerror = function () {
            connected = false;
        };
    }

    function disconnect() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
        connected = false;
    }

    // -----------------------------------------------------------------------
    // SDR Devices
    // -----------------------------------------------------------------------

    function refreshSdr() {
        var sidebarEl = document.getElementById('sysSdrList');
        if (sidebarEl) sidebarEl.innerHTML = 'Scanning&hellip;';

        var cardEl = document.getElementById('sysCardSdr');
        if (cardEl) cardEl.innerHTML = '<div class="sys-card-header">SDR Devices</div><div class="sys-card-body">Scanning&hellip;</div>';

        fetch('/system/sdr_devices')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var devices = data.devices || [];
                renderSdrCard(devices);
                // Update sidebar
                if (sidebarEl) {
                    if (!devices.length) {
                        sidebarEl.innerHTML = '<span style="color: var(--text-dim);">No SDR devices found</span>';
                    } else {
                        var html = '';
                        devices.forEach(function (d) {
                            html += '<div style="margin-bottom: 4px;"><span class="sys-process-dot running"></span> ' +
                                escHtml(d.type) + ' #' + d.index + ' &mdash; ' + escHtml(d.name || 'Unknown') + '</div>';
                        });
                        sidebarEl.innerHTML = html;
                    }
                }
            })
            .catch(function () {
                if (sidebarEl) sidebarEl.innerHTML = '<span style="color: var(--accent-red, #ff3366);">Detection failed</span>';
                renderSdrCard([]);
            });
    }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    function init() {
        globeDestroyed = false;
        connect();
        refreshSdr();
        fetchLocation();

        // Refresh weather every 10 minutes
        weatherTimer = setInterval(function () {
            fetchWeather();
        }, 600000);
    }

    function destroy() {
        disconnect();
        destroyGlobe();
        if (weatherTimer) {
            clearInterval(weatherTimer);
            weatherTimer = null;
        }
    }

    return {
        init: init,
        destroy: destroy,
        refreshSdr: refreshSdr,
    };
})();
