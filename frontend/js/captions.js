/* ================================================================
   SmartClass AI — Live captions
   Web Speech API transcription. Final lines go to the backend (stored +
   fanned out over the class socket) so every student sees captions even
   when their own mic is muted or their browser lacks speech support.
   ================================================================ */
const CaptionEngine = (() => {
  'use strict';

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let running = false;
  let sessionId = null;
  let socket = null;
  let startedAt = 0;
  let restartTimer = null;
  const transcript = [];
  const listeners = [];

  const supported = () => Boolean(SR);
  const onCaption = (fn) => { listeners.push(fn); return CaptionEngine; };
  const emit = (payload) => listeners.forEach(f => { try { f(payload); } catch (e) { console.error(e); } });

  function build(lang) {
    const r = new SR();
    r.continuous = true;
    r.interimResults = true;
    r.maxAlternatives = 1;
    r.lang = lang;

    r.onresult = (event) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const res = event.results[i];
        const text = res[0].transcript.trim();
        if (!text) continue;

        if (res.isFinal) {
          const offsetMs = Date.now() - startedAt;
          const entry = { text, offsetMs, confidence: res[0].confidence || 0.9, own: true };
          transcript.push(entry);
          emit({ final: text, interim: '', own: true });

          // Prefer the socket (sub-100ms fan-out); the POST persists it.
          socket?.send({ type: 'caption', text, isFinal: true, offsetMs });
          if (sessionId) {
            API.captions.push({ session_id: sessionId, text, is_final: true, offset_ms: offsetMs,
                                confidence: entry.confidence }).catch(() => {});
          }
        } else {
          interim += text + ' ';
        }
      }
      if (interim) emit({ final: '', interim: interim.trim(), own: true });
    };

    r.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        running = false;
        emit({ error: 'Microphone permission is required for captions.' });
      }
      // 'no-speech' / 'network' are transient — onend restarts us.
    };

    r.onend = () => {
      // Chrome stops after ~60s of silence; keep it alive for the whole class.
      if (running) restartTimer = setTimeout(() => { try { r.start(); } catch { /* racing */ } }, 350);
    };
    return r;
  }

  function start({ session, sock, lang = 'en-IN' } = {}) {
    sessionId = session || sessionId;
    socket = sock || socket;
    startedAt = startedAt || Date.now();

    if (!supported()) {
      emit({ error: 'This browser cannot generate captions, but you will still receive the teacher\'s.' });
      return false;   // receive-only mode still works via the socket
    }
    if (running) return true;
    recognition = build(lang);
    try { recognition.start(); running = true; } catch { return false; }
    return true;
  }

  function stop() {
    running = false;
    clearTimeout(restartTimer);
    try { recognition?.stop(); } catch { /* not started */ }
    recognition = null;
  }

  /** Captions arriving from other participants over the class socket. */
  function receive(msg) {
    if (msg.isFinal) transcript.push({ text: msg.text, speaker: msg.speaker, offsetMs: msg.offsetMs });
    emit({ final: msg.isFinal ? msg.text : '', interim: msg.isFinal ? '' : msg.text,
           speaker: msg.speaker, own: false });
  }

  const getTranscript = () => transcript.slice();
  const getText = () => transcript.map(t => t.text).join(' ');

  async function summarize() {
    if (!sessionId) return null;
    return API.captions.summarize(sessionId);
  }

  function download(filename = 'class-transcript.txt') {
    const body = transcript.map(t => {
      const m = Math.floor((t.offsetMs || 0) / 60000);
      const s = Math.floor(((t.offsetMs || 0) % 60000) / 1000);
      return `[${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}] ${t.speaker || 'You'}: ${t.text}`;
    }).join('\n');
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain' }));
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  }

  return { start, stop, receive, onCaption, supported, getTranscript, getText,
           summarize, download, get running() { return running; } };
})();

window.CaptionEngine = CaptionEngine;
