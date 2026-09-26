/**
 * Satellite tracking, GPS (gpsd) and drone (Remote ID) functions for the main
 * page. Moved out of templates/index.html unchanged (only de-indented). A
 * classic script loaded right after the page's main inline script, so it
 * shares that script's top-level names and runs in the same order as before.
 */
// ============================================
// SATELLITE MODE FUNCTIONS
// ============================================

function getLocation() {
    if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
            position => {
                const lat = position.coords.latitude;
                const lon = position.coords.longitude;
                document.getElementById('obsLat').value = lat.toFixed(4);
                document.getElementById('obsLon').value = lon.toFixed(4);
                observerLocation.lat = lat;
                observerLocation.lon = lon;
                if (window.ObserverLocation && ObserverLocation.isSharedEnabled()) {
                    ObserverLocation.setShared({ lat, lon });
                }
                showInfo('Location updated!');
            },
            error => {
                alert('Could not get location: ' + error.message);
            }
        );
    } else {
        alert('Geolocation not supported by browser');
    }
}

// ============================================
// GPS FUNCTIONS (gpsd auto-connect)
// ============================================

function scheduleGpsAutoConnect(delayMs = 20000) {
    if (gpsConnected || gpsAutoConnectInFlight || gpsAutoConnectTimer) return;
    gpsAutoConnectTimer = setTimeout(() => {
        gpsAutoConnectTimer = null;
        autoConnectGps();
    }, delayMs);
}

async function autoConnectGps() {
    if (gpsConnected) return true;
    if (gpsAutoConnectTimer) {
        clearTimeout(gpsAutoConnectTimer);
        gpsAutoConnectTimer = null;
    }
    if (gpsAutoConnectInFlight) {
        return gpsAutoConnectInFlight;
    }

    gpsAutoConnectInFlight = (async () => {
        try {
            const response = await fetch('/gps/auto-connect', { method: 'POST' });
            const data = await response.json();

            if (data.status === 'connected') {
                gpsConnected = true;
                startGpsStream();
                showGpsIndicator(true);
                console.log('GPS: Auto-connected to gpsd');
                if (data.position) {
                    updateLocationFromGps(data.position);
                }
                return true;
            }

            console.log('GPS: gpsd not available -', data.message);
            return false;
        } catch (e) {
            console.log('GPS: Auto-connect failed -', e.message);
            return false;
        } finally {
            gpsAutoConnectInFlight = null;
        }
    })();

    return gpsAutoConnectInFlight;
}

let gpsReconnectTimeout = null;

// GPS subscriber callbacks - modules can register to receive GPS stream data
const gpsStreamSubscribers = [];

function addGpsStreamSubscriber(fn) {
    if (!gpsStreamSubscribers.includes(fn)) {
        gpsStreamSubscribers.push(fn);
    }
}

function removeGpsStreamSubscriber(fn) {
    const idx = gpsStreamSubscribers.indexOf(fn);
    if (idx !== -1) gpsStreamSubscribers.splice(idx, 1);
}

function startGpsStream() {
    if (gpsEventSource) {
        gpsEventSource.close();
    }
    if (gpsReconnectTimeout) {
        clearTimeout(gpsReconnectTimeout);
        gpsReconnectTimeout = null;
    }

    gpsEventSource = new EventSource('/gps/stream');
    gpsEventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'position') {
                gpsLastPosition = data;
                updateLocationFromGps(data);
            }
            // Dispatch to all subscribers (e.g. GPS mode UI)
            gpsStreamSubscribers.forEach(fn => fn(data));
        } catch (e) {
            console.error('GPS parse error:', e);
        }
    };
    gpsEventSource.onerror = (e) => {
        // Don't log every error - connection suspends are normal
        if (gpsEventSource) {
            gpsEventSource.close();
            gpsEventSource = null;
        }
        // Auto-reconnect after 5 seconds if still connected
        if (gpsConnected && !gpsReconnectTimeout) {
            gpsReconnectTimeout = setTimeout(() => {
                gpsReconnectTimeout = null;
                if (gpsConnected) {
                    startGpsStream();
                }
            }, 5000);
        }
    };
}

// Reconnect GPS stream when tab becomes visible
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && gpsConnected && !gpsEventSource) {
        startGpsStream();
    }
});

function updateLocationFromGps(position) {
    const lat = Number(position && position.latitude);
    const lon = Number(position && position.longitude);
    const fixQuality = Number(position && position.fix_quality);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        return;
    }
    if (Number.isFinite(fixQuality) && fixQuality < 2) return;

    // Update satellite observer location
    const satLatInput = document.getElementById('obsLat');
    const satLonInput = document.getElementById('obsLon');
    if (satLatInput) satLatInput.value = lat.toFixed(4);
    if (satLonInput) satLonInput.value = lon.toFixed(4);

    // Update observerLocation
    observerLocation.lat = lat;
    observerLocation.lon = lon;

    // Keep live GPS separate from the configured shared observer location.
    updateAprsUserLocation({ latitude: lat, longitude: lon });
}

function showGpsIndicator(show) {
    // Show/hide all GPS indicators (by class and by ID)
    document.querySelectorAll('.gps-indicator').forEach(el => {
        el.style.display = show ? 'inline-flex' : 'none';
    });
    // Also target specific IDs in case class selector doesn't work
    ['satGpsIndicator', 'aprsGpsIndicator'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = show ? 'inline-flex' : 'none';
    });
}

function initPolarPlot() {
    const canvas = document.getElementById('polarPlotCanvas');
    if (!canvas) return;
    const container = canvas.parentElement;
    const size = Math.min(container.offsetWidth, 400);
    canvas.width = size;
    canvas.height = size;
    drawPolarPlot();
}

function drawPolarPlot(pass = null) {
    const canvas = document.getElementById('polarPlotCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const size = canvas.width;
    const cx = size / 2;
    const cy = size / 2;
    const radius = size / 2 - 30;

    // Clear
    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(0, 0, size, size);

    // Draw elevation rings
    ctx.strokeStyle = 'rgba(0, 255, 255, 0.2)';
    ctx.lineWidth = 1;
    for (let el = 0; el <= 90; el += 30) {
        const r = radius * (90 - el) / 90;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();

        // Label
        if (el > 0) {
            ctx.fillStyle = '#444';
            ctx.font = '10px Roboto Condensed';
            ctx.textAlign = 'center';
            ctx.fillText(el + '°', cx, cy - r + 12);
        }
    }

    // Draw azimuth lines
    for (let az = 0; az < 360; az += 45) {
        const rad = az * Math.PI / 180;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.sin(rad) * radius, cy - Math.cos(rad) * radius);
        ctx.stroke();
    }

    // Draw cardinal directions
    ctx.fillStyle = '#00ffff';
    ctx.font = 'bold 14px Rajdhani';
    ctx.textAlign = 'center';
    ctx.fillText('N', cx, cy - radius - 8);
    ctx.fillStyle = '#888';
    ctx.fillText('S', cx, cy + radius + 16);
    ctx.fillText('E', cx + radius + 12, cy + 4);
    ctx.fillText('W', cx - radius - 12, cy + 4);

    // Draw zenith
    ctx.fillStyle = '#00ffff';
    ctx.beginPath();
    ctx.arc(cx, cy, 3, 0, Math.PI * 2);
    ctx.fill();

    // Draw selected pass trajectory
    if (pass && pass.trajectory) {
        ctx.strokeStyle = pass.color || '#00ff00';
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();

        pass.trajectory.forEach((point, i) => {
            // Backend returns 'el' and 'az' properties
            const el = point.el !== undefined ? point.el : point.elevation;
            const az = point.az !== undefined ? point.az : point.azimuth;
            const r = radius * (90 - el) / 90;
            const rad = az * Math.PI / 180;
            const x = cx + Math.sin(rad) * r;
            const y = cy - Math.cos(rad) * r;

            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.setLineDash([]);

        // Draw max elevation point
        const maxPoint = pass.trajectory.reduce((max, p) => {
            const pEl = p.el !== undefined ? p.el : p.elevation;
            const maxEl = max.el !== undefined ? max.el : max.elevation;
            return pEl > maxEl ? p : max;
        }, { el: 0, elevation: 0 });
        const maxEl = maxPoint.el !== undefined ? maxPoint.el : maxPoint.elevation;
        const maxAz = maxPoint.az !== undefined ? maxPoint.az : maxPoint.azimuth;
        const maxR = radius * (90 - maxEl) / 90;
        const maxRad = maxAz * Math.PI / 180;
        const maxX = cx + Math.sin(maxRad) * maxR;
        const maxY = cy - Math.cos(maxRad) * maxR;

        ctx.fillStyle = pass.color || '#00ff00';
        ctx.beginPath();
        ctx.arc(maxX, maxY, 6, 0, Math.PI * 2);
        ctx.fill();

        // Label
        ctx.fillStyle = '#fff';
        ctx.font = '11px Roboto Condensed';
        ctx.fillText(pass.satellite, maxX + 10, maxY - 5);
    }
}

// Satellite mode agent state
let satelliteCurrentAgent = null;

function calculatePasses() {
    const lat = parseFloat(document.getElementById('obsLat').value);
    const lon = parseFloat(document.getElementById('obsLon').value);
    const hours = parseInt(document.getElementById('predictionHours').value);
    const minEl = parseInt(document.getElementById('minElevation').value);

    const satellites = getSelectedSatellites();

    if (satellites.length === 0) {
        alert('Please select at least one satellite to track');
        return;
    }

    // Check if using agent mode
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
    satelliteCurrentAgent = isAgentMode ? currentAgent : null;

    // Determine endpoint based on agent mode
    const endpoint = isAgentMode
        ? `/controller/agents/${currentAgent}/satellite/predict`
        : '/satellite/predict';

    fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat, lon, hours, minEl, satellites })
    })
        .then(r => r.json())
        .then(data => {
            // Handle controller proxy response format
            const result = isAgentMode && data.result ? data.result : data;

            if (result.status === 'success') {
                satellitePasses = result.passes;
                renderPassList();
                document.getElementById('passCount').textContent = result.passes.length;
                if (result.passes.length > 0) {
                    selectPass(0);
                    document.getElementById('satelliteCountdown').style.display = 'block';
                    updateSatelliteCountdown();
                    startCountdownTimer();
                } else {
                    document.getElementById('satelliteCountdown').style.display = 'none';
                }
            } else {
                alert('Error: ' + (result.message || result.error || 'Failed to predict passes'));
            }
        });
}

function renderPassList() {
    const container = document.getElementById('passList');
    container.innerHTML = '';

    if (satellitePasses.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 30px;">No passes found for selected criteria.</div>';
        return;
    }

    document.getElementById('passListCount').textContent = satellitePasses.length + ' passes';

    satellitePasses.forEach((pass, index) => {
        const card = document.createElement('div');
        card.className = 'pass-card' + (index === 0 ? ' active' : '');
        card.onclick = () => selectPass(index);

        const quality = pass.maxEl >= 60 ? 'excellent' : pass.maxEl >= 30 ? 'good' : 'fair';

        card.innerHTML = `
            <div class="pass-satellite">${pass.satellite}</div>
            <div class="pass-time">${pass.startTime}</div>
            <div class="pass-details">
                <div>Max El: <span>${pass.maxEl}°</span></div>
                <div>Duration: <span>${pass.duration}m</span></div>
                <div class="pass-quality ${quality}">${quality.toUpperCase()}</div>
            </div>
        `;
        container.appendChild(card);
    });
}

function selectPass(index) {
    selectedPass = satellitePasses[index];
    selectedPassIndex = index;
    document.querySelectorAll('.pass-card').forEach((card, i) => {
        card.classList.toggle('active', i === index);
    });
    drawPolarPlot(selectedPass);
    updateGroundTrack(selectedPass);
    // Update countdown to show selected pass
    updateSatelliteCountdown();
    // Start real-time position updates for full orbit track
    startSatellitePositionUpdates();
    // Fetch position immediately
    updateRealTimePosition();
}

// Ground Track Map
let groundTrackMap = null;
let gpsMapOverlays = null;
let groundTrackLine = null;
let satMarker = null;
let observerMarker = null;
let satPositionInterval = null;

async function initGroundTrackMap() {
    const mapContainer = document.getElementById('groundTrackMap');
    if (!mapContainer || groundTrackMap) return;

    groundTrackMap = MapUtils.init('groundTrackMap', {
        center: [20, 0],
        zoom: 1,
        zoomControl: true,
        attributionControl: false,
    });
    if (!groundTrackMap) return;
    window.groundTrackMap = groundTrackMap;

    gpsMapOverlays = MapUtils.addTacticalOverlays(groundTrackMap, {
        scaleBar: true,
    });

    // Observer crosshair via MapUtils
    const obsLat = parseFloat(document.getElementById('obsLat')?.value) || 51.5;
    const obsLon = parseFloat(document.getElementById('obsLon')?.value) || -0.1;
    observerMarker = MapUtils._buildReticle([obsLat, obsLon]);
    observerMarker.addTo(groundTrackMap);
}

function updateGroundTrack(pass) {
    if (!groundTrackMap) initGroundTrackMap();
    if (!pass || !pass.groundTrack) return;

    // Remove old track and marker
    if (groundTrackLine) {
        groundTrackMap.removeLayer(groundTrackLine);
        groundTrackLine = null;
    }
    if (satMarker) {
        groundTrackMap.removeLayer(satMarker);
        satMarker = null;
    }
    if (orbitTrackLine) {
        groundTrackMap.removeLayer(orbitTrackLine);
        orbitTrackLine = null;
    }
    if (pastOrbitLine) {
        groundTrackMap.removeLayer(pastOrbitLine);
        pastOrbitLine = null;
    }

    // Split ground track only at true antimeridian crossings (±180° line)
    const segments = [];
    let currentSegment = [];
    for (let i = 0; i < pass.groundTrack.length; i++) {
        const p = pass.groundTrack[i];
        if (currentSegment.length > 0) {
            const prevLon = currentSegment[currentSegment.length - 1][1];
            // Only split when crossing the antimeridian (one side > 90, other < -90)
            const crossesAntimeridian = (prevLon > 90 && p.lon < -90) || (prevLon < -90 && p.lon > 90);
            if (crossesAntimeridian) {
                if (currentSegment.length >= 1) segments.push(currentSegment);
                currentSegment = [];
            }
        }
        currentSegment.push([p.lat, p.lon]);
    }
    if (currentSegment.length >= 1) segments.push(currentSegment);

    // Draw ground track segments
    groundTrackLine = L.layerGroup();
    const allCoords = [];
    segments.forEach(seg => {
        L.polyline(seg, {
            color: pass.color || '#00ff00',
            weight: 2,
            opacity: 0.8,
            dashArray: '5, 5'
        }).addTo(groundTrackLine);
        allCoords.push(...seg);
    });
    groundTrackLine.addTo(groundTrackMap);

    // Add current position marker
    if (pass.currentPosition) {
        satMarker = L.marker([pass.currentPosition.lat, pass.currentPosition.lon], {
            icon: L.divIcon({
                className: 'sat-marker',
                html: '<div style="background:#ffff00;width:12px;height:12px;border-radius:50%;border:2px solid #000;box-shadow:0 0 10px #ffff00;"></div>',
                iconSize: [12, 12],
                iconAnchor: [6, 6]
            })
        }).addTo(groundTrackMap).bindPopup(pass.satellite);
    }

    // Update observer marker position
    const lat = parseFloat(document.getElementById('obsLat').value) || 51.5;
    const lon = parseFloat(document.getElementById('obsLon').value) || -0.1;
    if (observerMarker) {
        observerMarker.setLatLng([lat, lon]);
    }

    // Fit bounds to show track
    if (allCoords.length > 0) {
        groundTrackMap.fitBounds(L.latLngBounds(allCoords), { padding: [20, 20] });
    }
}

function toggleGroundTrack() {
    const show = document.getElementById('showGroundTrack').checked;
    document.getElementById('groundTrackMap').style.display = show ? 'block' : 'none';
    if (show && groundTrackMap) {
        groundTrackMap.invalidateSize();
    }
}

function startSatellitePositionUpdates() {
    if (satPositionInterval) clearInterval(satPositionInterval);
    satPositionInterval = setInterval(() => {
        if (selectedPass) {
            updateRealTimePosition();
        }
    }, 5000);
}

function updateRealTimePosition() {
    let satellites = getSelectedSatellites();

    // Ensure selected pass's satellite is included in the request
    if (selectedPass && selectedPass.satellite) {
        if (!satellites.includes(selectedPass.satellite)) {
            satellites = [selectedPass.satellite, ...satellites];
        }
    }

    if (satellites.length === 0) return;

    const lat = parseFloat(document.getElementById('obsLat').value);
    const lon = parseFloat(document.getElementById('obsLon').value);

    // Check if using agent mode
    const isAgentMode = satelliteCurrentAgent !== null;
    const endpoint = isAgentMode
        ? `/controller/agents/${satelliteCurrentAgent}/satellite/position`
        : '/satellite/position';

    fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat, lon, satellites, includeTrack: true })
    })
        .then(r => r.json())
        .then(data => {
            // Handle controller proxy response format
            const result = isAgentMode && data.result ? data.result : data;

            if (result.status === 'success' && result.positions) {
                updateRealTimeIndicators(result.positions);
            }
        });
}

let orbitTrackLine = null;
let pastOrbitLine = null;

function updateRealTimeIndicators(positions) {
    // Update ground track map markers
    positions.forEach(pos => {
        if (selectedPass && pos.satellite === selectedPass.satellite) {
            // Update satellite marker position
            if (satMarker) {
                satMarker.setLatLng([pos.lat, pos.lon]);
                satMarker.setPopupContent(pos.satellite + '<br>Alt: ' + pos.altitude.toFixed(0) + ' km<br>El: ' + pos.elevation.toFixed(1) + '°');
            } else if (groundTrackMap) {
                satMarker = L.marker([pos.lat, pos.lon], {
                    icon: L.divIcon({
                        className: 'sat-marker',
                        html: '<div style="background:#ffff00;width:14px;height:14px;border-radius:50%;border:2px solid #000;box-shadow:0 0 15px #ffff00;animation:pulse-sat 1s infinite;"></div>',
                        iconSize: [14, 14],
                        iconAnchor: [7, 7]
                    })
                }).addTo(groundTrackMap).bindPopup(pos.satellite + '<br>Alt: ' + pos.altitude.toFixed(0) + ' km');
            }

            // Draw full orbit track from position endpoint
            // Backend returns 'track' property
            const orbitData = pos.track || pos.orbitTrack;
            if (orbitData && orbitData.length > 0 && groundTrackMap) {
                // Split into past and future, handling antimeridian crossings
                const pastPoints = orbitData.filter(p => p.past);
                const futurePoints = orbitData.filter(p => !p.past);

                // Helper to split coords only at true antimeridian crossings (±180° line)
                function splitAtAntimeridian(points) {
                    const segments = [];
                    let currentSegment = [];
                    for (let i = 0; i < points.length; i++) {
                        const p = points[i];
                        if (currentSegment.length > 0) {
                            const prevLon = currentSegment[currentSegment.length - 1][1];
                            // Only split when crossing the antimeridian (one side > 90, other < -90)
                            const crossesAntimeridian = (prevLon > 90 && p.lon < -90) || (prevLon < -90 && p.lon > 90);
                            if (crossesAntimeridian) {
                                if (currentSegment.length >= 1) segments.push(currentSegment);
                                currentSegment = [];
                            }
                        }
                        currentSegment.push([p.lat, p.lon]);
                    }
                    if (currentSegment.length >= 1) segments.push(currentSegment);
                    return segments;
                }

                // Remove old lines
                if (orbitTrackLine) groundTrackMap.removeLayer(orbitTrackLine);
                if (pastOrbitLine) groundTrackMap.removeLayer(pastOrbitLine);

                // Draw past track segments (dimmer)
                const pastSegments = splitAtAntimeridian(pastPoints);
                if (pastSegments.length > 0) {
                    pastOrbitLine = L.layerGroup();
                    pastSegments.forEach(seg => {
                        L.polyline(seg, {
                            color: '#666666',
                            weight: 2,
                            opacity: 0.5,
                            dashArray: '3, 6'
                        }).addTo(pastOrbitLine);
                    });
                    pastOrbitLine.addTo(groundTrackMap);
                }

                // Draw future track segments (brighter)
                const futureSegments = splitAtAntimeridian(futurePoints);
                if (futureSegments.length > 0) {
                    orbitTrackLine = L.layerGroup();
                    futureSegments.forEach(seg => {
                        L.polyline(seg, {
                            color: selectedPass.color || '#00ff00',
                            weight: 3,
                            opacity: 0.8
                        }).addTo(orbitTrackLine);
                    });
                    orbitTrackLine.addTo(groundTrackMap);
                }
            }

            // Update polar plot with pass trajectory and real-time position
            if (selectedPass) {
                drawPolarPlot(selectedPass);
                // Draw current position on top if satellite is visible
                if (pos.elevation > 0) {
                    drawRealTimePositionOnPolar(pos);
                }
            }
        }
    });
}

function drawRealTimePositionOnPolar(pos) {
    const canvas = document.getElementById('polarPlotCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const size = canvas.width;
    const cx = size / 2;
    const cy = size / 2;
    const radius = size / 2 - 30;

    // Draw pulsing indicator for current position
    const r = radius * (90 - pos.elevation) / 90;
    const rad = pos.azimuth * Math.PI / 180;
    const x = cx + Math.sin(rad) * r;
    const y = cy - Math.cos(rad) * r;

    ctx.fillStyle = '#ffff00';
    ctx.beginPath();
    ctx.arc(x, y, 8, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = '#ffff00';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 12, 0, Math.PI * 2);
    ctx.stroke();
}

function updateTLE() {
    fetch('/satellite/update-tle', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                showInfo('TLE data updated!');
            } else {
                alert('Error updating TLE: ' + data.message);
            }
        });
}

// Satellite management
let trackedSatellites = [];

function renderSatelliteList() {
    const list = document.getElementById('satTrackingList');
    if (!list) return;

    list.innerHTML = trackedSatellites.map((sat, idx) => `
        <div class="sat-item ${sat.builtin ? 'builtin' : ''}">
            <label>
                <input type="checkbox" ${sat.checked ? 'checked' : ''} onchange="toggleSatellite(${idx})">
                <span class="sat-name">${sat.name}</span>
                <span class="sat-norad">#${sat.norad}</span>
            </label>
            <button class="sat-remove" onclick="removeSatellite(${idx})" title="Remove">✕</button>
        </div>
    `).join('');
}

function toggleSatellite(idx) {
    const sat = trackedSatellites[idx];
    sat.checked = !sat.checked;
    fetch(`/satellite/tracked/${sat.norad}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: sat.checked })
    }).catch(() => {});
}

function removeSatellite(idx) {
    const sat = trackedSatellites[idx];
    if (sat.builtin) return;
    fetch(`/satellite/tracked/${sat.norad}`, { method: 'DELETE' })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                trackedSatellites.splice(idx, 1);
                renderSatelliteList();
            }
        })
        .catch(() => {});
}

function getSelectedSatellites() {
    return trackedSatellites.filter(s => s.checked).map(s => s.id);
}

function showAddSatelliteModal() {
    document.getElementById('satModal').classList.add('active');
}

function closeSatModal() {
    document.getElementById('satModal').classList.remove('active');
}

function switchSatModalTab(tab) {
    document.querySelectorAll('.sat-modal-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.sat-modal-section').forEach(s => s.classList.remove('active'));

    if (tab === 'tle') {
        document.querySelector('.sat-modal-tab:first-child').classList.add('active');
        document.getElementById('tleSection').classList.add('active');
    } else {
        document.querySelector('.sat-modal-tab:last-child').classList.add('active');
        document.getElementById('celestrakSection').classList.add('active');
    }
}

function addFromTLE() {
    const tleText = document.getElementById('tleInput').value.trim();
    if (!tleText) {
        alert('Please paste TLE data');
        return;
    }

    const lines = tleText.split(/\r?\n/).map(l => l.trim()).filter(l => l);
    const toAdd = [];

    for (let i = 0; i < lines.length; i += 3) {
        if (i + 2 < lines.length) {
            const name = lines[i];
            const line1 = lines[i + 1];
            const line2 = lines[i + 2];

            if (line1.startsWith('1 ') && line2.startsWith('2 ')) {
                const norad = line1.substring(2, 7).trim();
                if (!trackedSatellites.find(s => s.norad === norad)) {
                    toAdd.push({ norad_id: norad, name: name, tle1: line1, tle2: line2, enabled: true });
                }
            }
        }
    }

    if (toAdd.length === 0) {
        alert('No valid TLE data found. Format: Name, Line 1, Line 2 (3 lines per satellite)');
        return;
    }

    fetch('/satellite/tracked', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(toAdd)
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            _loadSatellitesFromAPI();
            document.getElementById('tleInput').value = '';
            closeSatModal();
            showInfo(`Added ${data.added} satellite(s)`);
        }
    })
    .catch(() => alert('Failed to save satellites'));
}

function fetchCelestrak() {
    showAddSatelliteModal();
    switchSatModalTab('celestrak');
}

function fetchCelestrakCategory(category) {
    const status = document.getElementById('celestrakStatus');
    status.innerHTML = '<span style="color: var(--accent-cyan);">Fetching ' + category + '...</span>';
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);

    fetch('/satellite/celestrak/' + category, { signal: controller.signal })
        .then(r => r.json())
        .then(async data => {
            clearTimeout(timeout);
            if (data.status === 'success' && data.satellites) {
                const toAdd = data.satellites
                    .filter(sat => !trackedSatellites.find(s => s.norad === String(sat.norad)))
                    .map(sat => ({
                        norad_id: String(sat.norad),
                        name: sat.name,
                        tle1: sat.tle1,
                        tle2: sat.tle2,
                        enabled: false
                    }));

                if (toAdd.length === 0) {
                    status.innerHTML = `<span style="color: var(--accent-green);">All ${data.satellites.length} satellites already tracked</span>`;
                    return;
                }

                const batchSize = 250;
                let addedTotal = 0;

                for (let i = 0; i < toAdd.length; i += batchSize) {
                    const batch = toAdd.slice(i, i + batchSize);
                    const completed = Math.min(i + batch.length, toAdd.length);
                    status.innerHTML = `<span style="color: var(--accent-cyan);">Importing ${completed}/${toAdd.length} from ${category}...</span>`;

                    const resp = await fetch('/satellite/tracked', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(batch)
                    });
                    const result = await resp.json().catch(() => ({}));

                    if (!resp.ok || result.status !== 'success') {
                        throw new Error(result.message || result.error || `HTTP ${resp.status}`);
                    }
                    addedTotal += Number(result.added || 0);
                }

                _loadSatellitesFromAPI();
                status.innerHTML = `<span style="color: var(--accent-green);">Added ${addedTotal} satellites (${data.satellites.length} total in category)</span>`;
            } else {
                status.innerHTML = `<span style="color: var(--accent-red);">Error: ${data.message || 'Failed to fetch'}</span>`;
            }
        })
        .catch((err) => {
            clearTimeout(timeout);
            const msg = err && err.message ? err.message : 'Network error';
            const label = err && err.name === 'AbortError' ? 'Request timed out' : msg;
            status.innerHTML = `<span style="color: var(--accent-red);">Import failed: ${label}</span>`;
        });
}

function _loadSatellitesFromAPI() {
    fetch('/satellite/tracked')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success' && data.satellites) {
                trackedSatellites = data.satellites.map(sat => ({
                    id: String(sat.norad_id),
                    name: sat.name,
                    norad: sat.norad_id,
                    builtin: sat.builtin,
                    checked: sat.enabled,
                    tle: sat.tle_line1 ? [sat.name, sat.tle_line1, sat.tle_line2] : null
                }));
                renderSatelliteList();
            }
        })
        .catch(() => {
            // Fallback to hardcoded defaults if API fails
            if (trackedSatellites.length === 0) {
                trackedSatellites = [
                    { id: '25544', name: 'ISS (ZARYA)', norad: '25544', builtin: true, checked: true },
                    { id: '57166', name: 'Meteor-M2-3', norad: '57166', builtin: true, checked: true },
                    { id: '59051', name: 'Meteor-M2-4', norad: '59051', builtin: true, checked: true }
                ];
                renderSatelliteList();
            }
        });
}

// Initialize satellite list when satellite mode is loaded
function initSatelliteList() {
    _loadSatellitesFromAPI();
}

// Utility function. This later declaration replaces the feed version above,
// so status messages ("Sensor stream connected...") are the app's toasts.
function showInfo(message) {
    if (window.AppFeedback) {
        AppFeedback.toast({ type: 'info', message: String(message), durationMs: 3500 });
    }
}

// Theme toggle functions
function toggleTheme() {
    const html = document.documentElement;
    const currentTheme = html.getAttribute('data-theme') || 'dark';
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';

    html.setAttribute('data-theme', newTheme);

    // Save to localStorage for instant load on next visit
    localStorage.setItem('intercept-theme', newTheme);

    // Persist to server for cross-device sync
    fetch('/settings/theme', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: newTheme })
    }).catch(err => console.warn('Failed to save theme to server:', err));
}

// Load saved theme on page load
(function () {
    // First apply localStorage theme for instant load (no flash)
    const localTheme = localStorage.getItem('intercept-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', localTheme);

    // Then fetch from server to sync (in case changed on another device)
    fetch('/settings/theme')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success' && data.value) {
                const serverTheme = data.value;
                if (serverTheme !== localTheme) {
                    // Server has different theme, apply it
                    document.documentElement.setAttribute('data-theme', serverTheme);
                    localStorage.setItem('intercept-theme', serverTheme);
                }
            }
        })
        .catch(() => { }); // Ignore errors, localStorage is fallback
})();

// Help modal functions
function showHelp() {
    document.getElementById('helpModal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function hideHelp() {
    document.getElementById('helpModal').classList.remove('active');
    document.body.style.overflow = '';
}

function switchHelpTab(tab) {
    document.querySelectorAll('.help-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.help-section').forEach(s => s.classList.remove('active'));
    document.querySelector(`.help-tab[data-tab="${tab}"]`).classList.add('active');
    document.getElementById(`help-${tab}`).classList.add('active');
}

// Keyboard shortcuts for help
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') hideHelp();
    // Open help with F1 or ? key (when not typing in an input)
    if ((e.key === 'F1' || (e.key === '?' && !e.target.matches('input, textarea, select'))) && !document.getElementById('helpModal').classList.contains('active')) {
        e.preventDefault();
        showHelp();
    }
});

// Scanner and receiver logic are handled by Waterfall mode.

// ============================================
// Drone Intelligence Functions
// ============================================
var isDroneRunning = false;

async function refreshDroneDevices() {
    try {
        const resp = await fetch('/drone/devices');
        const data = await resp.json();
        const devs = data.devices || {};

        const wifiSel = document.getElementById('droneWifiIface');
        if (wifiSel) {
            wifiSel.innerHTML = '';
            if (devs.wifi_interfaces && devs.wifi_interfaces.length > 0) {
                devs.wifi_interfaces.forEach(iface => {
                    const opt = document.createElement('option');
                    opt.value = iface.name;
                    opt.textContent = iface.display_name || iface.name;
                    wifiSel.appendChild(opt);
                });
            } else {
                wifiSel.innerHTML = '<option value="">No WiFi interfaces found</option>';
            }
        }

        const sdrSel = document.getElementById('droneRtlIndex');
        if (sdrSel) {
            sdrSel.innerHTML = '';
            if (devs.sdr_devices && devs.sdr_devices.length > 0) {
                devs.sdr_devices.forEach(dev => {
                    const opt = document.createElement('option');
                    opt.value = dev.index !== undefined ? dev.index : 0;
                    opt.textContent = dev.display_name || dev.name || 'SDR Device';
                    sdrSel.appendChild(opt);
                });
            } else {
                sdrSel.innerHTML = '<option value="">No SDR devices found</option>';
            }
        }

        const warnEl = document.getElementById('droneDeviceWarnings');
        if (warnEl) {
            if (!data.running_as_root) {
                warnEl.textContent = 'Not running as root — WiFi monitor mode may be unavailable.';
                warnEl.style.display = 'block';
            } else {
                warnEl.style.display = 'none';
            }
        }
    } catch (e) {
        const wifiSel = document.getElementById('droneWifiIface');
        if (wifiSel) wifiSel.innerHTML = '<option value="">Failed to load interfaces</option>';
        const sdrSel = document.getElementById('droneRtlIndex');
        if (sdrSel) sdrSel.innerHTML = '<option value="">Failed to load devices</option>';
    }
}
