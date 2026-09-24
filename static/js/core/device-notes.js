/**
 * DeviceNotes: an operator's note and tags on an observed device, shown
 * wherever that device appears.
 *
 *     `${CopyId.html(ac.icao)}${DeviceNotes.html(ac.icao, 'adsb')}`
 *
 * html() leaves a placeholder that is filled with the device's tags, a note
 * marker (the note is its tooltip) and an edit button. Lists re-render all
 * the time, so a MutationObserver fills placeholders as they appear; no view
 * has to call anything after rendering. Notes are stored server-side
 * (/device-notes) and survive a restart.
 *
 * Note and tag text is operator input: it only ever reaches the page through
 * textContent and the title property, never as HTML.
 */
const DeviceNotes = (function () {
    'use strict';

    const SELECTOR = '.device-notes[data-dn-id]';
    let notes = {};
    let dialog = null;

    const STYLE =
        '.device-notes{display:inline-flex;align-items:center;gap:3px;margin-left:4px;vertical-align:middle}' +
        '.device-notes .dn-tag{padding:0 5px;border-radius:3px;background:var(--accent-cyan-dim,rgba(74,163,255,.16));' +
        'color:var(--accent-cyan,#4aa3ff);font-size:10px;line-height:15px}' +
        '.device-notes .dn-note{cursor:help;font-size:11px}' +
        '.device-notes .dn-edit{border:0;background:none;color:inherit;cursor:pointer;opacity:0;padding:0 2px;font:inherit}' +
        '*:hover>.device-notes .dn-edit,.device-notes .dn-edit:focus-visible{opacity:.7}' +
        '@media (hover:none){.device-notes .dn-edit{opacity:.5}}' +
        '.dn-dialog{border:1px solid var(--border-color,#263246);border-radius:8px;background:var(--bg-card,#0d1219);' +
        'color:var(--text-primary,#d7e0ee);padding:16px;width:min(420px,92vw)}' +
        '.dn-dialog::backdrop{background:rgba(0,0,0,.5)}' +
        '.dn-dialog textarea,.dn-dialog input{width:100%;box-sizing:border-box;margin:4px 0 10px;padding:6px;' +
        'background:var(--bg-tertiary,#101520);color:inherit;border:1px solid var(--border-color,#263246);border-radius:4px;font:inherit}' +
        '.dn-dialog menu{display:flex;gap:8px;justify-content:flex-end;padding:0;margin:0}' +
        '.dn-dialog .dn-error{color:var(--accent-red,#e25d5d);font-size:12px;min-height:1em}';

    function escapeAttr(text) {
        return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    /** A placeholder for a device's note and tags, filled in automatically. */
    function html(identifier, protocol) {
        if (!identifier) return '';
        return '<span class="device-notes" data-dn-id="' + escapeAttr(identifier) + '" data-dn-protocol="' +
            escapeAttr(protocol || 'other') + '"></span>';
    }

    function fill(el) {
        const id = el.dataset.dnId;
        const entry = notes[String(id).toUpperCase()];
        const parts = [];
        if (entry) {
            (entry.tags || []).forEach((tag) => {
                const chip = document.createElement('span');
                chip.className = 'dn-tag';
                chip.textContent = tag;
                parts.push(chip);
            });
            if (entry.notes) {
                const marker = document.createElement('span');
                marker.className = 'dn-note';
                marker.textContent = '🗒';
                marker.title = entry.notes;
                marker.setAttribute('aria-label', 'Note: ' + entry.notes);
                parts.push(marker);
            }
        }
        const edit = document.createElement('button');
        edit.type = 'button';
        edit.className = 'dn-edit';
        edit.textContent = '✎';
        edit.title = entry ? 'Edit note and tags' : 'Add a note or tags';
        edit.setAttribute('aria-label', edit.title + ' for ' + id);
        parts.push(edit);
        el.replaceChildren(...parts);
    }

    function refresh(root) {
        (root || document).querySelectorAll(SELECTOR).forEach(fill);
    }

    async function load() {
        try {
            const data = await (await fetch('/device-notes')).json();
            notes = (data && data.devices) || {};
        } catch (err) {
            notes = {};
        }
        refresh();
    }

    function ensureDialog() {
        if (dialog) return dialog;
        dialog = document.createElement('dialog');
        dialog.className = 'dn-dialog';
        dialog.innerHTML =
            '<form method="dialog">' +
            '<div class="dn-title" style="font-weight:600;margin-bottom:8px"></div>' +
            '<label>Note<textarea name="notes" rows="4" maxlength="2000"></textarea></label>' +
            '<label>Tags, comma separated<input name="tags" maxlength="400" placeholder="office, printer"></label>' +
            '<div class="dn-error" role="alert"></div>' +
            '<menu><button type="button" value="clear">Clear</button>' +
            '<button type="button" value="cancel">Cancel</button><button type="submit" value="save">Save</button></menu>' +
            '</form>';
        document.body.appendChild(dialog);
        dialog.addEventListener('click', (event) => {
            const button = event.target.closest('button');
            if (!button || !dialog.contains(button)) return;
            if (button.value === 'cancel') dialog.close();
            if (button.value === 'clear') save(true);
        });
        dialog.querySelector('form').addEventListener('submit', (event) => {
            event.preventDefault();
            save(false);
        });
        return dialog;
    }

    async function save(clear) {
        const form = dialog.querySelector('form');
        const error = dialog.querySelector('.dn-error');
        const id = dialog.dataset.id;
        const body = clear ? null : {
            protocol: dialog.dataset.protocol,
            notes: form.notes.value,
            tags: form.tags.value.split(',').map((t) => t.trim()).filter(Boolean),
        };
        try {
            const response = await fetch('/device-notes/' + encodeURIComponent(id), {
                method: clear ? 'DELETE' : 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: body ? JSON.stringify(body) : undefined,
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.message || 'Could not save');
            const device = data.device || {};
            if (device.notes || (device.tags || []).length) {
                notes[String(id).toUpperCase()] = { protocol: device.protocol, notes: device.notes, tags: device.tags };
            } else {
                delete notes[String(id).toUpperCase()];
            }
            refresh();
            dialog.close();
        } catch (err) {
            error.textContent = err.message;
        }
    }

    function edit(identifier, protocol) {
        const d = ensureDialog();
        const entry = notes[String(identifier).toUpperCase()] || {};
        d.dataset.id = identifier;
        d.dataset.protocol = protocol || 'other';
        d.querySelector('.dn-title').textContent = 'Note for ' + identifier;
        d.querySelector('.dn-error').textContent = '';
        const form = d.querySelector('form');
        form.notes.value = entry.notes || '';
        form.tags.value = (entry.tags || []).join(', ');
        d.showModal();
        form.notes.focus();
    }

    function start() {
        const style = document.createElement('style');
        style.textContent = STYLE;
        document.head.appendChild(style);

        // Edit buttons sit inside clickable rows: handle them first, and stop there.
        document.addEventListener('click', (event) => {
            const button = event.target.closest && event.target.closest('.device-notes .dn-edit');
            if (!button) return;
            event.preventDefault();
            event.stopPropagation();
            const holder = button.closest(SELECTOR);
            edit(holder.dataset.dnId, holder.dataset.dnProtocol);
        }, true);

        new MutationObserver((mutations) => {
            mutations.forEach((m) => m.addedNodes.forEach((node) => {
                if (node.nodeType !== 1) return;
                if (node.matches(SELECTOR)) fill(node);
                else if (node.querySelector(SELECTOR)) refresh(node);
            }));
        }).observe(document.body, { childList: true, subtree: true });

        load();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();

    return { html, edit, refresh, reload: load };
})();

window.DeviceNotes = DeviceNotes;
