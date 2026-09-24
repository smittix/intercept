/**
 * CopyId: a copy button on identifiers (ICAO hex, MMSI, MAC, node id).
 *
 *     `<span>${CopyId.html(ac.icao)}</span>`   // in an HTML template
 *     CopyId.set(element, network.bssid);      // where code set textContent
 *
 * One delegated listener handles every button. It runs in the capture phase
 * and stops the click there, so copying does not also select the row the
 * identifier sits in. iNTERCEPT is often served over plain HTTP on a LAN,
 * where navigator.clipboard does not exist, so there is a fallback.
 */
const CopyId = (function () {
    'use strict';

    const STYLE =
        '.copy-id{white-space:nowrap}' +
        '.copy-id-btn{margin-left:4px;padding:0 3px;border:0;background:none;color:inherit;font:inherit;' +
        'line-height:1;cursor:pointer;opacity:0;transition:opacity .15s}' +
        '.copy-id:hover .copy-id-btn,.copy-id-btn:focus-visible,.copy-id-btn.copied,.copy-id-btn.failed{opacity:.8}' +
        '.copy-id-btn.copied{color:var(--accent-green,#38c180)}.copy-id-btn.failed{color:var(--accent-red,#e25d5d)}' +
        '@media (hover:none){.copy-id-btn{opacity:.6}}';

    function escape(text) {
        return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    /** The identifier, escaped, with a copy button. `label` is what is shown, if different. */
    function html(value, label) {
        if (value === null || value === undefined || value === '') return escape(label === undefined ? '' : label);
        const text = String(value);
        const shown = escape(label === undefined ? text : label);
        return '<span class="copy-id">' + shown +
            '<button type="button" class="copy-id-btn" data-copy="' + escape(text) + '" title="Copy ' + escape(text) +
            '" aria-label="Copy ' + escape(text) + '">⧉</button></span>';
    }

    /** Put an identifier with its copy button into an element. */
    function set(element, value, label) {
        if (element) element.innerHTML = html(value, label);
    }

    async function copy(text) {
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(text);
                return true;
            }
        } catch (err) {
            // fall through to the selection-based copy
        }
        const area = document.createElement('textarea');
        area.value = text;
        area.setAttribute('readonly', '');
        area.style.cssText = 'position:fixed;top:-1000px;opacity:0';
        document.body.appendChild(area);
        area.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
        area.remove();
        return ok;
    }

    function feedback(button, ok) {
        button.classList.add(ok ? 'copied' : 'failed');
        button.textContent = ok ? '✓' : '!';
        button.title = ok ? 'Copied' : 'Copy failed';
        clearTimeout(button._copyTimer);
        button._copyTimer = setTimeout(() => {
            button.classList.remove('copied', 'failed');
            button.textContent = '⧉';
            button.title = 'Copy ' + button.dataset.copy;
        }, 1200);
    }

    document.addEventListener('click', (event) => {
        const button = event.target.closest && event.target.closest('.copy-id-btn');
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        copy(button.dataset.copy).then((ok) => feedback(button, ok));
    }, true);

    function injectStyle() {
        const style = document.createElement('style');
        style.textContent = STYLE;
        document.head.appendChild(style);
    }
    if (document.head) injectStyle(); else document.addEventListener('DOMContentLoaded', injectStyle);

    return { html, set, copy };
})();

window.CopyId = CopyId;
