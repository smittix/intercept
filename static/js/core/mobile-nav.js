/* Phone sidebar drawer.
 *
 * The markup (#hamburgerBtn, #mainSidebar.mobile-drawer, #drawerOverlay) and
 * the base styles in responsive.css already exist. static/js/core/app.js
 * wires them in initMobileNav(), but no template loads that file, so the
 * button does nothing on a phone. This script is the piece the main page
 * actually includes. It also publishes the real header and mobile-nav
 * heights: on a narrow screen the Run State strip wraps and the header is
 * taller than the 48px token, so a drawer pinned to top:0 sits underneath it.
 */
(function () {
    if (window.__interceptMobileNavInit) return;
    window.__interceptMobileNavInit = true;

    function syncDrawerInsets() {
        var header = document.querySelector('body > header');
        var nav = document.getElementById('mobileNavBar');
        var headerH = header ? Math.round(header.getBoundingClientRect().height) : 0;
        var navH = 0;
        if (nav) {
            var cs = window.getComputedStyle(nav);
            if (cs.display !== 'none' && cs.visibility !== 'hidden') {
                navH = Math.round(nav.getBoundingClientRect().height);
            }
        }
        var top = Math.max(0, headerH + navH);
        document.documentElement.style.setProperty('--intercept-header-height', headerH + 'px');
        document.documentElement.style.setProperty('--intercept-mobile-nav-height', navH + 'px');
        document.documentElement.style.setProperty('--intercept-drawer-top', top + 'px');
    }

    function initMobileNav() {
        var hamburgerBtn = document.getElementById('hamburgerBtn');
        var sidebar = document.getElementById('mainSidebar');
        var overlay = document.getElementById('drawerOverlay');
        if (!hamburgerBtn || !sidebar || !overlay) return;
        if (hamburgerBtn.dataset.mobileNavBound === '1') return;
        hamburgerBtn.dataset.mobileNavBound = '1';

        function openDrawer() {
            syncDrawerInsets();
            sidebar.classList.add('open');
            overlay.classList.add('visible');
            hamburgerBtn.classList.add('active');
            hamburgerBtn.classList.add('open');
            document.body.classList.add('mobile-drawer-open');
        }

        function closeDrawer() {
            sidebar.classList.remove('open');
            overlay.classList.remove('visible');
            hamburgerBtn.classList.remove('active');
            hamburgerBtn.classList.remove('open');
            document.body.classList.remove('mobile-drawer-open');
        }

        function toggleDrawer() {
            if (sidebar.classList.contains('open')) closeDrawer();
            else openDrawer();
        }

        hamburgerBtn.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            toggleDrawer();
        });

        // Only close when the dimmed backdrop itself is tapped. A click whose
        // target is the sidebar used to hit the overlay, because the overlay
        // painted on top of the drawer, and immediately closed the menu.
        overlay.addEventListener('click', function (event) {
            if (sidebar.contains(event.target)) return;
            if (hamburgerBtn.contains(event.target)) return;
            if (event.target !== overlay) return;
            closeDrawer();
        });

        sidebar.addEventListener('click', function (event) {
            event.stopPropagation();
        });
        sidebar.addEventListener('touchstart', function (event) {
            event.stopPropagation();
        }, { passive: true });

        window.addEventListener('resize', function () {
            syncDrawerInsets();
            if (window.innerWidth >= 1024) closeDrawer();
        });
        window.addEventListener('orientationchange', function () {
            window.setTimeout(syncDrawerInsets, 50);
        });

        if (typeof ResizeObserver === 'function') {
            var ro = new ResizeObserver(syncDrawerInsets);
            var header = document.querySelector('body > header');
            var nav = document.getElementById('mobileNavBar');
            if (header) ro.observe(header);
            if (nav) ro.observe(nav);
        }

        syncDrawerInsets();

        window.toggleMobileDrawer = toggleDrawer;
        window.closeMobileDrawer = closeDrawer;
        window.openMobileDrawer = openDrawer;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initMobileNav);
    } else {
        initMobileNav();
    }
})();
