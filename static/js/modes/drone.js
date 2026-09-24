var DroneMode = (function () {
    'use strict';

    var _sse = null;
    var _map = null;
    var _markers = {};
    var _trails = {};
    var _initialized = false;

    function init() {
        if (_initialized) {
            _refreshStatus();
            return;
        }
        _initialized = true;
        document.getElementById('droneStartBtn')?.addEventListener('click', _start);
        document.getElementById('droneStopBtn')?.addEventListener('click', _stop);
        _initMap();
        _connectSSE();
        _refreshStatus();
    }

    function destroy() {
        _disconnectSSE();
        if (_map) {
            if (typeof Settings !== 'undefined' && Settings.unregisterMap) Settings.unregisterMap(_map);
            _map.remove();
            _map = null;
        }
        _markers = {};
        _trails = {};
        _initialized = false;
    }

    function invalidateMap() {
        if (_map) _map.invalidateSize();
    }

    function _initMap() {
        if (_map) return;
        var mapEl = document.getElementById('droneMainMap');
        if (!mapEl || typeof L === 'undefined') return;
        _map = L.map('droneMainMap', { zoomControl: true }).setView([20, 0], 2);

        var hasSettings = typeof Settings !== 'undefined' && Settings.createTileLayer;
        if (hasSettings && Settings._initialized) {
            // Settings already loaded elsewhere this session — use it directly so a
            // configured CARTO API key is applied immediately (no unkeyed flash).
            Settings.createTileLayer().addTo(_map);
            Settings.registerMap(_map);
        } else {
            var fallbackTiles = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
                maxZoom: 19,
            }).addTo(_map);

            if (hasSettings) {
                Promise.race([
                    Settings.init(),
                    new Promise(function (_, reject) { setTimeout(function () { reject(new Error('Settings timeout')); }, 5000); })
                ]).then(function () {
                    _map.removeLayer(fallbackTiles);
                    Settings.createTileLayer().addTo(_map);
                    Settings.registerMap(_map);
                }).catch(function (e) {
                    console.warn('Drone: Settings init failed/timed out, using fallback tiles:', e);
                });
            }
        }

        if (typeof MapUtils !== 'undefined') MapUtils.addGraticuleControl(_map);
    }

    function _connectSSE() {
        if (_sse) return;
        _sse = new EventSource('/drone/stream');
        _sse.addEventListener('message', function (e) {
            try {
                var msg = JSON.parse(e.data);
                if (msg.type === 'contact') _handleContact(msg.data);
            } catch (_) {}
        });
        _sse.onerror = function () {
            _sse.close();
            _sse = null;
            setTimeout(_connectSSE, 3000);
        };
    }

    function _disconnectSSE() {
        if (_sse) { _sse.close(); _sse = null; }
    }

    function _handleContact(contact) {
        _upsertCard(contact);
        if (contact.position) _upsertMapMarker(contact);
        _updateStats();
    }

    function _upsertCard(contact) {
        var listEl = document.getElementById('droneContactList');
        var emptyEl = document.getElementById('droneContactEmpty');
        if (!listEl) return;
        if (emptyEl) emptyEl.style.display = 'none';
        var card = document.getElementById('drone-card-' + contact.id);
        if (!card) {
            card = document.createElement('div');
            card.id = 'drone-card-' + contact.id;
            card.className = 'drone-contact-card';
            card.addEventListener('click', function () { _focusContact(contact.id); });
            listEl.prepend(card);
        }
        card.className = 'drone-contact-card ' + contact.risk_level + '-risk';
        var complianceLabel = contact.compliant
            ? '<span class="drone-compliance-badge compliant">Remote ID</span>'
            : '<span class="drone-compliance-badge non-compliant">No Remote ID</span>';
        var vectors = (contact.detection_vectors || []).map(function (v) {
            return '<span class="drone-vector-pill active">' + v + '</span>';
        }).join('');
        var alt = contact.altitude_m != null ? contact.altitude_m.toFixed(0) + ' m' : '—';
        var spd = contact.speed_ms != null ? contact.speed_ms.toFixed(1) + ' m/s' : '—';
        card.innerHTML = [
            '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">',
            '  <span style="font-family:var(--font-mono); font-size:11px; color:var(--accent-cyan);">' + (contact.serial_number || contact.id) + '</span>',
            '  ' + complianceLabel,
            '</div>',
            '<div class="drone-vector-pills" style="margin-bottom:6px;">' + vectors + '</div>',
            '<div style="font-size:10px; color:var(--text-dim);">Alt: ' + alt + ' &nbsp; Speed: ' + spd + '</div>',
        ].join('');
    }

    function _upsertMapMarker(contact) {
        if (!_map) return;
        var lat = contact.position[0];
        var lon = contact.position[1];
        if (_markers[contact.id]) {
            _markers[contact.id].setLatLng([lat, lon]);
        } else {
            var color = contact.risk_level === 'high' ? 'var(--accent-red)' :
                        contact.risk_level === 'medium' ? 'var(--accent-yellow)' :
                        'var(--accent-cyan)';
            var icon = L.divIcon({
                className: 'drone-map-icon' + (contact.risk_level === 'high' ? ' drone-marker-high-risk' : ''),
                html: '<div style="width:10px;height:10px;border-radius:50%;background:' + color + ';border:2px solid #fff;"></div>',
                iconSize: [10, 10],
                iconAnchor: [5, 5],
            });
            _markers[contact.id] = L.marker([lat, lon], { icon: icon })
                .addTo(_map)
                .bindPopup('<b>' + (contact.serial_number || contact.id) + '</b><br>Risk: ' + contact.risk_level);
        }
        var trailPoints = (contact.position_history || []).map(function (p) {
            return [p.lat, p.lon];
        });
        if (_trails[contact.id]) {
            _trails[contact.id].setLatLngs(trailPoints);
        } else if (trailPoints.length > 1) {
            _trails[contact.id] = L.polyline(trailPoints, {
                color: contact.risk_level === 'high' ? '#ff4444' : (getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00ccff'),
                weight: 1.5,
                opacity: 0.6,
            }).addTo(_map);
        }
    }

    function _focusContact(contactId) {
        if (_map && _markers[contactId]) {
            _map.panTo(_markers[contactId].getLatLng());
            _markers[contactId].openPopup();
        }
    }

    function _updateStats() {
        fetch('/drone/contacts')
            .then(function (r) { return r.json(); })
            .then(function (contacts) {
                var nonCompliant = contacts.filter(function (c) { return !c.compliant; }).length;
                var highRisk = contacts.filter(function (c) { return c.risk_level === 'high'; }).length;
                var set = function (id, val) { var el = document.getElementById(id); if (el) el.textContent = val; };
                set('droneContactCount', contacts.length);
                set('droneNonCompliantCount', nonCompliant);
                set('droneVsContacts', contacts.length);
                set('droneVsNonCompliant', nonCompliant);
                set('droneVsHighRisk', highRisk);
            })
            .catch(function () {});
    }

    function _refreshStatus() {
        fetch('/drone/status')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                _setRunningUI(data.running);
                _updateVectorPills(data.vectors || []);
            })
            .catch(function () {});
    }

    function _start() {
        var ifaceVal = document.getElementById('droneWifiIface')?.value || '';
        var iface = ifaceVal || null;
        var rtlVal = document.getElementById('droneRtlIndex')?.value;
        var rtlIndex = rtlVal !== '' && rtlVal != null ? parseInt(rtlVal, 10) : 0;
        var useHackrf = document.getElementById('droneUseHackrf')?.checked ?? true;
        fetch('/drone/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wifi_iface: iface, rtl_sdr_index: rtlIndex, use_hackrf: useHackrf }),
        })
        .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
        .then(function (res) {
            var data = res.data || {};
            var unavailable = data.unavailable || [];
            if (!res.ok) {
                _showStartNote(data.message || data.error || 'Drone detection failed to start');
                return;
            }
            _showStartNote(unavailable.length ? 'Not running: ' + unavailable.join('; ') : '');
            _setRunningUI(true);
            _refreshStatus();
        })
        .catch(function (err) { _showStartNote('Drone detection failed to start: ' + err.message); });
    }

    /** Why a start failed, or which sources did not start; text only. */
    function _showStartNote(text) {
        var el = document.getElementById('droneDeviceWarnings');
        if (!el) return;
        el.textContent = text;
        el.style.display = text ? 'block' : 'none';
    }

    function _stop() {
        fetch('/drone/stop', { method: 'POST' })
            .then(function () { _setRunningUI(false); _refreshStatus(); })
            .catch(function () {});
    }

    function _setRunningUI(running) {
        var startBtn = document.getElementById('droneStartBtn');
        var stopBtn = document.getElementById('droneStopBtn');
        var statusEl = document.getElementById('droneStatusText');
        if (startBtn) startBtn.disabled = running;
        if (stopBtn) stopBtn.disabled = !running;
        if (statusEl) {
            statusEl.textContent = running ? 'Active' : 'Standby';
            statusEl.style.color = running ? 'var(--accent-green)' : 'var(--accent-yellow)';
        }
        // Sync global state for switchMode stop phase
        if (typeof isDroneRunning !== 'undefined') isDroneRunning = running;
    }

    function _updateVectorPills(activeVectors) {
        var pillMap = {
            'REMOTE_ID': 'dronePillRemoteId',
            'RTL433': 'dronePill433',
            'HACKRF': 'dronePillHackrf',
        };
        Object.keys(pillMap).forEach(function (key) {
            var el = document.getElementById(pillMap[key]);
            if (el) el.classList.toggle('active', activeVectors.some(function (v) { return v.includes(key); }));
        });
    }

    return { init: init, destroy: destroy, invalidateMap: invalidateMap };
})();
