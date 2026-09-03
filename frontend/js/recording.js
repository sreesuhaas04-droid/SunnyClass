/* ================================================================
   SunnyClass AI — Class recording
   MediaRecorder capture with chunked buffering, live upload to the
   backend at stop, and a local download fallback if the upload fails.
   ================================================================ */
const Recorder = (() => {
  'use strict';

  let recorder = null;
  let chunks = [];
  let startedAt = 0;
  let sessionId = null;
  let stream = null;
  const listeners = { start: [], stop: [], progress: [], error: [] };

  const on = (evt, fn) => { (listeners[evt] ||= []).push(fn); return Recorder; };
  const fire = (evt, p) => (listeners[evt] || []).forEach(f => { try { f(p); } catch (e) { console.error(e); } });

  function pickMimeType() {
    const candidates = [
      'video/webm;codecs=vp9,opus',
      'video/webm;codecs=vp8,opus',
      'video/webm',
      'video/mp4',                  // Safari
    ];
    return candidates.find(t => MediaRecorder.isTypeSupported?.(t)) || '';
  }

  /** Mix the local mic with every remote peer's audio so the recording
   *  captures the whole class, not just this device. */
  function buildMixedStream(local, peerStreams = []) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const dest = ctx.createMediaStreamDestination();
    const add = (s) => {
      if (s?.getAudioTracks().length) {
        try { ctx.createMediaStreamSource(s).connect(dest); } catch { /* no audio */ }
      }
    };
    add(local);
    peerStreams.forEach(add);

    const out = new MediaStream();
    local?.getVideoTracks().forEach(t => out.addTrack(t));
    dest.stream.getAudioTracks().forEach(t => out.addTrack(t));
    return out;
  }

  function init(localStream, { session, peerStreams = [] } = {}) {
    sessionId = session || sessionId;
    if (!localStream) { fire('error', 'No stream to record'); return false; }
    stream = peerStreams.length ? buildMixedStream(localStream, peerStreams) : localStream;
    return true;
  }

  function start(timesliceMs = 5000) {
    if (!stream) { fire('error', 'Recorder not initialised'); return false; }
    if (recorder?.state === 'recording') return true;

    chunks = [];
    const mimeType = pickMimeType();
    try {
      recorder = new MediaRecorder(stream, {
        mimeType: mimeType || undefined,
        videoBitsPerSecond: 900000,      // ~7 MB/min — friendly on mobile data
        audioBitsPerSecond: 64000,
      });
    } catch (err) {
      fire('error', `Recording unsupported on this browser: ${err.message}`);
      return false;
    }

    recorder.ondataavailable = (e) => {
      if (e.data?.size) {
        chunks.push(e.data);
        fire('progress', {
          seconds: Math.round((Date.now() - startedAt) / 1000),
          bytes: chunks.reduce((a, c) => a + c.size, 0),
        });
      }
    };
    recorder.onerror = (e) => fire('error', e.error?.message || 'Recorder error');

    startedAt = Date.now();
    recorder.start(timesliceMs);       // periodic chunks survive a crash
    fire('start');
    return true;
  }

  function stop() {
    return new Promise((resolve, reject) => {
      if (!recorder || recorder.state === 'inactive') return reject(new Error('Not recording'));
      recorder.onstop = () => {
        const duration = Math.round((Date.now() - startedAt) / 1000);
        const blob = new Blob(chunks, { type: recorder.mimeType || 'video/webm' });
        fire('stop', { blob, duration, size: blob.size });
        resolve({ blob, duration, size: blob.size });
      };
      recorder.stop();
    });
  }

  /** Stop, then upload to the backend. Falls back to a local download so a
   *  network failure never loses the lecture. */
  async function stopAndUpload() {
    const { blob, duration, size } = await stop();
    if (!sessionId) { download(); return { uploaded: false, blob, duration, size }; }
    try {
      const res = await API.recordings.upload(blob, sessionId, duration);
      return { uploaded: true, duration, size, ...res };
    } catch (err) {
      fire('error', `Upload failed (${err.message}) — saved to your device instead.`);
      download();
      return { uploaded: false, blob, duration, size, error: err.message };
    }
  }

  function download(filename) {
    const blob = new Blob(chunks, { type: recorder?.mimeType || 'video/webm' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || `sunnyclass-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.webm`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const isRecording = () => recorder?.state === 'recording';
  const elapsed = () => (isRecording() ? Math.round((Date.now() - startedAt) / 1000) : 0);

  return { init, start, stop, stopAndUpload, download, isRecording, elapsed, on };
})();

window.Recorder = Recorder;
