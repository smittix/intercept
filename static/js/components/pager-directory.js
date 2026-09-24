const PagerDirectory = (function () {
    'use strict';

    const STORAGE_KEY = 'pagerView';

    // Map<address, { count, protocol, lastSeen }>
    const addresses = new Map();
    let highlighted = null;

    // ---- Helpers ----

    function esc(str) {
        return String(str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ---- Directory rendering ----

    function renderDirectory() {
        const entriesEl = document.getElementById('pagerDirEntries');
        const countEl   = document.getElementById('pagerDirCount');
        if (!entriesEl) return;

        const sorted = [...addresses.entries()].sort((a, b) => b[1].count - a[1].count);
        const maxCount = sorted.length > 0 ? sorted[0][1].count : 1;

        if (countEl) countEl.textContent = sorted.length;

        sorted.forEach(([addr, data]) => {
            let el = entriesEl.querySelector(`[data-pdir-addr="${CSS.escape(addr)}"]`);
            const isActive = addr === highlighted;
            const pct = Math.round((data.count / maxCount) * 100);
            const isPocsag = data.protocol !== 'flex';
            const protoClass = isPocsag ? 'pdir-proto--p' : 'pdir-proto--f';
            const barClass   = isPocsag ? '' : 'pdir-bar--flex';
            const html = `
                <div class="pdir-entry-top">
                    <span class="pdir-proto ${protoClass}">${isPocsag ? 'P' : 'F'}</span>
                    <span class="pdir-addr">${esc(addr)}</span>
                    <span class="pdir-new-dot"></span>
                    <span class="pdir-count">×${data.count}</span>
                </div>
                <div class="pdir-bar-wrap"><div class="pdir-bar ${barClass}" style="width:${pct}%"></div></div>
                <div class="pdir-age">${InterceptTime.relTimeHtml(data.lastSeen)}</div>`;

            if (!el) {
                el = document.createElement('div');
                el.className = 'pdir-entry';
                el.dataset.pdirAddr = addr;
                el.addEventListener('click', () => toggleHighlight(addr));
                entriesEl.appendChild(el);
            }
            el.classList.toggle('pdir-entry--active', isActive);
            el.innerHTML = html;
        });

        // Re-order DOM to match sort
        sorted.forEach(([addr]) => {
            const el = entriesEl.querySelector(`[data-pdir-addr="${CSS.escape(addr)}"]`);
            if (el) entriesEl.appendChild(el);
        });
    }

    function flashNewDot(addr) {
        // Find the dot inside this entry after the current render frame
        setTimeout(() => {
            const entriesEl = document.getElementById('pagerDirEntries');
            const entry = entriesEl?.querySelector(`[data-pdir-addr="${CSS.escape(addr)}"]`);
            const dot = entry?.querySelector('.pdir-new-dot');
            if (!dot) return;
            dot.classList.remove('pdir-new-dot--active');
            void dot.offsetWidth; // force reflow to restart animation
            dot.classList.add('pdir-new-dot--active');
        }, 0);
    }

    // ---- Highlight ----

    function toggleHighlight(addr) {
        if (highlighted === addr) clearHighlight();
        else highlight(addr);
    }

    function highlight(addr) {
        highlighted = addr;
        renderDirectory();

        const feedLabel   = document.getElementById('pagerFeedLabel');
        const clearBtn    = document.getElementById('pagerClearHighlight');
        if (feedLabel) feedLabel.textContent = `${addr} highlighted`;
        if (clearBtn)  clearBtn.style.display = 'inline';

        const output = document.getElementById('output');
        if (!output) return;

        output.querySelectorAll('.signal-card').forEach(card => {
            card.classList.toggle('pdir-hl', card.dataset.address === addr);
        });

        const first = output.querySelector(`.signal-card[data-address="${CSS.escape(addr)}"]`);
        if (first) first.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function clearHighlight() {
        highlighted = null;
        renderDirectory();

        const feedLabel = document.getElementById('pagerFeedLabel');
        const clearBtn  = document.getElementById('pagerClearHighlight');
        if (feedLabel) feedLabel.textContent = 'All messages';
        if (clearBtn)  clearBtn.style.display = 'none';

        document.getElementById('output')
            ?.querySelectorAll('.pdir-hl')
            .forEach(c => c.classList.remove('pdir-hl'));
    }

    // ---- Public: message hook ----

    function addMessage(msg) {
        const addr  = msg.address;
        if (!addr) return;
        const proto = (msg.protocol || '').includes('FLEX') ? 'flex' : 'pocsag';
        const entry = addresses.get(addr);
        if (entry) {
            entry.count++;
            entry.lastSeen = Date.now();
            entry.protocol = proto;
        } else {
            addresses.set(addr, { count: 1, protocol: proto, lastSeen: Date.now() });
        }
        renderDirectory();
        flashNewDot(addr);
        // Re-apply highlight class to the newly inserted card (caller inserts it after this hook)
        if (highlighted === addr) {
            setTimeout(() => {
                const output = document.getElementById('output');
                output?.querySelectorAll(`.signal-card[data-address="${CSS.escape(addr)}"]`)
                    .forEach(c => c.classList.add('pdir-hl'));
            }, 0);
        }
    }

    // ---- Show / hide / reset ----

    function applyViewState(mode) {
        const dirPanel   = document.getElementById('pagerDirectoryView');
        const feedHeader = document.getElementById('pagerFeedHeader');

        if (mode === 'pager') {
            const saved = localStorage.getItem(STORAGE_KEY) || 'directory';
            const isDir = saved === 'directory';
            if (dirPanel)   dirPanel.style.display   = isDir ? 'flex' : 'none';
            if (feedHeader) feedHeader.style.display  = isDir ? 'flex' : 'none';
            _updateToggle(isDir);
            renderDirectory();
        } else {
            if (dirPanel)   dirPanel.style.display   = 'none';
            if (feedHeader) feedHeader.style.display = 'none';
            clearHighlight();
        }
    }

    function show() {
        localStorage.setItem(STORAGE_KEY, 'directory');
        applyViewState('pager');
    }

    function hide() {
        localStorage.setItem(STORAGE_KEY, 'feed');
        applyViewState('pager');
    }

    function _updateToggle(isDir) {
        document.getElementById('pagerToggleDir')?.classList.toggle('view-toggle-btn--active', isDir);
        document.getElementById('pagerToggleFeed')?.classList.toggle('view-toggle-btn--active', !isDir);
    }

    function reset() {
        addresses.clear();
        highlighted = null;
        const entriesEl = document.getElementById('pagerDirEntries');
        const countEl   = document.getElementById('pagerDirCount');
        if (entriesEl) entriesEl.innerHTML = '';
        if (countEl)   countEl.textContent = '0';
        clearHighlight();
    }

    return { addMessage, highlight, clearHighlight, show, hide, reset, applyViewState };
})();
