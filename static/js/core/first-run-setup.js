const FirstRunSetup = (function() {
    'use strict';

    const COMPLETE_KEY = 'intercept.setup.complete.v1';
    const DEFAULT_MODE_KEY = 'intercept.default_mode';

    let overlayEl = null;
    let depsStatusEl = null;
    let locationStatusEl = null;
    let notifyStatusEl = null;
    let modeStatusEl = null;
    let modeSelectEl = null;
    let uiTierStatusEl = null;
    let hardwareStatusEl = null;
    let hardwareStepEl = null;

    let dependencyReady = null;
    let serverStatus = null;

    function init() {
        buildDOM();
        maybeShow();
    }

    async function maybeShow() {
        if (localStorage.getItem(COMPLETE_KEY) === 'true') return;

        try {
            const response = await fetch('/setup/status');
            if (response.ok) {
                const payload = await response.json();
                serverStatus = payload;
                if (payload.complete) {
                    localStorage.setItem(COMPLETE_KEY, 'true');
                    return;
                }
                applyServerHints(payload);
            }
        } catch (_err) {
            serverStatus = null;
        }

        if (localStorage.getItem('disclaimerAccepted') === 'true') {
            open();
            refreshStatuses();
            return;
        }

        let attempts = 0;
        const waitTimer = setInterval(() => {
            attempts += 1;
            if (localStorage.getItem(COMPLETE_KEY) === 'true') {
                clearInterval(waitTimer);
                return;
            }
            if (localStorage.getItem('disclaimerAccepted') === 'true') {
                clearInterval(waitTimer);
                open();
                refreshStatuses();
            }
            if (attempts > 30) {
                clearInterval(waitTimer);
            }
        }, 1000);
    }

    function buildDOM() {
        overlayEl = document.createElement('div');
        overlayEl.id = 'firstRunSetupOverlay';
        overlayEl.className = 'setup-overlay';

        const modal = document.createElement('div');
        modal.className = 'setup-modal';

        const header = document.createElement('div');
        header.className = 'setup-header';

        const headingWrap = document.createElement('div');
        const title = document.createElement('h2');
        title.className = 'setup-title';
        title.textContent = 'Quick Setup';
        headingWrap.appendChild(title);

        const subtitle = document.createElement('p');
        subtitle.className = 'setup-subtitle';
        subtitle.textContent = 'Complete these checks once so all modes work reliably.';
        headingWrap.appendChild(subtitle);

        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'setup-close';
        closeBtn.textContent = '×';
        closeBtn.setAttribute('aria-label', 'Close setup assistant');
        closeBtn.addEventListener('click', close);

        header.appendChild(headingWrap);
        header.appendChild(closeBtn);

        const content = document.createElement('div');
        content.className = 'setup-content';

        const depsStep = createStep(
            'Dependencies',
            'Verify required tools are installed for enabled modes.',
            (statusEl, actionsEl) => {
                depsStatusEl = statusEl;

                const checkBtn = buildButton('Recheck', () => checkDependencies());
                const openToolsBtn = buildButton('Open Tools', () => {
                    if (typeof showSettings === 'function') showSettings();
                    if (typeof switchSettingsTab === 'function') switchSettingsTab('tools');
                });
                actionsEl.appendChild(checkBtn);
                actionsEl.appendChild(openToolsBtn);
            }
        );

        hardwareStepEl = createStep(
            'Unraid / USB SDR',
            'Confirm USB passthrough so RTL-SDR and HackRF dongles appear in this container.',
            (statusEl, actionsEl) => {
                hardwareStatusEl = statusEl;
                actionsEl.appendChild(buildButton('Recheck USB', refreshHardwareStatus));
            }
        );
        hardwareStepEl.hidden = true;

        const locationStep = createStep(
            'Observer Location',
            'Set latitude/longitude for pass prediction and mapping features.',
            (statusEl, actionsEl) => {
                locationStatusEl = statusEl;
                actionsEl.appendChild(buildButton('Open Location', () => {
                    if (typeof showSettings === 'function') showSettings();
                    if (typeof switchSettingsTab === 'function') switchSettingsTab('location');
                }));
                actionsEl.appendChild(buildButton('Recheck', refreshStatuses));
            }
        );

        const notifyStep = createStep(
            'Desktop Alerts',
            'Allow notifications so high-priority alerts are visible when the tab is hidden.',
            (statusEl, actionsEl) => {
                notifyStatusEl = statusEl;
                actionsEl.appendChild(buildButton('Enable Notifications', requestNotifications));
            }
        );

        const modeStep = createStep(
            'Default Start Mode',
            'Choose which mode should be selected by default.',
            (statusEl, actionsEl) => {
                modeStatusEl = statusEl;

                modeSelectEl = document.createElement('select');
                modeSelectEl.className = 'setup-btn';
                const modes = [
                    ['pager', 'Pager'],
                    ['sensor', '433MHz'],
                    ['rtlamr', 'Meters'],
                    ['waterfall', 'Waterfall'],
                    ['wifi', 'WiFi'],
                    ['bluetooth', 'Bluetooth'],
                    ['bt_locate', 'BT Locate'],
                    ['aprs', 'APRS'],
                    ['satellite', 'Satellite'],
                    ['sstv', 'ISS SSTV'],
                    ['weathersat', 'Weather Sat'],
                    ['sstv_general', 'HF SSTV'],
                ];
                for (const [value, label] of modes) {
                    const opt = document.createElement('option');
                    opt.value = value;
                    opt.textContent = label;
                    modeSelectEl.appendChild(opt);
                }

                const savedDefaultMode = localStorage.getItem(DEFAULT_MODE_KEY);
                if (savedDefaultMode) {
                    const normalizedMode = savedDefaultMode === 'listening' ? 'waterfall' : savedDefaultMode;
                    modeSelectEl.value = normalizedMode;
                    if (normalizedMode !== savedDefaultMode) {
                        localStorage.setItem(DEFAULT_MODE_KEY, normalizedMode);
                    }
                }

                actionsEl.appendChild(modeSelectEl);
                actionsEl.appendChild(buildButton('Save', () => {
                    const selected = modeSelectEl.value || 'pager';
                    localStorage.setItem(DEFAULT_MODE_KEY, selected);
                    refreshStatuses();
                    if (typeof showAppToast === 'function') {
                        showAppToast('Default Mode Saved', `New sessions will default to ${selected}.`, 'info');
                    }
                }));
            }
        );

        const tierStep = createStep(
            'Display Mode',
            'Choose how the interface looks. You can change this later with the toggle in the nav bar.',
            (statusEl, actionsEl) => {
                uiTierStatusEl = statusEl;

                const btnWrap = document.createElement('div');
                btnWrap.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:4px;';

                function makeTierBtn(tier, title, desc, previewFn) {
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'setup-btn';
                    btn.style.cssText = 'display:flex;flex-direction:column;align-items:flex-start;height:auto;padding:10px;text-align:left;gap:4px;';

                    const preview = document.createElement('div');
                    preview.style.cssText = 'width:100%;height:48px;border-radius:3px;margin-bottom:4px;overflow:hidden;';
                    previewFn(preview);

                    const titleEl = document.createElement('strong');
                    titleEl.style.cssText = 'font-size:12px;';
                    titleEl.textContent = title;

                    const descEl = document.createElement('span');
                    descEl.style.cssText = 'font-size:10px;color:var(--text-dim);white-space:normal;line-height:1.3;';
                    descEl.textContent = desc;

                    btn.appendChild(preview);
                    btn.appendChild(titleEl);
                    btn.appendChild(descEl);

                    btn.addEventListener('click', () => {
                        document.documentElement.setAttribute('data-ui-tier', tier);
                        localStorage.setItem('intercept-ui-tier', tier);
                        refreshStatuses();
                        if (typeof showAppToast === 'function') {
                            showAppToast('Display Mode Set', `Switched to ${title} mode.`, 'info');
                        }
                    });
                    return btn;
                }

                const leanBtn = makeTierBtn('lean', 'Lean', 'No effects — for Raspberry Pi or older hardware.', (el) => {
                    el.style.cssText += 'background:#111;border:1px solid #222;display:flex;flex-direction:column;justify-content:center;padding:6px;gap:3px;';
                    el.innerHTML = '<div style="display:flex;justify-content:space-between;font-size:7px;color:#aaa;font-family:monospace;"><span>INTERCEPT</span><span style="color:#4a4;">● LIVE</span></div><div style="font-size:7px;color:#888;font-family:monospace;margin-top:2px;">ADS-B ........... 247<br>TSCM ............ 3 ⚠</div>';
                });

                const enhancedBtn = makeTierBtn('enhanced', 'Enhanced', 'Signals teal console — for desktop or laptop.', (el) => {
                    el.style.cssText += 'background:#000202;border:1px solid rgba(46,125,138,0.3);display:flex;flex-direction:column;justify-content:center;padding:6px;gap:3px;';
                    el.innerHTML = '<div style="display:flex;justify-content:space-between;font-size:7px;color:#2e7d8a;font-family:monospace;letter-spacing:2px;"><span>INTERCEPT</span><span style="opacity:0.6;">14:27Z</span></div><div style="border-left:2px solid #2e7d8a;padding-left:4px;margin-top:4px;font-size:8px;color:#2e7d8a;font-family:monospace;font-weight:700;">247 ADS-B</div>';
                });

                btnWrap.appendChild(leanBtn);
                btnWrap.appendChild(enhancedBtn);
                actionsEl.appendChild(btnWrap);
            }
        );

        content.appendChild(depsStep);
        content.appendChild(hardwareStepEl);
        content.appendChild(locationStep);
        content.appendChild(notifyStep);
        content.appendChild(modeStep);
        content.appendChild(tierStep);

        const footer = document.createElement('div');
        footer.className = 'setup-footer';

        const note = document.createElement('span');
        note.className = 'setup-footer-note';
        note.textContent = 'You can reopen these options anytime in Settings.';

        const footerActions = document.createElement('div');
        footerActions.style.display = 'inline-flex';
        footerActions.style.gap = '8px';

        const laterBtn = buildButton('Remind Me Later', close);
        const completeBtn = buildButton('Mark Setup Complete', completeSetup, true);
        completeBtn.id = 'setupCompleteBtn';

        footerActions.appendChild(laterBtn);
        footerActions.appendChild(completeBtn);

        footer.appendChild(note);
        footer.appendChild(footerActions);

        modal.appendChild(header);
        modal.appendChild(content);
        modal.appendChild(footer);

        overlayEl.appendChild(modal);
        document.body.appendChild(overlayEl);
    }

    function applyServerHints(payload) {
        if (!payload || typeof payload !== 'object') return;
        const platform = payload.platform || {};
        if (hardwareStepEl) {
            hardwareStepEl.hidden = !(platform.unraid || platform.docker);
        }
        const observer = payload.observer || {};
        if (observer.configured && observer.lat != null && observer.lon != null) {
            if (!localStorage.getItem('observerLat')) {
                localStorage.setItem('observerLat', String(observer.lat));
            }
            if (!localStorage.getItem('observerLon')) {
                localStorage.setItem('observerLon', String(observer.lon));
            }
        }
        refreshHardwareStatus();
    }

    async function refreshHardwareStatus() {
        if (!hardwareStatusEl) return;
        hardwareStatusEl.textContent = 'Checking...';
        let status = serverStatus;
        try {
            const response = await fetch('/setup/status');
            if (response.ok) {
                status = await response.json();
                serverStatus = status;
            }
        } catch (_err) {
            /* keep last known status */
        }
        const platform = (status && status.platform) || {};
        const usb = Boolean(platform.usb_passthrough);
        const devices = (status && status.sdr_devices) || [];
        const fuse = Boolean(status && status.storage && status.storage.fuse_or_network);
        let label = usb ? 'USB bus mapped' : 'USB bus not visible';
        if (usb && devices.length) {
            label = `${devices.length} SDR device(s) cached`;
        } else if (usb) {
            label = 'USB mapped — plug in a dongle and open Devices';
        }
        if (fuse) {
            label += ' · use cache disk for instance/';
        }
        setStatus(hardwareStatusEl, usb, label);
        if (hardwareStepEl) {
            hardwareStepEl.hidden = !(platform.unraid || platform.docker);
        }
    }

    function createStep(title, description, initActions) {
        const root = document.createElement('div');
        root.className = 'setup-step';

        const header = document.createElement('div');
        header.className = 'setup-step-header';

        const titleEl = document.createElement('span');
        titleEl.className = 'setup-step-title';
        titleEl.textContent = title;

        const statusEl = document.createElement('span');
        statusEl.className = 'setup-step-status';
        statusEl.textContent = 'Pending';

        header.appendChild(titleEl);
        header.appendChild(statusEl);

        const descEl = document.createElement('p');
        descEl.className = 'setup-step-desc';
        descEl.textContent = description;

        const actionsEl = document.createElement('div');
        actionsEl.className = 'setup-step-actions';

        if (typeof initActions === 'function') {
            initActions(statusEl, actionsEl);
        }

        root.appendChild(header);
        root.appendChild(descEl);
        root.appendChild(actionsEl);
        return root;
    }

    function buildButton(label, onClick, primary) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `setup-btn${primary ? ' primary' : ''}`;
        btn.textContent = label;
        btn.addEventListener('click', onClick);
        return btn;
    }

    async function checkDependencies() {
        if (depsStatusEl) depsStatusEl.textContent = 'Checking...';
        try {
            const response = await fetch('/dependencies');
            const data = await response.json();
            if (data.status !== 'success') {
                dependencyReady = false;
            } else {
                const modes = Object.values(data.modes || {});
                dependencyReady = modes.every((modeInfo) => Boolean(modeInfo.ready));
            }
        } catch (err) {
            dependencyReady = false;
            if (typeof reportActionableError === 'function') {
                reportActionableError('Dependency Check', err, {
                    onRetry: checkDependencies,
                });
            }
        }
        refreshStatuses();
    }

    function refreshStatuses() {
        const hasLocation = hasValidLocation();
        const notifications = notificationStatus();
        const hasDefaultMode = Boolean(localStorage.getItem(DEFAULT_MODE_KEY));

        setStatus(locationStatusEl, hasLocation, hasLocation ? 'Configured' : 'Not set');
        setStatus(notifyStatusEl, notifications.ready, notifications.label);
        setStatus(modeStatusEl, hasDefaultMode, hasDefaultMode ? localStorage.getItem(DEFAULT_MODE_KEY) : 'Not set');
        const hasTier = Boolean(localStorage.getItem('intercept-ui-tier'));
        setStatus(uiTierStatusEl, hasTier, hasTier ? localStorage.getItem('intercept-ui-tier') : 'Not set');
        if (hardwareStatusEl && hardwareStepEl && !hardwareStepEl.hidden) {
            const platform = (serverStatus && serverStatus.platform) || {};
            const usb = Boolean(platform.usb_passthrough);
            setStatus(
                hardwareStatusEl,
                usb,
                usb ? 'USB bus mapped' : 'USB bus not visible'
            );
        }

        if (dependencyReady === null) {
            checkDependencies();
            return;
        }
        setStatus(depsStatusEl, dependencyReady, dependencyReady ? 'Ready' : 'Missing tools');

        const doneCount = Number(dependencyReady) + Number(hasLocation) + Number(notifications.ready) + Number(hasDefaultMode) + Number(hasTier);
        const completeBtn = document.getElementById('setupCompleteBtn');
        if (completeBtn) {
            completeBtn.textContent = doneCount >= 3 ? 'Mark Setup Complete' : 'Complete Anyway';
        }
    }

    function setStatus(el, done, label) {
        if (!el) return;
        el.classList.toggle('done', Boolean(done));
        el.textContent = String(label || (done ? 'Done' : 'Pending'));
    }

    function hasValidLocation() {
        const rawLat = localStorage.getItem('observerLat');
        const rawLon = localStorage.getItem('observerLon');

        if (rawLat === null || rawLon === null || rawLat === '' || rawLon === '') {
            return false;
        }

        const lat = Number(rawLat);
        const lon = Number(rawLon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false;

        return lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
    }

    function notificationStatus() {
        if (!('Notification' in window)) {
            return { ready: true, label: 'Unsupported (optional)' };
        }

        if (Notification.permission === 'granted') {
            return { ready: true, label: 'Enabled' };
        }

        if (Notification.permission === 'denied') {
            return { ready: false, label: 'Blocked in browser' };
        }

        return { ready: false, label: 'Permission needed' };
    }

    async function requestNotifications() {
        if (!('Notification' in window)) {
            refreshStatuses();
            return;
        }

        try {
            await Notification.requestPermission();
        } catch (err) {
            if (typeof reportActionableError === 'function') {
                reportActionableError('Notifications', err);
            }
        }
        refreshStatuses();
    }

    async function completeSetup() {
        localStorage.setItem(COMPLETE_KEY, 'true');
        try {
            await fetch('/setup/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: '{}',
            });
        } catch (_err) {
            /* local completion still stands */
        }
        close();

        if (typeof showAppToast === 'function') {
            showAppToast('Setup Complete', 'You can revisit these options in Settings.', 'info');
        }
    }

    function open() {
        if (!overlayEl) return;
        overlayEl.classList.add('open');
    }

    function close() {
        if (!overlayEl) return;
        overlayEl.classList.remove('open');
    }

    return {
        init,
        open,
        close,
        refreshStatuses,
        completeSetup,
    };
})();

document.addEventListener('DOMContentLoaded', () => {
    FirstRunSetup.init();
});
