(function DroneMode() {
    'use strict';

    let _sse = null;
    let _map = null;
    let _markers = {};
    let _trails = {};
    let _running = false;

    function init() {
        document.getElementById('droneStartBtn')?.addEventListener('click', _start);
        document.getElementById('droneStopBtn')?.addEventListener('click', _stop);
        _initMap();
        _connectSSE();
        _refreshStatus();
    }

    function _initMap() {
        if (_map) return;
        const mapEl = document.getElementById('droneMap');
        if (!mapEl || typeof L === 'undefined') return;
        _map = L.map('droneMap', { zoomControl: true }).setView([20, 0], 2);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap',
            maxZoom: 18,
        }).addTo(_map);
    }

    function destroy() {
        _disconnectSSE();
        if (_map) {
            _map.remove();
            _map = null;
        }
        _markers = {};
        _trails = {};
    }

    function _connectSSE() {
        if (_sse) return;
        _sse = new EventSource('/drone/stream');
        _sse.addEventListener('message', function (e) {
            try {
                const msg = JSON.parse(e.data);
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
        const listEl = document.getElementById('droneContactList');
        if (!listEl) return;
        let card = document.getElementById('drone-card-' + contact.id);
        if (!card) {
            card = document.createElement('div');
            card.id = 'drone-card-' + contact.id;
            card.className = 'drone-contact-card';
            card.addEventListener('click', function () { _focusContact(contact.id); });
            listEl.prepend(card);
        }
        card.className = 'drone-contact-card ' + contact.risk_level + '-risk';
        const complianceLabel = contact.compliant
            ? '<span class="drone-compliance-badge compliant">Remote ID</span>'
            : '<span class="drone-compliance-badge non-compliant">No Remote ID</span>';
        const vectors = (contact.detection_vectors || []).map(function (v) {
            return '<span class="drone-vector-pill active">' + v + '</span>';
        }).join('');
        const alt = contact.altitude_m != null ? contact.altitude_m.toFixed(0) + 'm' : '—';
        const spd = contact.speed_ms != null ? contact.speed_ms.toFixed(1) + 'm/s' : '—';
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
        const lat = contact.position[0];
        const lon = contact.position[1];
        if (_markers[contact.id]) {
            _markers[contact.id].setLatLng([lat, lon]);
        } else {
            const color = contact.risk_level === 'high' ? 'var(--accent-red)' :
                          contact.risk_level === 'medium' ? 'var(--accent-yellow)' :
                          'var(--accent-cyan)';
            const icon = L.divIcon({
                className: 'drone-map-icon' + (contact.risk_level === 'high' ? ' drone-marker-high-risk' : ''),
                html: '<div style="width:10px;height:10px;border-radius:50%;background:' + color + ';border:2px solid #fff;"></div>',
                iconSize: [10, 10],
                iconAnchor: [5, 5],
            });
            _markers[contact.id] = L.marker([lat, lon], { icon: icon })
                .addTo(_map)
                .bindPopup('<b>' + (contact.serial_number || contact.id) + '</b><br>Risk: ' + contact.risk_level);
        }
        const trailPoints = (contact.position_history || []).map(function (p) {
            return [p.lat, p.lon];
        });
        if (_trails[contact.id]) {
            _trails[contact.id].setLatLngs(trailPoints);
        } else if (trailPoints.length > 1) {
            _trails[contact.id] = L.polyline(trailPoints, {
                color: contact.risk_level === 'high' ? '#ff4444' : '#00ccff',
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
                const nonCompliant = contacts.filter(function (c) { return !c.compliant; }).length;
                const countEl = document.getElementById('droneContactCount');
                const ncEl = document.getElementById('droneNonCompliantCount');
                if (countEl) countEl.textContent = contacts.length;
                if (ncEl) ncEl.textContent = nonCompliant;
            })
            .catch(function () {});
    }

    function _refreshStatus() {
        fetch('/drone/status')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                _running = data.running;
                _setRunningUI(data.running);
                _updateVectorPills(data.vectors || []);
            })
            .catch(function () {});
    }

    function _start() {
        const iface = document.getElementById('droneWifiIface')?.value.trim() || null;
        fetch('/drone/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wifi_iface: iface }),
        })
        .then(function (r) { return r.json(); })
        .then(function () { _setRunningUI(true); _refreshStatus(); })
        .catch(function () {});
    }

    function _stop() {
        fetch('/drone/stop', { method: 'POST' })
            .then(function () { _setRunningUI(false); _refreshStatus(); })
            .catch(function () {});
    }

    function _setRunningUI(running) {
        const startBtn = document.getElementById('droneStartBtn');
        const stopBtn = document.getElementById('droneStopBtn');
        const statusEl = document.getElementById('droneStatusText');
        if (startBtn) startBtn.disabled = running;
        if (stopBtn) stopBtn.disabled = !running;
        if (statusEl) {
            statusEl.textContent = running ? 'Active' : 'Standby';
            statusEl.style.color = running ? 'var(--accent-green)' : 'var(--accent-yellow)';
        }
    }

    function _updateVectorPills(activeVectors) {
        const pillMap = {
            'REMOTE_ID': 'dronePillRemoteId',
            'RTL433': 'dronePill433',
            'HACKRF': 'dronePillHackrf',
        };
        Object.entries(pillMap).forEach(function ([key, id]) {
            const el = document.getElementById(id);
            if (el) el.classList.toggle('active', activeVectors.some(function (v) { return v.includes(key); }));
        });
    }

    window.DroneMode = { init: init, destroy: destroy };
})();
