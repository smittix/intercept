/**
 * Intercept - Core Utility Functions
 * Pure utility functions with no DOM dependencies
 */

// ============== HTML ESCAPING ==============

/**
 * Escape HTML to prevent XSS
 * @param {string} text - Text to escape
 * @returns {string} Escaped HTML
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Escape text for use in HTML attributes (especially onclick handlers)
 * @param {string} text - Text to escape
 * @returns {string} Escaped attribute value
 */
function escapeAttr(text) {
    if (text === null || text === undefined) return '';
    var s = String(text);
    s = s.replace(/&/g, '&amp;');
    s = s.replace(/'/g, '&#39;');
    s = s.replace(/"/g, '&quot;');
    s = s.replace(/</g, '&lt;');
    s = s.replace(/>/g, '&gt;');
    return s;
}

// ============== VALIDATION ==============

/**
 * Validate MAC address format (XX:XX:XX:XX:XX:XX)
 * @param {string} mac - MAC address to validate
 * @returns {boolean} True if valid
 */
function isValidMac(mac) {
    return /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(mac);
}

/**
 * Validate WiFi channel (1-200 covers all bands)
 * @param {string|number} ch - Channel number
 * @returns {boolean} True if valid
 */
function isValidChannel(ch) {
    const num = parseInt(ch, 10);
    return !isNaN(num) && num >= 1 && num <= 200;
}

// ============== TIME FORMATTING ==============

/**
 * Global time preferences — timezone and 12h/24h format.
 * Stored in localStorage, used by all modes.
 */
const InterceptTime = (function() {
    const TZ_MAP = {
        'UTC': 'UTC',
        'local': undefined,
        'US/Eastern': 'America/New_York',
        'US/Central': 'America/Chicago',
        'US/Mountain': 'America/Denver',
        'US/Pacific': 'America/Los_Angeles',
    };

    const TZ_LABELS = {
        'UTC': 'UTC',
        'local': '',
        'US/Eastern': 'ET',
        'US/Central': 'CT',
        'US/Mountain': 'MT',
        'US/Pacific': 'PT',
    };

    let _timezone = localStorage.getItem('interceptTimezone') || 'US/Eastern';
    let _hour12 = (localStorage.getItem('interceptHour12') || 'true') === 'true';
    const _listeners = [];

    function getTimezone() { return _timezone; }
    function getHour12() { return _hour12; }
    function getIANA() { return TZ_MAP[_timezone]; }
    function getLabel() { return TZ_LABELS[_timezone] || ''; }

    function setTimezone(tz) {
        if (!TZ_MAP.hasOwnProperty(tz)) return;
        _timezone = tz;
        localStorage.setItem('interceptTimezone', tz);
        // Migrate weather-sat specific key
        localStorage.setItem('wxsatTimezone', tz);
        _notify();
    }

    function setHour12(val) {
        _hour12 = !!val;
        localStorage.setItem('interceptHour12', _hour12 ? 'true' : 'false');
        _notify();
    }

    function onChange(fn) { _listeners.push(fn); }
    function _notify() { _listeners.forEach(fn => { try { fn(); } catch(e) { console.error(e); } }); }

    /**
     * Format a Date or ISO string for the global timezone.
     * @param {Date|string} input - Date object or ISO string
     * @param {object} [extraOpts] - Additional Intl.DateTimeFormat options
     * @returns {string}
     */
    function format(input, extraOpts) {
        if (!input) return '--';
        try {
            const date = typeof input === 'string' ? new Date(input) : input;
            if (isNaN(date.getTime())) return typeof input === 'string' ? input : '--';
            const opts = { hour12: _hour12, ...extraOpts };
            const iana = getIANA();
            if (iana) opts.timeZone = iana;
            return date.toLocaleString(undefined, opts);
        } catch { return typeof input === 'string' ? input : '--'; }
    }

    /** HH:MM (or h:MM AM/PM) */
    function shortTime(input) {
        return format(input, { hour: '2-digit', minute: '2-digit' });
    }

    /** HH:MM:SS */
    function fullTime(input) {
        return format(input, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    /** Mon 25, 14:30 (or 2:30 PM) */
    function dateTime(input) {
        return format(input, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    }

    /** Mon 25, 2026 */
    function dateOnly(input) {
        const iana = getIANA();
        const opts = { year: 'numeric', month: 'short', day: 'numeric' };
        if (iana) opts.timeZone = iana;
        try {
            const date = typeof input === 'string' ? new Date(input) : input;
            return date.toLocaleDateString(undefined, opts);
        } catch { return '--'; }
    }

    /** Short label like " ET" or " UTC" for appending to times */
    function tzSuffix() {
        const l = getLabel();
        return l ? ' ' + l : '';
    }

    return {
        getTimezone, getHour12, getIANA, getLabel,
        setTimezone, setHour12, onChange,
        format, shortTime, fullTime, dateTime, dateOnly, tzSuffix,
        TZ_MAP, TZ_LABELS,
    };
})();

/**
 * Get relative time string from timestamp
 * @param {string} timestamp - Time string in HH:MM:SS format
 * @returns {string} Relative time like "5s ago", "2m ago"
 */
function getRelativeTime(timestamp) {
    if (!timestamp) return '';
    const now = new Date();
    const parts = timestamp.split(':');
    const msgTime = new Date();
    msgTime.setHours(parseInt(parts[0]), parseInt(parts[1]), parseInt(parts[2]));

    const diff = Math.floor((now - msgTime) / 1000);
    if (diff < 5) return 'just now';
    if (diff < 60) return diff + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    return timestamp;
}

/**
 * Format UTC time string
 * @param {Date} date - Date object
 * @returns {string} UTC time in HH:MM:SS format
 */
function formatUtcTime(date) {
    return date.toISOString().substring(11, 19);
}

// ============== DISTANCE CALCULATIONS ==============

/**
 * Calculate distance between two points in nautical miles
 * Uses Haversine formula
 * @param {number} lat1 - Latitude of first point
 * @param {number} lon1 - Longitude of first point
 * @param {number} lat2 - Latitude of second point
 * @param {number} lon2 - Longitude of second point
 * @returns {number} Distance in nautical miles
 */
function calculateDistanceNm(lat1, lon1, lat2, lon2) {
    const R = 3440.065;  // Earth radius in nautical miles
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
}

/**
 * Calculate distance between two points in kilometers
 * @param {number} lat1 - Latitude of first point
 * @param {number} lon1 - Longitude of first point
 * @param {number} lat2 - Latitude of second point
 * @param {number} lon2 - Longitude of second point
 * @returns {number} Distance in kilometers
 */
function calculateDistanceKm(lat1, lon1, lat2, lon2) {
    const R = 6371;  // Earth radius in kilometers
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
}

// ============== FILE OPERATIONS ==============

/**
 * Download content as a file
 * @param {string} content - File content
 * @param {string} filename - Name for the downloaded file
 * @param {string} type - MIME type
 */
function downloadFile(content, filename, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

// ============== FREQUENCY FORMATTING ==============

/**
 * Format frequency value with proper units
 * @param {number} freqMhz - Frequency in MHz
 * @param {number} decimals - Number of decimal places (default 3)
 * @returns {string} Formatted frequency string
 */
function formatFrequency(freqMhz, decimals = 3) {
    return freqMhz.toFixed(decimals) + ' MHz';
}

/**
 * Parse frequency string to MHz
 * @param {string} freqStr - Frequency string (e.g., "118.0", "118.0 MHz")
 * @returns {number} Frequency in MHz
 */
function parseFrequency(freqStr) {
    return parseFloat(freqStr.replace(/[^\d.-]/g, ''));
}

// ============== LOCAL STORAGE HELPERS ==============

/**
 * Get item from localStorage with JSON parsing
 * @param {string} key - Storage key
 * @param {*} defaultValue - Default value if key doesn't exist
 * @returns {*} Parsed value or default
 */
function getStorageItem(key, defaultValue = null) {
    const saved = localStorage.getItem(key);
    if (saved === null) return defaultValue;
    try {
        return JSON.parse(saved);
    } catch (e) {
        return saved;
    }
}

/**
 * Set item in localStorage with JSON stringification
 * @param {string} key - Storage key
 * @param {*} value - Value to store
 */
function setStorageItem(key, value) {
    if (typeof value === 'object') {
        localStorage.setItem(key, JSON.stringify(value));
    } else {
        localStorage.setItem(key, value);
    }
}

// ============== ARRAY/OBJECT UTILITIES ==============

/**
 * Debounce function execution
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Throttle function execution
 * @param {Function} func - Function to throttle
 * @param {number} limit - Time limit in milliseconds
 * @returns {Function} Throttled function
 */
function throttle(func, limit) {
    let inThrottle;
    return function executedFunction(...args) {
        if (!inThrottle) {
            func(...args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

// ============== NUMBER FORMATTING ==============

/**
 * Format large numbers with K/M suffixes
 * @param {number} num - Number to format
 * @returns {string} Formatted string
 */
function formatNumber(num) {
    if (num >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    }
    if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return num.toString();
}

/**
 * Clamp a number between min and max
 * @param {number} num - Number to clamp
 * @param {number} min - Minimum value
 * @param {number} max - Maximum value
 * @returns {number} Clamped value
 */
function clamp(num, min, max) {
    return Math.min(Math.max(num, min), max);
}

/**
 * Map a value from one range to another
 * @param {number} value - Value to map
 * @param {number} inMin - Input range minimum
 * @param {number} inMax - Input range maximum
 * @param {number} outMin - Output range minimum
 * @param {number} outMax - Output range maximum
 * @returns {number} Mapped value
 */
function mapRange(value, inMin, inMax, outMin, outMax) {
    return (value - inMin) * (outMax - outMin) / (inMax - inMin) + outMin;
}

// ============== ICON SYSTEM ==============
// Minimal SVG icons. Each returns HTML string.
// Designed for screenshot legibility - standard symbols only.

const Icons = {
    // ===== Signal Type Icons =====
    wifi: function(className) {
        return `<span class="icon icon-wifi ${className || ''}" aria-label="WiFi"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1" fill="currentColor" stroke="none"/></svg></span>`;
    },
    bluetooth: function(className) {
        return `<span class="icon icon-bluetooth ${className || ''}" aria-label="Bluetooth"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6.5 6.5 17.5 17.5 12 22 12 2 17.5 6.5 6.5 17.5"/></svg></span>`;
    },
    cellular: function(className) {
        return `<span class="icon icon-cellular ${className || ''}" aria-label="Cellular"><svg viewBox="0 0 24 24" fill="currentColor"><rect x="2" y="16" width="4" height="6" rx="1"/><rect x="8" y="12" width="4" height="10" rx="1"/><rect x="14" y="8" width="4" height="14" rx="1"/><rect x="20" y="4" width="4" height="18" rx="1" opacity="0.3"/></svg></span>`;
    },
    radio: function(className) {
        return `<span class="icon icon-radio ${className || ''}" aria-label="Radio"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 12c0-3 2-6 5-6s4 3 5 6c1 3 2 6 5 6s5-3 5-6"/></svg></span>`;
    },

    // ===== Mode Icons =====
    pager: function(className) {
        return `<span class="icon icon-pager ${className || ''}" aria-label="Pager"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="14" rx="2"/><line x1="8" y1="10" x2="16" y2="10"/><line x1="8" y1="14" x2="12" y2="14"/></svg></span>`;
    },
    sensor: function(className) {
        return `<span class="icon icon-sensor ${className || ''}" aria-label="Sensor"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49m11.31-2.82a10 10 0 0 1 0 14.14m-14.14 0a10 10 0 0 1 0-14.14"/></svg></span>`;
    },
    aircraft: function(className) {
        return `<span class="icon icon-aircraft ${className || ''}" aria-label="Aircraft"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/></svg></span>`;
    },
    satellite: function(className) {
        return `<span class="icon icon-satellite ${className || ''}" aria-label="Satellite"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 7L9 3 5 7l4 4"/><path d="m17 11 4 4-4 4-4-4"/><path d="m8 12 4 4 6-6-4-4-6 6"/><path d="m16 8 3-3"/><path d="M9 21a6 6 0 0 0-6-6"/></svg></span>`;
    },
    location: function(className) {
        return `<span class="icon icon-location ${className || ''}" aria-label="Location"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg></span>`;
    },
    search: function(className) {
        return `<span class="icon icon-search ${className || ''}" aria-label="Search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></span>`;
    },
    meter: function(className) {
        return `<span class="icon icon-meter ${className || ''}" aria-label="Meter"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg></span>`;
    },
    scanner: function(className) {
        return `<span class="icon icon-scanner ${className || ''}" aria-label="Scanner"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/></svg></span>`;
    },

    // ===== Status Icons =====
    warning: function(className) {
        return `<span class="icon icon-warning ${className || ''}" aria-label="Warning"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></span>`;
    },
    check: function(className) {
        return `<span class="icon icon-check ${className || ''}" aria-label="Check"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg></span>`;
    },
    x: function(className) {
        return `<span class="icon icon-x ${className || ''}" aria-label="X"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></span>`;
    },
    recording: function(className) {
        return `<span class="icon icon-recording ${className || ''}" aria-label="Recording"><svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="8"/></svg></span>`;
    },
    anomaly: function(className) {
        return `<span class="icon icon-anomaly ${className || ''}" aria-label="Anomaly"><svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="6"/></svg></span>`;
    },
    flag: function(className) {
        return `<span class="icon icon-flag ${className || ''}" aria-label="Flag"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></svg></span>`;
    },
    newBadge: function(className) {
        return `<span class="icon icon-new ${className || ''}" aria-label="New"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></span>`;
    },
    offline: function(className) {
        return `<span class="icon icon-offline ${className || ''}" aria-label="Offline"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55"/><path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39"/><path d="M10.71 5.05A16 16 0 0 1 22.58 9"/><path d="M1.42 9a15.91 15.91 0 0 1 4.7-2.88"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg></span>`;
    },

    // ===== Device Type Icons =====
    user: function(className) {
        return `<span class="icon icon-user ${className || ''}" aria-label="User"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></span>`;
    },
    drone: function(className) {
        return `<span class="icon icon-drone ${className || ''}" aria-label="Drone"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0"/><path d="M3 9a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M17 9a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M3 15a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M17 15a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M9 9l-4 -1"/><path d="M15 9l4 -1"/><path d="M9 15l-4 1"/><path d="M15 15l4 1"/></svg></span>`;
    },
    military: function(className) {
        return `<span class="icon icon-military ${className || ''}" aria-label="Military"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></span>`;
    },
    handshake: function(className) {
        return `<span class="icon icon-handshake ${className || ''}" aria-label="Handshake"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m11 17 2 2a1 1 0 1 0 3-3"/><path d="m14 14 2.5 2.5a1 1 0 1 0 3-3l-3.88-3.88a3 3 0 0 0-4.24 0l-.88.88a1 1 0 1 1-3-3l2.81-2.81a5.79 5.79 0 0 1 7.06-.87l.47.28a2 2 0 0 0 1.42.25L21 4"/><path d="m21 3 1 11h-2"/><path d="M3 3 2 14l6.5 6.5a1 1 0 1 0 3-3"/><path d="M3 4h8"/></svg></span>`;
    },

    // ===== Action Icons =====
    refresh: function(className) {
        return `<span class="icon icon-refresh ${className || ''}" aria-label="Refresh"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg></span>`;
    },
    download: function(className) {
        return `<span class="icon icon-download ${className || ''}" aria-label="Download"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></span>`;
    },
    export: function(className) {
        return `<span class="icon icon-export ${className || ''}" aria-label="Export"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg></span>`;
    },
    copy: function(className) {
        return `<span class="icon icon-copy ${className || ''}" aria-label="Copy"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></span>`;
    },
    link: function(className) {
        return `<span class="icon icon-link ${className || ''}" aria-label="Link"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></span>`;
    },
    chart: function(className) {
        return `<span class="icon icon-chart ${className || ''}" aria-label="Chart"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg></span>`;
    },
    star: function(className) {
        return `<span class="icon icon-star ${className || ''}" aria-label="Star"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg></span>`;
    },
    target: function(className) {
        return `<span class="icon icon-target ${className || ''}" aria-label="Target"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg></span>`;
    },
    settings: function(className) {
        return `<span class="icon icon-settings ${className || ''}" aria-label="Settings"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></span>`;
    },

    // ===== Playback Controls =====
    play: function(className) {
        return `<span class="icon icon-play ${className || ''}" aria-label="Play"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg></span>`;
    },
    pause: function(className) {
        return `<span class="icon icon-pause ${className || ''}" aria-label="Pause"><svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg></span>`;
    },
    stop: function(className) {
        return `<span class="icon icon-stop ${className || ''}" aria-label="Stop"><svg viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg></span>`;
    },
    headphones: function(className) {
        return `<span class="icon icon-headphones ${className || ''}" aria-label="Headphones"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg></span>`;
    },
    volumeOn: function(className) {
        return `<span class="icon icon-volume-on ${className || ''}" aria-label="Volume On"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg></span>`;
    },
    volumeOff: function(className) {
        return `<span class="icon icon-volume-off ${className || ''}" aria-label="Volume Off"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg></span>`;
    },

    // ===== UI Icons =====
    sun: function(className) {
        return `<span class="icon icon-sun ${className || ''}" aria-label="Light mode"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg></span>`;
    },
    moon: function(className) {
        return `<span class="icon icon-moon ${className || ''}" aria-label="Dark mode"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg></span>`;
    },
    arrowDown: function(className) {
        return `<span class="icon icon-arrow-down ${className || ''}" aria-label="Arrow down"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg></span>`;
    },
    chevronDown: function(className) {
        return `<span class="icon icon-chevron-down ${className || ''}" aria-label="Expand"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></span>`;
    },
    chevronRight: function(className) {
        return `<span class="icon icon-chevron-right ${className || ''}" aria-label="Right"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></span>`;
    },
    chevronLeft: function(className) {
        return `<span class="icon icon-chevron-left ${className || ''}" aria-label="Left"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></span>`;
    },
    mail: function(className) {
        return `<span class="icon icon-mail ${className || ''}" aria-label="Mail"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg></span>`;
    },
    loader: function(className) {
        return `<span class="icon icon-loader ${className || ''}" aria-label="Loading"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"/><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"/></svg></span>`;
    },
    bell: function(className) {
        return `<span class="icon icon-bell ${className || ''}" aria-label="Notifications"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg></span>`;
    },

    // ===== Helper function =====
    forSignalType: function(type, className) {
        const t = (type || '').toLowerCase();
        if (t.includes('wifi') || t.includes('802.11')) return this.wifi(className);
        if (t.includes('bluetooth') || t.includes('bt') || t.includes('ble')) return this.bluetooth(className);
        if (t.includes('cellular') || t.includes('lte') || t.includes('gsm') || t.includes('5g')) return this.cellular(className);
        return this.radio(className);
    }
};
