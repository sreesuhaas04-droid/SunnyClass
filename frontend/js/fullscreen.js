/* ================================================================
   SmartClass AI — Focus guard
   Enforced fullscreen + tab-switch detection. Every infraction is
   reported to the backend so it lands on the teacher's live panel and
   in the student's attendance record.
   ================================================================ */
const FullscreenGuard = (() => {
  'use strict';

  let sessionId = null;
  let active = false;
  let violations = 0;
  let lastReportAt = 0;
  const listeners = [];
  const REPORT_COOLDOWN_MS = 2500;   // debounce OS-level focus churn

  const on = (fn) => { listeners.push(fn); return FullscreenGuard; };
  const fire = (payload) => listeners.forEach(f => { try { f(payload); } catch (e) { console.error(e); } });

  const isFullscreen = () =>
    Boolean(document.fullscreenElement || document.webkitFullscreenElement ||
            document.mozFullScreenElement || document.msFullscreenElement);

  async function request(el = document.documentElement) {
    try {
      if (el.requestFullscreen) await el.requestFullscreen({ navigationUI: 'hide' });
      else if (el.webkitRequestFullscreen) await el.webkitRequestFullscreen();
      else if (el.msRequestFullscreen) await el.msRequestFullscreen();

      // Phones: keep the class in landscape and the screen awake.
      if (screen.orientation?.lock) screen.orientation.lock('landscape').catch(() => {});
      requestWakeLock();
      return true;
    } catch (err) {
      // iOS Safari has no Fullscreen API on iPhone — degrade to a scroll-locked
      // "immersive" mode rather than blocking the student out of class.
      document.documentElement.classList.add('immersive-fallback');
      return false;
    }
  }

  async function exit() {
    try {
      if (document.exitFullscreen) await document.exitFullscreen();
      else if (document.webkitExitFullscreen) await document.webkitExitFullscreen();
    } catch { /* already exited */ }
    document.documentElement.classList.remove('immersive-fallback');
    releaseWakeLock();
  }

  /* --- keep the screen on during class --- */
  let wakeLock = null;
  async function requestWakeLock() {
    try { wakeLock = await navigator.wakeLock?.request('screen'); } catch { /* unsupported */ }
  }
  function releaseWakeLock() { wakeLock?.release?.().catch(() => {}); wakeLock = null; }

  async function report(kind, severity = 'warning', detail = null) {
    const now = Date.now();
    if (now - lastReportAt < REPORT_COOLDOWN_MS) return;
    lastReportAt = now;

    violations += 1;
    let serverState = null;
    if (sessionId) {
      try {
        serverState = await API.attendance.violation({ session_id: sessionId, kind, severity, detail });
        violations = serverState.violations;
      } catch { /* offline — keep counting locally, it re-syncs next report */ }
    }
    fire({ type: kind, severity, count: violations, flagged: serverState?.flagged || false,
           engagement: serverState?.engagement_score });
  }

  /* --- handlers --- */
  const onVisibility = () => {
    if (!active) return;
    if (document.hidden) report('tab_switch', 'warning', 'Page hidden — student switched tab or app');
  };
  const onBlur = () => {
    if (!active) return;
    if (!document.hidden) report('window_blur', 'info', 'Class window lost focus');
  };
  const onFsChange = () => {
    if (!active) return;
    if (!isFullscreen()) report('fullscreen_exit', 'warning', 'Left fullscreen mode');
  };
  const onKeydown = (e) => {
    if (!active) return;
    // Block the obvious escapes. Browsers reserve Ctrl+W / Alt+Tab at the OS
    // level — those still surface as visibility changes, which we do catch.
    const combo = (e.ctrlKey || e.metaKey);
    if (combo && ['t', 'n', 'w', 'p'].includes(e.key.toLowerCase())) { e.preventDefault(); }
    if (e.key === 'F12' || (combo && e.shiftKey && ['i', 'j', 'c'].includes(e.key.toLowerCase()))) {
      e.preventDefault();
      report('devtools', 'critical', 'Developer tools shortcut pressed');
    }
  };
  const onContextMenu = (e) => { if (active) e.preventDefault(); };
  const onBeforeUnload = (e) => {
    if (!active) return;
    e.preventDefault(); e.returnValue = '';
  };

  function start(id) {
    sessionId = id;
    active = true;
    violations = 0;
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('blur', onBlur);
    document.addEventListener('fullscreenchange', onFsChange);
    document.addEventListener('webkitfullscreenchange', onFsChange);
    document.addEventListener('keydown', onKeydown, true);
    document.addEventListener('contextmenu', onContextMenu);
    window.addEventListener('beforeunload', onBeforeUnload);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && active) requestWakeLock();
    });
    return FullscreenGuard;
  }

  function stop() {
    active = false;
    document.removeEventListener('visibilitychange', onVisibility);
    window.removeEventListener('blur', onBlur);
    document.removeEventListener('fullscreenchange', onFsChange);
    document.removeEventListener('webkitfullscreenchange', onFsChange);
    document.removeEventListener('keydown', onKeydown, true);
    document.removeEventListener('contextmenu', onContextMenu);
    window.removeEventListener('beforeunload', onBeforeUnload);
    releaseWakeLock();
  }

  return {
    start, stop, on, report,
    requestFullscreen: request, exitFullscreen: exit, isFullscreen,
    get violations() { return violations; },
    get active() { return active; },
  };
})();

window.FullscreenGuard = FullscreenGuard;
