/* ================================================================
   SUNNY — SunnyClass AI assistant
   Floating widget injected on every page. Talks to POST /api/chat, which
   grounds answers in the signed-in student's real attendance data and the
   live lecture transcript. Falls back to a local knowledge base when the
   backend is unreachable.
   ================================================================ */
const SUNNY = (() => {
  'use strict';

  let isOpen = false;
  let isTyping = false;
  let isListening = false;
  let recognition = null;
  let conversationId = null;
  let sessionId = null;
  let capabilities = null;
  const messages = [];
  const el = {};

  /* ---------------- tiny markdown renderer (safe) ---------------- */
  const escapeHtml = (s) => s.replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function md(text) {
    let h = escapeHtml(text);
    h = h.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code.trim()}</code></pre>`);
    h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
    h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    h = h.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    h = h.replace(/^### (.*)$/gm, '<strong>$1</strong>');
    h = h.replace(/^[-•] (.*)$/gm, '<li>$1</li>');
    h = h.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, '<ul>$1</ul>');
    h = h.replace(/\n{2,}/g, '<br><br>').replace(/\n/g, '<br>');
    return h;
  }

  /* ---------------- offline fallback ---------------- */
  const LOCAL = {
    attendance: "📋 Attendance is automatic — your camera stays on, face-api.js builds a face descriptor in your browser, and the server matches it against the class roster every 45 seconds.",
    recording: "🎥 Recordings upload to the server when the class ends, with the full caption transcript attached. Find them under Dashboard → Recordings.",
    captions: "📝 Live captions come from the Web Speech API and are broadcast over the class socket, so you see them even on a slow connection.",
    help: "I can help with attendance, recordings, captions, engagement and your schedule — and with any study question once an LLM key is configured.",
  };
  function localAnswer(q) {
    const l = q.toLowerCase();
    if (/attend|present|absent/.test(l)) return LOCAL.attendance;
    if (/record|replay|download/.test(l)) return LOCAL.recording;
    if (/caption|transcript|subtitle/.test(l)) return LOCAL.captions;
    return LOCAL.help + "\n\n_(I can't reach the SunnyClass server right now, so that came from my offline notes.)_";
  }

  /* ---------------- UI ---------------- */
  function buildUI() {
    const fab = document.createElement('button');
    fab.className = 'sunny-fab';
    fab.id = 'sunny-fab';
    fab.setAttribute('aria-label', 'Open SUNNY, the SunnyClass assistant');
    fab.innerHTML = '☀️';
    fab.addEventListener('click', toggle);

    const panel = document.createElement('div');
    panel.className = 'sunny-panel glass-float';
    panel.id = 'sunny-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'SUNNY assistant');
    panel.innerHTML = `
      <div class="sunny-header">
        <div class="sunny-avatar">☀️</div>
        <div style="flex:1;min-width:0">
          <div class="sunny-name">SUNNY</div>
          <div class="sunny-status">
            <span class="status-dot status-dot-live"></span>
            <span id="sunny-status-text">Online</span>
          </div>
        </div>
        <button class="sunny-close" id="sunny-close" aria-label="Close">✕</button>
      </div>
      <div class="sunny-messages" id="sunny-messages" aria-live="polite"></div>
      <div class="sunny-chips" id="sunny-chips"></div>
      <div class="sunny-input-area">
        <button class="sunny-voice" id="sunny-voice" aria-label="Speak to SUNNY">🎤</button>
        <input class="sunny-input" id="sunny-input" type="text" autocomplete="off"
               placeholder="Ask me anything…" aria-label="Message SUNNY" />
        <button class="sunny-send" id="sunny-send" aria-label="Send">➤</button>
      </div>`;

    document.body.append(fab, panel);

    el.fab = fab;
    el.panel = panel;
    el.messages = panel.querySelector('#sunny-messages');
    el.input = panel.querySelector('#sunny-input');
    el.send = panel.querySelector('#sunny-send');
    el.voice = panel.querySelector('#sunny-voice');
    el.chips = panel.querySelector('#sunny-chips');
    el.status = panel.querySelector('#sunny-status-text');

    panel.querySelector('#sunny-close').addEventListener('click', toggle);
    el.send.addEventListener('click', submit);
    el.input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
    el.voice.addEventListener('click', toggleVoice);

    setChips(['📋 My attendance', '📅 My schedule', '📝 Summarise class', '🤔 What can you do?']);
  }

  function setChips(items) {
    el.chips.innerHTML = '';
    items.slice(0, 4).forEach(label => {
      const b = document.createElement('button');
      b.className = 'sunny-chip';
      b.textContent = label;
      b.addEventListener('click', () => { el.input.value = label.replace(/^\W+\s*/, ''); submit(); });
      el.chips.appendChild(b);
    });
  }

  function addMessage(text, who = 'bot') {
    const div = document.createElement('div');
    div.className = `sunny-msg sunny-msg-${who}`;
    div.innerHTML = who === 'bot' ? md(text) : escapeHtml(text);
    el.messages.appendChild(div);
    el.messages.scrollTop = el.messages.scrollHeight;
    messages.push({ who, text });
    return div;
  }

  function showTyping() {
    if (isTyping) return;
    isTyping = true;
    const d = document.createElement('div');
    d.className = 'sunny-msg sunny-msg-bot sunny-typing';
    d.id = 'sunny-typing';
    d.innerHTML = '<span></span><span></span><span></span>';
    el.messages.appendChild(d);
    el.messages.scrollTop = el.messages.scrollHeight;
  }
  function hideTyping() {
    isTyping = false;
    document.getElementById('sunny-typing')?.remove();
  }

  /* ---------------- conversation ---------------- */
  async function submit() {
    const text = el.input.value.trim();
    if (!text || isTyping) return;
    el.input.value = '';
    addMessage(text, 'user');
    await respond(text);
  }

  async function respond(text) {
    showTyping();
    const t0 = performance.now();
    try {
      const res = await API.sunny.ask(text, { sessionId, conversationId, context: pageContext() });
      conversationId = res.conversation_id;
      // Keep the typing indicator visible for a beat so replies don't flash.
      const elapsed = performance.now() - t0;
      if (elapsed < 250) await new Promise(r => setTimeout(r, 250 - elapsed));
      hideTyping();
      addMessage(res.response, 'bot');
      if (res.suggestions?.length) setChips(res.suggestions);
      el.status.textContent = res.provider === 'fallback' ? 'Offline knowledge base' : `Online · ${res.provider}`;
    } catch (err) {
      hideTyping();
      addMessage(localAnswer(text), 'bot');
      el.status.textContent = 'Reconnecting…';
    }
  }

  /** Small hints about where the user is, so answers are situated. */
  function pageContext() {
    const ctx = { page: document.title, path: location.pathname };
    if (sessionId) ctx.in_class = true;
    const eng = document.getElementById('engagement-score');
    if (eng) ctx.current_engagement = eng.textContent;
    return ctx;
  }

  /* ---------------- voice input ---------------- */
  function toggleVoice() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { addMessage('Voice input needs Chrome, Edge or Safari.', 'bot'); return; }

    if (isListening) { recognition?.stop(); return; }

    recognition = new SR();
    recognition.lang = 'en-IN';
    recognition.interimResults = true;
    recognition.continuous = false;

    recognition.onstart = () => { isListening = true; el.voice.classList.add('listening'); };
    recognition.onresult = (e) => {
      const text = Array.from(e.results).map(r => r[0].transcript).join('');
      el.input.value = text;
      if (e.results[e.results.length - 1].isFinal) { recognition.stop(); submit(); }
    };
    recognition.onerror = () => { isListening = false; el.voice.classList.remove('listening'); };
    recognition.onend = () => { isListening = false; el.voice.classList.remove('listening'); };
    recognition.start();
  }

  /* ---------------- open/close ---------------- */
  function toggle() {
    isOpen = !isOpen;
    el.panel.classList.toggle('open', isOpen);
    el.fab.classList.toggle('open', isOpen);
    el.fab.innerHTML = isOpen ? '✕' : '☀️';
    if (isOpen) {
      if (!messages.length) greet();
      setTimeout(() => el.input.focus(), 260);
    }
  }

  function greet() {
    const user = API.getUser?.();
    const TITLES = new Set(['dr', 'dr.', 'prof', 'prof.', 'mr', 'mr.', 'mrs', 'mrs.', 'ms', 'ms.']);
    const name = user?.full_name?.split(' ').find(w => !TITLES.has(w.toLowerCase()));
    addMessage(
      `Hi${name ? ` ${name}` : ''}! ☀️ I'm **SUNNY**.\n\n` +
      `I can pull up your attendance, summarise a lecture from its transcript, ` +
      `or explain anything from today's class. What do you need?`, 'bot');
  }

  /* ---------------- public ---------------- */
  async function init(opts = {}) {
    sessionId = opts.sessionId || null;
    if (!document.getElementById('sunny-fab')) buildUI();

    try {
      capabilities = await API.sunny.status();
      el.status.textContent = capabilities.llm_enabled
        ? `Online · ${capabilities.provider}`
        : 'Online · knowledge base';
    } catch {
      el.status.textContent = 'Offline';
    }
    return SUNNY;
  }

  const setSession = (id) => { sessionId = id; };
  const open = () => { if (!isOpen) toggle(); };
  const ask = async (text) => { open(); addMessage(text, 'user'); await respond(text); };

  return { init, open, toggle, ask, setSession, get isOpen() { return isOpen; } };
})();

window.SUNNY = SUNNY;

// Auto-mount on every page that includes this file.
document.addEventListener('DOMContentLoaded', () => {
  if (window.API?.isAuthed?.()) SUNNY.init();
});
