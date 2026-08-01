/**
 * Idle Throttle - reduces how often a mode re-renders its live data while
 * the browser tab/window is hidden, without touching the underlying
 * EventSource/poll timer or dropping any received data.
 *
 * Usage (inside a mode module's SSE/poll message handler):
 *   const throttle = IdleThrottle.register(function (batch) {
 *       batch.forEach(function (payload) { appendRow(payload); });
 *   });
 *   eventSource.onmessage = (e) => throttle.call(JSON.parse(e.data));
 *   // in the mode's destroy() hook:
 *   throttle.dispose();
 */
const IdleThrottle = {
    /**
     * @param {function(Array):void} applyFn - renders a batch of queued payloads
     * @param {{minIntervalMs?: number, maxQueueSize?: number}} [options]
     * @returns {{call: function(*):void, dispose: function():void}}
     */
    register(applyFn, options) {
        const minIntervalMs = (options && options.minIntervalMs) || 2000;
        const maxQueueSize = (options && options.maxQueueSize) || 500;
        let queue = [];
        let timerId = null;

        const flush = () => {
            timerId = null;
            if (queue.length) {
                const batch = queue;
                queue = [];
                applyFn(batch);
            }
        };

        const onVisibilityChange = () => {
            if (!document.hidden && timerId !== null) {
                clearTimeout(timerId);
                flush();
            }
        };
        document.addEventListener('visibilitychange', onVisibilityChange);

        return {
            call(payload) {
                if (!document.hidden) {
                    applyFn([payload]);
                    return;
                }
                queue.push(payload);
                if (queue.length > maxQueueSize) {
                    queue = queue.slice(queue.length - maxQueueSize);
                }
                if (timerId === null) {
                    timerId = setTimeout(flush, minIntervalMs);
                }
            },
            dispose() {
                document.removeEventListener('visibilitychange', onVisibilityChange);
                if (timerId !== null) {
                    clearTimeout(timerId);
                    timerId = null;
                }
                queue = [];
            }
        };
    }
};
