/* Phone page scrolling.
 *
 * Several pages are app shells: body or main is fixed at 100vh with
 * overflow:hidden, and a flex child with overflow other than visible is
 * allowed to shrink to nothing. On a phone the panels stack past the
 * screen and the bottom cannot be reached. Leaflet also calls
 * preventDefault() on every touchmove that starts on a map, so a full-width
 * map swallows the swipe.
 *
 * Under 1024px this marks clipping boxes so mobile.css can scroll them, or
 * grow them when they hold a map. At 1024px and above it removes those
 * marks and does nothing else. One-finger vertical swipes that start on a
 * map scroll the page; sideways drags and two-finger pan/pinch still go to
 * the map.
 */
(function () {
    'use strict';
    if (window.__interceptMobileScroll) return;
    window.__interceptMobileScroll = true;

    var CLS = 'mobile-scroll-unclip';
    var UNSQ = 'mobile-scroll-unsquash';
    var GROW = 'mobile-scroll-grow';
    var mq = window.matchMedia('(max-width: 1023px)');
    var SKIP = '.leaflet-container, .leaflet-pane, svg, canvas, video, iframe, img, ' +
               'select, textarea, .modal, [role="dialog"], .mobile-drawer, #mainSidebar, ' +
               '.toast, .toast-container, [aria-hidden="true"]';
    var CONTENT = 'button, input, label, a, h1, h2, h3, h4, h5, h6, p, li, td, th, span, strong, small';

    function clipsContent(el, box) {
        var limit = box.bottom - 2;
        var kids = el.querySelectorAll(CONTENT + ', div, p');
        var n = Math.min(kids.length, 400);
        for (var i = kids.length - 1, seen = 0; i >= 0 && seen < n; i--, seen++) {
            var k = kids[i];
            if (k.closest(SKIP) && !el.matches(SKIP)) continue;
            var r = k.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) continue;
            if (r.top >= limit || r.bottom > limit + 4) {
                var cs = getComputedStyle(k);
                if (cs.position === 'absolute' || cs.position === 'fixed') continue;
                if (cs.visibility === 'hidden' || cs.display === 'none') continue;
                var t = '';
                for (var c = k.firstChild; c; c = c.nextSibling) {
                    if (c.nodeType === 3) t += c.nodeValue;
                }
                t = t.trim();
                if (!t && !/^(BUTTON|INPUT|A|LABEL)$/.test(k.tagName)) continue;
                return true;
            }
        }
        return false;
    }

    function squashedFlexChild(el, cs) {
        var p = el.parentElement;
        if (!p) return false;
        var ps = getComputedStyle(p);
        return ps.display.indexOf('flex') !== -1 && ps.flexDirection.indexOf('column') === 0 &&
               parseFloat(cs.flexGrow) > 0 && cs.maxHeight === 'none' &&
               el.style.height === '' && el.getBoundingClientRect().width >= 120;
    }

    function unsquash(el) {
        var p = el.parentElement;
        if (!p || p === document.body || p === document.documentElement) return;
        var pd = getComputedStyle(p).display;
        if (!/flex|grid/.test(pd)) return;
        var extra = el.scrollHeight - el.clientHeight;
        if (extra < 8) return;
        var ph = p.getBoundingClientRect().height;
        el.classList.add(UNSQ);
        if (!p.hasAttribute('data-mobile-minh')) {
            p.setAttribute('data-mobile-minh', p.style.minHeight || '');
            p.setAttribute('data-mobile-shrink', p.style.flexShrink || '');
        }
        p.style.minHeight = Math.ceil(ph + extra) + 'px';
        p.style.flexShrink = '0';
    }

    function undoAll() {
        var on = document.querySelectorAll('.' + CLS + ', .' + UNSQ + ', .' + GROW);
        for (var j = 0; j < on.length; j++) on[j].classList.remove(CLS, UNSQ, GROW);
        var ps = document.querySelectorAll('[data-mobile-minh]');
        for (var k = 0; k < ps.length; k++) {
            ps[k].style.minHeight = ps[k].getAttribute('data-mobile-minh');
            ps[k].style.flexShrink = ps[k].getAttribute('data-mobile-shrink');
            ps[k].removeAttribute('data-mobile-minh');
            ps[k].removeAttribute('data-mobile-shrink');
        }
    }

    var lastW = window.innerWidth, lastH = window.innerHeight;
    function scan() {
        if (window.innerWidth !== lastW || Math.abs(window.innerHeight - lastH) > 120) {
            undoAll();
            lastW = window.innerWidth;
            lastH = window.innerHeight;
        }
        scheduled = false;
        if (!mq.matches) { undoAll(); return; }
        var sc = document.querySelectorAll('.' + CLS);
        for (var q = 0; q < sc.length; q++) {
            if (sc[q].querySelector('.leaflet-container')) {
                sc[q].classList.remove(CLS);
                sc[q].classList.add(GROW);
            }
        }
        var vh = window.innerHeight || 800;
        var all = document.body ? document.body.getElementsByTagName('*') : [];
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (el.classList.contains(CLS) || el.classList.contains(UNSQ) || el.classList.contains(GROW)) continue;
            if (el.scrollHeight - el.clientHeight < 8) continue;
            var cs = getComputedStyle(el);
            if (cs.overflowY !== 'hidden' && cs.overflowY !== 'clip') continue;
            if (cs.display === 'none' || cs.visibility === 'hidden') continue;
            if (cs.position === 'fixed') continue;
            if (cs.webkitLineClamp && cs.webkitLineClamp !== 'none') continue;
            if (cs.textOverflow === 'ellipsis') continue;
            if (el.clientHeight < 8 && !squashedFlexChild(el, cs)) continue;
            if (el.matches(SKIP) || el.closest('.leaflet-container')) continue;
            var box = el.getBoundingClientRect();
            if (box.width < 120) continue;
            if (!clipsContent(el, box)) continue;
            if (el.clientHeight >= Math.min(120, vh * 0.15)) {
                if (el.querySelector('.leaflet-container')) el.classList.add(GROW);
                else el.classList.add(CLS);
            } else {
                unsquash(el);
            }
        }
    }

    var scheduled = false, lastScan = 0;
    function schedule() {
        if (scheduled) return;
        scheduled = true;
        var wait = Math.max(250, 1500 - (Date.now() - lastScan));
        setTimeout(function () { lastScan = Date.now(); scan(); }, wait);
    }

    function start() {
        lastScan = Date.now();
        scan();
        setTimeout(schedule, 1500);
        setTimeout(schedule, 4000);
        window.addEventListener('resize', schedule, { passive: true });
        window.addEventListener('orientationchange', schedule, { passive: true });
        if (mq.addEventListener) mq.addEventListener('change', schedule);
        else if (mq.addListener) mq.addListener(schedule);
        if (window.MutationObserver) {
            var mo = new MutationObserver(schedule);
            mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'] });
        }
    }

    var coarse = window.matchMedia('(pointer: coarse)');
    var gesture = null;
    window.addEventListener('touchstart', function (e) {
        gesture = null;
        if (!mq.matches || !coarse.matches || e.touches.length !== 1) return;
        var t = e.target;
        if (!t || !t.closest || !t.closest('.leaflet-container')) return;
        if (t.closest('.leaflet-control, .leaflet-popup, .leaflet-marker-icon')) return;
        gesture = { x: e.touches[0].clientX, y: e.touches[0].clientY, mode: 'pending' };
    }, { capture: true, passive: true });
    window.addEventListener('touchmove', function (e) {
        if (!gesture) return;
        if (e.touches.length !== 1) { gesture = null; return; }
        if (gesture.mode === 'pending') {
            var dx = Math.abs(e.touches[0].clientX - gesture.x);
            var dy = Math.abs(e.touches[0].clientY - gesture.y);
            gesture.mode = dy >= dx ? 'page' : 'map';
        }
        if (gesture.mode === 'page') e.stopPropagation();
    }, { capture: true, passive: true });
    window.addEventListener('touchend', function () { gesture = null; }, { capture: true, passive: true });
    window.addEventListener('touchcancel', function () { gesture = null; }, { capture: true, passive: true });

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
