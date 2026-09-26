const CommandPalette = (function() {
    'use strict';

    let overlayEl = null;
    let inputEl = null;
    let listEl = null;
    let isOpen = false;
    let activeIndex = 0;
    let filteredItems = [];

    const fallbackModeCommands = [
        { mode: 'pager', label: 'Pager' },
        { mode: 'sensor', label: '433MHz Sensors' },
        { mode: 'rtlamr', label: 'Meters' },
        { mode: 'subghz', label: 'SubGHz' },
        { mode: 'waterfall', label: 'Spectrum Waterfall' },
        { mode: 'wifi', label: 'WiFi Scanner' },
        { mode: 'bluetooth', label: 'Bluetooth Scanner' },
        { mode: 'bt_locate', label: 'BT Locate' },
        { mode: 'satellite', label: 'Satellite' },
        { mode: 'sstv', label: 'ISS SSTV' },
        { mode: 'weathersat', label: 'Weather Sat' },
        { mode: 'sstv_general', label: 'HF SSTV' },
        { mode: 'gps', label: 'GPS' },
        { mode: 'websdr', label: 'WebSDR' },
        { mode: 'spaceweather', label: 'Space Weather' },
    ];

    function getModeCommands() {
        const commands = [];
        const seenModes = new Set();

        const catalog = window.interceptModeCatalog;
        if (catalog && typeof catalog === 'object') {
            for (const [mode, meta] of Object.entries(catalog)) {
                if (!mode || seenModes.has(mode)) continue;
                const label = String((meta && meta.label) || mode).trim();
                commands.push({ mode, label });
                seenModes.add(mode);
            }
            if (commands.length > 0) return commands;
        }

        const navNodes = document.querySelectorAll('.mode-nav-btn[data-mode], .mobile-nav-btn[data-mode]');
        navNodes.forEach((node) => {
            if (node.tagName === 'A') {
                const href = String(node.getAttribute('href') || '');
                if (href.includes('/dashboard')) return;
            }
            const mode = String(node.dataset.mode || '').trim();
            if (!mode || seenModes.has(mode)) return;
            const label = String(node.dataset.modeLabel || node.textContent || mode).trim();
            commands.push({ mode, label });
            seenModes.add(mode);
        });
        if (commands.length > 0) return commands;

        return fallbackModeCommands.slice();
    }

    function init() {
        buildDOM();
        registerHotkeys();
        renderItems('');
    }

    function buildDOM() {
        overlayEl = document.createElement('div');
        overlayEl.className = 'command-palette-overlay';
        overlayEl.id = 'commandPaletteOverlay';
        overlayEl.addEventListener('click', (event) => {
            if (event.target === overlayEl) close();
        });

        const palette = document.createElement('div');
        palette.className = 'command-palette';

        const header = document.createElement('div');
        header.className = 'command-palette-header';

        inputEl = document.createElement('input');
        inputEl.className = 'command-palette-input';
        inputEl.type = 'text';
        inputEl.autocomplete = 'off';
        inputEl.placeholder = 'Search commands and modes...';
        inputEl.setAttribute('aria-label', 'Command Palette Search');
        inputEl.addEventListener('input', () => {
            renderItems(inputEl.value || '');
        });
        inputEl.addEventListener('keydown', onInputKeyDown);

        const hint = document.createElement('span');
        hint.className = 'command-palette-hint';
        hint.textContent = 'Esc close';

        header.appendChild(inputEl);
        header.appendChild(hint);

        listEl = document.createElement('div');
        listEl.className = 'command-palette-list';
        listEl.id = 'commandPaletteList';

        palette.appendChild(header);
        palette.appendChild(listEl);
        overlayEl.appendChild(palette);
        document.body.appendChild(overlayEl);
    }

    function registerHotkeys() {
        document.addEventListener('keydown', (event) => {
            const cmdK = (event.key.toLowerCase() === 'k') && (event.ctrlKey || event.metaKey);
            if (cmdK) {
                event.preventDefault();
                if (isOpen) {
                    close();
                } else {
                    open();
                }
                return;
            }

            if (!isOpen) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                close();
            }
        });
    }

    function onInputKeyDown(event) {
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            activeIndex = Math.min(activeIndex + 1, Math.max(filteredItems.length - 1, 0));
            renderSelection();
            return;
        }

        if (event.key === 'ArrowUp') {
            event.preventDefault();
            activeIndex = Math.max(activeIndex - 1, 0);
            renderSelection();
            return;
        }

        if (event.key === 'Enter') {
            event.preventDefault();
            const item = filteredItems[activeIndex];
            if (item && typeof item.run === 'function') {
                item.run();
            }
            close();
        }
    }

    function getCommands() {
        const commands = [
            {
                title: 'Open Settings',
                description: 'Open global settings panel',
                keyword: 'settings configure tools',
                shortcut: 'S',
                run: () => {
                    if (typeof showSettings === 'function') showSettings();
                }
            },
            {
                title: 'Settings: Alerts',
                description: 'Open alert rules and feed',
                keyword: 'settings alerts rule',
                run: () => openSettingsTab('alerts')
            },
            {
                title: 'Settings: Recording',
                description: 'Open recording manager',
                keyword: 'settings recording replay',
                run: () => openSettingsTab('recording')
            },
            {
                title: 'Settings: Location',
                description: 'Configure observer location',
                keyword: 'settings location gps lat lon',
                run: () => openSettingsTab('location')
            },
            {
                title: 'View Aircraft Dashboard',
                description: 'Open dedicated ADS-B dashboard page',
                keyword: 'aircraft adsb dashboard',
                run: () => {
                    if (window.InterceptNavPerf && typeof window.InterceptNavPerf.markStart === 'function') {
                        window.InterceptNavPerf.markStart({
                            targetPath: '/adsb/dashboard',
                            trigger: 'command-palette',
                            sourceMode: (typeof currentMode === 'string' && currentMode) ? currentMode : null,
                            activeScans: (typeof getActiveScanSummary === 'function') ? getActiveScanSummary() : null,
                        });
                    }
                    if (typeof stopActiveLocalScansForNavigation === 'function') {
                        stopActiveLocalScansForNavigation();
                    }
                    window.location.href = '/adsb/dashboard';
                }
            },
            {
                title: 'View Vessel Dashboard',
                description: 'Open dedicated AIS dashboard page',
                keyword: 'vessel ais dashboard',
                run: () => {
                    if (window.InterceptNavPerf && typeof window.InterceptNavPerf.markStart === 'function') {
                        window.InterceptNavPerf.markStart({
                            targetPath: '/ais/dashboard',
                            trigger: 'command-palette',
                            sourceMode: (typeof currentMode === 'string' && currentMode) ? currentMode : null,
                            activeScans: (typeof getActiveScanSummary === 'function') ? getActiveScanSummary() : null,
                        });
                    }
                    if (typeof stopActiveLocalScansForNavigation === 'function') {
                        stopActiveLocalScansForNavigation();
                    }
                    window.location.href = '/ais/dashboard';
                }
            },
            {
                title: 'View APRS Dashboard',
                description: 'Open dedicated APRS dashboard page',
                keyword: 'aprs packet radio dashboard',
                run: () => {
                    if (window.InterceptNavPerf && typeof window.InterceptNavPerf.markStart === 'function') {
                        window.InterceptNavPerf.markStart({
                            targetPath: '/aprs/dashboard',
                            trigger: 'command-palette',
                            sourceMode: (typeof currentMode === 'string' && currentMode) ? currentMode : null,
                            activeScans: (typeof getActiveScanSummary === 'function') ? getActiveScanSummary() : null,
                        });
                    }
                    if (typeof stopActiveLocalScansForNavigation === 'function') {
                        stopActiveLocalScansForNavigation();
                    }
                    window.location.href = '/aprs/dashboard';
                }
            },
            {
                title: 'View Meshtastic Dashboard',
                description: 'Open dedicated Meshtastic mesh console page',
                keyword: 'meshtastic mesh lora dashboard',
                run: () => {
                    if (window.InterceptNavPerf && typeof window.InterceptNavPerf.markStart === 'function') {
                        window.InterceptNavPerf.markStart({
                            targetPath: '/meshtastic/dashboard',
                            trigger: 'command-palette',
                            sourceMode: (typeof currentMode === 'string' && currentMode) ? currentMode : null,
                            activeScans: (typeof getActiveScanSummary === 'function') ? getActiveScanSummary() : null,
                        });
                    }
                    if (typeof stopActiveLocalScansForNavigation === 'function') {
                        stopActiveLocalScansForNavigation();
                    }
                    window.location.href = '/meshtastic/dashboard';
                }
            },
            {
                title: 'View Meshcore Dashboard',
                description: 'Open dedicated Meshcore mesh console page',
                keyword: 'meshcore mesh lora dashboard',
                run: () => {
                    if (window.InterceptNavPerf && typeof window.InterceptNavPerf.markStart === 'function') {
                        window.InterceptNavPerf.markStart({
                            targetPath: '/meshcore/dashboard',
                            trigger: 'command-palette',
                            sourceMode: (typeof currentMode === 'string' && currentMode) ? currentMode : null,
                            activeScans: (typeof getActiveScanSummary === 'function') ? getActiveScanSummary() : null,
                        });
                    }
                    if (typeof stopActiveLocalScansForNavigation === 'function') {
                        stopActiveLocalScansForNavigation();
                    }
                    window.location.href = '/meshcore/dashboard';
                }
            },
            {
                title: 'Kill All Running Processes',
                description: 'Stop all decoders and scans',
                keyword: 'kill stop processes emergency',
                run: () => {
                    if (typeof killAll === 'function') {
                        killAll();
                    } else if (typeof fetch === 'function') {
                        fetch('/killall', { method: 'POST' });
                    }
                }
            },
            {
                title: 'Toggle Sidebar Width',
                description: 'Collapse or expand the left sidebar',
                keyword: 'sidebar collapse layout',
                run: () => {
                    if (typeof toggleMainSidebarCollapse === 'function') {
                        toggleMainSidebarCollapse();
                    }
                }
            },
        ];

        for (const modeEntry of getModeCommands()) {
            commands.push({
                title: `Switch Mode: ${modeEntry.label}`,
                description: 'Navigate directly to mode',
                keyword: `mode ${modeEntry.mode} ${modeEntry.label.toLowerCase()}`,
                run: () => goToMode(modeEntry.mode),
            });
        }

        return commands;
    }

    function renderItems(query) {
        const q = String(query || '').trim().toLowerCase();
        const allItems = getCommands();

        filteredItems = allItems.filter((item) => {
            if (!q) return true;
            const haystack = `${item.title} ${item.description || ''} ${item.keyword || ''}`.toLowerCase();
            return haystack.includes(q);
        }).slice(0, 80);

        activeIndex = 0;

        listEl.innerHTML = '';
        if (filteredItems.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'command-palette-empty';
            empty.textContent = 'No matching commands';
            listEl.appendChild(empty);
            return;
        }

        filteredItems.forEach((item, idx) => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'command-palette-item';
            row.dataset.index = String(idx);
            row.addEventListener('click', () => {
                item.run();
                close();
            });

            const meta = document.createElement('span');
            meta.className = 'meta';

            const title = document.createElement('span');
            title.className = 'title';
            title.textContent = item.title;
            meta.appendChild(title);

            const desc = document.createElement('span');
            desc.className = 'desc';
            desc.textContent = item.description || '';
            meta.appendChild(desc);

            row.appendChild(meta);

            if (item.shortcut) {
                const kbd = document.createElement('span');
                kbd.className = 'kbd';
                kbd.textContent = item.shortcut;
                row.appendChild(kbd);
            }

            listEl.appendChild(row);
        });

        renderSelection();
    }

    function renderSelection() {
        const rows = listEl.querySelectorAll('.command-palette-item');
        rows.forEach((row) => {
            const idx = Number(row.dataset.index || 0);
            row.classList.toggle('active', idx === activeIndex);
        });

        const activeRow = listEl.querySelector(`.command-palette-item[data-index="${activeIndex}"]`);
        if (activeRow) {
            activeRow.scrollIntoView({ block: 'nearest' });
        }
    }

    function goToMode(mode) {
        if (mode === 'satellite') {
            window.open('/satellite/dashboard', '_blank', 'noopener');
            return;
        }

        const welcome = document.getElementById('welcomePage');
        if (welcome && getComputedStyle(welcome).display !== 'none') {
            welcome.style.display = 'none';
        }

        if (typeof switchMode === 'function') {
            switchMode(mode, { updateUrl: true });
        }
    }

    function openSettingsTab(tab) {
        if (typeof showSettings === 'function') {
            showSettings();
        }
        if (typeof switchSettingsTab === 'function') {
            switchSettingsTab(tab);
        }
    }

    function open() {
        if (!overlayEl) return;
        isOpen = true;
        overlayEl.classList.add('open');
        renderItems('');
        inputEl.value = '';
        requestAnimationFrame(() => {
            inputEl.focus();
        });
    }

    function close() {
        if (!overlayEl) return;
        isOpen = false;
        overlayEl.classList.remove('open');
    }

    return {
        init,
        open,
        close,
    };
})();

document.addEventListener('DOMContentLoaded', () => {
    CommandPalette.init();
});
