/**
 * Activity Timeline Component
 * Reusable, configuration-driven timeline visualization for time-based metadata
 * Supports multiple modes: TSCM, RF Receiver, Bluetooth, WiFi, Monitoring
 */

const ActivityTimeline = (function() {
    'use strict';

    // Default configuration
    const defaults = {
        // Identity
        title: 'Activity Timeline',
        mode: 'generic',

        // Display options
        visualMode: 'enriched',      // 'compact' | 'enriched' | 'summary'
        collapsed: true,
        showAnnotations: true,
        showLegend: true,

        // Time configuration
        timeWindows: {
            '5m': 5 * 60 * 1000,
            '15m': 15 * 60 * 1000,
            '30m': 30 * 60 * 1000,
            '1h': 60 * 60 * 1000,
            '2h': 2 * 60 * 60 * 1000,
            '4h': 4 * 60 * 60 * 1000,
            '8h': 8 * 60 * 60 * 1000
        },
        defaultWindow: '30m',
        availableWindows: ['5m', '15m', '30m', '1h', '2h'],

        // Filter configuration
        filters: {
            hideBaseline: { enabled: true, label: 'Hide Known', default: false },
            showOnlyNew: { enabled: true, label: 'New Only', default: false },
            showOnlyBurst: { enabled: true, label: 'Bursts', default: false }
        },
        customFilters: [],

        // Limits
        maxItems: 100,
        maxDisplayedLanes: 15,
        burstThreshold: 5,
        burstWindow: 60 * 1000,
        updateInterval: 5000,
        barMinWidth: 2,

        // Callbacks
        onItemClick: null,
        onItemFlag: null,
        onExport: null,

        // Label generator (can be overridden)
        labelGenerator: null
    };

    // Instances registry for multi-instance support
    const instances = new Map();

    /**
     * Create a new timeline instance
     */
    function create(containerId, options = {}) {
        const container = document.getElementById(containerId);
        if (!container) {
            console.error(`ActivityTimeline: Container '${containerId}' not found`);
            return null;
        }

        // Merge options with defaults
        const config = mergeConfig(defaults, options);

        // Create instance state
        const state = {
            containerId: containerId,
            config: config,
            items: new Map(),
            annotations: [],
            filterState: initFilterState(config),
            timeWindow: config.defaultWindow,
            tooltip: null,
            updateTimer: null,
            element: null
        };

        // Store instance
        instances.set(containerId, state);

        // Build DOM
        state.element = buildTimeline(container, state);

        // Setup interactions
        setupEventListeners(state);

        // Create tooltip
        createTooltip(state);

        // Start update cycle
        startUpdateTimer(state);

        // Initial render
        render(state);

        // Return public API bound to this instance
        return createPublicAPI(containerId);
    }

    /**
     * Merge user config with defaults
     */
    function mergeConfig(defaults, options) {
        const config = { ...defaults };

        for (const key of Object.keys(options)) {
            if (typeof options[key] === 'object' && !Array.isArray(options[key]) && options[key] !== null) {
                config[key] = { ...defaults[key], ...options[key] };
            } else {
                config[key] = options[key];
            }
        }

        return config;
    }

    /**
     * Initialize filter state from config
     */
    function initFilterState(config) {
        const state = {};
        for (const [key, filter] of Object.entries(config.filters)) {
            if (filter.enabled) {
                state[key] = filter.default || false;
            }
        }
        for (const filter of config.customFilters) {
            state[filter.key] = filter.default || false;
        }
        return state;
    }

    /**
     * Create item data structure
     */
    function createItem(id, label, options = {}) {
        return {
            id: id,
            label: label,
            type: options.type || 'generic',
            events: [],
            firstSeen: null,
            lastSeen: null,
            status: 'new',
            pattern: null,
            flagged: false,
            eventCount: 0,
            tags: options.tags || [],
            metadata: options.metadata || {}
        };
    }

    /**
     * Generate label for an item (can be overridden via config)
     */
    function generateLabel(id, state) {
        if (state.config.labelGenerator) {
            return state.config.labelGenerator(id);
        }
        return categorizeById(id, state.config.mode);
    }

    /**
     * Default categorization by mode
     */
    function categorizeById(id, mode) {
        // RF frequency categorization
        if (mode === 'rf' || mode === 'tscm' || mode === 'waterfall') {
            const f = parseFloat(id);
            if (!isNaN(f)) {
                if (f >= 2400 && f <= 2500) return '2.4 GHz wireless band';
                if (f >= 5150 && f <= 5850) return '5 GHz wireless band';
                if (f >= 433 && f <= 434) return '433 MHz low-power band';
                if (f >= 868 && f <= 869) return '868 MHz low-power band';
                if (f >= 902 && f <= 928) return '915 MHz low-power band';
                if (f >= 315 && f <= 316) return '315MHz';
                if (f >= 2402 && f <= 2480) return 'Bluetooth band';
                if (f >= 144 && f <= 148) return 'VHF amateur band';
                if (f >= 420 && f <= 450) return 'UHF amateur band';
                return `${f.toFixed(3)} MHz`;
            }
        }

        // Bluetooth mode - MAC address
        if (mode === 'bluetooth') {
            if (/^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(id)) {
                return id.substring(0, 8) + '...';
            }
        }

        // WiFi mode - SSID
        if (mode === 'wifi') {
            if (id.length > 20) {
                return id.substring(0, 17) + '...';
            }
        }

        return id;
    }

    /**
     * Add or update an event
     */
    function addEvent(containerId, eventData) {
        const state = instances.get(containerId);
        if (!state) return null;

        const now = Date.now();
        const id = eventData.id;
        const label = eventData.label || generateLabel(id, state);

        let item = state.items.get(id);

        if (!item) {
            item = createItem(id, label, {
                type: eventData.type,
                tags: eventData.tags,
                metadata: eventData.metadata
            });
            item.firstSeen = now;
            state.items.set(id, item);

            addAnnotation(state, 'new', `New activity: ${item.label}`, now);
        }

        // Add event
        item.events.push({
            timestamp: now,
            strength: Math.min(5, Math.max(1, eventData.strength || 3)),
            duration: eventData.duration || 1000
        });

        item.lastSeen = now;
        item.eventCount++;

        // Update status
        updateItemStatus(item, state.config);

        // Detect patterns
        detectPatterns(item, state);

        // Prune old events
        const windowMs = state.config.timeWindows['8h'] || state.config.timeWindows['2h'];
        item.events = item.events.filter(e => now - e.timestamp < windowMs);

        // Prune items if over limit
        if (state.items.size > state.config.maxItems) {
            pruneOldItems(state);
        }

        return item;
    }

    /**
     * Bulk import events
     */
    function importEvents(containerId, events) {
        for (const event of events) {
            addEvent(containerId, event);
        }
    }

    /**
     * Remove oldest/least active items
     */
    function pruneOldItems(state) {
        const items = Array.from(state.items.entries());
        items.sort((a, b) => {
            if (a[1].flagged && !b[1].flagged) return 1;
            if (!a[1].flagged && b[1].flagged) return -1;
            return a[1].lastSeen - b[1].lastSeen;
        });

        const toRemove = items.length - state.config.maxItems;
        for (let i = 0; i < toRemove; i++) {
            if (!items[i][1].flagged) {
                state.items.delete(items[i][0]);
            }
        }
    }

    /**
     * Update item status based on activity
     */
    function updateItemStatus(item, config) {
        const now = Date.now();
        const recentEvents = item.events.filter(
            e => now - e.timestamp < config.burstWindow
        );

        if (recentEvents.length >= config.burstThreshold) {
            if (item.status !== 'burst') {
                item.status = 'burst';
            }
        } else if (item.eventCount >= 20) {
            item.status = 'baseline';
        } else if (now - item.firstSeen < 5 * 60 * 1000) {
            item.status = 'new';
        }

        if (item.flagged) {
            item.status = 'flagged';
        }
    }

    /**
     * Detect repeating patterns
     */
    function detectPatterns(item, state) {
        if (item.events.length < 4) return;

        const intervals = [];
        for (let i = 1; i < item.events.length; i++) {
            intervals.push(item.events[i].timestamp - item.events[i-1].timestamp);
        }

        if (intervals.length >= 3) {
            const avgInterval = intervals.reduce((a, b) => a + b, 0) / intervals.length;
            const tolerance = avgInterval * 0.1;
            const consistent = intervals.filter(
                i => Math.abs(i - avgInterval) <= tolerance
            ).length;

            if (consistent >= intervals.length * 0.7) {
                const seconds = Math.round(avgInterval / 1000);
                if (seconds >= 1 && seconds <= 3600) {
                    const patternStr = seconds < 60
                        ? `${seconds}s interval`
                        : `${Math.round(seconds/60)}m interval`;

                    if (item.pattern !== patternStr) {
                        item.pattern = patternStr;
                        addAnnotation(state, 'pattern', `Repeating pattern observed: ${patternStr} - ${item.label}`, Date.now());
                    }
                }
            }
        }
    }

    /**
     * Add annotation
     */
    function addAnnotation(state, type, message, timestamp) {
        state.annotations.unshift({
            type: type,
            message: message,
            timestamp: timestamp
        });

        if (state.annotations.length > 20) {
            state.annotations.pop();
        }
    }

    /**
     * Toggle flag on item
     */
    function toggleFlag(containerId, id) {
        const state = instances.get(containerId);
        if (!state) return;

        const item = state.items.get(id);
        if (item) {
            item.flagged = !item.flagged;
            item.status = item.flagged ? 'flagged' : 'new';
            addAnnotation(state,
                'flagged',
                item.flagged ? `Marked for review: ${item.label}` : `Review mark removed: ${item.label}`,
                Date.now()
            );

            if (state.config.onItemFlag) {
                state.config.onItemFlag(item);
            }

            render(state);
        }
    }

    /**
     * Mark item as inactive
     */
    function markInactive(containerId, id) {
        const state = instances.get(containerId);
        if (!state) return;

        const item = state.items.get(id);
        if (item && item.status !== 'gone') {
            item.status = 'gone';
            addAnnotation(state, 'gone', `No longer active: ${item.label}`, Date.now());
        }
    }

    /**
     * Build timeline DOM
     */
    function buildTimeline(container, state) {
        const config = state.config;
        const timeline = document.createElement('div');
        timeline.className = `activity-timeline activity-timeline--${config.visualMode}` +
                            (config.collapsed ? ' collapsed' : '');
        timeline.id = `activityTimeline-${state.containerId}`;
        timeline.dataset.mode = config.mode;

        // Build filter buttons HTML
        const filterButtonsHtml = buildFilterButtons(config);

        // Build window options HTML
        const windowOptionsHtml = config.availableWindows.map(w =>
            `<option value="${w}"${w === config.defaultWindow ? ' selected' : ''}>${formatWindowLabel(w)}</option>`
        ).join('');

        // Build legend HTML
        const legendHtml = config.showLegend ? `
            <div class="activity-timeline-legend">
                <div class="activity-timeline-legend-item">
                    <div class="activity-timeline-legend-dot new"></div>
                    <span>New</span>
                </div>
                <div class="activity-timeline-legend-item">
                    <div class="activity-timeline-legend-dot baseline"></div>
                    <span>Baseline</span>
                </div>
                <div class="activity-timeline-legend-item">
                    <div class="activity-timeline-legend-dot burst"></div>
                    <span>Burst</span>
                </div>
                <div class="activity-timeline-legend-item">
                    <div class="activity-timeline-legend-dot flagged"></div>
                    <span>Flagged</span>
                </div>
            </div>
        ` : '';

        timeline.innerHTML = `
            <div class="activity-timeline-header">
                <div style="display: flex; align-items: center;">
                    <span class="activity-timeline-collapse-icon">▼</span>
                    <span class="activity-timeline-title">${config.title}</span>
                </div>
                <div class="activity-timeline-header-stats">
                    <div class="activity-timeline-header-stat">
                        <span class="stat-value" data-stat="total">0</span>
                        <span>total</span>
                    </div>
                    <div class="activity-timeline-header-stat">
                        <span class="stat-value" data-stat="new">0</span>
                        <span>new</span>
                    </div>
                    <div class="activity-timeline-header-stat">
                        <span class="stat-value" data-stat="burst">0</span>
                        <span>burst</span>
                    </div>
                </div>
            </div>
            <div class="activity-timeline-body">
                <div class="activity-timeline-controls">
                    ${filterButtonsHtml}
                    <div class="activity-timeline-window">
                        <span>Window:</span>
                        <select class="activity-timeline-window-select">
                            ${windowOptionsHtml}
                        </select>
                    </div>
                </div>
                <div class="activity-timeline-axis"></div>
                <div class="activity-timeline-lanes">
                    <div class="activity-timeline-empty">
                        No activity in this window
                    </div>
                </div>
                <div class="activity-timeline-annotations" style="display: none;"></div>
                ${legendHtml}
            </div>
        `;

        container.appendChild(timeline);
        return timeline;
    }

    /**
     * Build filter buttons HTML
     */
    function buildFilterButtons(config) {
        let html = '';

        for (const [key, filter] of Object.entries(config.filters)) {
            if (filter.enabled) {
                html += `<button class="activity-timeline-btn" data-filter="${key}">${filter.label}</button>`;
            }
        }

        for (const filter of config.customFilters) {
            html += `<button class="activity-timeline-btn" data-filter="${filter.key}">${filter.label}</button>`;
        }

        return html;
    }

    /**
     * Format window label
     */
    function formatWindowLabel(window) {
        const labels = {
            '5m': '5 min',
            '15m': '15 min',
            '30m': '30 min',
            '1h': '1 hour',
            '2h': '2 hours',
            '4h': '4 hours',
            '8h': '8 hours'
        };
        return labels[window] || window;
    }

    /**
     * Setup event listeners
     */
    function setupEventListeners(state) {
        const timeline = state.element;

        // Collapse toggle
        const header = timeline.querySelector('.activity-timeline-header');
        if (header) {
            header.addEventListener('click', (e) => {
                if (e.target.closest('button') || e.target.closest('select')) return;
                timeline.classList.toggle('collapsed');
            });
        }

        // Filter buttons
        timeline.querySelectorAll('.activity-timeline-btn[data-filter]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const filter = btn.dataset.filter;
                state.filterState[filter] = !state.filterState[filter];
                btn.classList.toggle('active', state.filterState[filter]);
                render(state);
            });
        });

        // Time window selector
        const windowSelect = timeline.querySelector('.activity-timeline-window-select');
        if (windowSelect) {
            windowSelect.addEventListener('click', (e) => e.stopPropagation());
            windowSelect.addEventListener('change', (e) => {
                state.timeWindow = e.target.value;
                render(state);
            });
        }

        // Lane interactions
        timeline.addEventListener('click', (e) => {
            const lane = e.target.closest('.activity-timeline-lane');
            if (lane && !e.target.closest('button')) {
                lane.classList.toggle('expanded');

                if (state.config.onItemClick) {
                    const id = lane.dataset.id;
                    const item = state.items.get(id);
                    if (item) {
                        state.config.onItemClick(item);
                    }
                }
            }
        });

        // Right-click to flag
        timeline.addEventListener('contextmenu', (e) => {
            const lane = e.target.closest('.activity-timeline-lane');
            if (lane) {
                e.preventDefault();
                const id = lane.dataset.id;
                toggleFlag(state.containerId, id);
            }
        });
    }

    /**
     * Create tooltip element
     */
    function createTooltip(state) {
        if (state.tooltip) return;

        state.tooltip = document.createElement('div');
        state.tooltip.className = 'activity-timeline-tooltip';
        state.tooltip.style.display = 'none';
        document.body.appendChild(state.tooltip);
    }

    /**
     * Show tooltip
     */
    function showTooltip(e, item, state) {
        if (!state.tooltip) return;

        const lastSeenStr = formatTimeAgo(item.lastSeen);

        state.tooltip.innerHTML = `
            <div class="activity-timeline-tooltip-header">${item.label}</div>
            <div class="activity-timeline-tooltip-row">
                <span>ID:</span>
                <span>${item.id}</span>
            </div>
            <div class="activity-timeline-tooltip-row">
                <span>First seen:</span>
                <span>${formatTime(item.firstSeen)}</span>
            </div>
            <div class="activity-timeline-tooltip-row">
                <span>Last seen:</span>
                <span>${lastSeenStr}</span>
            </div>
            <div class="activity-timeline-tooltip-row">
                <span>Events:</span>
                <span>${item.eventCount}</span>
            </div>
            ${item.pattern ? `
            <div class="activity-timeline-tooltip-row">
                <span>Pattern:</span>
                <span>${item.pattern}</span>
            </div>
            ` : ''}
            <div class="activity-timeline-tooltip-row">
                <span>Status:</span>
                <span style="text-transform: capitalize;">${item.status}</span>
            </div>
            ${item.tags.length > 0 ? `
            <div class="activity-timeline-tooltip-row">
                <span>Tags:</span>
                <span>${item.tags.join(', ')}</span>
            </div>
            ` : ''}
        `;

        state.tooltip.style.display = 'block';
        state.tooltip.style.left = (e.clientX + 10) + 'px';
        state.tooltip.style.top = (e.clientY + 10) + 'px';
    }

    /**
     * Hide tooltip
     */
    function hideTooltip(state) {
        if (state.tooltip) {
            state.tooltip.style.display = 'none';
        }
    }

    /**
     * Start update timer
     */
    function startUpdateTimer(state) {
        if (state.updateTimer) {
            clearInterval(state.updateTimer);
        }
        state.updateTimer = setInterval(() => {
            render(state);
        }, state.config.updateInterval);
    }

    /**
     * Stop update timer
     */
    function stopUpdateTimer(state) {
        if (state.updateTimer) {
            clearInterval(state.updateTimer);
            state.updateTimer = null;
        }
    }

    /**
     * Render the timeline
     */
    function render(state) {
        const lanesContainer = state.element.querySelector('.activity-timeline-lanes');
        const axisContainer = state.element.querySelector('.activity-timeline-axis');
        const annotationsContainer = state.element.querySelector('.activity-timeline-annotations');

        if (!lanesContainer) return;

        const now = Date.now();
        const windowMs = state.config.timeWindows[state.timeWindow];
        const startTime = now - windowMs;

        // Render time axis
        renderAxis(axisContainer, startTime, now, windowMs);

        // Get filtered items
        let items = Array.from(state.items.values());

        // Apply standard filters
        if (state.filterState.hideBaseline) {
            items = items.filter(s => s.status !== 'baseline');
        }
        if (state.filterState.showOnlyNew) {
            items = items.filter(s => s.status === 'new');
        }
        if (state.filterState.showOnlyBurst) {
            items = items.filter(s => s.status === 'burst');
        }

        // Apply custom filters
        for (const filter of state.config.customFilters) {
            if (state.filterState[filter.key] && filter.predicate) {
                items = items.filter(filter.predicate);
            }
        }

        // Sort by priority and recency
        const statusPriority = { flagged: 0, burst: 1, new: 2, baseline: 3, gone: 4 };
        items.sort((a, b) => {
            const priorityDiff = statusPriority[a.status] - statusPriority[b.status];
            if (priorityDiff !== 0) return priorityDiff;
            return b.lastSeen - a.lastSeen;
        });

        // Render lanes
        const totalItems = items.length;
        const displayedItems = items.slice(0, state.config.maxDisplayedLanes);
        const hiddenCount = totalItems - displayedItems.length;

        if (items.length === 0) {
            lanesContainer.innerHTML = `
                <div class="activity-timeline-empty">
                    No activity in this window
                </div>
            `;
        } else {
            let html = displayedItems.map(item =>
                renderLane(item, startTime, now, windowMs, state)
            ).join('');

            if (hiddenCount > 0) {
                html += `
                    <div class="activity-timeline-more">
                        +${hiddenCount} more (scroll or adjust filters)
                    </div>
                `;
            }

            lanesContainer.innerHTML = html;

            // Add tooltip listeners
            lanesContainer.querySelectorAll('.activity-timeline-lane').forEach(lane => {
                const id = lane.dataset.id;
                const item = state.items.get(id);

                lane.addEventListener('mouseenter', (e) => showTooltip(e, item, state));
                lane.addEventListener('mousemove', (e) => showTooltip(e, item, state));
                lane.addEventListener('mouseleave', () => hideTooltip(state));
            });
        }

        // Update header stats
        const allItems = Array.from(state.items.values());
        const statTotal = state.element.querySelector('[data-stat="total"]');
        const statNew = state.element.querySelector('[data-stat="new"]');
        const statBurst = state.element.querySelector('[data-stat="burst"]');
        if (statTotal) statTotal.textContent = allItems.length;
        if (statNew) statNew.textContent = allItems.filter(s => s.status === 'new').length;
        if (statBurst) statBurst.textContent = allItems.filter(s => s.status === 'burst').length;

        // Render annotations
        if (state.config.showAnnotations) {
            renderAnnotations(annotationsContainer, state);
        }
    }

    /**
     * Render time axis
     */
    function renderAxis(container, startTime, endTime, windowMs) {
        if (!container) return;

        const labels = [];
        const steps = 6;
        for (let i = 0; i <= steps; i++) {
            const time = startTime + (windowMs * i / steps);
            const label = i === steps ? 'Now' : formatTimeShort(time);
            labels.push(`<span class="activity-timeline-axis-label">${label}</span>`);
        }

        container.innerHTML = labels.join('');
    }

    /**
     * Render a single lane
     */
    function renderLane(item, startTime, endTime, windowMs, state) {
        const isBaseline = item.status === 'baseline';
        const visualMode = state.config.visualMode;

        // Get events within time window
        const visibleEvents = item.events.filter(
            e => e.timestamp >= startTime && e.timestamp <= endTime
        );

        // Generate bars
        const barsHtml = aggregateAndRenderBars(visibleEvents, startTime, windowMs, state.config);

        // Generate ticks for expanded view
        const ticksHtml = visibleEvents.map(event => {
            const position = ((event.timestamp - startTime) / windowMs) * 100;
            return `<div class="activity-timeline-tick" style="left: ${position}%;" data-strength="${event.strength}"></div>`;
        }).join('');

        const recentCount = visibleEvents.length;

        // Compact mode: minimal info
        if (visualMode === 'compact') {
            return `
                <div class="activity-timeline-lane ${isBaseline ? 'baseline' : ''}"
                     data-id="${item.id}" data-status="${item.status}">
                    <div class="activity-timeline-status" data-status="${item.status}"></div>
                    <div class="activity-timeline-label">
                        <span class="activity-timeline-name">${item.label}</span>
                    </div>
                    <div class="activity-timeline-track">
                        <div class="activity-timeline-track-bg">${barsHtml}</div>
                    </div>
                    <div class="activity-timeline-stats">
                        <span class="activity-timeline-stat-count">${recentCount}</span>
                    </div>
                </div>
            `;
        }

        // Enriched mode: full info
        return `
            <div class="activity-timeline-lane ${isBaseline ? 'baseline' : ''}"
                 data-id="${item.id}" data-status="${item.status}">
                <div class="activity-timeline-status" data-status="${item.status}"></div>
                <div class="activity-timeline-label">
                    <span class="activity-timeline-id">${item.id}</span>
                    <span class="activity-timeline-name">${item.label}</span>
                </div>
                <div class="activity-timeline-track">
                    <div class="activity-timeline-track-bg">${barsHtml}</div>
                    <div class="activity-timeline-ticks">${ticksHtml}</div>
                </div>
                <div class="activity-timeline-stats">
                    <span class="activity-timeline-stat-count">${recentCount}</span>
                    <span class="activity-timeline-stat-label">events</span>
                </div>
            </div>
        `;
    }

    /**
     * Aggregate events into bars
     */
    function aggregateAndRenderBars(events, startTime, windowMs, config) {
        if (events.length === 0) return '';

        const bars = [];
        let currentBar = null;
        const minGap = windowMs / 100;

        events.sort((a, b) => a.timestamp - b.timestamp);

        for (const event of events) {
            if (!currentBar) {
                currentBar = {
                    start: event.timestamp,
                    end: event.timestamp + event.duration,
                    maxStrength: event.strength,
                    count: 1
                };
            } else if (event.timestamp - currentBar.end <= minGap) {
                currentBar.end = Math.max(currentBar.end, event.timestamp + event.duration);
                currentBar.maxStrength = Math.max(currentBar.maxStrength, event.strength);
                currentBar.count++;
            } else {
                bars.push(currentBar);
                currentBar = {
                    start: event.timestamp,
                    end: event.timestamp + event.duration,
                    maxStrength: event.strength,
                    count: 1
                };
            }
        }
        if (currentBar) bars.push(currentBar);

        return bars.map(bar => {
            const left = ((bar.start - startTime) / windowMs) * 100;
            const width = Math.max(
                config.barMinWidth / 8,
                ((bar.end - bar.start) / windowMs) * 100
            );
            const status = bar.count >= config.burstThreshold ? 'burst' :
                           bar.count > 1 ? 'repeated' : 'new';

            return `<div class="activity-timeline-bar" style="left: ${left}%; width: ${width}%;" data-strength="${bar.maxStrength}" data-status="${status}"></div>`;
        }).join('');
    }

    /**
     * Render annotations
     */
    function renderAnnotations(container, state) {
        if (!container) return;

        const recentAnnotations = state.annotations.slice(0, 5);

        if (recentAnnotations.length === 0) {
            container.style.display = 'none';
            return;
        }

        container.style.display = 'block';

        const iconMap = {
            new: '●',
            burst: '◆',
            pattern: '↻',
            flagged: '⚑',
            gone: '○'
        };

        container.innerHTML = recentAnnotations.map(ann => {
            const icon = iconMap[ann.type] || '•';
            return `
                <div class="activity-timeline-annotation" data-type="${ann.type}">
                    <span class="activity-timeline-annotation-icon">${icon}</span>
                    <span>${ann.message}</span>
                    <span style="margin-left: auto; opacity: 0.6;">${formatTimeAgo(ann.timestamp)}</span>
                </div>
            `;
        }).join('');
    }

    /**
     * Format time
     */
    function formatTime(timestamp) {
        return new Date(timestamp).toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        });
    }

    /**
     * Format short time for axis
     */
    function formatTimeShort(timestamp) {
        return new Date(timestamp).toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        });
    }

    /**
     * Format time ago
     */
    function formatTimeAgo(timestamp) {
        const seconds = Math.floor((Date.now() - timestamp) / 1000);
        if (seconds < 5) return 'just now';
        if (seconds < 60) return `${seconds}s ago`;
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        return `${hours}h ago`;
    }

    /**
     * Clear all data
     */
    function clear(containerId) {
        const state = instances.get(containerId);
        if (!state) return;

        state.items.clear();
        state.annotations = [];
        render(state);
    }

    /**
     * Export data
     */
    function exportData(containerId) {
        const state = instances.get(containerId);
        if (!state) return null;

        const items = Array.from(state.items.values()).map(s => ({
            id: s.id,
            label: s.label,
            type: s.type,
            status: s.status,
            pattern: s.pattern,
            firstSeen: new Date(s.firstSeen).toISOString(),
            lastSeen: new Date(s.lastSeen).toISOString(),
            eventCount: s.eventCount,
            flagged: s.flagged,
            tags: s.tags
        }));

        return {
            exportTime: new Date().toISOString(),
            mode: state.config.mode,
            timeWindow: state.timeWindow,
            items: items,
            annotations: state.annotations.map(a => ({
                ...a,
                timestamp: new Date(a.timestamp).toISOString()
            }))
        };
    }

    /**
     * Get stats
     */
    function getStats(containerId) {
        const state = instances.get(containerId);
        if (!state) return null;

        const items = Array.from(state.items.values());
        return {
            total: items.length,
            new: items.filter(s => s.status === 'new').length,
            baseline: items.filter(s => s.status === 'baseline').length,
            burst: items.filter(s => s.status === 'burst').length,
            flagged: items.filter(s => s.flagged).length,
            withPattern: items.filter(s => s.pattern).length
        };
    }

    /**
     * Destroy instance
     */
    function destroy(containerId) {
        const state = instances.get(containerId);
        if (!state) return;

        stopUpdateTimer(state);

        if (state.tooltip) {
            state.tooltip.remove();
            state.tooltip = null;
        }

        if (state.element) {
            state.element.remove();
        }

        instances.delete(containerId);
    }

    /**
     * Create public API for an instance
     */
    function createPublicAPI(containerId) {
        return {
            // Data management
            addEvent: (eventData) => addEvent(containerId, eventData),
            importEvents: (events) => importEvents(containerId, events),
            toggleFlag: (id) => toggleFlag(containerId, id),
            markInactive: (id) => markInactive(containerId, id),
            clear: () => clear(containerId),

            // Rendering
            render: () => {
                const state = instances.get(containerId);
                if (state) render(state);
            },

            // Data access
            getItems: () => {
                const state = instances.get(containerId);
                return state ? Array.from(state.items.values()) : [];
            },
            getAnnotations: () => {
                const state = instances.get(containerId);
                return state ? state.annotations : [];
            },
            getStats: () => getStats(containerId),
            exportData: () => exportData(containerId),

            // Configuration
            setTimeWindow: (window) => {
                const state = instances.get(containerId);
                if (state && state.config.timeWindows[window]) {
                    state.timeWindow = window;
                    render(state);
                }
            },
            setFilter: (filter, value) => {
                const state = instances.get(containerId);
                if (state && state.filterState.hasOwnProperty(filter)) {
                    state.filterState[filter] = value;
                    render(state);
                }
            },

            // Lifecycle
            destroy: () => destroy(containerId)
        };
    }

    // Global API
    return {
        create: create,

        // Convenience methods for single-instance use
        addEvent: (containerId, eventData) => addEvent(containerId, eventData),
        clear: (containerId) => clear(containerId),
        getStats: (containerId) => getStats(containerId),
        exportData: (containerId) => exportData(containerId),
        destroy: (containerId) => destroy(containerId),

        // Instance access
        getInstance: (containerId) => instances.get(containerId),
        getInstances: () => instances
    };
})();

// Backwards compatibility alias
window.ActivityTimeline = ActivityTimeline;

// Legacy SignalTimeline compatibility wrapper
window.SignalTimeline = (function() {
    'use strict';

    let legacyInstance = null;
    const LEGACY_CONTAINER = 'signalTimelineContainer';

    return {
        create: function(containerId, options = {}) {
            // Map old options to new format
            const newOptions = {
                title: 'Signal Activity Timeline',
                mode: 'tscm',
                visualMode: 'enriched',
                collapsed: options.collapsed !== false,
                showAnnotations: true,
                showLegend: true
            };

            legacyInstance = ActivityTimeline.create(containerId, newOptions);
            return legacyInstance ? document.getElementById(`activityTimeline-${containerId}`) : null;
        },

        destroy: function() {
            if (legacyInstance) {
                legacyInstance.destroy();
                legacyInstance = null;
            }
        },

        addEvent: function(frequency, strength = 3, duration = 1000, name = null) {
            if (legacyInstance) {
                return legacyInstance.addEvent({
                    id: String(frequency),
                    label: name,
                    strength: strength,
                    duration: duration,
                    type: 'rf'
                });
            }
            return null;
        },

        flagSignal: function(frequency) {
            if (legacyInstance) {
                legacyInstance.toggleFlag(String(frequency));
            }
        },

        markGone: function(frequency) {
            if (legacyInstance) {
                legacyInstance.markInactive(String(frequency));
            }
        },

        clear: function() {
            if (legacyInstance) {
                legacyInstance.clear();
            }
        },

        render: function() {
            if (legacyInstance) {
                legacyInstance.render();
            }
        },

        getSignals: function() {
            return legacyInstance ? legacyInstance.getItems() : [];
        },

        getAnnotations: function() {
            return legacyInstance ? legacyInstance.getAnnotations() : [];
        },

        getStats: function() {
            return legacyInstance ? legacyInstance.getStats() : null;
        },

        exportData: function() {
            return legacyInstance ? legacyInstance.exportData() : null;
        },

        setTimeWindow: function(window) {
            if (legacyInstance) {
                legacyInstance.setTimeWindow(window);
            }
        },

        setFilter: function(filter, value) {
            if (legacyInstance) {
                legacyInstance.setFilter(filter, value);
            }
        }
    };
})();
