/**
 * Meter Aggregator Component
 * Client-side aggregation for rtlamr meter readings
 * Groups readings by meter ID and tracks consumption history
 */

const MeterAggregator = (function() {
    'use strict';

    // Configuration
    const CONFIG = {
        maxHistoryAge: 60 * 60 * 1000,  // 60 minutes
        maxHistoryLength: 50,            // Max readings to keep per meter
        rateWindowMs: 30 * 60 * 1000     // 30 minutes for rate calculation
    };

    // Storage for aggregated meters
    // Map<meterId, MeterData>
    const meters = new Map();

    /**
     * MeterData structure:
     * {
     *   id: string,
     *   type: string,
     *   utility: string,
     *   manufacturer: string,
     *   firstSeen: number (timestamp),
     *   lastSeen: number (timestamp),
     *   readingCount: number,
     *   latestReading: object (full reading data),
     *   history: Array<{timestamp, consumption, raw}>,
     *   delta: number | null (change from previous reading),
     *   rate: number | null (units per hour)
     * }
     */

    /**
     * Ingest a new meter reading
     * @param {Object} data - The raw meter reading data
     * @returns {Object} - { meter: MeterData, isNew: boolean }
     */
    function ingest(data) {
        const msgData = data.Message || {};
        // rtlamr names the meter's number per message type: ID (SCM, R900),
        // EndpointID (SCM+), ERTSerialNumber (IDM, NetIDM). Reading ID alone
        // put every SCM+ and IDM meter on one "Unknown" card.
        const meterId = String(msgData.ID ?? msgData.EndpointID ?? msgData.ERTSerialNumber ?? data.id ?? 'Unknown');
        const timestamp = Date.now();
        const consumption = msgData.Consumption ?? msgData.LastConsumptionCount ?? data.consumption;

        // Get meter type info if available
        const meterInfo = typeof getMeterTypeInfo === 'function'
            ? getMeterTypeInfo(msgData.EndpointType, data.Type)
            : { utility: 'Unknown', manufacturer: 'Unknown' };

        const existing = meters.get(meterId);
        const isNew = !existing;

        if (isNew) {
            // Create new meter entry
            const meter = {
                id: meterId,
                type: data.Type || 'Unknown',
                utility: meterInfo.utility,
                manufacturer: meterInfo.manufacturer,
                firstSeen: timestamp,
                lastSeen: timestamp,
                readingCount: 1,
                latestReading: data,
                history: [{
                    timestamp: timestamp,
                    consumption: consumption,
                    raw: data
                }],
                delta: null,
                rate: null
            };
            meters.set(meterId, meter);
            return { meter, isNew: true };
        }

        // Update existing meter
        const previousConsumption = existing.history.length > 0
            ? existing.history[existing.history.length - 1].consumption
            : null;

        // Add to history
        existing.history.push({
            timestamp: timestamp,
            consumption: consumption,
            raw: data
        });

        // Prune old history
        pruneHistory(existing);

        // Calculate delta (change from previous reading)
        if (previousConsumption !== null && consumption !== undefined && consumption !== null) {
            existing.delta = consumption - previousConsumption;
        } else {
            existing.delta = null;
        }

        // Calculate rate (units per hour)
        existing.rate = calculateRate(existing);

        // Update meter data
        existing.lastSeen = timestamp;
        existing.readingCount++;
        existing.latestReading = data;
        existing.type = data.Type || existing.type;
        if (meterInfo.utility !== 'Unknown') existing.utility = meterInfo.utility;
        if (meterInfo.manufacturer !== 'Unknown') existing.manufacturer = meterInfo.manufacturer;

        return { meter: existing, isNew: false };
    }

    /**
     * Prune history older than maxHistoryAge and beyond maxHistoryLength
     */
    function pruneHistory(meter) {
        const cutoff = Date.now() - CONFIG.maxHistoryAge;

        // Remove old entries
        meter.history = meter.history.filter(h => h.timestamp >= cutoff);

        // Limit length
        if (meter.history.length > CONFIG.maxHistoryLength) {
            meter.history = meter.history.slice(-CONFIG.maxHistoryLength);
        }
    }

    /**
     * Calculate consumption rate over the rate window
     * @returns {number|null} Units per hour, or null if insufficient data
     */
    function calculateRate(meter) {
        if (meter.history.length < 2) return null;

        const now = Date.now();
        const windowStart = now - CONFIG.rateWindowMs;

        // Find readings within the rate window
        const recentHistory = meter.history.filter(h => h.timestamp >= windowStart);
        if (recentHistory.length < 2) return null;

        const oldest = recentHistory[0];
        const newest = recentHistory[recentHistory.length - 1];

        // Need both to have valid consumption values
        if (oldest.consumption === undefined || oldest.consumption === null ||
            newest.consumption === undefined || newest.consumption === null) {
            return null;
        }

        const consumptionDiff = newest.consumption - oldest.consumption;
        const timeDiffHours = (newest.timestamp - oldest.timestamp) / (1000 * 60 * 60);

        // Under a minute of readings extrapolates noise to an hourly rate
        if (timeDiffHours < 1 / 60) return null;

        return consumptionDiff / timeDiffHours;
    }

    /**
     * Get consumption deltas for sparkline display
     * @returns {Array<{timestamp, delta}>}
     */
    function getConsumptionDeltas(meter) {
        const deltas = [];
        for (let i = 1; i < meter.history.length; i++) {
            const prev = meter.history[i - 1];
            const curr = meter.history[i];
            if (prev.consumption !== undefined && prev.consumption !== null &&
                curr.consumption !== undefined && curr.consumption !== null) {
                deltas.push({
                    timestamp: curr.timestamp,
                    delta: curr.consumption - prev.consumption
                });
            }
        }
        return deltas;
    }

    /**
     * Get a meter by ID
     * @param {string} id
     * @returns {Object|null}
     */
    function getMeter(id) {
        return meters.get(String(id)) || null;
    }

    /**
     * Get all meters
     * @returns {Array<Object>}
     */
    function getAllMeters() {
        return Array.from(meters.values());
    }

    /**
     * Get meter count
     * @returns {number}
     */
    function getCount() {
        return meters.size;
    }

    /**
     * Clear all aggregated data
     */
    function clear() {
        meters.clear();
    }

    /**
     * Get time since last reading for a meter
     * @param {Object} meter
     * @returns {string}
     */
    function getTimeSinceLastReading(meter) {
        const diff = Date.now() - meter.lastSeen;
        const seconds = Math.floor(diff / 1000);
        if (seconds < 60) return 'Just now';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes} minute${minutes !== 1 ? 's' : ''} ago`;
        const hours = Math.floor(minutes / 60);
        return `${hours} hour${hours !== 1 ? 's' : ''} ago`;
    }

    /**
     * Format rate for display
     * @param {number|null} rate
     * @returns {string}
     */
    function formatRate(rate) {
        if (rate === null || rate === undefined || isNaN(rate)) {
            return '--';
        }
        // Format based on magnitude
        const absRate = Math.abs(rate);
        if (absRate >= 100) {
            return rate.toFixed(0) + '/hr';
        } else if (absRate >= 1) {
            return rate.toFixed(1) + '/hr';
        } else {
            return rate.toFixed(2) + '/hr';
        }
    }

    /**
     * Format delta for display
     * @param {number|null} delta
     * @returns {string}
     */
    function formatDelta(delta) {
        if (delta === null || delta === undefined || isNaN(delta)) {
            return '--';
        }
        const sign = delta >= 0 ? '+' : '';
        return sign + delta.toLocaleString();
    }

    // Public API
    return {
        ingest,
        getMeter,
        getAllMeters,
        getCount,
        clear,
        getConsumptionDeltas,
        getTimeSinceLastReading,
        formatRate,
        formatDelta,
        CONFIG
    };
})();

// Make globally available
window.MeterAggregator = MeterAggregator;
