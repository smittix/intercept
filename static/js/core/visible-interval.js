/**
 * VisibleInterval: setInterval for work that exists only to be looked at.
 *
 * A clock, a countdown, a status refresh. These are suspended while the tab
 * is hidden and run once when it returns, so a dashboard left on a second
 * monitor or in a background tab stops polling and redrawing.
 *
 * Never use this for anything that would lose data while suspended: SSE
 * handling, recording state, or polls that feed a list or a track.
 *
 *     const id = VisibleInterval.set(updateClock, 1000);
 *     VisibleInterval.clear(id);
 *
 * Ids are VisibleInterval's own; clear them with VisibleInterval.clear, not
 * clearInterval.
 */
(function (global) {
    'use strict';
    if (global.VisibleInterval) return;  // loaded by more than one template

    const timers = new Map();
    let nextId = 1;

    function arm(timer) {
        if (!timer.handle) timer.handle = setInterval(timer.fn, timer.ms);
    }

    function disarm(timer) {
        if (timer.handle) {
            clearInterval(timer.handle);
            timer.handle = null;
        }
    }

    function set(fn, ms) {
        const id = nextId++;
        const timer = { fn, ms, handle: null };
        timers.set(id, timer);
        if (!document.hidden) arm(timer);
        return id;
    }

    function clear(id) {
        const timer = timers.get(id);
        if (!timer) return;
        disarm(timer);
        timers.delete(id);
    }

    document.addEventListener('visibilitychange', () => {
        timers.forEach((timer) => {
            if (document.hidden) {
                disarm(timer);
                return;
            }
            // Catch up at once rather than showing a stale value for a tick.
            try { timer.fn(); } catch (err) { console.error('[VisibleInterval]', err); }
            arm(timer);
        });
    });

    global.VisibleInterval = { set, clear, _count: () => timers.size };
})(window);
