/**
 * Proximity Radar Component
 *
 * SVG radar for Bluetooth devices: nearer the centre means a stronger
 * signal. It is not a distance: a single receiver cannot measure one, and
 * a weak transmitter nearby reads the same as a strong one behind a wall.
 * The rings are labelled by signal band for that reason.
 */

const ProximityRadar = (function() {
    'use strict';

    // Configuration
    const CONFIG = {
        size: 400,          // viewBox units; the SVG scales to its panel
        padding: 22,
        centerRadius: 5,
        // Band boundaries as a fraction of the radius; devices sit inside
        // them (see calculateDevicePosition).
        rings: [
            { radius: 0.28, label: 'STRONG' },
            { radius: 0.55, label: 'MEDIUM' },
            { radius: 0.82, label: 'WEAK' },
        ],
        sweepSeconds: 4,
        dotMinSize: 4,
        dotMaxSize: 9,
        pulseAnimationDuration: 2000,
        newDeviceThreshold: 30, // seconds
    };

    function _accent() {
        return getComputedStyle(document.documentElement).getPropertyValue('--accent-cyan').trim() || '#00d4ff';
    }

    // State
    let container = null;
    let svg = null;
    let devices = new Map();
    let isPaused = false;
    let activeFilter = null;
    let onDeviceClick = null;
    let selectedDeviceKey = null;
    let renderTimer = null;

    /**
     * Initialize the radar component
     */
    function init(containerId, options = {}) {
        container = document.getElementById(containerId);
        if (!container) {
            console.error('[ProximityRadar] Container not found:', containerId);
            return;
        }

        if (options.onDeviceClick) {
            onDeviceClick = options.onDeviceClick;
        }

        createSVG();
    }

    /**
     * Create the SVG radar structure
     */
    function createSVG() {
        const size = CONFIG.size;
        container.innerHTML = `
            <svg viewBox="0 0 ${size} ${size}" class="proximity-radar-svg" role="img"
                 aria-label="Bluetooth devices by signal strength">
                ${RadarFace.markup({
                    id: 'pr',
                    size,
                    padding: CONFIG.padding,
                    rings: CONFIG.rings,
                    sweepSeconds: CONFIG.sweepSeconds,
                    centerRadius: CONFIG.centerRadius,
                })}
                <g class="radar-devices"></g>
            </svg>
            <div class="pr-caption">Nearer the centre = stronger signal. Not a distance.</div>
        `;

        svg = container.querySelector('svg');

        // Event delegation on the devices group (survives innerHTML rebuilds)
        const devicesGroup = svg.querySelector('.radar-devices');

        devicesGroup.addEventListener('click', (e) => {
            const deviceEl = e.target.closest('.radar-device');
            if (!deviceEl) return;
            const deviceKey = deviceEl.getAttribute('data-device-key');
            if (onDeviceClick && deviceKey) {
                onDeviceClick(deviceKey);
            }
        });

    }

    /**
     * Update devices on the radar
     */
    function updateDevices(deviceList) {
        if (isPaused) return;

        deviceList.forEach(device => {
            devices.set(device.device_key, device);
        });

        // Debounce rapid updates (e.g. per-device SSE events)
        if (renderTimer) clearTimeout(renderTimer);
        renderTimer = setTimeout(() => {
            renderTimer = null;
            renderDevices();
        }, 200);
    }

    /**
     * Render device dots on the radar using in-place DOM updates.
     * Elements are never destroyed and recreated — only their attributes and
     * transforms are mutated — so hover state is never disturbed by a render.
     */
    function renderDevices() {
        const devicesGroup = svg.querySelector('.radar-devices');
        if (!devicesGroup) return;

        const center = CONFIG.size / 2;
        const maxRadius = center - CONFIG.padding;
        const ns = 'http://www.w3.org/2000/svg';

        // Filter devices
        let visibleDevices = Array.from(devices.values());

        if (activeFilter === 'newOnly') {
            visibleDevices = visibleDevices.filter(d => d.is_new || d.age_seconds < CONFIG.newDeviceThreshold);
        } else if (activeFilter === 'strongest') {
            visibleDevices = visibleDevices
                .filter(d => d.rssi_current != null)
                .sort((a, b) => (b.rssi_current || -100) - (a.rssi_current || -100))
                .slice(0, 10);
        } else if (activeFilter === 'unapproved') {
            visibleDevices = visibleDevices.filter(d => !d.in_baseline);
        }

        const visibleKeys = new Set(visibleDevices.map(d => d.device_key));

        // Remove elements for devices no longer in the visible set
        devicesGroup.querySelectorAll('.radar-device-wrapper').forEach(el => {
            if (!visibleKeys.has(el.getAttribute('data-device-key'))) {
                el.remove();
            }
        });

        // Sort weakest signal first so strongest renders on top (SVG z-order)
        visibleDevices.sort((a, b) => (a.rssi_current || -100) - (b.rssi_current || -100));

        // Compute all positions upfront so we can spread overlapping dots
        const posMap = new Map();
        visibleDevices.forEach(device => {
            posMap.set(device.device_key, calculateDevicePosition(device, center, maxRadius));
        });

        // Spread dots that land too close together within the same band.
        // minGapPx = diameter of largest possible hit area + 2px breathing room.
        const maxHitArea = CONFIG.dotMaxSize + 4;
        spreadOverlappingDots(Array.from(posMap.values()), center, maxHitArea * 2 + 2);

        visibleDevices.forEach(device => {
            const { x, y } = posMap.get(device.device_key);
            const confidence = device.distance_confidence || 0.5;
            const dotSize = CONFIG.dotMinSize + (CONFIG.dotMaxSize - CONFIG.dotMinSize) * confidence;
            const color = getBandColor(device.proximity_band);
            const isNew = device.age_seconds < 5;
            const isSelected = !!(selectedDeviceKey && device.device_key === selectedDeviceKey);
            const hitAreaSize = dotSize + 4;
            const key = device.device_key;

            const existing = devicesGroup.querySelector(
                `.radar-device-wrapper[data-device-key="${CSS.escape(key)}"]`
            );

            if (existing) {
                // ── In-place update: mutate attributes, never recreate ──
                existing.setAttribute('transform', `translate(${x}, ${y})`);

                const innerG = existing.querySelector('.radar-device');
                if (innerG) {
                    innerG.className.baseVal =
                        `radar-device${isNew ? ' radar-dot-pulse' : ''}${isSelected ? ' selected' : ''}`;

                    const hitArea = innerG.querySelector('.radar-device-hitarea');
                    if (hitArea) hitArea.setAttribute('r', hitAreaSize);

                    const dot = innerG.querySelector('.radar-dot');
                    if (dot) {
                        dot.setAttribute('r', dotSize);
                        dot.setAttribute('fill', color);
                        dot.setAttribute('fill-opacity', isSelected ? 1 : 0.4 + confidence * 0.5);
                        dot.setAttribute('stroke', isSelected ? _accent() : color);
                        dot.setAttribute('stroke-width', isSelected ? 2 : 1);
                    }

                    const title = innerG.querySelector('title');
                    if (title) {
                        title.textContent =
                            `${escapeHtml(device.name || device.address)} (${device.rssi_current || '--'} dBm)`;
                    }

                    // Selection ring: add if newly selected, remove if deselected
                    let ring = innerG.querySelector('.radar-select-ring');
                    if (isSelected && !ring) {
                        ring = buildSelectRing(ns, dotSize);
                        const hitAreaEl = innerG.querySelector('.radar-device-hitarea');
                        innerG.insertBefore(ring, hitAreaEl ? hitAreaEl.nextSibling : innerG.firstChild);
                    } else if (!isSelected && ring) {
                        ring.remove();
                    }

                    // New-device indicator ring
                    let newRing = innerG.querySelector('.radar-new-ring');
                    if (device.is_new && !isSelected) {
                        if (!newRing) {
                            newRing = document.createElementNS(ns, 'circle');
                            newRing.classList.add('radar-new-ring');
                            newRing.setAttribute('fill', 'none');
                            newRing.setAttribute('stroke', '#3b82f6');
                            newRing.setAttribute('stroke-width', '1');
                            newRing.setAttribute('stroke-dasharray', '2,2');
                            innerG.appendChild(newRing);
                        }
                        newRing.setAttribute('r', dotSize + 3);
                    } else if (newRing) {
                        newRing.remove();
                    }
                }
            } else {
                // ── Create new element ──
                const wrapperG = document.createElementNS(ns, 'g');
                wrapperG.classList.add('radar-device-wrapper');
                wrapperG.setAttribute('data-device-key', key);
                wrapperG.setAttribute('transform', `translate(${x}, ${y})`);

                const innerG = document.createElementNS(ns, 'g');
                innerG.classList.add('radar-device');
                if (isNew) innerG.classList.add('radar-dot-pulse');
                if (isSelected) innerG.classList.add('selected');
                innerG.setAttribute('data-device-key', escapeAttr(key));
                innerG.style.cursor = 'pointer';

                const hitArea = document.createElementNS(ns, 'circle');
                hitArea.classList.add('radar-device-hitarea');
                hitArea.setAttribute('r', hitAreaSize);
                hitArea.setAttribute('fill', 'transparent');
                innerG.appendChild(hitArea);

                if (isSelected) {
                    innerG.appendChild(buildSelectRing(ns, dotSize));
                }

                const dot = document.createElementNS(ns, 'circle');
                dot.classList.add('radar-dot');
                dot.setAttribute('filter', 'url(#pr-glow)');
                dot.setAttribute('r', dotSize);
                dot.setAttribute('fill', color);
                dot.setAttribute('fill-opacity', isSelected ? 1 : 0.4 + confidence * 0.5);
                dot.setAttribute('stroke', isSelected ? _accent() : color);
                dot.setAttribute('stroke-width', isSelected ? 2 : 1);
                innerG.appendChild(dot);

                if (device.is_new && !isSelected) {
                    const newRing = document.createElementNS(ns, 'circle');
                    newRing.classList.add('radar-new-ring');
                    newRing.setAttribute('r', dotSize + 3);
                    newRing.setAttribute('fill', 'none');
                    newRing.setAttribute('stroke', '#3b82f6');
                    newRing.setAttribute('stroke-width', '1');
                    newRing.setAttribute('stroke-dasharray', '2,2');
                    innerG.appendChild(newRing);
                }

                const title = document.createElementNS(ns, 'title');
                title.textContent =
                    `${escapeHtml(device.name || device.address)} (${device.rssi_current || '--'} dBm)`;
                innerG.appendChild(title);

                wrapperG.appendChild(innerG);
                devicesGroup.appendChild(wrapperG);
            }
        });
    }

    /**
     * Build an animated SVG selection ring element
     */
    function buildSelectRing(ns, dotSize) {
        const ring = document.createElementNS(ns, 'circle');
        ring.classList.add('radar-select-ring');
        ring.setAttribute('r', dotSize + 8);
        ring.setAttribute('fill', 'none');
        ring.setAttribute('stroke', _accent());
        ring.setAttribute('stroke-width', '2');
        ring.setAttribute('stroke-opacity', '0.8');

        const animR = document.createElementNS(ns, 'animate');
        animR.setAttribute('attributeName', 'r');
        animR.setAttribute('values', `${dotSize + 6};${dotSize + 10};${dotSize + 6}`);
        animR.setAttribute('dur', '1.5s');
        animR.setAttribute('repeatCount', 'indefinite');
        ring.appendChild(animR);

        const animO = document.createElementNS(ns, 'animate');
        animO.setAttribute('attributeName', 'stroke-opacity');
        animO.setAttribute('values', '0.8;0.4;0.8');
        animO.setAttribute('dur', '1.5s');
        animO.setAttribute('repeatCount', 'indefinite');
        ring.appendChild(animO);

        return ring;
    }

    /**
     * Calculate device position on radar
     */
    function calculateDevicePosition(device, center, maxRadius) {
        // Position is band-only — the band is computed server-side from rssi_ema
        // (already smoothed), so it changes infrequently and never jitters.
        // Using raw estimated_distance_m caused constant micro-movement as RSSI
        // fluctuated on every update cycle.
        let radiusRatio;
        switch (device.proximity_band || 'unknown') {
            case 'immediate': radiusRatio = 0.15; break;
            case 'near':      radiusRatio = 0.40; break;
            case 'far':       radiusRatio = 0.70; break;
            default:          radiusRatio = 0.90; break;
        }

        // Calculate angle based on device key hash (stable positioning)
        const angle = hashToAngle(device.device_key || device.device_id);
        const radius = radiusRatio * maxRadius;

        const x = center + Math.sin(angle) * radius;
        const y = center - Math.cos(angle) * radius;

        return { x, y, angle, radius };
    }

    /**
     * Spread dots within the same band that land too close together.
     * Groups entries by radius, sorts by angle, then nudges neighbours
     * apart until the arc gap between any two dots is at least minGapPx.
     * Positions are updated in-place on the entry objects.
     */
    function spreadOverlappingDots(entries, center, minGapPx) {
        const groups = new Map();
        entries.forEach(e => {
            const key = Math.round(e.radius);
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(e);
        });

        groups.forEach((group, r) => {
            if (group.length < 2 || r < 1) return;
            const minSep = minGapPx / r; // radians

            group.sort((a, b) => a.angle - b.angle);

            // Iterative push-apart (up to 8 passes)
            for (let iter = 0; iter < 8; iter++) {
                let moved = false;
                for (let i = 0; i < group.length; i++) {
                    const j = (i + 1) % group.length;
                    let gap = group[j].angle - group[i].angle;
                    if (gap < 0) gap += 2 * Math.PI;
                    if (gap < minSep) {
                        const push = (minSep - gap) / 2;
                        group[i].angle -= push;
                        group[j].angle += push;
                        moved = true;
                    }
                }
                if (!moved) break;
            }

            // Normalise angles back to [0, 2π) and recompute x/y
            group.forEach(e => {
                e.angle = ((e.angle % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
                e.x = center + Math.sin(e.angle) * r;
                e.y = center - Math.cos(e.angle) * r;
            });
        });
    }

    /**
     * Hash string to angle for stable positioning
     */
    function hashToAngle(str) {
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            hash = ((hash << 5) - hash) + str.charCodeAt(i);
            hash = hash & hash;
        }
        return (Math.abs(hash) % 360) * (Math.PI / 180);
    }

    /**
     * Get color for proximity band
     */
    function getBandColor(band) {
        const css = getComputedStyle(document.documentElement);
        const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
        switch (band) {
            case 'immediate': return token('--accent-green', '#38c180');
            case 'near': return token('--accent-amber', '#d6a85e');
            case 'far': return token('--accent-red', '#e25d5d');
            default: return token('--text-dim', '#6b7280');
        }
    }

    /**
     * Set filter mode
     */
    function setFilter(filter) {
        activeFilter = filter === activeFilter ? null : filter;
        renderDevices();
    }

    /**
     * Toggle pause state
     */
    function setPaused(paused) {
        isPaused = paused;
        if (svg && svg.pauseAnimations) {
            if (paused) svg.pauseAnimations(); else svg.unpauseAnimations();
        }
    }

    /**
     * Clear all devices
     */
    function clear() {
        devices.clear();
        selectedDeviceKey = null;
        renderDevices();
    }

    /**
     * Highlight a specific device on the radar (in-place update, no full re-render)
     */
    function highlightDevice(deviceKey) {
        const prev = selectedDeviceKey;
        selectedDeviceKey = deviceKey;

        if (!svg) { return; }
        const devicesGroup = svg.querySelector('.radar-devices');
        if (!devicesGroup) { return; }

        // Remove highlight from previously selected node
        if (prev && prev !== deviceKey) {
            const oldEl = devicesGroup.querySelector(`.radar-device[data-device-key="${CSS.escape(prev)}"]`);
            if (oldEl) {
                oldEl.classList.remove('selected');
                // Remove animated selection ring
                const ring = oldEl.querySelector('.radar-select-ring');
                if (ring) ring.remove();
                // Restore dot opacity
                const dot = oldEl.querySelector('circle:not(.radar-device-hitarea):not(.radar-select-ring)');
                if (dot && dot.getAttribute('fill') !== 'none' && dot.getAttribute('fill') !== 'transparent') {
                    const device = devices.get(prev);
                    const confidence = device ? (device.distance_confidence || 0.5) : 0.5;
                    dot.setAttribute('fill-opacity', 0.4 + confidence * 0.5);
                    dot.setAttribute('stroke', dot.getAttribute('fill'));
                    dot.setAttribute('stroke-width', '1');
                }
            }
        }

        // Add highlight to newly selected node
        if (deviceKey) {
            const newEl = devicesGroup.querySelector(`.radar-device[data-device-key="${CSS.escape(deviceKey)}"]`);
            if (newEl) {
                applySelectionToElement(newEl, deviceKey);
            } else {
                // Node not in DOM yet; full render needed on next cycle
                renderDevices();
            }
        }
    }

    /**
     * Apply selection styling to a radar device element in-place
     */
    function applySelectionToElement(el, deviceKey) {
        el.classList.add('selected');
        const device = devices.get(deviceKey);
        const confidence = device ? (device.distance_confidence || 0.5) : 0.5;
        const dotSize = CONFIG.dotMinSize + (CONFIG.dotMaxSize - CONFIG.dotMinSize) * confidence;

        // Update dot styling
        const dot = el.querySelector('circle:not(.radar-device-hitarea):not(.radar-select-ring)');
        if (dot && dot.getAttribute('fill') !== 'none' && dot.getAttribute('fill') !== 'transparent') {
            dot.setAttribute('fill-opacity', '1');
            dot.setAttribute('stroke', _accent());
            dot.setAttribute('stroke-width', '2');
        }

        // Add animated selection ring if not already present
        if (!el.querySelector('.radar-select-ring')) {
            const ns = 'http://www.w3.org/2000/svg';
            const ring = document.createElementNS(ns, 'circle');
            ring.classList.add('radar-select-ring');
            ring.setAttribute('r', dotSize + 8);
            ring.setAttribute('fill', 'none');
            ring.setAttribute('stroke', _accent());
            ring.setAttribute('stroke-width', '2');
            ring.setAttribute('stroke-opacity', '0.8');

            const animR = document.createElementNS(ns, 'animate');
            animR.setAttribute('attributeName', 'r');
            animR.setAttribute('values', `${dotSize + 6};${dotSize + 10};${dotSize + 6}`);
            animR.setAttribute('dur', '1.5s');
            animR.setAttribute('repeatCount', 'indefinite');
            ring.appendChild(animR);

            const animO = document.createElementNS(ns, 'animate');
            animO.setAttribute('attributeName', 'stroke-opacity');
            animO.setAttribute('values', '0.8;0.4;0.8');
            animO.setAttribute('dur', '1.5s');
            animO.setAttribute('repeatCount', 'indefinite');
            ring.appendChild(animO);

            // Insert after the hit area
            const hitArea = el.querySelector('.radar-device-hitarea');
            if (hitArea && hitArea.nextSibling) {
                el.insertBefore(ring, hitArea.nextSibling);
            } else {
                el.insertBefore(ring, el.firstChild);
            }
        }
    }

    /**
     * Clear device highlighting (in-place update, no full re-render)
     */
    function clearHighlight() {
        const prev = selectedDeviceKey;
        selectedDeviceKey = null;

        if (!svg || !prev) { return; }
        const devicesGroup = svg.querySelector('.radar-devices');
        if (!devicesGroup) { return; }

        const oldEl = devicesGroup.querySelector(`.radar-device[data-device-key="${CSS.escape(prev)}"]`);
        if (oldEl) {
            oldEl.classList.remove('selected');
            const ring = oldEl.querySelector('.radar-select-ring');
            if (ring) ring.remove();
            const dot = oldEl.querySelector('circle:not(.radar-device-hitarea):not(.radar-select-ring)');
            if (dot && dot.getAttribute('fill') !== 'none' && dot.getAttribute('fill') !== 'transparent') {
                const device = devices.get(prev);
                const confidence = device ? (device.distance_confidence || 0.5) : 0.5;
                dot.setAttribute('fill-opacity', 0.4 + confidence * 0.5);
                dot.setAttribute('stroke', dot.getAttribute('fill'));
                dot.setAttribute('stroke-width', '1');
            }
        }
    }

    /**
     * Get zone counts
     */
    function getZoneCounts() {
        const counts = { immediate: 0, near: 0, far: 0, unknown: 0 };
        devices.forEach(device => {
            const band = device.proximity_band || 'unknown';
            if (counts.hasOwnProperty(band)) {
                counts[band]++;
            } else {
                counts.unknown++;
            }
        });
        return counts;
    }

    /**
     * Escape HTML for safe rendering
     */
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    /**
     * Escape attribute value
     */
    function escapeAttr(text) {
        if (!text) return '';
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    // Public API
    return {
        init,
        updateDevices,
        setFilter,
        setPaused,
        clear,
        getZoneCounts,
        highlightDevice,
        clearHighlight,
        isPaused: () => isPaused,
        getFilter: () => activeFilter,
        getSelectedDevice: () => selectedDeviceKey,
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ProximityRadar;
}

window.ProximityRadar = ProximityRadar;
