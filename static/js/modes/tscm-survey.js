/**
 * TSCM Survey workspace.
 *
 * Walks a practitioner through a survey in order: the active baseline, what
 * the latest sweep shows that the baseline did not, which devices are known,
 * the threats and findings raised, and the report. Every figure comes from an
 * existing /tscm endpoint; nothing here computes analysis of its own.
 *
 * Deliberately not shown: distance, bearing or location derived from RSSI
 * (a single omnidirectional receiver cannot measure them) and the timeline's
 * movement pattern, which is inferred from RSSI variance. Where a score is
 * shown, its components are shown with it.
 *
 * Running sweeps and recording baselines stay in TSCM mode, which owns the
 * interface, SDR and agent selection; this workspace links there.
 */
const TscmSurvey = (function () {
    'use strict';

    const STATUS_POLL_MS = 10000;
    let root = null;
    let pollTimer = null;
    let cases = [];
    let activeBaseline = null;
    let latestSweep = null;
    let showAcknowledged = false;

    // ---------------------------------------------------------------- helpers

    /** Build an element. Children are nodes or text; text is never parsed as HTML. */
    function el(tag, attrs, ...children) {
        const node = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([key, value]) => {
            if (value === undefined || value === null || value === false) return;
            if (key === 'class') node.className = value;
            else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
            else node.setAttribute(key, value === true ? '' : value);
        });
        children.flat().forEach((child) => {
            if (child === null || child === undefined || child === false) return;
            node.append(child instanceof Node ? child : String(child));
        });
        return node;
    }

    async function api(path, options) {
        const init = options ? { ...options } : {};
        if (init.body && typeof init.body !== 'string') {
            init.body = JSON.stringify(init.body);
            init.headers = { 'Content-Type': 'application/json' };
        }
        const resp = await fetch(path, init);
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status === 'error') {
            throw new Error(data.message || `Request failed (${resp.status})`);
        }
        return data;
    }

    /** SQLite stores UTC without an offset; show it in the viewer's local time. */
    function localTime(value) {
        if (!value) return '—';
        const text = String(value);
        const utc = /[zZ]|[+-]\d\d:?\d\d$/.test(text) ? text : `${text.replace(' ', 'T')}Z`;
        const date = new Date(utc);
        return Number.isNaN(date.getTime()) ? text : date.toLocaleString();
    }

    function message(text, kind) {
        return el('div', { class: `tscm-survey-message${kind ? ` ${kind}` : ''}` }, text);
    }

    function button(label, onclick, kind) {
        return el('button', { type: 'button', class: `tscm-survey-btn${kind ? ` ${kind}` : ''}`, onclick }, label);
    }

    /** Run an action from a button, showing failure beside it. */
    async function act(btn, fn) {
        btn.disabled = true;
        const note = btn.parentNode.querySelector('.tscm-survey-inline-error');
        if (note) note.remove();
        try {
            await fn();
        } catch (err) {
            btn.after(el('span', { class: 'tscm-survey-inline-error' }, err.message));
        } finally {
            btn.disabled = false;
        }
    }

    function fill(id, ...nodes) {
        const target = root && root.querySelector(`#${id}`);
        if (target) target.replaceChildren(...nodes);
    }

    async function load(id, fn) {
        fill(id, message('Loading…'));
        try {
            fill(id, ...[].concat(await fn()));
        } catch (err) {
            fill(id, message(err.message, 'error'));
        }
    }

    function keyValues(pairs) {
        return el('dl', { class: 'tscm-survey-kv' },
            pairs.filter(([, v]) => v !== undefined && v !== null && v !== '').flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v)]));
    }

    /** A score always appears beside what it is made of. */
    function indicatorList(indicators, modifier) {
        const items = (indicators || []).map((i) =>
            el('li', {}, el('code', {}, `${i.type || 'unknown'} +${i.score ?? 0}`), ' ', i.description || ''));
        if (modifier) items.push(el('li', {}, el('code', {}, `known-device adjustment ${modifier > 0 ? '+' : ''}${modifier}`)));
        return items.length ? el('ul', { class: 'tscm-survey-indicators' }, items) : null;
    }

    function openTscmMode() {
        if (typeof switchMode === 'function') switchMode('tscm');
    }

    // ------------------------------------------------------ 1. baseline

    async function renderBaseline() {
        const [active, recording, list] = await Promise.all([
            api('/tscm/baseline/active'),
            api('/tscm/baseline/status'),
            api('/tscm/baselines'),
        ]);
        activeBaseline = active.baseline;
        const nodes = [];

        if (recording.recording) {
            nodes.push(message(
                `Recording a baseline now: ${recording.wifi_count} Wi-Fi, ${recording.wifi_client_count} Wi-Fi clients, ` +
                `${recording.bt_count} Bluetooth, ${recording.rf_count} RF so far. Stop it in TSCM mode.`, 'info'));
        }

        if (activeBaseline) {
            const b = activeBaseline;
            nodes.push(keyValues([
                ['Active baseline', b.name],
                ['Captured', localTime(b.created_at)],
                ['Location', b.location],
                ['Devices', `${(b.wifi_networks || []).length} Wi-Fi, ${(b.bt_devices || []).length} Bluetooth, ${(b.rf_frequencies || []).length} RF`],
            ]));
            const { health } = await api(`/tscm/baseline/${b.id}/health`);
            nodes.push(el('div', { class: `tscm-survey-health ${health.status}` },
                el('strong', {}, `Health: ${health.status} (score ${health.score})`),
                el('ul', {},
                    el('li', {}, `Age: ${health.age_hours} hour${health.age_hours === 1 ? '' : 's'} (noisy after 72, stale after 168)`),
                    el('li', {}, `Devices: ${health.total_devices} (fewer than 3 lowers the score)`),
                    (health.reasons || []).map((r) => el('li', {}, r)))));
        } else {
            nodes.push(message('No baseline is active. Activate one below, or record one in TSCM mode.', 'warn'));
        }

        const rows = (list.baselines || []).map((b) => el('tr', {},
            el('td', {}, b.name, b.is_active ? el('span', { class: 'tscm-survey-tag' }, 'active') : null),
            el('td', {}, b.location || '—'),
            el('td', {}, localTime(b.created_at)),
            el('td', {}, b.is_active ? null : button('Activate', (e) => act(e.target, async () => {
                await api(`/tscm/baseline/${b.id}/activate`, { method: 'POST' });
                await refresh();
            })))));
        nodes.push(rows.length
            ? el('table', { class: 'tscm-survey-table' },
                el('thead', {}, el('tr', {}, ['Baseline', 'Location', 'Captured', ''].map((h) => el('th', {}, h)))),
                el('tbody', {}, rows))
            : message('No baselines recorded yet.'));
        nodes.push(el('div', { class: 'tscm-survey-actions' }, button('Record a baseline in TSCM mode', openTscmMode)));
        return nodes;
    }

    // ------------------------------------------------------ 2. sweep & diff

    async function renderSweep() {
        const [status, presets] = await Promise.all([api('/tscm/sweep/status'), api('/tscm/presets')]);
        latestSweep = status.sweep || null;
        const nodes = [];

        if (latestSweep) {
            const s = latestSweep;
            const r = s.results || {};
            nodes.push(keyValues([
                ['Latest sweep', `#${s.id} (${s.sweep_type || 'standard'})`],
                ['Status', status.running ? 'running' : s.status],
                ['Started', localTime(s.started_at)],
                ['Finished', s.completed_at ? localTime(s.completed_at) : null],
                ['Detected', s.results ? `${r.wifi_count ?? 0} Wi-Fi, ${r.wifi_client_count ?? 0} Wi-Fi clients, ${r.bt_count ?? 0} Bluetooth, ${r.rf_count ?? 0} RF` : 'results appear when the sweep completes'],
            ]));
        } else {
            nodes.push(message('No sweep has run since the server started. Run one in TSCM mode.', 'warn'));
        }

        const presetList = el('div', { class: 'tscm-survey-presets' },
            Object.entries(presets.presets || {}).map(([key, p]) => {
                const details = el('div', { class: 'tscm-survey-preset-details' });
                return el('details', {
                    ontoggle: (e) => {
                        if (!e.target.open || details.childNodes.length) return;
                        api(`/tscm/presets/${encodeURIComponent(key)}`).then(({ preset }) => {
                            details.replaceChildren(
                                el('div', {}, `Bands: ${['wifi', 'bluetooth', 'rf'].filter((b) => preset[b]).join(', ') || 'none'}`),
                                el('ul', {}, (preset.ranges || []).map((rg) => el('li', {}, `${rg.name}: ${rg.start}–${rg.end} MHz`))));
                        }).catch((err) => details.replaceChildren(message(err.message, 'error')));
                    },
                }, el('summary', {}, `${p.name}: ${p.description} (${Math.round((p.duration_seconds || 0) / 60)} min)`), details);
            }));
        nodes.push(el('h4', {}, 'Sweep types'), presetList);
        nodes.push(el('div', { class: 'tscm-survey-actions' }, button('Run a sweep in TSCM mode', openTscmMode)));

        nodes.push(el('h4', {}, 'Compared with the baseline'));
        if (!activeBaseline) {
            nodes.push(message('Activate a baseline to compare against.'));
        } else if (!latestSweep || !latestSweep.results) {
            nodes.push(message('The comparison appears once a sweep completes.'));
        } else {
            nodes.push(await renderDiff(activeBaseline.id, latestSweep.id));
        }
        return nodes;
    }

    async function renderDiff(baselineId, sweepId) {
        const { diff } = await api(`/tscm/baseline/diff/${baselineId}/${sweepId}`);
        const s = diff.summary || {};
        const deviceRows = (devices, withKnown) => devices.map((d) => el('tr', {},
            el('td', {}, el('code', {}, d.identifier)),
            el('td', {}, d.protocol),
            el('td', {}, d.description || d.change_type || ''),
            el('td', {}, withKnown ? markKnownButton(d.identifier, d.protocol, (d.details || {}).name || (d.details || {}).ssid) : null)));
        const table = (title, devices, withKnown) => devices && devices.length
            ? [el('h5', {}, `${title} (${devices.length})`), el('table', { class: 'tscm-survey-table' }, el('tbody', {}, deviceRows(devices, withKnown)))]
            : [];
        return el('div', {},
            keyValues([
                ['New since baseline', s.new_devices],
                ['Missing since baseline', s.missing_devices],
                ['Changed', s.changed_devices],
                ['Expected / unexpected changes', `${s.expected_changes ?? 0} / ${s.unexpected_changes ?? 0}`],
                ['Baseline health', diff.health ? `${diff.health.status} (score ${diff.health.score}): ${(diff.health.reasons || []).join('; ') || 'no issues'}` : null],
            ]),
            table('New devices', diff.new_devices, true),
            table('Missing devices', diff.missing_devices, false),
            table('Changed devices', diff.changed_devices, false),
            diff.disclaimer ? el('p', { class: 'tscm-survey-disclaimer' }, diff.disclaimer) : null);
    }

    // ------------------------------------------------------ 3. known devices

    function markKnownButton(identifier, protocol, name) {
        return button('Mark known', (e) => act(e.target, async () => {
            await api('/tscm/known-devices', { method: 'POST', body: { identifier, protocol, name: name || undefined } });
            e.target.replaceWith(el('span', { class: 'tscm-survey-tag known' }, 'known'));
            load('tscmSurveyKnown', renderKnown);
        }));
    }

    async function renderKnown() {
        const [findings, registry] = await Promise.all([api('/tscm/findings'), api('/tscm/known-devices')]);
        const byRisk = (findings.findings || {}).devices || {};
        const profiles = ['high_interest', 'needs_review', 'informational'].flatMap((risk) => byRisk[risk] || []);

        const deviceRows = profiles.map((p) => el('tr', {},
            el('td', {}, el('code', {}, p.identifier), p.name ? el('div', { class: 'tscm-survey-dim' }, p.name) : null),
            el('td', {}, p.protocol),
            el('td', {}, p.risk_level.replace('_', ' ')),
            el('td', {}, `score ${p.total_score}`, indicatorList(p.indicators, p.score_modifier)),
            el('td', {}, p.known_device
                ? [el('span', { class: 'tscm-survey-tag known' }, p.known_device_name || 'known'), ' ', removeKnownButton(p.identifier)]
                : markKnownButton(p.identifier, p.protocol, p.name))));

        const registryRows = (registry.devices || []).map((d) => el('tr', {},
            el('td', {}, el('code', {}, d.identifier)),
            el('td', {}, d.name || '—'),
            el('td', {}, d.protocol),
            el('td', {}, `${d.scope || 'global'}${d.location ? ` (${d.location})` : ''}`),
            el('td', {}, `score ${d.score_modifier ?? -2}`),
            el('td', {}, removeKnownButton(d.identifier))));

        const lookupInput = el('input', { type: 'text', class: 'tscm-survey-input', placeholder: 'MAC, BSSID or frequency' });
        const lookupResult = el('span', { class: 'tscm-survey-dim' });
        const lookup = el('form', {
            class: 'tscm-survey-actions',
            onsubmit: (e) => {
                e.preventDefault();
                const id = lookupInput.value.trim();
                if (!id) return;
                const location = activeBaseline && activeBaseline.location;
                api(`/tscm/known-devices/check/${encodeURIComponent(id)}${location ? `?location=${encodeURIComponent(location)}` : ''}`)
                    .then((r) => { lookupResult.textContent = r.is_known ? `Known: ${(r.details || {}).name || id}` : 'Not in the known-device registry'; })
                    .catch((err) => { lookupResult.textContent = err.message; });
            },
        }, lookupInput, el('button', { type: 'submit', class: 'tscm-survey-btn' }, 'Check'), lookupResult);

        return [
            el('h4', {}, `Devices seen in this session (${profiles.length})`),
            profiles.length
                ? el('table', { class: 'tscm-survey-table' },
                    el('thead', {}, el('tr', {}, ['Device', 'Protocol', 'Tier', 'Score and components', 'Known'].map((h) => el('th', {}, h)))),
                    el('tbody', {}, deviceRows))
                : message('No devices profiled yet. They appear while a sweep runs.'),
            el('h4', {}, `Known-device registry (${registryRows.length})`),
            registryRows.length
                ? el('table', { class: 'tscm-survey-table' }, el('tbody', {}, registryRows))
                : message('No devices marked known.'),
            el('p', { class: 'tscm-survey-dim' }, 'Known devices stay in reports; their score is lowered by the adjustment shown.'),
            lookup,
        ];
    }

    function removeKnownButton(identifier) {
        return button('Remove', (e) => act(e.target, async () => {
            await api(`/tscm/known-devices/${encodeURIComponent(identifier)}`, { method: 'DELETE' });
            load('tscmSurveyKnown', renderKnown);
        }), 'subtle');
    }

    // ------------------------------------------------------ 4. threats & findings

    async function renderThreats() {
        const query = showAcknowledged ? '' : '?acknowledged=false';
        const [summary, list, caseList] = await Promise.all([
            api('/tscm/threats/summary'), api(`/tscm/threats${query}`), api('/tscm/cases'),
        ]);
        cases = caseList.cases || [];
        const s = summary.summary || {};

        const rows = (list.threats || []).map((t) => {
            const notes = el('input', { type: 'text', class: 'tscm-survey-input', placeholder: 'Resolution notes' });
            const caseSelect = el('select', { class: 'tscm-survey-input' },
                el('option', { value: '' }, 'Add to case…'),
                cases.map((c) => el('option', { value: c.id }, c.name)));
            return el('tr', {},
                el('td', {}, el('span', { class: `tscm-survey-severity ${t.severity}` }, t.severity)),
                el('td', {}, t.threat_type, el('div', { class: 'tscm-survey-dim' }, t.name || t.identifier || '')),
                el('td', {}, localTime(t.detected_at), t.sweep_id ? el('div', { class: 'tscm-survey-dim' }, `sweep #${t.sweep_id}`) : null),
                el('td', {}, t.acknowledged
                    ? [el('span', { class: 'tscm-survey-tag known' }, 'resolved'), t.notes ? el('div', { class: 'tscm-survey-dim' }, t.notes) : null]
                    : el('div', { class: 'tscm-survey-actions' }, notes, button('Resolve', (e) => act(e.target, async () => {
                        await api(`/tscm/threats/${t.id}`, { method: 'PUT', body: { acknowledge: true, notes: notes.value.trim() || null } });
                        load('tscmSurveyThreats', renderThreats);
                    })))),
                el('td', {}, cases.length ? el('div', { class: 'tscm-survey-actions' }, caseSelect, button('Add', (e) => act(e.target, async () => {
                    if (!caseSelect.value) throw new Error('Choose a case');
                    await api(`/tscm/cases/${caseSelect.value}/threats/${t.id}`, { method: 'POST' });
                    e.target.replaceWith(el('span', { class: 'tscm-survey-tag' }, 'added'));
                }))) : el('span', { class: 'tscm-survey-dim' }, 'no cases')));
        });

        const toggle = el('label', { class: 'tscm-survey-dim' },
            el('input', { type: 'checkbox', checked: showAcknowledged, onchange: (e) => { showAcknowledged = e.target.checked; load('tscmSurveyThreats', renderThreats); } }),
            ' show resolved');

        return [
            keyValues([['Unresolved', `${s.total ?? 0} (critical ${s.critical ?? 0}, high ${s.high ?? 0}, medium ${s.medium ?? 0}, low ${s.low ?? 0})`]]),
            toggle,
            rows.length
                ? el('table', { class: 'tscm-survey-table' },
                    el('thead', {}, el('tr', {}, ['Severity', 'Threat', 'Detected', 'Status', 'Case'].map((h) => el('th', {}, h)))),
                    el('tbody', {}, rows))
                : message(showAcknowledged ? 'No threats recorded.' : 'No unresolved threats.'),
            renderCaseNote(),
        ];
    }

    function renderCaseNote() {
        if (!cases.length) return message('Create a case in TSCM mode to attach threats and notes.');
        const caseSelect = el('select', { class: 'tscm-survey-input' }, cases.map((c) => el('option', { value: c.id }, c.name)));
        const text = el('textarea', { class: 'tscm-survey-input', rows: 2, placeholder: 'Note for the case file' });
        return el('div', {}, el('h4', {}, 'Case note'), el('div', { class: 'tscm-survey-actions' }, caseSelect, text,
            button('Add note', (e) => act(e.target, async () => {
                if (!text.value.trim()) throw new Error('Write a note first');
                await api(`/tscm/cases/${caseSelect.value}/notes`, { method: 'POST', body: { content: text.value.trim() } });
                text.value = '';
                e.target.after(el('span', { class: 'tscm-survey-dim' }, ' added'));
            }))));
    }

    async function renderFindings() {
        const [high, corr] = await Promise.all([api('/tscm/findings/high-interest'), api('/tscm/findings/correlations')]);
        const cards = (high.devices || []).map((d) => {
            const more = el('div', { class: 'tscm-survey-more' });
            const loadInto = (title, fn) => (e) => act(e.target, async () => {
                more.replaceChildren(el('h5', {}, title), ...[].concat(await fn()));
            });
            return el('div', { class: 'tscm-survey-card' },
                el('div', {}, el('code', {}, d.identifier), ` ${d.protocol}`, d.name ? ` · ${d.name}` : ''),
                el('div', {}, `score ${d.total_score}`), indicatorList(d.indicators, d.score_modifier),
                el('div', { class: 'tscm-survey-actions' },
                    button('Playbook', loadInto('Playbook', async () => {
                        const { playbook } = await api(`/tscm/findings/${encodeURIComponent(d.identifier)}/playbook`);
                        return [el('div', {}, `${playbook.playbook_id}: ${playbook.title}`),
                            el('ol', {}, (playbook.steps || []).map((st) => el('li', {}, el('strong', {}, st.action), ` ${st.details || ''}`,
                                st.safety_note ? el('div', { class: 'tscm-survey-dim' }, `Safety: ${st.safety_note}`) : null)))];
                    }), 'subtle'),
                    button('Timeline', loadInto('Timeline', async () => {
                        const { timeline } = await api(`/tscm/device/${encodeURIComponent(d.identifier)}/timeline?protocol=${encodeURIComponent(d.protocol)}`);
                        const m = timeline.metrics || {};
                        const sig = timeline.signal || {};
                        const meet = timeline.meeting_correlation || {};
                        return keyValues([
                            ['First seen', m.first_seen ? new Date(m.first_seen).toLocaleString() : null],
                            ['Last seen', m.last_seen ? new Date(m.last_seen).toLocaleString() : null],
                            ['Observations', m.total_observations ?? (timeline.observations || []).length],
                            ['Present', m.presence_ratio !== undefined ? `${Math.round(m.presence_ratio * 100)}% of the sweep` : null],
                            ['RSSI (dBm)', sig.rssi_min !== undefined && sig.rssi_min !== null ? `${sig.rssi_min} to ${sig.rssi_max}, mean ${sig.rssi_mean}` : null],
                            ['During meetings', meet.correlated ? `${meet.observations_during_meeting} observations` : 'no'],
                        ]);
                    }), 'subtle')),
                more);
        });
        const corrRows = (corr.correlations || []).map((c) => el('li', {},
            `${c.description} (${(c.devices || []).join(' + ')}), adds +${c.score_boost ?? 0} to each device's score`));
        return [
            el('h4', {}, `High-interest devices (${cards.length})`),
            cards.length ? el('div', { class: 'tscm-survey-cards' }, cards) : message('No high-interest devices in this session.'),
            high.disclaimer ? el('p', { class: 'tscm-survey-disclaimer' }, high.disclaimer) : null,
            el('h4', {}, `Cross-protocol correlations (${corrRows.length})`),
            corrRows.length ? el('ul', {}, corrRows) : message('None found.'),
            corr.explanation ? el('p', { class: 'tscm-survey-disclaimer' }, corr.explanation) : null,
        ];
    }

    // ------------------------------------------------------ 5. report

    function renderReport() {
        const site = el('input', { type: 'text', class: 'tscm-survey-input', placeholder: 'Site name', maxlength: 200 });
        const examiner = el('input', { type: 'text', class: 'tscm-survey-input', placeholder: 'Examiner', maxlength: 200 });
        const tiers = ['high_interest', 'needs_review', 'informational'].map((c) =>
            el('label', {}, el('input', { type: 'checkbox', value: c, checked: true }), ` ${c.replace('_', ' ')}`));
        const link = (path, extra) => () => {
            if (!latestSweep) return;
            const params = new URLSearchParams({ sweep_id: latestSweep.id, ...extra });
            if (site.value.trim()) params.set('site_name', site.value.trim());
            if (examiner.value.trim()) params.set('examiner_name', examiner.value.trim());
            const chosen = tiers.map((t) => t.querySelector('input')).filter((i) => i.checked).map((i) => i.value);
            if (chosen.length && chosen.length < 3) params.set('categories', chosen.join(','));
            window.open(`${path}?${params}`, '_blank', 'noopener');
        };
        if (!latestSweep) return message('Run a sweep first; the report covers the latest sweep.');
        return [
            el('div', { class: 'tscm-survey-actions' }, site, examiner),
            el('div', { class: 'tscm-survey-actions' }, tiers),
            el('div', { class: 'tscm-survey-actions' },
                button(`Client report (sweep #${latestSweep.id})`, link('/tscm/report/pdf')),
                button('Technical annex (JSON)', link('/tscm/report/annex', { format: 'json' }), 'subtle'),
                button('Technical annex (CSV)', link('/tscm/report/annex', { format: 'csv' }), 'subtle')),
        ];
    }

    // ------------------------------------------------------ lifecycle

    function skeleton() {
        const step = (n, id, title, intro, ...extra) => el('section', { class: 'tscm-survey-step', id: `${id}Step` },
            el('h3', {}, el('span', { class: 'tscm-survey-num' }, n), title),
            intro ? el('p', { class: 'tscm-survey-dim' }, intro) : null,
            el('div', { id }),
            extra);
        root.replaceChildren(
            el('div', { class: 'tscm-survey-header' },
                el('div', {}, el('strong', {}, 'TSCM Survey'), el('span', { class: 'tscm-survey-dim' }, ' baseline → sweep → known devices → findings → report')),
                button('Refresh', () => refresh(), 'subtle')),
            step(1, 'tscmSurveyBaseline', 'Baseline', 'Which baseline is active, and when was it captured?'),
            step(2, 'tscmSurveySweep', 'Sweep', 'What does the latest sweep show that the baseline did not?'),
            step(3, 'tscmSurveyKnown', 'Known devices', 'Which devices are known, and which are not?'),
            step(4, 'tscmSurveyThreats', 'Threats and findings', 'What has been raised, and is it resolved?',
                el('div', { id: 'tscmSurveyFindings' })),
            step(5, 'tscmSurveyReport', 'Report', 'Generate the client report and technical annexes for the latest sweep.'));
    }

    async function refresh() {
        await load('tscmSurveyBaseline', renderBaseline);  // sets activeBaseline, used by the sweep diff
        await load('tscmSurveySweep', renderSweep);         // sets latestSweep, used by the report
        load('tscmSurveyKnown', renderKnown);
        load('tscmSurveyThreats', renderThreats);
        load('tscmSurveyFindings', renderFindings);
        fill('tscmSurveyReport', ...[].concat(renderReport()));
    }

    function scrollTo(step) {
        const target = root && root.querySelector(`#${step}Step`);
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function init() {
        root = document.getElementById('tscmSurveyVisuals');
        if (!root) return;
        root.style.display = 'flex';
        skeleton();
        refresh();
        // Keep the sweep step current while a sweep runs elsewhere.
        clearInterval(pollTimer);
        pollTimer = setInterval(() => {
            if (document.hidden) return;
            api('/tscm/sweep/status').then((s) => {
                const before = latestSweep ? `${latestSweep.id}:${latestSweep.status}` : '';
                const now = s.sweep ? `${s.sweep.id}:${s.sweep.status}` : '';
                if (before !== now) refresh();
            }).catch(() => {});
        }, STATUS_POLL_MS);
    }

    function destroy() {
        clearInterval(pollTimer);
        pollTimer = null;
        if (root) root.style.display = 'none';
    }

    return { init, destroy, refresh, scrollTo, openTscmMode };
})();

window.TscmSurvey = TscmSurvey;
