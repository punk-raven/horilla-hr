/*
 * horilla_theme micro-interactions, built on Motion (motion.dev).
 *
 * Source of static/horilla_theme/assets/js/ux-motion.js (bundled, minified
 * and committed). Rebuild with `npm run build:js` in horilla_theme/frontend -
 * see README.md there.
 *
 * Uses Motion's vanilla "mini" animate (Web Animations API, ~3 KB), so it
 * needs no React and no framework. Everything is additive: it only watches
 * the existing markup (.oh-modal, .oh-dropdown__menu, htmx events) and never
 * changes classes, ids or layout. Animations run on opacity and transform
 * only and finish on transform: none, so nothing that
 * positions itself inside a dialog (select2, date pickers) is affected.
 *
 * prefers-reduced-motion: reduce turns every movement off; the loading bar
 * then just appears and disappears.
 */
import { animate } from "motion/mini";

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const prefersReduced = () => reducedMotion.matches;

// Shared with ux.css (--hx-duration-* / --hx-ease-out).
const EASE_OUT = [0.22, 1, 0.36, 1];
const ENTER = 0.22;
const EXIT = 0.14;

function safeAnimate(el, keyframes, options) {
  try {
    return animate(el, keyframes, options);
  } catch (_) {
    return null;
  }
}

// ── Modal dialogs ───────────────────────────────────────────────────────────
// A dialog opens when its .oh-modal gains .oh-modal--show (index.js, Alpine
// and inline handlers all do it that way). Its content usually arrives a
// moment later through an htmx swap into the (then empty) dialog, so the
// entrance also plays when content lands in an empty, open dialog - once per
// opening.

const lastDialogEntrance = new WeakMap();

function playDialogEntrance(dialog) {
  if (!dialog || prefersReduced()) return;
  const now = performance.now();
  const last = lastDialogEntrance.get(dialog) || 0;
  if (now - last < 350) return;
  lastDialogEntrance.set(dialog, now);
  safeAnimate(
    dialog,
    {
      opacity: [0, 1],
      transform: ["translateY(10px) scale(0.98)", "none"],
    },
    { duration: ENTER, ease: EASE_OUT },
  );
}

function dialogOf(modal) {
  for (const child of modal.children) {
    if (child.classList.contains("oh-modal__dialog")) return child;
  }
  return null;
}

function dialogHasContent(dialog) {
  return !!dialog && dialog.childElementCount > 0;
}

// ── Dropdown menus ──────────────────────────────────────────────────────────
// .oh-dropdown__menu is shown by Alpine (x-show, an inline display style) or
// by toggling a class; fade and drop it in a few pixels when it appears.

const dropdownVisible = new WeakMap();

function isShown(el) {
  return el.isConnected && getComputedStyle(el).display !== "none";
}

function onDropdownMutation(menu) {
  const visible = isShown(menu);
  const was = dropdownVisible.get(menu) || false;
  dropdownVisible.set(menu, visible);
  if (visible && !was && !prefersReduced()) {
    safeAnimate(
      menu,
      { opacity: [0, 1], transform: ["translateY(-4px)", "none"] },
      { duration: 0.16, ease: EASE_OUT },
    );
  }
}

const observer = new MutationObserver((mutations) => {
  for (const m of mutations) {
    const el = m.target;
    if (!(el instanceof HTMLElement)) continue;
    const cls = el.classList;
    if (cls.contains("oh-modal") && m.attributeName === "class") {
      const shown = cls.contains("oh-modal--show");
      const wasShown = (m.oldValue || "").split(/\s+/).includes("oh-modal--show");
      if (shown && !wasShown) {
        const dialog = dialogOf(el);
        if (dialogHasContent(dialog)) playDialogEntrance(dialog);
      }
    } else if (cls.contains("oh-dropdown__menu")) {
      onDropdownMutation(el);
    }
  }
});

// ── htmx request feedback ───────────────────────────────────────────────────
// A thin top progress bar while any request is in flight, shown only when a
// request takes longer than SHOW_AFTER so instant swaps don't flash it, plus
// aria-busy on the element being updated for assistive technology.

const SHOW_AFTER = 150;
const STUCK_AFTER = 20000;
let inFlight = 0;
let showTimer = null;
let stuckTimer = null;
let bar = null;
let barShown = false;

function ensureBar() {
  if (bar && bar.isConnected) return bar;
  bar = document.createElement("div");
  bar.id = "hx-progress";
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-hidden", "true");
  document.body.appendChild(bar);
  return bar;
}

function showBar() {
  const el = ensureBar();
  barShown = true;
  el.style.opacity = "1";
  if (prefersReduced()) {
    el.style.transform = "scaleX(0.6)";
    return;
  }
  // Ease towards 85% and wait there for the response (an indeterminate
  // trickle: htmx can't report real progress for most requests).
  el.style.transform = "scaleX(0.85)";
  safeAnimate(
    el,
    { transform: ["scaleX(0.05)", "scaleX(0.6)", "scaleX(0.85)"] },
    { duration: 4, ease: "easeOut" },
  );
}

function hideBar() {
  clearTimeout(showTimer);
  showTimer = null;
  if (!barShown || !bar) return;
  barShown = false;
  const el = bar;
  if (prefersReduced()) {
    el.style.opacity = "0";
    el.style.transform = "scaleX(0)";
    return;
  }
  const finish = safeAnimate(el, { transform: "scaleX(1)" }, { duration: EXIT, ease: EASE_OUT });
  const done = () => {
    if (barShown) return; // a new request started meanwhile
    const fade = safeAnimate(el, { opacity: 0 }, { duration: EXIT, ease: "easeIn" });
    const reset = () => {
      if (barShown) return;
      el.style.opacity = "0";
      el.style.transform = "scaleX(0)";
    };
    if (fade && fade.finished) fade.finished.then(reset, reset);
    else reset();
  };
  if (finish && finish.finished) finish.finished.then(done, done);
  else done();
}

function requestStarted() {
  inFlight += 1;
  if (inFlight === 1) {
    clearTimeout(showTimer);
    showTimer = setTimeout(showBar, SHOW_AFTER);
  }
  clearTimeout(stuckTimer);
  stuckTimer = setTimeout(() => {
    inFlight = 0;
    hideBar();
  }, STUCK_AFTER);
}

function requestEnded() {
  inFlight = Math.max(0, inFlight - 1);
  if (inFlight === 0) {
    clearTimeout(stuckTimer);
    hideBar();
  }
}

function busyTarget(evt) {
  const t = evt.detail && evt.detail.target;
  return t instanceof HTMLElement && t !== document.body ? t : null;
}

const emptyBeforeSwap = new WeakSet();

function init() {
  observer.observe(document.body, {
    subtree: true,
    attributes: true,
    attributeOldValue: true,
    attributeFilter: ["class", "style"],
  });

  document.body.addEventListener("htmx:beforeRequest", (evt) => {
    requestStarted();
    const t = busyTarget(evt);
    if (t) t.setAttribute("aria-busy", "true");
  });
  document.body.addEventListener("htmx:afterRequest", (evt) => {
    requestEnded();
    const t = busyTarget(evt);
    if (t) t.removeAttribute("aria-busy");
  });

  document.body.addEventListener("htmx:beforeSwap", (evt) => {
    const t = busyTarget(evt);
    if (t && t.classList.contains("oh-modal__dialog") && !dialogHasContent(t)) {
      emptyBeforeSwap.add(t);
    }
  });
  document.body.addEventListener("htmx:afterSwap", (evt) => {
    const t = busyTarget(evt);
    if (!t || !emptyBeforeSwap.has(t)) return;
    emptyBeforeSwap.delete(t);
    const modal = t.parentElement;
    if (modal && modal.classList.contains("oh-modal--show")) playDialogEntrance(t);
  });

  // Sidebar navigation swaps the whole page body (#ohMainContent): a short
  // fade marks the page change without moving anything.
  document.body.addEventListener("htmx:afterSettle", (evt) => {
    const t = busyTarget(evt);
    if (!t || t.id !== "ohMainContent" || prefersReduced()) return;
    safeAnimate(t, { opacity: [0.35, 1] }, { duration: 0.18, ease: EASE_OUT });
  });
}

if (document.body) init();
else document.addEventListener("DOMContentLoaded", init, { once: true });
