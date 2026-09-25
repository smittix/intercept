const AppFeedback = (function() {
    'use strict';

    let stackEl = null;
    let nextToastId = 1;
    const TOAST_MAX = 5;

    function init() {
        ensureStack();
        installGlobalHandlers();
    }

    function ensureStack() {
        if (stackEl && document.body.contains(stackEl)) return stackEl;

        stackEl = document.getElementById('appToastStack');
        if (!stackEl) {
            stackEl = document.createElement('div');
            stackEl.id = 'appToastStack';
            stackEl.className = 'app-toast-stack';
            stackEl.setAttribute('aria-live', 'assertive');
            stackEl.setAttribute('role', 'alert');
            document.body.appendChild(stackEl);
        }
        return stackEl;
    }

    // Every toast is also kept, for the toolbar's notification history
    // (partials/nav.html), in session storage so it follows you between pages.
    const HISTORY_KEY = 'intercept.notices';
    const HISTORY_MAX = 50;

    function remember(type, title, message) {
        try {
            const list = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]');
            list.push({ t: Date.now(), type, title, message });
            sessionStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(-HISTORY_MAX)));
        } catch (err) { /* the toast still shows */ }
        window.dispatchEvent(new CustomEvent('intercept:notice'));
    }

    function toast(options) {
        const opts = options || {};
        const type = normalizeType(opts.type);
        const id = nextToastId++;
        const durationMs = Number.isFinite(opts.durationMs) ? opts.durationMs : 6500;
        remember(type, String(opts.title || defaultTitle(type)), String(opts.message || ''));

        const root = document.createElement('div');
        root.className = `app-toast ${type}`;
        root.dataset.toastId = String(id);

        const titleEl = document.createElement('div');
        titleEl.className = 'app-toast-title';
        titleEl.textContent = String(opts.title || defaultTitle(type));
        root.appendChild(titleEl);

        const msgEl = document.createElement('div');
        msgEl.className = 'app-toast-msg';
        msgEl.textContent = String(opts.message || '');
        root.appendChild(msgEl);

        const actions = Array.isArray(opts.actions) ? opts.actions.filter(Boolean).slice(0, 3) : [];
        if (actions.length > 0) {
            const actionsEl = document.createElement('div');
            actionsEl.className = 'app-toast-actions';
            for (const action of actions) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.textContent = String(action.label || 'Action');
                btn.addEventListener('click', () => {
                    try {
                        if (typeof action.onClick === 'function') {
                            action.onClick();
                        }
                    } finally {
                        removeToast(id);
                    }
                });
                actionsEl.appendChild(btn);
            }
            root.appendChild(actionsEl);
        }

        const stack = ensureStack();

        // Enforce toast cap — remove oldest when exceeded
        while (stack.children.length >= TOAST_MAX) {
            stack.removeChild(stack.firstChild);
        }

        stack.appendChild(root);

        if (durationMs > 0) {
            window.setTimeout(() => {
                removeToast(id);
            }, durationMs);
        }

        return id;
    }

    function removeToast(id) {
        if (!stackEl) return;
        const toastEl = stackEl.querySelector(`[data-toast-id="${id}"]`);
        if (!toastEl) return;
        toastEl.remove();
    }

    function reportError(context, error, options) {
        const opts = options || {};
        const message = extractMessage(error);
        const actions = [];

        if (isSettingsError(message)) {
            actions.push({
                label: 'Open Settings',
                onClick: () => {
                    if (typeof showSettings === 'function') {
                        showSettings();
                    }
                }
            });
        }

        if (isNetworkError(message)) {
            actions.push({
                label: 'Retry',
                onClick: () => {
                    if (typeof opts.onRetry === 'function') {
                        opts.onRetry();
                    }
                }
            });
        }

        if (typeof opts.extraAction === 'function' && opts.extraActionLabel) {
            actions.push({
                label: String(opts.extraActionLabel),
                onClick: opts.extraAction,
            });
        }

        return toast({
            type: 'error',
            title: context || 'Action Failed',
            message,
            actions,
            durationMs: opts.persistent ? 0 : 8500,
        });
    }

    function installGlobalHandlers() {
        window.addEventListener('error', (event) => {
            const target = event && event.target;
            if (target && (target.tagName === 'IMG' || target.tagName === 'SCRIPT')) {
                return;
            }

            const message = extractMessage(event && event.error) || String(event.message || 'Unknown error');
            if (shouldIgnore(message)) return;
            toast({
                type: 'warning',
                title: 'Unhandled Error',
                message,
            });
        });

        window.addEventListener('unhandledrejection', (event) => {
            const message = extractMessage(event && event.reason);
            if (shouldIgnore(message)) return;
            toast({
                type: 'warning',
                title: 'Promise Rejection',
                message,
            });
        });
    }

    function normalizeType(type) {
        const t = String(type || 'info').toLowerCase();
        if (t === 'error' || t === 'warning' || t === 'success') return t;
        return 'info';
    }

    function defaultTitle(type) {
        if (type === 'error') return 'Error';
        if (type === 'warning') return 'Warning';
        if (type === 'success') return 'Done';
        return 'Notice';
    }

    function extractMessage(error) {
        if (!error) return 'Unknown error';
        if (typeof error === 'string') return error;
        if (error instanceof Error) return error.message || error.name;
        if (typeof error.message === 'string') return error.message;
        return String(error);
    }

    function shouldIgnore(message) {
        const text = String(message || '').toLowerCase();
        return text.includes('script error') || text.includes('resizeobserver loop limit exceeded');
    }

    function renderCollectionState(container, options) {
        if (!container) return null;
        const opts = options || {};
        const type = String(opts.type || 'empty').toLowerCase();
        const message = String(opts.message || (type === 'loading' ? 'Loading...' : 'No data available'));
        const className = opts.className || `app-collection-state is-${type}`;

        container.innerHTML = '';

        if (container.tagName === 'TBODY') {
            const row = document.createElement('tr');
            row.className = 'app-collection-state-row';
            const cell = document.createElement('td');
            const columns = Number.isFinite(opts.columns) ? opts.columns : 1;
            cell.colSpan = Math.max(1, columns);
            const state = document.createElement('div');
            state.className = className;
            state.textContent = message;
            cell.appendChild(state);
            row.appendChild(cell);
            container.appendChild(row);
            return row;
        }

        const state = document.createElement('div');
        state.className = className;
        state.textContent = message;
        container.appendChild(state);
        return state;
    }

    function isOffline() {
        return typeof navigator !== 'undefined' && navigator.onLine === false;
    }

    function isTransientNetworkError(error) {
        const text = String(extractMessage(error) || '').toLowerCase();
        if (!text) return false;

        return text.includes('networkerror') ||
            text.includes('failed to fetch') ||
            text.includes('network request failed') ||
            text.includes('load failed') ||
            text.includes('err_network_io_suspended') ||
            text.includes('network io suspended') ||
            text.includes('the network connection was lost') ||
            text.includes('connection reset') ||
            text.includes('timeout');
    }

    function isTransientOrOffline(error) {
        return isOffline() || isTransientNetworkError(error);
    }

    function isNetworkError(message) {
        return isTransientNetworkError(message);
    }

    function isSettingsError(message) {
        const text = String(message || '').toLowerCase();
        return text.includes('permission') || text.includes('denied') || text.includes('dependency') || text.includes('tool');
    }

    // --- Button loading state ---

    function withLoadingButton(btn, asyncFn) {
        if (!btn || btn.disabled) return Promise.resolve();

        const originalText = btn.textContent;
        btn.disabled = true;
        btn.classList.add('btn-loading');

        return Promise.resolve()
            .then(function() { return asyncFn(); })
            .then(function(result) {
                btn.disabled = false;
                btn.classList.remove('btn-loading');
                btn.textContent = originalText;
                return result;
            })
            .catch(function(err) {
                btn.disabled = false;
                btn.classList.remove('btn-loading');
                btn.textContent = originalText;
                throw err;
            });
    }

    // --- Confirmation modal ---

    function confirmAction(options) {
        var opts = options || {};
        var title = opts.title || 'Confirm Action';
        var message = opts.message || 'Are you sure?';
        var confirmLabel = opts.confirmLabel || 'Confirm';
        var confirmClass = opts.confirmClass || 'btn-danger';

        return new Promise(function(resolve) {
            // Create backdrop
            var backdrop = document.createElement('div');
            backdrop.className = 'confirm-modal-backdrop';

            var modal = document.createElement('div');
            modal.className = 'confirm-modal';
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');
            modal.setAttribute('aria-labelledby', 'confirm-modal-title');

            var titleEl = document.createElement('div');
            titleEl.className = 'confirm-modal-title';
            titleEl.id = 'confirm-modal-title';
            titleEl.textContent = title;
            modal.appendChild(titleEl);

            var msgEl = document.createElement('div');
            msgEl.className = 'confirm-modal-message';
            msgEl.textContent = message;
            modal.appendChild(msgEl);

            var actions = document.createElement('div');
            actions.className = 'confirm-modal-actions';

            var cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className = 'btn btn-ghost';
            cancelBtn.textContent = 'Cancel';

            var confirmBtn = document.createElement('button');
            confirmBtn.type = 'button';
            confirmBtn.className = 'btn ' + confirmClass;
            confirmBtn.textContent = confirmLabel;

            actions.appendChild(cancelBtn);
            actions.appendChild(confirmBtn);
            modal.appendChild(actions);
            backdrop.appendChild(modal);
            document.body.appendChild(backdrop);

            // Focus confirm button
            confirmBtn.focus();

            function cleanup(result) {
                backdrop.remove();
                document.removeEventListener('keydown', onKey);
                resolve(result);
            }

            function onKey(e) {
                if (e.key === 'Escape') cleanup(false);
                if (e.key === 'Enter') cleanup(true);
            }

            cancelBtn.addEventListener('click', function() { cleanup(false); });
            confirmBtn.addEventListener('click', function() { cleanup(true); });
            backdrop.addEventListener('click', function(e) {
                if (e.target === backdrop) cleanup(false);
            });
            document.addEventListener('keydown', onKey);
        });
    }

    // --- Keyboard navigation for lists ---

    function enableListKeyNav(container, itemSelector) {
        if (!container) return;

        container.setAttribute('role', 'listbox');
        container.setAttribute('tabindex', '0');

        container.addEventListener('keydown', function(e) {
            var items = container.querySelectorAll(itemSelector);
            if (!items.length) return;

            var current = container.querySelector(itemSelector + '[aria-selected="true"]');
            var idx = current ? Array.prototype.indexOf.call(items, current) : -1;

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                var next = Math.min(idx + 1, items.length - 1);
                selectItem(items, next);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                var prev = Math.max(idx - 1, 0);
                selectItem(items, prev);
            } else if (e.key === 'Enter' && current) {
                e.preventDefault();
                current.click();
            } else if (e.key === 'Escape' && current) {
                e.preventDefault();
                current.setAttribute('aria-selected', 'false');
                current.classList.remove('keyboard-focused');
            }
        });

        function selectItem(items, index) {
            items.forEach(function(item) {
                item.setAttribute('aria-selected', 'false');
                item.classList.remove('keyboard-focused');
            });
            var target = items[index];
            if (target) {
                target.setAttribute('aria-selected', 'true');
                target.classList.add('keyboard-focused');
                target.scrollIntoView({ block: 'nearest' });
            }
        }
    }

    return {
        init,
        toast,
        reportError,
        removeToast,
        renderCollectionState,
        isOffline,
        isTransientNetworkError,
        isTransientOrOffline,
        withLoadingButton,
        confirmAction,
        enableListKeyNav,
    };
})();

window.showAppToast = function(title, message, type) {
    return AppFeedback.toast({
        title,
        message,
        type,
    });
};

window.reportActionableError = function(context, error, options) {
    return AppFeedback.reportError(context, error, options);
};

window.renderCollectionState = function(container, options) {
    return AppFeedback.renderCollectionState(container, options);
};

window.isOffline = function() {
    return AppFeedback.isOffline();
};

window.isTransientNetworkError = function(error) {
    return AppFeedback.isTransientNetworkError(error);
};

window.isTransientOrOffline = function(error) {
    return AppFeedback.isTransientOrOffline(error);
};

// A top-level const is not a property of window; code that checks
// window.AppFeedback (the nav, showInfo) needs it to be.
window.AppFeedback = AppFeedback;

// A page can use the toasts without the global error handlers by loading
// this script with data-global-handlers="off" (the dashboards do).
const appFeedbackScript = document.currentScript;
document.addEventListener('DOMContentLoaded', () => {
    if (appFeedbackScript && appFeedbackScript.dataset.globalHandlers === 'off') return;
    AppFeedback.init();
});
