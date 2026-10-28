/**
 * tourController.js
 *
 * Loads tours from the server for the current page, auto-starts any pending
 * auto-start tour, and exposes window.horillaTour.toggle() so the "?" navbar
 * button can open/close the launcher panel. Users can opt out of auto-started
 * tours from the popover ("Don't show tours again") and opt back in from the
 * launcher panel; tours stay available there either way.
 *
 * Depends on:
 *   - driver.js  (already on window.driver.js.driver)
 *   - window.HORILLA_TOUR  set by footer_scripts.html
 */
(function () {
  'use strict';

  var CFG = window.HORILLA_TOUR || {};
  var LABELS = CFG.labels || {};
  if (!CFG.activeUrl) return;

  var driverFactory = window.driver && window.driver.js && window.driver.js.driver;
  if (!driverFactory) return;

  var _tours = [];
  var _driverInst = null;
  var _panel = null;
  var _fetched = false;
  var _autoStartDisabled = false;

  // ── CSRF ──────────────────────────────────────────────────────────────────

  function csrfToken() {
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    if (el) return el.value;
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  // ── Progress reporting ────────────────────────────────────────────────────

  function postProgress(tourId, status, lastStep) {
    var fd = new FormData();
    fd.append('tour_id', tourId);
    fd.append('status', status);
    if (lastStep !== undefined) fd.append('last_step', lastStep);
    fd.append('csrfmiddlewaretoken', csrfToken());
    // keepalive: the close is often followed at once by a navigation, which
    // would otherwise cancel the request and leave the tour unrecorded.
    fetch(CFG.progressUrl, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
      keepalive: true,
    }).catch(function () {});
  }

  // ── Auto-start preference ─────────────────────────────────────────────────

  function postPreference(disabled) {
    _autoStartDisabled = disabled;
    if (!CFG.preferenceUrl) return;
    var fd = new FormData();
    fd.append('auto_start_disabled', disabled ? '1' : '0');
    fd.append('csrfmiddlewaretoken', csrfToken());
    fetch(CFG.preferenceUrl, { method: 'POST', body: fd }).catch(function () {});
  }

  function setPendingDot(visible) {
    var dot = document.querySelector('#tourLauncherBtn span.absolute');
    if (dot) dot.style.display = visible ? '' : 'none';
  }

  // Popover link: skip this tour and stop auto-starting any tour.
  function addOptOutLink(popover, tour) {
    if (!popover || !popover.wrapper) return;
    if (popover.wrapper.querySelector('.horilla-tour-optout')) return;
    var row = document.createElement('div');
    row.className = 'horilla-tour-optout';
    row.style.cssText = 'margin-top:10px;padding-top:8px;border-top:1px solid #f3f4f6;text-align:left;';
    var link = document.createElement('button');
    link.type = 'button';
    link.style.cssText = 'background:none;border:none;padding:0;cursor:pointer;font-size:12px;color:#6b7280;text-decoration:underline;';
    link.textContent = LABELS.dontShowAgain || "Don't show tours again";
    link.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      postPreference(true);
      postProgress(tour.id, 'skipped', _driverInst ? _driverInst.getActiveIndex() : 0);
      setPendingDot(false);
      if (_driverInst) {
        _driverInst.destroy();
        _driverInst = null;
      }
    });
    row.appendChild(link);
    popover.wrapper.appendChild(row);
    // The popover was positioned before this row existed; re-place it.
    if (_driverInst) _driverInst.refresh();
  }

  // ── Run a tour ────────────────────────────────────────────────────────────

  function runTour(tour) {
    if (_driverInst) {
      _driverInst.destroy();
      _driverInst = null;
    }
    closePanel();

    postProgress(tour.id, 'in_progress', 0);

    var steps = (tour.steps || []).map(function (s) {
      return {
        element: s.element || undefined,
        popover: {
          title: s.title,
          description: s.description,
          side: s.side || 'bottom',
          align: s.align || 'start',
        },
      };
    });

    if (!steps.length) return;

    _driverInst = driverFactory({
      animate: true,
      allowClose: tour.allow_close !== false,
      showProgress: tour.show_progress !== false,
      steps: steps,
      onPopoverRendered: function (popover) { addOptOutLink(popover, tour); },
      onDestroyStarted: function (el, step, opts) {
        var idx = opts.state.activeIndex;
        var isLast = typeof idx === 'number' && idx >= steps.length - 1;
        var status = isLast ? 'completed' : 'skipped';
        postProgress(tour.id, status, idx);
        if (_driverInst) {
          _driverInst.destroy();
          _driverInst = null;
        }
        if (isLast) {
          // Remove pending dot for this tour
          var dot = document.querySelector('#tourLauncherBtn span.absolute');
          if (dot) dot.remove();
        }
      },
    });

    _driverInst.drive(0);
  }

  // ── Fetch tours from API ──────────────────────────────────────────────────

  function fetchTours() {
    // Always use the live pathname so HTMX navigation is accounted for.
    // Send page='' so the server resolves the URL name from the path itself
    // (tour_active view already has that fallback via django.urls.resolve).
    var params = new URLSearchParams({
      page: '',
      path: window.location.pathname,
    });
    return fetch(CFG.activeUrl + '?' + params.toString(), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
      .then(function (r) { return r.ok ? r.json() : { tours: [] }; })
      .then(function (data) {
        _tours = data.tours || [];
        _autoStartDisabled = !!data.auto_start_disabled;
        _fetched = true;
        return _tours;
      })
      .catch(function () {
        _tours = [];
        _fetched = true;
        return [];
      });
  }

  // ── Launcher panel ────────────────────────────────────────────────────────

  function buildPanel() {
    if (_panel) return _panel;

    var btn = document.getElementById('tourLauncherBtn');
    if (!btn) return null;
    var wrapper = btn.closest('.dropdown-wrapper');
    if (!wrapper) return null;

    _panel = document.createElement('div');
    _panel.id = 'tourLauncherPanel';
    // Looks (light and dark) live in horilla_theme's ux.css (.hx-tour-panel).
    _panel.className = 'hx-tour-panel';
    _panel.style.display = 'none';

    wrapper.style.position = 'relative';
    wrapper.appendChild(_panel);

    document.addEventListener('click', function (e) {
      if (!_panel) return;
      var btn2 = document.getElementById('tourLauncherBtn');
      if (btn2 && btn2.contains(e.target)) return;
      // Keep the panel open while ticking its preference checkbox.
      if (_panel.contains(e.target)) return;
      closePanel();
    });

    return _panel;
  }

  // Panel footer checkbox: the way back in after "Don't show tours again".
  function renderPreferenceToggle(panel) {
    var label = document.createElement('label');
    label.style.cssText = 'display:flex;align-items:center;gap:8px;margin:0;padding:9px 12px;border-top:1px solid #f3f4f6;font-size:12px;color:#4b5563;cursor:pointer;';
    var box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !_autoStartDisabled;
    box.style.cssText = 'cursor:pointer;margin:0;';
    box.addEventListener('change', function () {
      postPreference(!box.checked);
      if (!box.checked) setPendingDot(false);
    });
    var text = document.createElement('span');
    text.textContent = LABELS.showAutomatically || 'Show tours automatically';
    label.appendChild(box);
    label.appendChild(text);
    panel.appendChild(label);
  }

  function renderPanel(tours) {
    var panel = buildPanel();
    if (!panel) return;
    panel.innerHTML = '';

    // Header
    var hdr = document.createElement('div');
    hdr.className = 'hx-tour-panel__header';
    hdr.textContent = 'Product Tours';
    panel.appendChild(hdr);

    if (!tours.length) {
      var empty = document.createElement('div');
      empty.className = 'hx-tour-panel__empty';
      empty.textContent = 'No tours available for this page.';
      panel.appendChild(empty);
      renderPreferenceToggle(panel);
      return;
    }

    var list = document.createElement('ul');
    list.className = 'hx-tour-panel__list';

    tours.forEach(function (tour) {
      var li = document.createElement('li');
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'hx-tour-panel__item';

      var iconEl = document.createElement('ion-icon');
      iconEl.setAttribute('name', tour.icon || 'map-outline');
      iconEl.className = 'hx-tour-panel__icon';

      var labelEl = document.createElement('span');
      labelEl.className = 'hx-tour-panel__label';
      labelEl.textContent = tour.title;

      btn.appendChild(iconEl);
      btn.appendChild(labelEl);

      // Pending dot
      if (tour.auto_start && tour.status !== 'completed' && tour.status !== 'skipped') {
        var dot = document.createElement('span');
        dot.className = 'hx-tour-panel__dot';
        btn.appendChild(dot);
      }

      btn.addEventListener('click', function () { runTour(tour); });
      li.appendChild(btn);
      list.appendChild(li);
    });

    panel.appendChild(list);
    renderPreferenceToggle(panel);
  }

  function openPanel() {
    var panel = buildPanel();
    if (!panel) return;
    panel.style.display = 'block';
    // Always re-fetch: path may have changed via HTMX since last open
    panel.innerHTML = '<div class="hx-tour-panel__empty">Loading…</div>';
    fetchTours().then(function (tours) { renderPanel(tours); });
  }

  function closePanel() {
    if (_panel) _panel.style.display = 'none';
  }

  // ── Public API ────────────────────────────────────────────────────────────

  window.horillaTour = {
    toggle: function () {
      var panel = _panel;
      if (!panel || panel.style.display === 'none') {
        openPanel();
      } else {
        closePanel();
      }
    },
    start: function (tourIdOrSlug) {
      var t = _tours.find(function (x) { return x.id === tourIdOrSlug || x.slug === tourIdOrSlug; });
      if (t) runTour(t);
    },
  };

  // ── Boot ──────────────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function () {
    fetchTours().then(function (tours) {
      // Auto-start: first tour with auto_start flag set by the server
      var auto = tours.find(function (t) { return t.auto_start; });
      if (auto) {
        setTimeout(function () { runTour(auto); }, 500);
      }
    });
  });
})();
