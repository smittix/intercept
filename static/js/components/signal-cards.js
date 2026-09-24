/**
 * Signal Cards Component
 * JavaScript utilities for creating and managing signal cards
 * Used across: Pager, APRS, Sensors, and other signal-based modes
 */

const SignalCards = (function() {
    'use strict';

    // ==========================================================================
    // Signal Strength Classification
    // Translates RSSI values to confidence-safe, client-facing language
    // ==========================================================================

    const SignalClassification = {
        // RSSI thresholds (dBm) - upper bounds
        THRESHOLDS: {
            MINIMAL: -85,
            WEAK: -70,
            MODERATE: -55,
            STRONG: -40
            // VERY_STRONG: > -40
        },

        // Signal strength metadata
        STRENGTH_INFO: {
            minimal: {
                label: 'Minimal',
                description: 'Near minimum observable level',
                interpretation: 'may represent background activity or a distant source',
                confidence: 'low',
                color: '#888888',
                icon: 'signal-0',
                bars: 1
            },
            weak: {
                label: 'Weak',
                description: 'Low-level signal present',
                interpretation: 'possibly distant or partially obstructed',
                confidence: 'low',
                color: '#6baed6',
                icon: 'signal-1',
                bars: 2
            },
            moderate: {
                label: 'Moderate',
                description: 'Consistent signal presence',
                interpretation: 'likely in proximity',
                confidence: 'medium',
                color: '#3182bd',
                icon: 'signal-2',
                bars: 3
            },
            strong: {
                label: 'Strong',
                description: 'Clear, consistent signal',
                interpretation: 'suggests relatively close proximity',
                confidence: 'medium',
                color: '#fd8d3c',
                icon: 'signal-3',
                bars: 4
            },
            very_strong: {
                label: 'Very Strong',
                description: 'Elevated signal level',
                interpretation: 'consistent with a nearby source',
                confidence: 'high',
                color: '#e6550d',
                icon: 'signal-4',
                bars: 5
            }
        },

        // Duration thresholds (seconds)
        DURATION_THRESHOLDS: {
            TRANSIENT: 5,
            SHORT: 30,
            SUSTAINED: 120
            // PERSISTENT: > 120
        },

        DURATION_INFO: {
            transient: {
                label: 'Transient',
                modifier: 'observed briefly',
                confidence_impact: 'limits assessment confidence'
            },
            short: {
                label: 'Short-duration',
                modifier: 'observed for a short period',
                confidence_impact: 'provides limited confidence'
            },
            sustained: {
                label: 'Sustained',
                modifier: 'observed over sustained period',
                confidence_impact: 'supports assessment confidence'
            },
            persistent: {
                label: 'Persistent',
                modifier: 'continuously observed',
                confidence_impact: 'strengthens assessment confidence'
            }
        },

        /**
         * Classify RSSI value into qualitative signal strength
         */
        classifyStrength(rssi) {
            if (rssi === null || rssi === undefined || isNaN(rssi)) {
                return 'minimal';
            }
            const val = parseFloat(rssi);
            if (val <= -85) return 'minimal';
            if (val <= -70) return 'weak';
            if (val <= -55) return 'moderate';
            if (val <= -40) return 'strong';
            return 'very_strong';
        },

        /**
         * Classify detection duration
         */
        classifyDuration(seconds) {
            if (seconds === null || seconds === undefined || seconds < 0) {
                return 'transient';
            }
            const val = parseFloat(seconds);
            if (val < 5) return 'transient';
            if (val < 30) return 'short';
            if (val < 120) return 'sustained';
            return 'persistent';
        },

        /**
         * Get full signal strength info
         */
        getStrengthInfo(rssi) {
            const strength = this.classifyStrength(rssi);
            return {
                strength,
                rssi,
                ...this.STRENGTH_INFO[strength]
            };
        },

        /**
         * Get full duration info
         */
        getDurationInfo(seconds) {
            const duration = this.classifyDuration(seconds);
            return {
                duration,
                seconds,
                ...this.DURATION_INFO[duration]
            };
        },

        /**
         * Calculate overall confidence from signal + duration + observations
         */
        calculateConfidence(rssi, durationSeconds, observationCount = 1) {
            let score = 0;
            const strength = this.classifyStrength(rssi);
            const duration = this.classifyDuration(durationSeconds);

            // Signal strength contribution
            if (strength === 'strong' || strength === 'very_strong') score += 2;
            else if (strength === 'moderate') score += 1;

            // Duration contribution
            if (duration === 'persistent') score += 2;
            else if (duration === 'sustained') score += 1;

            // Observation count contribution
            if (observationCount >= 5) score += 2;
            else if (observationCount >= 3) score += 1;

            // Map to confidence level
            if (score >= 5) return 'high';
            if (score >= 3) return 'medium';
            return 'low';
        },

        /**
         * Generate hedged summary statement
         */
        generateSummary(rssi, durationSeconds, observationCount = 1) {
            const strengthInfo = this.getStrengthInfo(rssi);
            const durationInfo = this.getDurationInfo(durationSeconds);
            const confidence = this.calculateConfidence(rssi, durationSeconds, observationCount);

            if (confidence === 'high') {
                return `${strengthInfo.label}, ${durationInfo.label.toLowerCase()} signal with characteristics that suggest a transmitting device may be nearby`;
            } else if (confidence === 'medium') {
                return `${strengthInfo.label}, ${durationInfo.label.toLowerCase()} signal that may indicate nearby device activity`;
            } else {
                return `${durationInfo.modifier.charAt(0).toUpperCase() + durationInfo.modifier.slice(1)} ${strengthInfo.label.toLowerCase()} signal consistent with possible nearby device activity`;
            }
        },

        /**
         * Generate interpretation with hedging
         */
        generateInterpretation(rssi, durationSeconds, observationCount = 1) {
            const strengthInfo = this.getStrengthInfo(rssi);
            const confidence = this.calculateConfidence(rssi, durationSeconds, observationCount);
            const base = strengthInfo.interpretation;

            if (confidence === 'high') {
                return `Signal characteristics suggest ${base}`;
            } else if (confidence === 'medium') {
                return `Observed pattern may indicate ${base}`;
            } else {
                return `With limited data, this signal may represent ${base} or environmental factors`;
            }
        },

        /**
         * Estimate range from RSSI (with heavy caveats)
         */
        estimateRange(rssi) {
            if (rssi === null || rssi === undefined) {
                return { estimate: 'Unknown', disclaimer: 'Insufficient signal data' };
            }
            const val = parseFloat(rssi);
            let estimate, rangeMin, rangeMax;

            if (val > -40) {
                estimate = '< 3 meters';
                rangeMin = 0; rangeMax = 3;
            } else if (val > -55) {
                estimate = '3-10 meters';
                rangeMin = 3; rangeMax = 10;
            } else if (val > -70) {
                estimate = '5-20 meters';
                rangeMin = 5; rangeMax = 20;
            } else if (val > -85) {
                estimate = '10-50 meters';
                rangeMin = 10; rangeMax = 50;
            } else {
                estimate = '> 30 meters or heavily obstructed';
                rangeMin = 30; rangeMax = null;
            }

            return {
                estimate,
                rangeMin,
                rangeMax,
                disclaimer: 'Range estimates are approximate and influenced by physical obstructions, interference, and transmitter power'
            };
        }
    };

    // Address tracking for new/repeated detection
    const addressHistory = {
        pager: new Map(),      // address -> { count, firstSeen, lastSeen }
        aprs: new Map(),       // callsign -> { count, firstSeen, lastSeen }
        sensor: new Map(),     // id -> { count, firstSeen, lastSeen }
        acars: new Map(),      // flight -> { count, firstSeen, lastSeen }
        ais: new Map(),        // mmsi -> { count, firstSeen, lastSeen }
        meter: new Map()       // meter id -> { count, firstSeen, lastSeen }
    };

    // Threshold for "repeated" status (messages from same source)
    const REPEATED_THRESHOLD = 3;

    // Time window for "burst" detection (ms)
    const BURST_WINDOW = 60000; // 1 minute
    const BURST_THRESHOLD = 5;  // 5+ messages in window = burst

    // Store for managing cards and state
    const state = {
        cards: new Map(),
        filters: {
            status: 'all',
            protocol: 'all',
            msgType: 'all',
            search: ''
        },
        counts: {
            all: 0,
            emergency: 0,
            new: 0,
            burst: 0,
            repeated: 0,
            baseline: 0
        }
    };

    /**
     * Escape HTML to prevent XSS
     */
    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    /**
     * Format timestamp to relative time
     */
    function formatRelativeTime(timestamp) {
        return InterceptTime.relative(timestamp);
    }

    /**
     * Track an address/identifier and return its status
     */
    function trackAddress(type, identifier) {
        const history = addressHistory[type];
        if (!history) return { isNew: true, count: 1 };

        const now = Date.now();
        const existing = history.get(identifier);

        if (!existing) {
            // First time seeing this address
            history.set(identifier, {
                count: 1,
                firstSeen: now,
                lastSeen: now,
                recentTimestamps: [now]
            });
            return { isNew: true, count: 1, isBurst: false };
        }

        // Update existing record
        existing.count++;
        existing.lastSeen = now;

        // Track recent timestamps for burst detection
        existing.recentTimestamps = existing.recentTimestamps || [];
        existing.recentTimestamps.push(now);

        // Clean old timestamps outside burst window
        existing.recentTimestamps = existing.recentTimestamps.filter(
            ts => (now - ts) < BURST_WINDOW
        );

        const isBurst = existing.recentTimestamps.length >= BURST_THRESHOLD;
        const isRepeated = existing.count >= REPEATED_THRESHOLD;

        return {
            isNew: false,
            count: existing.count,
            isBurst: isBurst,
            isRepeated: isRepeated,
            firstSeen: existing.firstSeen
        };
    }

    /**
     * Get address stats without updating
     */
    function getAddressStats(type, identifier) {
        const history = addressHistory[type];
        if (!history) return null;
        return history.get(identifier) || null;
    }

    /**
     * Clear address history (e.g., on session reset)
     */
    function clearAddressHistory(type) {
        if (type) {
            if (addressHistory[type]) {
                addressHistory[type].clear();
            }
        } else {
            Object.keys(addressHistory).forEach(key => {
                addressHistory[key].clear();
            });
        }
    }

    /**
     * Determine signal status based on message data and tracking
     */
    function determineStatus(msg, trackingType = 'pager') {
        // Check for emergency indicators first
        if (msg.emergency ||
            (msg.message && /emergency|distress|mayday|sos|911|help/i.test(msg.message))) {
            return 'emergency';
        }

        // Get identifier based on message type
        let identifier;
        switch (trackingType) {
            case 'pager':
                identifier = msg.address;
                break;
            case 'aprs':
                identifier = msg.callsign || msg.source;
                break;
            case 'sensor':
                identifier = msg.id || msg.sensor_id;
                break;
            case 'acars':
                identifier = msg.flight || msg.tail;
                break;
            case 'ais':
                identifier = msg.mmsi;
                break;
            default:
                identifier = msg.address || msg.id;
        }

        if (!identifier) {
            return 'baseline';
        }

        // Track and get status
        const stats = trackAddress(trackingType, identifier);

        if (stats.isNew) {
            return 'new';
        }
        if (stats.isBurst) {
            return 'burst';
        }
        if (stats.isRepeated) {
            return 'repeated';
        }
        return 'baseline';
    }

    /**
     * Get protocol class name
     */
    function getProtoClass(protocol) {
        if (!protocol) return '';
        const proto = protocol.toLowerCase();
        if (proto.includes('pocsag')) return 'pocsag';
        if (proto.includes('flex')) return 'flex';
        if (proto.includes('aprs')) return 'aprs';
        if (proto.includes('ais')) return 'ais';
        if (proto.includes('acars')) return 'acars';
        return '';
    }

    /**
     * Check if message content is numeric
     */
    function isNumericContent(message) {
        if (!message) return false;
        return /^[0-9\s\-\*\#U]+$/.test(message);
    }

    /**
     * Create signal strength indicator HTML
     * Shows bars + label + optional tooltip with interpretation
     */
    function createSignalIndicator(rssi, options = {}) {
        if (rssi === null || rssi === undefined) return '';

        const info = SignalClassification.getStrengthInfo(rssi);
        const showLabel = options.showLabel !== false;
        const showTooltip = options.showTooltip !== false;
        const compact = options.compact === true;

        // Create signal bars SVG
        const bars = info.bars;
        const barsSvg = `
            <svg class="signal-strength-bars" viewBox="0 0 20 16" width="${compact ? 16 : 20}" height="${compact ? 12 : 16}">
                <rect x="0" y="12" width="3" height="4" fill="${bars >= 1 ? info.color : '#444'}"/>
                <rect x="4" y="9" width="3" height="7" fill="${bars >= 2 ? info.color : '#444'}"/>
                <rect x="8" y="6" width="3" height="10" fill="${bars >= 3 ? info.color : '#444'}"/>
                <rect x="12" y="3" width="3" height="13" fill="${bars >= 4 ? info.color : '#444'}"/>
                <rect x="16" y="0" width="3" height="16" fill="${bars >= 5 ? info.color : '#444'}"/>
            </svg>
        `;

        // Build tooltip content
        let tooltipContent = '';
        if (showTooltip) {
            const rangeEst = SignalClassification.estimateRange(rssi);
            tooltipContent = `
                ${info.label} signal (${rssi} dBm)
                ${info.description}
                Est. range: ${rangeEst.estimate}
                Confidence: ${info.confidence}
            `.trim();
        }

        // Determine CSS class based on confidence
        const confidenceClass = `signal-confidence-${info.confidence}`;

        if (compact) {
            return `
                <span class="signal-strength-indicator compact ${confidenceClass}"
                      ${showTooltip ? `title="${escapeHtml(tooltipContent)}"` : ''}>
                    ${barsSvg}
                </span>
            `;
        }

        return `
            <span class="signal-strength-indicator ${confidenceClass}"
                  ${showTooltip ? `title="${escapeHtml(tooltipContent)}"` : ''}>
                ${barsSvg}
                ${showLabel ? `<span class="signal-strength-label" style="color: ${info.color}">${info.label}</span>` : ''}
            </span>
        `;
    }

    /**
     * Create detailed signal assessment panel for advanced details
     */
    function createSignalAssessmentPanel(rssi, durationSeconds, observationCount = 1) {
        if (rssi === null || rssi === undefined) return '';

        const strengthInfo = SignalClassification.getStrengthInfo(rssi);
        const durationInfo = SignalClassification.getDurationInfo(durationSeconds);
        const confidence = SignalClassification.calculateConfidence(rssi, durationSeconds, observationCount);
        const rangeEst = SignalClassification.estimateRange(rssi);
        const interpretation = SignalClassification.generateInterpretation(rssi, durationSeconds, observationCount);

        return `
            <div class="signal-advanced-section signal-assessment">
                <div class="signal-advanced-title">Signal Assessment</div>
                <div class="signal-assessment-summary">
                    ${createSignalIndicator(rssi, { compact: false, showTooltip: false })}
                    <span class="signal-assessment-text">${escapeHtml(interpretation)}</span>
                </div>
                <div class="signal-advanced-grid">
                    <div class="signal-advanced-item">
                        <span class="signal-advanced-label">Signal Strength</span>
                        <span class="signal-advanced-value">${strengthInfo.label} (${rssi} dBm)</span>
                    </div>
                    <div class="signal-advanced-item">
                        <span class="signal-advanced-label">Detection</span>
                        <span class="signal-advanced-value">${durationInfo.label}</span>
                    </div>
                    <div class="signal-advanced-item">
                        <span class="signal-advanced-label">Est. Range</span>
                        <span class="signal-advanced-value">${rangeEst.estimate}</span>
                    </div>
                    <div class="signal-advanced-item">
                        <span class="signal-advanced-label">Confidence</span>
                        <span class="signal-advanced-value signal-confidence-${confidence}">${confidence.charAt(0).toUpperCase() + confidence.slice(1)}</span>
                    </div>
                </div>
                <div class="signal-assessment-caveat">
                    Note: ${rangeEst.disclaimer}
                </div>
            </div>
        `;
    }

    /**
     * Get message type label
     */
    function getMsgTypeLabel(msg) {
        if (msg.msg_type) return msg.msg_type;
        if (msg.message === '[Tone Only]') return 'Tone';
        if (isNumericContent(msg.message)) return 'Numeric';
        return 'Alpha';
    }

    /**
     * Create a pager message card
     */
    function createPagerCard(msg, options = {}) {
        const status = options.status || determineStatus(msg, 'pager');
        const protoClass = getProtoClass(msg.protocol);
        const isNumeric = isNumericContent(msg.message);
        const relativeTime = formatRelativeTime(msg.timestamp);
        const isToneOnly = msg.message === '[Tone Only]' || msg.msg_type === 'Tone';
        const msgType = getMsgTypeLabel(msg);

        const card = document.createElement('article');
        card.className = 'signal-card signal-card-clickable';
        card.dataset.status = status;
        card.dataset.type = 'message';
        card.dataset.protocol = protoClass;
        card.dataset.msgType = msgType.toLowerCase();
        if (msg.address) card.dataset.address = msg.address;

        // Store message data for dialog
        card.dataset.msgData = JSON.stringify(msg);

        // Get address stats for display
        const stats = getAddressStats('pager', msg.address);
        const seenCount = stats ? stats.count : 1;

        card.innerHTML = `
            <div class="signal-card-header">
                <div class="signal-card-badges">
                    <span class="signal-proto-badge ${protoClass}">${escapeHtml(msg.protocol)}</span>
                    <span class="signal-freq-badge">Addr: ${escapeHtml(msg.address)}${msg.function ? ' / F' + escapeHtml(msg.function) : ''}</span>
                </div>
                ${status !== 'baseline' ? `
                <span class="signal-status-pill" data-status="${status}">
                    <span class="status-dot"></span>
                    ${status.charAt(0).toUpperCase() + status.slice(1)}
                </span>
                ` : ''}
            </div>
            <div class="signal-card-body">
                <div class="signal-meta-row">
                    <span class="signal-msg-type">${escapeHtml(msgType)}</span>
                    ${seenCount > 1 ? `<span class="signal-seen-count" title="Messages from this address">×${seenCount}</span>` : ''}
                    <span class="signal-timestamp" data-timestamp="${escapeHtml(msg.timestamp)}" title="${escapeHtml(msg.timestamp)}">${escapeHtml(relativeTime)}</span>
                </div>
                <div class="signal-message ${isNumeric ? 'numeric' : ''} ${isToneOnly ? 'tone-only' : ''}">${escapeHtml(msg.message || '[No content]')}</div>
            </div>
            <div class="signal-card-actions-float">
                ${!isToneOnly ? `<button class="signal-action-btn" onclick="event.stopPropagation(); SignalCards.copyMessage(this)">Copy</button>` : ''}
                <button class="signal-action-btn" onclick="event.stopPropagation(); SignalCards.muteAddress('${escapeHtml(msg.address)}')">Mute</button>
            </div>
        `;

        // Add click handler to open details dialog
        card.addEventListener('click', () => {
            showSignalDetails(card);
        });

        return card;
    }

    /**
     * Create an APRS message card
     */
    function createAprsCard(msg, options = {}) {
        const status = options.status || determineStatus(msg, 'aprs');
        const relativeTime = formatRelativeTime(msg.timestamp);
        const hasPosition = msg.latitude && msg.longitude;

        const card = document.createElement('article');
        card.className = 'signal-card';
        if (options.compact) card.classList.add('signal-card-compact');
        card.dataset.status = status;
        card.dataset.type = 'aprs';
        card.dataset.protocol = 'aprs';
        if (msg.callsign) card.dataset.callsign = msg.callsign;

        // Determine APRS message type from packet_type or message content
        let aprsType = msg.packet_type || 'position';
        if (msg.weather) aprsType = 'weather';
        else if (msg.telemetry) aprsType = 'telemetry';
        else if (msg.message) aprsType = 'message';
        else if (msg.status) aprsType = 'status';
        card.dataset.packetType = aprsType.toLowerCase();

        // Get stats
        const stats = getAddressStats('aprs', msg.callsign);
        const seenCount = stats ? stats.count : 1;

        card.innerHTML = `
            <div class="signal-card-header">
                <div class="signal-card-badges">
                    <span class="signal-proto-badge aprs">APRS</span>
                    <span class="signal-freq-badge signal-station-clickable" data-callsign="${escapeHtml(msg.callsign || 'Unknown')}" data-raw="${msg.raw ? escapeHtml(msg.raw) : ''}" onclick="SignalCards.showStationRawData(this)" title="Click to view raw data">${escapeHtml(msg.callsign || 'Unknown')}</span>
                </div>
                ${status !== 'baseline' ? `
                <span class="signal-status-pill" data-status="${status}">
                    <span class="status-dot"></span>
                    ${status.charAt(0).toUpperCase() + status.slice(1)}
                </span>
                ` : ''}
            </div>
            <div class="signal-card-body">
                <div class="signal-meta-row">
                    <span class="signal-msg-type">${aprsType.charAt(0).toUpperCase() + aprsType.slice(1)}</span>
                    ${msg.symbol ? `<span class="signal-aprs-symbol" title="APRS Symbol">${escapeHtml(msg.symbol)}</span>` : ''}
                    ${msg.distance !== null && msg.distance !== undefined ? `<span class="signal-distance">${msg.distance.toFixed(1)} mi</span>` : ''}
                    ${seenCount > 1 ? `<span class="signal-seen-count">×${seenCount}</span>` : ''}
                    <span class="signal-timestamp" data-timestamp="${escapeHtml(msg.timestamp)}">${escapeHtml(relativeTime)}</span>
                </div>
                ${msg.comment || msg.status || msg.message ? `
                <div class="signal-message">${escapeHtml(msg.comment || msg.status || msg.message)}</div>
                ` : ''}
                ${msg.weather ? `
                <div class="signal-message">
                    ${msg.weather.temp ? `Temp: ${msg.weather.temp}°F ` : ''}
                    ${msg.weather.humidity ? `Humidity: ${msg.weather.humidity}% ` : ''}
                    ${msg.weather.wind_speed ? `Wind: ${msg.weather.wind_speed}mph ` : ''}
                    ${msg.weather.wind_dir ? `from ${msg.weather.wind_dir}° ` : ''}
                </div>
                ` : ''}
                ${hasPosition ? `
                <div class="signal-mini-map" onclick="SignalCards.showOnMap(${msg.latitude}, ${msg.longitude}, '${escapeHtml(msg.callsign)}')">
                    <div class="signal-map-pin"></div>
                    <span class="signal-map-coords">${msg.latitude.toFixed(4)}°, ${msg.longitude.toFixed(4)}°</span>
                </div>
                ` : ''}
            </div>
            <div class="signal-card-footer">
                <button class="signal-advanced-toggle" onclick="SignalCards.toggleAdvanced(this)">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M6 9l6 6 6-6"/>
                    </svg>
                    Details
                </button>
                <div class="signal-card-actions">
                    ${hasPosition ? `<button class="signal-action-btn primary" onclick="SignalCards.showOnMap(${msg.latitude}, ${msg.longitude}, '${escapeHtml(msg.callsign)}')">Map</button>` : ''}
                    <button class="signal-action-btn" onclick="SignalCards.muteAddress('${escapeHtml(msg.callsign)}')">Mute</button>
                </div>
            </div>
            <div class="signal-advanced-panel">
                <div class="signal-advanced-inner">
                    <div class="signal-advanced-content">
                        <div class="signal-advanced-section">
                            <div class="signal-advanced-title">Station Details</div>
                            <div class="signal-advanced-grid">
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Callsign</span>
                                    <span class="signal-advanced-value">${escapeHtml(msg.callsign)}</span>
                                </div>
                                ${msg.path ? `
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Path</span>
                                    <span class="signal-advanced-value">${escapeHtml(msg.path)}</span>
                                </div>
                                ` : ''}
                                ${hasPosition ? `
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Position</span>
                                    <span class="signal-advanced-value">${msg.latitude.toFixed(5)}°, ${msg.longitude.toFixed(5)}°</span>
                                </div>
                                ` : ''}
                                ${msg.altitude ? `
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Altitude</span>
                                    <span class="signal-advanced-value">${msg.altitude} ft</span>
                                </div>
                                ` : ''}
                                ${msg.speed ? `
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Speed</span>
                                    <span class="signal-advanced-value">${msg.speed} mph</span>
                                </div>
                                ` : ''}
                                ${msg.course ? `
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Course</span>
                                    <span class="signal-advanced-value">${msg.course}°</span>
                                </div>
                                ` : ''}
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Seen</span>
                                    <span class="signal-advanced-value">${seenCount} time${seenCount > 1 ? 's' : ''}</span>
                                </div>
                            </div>
                        </div>
                        ${msg.raw ? `
                        <div class="signal-advanced-section">
                            <div class="signal-advanced-title">Raw Packet</div>
                            <div class="signal-raw-data">${escapeHtml(msg.raw)}</div>
                        </div>
                        ` : ''}
                    </div>
                </div>
            </div>
        `;

        return card;
    }

    /**
     * Create a sensor (433MHz) message card
     */
    function createSensorCard(msg, options = {}) {
        const status = options.status || determineStatus(msg, 'sensor');
        const relativeTime = formatRelativeTime(msg.timestamp);

        const card = document.createElement('article');
        card.className = 'signal-card signal-card-clickable';
        card.dataset.status = status;
        card.dataset.type = 'sensor';
        card.dataset.protocol = msg.model || 'unknown';
        if (msg.id) card.dataset.sensorId = msg.id;

        // Store message data for dialog
        card.dataset.msgData = JSON.stringify(msg);

        // Get stats
        const stats = getAddressStats('sensor', msg.id);
        const seenCount = stats ? stats.count : 1;

        // Get signal strength if available (rtl_433 uses 'snr' for signal-to-noise ratio)
        const rssi = msg.rssi || msg.signal_strength || msg.snr || msg.noise || null;
        const signalIndicator = rssi !== null
            ? createSignalIndicator(rssi, { compact: true })
            : '<span class="signal-strength-indicator compact no-data" title="No signal data available">--</span>';

        // Signal type guessing based on frequency
        let signalGuessBadge = '';
        if (msg.frequency && typeof SignalGuess !== 'undefined') {
            const frequencyHz = parseFloat(msg.frequency) * 1_000_000; // Convert MHz to Hz
            const signalGuess = SignalGuess.guessSignalType({
                frequency_hz: frequencyHz,
                modulation: msg.modulation || null,
                bandwidth_hz: msg.bandwidth ? parseFloat(msg.bandwidth) * 1000 : null,
                rssi_dbm: rssi,
                region: 'UK/EU'
            });

            // Create compact badge for header
            if (signalGuess && signalGuess.primary_label !== 'Unknown Signal') {
                signalGuessBadge = SignalGuess.createCompactBadge(signalGuess).outerHTML;
            }
        }

        card.innerHTML = `
            <div class="signal-card-header">
                <div class="signal-card-badges">
                    <span class="signal-proto-badge sensor">${escapeHtml(msg.model || 'Unknown')}</span>
                    <span class="signal-freq-badge">ID: ${escapeHtml(msg.id || 'N/A')}</span>
                    ${signalIndicator}
                    ${signalGuessBadge}
                </div>
                ${status !== 'baseline' ? `
                <span class="signal-status-pill" data-status="${status}">
                    <span class="status-dot"></span>
                    ${status.charAt(0).toUpperCase() + status.slice(1)}
                </span>
                ` : ''}
            </div>
            <div class="signal-card-body">
                <div class="signal-meta-row">
                    ${msg.channel ? `<span class="signal-msg-type">Ch ${msg.channel}</span>` : ''}
                    ${seenCount > 1 ? `<span class="signal-seen-count">×${seenCount}</span>` : ''}
                    <span class="signal-timestamp" data-timestamp="${escapeHtml(msg.timestamp)}">${escapeHtml(relativeTime)}</span>
                </div>
                <div class="signal-sensor-data">
                    ${msg.temperature !== undefined ? `
                    <div class="signal-sensor-reading">
                        <span class="sensor-label">Temp</span>
                        <span class="sensor-value">${msg.temperature}°${msg.temperature_unit || 'F'}</span>
                    </div>
                    ` : ''}
                    ${msg.humidity !== undefined ? `
                    <div class="signal-sensor-reading">
                        <span class="sensor-label">Humidity</span>
                        <span class="sensor-value">${msg.humidity}%</span>
                    </div>
                    ` : ''}
                    ${msg.battery !== undefined ? `
                    <div class="signal-sensor-reading">
                        <span class="sensor-label">Battery</span>
                        <span class="sensor-value ${msg.battery === 'LOW' ? 'low-battery' : ''}">${msg.battery}</span>
                    </div>
                    ` : ''}
                    ${msg.pressure !== undefined ? `
                    <div class="signal-sensor-reading">
                        <span class="sensor-label">Pressure</span>
                        <span class="sensor-value">${msg.pressure} ${msg.pressure_unit || 'hPa'}</span>
                    </div>
                    ` : ''}
                    ${msg.wind_speed !== undefined ? `
                    <div class="signal-sensor-reading">
                        <span class="sensor-label">Wind</span>
                        <span class="sensor-value">${msg.wind_speed} ${msg.wind_unit || 'mph'}</span>
                    </div>
                    ` : ''}
                    ${msg.rain !== undefined ? `
                    <div class="signal-sensor-reading">
                        <span class="sensor-label">Rain</span>
                        <span class="sensor-value">${msg.rain} ${msg.rain_unit || 'mm'}</span>
                    </div>
                    ` : ''}
                    ${msg.state !== undefined ? `
                    <div class="signal-sensor-reading">
                        <span class="sensor-label">State</span>
                        <span class="sensor-value">${escapeHtml(msg.state)}</span>
                    </div>
                    ` : ''}
                </div>
            </div>
            <div class="signal-card-actions-float">
                <button class="signal-action-btn" onclick="event.stopPropagation(); SignalCards.muteAddress('${escapeHtml(msg.id)}')">Mute</button>
            </div>
        `;

        // Add click handler to open details dialog
        card.addEventListener('click', () => {
            showSignalDetails(card);
        });

        return card;
    }

    /**
     * Create an ACARS message card
     */
    function createAcarsCard(msg, options = {}) {
        const status = options.status || determineStatus(msg, 'acars');
        const relativeTime = formatRelativeTime(msg.timestamp);

        const card = document.createElement('article');
        card.className = 'signal-card';
        card.dataset.status = status;
        card.dataset.type = 'acars';
        card.dataset.protocol = 'acars';
        if (msg.flight) card.dataset.flight = msg.flight;

        // Get stats
        const stats = getAddressStats('acars', msg.flight || msg.tail);
        const seenCount = stats ? stats.count : 1;

        card.innerHTML = `
            <div class="signal-card-header">
                <div class="signal-card-badges">
                    <span class="signal-proto-badge acars">ACARS</span>
                    <span class="signal-freq-badge">${escapeHtml(msg.flight || msg.tail || 'Unknown')}</span>
                </div>
                ${status !== 'baseline' ? `
                <span class="signal-status-pill" data-status="${status}">
                    <span class="status-dot"></span>
                    ${status.charAt(0).toUpperCase() + status.slice(1)}
                </span>
                ` : ''}
            </div>
            <div class="signal-card-body">
                <div class="signal-meta-row">
                    ${msg.label ? `<span class="signal-msg-type">${escapeHtml(msg.label)}</span>` : ''}
                    ${seenCount > 1 ? `<span class="signal-seen-count">×${seenCount}</span>` : ''}
                    <span class="signal-timestamp" data-timestamp="${escapeHtml(msg.timestamp)}">${escapeHtml(relativeTime)}</span>
                </div>
                ${msg.text ? `
                <div class="signal-message">${escapeHtml(msg.text)}</div>
                ` : ''}
            </div>
            <div class="signal-card-footer">
                <button class="signal-advanced-toggle" onclick="SignalCards.toggleAdvanced(this)">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M6 9l6 6 6-6"/>
                    </svg>
                    Details
                </button>
                <div class="signal-card-actions">
                    <button class="signal-action-btn" onclick="SignalCards.copyMessage(this)">Copy</button>
                </div>
            </div>
            <div class="signal-advanced-panel">
                <div class="signal-advanced-inner">
                    <div class="signal-advanced-content">
                        <div class="signal-advanced-section">
                            <div class="signal-advanced-title">Flight Details</div>
                            <div class="signal-advanced-grid">
                                ${msg.flight ? `
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Flight</span>
                                    <span class="signal-advanced-value">${escapeHtml(msg.flight)}</span>
                                </div>
                                ` : ''}
                                ${msg.tail ? `
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Tail #</span>
                                    <span class="signal-advanced-value">${escapeHtml(msg.tail)}</span>
                                </div>
                                ` : ''}
                                ${msg.label ? `
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Label</span>
                                    <span class="signal-advanced-value">${escapeHtml(msg.label)}</span>
                                </div>
                                ` : ''}
                                ${msg.mode ? `
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Mode</span>
                                    <span class="signal-advanced-value">${escapeHtml(msg.mode)}</span>
                                </div>
                                ` : ''}
                                ${msg.frequency ? `
                                <div class="signal-advanced-item">
                                    <span class="signal-advanced-label">Frequency</span>
                                    <span class="signal-advanced-value">${msg.frequency} MHz</span>
                                </div>
                                ` : ''}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        return card;
    }

    /**
     * Build HTML for all meter detail fields from raw message data
     */
    function buildMeterDetailsHtml(msg, seenCount) {
        let html = '';
        const rawMessage = msg.rawMessage || {};

        // Add device intelligence info at the top
        if (msg.utility && msg.utility !== 'Unknown') {
            html += `
                <div class="signal-advanced-item">
                    <span class="signal-advanced-label">Utility Type</span>
                    <span class="signal-advanced-value">${escapeHtml(msg.utility)}</span>
                </div>
            `;
        }
        if (msg.manufacturer && msg.manufacturer !== 'Unknown') {
            html += `
                <div class="signal-advanced-item">
                    <span class="signal-advanced-label">Manufacturer</span>
                    <span class="signal-advanced-value">${escapeHtml(msg.manufacturer)}</span>
                </div>
            `;
        }

        // Display all fields from the raw rtlamr message
        for (const [key, value] of Object.entries(rawMessage)) {
            if (value === null || value === undefined) continue;

            // Format the label (convert camelCase/PascalCase to spaces)
            const label = key.replace(/([A-Z])/g, ' $1').replace(/^./, s => s.toUpperCase()).trim();

            // Format the value based on type
            let displayValue;
            if (Array.isArray(value)) {
                // For arrays like DifferentialConsumptionIntervals, show count and values
                if (value.length > 10) {
                    displayValue = `[${value.length} values] ${value.slice(0, 5).join(', ')}...`;
                } else {
                    displayValue = value.join(', ');
                }
            } else if (typeof value === 'object') {
                displayValue = JSON.stringify(value);
            } else if (key === 'Consumption') {
                displayValue = `${value.toLocaleString()} units`;
            } else {
                displayValue = String(value);
            }

            html += `
                <div class="signal-advanced-item">
                    <span class="signal-advanced-label">${escapeHtml(label)}</span>
                    <span class="signal-advanced-value">${escapeHtml(displayValue)}</span>
                </div>
            `;
        }

        // Add message type if not in raw message
        if (!rawMessage.Type && msg.type) {
            html += `
                <div class="signal-advanced-item">
                    <span class="signal-advanced-label">Message Type</span>
                    <span class="signal-advanced-value">${escapeHtml(msg.type)}</span>
                </div>
            `;
        }

        // Add seen count
        html += `
            <div class="signal-advanced-item">
                <span class="signal-advanced-label">Seen</span>
                <span class="signal-advanced-value">${seenCount} time${seenCount > 1 ? 's' : ''}</span>
            </div>
        `;

        return html;
    }

    /**
     * Create a utility meter (rtlamr) card
     */
    function createMeterCard(msg, options = {}) {
        const status = options.status || determineStatus(msg, 'meter');
        const relativeTime = formatRelativeTime(msg.timestamp);

        const card = document.createElement('article');
        card.className = 'signal-card';
        card.dataset.status = status;
        card.dataset.type = 'meter';
        card.dataset.protocol = msg.type || 'unknown';
        if (msg.id) card.dataset.meterId = msg.id;

        // Get stats
        const stats = getAddressStats('meter', msg.id);
        const seenCount = stats ? stats.count : 1;

        // Determine meter type color based on utility type
        let meterTypeClass = 'electric';
        const utility = (msg.utility || '').toLowerCase();
        const meterType = (msg.type || '').toLowerCase();
        if (utility === 'gas' || meterType.includes('gas')) {
            meterTypeClass = 'gas';
        } else if (utility === 'water' || meterType.includes('water') || meterType.includes('r900')) {
            meterTypeClass = 'water';
        }

        // Format utility display
        const utilityDisplay = msg.utility && msg.utility !== 'Unknown' ? msg.utility : null;
        const manufacturerDisplay = msg.manufacturer && msg.manufacturer !== 'Unknown' ? msg.manufacturer : null;

        card.innerHTML = `
            <div class="signal-card-header">
                <div class="signal-card-badges">
                    <span class="signal-proto-badge meter ${meterTypeClass}">${escapeHtml(utilityDisplay || msg.type || 'Meter')}</span>
                    <span class="signal-freq-badge">ID: ${escapeHtml(msg.id || 'N/A')}</span>
                </div>
                ${status !== 'baseline' ? `
                <span class="signal-status-pill" data-status="${status}">
                    <span class="status-dot"></span>
                    ${status.charAt(0).toUpperCase() + status.slice(1)}
                </span>
                ` : ''}
            </div>
            <div class="signal-card-body">
                <div class="signal-meta-row">
                    ${manufacturerDisplay ? `<span class="signal-msg-type">${escapeHtml(manufacturerDisplay)}</span>` : ''}
                    ${msg.type ? `<span class="signal-msg-type" style="opacity: 0.7">${escapeHtml(msg.type)}</span>` : ''}
                    ${seenCount > 1 ? `<span class="signal-seen-count">×${seenCount}</span>` : ''}
                    <span class="signal-timestamp" data-timestamp="${escapeHtml(msg.timestamp)}">${escapeHtml(relativeTime)}</span>
                </div>
                <div class="signal-meter-data">
                    ${msg.consumption !== undefined ? `
                    <div class="signal-meter-reading">
                        <span class="meter-label">Consumption</span>
                        <span class="meter-value">${msg.consumption.toLocaleString()} ${msg.unit || 'units'}</span>
                    </div>
                    ` : ''}
                </div>
            </div>
            <div class="signal-card-footer">
                <button class="signal-advanced-toggle" onclick="SignalCards.toggleAdvanced(this)">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M6 9l6 6 6-6"/>
                    </svg>
                    Details
                </button>
                <div class="signal-card-actions">
                    <button class="signal-action-btn" onclick="SignalCards.muteAddress('${escapeHtml(msg.id)}')">Mute</button>
                </div>
            </div>
            <div class="signal-advanced-panel">
                <div class="signal-advanced-inner">
                    <div class="signal-advanced-content">
                        <div class="signal-advanced-section">
                            <div class="signal-advanced-title">Meter Details</div>
                            <div class="signal-advanced-grid">
                                ${buildMeterDetailsHtml(msg, seenCount)}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        return card;
    }

    /**
     * Create an aggregated utility meter card (grouped by meter ID)
     * Shows consumption history, sparkline, delta, and rate
     * @param {Object} meter - Aggregated meter data from MeterAggregator
     * @param {Object} options - Optional configuration
     * @returns {HTMLElement}
     */
    function createAggregatedMeterCard(meter, options = {}) {
        const status = meter.readingCount === 1 ? 'new' : 'baseline';
        const relativeTime = MeterAggregator.getTimeSinceLastReading(meter);

        const card = document.createElement('article');
        card.className = 'signal-card meter-aggregated';
        card.dataset.status = status;
        card.dataset.type = 'meter';
        card.dataset.protocol = meter.type || 'unknown';
        card.dataset.meterId = meter.id;
        card.id = 'metercard_' + meter.id;

        // Determine meter type color
        let meterTypeClass = 'electric';
        const utility = (meter.utility || '').toLowerCase();
        const meterType = (meter.type || '').toLowerCase();
        if (utility === 'gas' || meterType.includes('gas')) {
            meterTypeClass = 'gas';
        } else if (utility === 'water' || meterType.includes('water') || meterType.includes('r900')) {
            meterTypeClass = 'water';
        }

        // Format utility display
        const utilityDisplay = meter.utility && meter.utility !== 'Unknown' ? meter.utility : null;
        const manufacturerDisplay = meter.manufacturer && meter.manufacturer !== 'Unknown' ? meter.manufacturer : null;

        // Get consumption deltas for sparkline
        const deltas = typeof MeterAggregator !== 'undefined'
            ? MeterAggregator.getConsumptionDeltas(meter)
            : [];

        // Create sparkline
        const sparklineHtml = typeof ConsumptionSparkline !== 'undefined'
            ? ConsumptionSparkline.createSparklineSvg(deltas, { width: 100, height: 28 })
            : '<span class="meter-sparkline-placeholder">--</span>';

        // Format delta and rate
        const deltaFormatted = MeterAggregator.formatDelta(meter.delta);
        const rateFormatted = MeterAggregator.formatRate(meter.rate);
        const deltaClass = meter.delta === null ? '' : (meter.delta >= 0 ? 'positive' : 'negative');

        // Get latest consumption
        const latestConsumption = meter.history.length > 0
            ? meter.history[meter.history.length - 1].consumption
            : null;

        card.innerHTML = `
            <div class="signal-card-header">
                <div class="signal-card-badges">
                    <span class="signal-proto-badge meter ${meterTypeClass}">${escapeHtml(utilityDisplay || meter.type || 'Meter')}</span>
                    <span class="signal-freq-badge">ID: ${escapeHtml(meter.id || 'N/A')}</span>
                    ${meter.readingCount > 1 ? `<span class="signal-seen-count">&times;${meter.readingCount}</span>` : ''}
                </div>
                ${status === 'new' ? `
                <span class="signal-status-pill" data-status="new">
                    <span class="status-dot"></span>
                    New
                </span>
                ` : ''}
            </div>
            <div class="signal-card-body">
                <div class="signal-meta-row">
                    ${manufacturerDisplay ? `<span class="signal-msg-type">${escapeHtml(manufacturerDisplay)}</span>` : ''}
                    ${meter.type ? `<span class="signal-msg-type" style="opacity: 0.7">${escapeHtml(meter.type)}</span>` : ''}
                    <span class="signal-timestamp meter-last-seen" data-timestamp="${meter.lastSeen}">${escapeHtml(relativeTime)}</span>
                </div>
                <div class="meter-aggregated-grid">
                    <div class="meter-aggregated-col consumption-col">
                        <span class="meter-aggregated-label">Consumption</span>
                        <span class="meter-aggregated-value consumption-value">${latestConsumption !== null ? latestConsumption.toLocaleString() : '--'}</span>
                        <span class="meter-delta ${deltaClass}" title="Change from previous reading">${deltaFormatted}</span>
                    </div>
                    <div class="meter-aggregated-col trend-col">
                        <span class="meter-aggregated-label">Trend</span>
                        <div class="meter-sparkline-container">
                            ${sparklineHtml}
                        </div>
                    </div>
                    <div class="meter-aggregated-col rate-col">
                        <span class="meter-aggregated-label">Rate</span>
                        <span class="meter-rate-value">${rateFormatted}</span>
                    </div>
                </div>
            </div>
            <div class="signal-card-footer">
                <button class="signal-advanced-toggle" onclick="SignalCards.toggleAdvanced(this)">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M6 9l6 6 6-6"/>
                    </svg>
                    Details
                </button>
                <div class="signal-card-actions">
                    <button class="signal-action-btn" onclick="SignalCards.muteAddress('${escapeHtml(meter.id)}')">Mute</button>
                </div>
            </div>
            <div class="signal-advanced-panel">
                <div class="signal-advanced-inner">
                    <div class="signal-advanced-content">
                        <div class="signal-advanced-section">
                            <div class="signal-advanced-title">Meter Details</div>
                            <div class="signal-advanced-grid">
                                ${buildAggregatedMeterDetailsHtml(meter)}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        return card;
    }

    /**
     * Update an existing aggregated meter card in place
     * @param {HTMLElement} card - The card element to update
     * @param {Object} meter - Updated meter data from MeterAggregator
     */
    function updateAggregatedMeterCard(card, meter) {
        if (!card || !meter) return;

        // Update timestamp
        const relativeTime = MeterAggregator.getTimeSinceLastReading(meter);
        const timestampEl = card.querySelector('.meter-last-seen');
        if (timestampEl) {
            timestampEl.dataset.timestamp = meter.lastSeen;
            timestampEl.textContent = relativeTime;
        }

        // Update seen count badge
        const seenCountEl = card.querySelector('.signal-seen-count');
        if (seenCountEl) {
            seenCountEl.innerHTML = `&times;${meter.readingCount}`;
        } else if (meter.readingCount > 1) {
            // Add seen count if it doesn't exist
            const badges = card.querySelector('.signal-card-badges');
            if (badges) {
                const countSpan = document.createElement('span');
                countSpan.className = 'signal-seen-count';
                countSpan.innerHTML = `&times;${meter.readingCount}`;
                badges.appendChild(countSpan);
            }
        }

        // Remove "new" status pill after first update
        if (meter.readingCount > 1) {
            card.dataset.status = 'baseline';
            const statusPill = card.querySelector('.signal-status-pill[data-status="new"]');
            if (statusPill) {
                statusPill.remove();
            }
        }

        // Update consumption value
        const latestConsumption = meter.history.length > 0
            ? meter.history[meter.history.length - 1].consumption
            : null;
        const consumptionEl = card.querySelector('.consumption-value');
        if (consumptionEl) {
            consumptionEl.textContent = latestConsumption !== null ? latestConsumption.toLocaleString() : '--';
        }

        // Update delta
        const deltaEl = card.querySelector('.meter-delta');
        if (deltaEl) {
            const deltaFormatted = MeterAggregator.formatDelta(meter.delta);
            deltaEl.textContent = deltaFormatted;
            deltaEl.classList.remove('positive', 'negative');
            if (meter.delta !== null) {
                deltaEl.classList.add(meter.delta >= 0 ? 'positive' : 'negative');
            }
        }

        // Update sparkline
        const sparklineContainer = card.querySelector('.meter-sparkline-container');
        if (sparklineContainer && typeof ConsumptionSparkline !== 'undefined') {
            const deltas = MeterAggregator.getConsumptionDeltas(meter);
            sparklineContainer.innerHTML = ConsumptionSparkline.createSparklineSvg(deltas, { width: 100, height: 28 });
        }

        // Update rate
        const rateEl = card.querySelector('.meter-rate-value');
        if (rateEl) {
            rateEl.textContent = MeterAggregator.formatRate(meter.rate);
        }

        // Update details panel
        const detailsGrid = card.querySelector('.signal-advanced-grid');
        if (detailsGrid) {
            detailsGrid.innerHTML = buildAggregatedMeterDetailsHtml(meter);
        }

        // Add subtle update animation
        card.classList.add('meter-updated');
        setTimeout(() => card.classList.remove('meter-updated'), 300);
    }

    /**
     * Build HTML for aggregated meter detail fields
     * @param {Object} meter - Aggregated meter data
     * @returns {string} - HTML string
     */
    function buildAggregatedMeterDetailsHtml(meter) {
        let html = '';
        const latestReading = meter.latestReading || {};
        const rawMessage = latestReading.Message || {};

        // Add device intelligence info at the top
        if (meter.utility && meter.utility !== 'Unknown') {
            html += `
                <div class="signal-advanced-item">
                    <span class="signal-advanced-label">Utility Type</span>
                    <span class="signal-advanced-value">${escapeHtml(meter.utility)}</span>
                </div>
            `;
        }
        if (meter.manufacturer && meter.manufacturer !== 'Unknown') {
            html += `
                <div class="signal-advanced-item">
                    <span class="signal-advanced-label">Manufacturer</span>
                    <span class="signal-advanced-value">${escapeHtml(meter.manufacturer)}</span>
                </div>
            `;
        }

        // Add aggregation stats
        html += `
            <div class="signal-advanced-item">
                <span class="signal-advanced-label">Total Readings</span>
                <span class="signal-advanced-value">${meter.readingCount}</span>
            </div>
            <div class="signal-advanced-item">
                <span class="signal-advanced-label">First Seen</span>
                <span class="signal-advanced-value">${new Date(meter.firstSeen).toLocaleTimeString()}</span>
            </div>
        `;

        // Add rate info if available
        if (meter.rate !== null) {
            html += `
                <div class="signal-advanced-item">
                    <span class="signal-advanced-label">Consumption Rate</span>
                    <span class="signal-advanced-value">${MeterAggregator.formatRate(meter.rate)}</span>
                </div>
            `;
        }

        // Display fields from the raw rtlamr message
        for (const [key, value] of Object.entries(rawMessage)) {
            if (value === null || value === undefined) continue;

            // Format the label
            const label = key.replace(/([A-Z])/g, ' $1').replace(/^./, s => s.toUpperCase()).trim();

            // Format the value
            let displayValue;
            if (Array.isArray(value)) {
                if (value.length > 10) {
                    displayValue = `[${value.length} values] ${value.slice(0, 5).join(', ')}...`;
                } else {
                    displayValue = value.join(', ');
                }
            } else if (typeof value === 'object') {
                displayValue = JSON.stringify(value);
            } else if (key === 'Consumption') {
                displayValue = `${value.toLocaleString()} units`;
            } else {
                displayValue = String(value);
            }

            html += `
                <div class="signal-advanced-item">
                    <span class="signal-advanced-label">${escapeHtml(label)}</span>
                    <span class="signal-advanced-value">${escapeHtml(displayValue)}</span>
                </div>
            `;
        }

        // Add message type if not in raw message
        if (!rawMessage.Type && meter.type) {
            html += `
                <div class="signal-advanced-item">
                    <span class="signal-advanced-label">Message Type</span>
                    <span class="signal-advanced-value">${escapeHtml(meter.type)}</span>
                </div>
            `;
        }

        return html;
    }

    /**
     * Toggle advanced panel on a card
     */
    function toggleAdvanced(button) {
        const card = button.closest('.signal-card');
        const panel = card.querySelector('.signal-advanced-panel');
        button.classList.toggle('open');
        panel.classList.toggle('open');
    }

    /**
     * Copy message content to clipboard
     */
    function copyMessage(button) {
        const card = button.closest('.signal-card');
        const message = card.querySelector('.signal-message');
        if (message) {
            navigator.clipboard.writeText(message.textContent).then(() => {
                showToast('Content copied');
            }).catch(() => {
                showToast('Unable to copy content', 'error');
            });
        }
    }

    /**
     * Mute an address (add to filter list)
     */
    function muteAddress(address) {
        const muted = JSON.parse(localStorage.getItem('mutedAddresses') || '[]');
        if (!muted.includes(address)) {
            muted.push(address);
            localStorage.setItem('mutedAddresses', JSON.stringify(muted));
            showToast(`Source ${address} hidden from view`);
            updateMutedIndicator();

            // Hide existing cards with this address
            document.querySelectorAll(`.signal-card[data-address="${address}"], .signal-card[data-callsign="${address}"], .signal-card[data-sensor-id="${address}"]`).forEach(card => {
                card.style.opacity = '0';
                card.style.transform = 'scale(0.95)';
                setTimeout(() => card.remove(), 200);
            });
        }
    }

    /**
     * Check if an address is muted
     */
    function isAddressMuted(address) {
        const muted = JSON.parse(localStorage.getItem('mutedAddresses') || '[]');
        return muted.includes(address);
    }

    /**
     * Unmute all addresses and refresh display
     */
    function unmuteAll() {
        localStorage.setItem('mutedAddresses', '[]');
        updateMutedIndicator();
        showToast('All sources unmuted');
        // Reload to re-display previously muted messages
        location.reload();
    }

    /**
     * Update the muted address count indicator in the sidebar
     */
    function updateMutedIndicator() {
        const muted = JSON.parse(localStorage.getItem('mutedAddresses') || '[]');
        const info = document.getElementById('mutedAddressInfo');
        const count = document.getElementById('mutedAddressCount');
        if (info && count) {
            count.textContent = muted.length;
            info.style.display = muted.length > 0 ? 'block' : 'none';
        }
    }

    /**
     * Show location on map (for APRS)
     */
    function showOnMap(lat, lon, label) {
        // Trigger custom event that map components can listen to
        const event = new CustomEvent('showOnMap', {
            detail: { lat, lon, label }
        });
        document.dispatchEvent(event);
        showToast(`Displaying ${label} location`);
    }

    /**
     * Show raw data modal for a station
     */
    function showStationRawData(element) {
        const callsign = element.dataset.callsign || 'Unknown';
        const rawData = element.dataset.raw || '';
        // Create or reuse modal
        let modal = document.getElementById('stationRawDataModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'stationRawDataModal';
            modal.className = 'station-raw-modal';
            modal.innerHTML = `
                <div class="station-raw-modal-backdrop"></div>
                <div class="station-raw-modal-content">
                    <div class="station-raw-modal-header">
                        <span class="station-raw-modal-title"></span>
                        <button class="station-raw-modal-close">&times;</button>
                    </div>
                    <div class="station-raw-modal-body">
                        <div class="station-raw-label">Raw Packet Data</div>
                        <pre class="station-raw-data-display"></pre>
                    </div>
                    <div class="station-raw-modal-footer">
                        <button class="station-raw-copy-btn">Copy to Clipboard</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);

            // Close handlers
            modal.querySelector('.station-raw-modal-backdrop').addEventListener('click', () => {
                modal.classList.remove('show');
            });
            modal.querySelector('.station-raw-modal-close').addEventListener('click', () => {
                modal.classList.remove('show');
            });
            modal.querySelector('.station-raw-copy-btn').addEventListener('click', () => {
                const rawText = modal.querySelector('.station-raw-data-display').textContent;
                navigator.clipboard.writeText(rawText).then(() => {
                    showToast('Raw data copied to clipboard');
                }).catch(() => {
                    showToast('Failed to copy', 'error');
                });
            });
        }

        // Populate modal
        modal.querySelector('.station-raw-modal-title').textContent = `Station: ${callsign}`;
        modal.querySelector('.station-raw-data-display').textContent = rawData || 'No raw data available';

        // Show modal
        modal.classList.add('show');
    }

    /**
     * Show signal details dialog for pager/sensor cards
     */
    function showSignalDetails(card) {
        const type = card.dataset.type;
        const msgData = JSON.parse(card.dataset.msgData || '{}');

        // Create or reuse modal
        let modal = document.getElementById('signalDetailsModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'signalDetailsModal';
            modal.className = 'signal-details-modal';
            modal.innerHTML = `
                <div class="signal-details-modal-backdrop"></div>
                <div class="signal-details-modal-content">
                    <div class="signal-details-modal-header">
                        <span class="signal-details-modal-title"></span>
                        <button class="signal-details-modal-close">&times;</button>
                    </div>
                    <div class="signal-details-modal-body"></div>
                    <div class="signal-details-modal-footer">
                        <button class="signal-details-copy-btn">Copy Raw Data</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);

            // Close handlers
            modal.querySelector('.signal-details-modal-backdrop').addEventListener('click', () => {
                modal.classList.remove('show');
            });
            modal.querySelector('.signal-details-modal-close').addEventListener('click', () => {
                modal.classList.remove('show');
            });
            modal.querySelector('.signal-details-copy-btn').addEventListener('click', () => {
                const rawEl = modal.querySelector('.signal-raw-data');
                if (rawEl) {
                    navigator.clipboard.writeText(rawEl.textContent).then(() => {
                        showToast('Raw data copied to clipboard');
                    }).catch(() => {
                        showToast('Failed to copy', 'error');
                    });
                }
            });

            // Close on escape key
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && modal.classList.contains('show')) {
                    modal.classList.remove('show');
                }
            });
        }

        // Build content based on card type
        let title = '';
        let bodyContent = '';

        if (type === 'message') {
            // Pager message details
            title = `${escapeHtml(msgData.protocol || 'Pager')} - Address ${escapeHtml(msgData.address || 'Unknown')}`;
            const stats = getAddressStats('pager', msgData.address);
            const seenCount = stats ? stats.count : 1;
            const msgType = getMsgTypeLabel(msgData);

            bodyContent = `
                <div class="signal-details-section">
                    <div class="signal-details-title">Message Content</div>
                    <div class="signal-details-message">${escapeHtml(msgData.message || '[No content]')}</div>
                </div>
                <div class="signal-details-section">
                    <div class="signal-details-title">Signal Details</div>
                    <div class="signal-details-grid">
                        <div class="signal-details-item">
                            <span class="signal-details-label">Protocol</span>
                            <span class="signal-details-value">${escapeHtml(msgData.protocol || 'Unknown')}</span>
                        </div>
                        <div class="signal-details-item">
                            <span class="signal-details-label">Address</span>
                            <span class="signal-details-value">${escapeHtml(msgData.address || 'Unknown')}</span>
                        </div>
                        ${msgData.function ? `
                        <div class="signal-details-item">
                            <span class="signal-details-label">Function</span>
                            <span class="signal-details-value">${escapeHtml(msgData.function)}</span>
                        </div>
                        ` : ''}
                        <div class="signal-details-item">
                            <span class="signal-details-label">Type</span>
                            <span class="signal-details-value">${escapeHtml(msgType)}</span>
                        </div>
                        <div class="signal-details-item">
                            <span class="signal-details-label">Seen</span>
                            <span class="signal-details-value">${seenCount} time${seenCount > 1 ? 's' : ''}</span>
                        </div>
                        <div class="signal-details-item">
                            <span class="signal-details-label">Timestamp</span>
                            <span class="signal-details-value">${escapeHtml(msgData.timestamp || 'Unknown')}</span>
                        </div>
                    </div>
                </div>
                ${msgData.raw ? `
                <div class="signal-details-section">
                    <div class="signal-details-title">Raw Data</div>
                    <pre class="signal-raw-data">${escapeHtml(msgData.raw)}</pre>
                </div>
                ` : ''}
            `;
        } else if (type === 'sensor') {
            // 433MHz sensor details
            title = `${escapeHtml(msgData.model || 'Sensor')} - ID ${escapeHtml(msgData.id || 'Unknown')}`;
            const stats = getAddressStats('sensor', msgData.id);
            const seenCount = stats ? stats.count : 1;
            const rssi = msgData.rssi || msgData.signal_strength || msgData.snr || msgData.noise || null;

            // Signal assessment section
            let signalAssessment = '';
            if (rssi !== null) {
                signalAssessment = createSignalAssessmentPanel(rssi, stats?.lastSeen ? (Date.now() - stats.firstSeen) / 1000 : null, seenCount);
            }

            // Signal guess section
            let signalGuessHtml = '';
            if (msgData.frequency && typeof SignalGuess !== 'undefined') {
                const frequencyHz = parseFloat(msgData.frequency) * 1_000_000;
                const signalGuess = SignalGuess.guessSignalType({
                    frequency_hz: frequencyHz,
                    modulation: msgData.modulation || null,
                    bandwidth_hz: msgData.bandwidth ? parseFloat(msgData.bandwidth) * 1000 : null,
                    rssi_dbm: rssi,
                    region: 'UK/EU'
                });
                if (signalGuess) {
                    const guessElement = SignalGuess.createGuessElement(signalGuess, { showAlternatives: true, compact: false });
                    signalGuessHtml = `
                        <div class="signal-details-section signal-guess-section">
                            <div class="signal-details-title">Signal Identification</div>
                            <div class="signal-guess-content">${guessElement.outerHTML}</div>
                        </div>
                    `;
                }
            }

            // Sensor readings
            let sensorReadings = '';
            const readings = [];
            if (msgData.temperature !== undefined) readings.push(`<div class="signal-details-item"><span class="signal-details-label">Temperature</span><span class="signal-details-value">${msgData.temperature}°${msgData.temperature_unit || 'F'}</span></div>`);
            if (msgData.humidity !== undefined) readings.push(`<div class="signal-details-item"><span class="signal-details-label">Humidity</span><span class="signal-details-value">${msgData.humidity}%</span></div>`);
            if (msgData.battery !== undefined) readings.push(`<div class="signal-details-item"><span class="signal-details-label">Battery</span><span class="signal-details-value">${msgData.battery}</span></div>`);
            if (msgData.pressure !== undefined) readings.push(`<div class="signal-details-item"><span class="signal-details-label">Pressure</span><span class="signal-details-value">${msgData.pressure} ${msgData.pressure_unit || 'hPa'}</span></div>`);
            if (msgData.wind_speed !== undefined) readings.push(`<div class="signal-details-item"><span class="signal-details-label">Wind Speed</span><span class="signal-details-value">${msgData.wind_speed} ${msgData.wind_unit || 'mph'}</span></div>`);
            if (msgData.rain !== undefined) readings.push(`<div class="signal-details-item"><span class="signal-details-label">Rain</span><span class="signal-details-value">${msgData.rain} ${msgData.rain_unit || 'mm'}</span></div>`);
            if (msgData.state !== undefined) readings.push(`<div class="signal-details-item"><span class="signal-details-label">State</span><span class="signal-details-value">${escapeHtml(msgData.state)}</span></div>`);

            if (readings.length > 0) {
                sensorReadings = `
                    <div class="signal-details-section">
                        <div class="signal-details-title">Sensor Readings</div>
                        <div class="signal-details-grid">${readings.join('')}</div>
                    </div>
                `;
            }

            bodyContent = `
                ${signalAssessment}
                ${signalGuessHtml}
                ${sensorReadings}
                <div class="signal-details-section">
                    <div class="signal-details-title">Sensor Details</div>
                    <div class="signal-details-grid">
                        <div class="signal-details-item">
                            <span class="signal-details-label">Model</span>
                            <span class="signal-details-value">${escapeHtml(msgData.model || 'Unknown')}</span>
                        </div>
                        <div class="signal-details-item">
                            <span class="signal-details-label">ID</span>
                            <span class="signal-details-value">${escapeHtml(msgData.id || 'N/A')}</span>
                        </div>
                        ${msgData.channel ? `
                        <div class="signal-details-item">
                            <span class="signal-details-label">Channel</span>
                            <span class="signal-details-value">${msgData.channel}</span>
                        </div>
                        ` : ''}
                        ${msgData.frequency ? `
                        <div class="signal-details-item">
                            <span class="signal-details-label">Frequency</span>
                            <span class="signal-details-value">${msgData.frequency} MHz</span>
                        </div>
                        ` : ''}
                        <div class="signal-details-item">
                            <span class="signal-details-label">Seen</span>
                            <span class="signal-details-value">${seenCount} time${seenCount > 1 ? 's' : ''}</span>
                        </div>
                        <div class="signal-details-item">
                            <span class="signal-details-label">Timestamp</span>
                            <span class="signal-details-value">${escapeHtml(msgData.timestamp || 'Unknown')}</span>
                        </div>
                    </div>
                </div>
                ${msgData.raw ? `
                <div class="signal-details-section">
                    <div class="signal-details-title">Raw Data</div>
                    <pre class="signal-raw-data">${escapeHtml(typeof msgData.raw === 'object' ? JSON.stringify(msgData.raw, null, 2) : msgData.raw)}</pre>
                </div>
                ` : ''}
            `;
        }

        // Populate modal
        modal.querySelector('.signal-details-modal-title').textContent = title;
        modal.querySelector('.signal-details-modal-body').innerHTML = bodyContent;

        // Show/hide copy button based on whether there's raw data
        const copyBtn = modal.querySelector('.signal-details-copy-btn');
        copyBtn.style.display = (msgData.raw) ? '' : 'none';

        // Show modal
        modal.classList.add('show');
    }

    /**
     * Show toast notification
     */
    function showToast(message, type = 'success') {
        let toast = document.getElementById('signalToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'signalToast';
            toast.className = 'signal-toast';
            document.body.appendChild(toast);
        }

        toast.textContent = message;
        toast.className = 'signal-toast ' + type;
        toast.offsetHeight; // Force reflow
        toast.classList.add('show');

        setTimeout(() => {
            toast.classList.remove('show');
        }, 2500);
    }

    /**
     * Create pager filter bar with protocol and message type filters
     */
    function createPagerFilterBar(outputContainer, options = {}) {
        const filterBar = document.createElement('div');
        filterBar.className = 'signal-filter-bar';
        filterBar.id = 'pagerFilterBar';

        filterBar.innerHTML = `
            <span class="signal-filter-label">Status</span>
            <button class="signal-filter-btn active" data-filter="status" data-value="all">
                <span class="filter-dot"></span>
                All
                <span class="signal-filter-count" data-count="all">0</span>
            </button>
            <button class="signal-filter-btn" data-filter="status" data-value="new">
                <span class="filter-dot"></span>
                New
                <span class="signal-filter-count" data-count="new">0</span>
            </button>
            <button class="signal-filter-btn" data-filter="status" data-value="repeated">
                <span class="filter-dot"></span>
                Repeated
                <span class="signal-filter-count" data-count="repeated">0</span>
            </button>
            <button class="signal-filter-btn" data-filter="status" data-value="burst">
                <span class="filter-dot"></span>
                Burst
                <span class="signal-filter-count" data-count="burst">0</span>
            </button>

            <span class="signal-filter-divider"></span>

            <span class="signal-filter-label">Protocol</span>
            <button class="signal-filter-btn protocol-btn active" data-filter="protocol" data-value="all">All</button>
            <button class="signal-filter-btn protocol-btn" data-filter="protocol" data-value="pocsag">POCSAG</button>
            <button class="signal-filter-btn protocol-btn" data-filter="protocol" data-value="flex">FLEX</button>

            <span class="signal-filter-divider"></span>

            <span class="signal-filter-label">Type</span>
            <button class="signal-filter-btn type-btn active" data-filter="msgType" data-value="all">All</button>
            <button class="signal-filter-btn type-btn" data-filter="msgType" data-value="alpha">Alpha</button>
            <button class="signal-filter-btn type-btn" data-filter="msgType" data-value="numeric">Numeric</button>
            <button class="signal-filter-btn type-btn" data-filter="msgType" data-value="tone">Tone</button>

            <span class="signal-filter-divider"></span>

            <div class="signal-search-container">
                <input type="text" class="signal-search-input" id="pagerSearchInput" placeholder="Search address or content..." />
            </div>
        `;

        // Add click handlers for filter buttons
        filterBar.querySelectorAll('.signal-filter-btn[data-filter="status"]').forEach(btn => {
            btn.addEventListener('click', () => {
                filterBar.querySelectorAll('.signal-filter-btn[data-filter="status"]').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                state.filters.status = btn.dataset.value;
                applyAllFilters(outputContainer);
            });
        });

        filterBar.querySelectorAll('.signal-filter-btn[data-filter="protocol"]').forEach(btn => {
            btn.addEventListener('click', () => {
                filterBar.querySelectorAll('.signal-filter-btn[data-filter="protocol"]').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                state.filters.protocol = btn.dataset.value;
                applyAllFilters(outputContainer);
            });
        });

        filterBar.querySelectorAll('.signal-filter-btn[data-filter="msgType"]').forEach(btn => {
            btn.addEventListener('click', () => {
                filterBar.querySelectorAll('.signal-filter-btn[data-filter="msgType"]').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                state.filters.msgType = btn.dataset.value;
                applyAllFilters(outputContainer);
            });
        });

        // Add search handler with debounce
        const searchInput = filterBar.querySelector('#pagerSearchInput');
        let searchTimeout;
        searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                state.filters.search = e.target.value.toLowerCase();
                applyAllFilters(outputContainer);
            }, 200);
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            // Only when not typing in an input
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

            if (e.key === '/') {
                e.preventDefault();
                searchInput.focus();
            }
        });

        return filterBar;
    }

    /**
     * Apply all filters (status, protocol, msgType, search)
     */
    function applyAllFilters(container) {
        const cards = container.querySelectorAll('.signal-card');
        let visibleCount = 0;
        const counts = {
            all: 0,
            new: 0,
            repeated: 0,
            burst: 0,
            baseline: 0,
            emergency: 0
        };

        cards.forEach(card => {
            const cardStatus = card.dataset.status;
            const cardProtocol = card.dataset.protocol;
            const cardMsgType = card.dataset.msgType;
            const cardAddress = card.dataset.address || '';
            const cardContent = card.querySelector('.signal-message')?.textContent || '';

            // Count all cards by status
            counts.all++;
            if (counts.hasOwnProperty(cardStatus)) {
                counts[cardStatus]++;
            }

            // Check all filters
            const statusMatch = state.filters.status === 'all' || cardStatus === state.filters.status;
            const protocolMatch = state.filters.protocol === 'all' || cardProtocol === state.filters.protocol;
            const typeMatch = state.filters.msgType === 'all' || cardMsgType === state.filters.msgType;
            const searchMatch = !state.filters.search ||
                cardAddress.toLowerCase().includes(state.filters.search) ||
                cardContent.toLowerCase().includes(state.filters.search);

            if (statusMatch && protocolMatch && typeMatch && searchMatch) {
                card.classList.remove('hidden');
                visibleCount++;
            } else {
                card.classList.add('hidden');
            }
        });

        // Update count badges - find filter bar in multiple possible locations
        const filterBars = [
            document.getElementById('filterBarContainer')?.querySelector('.signal-filter-bar'),
            document.getElementById('aprsFilterBarContainer')?.querySelector('.signal-filter-bar')
        ].filter(Boolean);

        filterBars.forEach(filterBar => {
            Object.keys(counts).forEach(key => {
                const badge = filterBar.querySelector(`[data-count="${key}"]`);
                if (badge) {
                    badge.textContent = counts[key];
                }
            });
        });

        // Show/hide empty state
        const emptyState = container.querySelector('.signal-empty-state');
        if (emptyState) {
            emptyState.style.display = visibleCount === 0 && cards.length > 0 ? 'block' : 'none';
        }

        state.counts = counts;
    }

    /**
     * Initialize filter bar (legacy support)
     */
    function initFilterBar(container, options = {}) {
        return createPagerFilterBar(container, options);
    }

    /**
     * Apply current filters to cards (legacy support)
     */
    function applyFilters(container) {
        applyAllFilters(container);
    }

    /**
     * Update filter counts
     */
    function updateCounts(container) {
        applyAllFilters(container);
        return state.counts;
    }

    /**
     * Create APRS filter bar with status and packet type filters
     */
    function createAprsFilterBar(outputContainer, options = {}) {
        const filterBar = document.createElement('div');
        filterBar.className = 'signal-filter-bar signal-filter-bar-compact';
        filterBar.id = 'aprsFilterBar';

        filterBar.innerHTML = `
            <button class="signal-filter-btn active" data-filter="status" data-value="all">
                <span class="filter-dot"></span>
                All
                <span class="signal-filter-count" data-count="all">0</span>
            </button>
            <button class="signal-filter-btn" data-filter="status" data-value="new">
                <span class="filter-dot"></span>
                New
                <span class="signal-filter-count" data-count="new">0</span>
            </button>
            <button class="signal-filter-btn" data-filter="status" data-value="repeated">
                <span class="filter-dot"></span>
                Repeated
                <span class="signal-filter-count" data-count="repeated">0</span>
            </button>

            <span class="signal-filter-divider"></span>

            <span class="signal-filter-label">Type</span>
            <button class="signal-filter-btn type-btn active" data-filter="packetType" data-value="all">All</button>
            <button class="signal-filter-btn type-btn" data-filter="packetType" data-value="position">Position</button>
            <button class="signal-filter-btn type-btn" data-filter="packetType" data-value="weather">Weather</button>
            <button class="signal-filter-btn type-btn" data-filter="packetType" data-value="message">Message</button>

            <div class="signal-search-container">
                <input type="text" class="signal-search-input" id="aprsSearchInput" placeholder="Search callsign..." />
            </div>
        `;

        // Store filter state specific to APRS
        const aprsFilters = { status: 'all', packetType: 'all', search: '' };

        // Apply filters function for APRS
        const applyAprsFilters = () => {
            const cards = outputContainer.querySelectorAll('.signal-card');
            let visibleCount = 0;
            const counts = { all: 0, new: 0, repeated: 0, burst: 0, baseline: 0, emergency: 0 };

            cards.forEach(card => {
                const cardStatus = card.dataset.status;
                const cardType = card.dataset.packetType || card.querySelector('.signal-msg-type')?.textContent?.toLowerCase() || '';
                const cardCallsign = card.dataset.callsign || '';

                counts.all++;
                if (counts.hasOwnProperty(cardStatus)) counts[cardStatus]++;

                const statusMatch = aprsFilters.status === 'all' || cardStatus === aprsFilters.status;
                const typeMatch = aprsFilters.packetType === 'all' || cardType.includes(aprsFilters.packetType);
                const searchMatch = !aprsFilters.search || cardCallsign.toLowerCase().includes(aprsFilters.search);

                if (statusMatch && typeMatch && searchMatch) {
                    card.classList.remove('hidden');
                    visibleCount++;
                } else {
                    card.classList.add('hidden');
                }
            });

            // Update count badges
            Object.keys(counts).forEach(key => {
                const badge = filterBar.querySelector(`[data-count="${key}"]`);
                if (badge) badge.textContent = counts[key];
            });
        };

        // Status filter handlers
        filterBar.querySelectorAll('.signal-filter-btn[data-filter="status"]').forEach(btn => {
            btn.addEventListener('click', () => {
                filterBar.querySelectorAll('.signal-filter-btn[data-filter="status"]').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                aprsFilters.status = btn.dataset.value;
                applyAprsFilters();
            });
        });

        // Packet type filter handlers
        filterBar.querySelectorAll('.signal-filter-btn[data-filter="packetType"]').forEach(btn => {
            btn.addEventListener('click', () => {
                filterBar.querySelectorAll('.signal-filter-btn[data-filter="packetType"]').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                aprsFilters.packetType = btn.dataset.value;
                applyAprsFilters();
            });
        });

        // Search handler with debounce
        const searchInput = filterBar.querySelector('#aprsSearchInput');
        let searchTimeout;
        searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                aprsFilters.search = e.target.value.toLowerCase();
                applyAprsFilters();
            }, 200);
        });

        // Store applyFilters reference for external calls
        filterBar.applyFilters = applyAprsFilters;

        return filterBar;
    }

    /**
     * Create Sensor (433MHz) filter bar
     */
    function createSensorFilterBar(outputContainer, options = {}) {
        const filterBar = document.createElement('div');
        filterBar.className = 'signal-filter-bar';
        filterBar.id = 'sensorFilterBar';

        filterBar.innerHTML = `
            <span class="signal-filter-label">Status</span>
            <button class="signal-filter-btn active" data-filter="status" data-value="all">
                <span class="filter-dot"></span>
                All
                <span class="signal-filter-count" data-count="all">0</span>
            </button>
            <button class="signal-filter-btn" data-filter="status" data-value="new">
                <span class="filter-dot"></span>
                New
                <span class="signal-filter-count" data-count="new">0</span>
            </button>
            <button class="signal-filter-btn" data-filter="status" data-value="repeated">
                <span class="filter-dot"></span>
                Repeated
                <span class="signal-filter-count" data-count="repeated">0</span>
            </button>
            <button class="signal-filter-btn" data-filter="status" data-value="burst">
                <span class="filter-dot"></span>
                Burst
                <span class="signal-filter-count" data-count="burst">0</span>
            </button>

            <span class="signal-filter-divider"></span>

            <div class="signal-search-container">
                <input type="text" class="signal-search-input" id="sensorSearchInput" placeholder="Search model or ID..." />
            </div>
        `;

        // Store filter state for sensors
        const sensorFilters = { status: 'all', search: '' };

        // Apply filters function for sensors
        const applySensorFilters = () => {
            const cards = outputContainer.querySelectorAll('.signal-card');
            let visibleCount = 0;
            const counts = { all: 0, new: 0, repeated: 0, burst: 0, baseline: 0, emergency: 0 };

            cards.forEach(card => {
                const cardStatus = card.dataset.status;
                const cardProtocol = card.dataset.protocol || '';
                const cardSensorId = card.dataset.sensorId || '';

                counts.all++;
                if (counts.hasOwnProperty(cardStatus)) counts[cardStatus]++;

                const statusMatch = sensorFilters.status === 'all' || cardStatus === sensorFilters.status;
                const searchMatch = !sensorFilters.search ||
                    cardProtocol.toLowerCase().includes(sensorFilters.search) ||
                    cardSensorId.toLowerCase().includes(sensorFilters.search);

                if (statusMatch && searchMatch) {
                    card.classList.remove('hidden');
                    visibleCount++;
                } else {
                    card.classList.add('hidden');
                }
            });

            // Update count badges
            Object.keys(counts).forEach(key => {
                const badge = filterBar.querySelector(`[data-count="${key}"]`);
                if (badge) badge.textContent = counts[key];
            });
        };

        // Status filter handlers
        filterBar.querySelectorAll('.signal-filter-btn[data-filter="status"]').forEach(btn => {
            btn.addEventListener('click', () => {
                filterBar.querySelectorAll('.signal-filter-btn[data-filter="status"]').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                sensorFilters.status = btn.dataset.value;
                applySensorFilters();
            });
        });

        // Search handler with debounce
        const searchInput = filterBar.querySelector('#sensorSearchInput');
        let searchTimeout;
        searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                sensorFilters.search = e.target.value.toLowerCase();
                applySensorFilters();
            }, 200);
        });

        // Store applyFilters reference for external calls
        filterBar.applyFilters = applySensorFilters;

        return filterBar;
    }

    /**
     * Update relative timestamps on cards
     */
    function updateTimestamps(container) {
        container.querySelectorAll('.signal-timestamp[data-timestamp]').forEach(el => {
            const timestamp = el.dataset.timestamp;
            if (timestamp) {
                el.textContent = formatRelativeTime(timestamp);
            }
        });
    }

    // Public API
    return {
        // Card creators
        createPagerCard,
        createAprsCard,
        createSensorCard,
        createAcarsCard,
        createMeterCard,
        createAggregatedMeterCard,
        updateAggregatedMeterCard,

        // Signal classification
        SignalClassification,
        createSignalIndicator,
        createSignalAssessmentPanel,

        // UI interactions
        toggleAdvanced,
        copyMessage,
        muteAddress,
        isAddressMuted,
        unmuteAll,
        updateMutedIndicator,
        showOnMap,
        showStationRawData,
        showSignalDetails,
        showToast,

        // Filter bar
        createPagerFilterBar,
        createAprsFilterBar,
        createSensorFilterBar,
        initFilterBar,
        applyFilters,
        applyAllFilters,
        updateCounts,
        updateTimestamps,

        // Address tracking
        trackAddress,
        getAddressStats,
        clearAddressHistory,

        // Utilities
        escapeHtml,
        formatRelativeTime,
        determineStatus,
        getProtoClass,

        // State
        state,
        addressHistory
    };
})();

// Make globally available
window.SignalCards = SignalCards;
