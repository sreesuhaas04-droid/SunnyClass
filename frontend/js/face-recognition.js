/* ================================================================
   SunnyClass AI — Face recognition (browser detect → server match)
   face-api.js computes the 128-d descriptor locally; the raw video never
   leaves the device. The server owns the enrolled gallery and the match
   decision, so a client cannot assert someone else's roll number.
   ================================================================ */
const FaceRecognition = (() => {
  'use strict';

  // Models are vendored under /models so the classroom works offline, loads
  // fast on slow links, and doesn't depend on a third-party CDN staying up.
  const MODEL_URL = window.SUNNYCLASS_MODEL_URL || '/models';
  let modelsLoaded = false;
  let loading = null;
  let verifyTimer = null;
  let options = null;
  let backendName = null;

  const listeners = { verified: [], lost: [], mismatch: [], progress: [] };
  const on = (evt, fn) => { (listeners[evt] ||= []).push(fn); return FaceRecognition; };
  const fire = (evt, payload) => (listeners[evt] || []).forEach(f => {
    try { f(payload); } catch (e) { console.error(e); }
  });

  async function init() {
    if (modelsLoaded) return true;
    if (loading) return loading;

    loading = (async () => {
      if (typeof faceapi === 'undefined') {
        console.error('face-api.js not loaded');
        return false;
      }

      // Pick the fastest inference backend this device actually has.
      // WebGL is 5-20x faster than wasm on phones; wasm (SIMD) is the fallback
      // when WebGL is blocked or unavailable; cpu is the last resort.
      fire('progress', { stage: 'backend', pct: 5 });
      const tf = faceapi.tf;
      // tfjs resolves the .wasm binaries relative to the script that loaded it,
      // and they sit beside vendor/face-api.js — no extra configuration needed.
      for (const backend of ['webgl', 'wasm', 'cpu']) {
        try {
          if (await tf.setBackend(backend)) break;
        } catch { /* try the next one */ }
      }
      await tf.ready();
      backendName = tf.getBackend();
      console.info(`[SunnyClass] face inference backend: ${backendName}`);

      fire('progress', { stage: 'loading-models', pct: 10 });
      await faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL);
      fire('progress', { stage: 'landmarks', pct: 45 });
      await faceapi.nets.faceLandmark68Net.loadFromUri(MODEL_URL);
      fire('progress', { stage: 'recognition', pct: 80 });
      await faceapi.nets.faceRecognitionNet.loadFromUri(MODEL_URL);

      // 224 input is the speed/accuracy sweet spot on phones.
      options = new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.5 });
      modelsLoaded = true;
      fire('progress', { stage: 'ready', pct: 100 });
      return true;
    })();
    return loading;
  }

  const isReady = () => modelsLoaded;
  const backend = () => backendName;

  /** Detect exactly one face and return its descriptor, or a reason why not. */
  async function describe(video) {
    if (!modelsLoaded) await init();
    if (!video || video.readyState < 2) return { ok: false, reason: 'camera_not_ready' };

    const all = await faceapi
      .detectAllFaces(video, options)
      .withFaceLandmarks()
      .withFaceDescriptors();

    if (!all.length) return { ok: false, reason: 'no_face' };
    if (all.length > 1) return { ok: false, reason: 'multiple_faces', count: all.length };

    const det = all[0];
    if (det.detection.score < 0.55) return { ok: false, reason: 'low_quality', score: det.detection.score };

    return {
      ok: true,
      descriptor: Array.from(det.descriptor),
      score: det.detection.score,
      box: det.detection.box,
      landmarks: det.landmarks,
    };
  }

  /** Capture N good samples a second apart — multi-angle enrollment is far more
   *  robust than a single snapshot. */
  async function captureSamples(video, count = 3, onProgress = null) {
    const samples = [];
    let attempts = 0;
    while (samples.length < count && attempts < count * 6) {
      attempts += 1;
      const r = await describe(video);
      if (r.ok) {
        samples.push(r.descriptor);
        onProgress?.({ captured: samples.length, total: count, score: r.score });
      } else {
        onProgress?.({ captured: samples.length, total: count, issue: r.reason });
      }
      await new Promise(res => setTimeout(res, 700));
    }
    return samples;
  }

  async function enroll(video, count = 3, onProgress = null, reenrol = false) {
    const samples = await captureSamples(video, count, onProgress);
    if (!samples.length) return { success: false, reason: 'no_face' };
    const res = await API.face.enroll(samples, 1.0, reenrol);
    return { success: true, ...res };
  }

  /** One verification round-trip against the server gallery. */
  async function verify(video, sessionId = null) {
    const d = await describe(video);
    if (!d.ok) {
      fire('lost', { reason: d.reason, count: d.count });
      return { matched: false, reason: d.reason };
    }
    try {
      const res = await API.face.verify(d.descriptor, sessionId);
      if (res.matched) fire('verified', res);
      else if (res.status === 'identity_mismatch') fire('mismatch', res);
      else fire('lost', { reason: 'no_match', ...res });
      return res;
    } catch (err) {
      return { matched: false, reason: 'network', error: err.message };
    }
  }

  /**
   * Continuous in-class verification. Runs on requestIdleCallback so face
   * inference never competes with video rendering on a low-end phone.
   */
  function startContinuousVerification(video, sessionId, intervalMs = 45000) {
    stopContinuousVerification();
    const tick = () => {
      const run = () => verify(video, sessionId);
      if ('requestIdleCallback' in window) requestIdleCallback(run, { timeout: 4000 });
      else run();
    };
    setTimeout(tick, 2500);            // first check shortly after joining
    verifyTimer = setInterval(tick, intervalMs);
  }

  function stopContinuousVerification() {
    if (verifyTimer) { clearInterval(verifyTimer); verifyTimer = null; }
  }

  const status = () => API.face.status();

  return {
    init, isReady, backend, describe, captureSamples, enroll, verify, status,
    startContinuousVerification, stopContinuousVerification, on,
  };
})();

window.FaceRecognition = FaceRecognition;
