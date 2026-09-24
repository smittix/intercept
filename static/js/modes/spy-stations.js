/**
 * Spy Stations Mode
 * Number stations and diplomatic HF radio networks
 */

const SpyStations = (function() {
    // State
    let stations = [];
    let filteredStations = [];
    const VIEW_KEY = 'intercept.spyStations.view';
    let view = readView();
    let activeFilters = {
        types: ['number', 'diplomatic'],
        countries: [],
        modes: []
    };

    // Country flag emoji map
    const countryFlags = {
        'RU': '\u{1F1F7}\u{1F1FA}',
        'CU': '\u{1F1E8}\u{1F1FA}',
        'BG': '\u{1F1E7}\u{1F1EC}',
        'CZ': '\u{1F1E8}\u{1F1FF}',
        'EG': '\u{1F1EA}\u{1F1EC}',
        'KP': '\u{1F1F0}\u{1F1F5}',
        'TN': '\u{1F1F9}\u{1F1F3}',
        'US': '\u{1F1FA}\u{1F1F8}',
        'PL': '\u{1F1F5}\u{1F1F1}',
        'IL': '\u{1F1EE}\u{1F1F1}',
        'CN': '\u{1F1E8}\u{1F1F3}',
        'MA': '\u{1F1F2}\u{1F1E6}',
        'FR': '\u{1F1EB}\u{1F1F7}',
        'RO': '\u{1F1F7}\u{1F1F4}',
        'DZ': '\u{1F1E9}\u{1F1FF}'
    };

    /**
     * Initialize the spy stations mode
     */
    function init() {
        fetchStations();
        checkTuneFrequency();
    }

    /**
     * Fetch stations from the API
     */
    async function fetchStations() {
        try {
            const response = await fetch('/spy-stations/stations');
            const data = await response.json();

            if (data.status === 'success') {
                stations = data.stations;
                initFilters();
                applyFilters();
                updateStats();
            }
        } catch (err) {
            console.error('Failed to fetch spy stations:', err);
        }
    }

    /**
     * Initialize filter checkboxes
     */
    function initFilters() {
        // Get unique countries and modes
        const countries = [...new Set(stations.map(s => JSON.stringify({name: s.country, code: s.country_code})))].map(s => JSON.parse(s));
        const modes = [...new Set(stations.map(s => s.mode.split('/')[0]))].sort();

        // Populate country filters
        const countryContainer = document.getElementById('countryFilters');
        if (countryContainer) {
            countryContainer.innerHTML = countries.map(c => `
                <label class="inline-checkbox">
                    <input type="checkbox" data-country="${c.code}" checked onchange="SpyStations.applyFilters()">
                    <span>${countryFlags[c.code] || ''} ${c.name}</span>
                </label>
            `).join('');
        }

        // Populate mode filters
        const modeContainer = document.getElementById('modeFilters');
        if (modeContainer) {
            modeContainer.innerHTML = modes.map(m => `
                <label class="inline-checkbox">
                    <input type="checkbox" data-mode="${m}" checked onchange="SpyStations.applyFilters()">
                    <span style="font-family: 'Roboto Condensed', 'Arial Narrow', sans-serif; font-size: 10px;">${m}</span>
                </label>
            `).join('');
        }

        // Set initial filter states
        activeFilters.countries = countries.map(c => c.code);
        activeFilters.modes = modes;
    }

    /**
     * Apply filters and render stations
     */
    function applyFilters() {
        // Read type filters
        const typeNumber = document.getElementById('filterTypeNumber');
        const typeDiplomatic = document.getElementById('filterTypeDiplomatic');

        activeFilters.types = [];
        if (typeNumber && typeNumber.checked) activeFilters.types.push('number');
        if (typeDiplomatic && typeDiplomatic.checked) activeFilters.types.push('diplomatic');

        // Read country filters
        activeFilters.countries = [];
        document.querySelectorAll('#countryFilters input[data-country]:checked').forEach(cb => {
            activeFilters.countries.push(cb.dataset.country);
        });

        // Read mode filters
        activeFilters.modes = [];
        document.querySelectorAll('#modeFilters input[data-mode]:checked').forEach(cb => {
            activeFilters.modes.push(cb.dataset.mode);
        });

        // Apply filters
        filteredStations = stations.filter(s => {
            if (!activeFilters.types.includes(s.type)) return false;
            if (!activeFilters.countries.includes(s.country_code)) return false;
            const stationMode = s.mode.split('/')[0];
            if (!activeFilters.modes.includes(stationMode)) return false;
            return true;
        });

        renderStations();
        updateStats(true);
    }

    /**
     * Render station cards
     */
    function renderStations() {
        const container = document.getElementById('spyStationsGrid');
        if (!container) return;

        if (filteredStations.length === 0) {
            container.innerHTML = `
                <div class="spy-station-empty">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 48px; height: 48px; opacity: 0.3; margin-bottom: 12px;">
                        <path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/>
                        <path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/>
                        <circle cx="12" cy="12" r="2"/>
                        <path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/>
                        <path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/>
                    </svg>
                    <p>No stations match your filters</p>
                </div>
            `;
            return;
        }

        syncViewButtons();
        container.classList.toggle('table-view', view === 'table');
        container.innerHTML = view === 'table'
            ? renderStationTable(filteredStations)
            : filteredStations.map(station => renderStationCard(station)).join('');
    }

    function readView() {
        try { return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'cards'; } catch (e) { return 'cards'; }
    }

    /** Cards (with descriptions) or a compact table, remembered for next time. */
    function setView(next) {
        view = next === 'table' ? 'table' : 'cards';
        try { localStorage.setItem(VIEW_KEY, view); } catch (e) { /* this visit only */ }
        renderStations();
    }

    function syncViewButtons() {
        document.querySelectorAll('.spy-view-toggle button').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.view === view);
            btn.setAttribute('aria-pressed', String(btn.dataset.view === view));
        });
    }

    /** One row per station: many more on screen than the cards allow. */
    function renderStationTable(list) {
        const rows = list.map(station => {
            const flag = countryFlags[station.country_code] || '';
            const primary = station.frequencies.find(f => f.primary) || station.frequencies[0];
            const tune = station.frequencies.length > 1
                ? `<select class="spy-freq-select" id="freq-select-${station.id}">${station.frequencies.map(f =>
                    `<option value="${f.freq_khz}">${formatFrequency(f.freq_khz)}${f.primary ? ' (primary)' : ''}</option>`).join('')}</select>
                   <button class="spy-tune-btn" onclick="SpyStations.tuneToSelectedFreq('${station.id}')">Tune In</button>`
                : `<span class="spy-table-freq">${formatFrequency(primary.freq_khz)}</span>
                   <button class="spy-tune-btn" onclick="SpyStations.tuneToStation('${station.id}', ${primary.freq_khz})">Tune In</button>`;
            return `
                <tr data-station-id="${station.id}">
                    <td class="spy-table-name"><span class="spy-station-flag">${flag}</span><b>${station.name}</b>
                        ${station.nickname ? `<span class="spy-station-nickname">${station.nickname}</span>` : ''}</td>
                    <td><span class="spy-station-badge ${station.type === 'number' ? 'spy-badge-number' : 'spy-badge-diplomatic'}">${station.type === 'number' ? 'NUMBER' : 'DIPLOMATIC'}</span></td>
                    <td>${station.country}</td>
                    <td class="spy-meta-mode">${station.mode}</td>
                    <td class="spy-table-count">${station.frequencies.length}</td>
                    <td class="spy-table-tune">${tune}</td>
                    <td><button class="spy-details-btn" onclick="SpyStations.showDetails('${station.id}')">Details</button></td>
                </tr>`;
        }).join('');
        return `
            <table class="spy-table">
                <thead><tr><th>Station</th><th>Type</th><th>Origin</th><th>Mode</th><th title="Frequencies">Freqs</th><th>Tune</th><th></th></tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
    }

    /**
     * Render a single station card
     */
    function renderStationCard(station) {
        const flag = countryFlags[station.country_code] || '';
        const typeBadgeClass = station.type === 'number' ? 'spy-badge-number' : 'spy-badge-diplomatic';
        const typeBadgeText = station.type === 'number' ? 'NUMBER' : 'DIPLOMATIC';

        const primaryFreq = station.frequencies.find(f => f.primary) || station.frequencies[0];
        const freqList = station.frequencies.slice(0, 4).map(f => formatFrequency(f.freq_khz)).join(', ');
        const moreFreqs = station.frequencies.length > 4 ? ` +${station.frequencies.length - 4} more` : '';

        // Build tune button with frequency selector if multiple frequencies
        let tuneSection;
        if (station.frequencies.length > 1) {
            const options = station.frequencies.map(f => {
                const label = formatFrequency(f.freq_khz) + (f.primary ? ' (primary)' : '');
                return `<option value="${f.freq_khz}">${label}</option>`;
            }).join('');
            tuneSection = `
                <div class="spy-tune-group">
                    <select class="spy-freq-select" id="freq-select-${station.id}">
                        ${options}
                    </select>
                    <button class="spy-tune-btn" onclick="SpyStations.tuneToSelectedFreq('${station.id}')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 14px; height: 14px;">
                            <path d="M3 18v-6a9 9 0 0 1 18 0v6"/>
                            <path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>
                        </svg>
                        Tune In
                    </button>
                </div>
            `;
        } else {
            tuneSection = `
                <button class="spy-tune-btn" onclick="SpyStations.tuneToStation('${station.id}', ${primaryFreq.freq_khz})">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 14px; height: 14px;">
                        <path d="M3 18v-6a9 9 0 0 1 18 0v6"/>
                        <path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>
                    </svg>
                    Tune In
                </button>
            `;
        }

        return `
            <div class="spy-station-card" data-station-id="${station.id}">
                <div class="spy-station-header">
                    <div class="spy-station-title">
                        <span class="spy-station-flag">${flag}</span>
                        <span class="spy-station-name">${station.name}</span>
                        ${station.nickname ? `<span class="spy-station-nickname">- ${station.nickname}</span>` : ''}
                    </div>
                    <span class="spy-station-badge ${typeBadgeClass}">${typeBadgeText}</span>
                </div>
                <div class="spy-station-body">
                    <div class="spy-station-meta">
                        <div class="spy-station-meta-item">
                            <span class="spy-meta-label">Origin</span>
                            <span class="spy-meta-value">${station.country}</span>
                        </div>
                        <div class="spy-station-meta-item">
                            <span class="spy-meta-label">Mode</span>
                            <span class="spy-meta-value spy-meta-mode">${station.mode}</span>
                        </div>
                    </div>
                    <div class="spy-station-freqs">
                        <span class="spy-meta-label">Frequencies</span>
                        <span class="spy-freq-list">${freqList}${moreFreqs}</span>
                    </div>
                    <div class="spy-station-desc">${station.description}</div>
                </div>
                <div class="spy-station-footer">
                    ${tuneSection}
                    <button class="spy-details-btn" onclick="SpyStations.showDetails('${station.id}')">
                        Details
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 12px; height: 12px;">
                            <polyline points="6 9 12 15 18 9"/>
                        </svg>
                    </button>
                </div>
            </div>
        `;
    }

    /**
     * Format frequency for display
     */
    function formatFrequency(freqKhz) {
        if (freqKhz >= 1000) {
            return (freqKhz / 1000).toFixed(3) + ' MHz';
        }
        return freqKhz + ' kHz';
    }

    /**
     * Get appropriate SDR mode from station mode string
     */
    function getModeFromStation(stationMode) {
        const mode = stationMode.toLowerCase();
        if (mode.includes('am') || mode.includes('ofdm')) return 'am';
        if (mode.includes('lsb')) return 'lsb';
        if (mode.includes('fm')) return 'fm';
        // Default to USB for most number stations and digital modes
        return 'usb';
    }

    /**
     * Tune to a station frequency
     */
    function tuneToStation(stationId, freqKhz) {
        const freqMhz = freqKhz / 1000;

        // Find the station and determine mode
        const station = stations.find(s => s.id === stationId);
        const tuneMode = station ? getModeFromStation(station.mode) : 'usb';

        const stationName = station ? station.name : 'Station';

        if (typeof showNotification === 'function') {
            showNotification('Tuning to ' + stationName, formatFrequency(freqKhz) + ' (' + tuneMode.toUpperCase() + ')');
        }

        // Switch to spectrum waterfall mode and tune after init completes.
        const doTune = () => {
            if (typeof Waterfall !== 'undefined' && typeof Waterfall.quickTune === 'function') {
                Waterfall.quickTune(freqMhz, tuneMode);
            }
        };

        if (typeof switchMode === 'function') {
            switchMode('waterfall').then(doTune);
        } else if (typeof selectMode === 'function') {
            selectMode('waterfall');
            setTimeout(doTune, 300);
        }
    }

    /**
     * Tune to selected frequency from dropdown
     */
    function tuneToSelectedFreq(stationId) {
        const select = document.getElementById('freq-select-' + stationId);
        if (select) {
            const freqKhz = parseInt(select.value, 10);
            tuneToStation(stationId, freqKhz);
        }
    }

    /**
     * Check if we arrived from another page with a tune request
     */
    function checkTuneFrequency() {
        // Reserved for cross-mode tune handoff behavior.
    }

    /**
     * Show station details modal
     */
    function showDetails(stationId) {
        const station = stations.find(s => s.id === stationId);
        if (!station) return;

        let modal = document.getElementById('spyStationDetailsModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'spyStationDetailsModal';
            modal.className = 'signal-details-modal';
            document.body.appendChild(modal);
        }

        const flag = countryFlags[station.country_code] || '';
        const allFreqs = station.frequencies.map(f => {
            const label = f.primary ? ' (primary)' : '';
            return `<span class="spy-freq-item spy-freq-clickable" onclick="SpyStations.tuneToStation('${station.id}', ${f.freq_khz}); SpyStations.closeDetails();">${formatFrequency(f.freq_khz)}${label}</span>`;
        }).join('');

        modal.innerHTML = `
            <div class="signal-details-modal-backdrop" onclick="SpyStations.closeDetails()"></div>
            <div class="signal-details-modal-content">
                <div class="signal-details-modal-header">
                    <h3>${flag} ${station.name} ${station.nickname ? '- ' + station.nickname : ''}</h3>
                    <button class="signal-details-modal-close" onclick="SpyStations.closeDetails()">&times;</button>
                </div>
                <div class="signal-details-modal-body">
                    <div class="signal-details-section">
                        <div class="signal-details-title">Overview</div>
                        <div class="signal-details-grid">
                            <div class="signal-details-item">
                                <span class="signal-details-label">Type</span>
                                <span class="signal-details-value">${station.type === 'number' ? 'Number Station' : 'Diplomatic Network'}</span>
                            </div>
                            <div class="signal-details-item">
                                <span class="signal-details-label">Country</span>
                                <span class="signal-details-value">${station.country}</span>
                            </div>
                            <div class="signal-details-item">
                                <span class="signal-details-label">Mode</span>
                                <span class="signal-details-value">${station.mode}</span>
                            </div>
                            <div class="signal-details-item">
                                <span class="signal-details-label">Operator</span>
                                <span class="signal-details-value">${station.operator || 'Unknown'}</span>
                            </div>
                        </div>
                    </div>
                    <div class="signal-details-section">
                        <div class="signal-details-title">Description</div>
                        <p style="color: var(--text-secondary); font-size: 12px; line-height: 1.6;">${station.description}</p>
                    </div>
                    <div class="signal-details-section">
                        <div class="signal-details-title">Frequencies (${station.frequencies.length})</div>
                        <div class="spy-freq-grid">${allFreqs}</div>
                    </div>
                    ${station.schedule ? `
                    <div class="signal-details-section">
                        <div class="signal-details-title">Schedule</div>
                        <p style="color: var(--text-secondary); font-size: 12px;">${station.schedule}</p>
                    </div>
                    ` : ''}
                    ${station.source_url ? `
                    <div class="signal-details-section">
                        <div class="signal-details-title">Source</div>
                        <a href="${station.source_url}" target="_blank" rel="noopener" style="color: var(--accent-cyan); font-size: 12px;">${station.source_url}</a>
                    </div>
                    ` : ''}
                </div>
                <div class="signal-details-modal-footer">
                    <button class="spy-tune-btn" onclick="SpyStations.tuneToStation('${station.id}', ${station.frequencies[0].freq_khz}); SpyStations.closeDetails();">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 14px; height: 14px;">
                            <path d="M3 18v-6a9 9 0 0 1 18 0v6"/>
                            <path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>
                        </svg>
                        Tune In
                    </button>
                </div>
            </div>
        `;

        modal.classList.add('show');
    }

    /**
     * Close details modal
     */
    function closeDetails() {
        const modal = document.getElementById('spyStationDetailsModal');
        if (modal) {
            modal.classList.remove('show');
        }
    }

    /**
     * Show help modal
     */
    function showHelp() {
        let modal = document.getElementById('spyStationsHelpModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'spyStationsHelpModal';
            modal.className = 'signal-details-modal';
            document.body.appendChild(modal);
        }

        modal.innerHTML = `
            <div class="signal-details-modal-backdrop" onclick="SpyStations.closeHelp()"></div>
            <div class="signal-details-modal-content">
                <div class="signal-details-modal-header">
                    <h3>About Spy Stations</h3>
                    <button class="signal-details-modal-close" onclick="SpyStations.closeHelp()">&times;</button>
                </div>
                <div class="signal-details-modal-body">
                    <div class="signal-details-section">
                        <div class="signal-details-title">Number Stations</div>
                        <p style="color: var(--text-secondary); font-size: 12px; line-height: 1.6;">
                            Number stations are shortwave radio transmissions believed to be used by intelligence agencies
                            to communicate with spies in the field. They typically broadcast strings of numbers, letters,
                            or words read by synthesized or live voices. These one-way broadcasts are encrypted using
                            one-time pads, making them virtually unbreakable.
                        </p>
                    </div>
                    <div class="signal-details-section">
                        <div class="signal-details-title">Diplomatic Networks</div>
                        <p style="color: var(--text-secondary); font-size: 12px; line-height: 1.6;">
                            Foreign ministries maintain HF radio networks to communicate with embassies worldwide,
                            especially in regions where satellite or internet connectivity may be unreliable or
                            compromised. These networks use various digital modes like PACTOR, ALE, and proprietary
                            protocols for encrypted diplomatic traffic.
                        </p>
                    </div>
                    <div class="signal-details-section">
                        <div class="signal-details-title">How to Listen</div>
                        <p style="color: var(--text-secondary); font-size: 12px; line-height: 1.6;">
                            Click "Tune In" on any station to open Spectrum Waterfall with the frequency pre-configured.
                            Most number stations use USB (Upper Sideband) mode. You'll need an SDR capable of receiving
                            HF frequencies (typically 3-30 MHz) and an appropriate antenna.
                        </p>
                    </div>
                    <div class="signal-details-section">
                        <div class="signal-details-title">Best Practices</div>
                        <ul style="color: var(--text-secondary); font-size: 12px; line-height: 1.6; padding-left: 20px;">
                            <li>HF propagation varies with time of day and solar conditions</li>
                            <li>Use a long wire or loop antenna for best results</li>
                            <li>Check schedules on priyom.org for transmission times</li>
                            <li>Night time generally offers better long-distance reception</li>
                        </ul>
                    </div>
                    <div class="signal-details-section">
                        <div class="signal-details-title">Data Sources</div>
                        <p style="color: var(--text-secondary); font-size: 12px; line-height: 1.6;">
                            Station data sourced from <a href="https://priyom.org" target="_blank" rel="noopener" style="color: var(--accent-cyan);">priyom.org</a>,
                            a community-maintained database of number stations and related transmissions.
                        </p>
                    </div>
                </div>
            </div>
        `;

        modal.classList.add('show');
    }

    /**
     * Close help modal
     */
    function closeHelp() {
        const modal = document.getElementById('spyStationsHelpModal');
        if (modal) {
            modal.classList.remove('show');
        }
    }

    /**
     * Update sidebar stats
     * @param {boolean} useFiltered - If true, use filtered stations instead of all stations
     */
    function updateStats(useFiltered) {
        const stationList = useFiltered ? filteredStations : stations;
        const numberCount = stationList.filter(s => s.type === 'number').length;
        const diplomaticCount = stationList.filter(s => s.type === 'diplomatic').length;
        const countryCount = new Set(stationList.map(s => s.country_code)).size;
        const freqCount = stationList.reduce((sum, s) => sum + s.frequencies.length, 0);

        const numberEl = document.getElementById('spyStatsNumber');
        const diplomaticEl = document.getElementById('spyStatsDiplomatic');
        const countriesEl = document.getElementById('spyStatsCountries');
        const freqsEl = document.getElementById('spyStatsFreqs');

        if (numberEl) numberEl.textContent = numberCount;
        if (diplomaticEl) diplomaticEl.textContent = diplomaticCount;
        if (countriesEl) countriesEl.textContent = countryCount;
        if (freqsEl) freqsEl.textContent = freqCount;

        // Update visible count in header if element exists
        const visibleCountEl = document.getElementById('spyStationsVisibleCount');
        if (visibleCountEl) {
            visibleCountEl.textContent = stationList.length;
        }
    }

    /**
     * Destroy — no-op placeholder for consistent lifecycle interface.
     */
    function destroy() {
        // SpyStations has no background timers or streams to clean up.
    }

    // Public API
    return {
        init,
        applyFilters,
        tuneToStation,
        tuneToSelectedFreq,
        showDetails,
        closeDetails,
        showHelp,
        closeHelp,
        setView,
        destroy
    };
})();

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    // Will be initialized when mode is switched to spy stations
});
