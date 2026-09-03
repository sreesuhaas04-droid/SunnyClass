/* ================================================================
   SunnyClass AI — Gaze & attention
   68-point landmarks → eye-aspect-ratio (drowsiness) + gaze direction
   (looking away). Produces the engagement score sent on each heartbeat.
   ================================================================ */
const EyeTracker = (() => {
  'use strict';

  let video = null;
  let running = false;
  let rafId = null;
  let lastRun = 0;

  let score = 100;
  let eyesClosedSince = null;
  let gazeAwaySince = null;
  let faceMissingSince = null;
  let lastState = { gazeOnScreen: true, ear: 0.3, facePresent: true };

  const SAMPLE_INTERVAL_MS = 1200;      // inference is expensive; 1/sec is plenty
  const EAR_CLOSED = 0.19;
  const DROWSY_MS = 2800;
  const GAZE_AWAY_MS = 6000;

  const listeners = { drowsy: [], away: [], back: [], score: [] };
  const on = (e, fn) => { (listeners[e] ||= []).push(fn); return EyeTracker; };
  const fire = (e, p) => (listeners[e] || []).forEach(f => { try { f(p); } catch (err) { console.error(err); } });

  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

  /** Eye Aspect Ratio (Soukupová & Čech) — vertical/horizontal eye opening. */
  function ear(eye) {
    if (eye.length < 6) return 0.3;
    return (dist(eye[1], eye[5]) + dist(eye[2], eye[4])) / (2 * dist(eye[0], eye[3]));
  }

  /** Horizontal iris offset relative to the eye box, plus head yaw from the
   *  nose position — enough to tell "reading the slide" from "looking away". */
  function gazeRatio(landmarks) {
    const left = landmarks.getLeftEye();
    const right = landmarks.getRightEye();
    const nose = landmarks.getNose();
    if (!left.length || !right.length || !nose.length) return 0.5;

    const eyeCentre = (left[0].x + left[3].x + right[0].x + right[3].x) / 4;
    const noseX = nose[Math.floor(nose.length / 2)].x;
    const span = Math.abs(right[3].x - left[0].x) || 1;
    return 0.5 + (noseX - eyeCentre) / span;    // ~0.5 when facing the screen
  }

  async function sample() {
    if (typeof faceapi === 'undefined' || !video || video.readyState < 2) return;

    const det = await faceapi
      .detectSingleFace(video, new faceapi.TinyFaceDetectorOptions({ inputSize: 160, scoreThreshold: 0.4 }))
      .withFaceLandmarks();

    const now = Date.now();

    if (!det) {
      faceMissingSince ??= now;
      if (now - faceMissingSince > 4000) { adjust(-4); lastState.facePresent = false; }
      return;
    }
    faceMissingSince = null;
    lastState.facePresent = true;

    const lm = det.landmarks;
    const avgEar = (ear(lm.getLeftEye()) + ear(lm.getRightEye())) / 2;
    lastState.ear = Number(avgEar.toFixed(3));

    // --- drowsiness ---
    if (avgEar < EAR_CLOSED) {
      eyesClosedSince ??= now;
      if (now - eyesClosedSince > DROWSY_MS) {
        adjust(-6);
        fire('drowsy', { ear: avgEar, seconds: Math.round((now - eyesClosedSince) / 1000) });
      }
    } else {
      eyesClosedSince = null;
    }

    // --- gaze ---
    const g = gazeRatio(lm);
    const onScreen = g > 0.32 && g < 0.68;
    lastState.gazeOnScreen = onScreen;

    if (!onScreen) {
      gazeAwaySince ??= now;
      if (now - gazeAwaySince > GAZE_AWAY_MS) {
        adjust(-3);
        fire('away', { ratio: Number(g.toFixed(2)), seconds: Math.round((now - gazeAwaySince) / 1000) });
      }
    } else {
      if (gazeAwaySince && now - gazeAwaySince > GAZE_AWAY_MS) fire('back', {});
      gazeAwaySince = null;
      adjust(+2);        // reward sustained attention
    }
  }

  function adjust(delta) {
    const next = Math.max(0, Math.min(100, score + delta));
    if (next !== score) { score = next; fire('score', score); }
  }

  function loop(ts) {
    if (!running) return;
    if (ts - lastRun > SAMPLE_INTERVAL_MS) {
      lastRun = ts;
      sample().catch(() => {});
    }
    rafId = requestAnimationFrame(loop);
  }

  function start(videoEl) {
    video = videoEl;
    running = true;
    score = 100;
    rafId = requestAnimationFrame(loop);
    return EyeTracker;
  }

  function stop() {
    running = false;
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
  }

  return {
    start, stop, on,
    getEngagementScore: () => Math.round(score),
    getState: () => ({ ...lastState, score: Math.round(score) }),
    get running() { return running; },
  };
})();

window.EyeTracker = EyeTracker;
