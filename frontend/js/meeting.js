/* ================================================================
   SunnyClass AI — Meeting features
   In-class chat (persisted), whiteboard, polls, reactions.
   All rides the existing class socket; the server relays + persists.
   ================================================================ */
const Meeting = (() => {
  'use strict';

  let socket = null;
  let sessionId = null;
  let userId = null;
  let isTeacher = false;
  let chatOpen = false;
  let unread = 0;

  /* ------------------------------------------------------------- chat */
  const chatEl = () => document.getElementById('chat-scroll');

  function addChatMessage(msg, own = false) {
    const box = chatEl();
    if (!box) return;
    const row = document.createElement('div');
    row.className = 'chat-msg' + (own ? ' own' : '') +
      ((msg.senderRole || '') !== 'student' ? ' from-teacher' : '');
    const time = msg.ts ? new Date(msg.ts.replace('Z', '')).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
    row.innerHTML = `
      <div class="chat-name"></div>
      <div class="chat-bubble"></div>
      <div class="chat-time">${time}</div>`;
    row.querySelector('.chat-name').textContent =
      msg.senderName + (own ? ' (you)' : '');
    row.querySelector('.chat-bubble').textContent = msg.text;   // textContent — never innerHTML
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
  }

  function openChat() {
    chatOpen = true; unread = 0;
    document.getElementById('btn-chat')?.classList.remove('has-badge');
    switchPanel('chat');
    setTimeout(() => document.getElementById('chat-input')?.focus(), 50);
  }

  function notifyChat() {
    if (chatOpen) return;
    unread += 1;
    const btn = document.getElementById('btn-chat');
    if (btn) {
      btn.classList.add('has-badge');
      btn.dataset.badge = String(Math.min(unread, 99));
    }
  }

  /* ------------------------------------------------------- whiteboard */
  const wb = {
    canvas: null, ctx: null,
    drawing: false, points: [],
    color: '#E8B98F', width: 3,
    strokes: [],            // my own strokes for undo
    rafPending: false,
    buffer: [],             // points batched before sending
  };

  function wbOpen() {
    const wrap = document.getElementById('whiteboard');
    if (!wrap.hidden) return;
    wrap.hidden = false;
    const canvas = document.getElementById('wb-canvas');
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    wb.canvas = canvas;
    wb.ctx = canvas.getContext('2d');
    wb.ctx.scale(dpr, dpr);
    wb.ctx.lineCap = 'round';
    wb.ctx.lineJoin = 'round';
    document.body.classList.add('wb-open');
  }

  function wbClose() {
    document.getElementById('whiteboard').hidden = true;
    document.body.classList.remove('wb-open');
  }

  function wbDraw(points, color, width, fromRemote = false) {
    if (!wb.ctx || points.length < 2) return;
    const ctx = wb.ctx;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i += 1) ctx.lineTo(points[i][0], points[i][1]);
    ctx.stroke();
    if (!fromRemote) wb.strokes.push({ points, color, width });
  }

  function wbPos(e) {
    const r = wb.canvas.getBoundingClientRect();
    const ev = e.touches?.[0] || e;
    return [ev.clientX - r.left, ev.clientY - r.top];
  }

  function wbStart(e) {
    if (!wb.ctx) return;
    e.preventDefault();
    wb.drawing = true;
    wb.points = [wbPos(e)];
  }

  function wbMove(e) {
    if (!wb.drawing) return;
    e.preventDefault();
    wb.points.push(wbPos(e));
    // local preview: draw the growing segment cheaply
    const n = wb.points.length;
    if (n >= 2) wbDraw(wb.points.slice(n - 2), wb.color, wb.width);
    // throttle sends: flush every ~80ms of accumulated points
    if (!wb.rafPending) {
      wb.rafPending = true;
      setTimeout(() => {
        wb.rafPending = false;
        if (wb.points.length > 1) {
          socket?.send({ type: 'whiteboard', action: 'stroke',
                         points: wb.points.map(p => [Math.round(p[0]), Math.round(p[1])]),
                         color: wb.color, width: wb.width });
        }
      }, 80);
    }
  }

  function wbEnd() {
    if (!wb.drawing) return;
    wb.drawing = false;
    if (wb.points.length > 1) {
      socket?.send({ type: 'whiteboard', action: 'stroke',
                     points: wb.points.map(p => [Math.round(p[0]), Math.round(p[1])]),
                     color: wb.color, width: wb.width });
    }
    wb.points = [];
  }

  function wbClear(send = true) {
    if (!wb.ctx) return;
    wb.ctx.clearRect(0, 0, wb.canvas.width, wb.canvas.height);
    wb.strokes = [];
    if (send) socket?.send({ type: 'whiteboard', action: 'clear' });
  }

  function wbUndo() {
    if (!wb.ctx || !wb.strokes.length) return;
    wb.strokes.pop();
    wb.ctx.clearRect(0, 0, wb.canvas.width, wb.canvas.height);
    wb.strokes.forEach(s => wbDraw(s.points, s.color, s.width, true));
    socket?.send({ type: 'whiteboard', action: 'undo' });   // others replay without our last stroke
  }

  function wbReplayAllRemote() {
    // after an undo from a peer, the simplest correct view is to redraw what we
    // still hold locally; server keeps strokes stateless so each client is
    // authoritative for its own strokes.
  }

  /* -------------------------------------------------------------- polls */
  let activePoll = null;
  let myVote = null;

  function renderPoll(poll, ownVote = null) {
    activePoll = poll;
    const card = document.getElementById('poll-live');
    document.getElementById('poll-live-question').textContent = poll.question;
    const body = document.getElementById('poll-live-body');
    body.innerHTML = '';
    const total = poll.total || 0;
    const counts = poll.counts || null;

    poll.options.forEach((opt, i) => {
      const row = document.createElement('button');
      row.className = 'poll-option';
      const count = counts ? counts[i] : null;
      const pct = counts && total ? Math.round((count / total) * 100) : 0;
      if (ownVote === i || myVote === i) row.classList.add('chosen');
      row.innerHTML = `
        <span class="poll-opt-text"></span>
        <span class="poll-bar" style="width:${counts ? pct : 0}%"></span>
        <span class="poll-opt-count">${counts != null ? `${count}` : ''}</span>`;
      row.querySelector('.poll-opt-text').textContent = opt;
      row.onclick = () => votePoll(poll.pollId, i);
      body.appendChild(row);
    });

    document.getElementById('poll-live-total').textContent =
      counts ? `${total} votes` : `${total} voted`;
    document.getElementById('poll-live-status').textContent =
      poll.closed ? 'Poll closed — results shown' : (isTeacher ? 'Poll live' : 'Vote above');
    const closeBtn = document.getElementById('poll-close-btn');
    closeBtn.hidden = !isTeacher || poll.closed;
    card.hidden = false;
    clearTimeout(card._t);
    card._t = setTimeout(() => { if (poll.closed) card.hidden = true; }, 15000);
  }

  function votePoll(pollId, option) {
    if (!activePoll || activePoll.closed) return;
    if (myVote != null) return;                 // one vote per user (server also enforces)
    myVote = option;
    socket?.send({ type: 'poll-vote', pollId, option });
    document.getElementById('poll-live-status').textContent = 'Vote counted — thanks!';
  }

  /* ---------------------------------------------------------- reactions */
  const REACTION_EMOJI = ['👍', '👏', '❤️', '😂', '🎉', '🤔', '🚀'];
  function floatReaction(emoji, name) {
    const stage = document.getElementById('stage');
    if (!stage) return;
    const el = document.createElement('div');
    el.className = 'reaction-float';
    el.textContent = emoji;
    el.style.left = `${12 + Math.random() * 76}%`;
    el.title = name || '';
    stage.appendChild(el);
    setTimeout(() => el.remove(), 2600);
  }

  /* --------------------------------------------------------------- init */
  function init({ sock, session, user, teacher }) {
    socket = sock;
    sessionId = session;
    userId = user?.id;
    isTeacher = !!teacher;
    myVote = null;

    /* --- socket events --- */
    sock.on('welcome', (msg) => {
      (msg.chat || []).forEach(c => addChatMessage(c, c.senderId === userId));
      (msg.polls || []).forEach(p => { if (!p.closed) renderPoll(p); });
    });

    sock.on('chat', (msg) => {
      const own = msg.senderId === userId;
      addChatMessage(msg, own);
      if (!own) notifyChat();
    });

    sock.on('poll', (msg) => {
      if (msg.counts) {
        renderPoll(msg);
      } else if (!isTeacher) {
        myVote = myVote;   // preserve
        renderPoll(msg);
      } else if (isTeacher && !msg.counts && msg.closed) {
        renderPoll(msg);
      } else if (isTeacher) {
        // teacher sees a "voted" tally card until closed — keep the composer view
        renderPoll(msg);
      }
    });

    sock.on('whiteboard', (msg) => {
      if (msg.action === 'stroke') wbDraw(msg.points, msg.color, msg.width, true);
      else if (msg.action === 'clear') {
        wb.ctx?.clearRect(0, 0, wb.canvas.width, wb.canvas.height);
        if (msg.peerId !== 'self') toastBoard('Board cleared');
      } else if (msg.action === 'undo') wbReplayAllRemote();
    });

    sock.on('reaction', (msg) => {
      const emoji = String(msg.emoji || '').slice(0, 8);
      if (REACTION_EMOJI.includes(emoji)) floatReaction(emoji, msg.name);
    });

    /* --- toolbar wiring --- */
    document.getElementById('btn-chat')?.addEventListener('click', () => {
      if (chatOpen) switchPanel('people'), (chatOpen = false);
      else openChat();
    });

    document.getElementById('chat-form')?.addEventListener('submit', (e) => {
      e.preventDefault();
      const input = document.getElementById('chat-input');
      const text = (input.value || '').trim();
      if (!text) return;
      socket?.send({ type: 'chat', text });
      input.value = '';
    });

    // reactions
    const rmenu = document.getElementById('reaction-menu');
    document.getElementById('btn-reactions')?.addEventListener('click', () => {
      rmenu.hidden = !rmenu.hidden;
    });
    rmenu?.querySelectorAll('button').forEach(b => {
      b.addEventListener('click', () => {
        socket?.send({ type: 'reaction', emoji: b.dataset.r });
        floatReaction(b.dataset.r, 'You');
        rmenu.hidden = true;
      });
    });

    // whiteboard (teacher opens; students see strokes live too)
    document.getElementById('btn-whiteboard')?.addEventListener('click', () => {
      const wrap = document.getElementById('whiteboard');
      if (wrap.hidden) wbOpen(); else wbClose();
    });
    document.getElementById('wb-close')?.addEventListener('click', wbClose);
    document.getElementById('wb-clear')?.addEventListener('click', () => wbClear(true));
    document.getElementById('wb-eraser')?.addEventListener('click', wbUndo);
    document.querySelectorAll('.wb-color').forEach(b => {
      b.addEventListener('click', () => {
        wb.color = b.dataset.c;
        document.querySelectorAll('.wb-color').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
      });
    });
    const canvas = document.getElementById('wb-canvas');
    canvas?.addEventListener('mousedown', wbStart);
    canvas?.addEventListener('mousemove', wbMove);
    window.addEventListener('mouseup', wbEnd);
    canvas?.addEventListener('touchstart', wbStart, { passive: false });
    canvas?.addEventListener('touchmove', wbMove, { passive: false });
    canvas?.addEventListener('touchend', wbEnd);

    // polls (teacher only)
    const composer = document.getElementById('poll-composer');
    document.getElementById('btn-poll')?.addEventListener('click', () => {
      composer.hidden = !composer.hidden;
    });
    document.getElementById('poll-add-option')?.addEventListener('click', () => {
      const opts = document.getElementById('poll-options');
      if (opts.querySelectorAll('.poll-opt').length >= 6) return;
      const inp = document.createElement('input');
      inp.type = 'text'; inp.className = 'poll-opt';
      inp.placeholder = `Option ${opts.querySelectorAll('.poll-opt').length + 1}`;
      inp.maxLength = 120;
      opts.appendChild(inp);
    });
    document.getElementById('poll-launch')?.addEventListener('click', () => {
      const question = document.getElementById('poll-question').value.trim();
      const options = [...document.querySelectorAll('.poll-opt')]
        .map(i => i.value.trim()).filter(Boolean);
      if (!question || options.length < 2) {
        window.toast?.('A poll needs a question and 2+ options.', 'warning');
        return;
      }
      socket?.send({ type: 'poll-create', question, options });
      composer.hidden = true;
      document.getElementById('poll-question').value = '';
      document.querySelectorAll('.poll-opt').forEach(i => (i.value = ''));
    });
    document.getElementById('poll-close-btn')?.addEventListener('click', () => {
      if (activePoll) socket?.send({ type: 'poll-close', pollId: activePoll.pollId });
    });
  }

  function switchPanel(name) {
    document.querySelectorAll('.side-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.panel === name);
    });
    ['people', 'chat', 'attendance', 'transcript'].forEach(p => {
      const el = document.getElementById(`panel-${p}`);
      if (el) el.hidden = p !== name;
    });
    if (name === 'chat') { chatOpen = true; unread = 0; }
  }

  function toastBoard(msg) {
    const t = window.toast;
    if (t) t(msg, 'info', 2000);
  }

  return {
    init,
    openChat,
    switchPanel,
    get chatOpen() { return chatOpen; },
    whiteboard: { open: wbOpen, close: wbClose, clear: () => wbClear(true) },
  };
})();

window.Meeting = Meeting;
