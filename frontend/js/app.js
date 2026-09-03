/* ================================================================
   SunnyClass AI — Live classroom orchestrator
   Flow:  auth → camera → face models → face verify → roll-number gate
          → admitted → fullscreen lock + mesh video + attendance loop
   ================================================================ */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const qs = new URLSearchParams(location.search);

  const state = {
    user: null,
    sessionId: qs.get('session'),
    roomCode: qs.get('room'),
    classroom: null,
    session: null,
    isHost: false,
    socket: null,
    localStream: null,
    startedAt: null,
    tiles: new Map(),
    participants: new Map(),
    captionsOn: false,
    handRaised: false,
    heartbeatTimer: null,
    timerTimer: null,
  };

  // How often heartbeats fire — kept in one place so elapsed_seconds always matches.
  const HEARTBEAT_INTERVAL_SECS = 15;

  /* ---------------------------------------------------------------- utils */
  function toast(msg, type = 'info', ms = 4200) {
    const box = $('toasts');
    const icons = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.innerHTML = `<span>${icons[type] || 'ℹ'}</span><span></span>`;
    el.lastElementChild.textContent = msg;
    box.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .3s, transform .3s';
      el.style.opacity = '0'; el.style.transform = 'translateX(24px)';
      setTimeout(() => el.remove(), 320);
    }, ms);
  }

  const initials = (name = '') =>
    name.split(' ').filter(Boolean).map(w => w[0]).join('').toUpperCase().slice(0, 2);

  function setStep(step, cls) {
    const li = document.querySelector(`.gate-steps li[data-step="${step}"]`);
    if (!li) return;
    li.classList.remove('active', 'done', 'fail');
    if (cls) li.classList.add(cls);
  }
  function gateMsg(text, kind = '') {
    const el = $('gate-msg');
    el.textContent = text;
    el.className = `gate-msg ${kind}`;
  }

  /* ---------------------------------------------------------------- theme */
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('sc-theme', theme);
    const btn = $('btn-theme');
    if (btn) btn.textContent = theme === 'dark' ? '🌙' : '☀️';
    document.querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', theme === 'dark' ? '#1A0706' : '#f5f0ef');
  }
  function initTheme() {
    applyTheme(API.getUser()?.theme || localStorage.getItem('sc-theme') || 'dark');
    $('btn-theme')?.addEventListener('click', () => {
      const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      API.auth.setTheme(next).catch(() => {});   // follows the account across devices
    });
  }

  /* ------------------------------------------------------------ the gate */
  async function runGate() {
    if (!API.isAuthed()) { location.href = '/index.html?next=' + encodeURIComponent(location.href); return; }

    try { state.user = await API.auth.me(); }
    catch { location.href = '/index.html'; return; }

    applyTheme(state.user.theme || 'dark');
    $('gate-cancel').addEventListener('click', () => location.href = '/dashboard.html');

    // -- resolve which class we're joining --------------------------------
    if (!state.sessionId && !state.roomCode) {
      try {
        const live = await API.sessions.live();
        if (!live.sessions.length) {
          $('gate-title').textContent = 'No live class right now';
          gateMsg('None of your classes are running. Your teacher starts the session.', 'error');
          $('gate-action').textContent = 'Back to dashboard';
          $('gate-action').disabled = false;
          $('gate-action').onclick = () => location.href = '/dashboard.html';
          return;
        }
        state.sessionId = live.sessions[0].session_id;
        $('gate-title').textContent = `Joining ${live.sessions[0].class_name}`;
      } catch (e) { gateMsg(e.message, 'error'); return; }
    }

    // -- is this user the host? Teachers open the room; they aren't gated. ---
    try {
      const info = await API.sessions.get(state.sessionId);
      state.session = info.session;
      state.classroom = info.classroom;
      state.isHost = state.user.id === info.session.host_id ||
                     state.user.role === 'teacher' || state.user.role === 'admin';
      $('gate-title').textContent = state.isHost
        ? `Starting ${info.classroom?.name || 'class'}`
        : `Joining ${info.classroom?.name || 'class'}`;
      if (state.isHost) {
        // Drop the identity steps from the checklist so the host isn't shown
        // requirements that don't apply to them.
        document.querySelector('.gate-steps li[data-step="face"]')?.remove();
        document.querySelector('.gate-steps li[data-step="roll"]')?.remove();
        $('gate-sub').textContent = 'Preparing your camera and the room';
      }
    } catch { /* fall through to the normal student path */ }

    // -- 1. camera --------------------------------------------------------
    setStep('camera', 'active');
    gateMsg('Requesting camera and microphone…');
    try {
      state.localStream = await MeshRTC.initLocalMedia();
      $('gate-video').srcObject = state.localStream;
      setStep('camera', 'done');
    } catch (err) {
      setStep('camera', 'fail');
      gateMsg('Camera and microphone access are required to attend class. ' +
              'Allow them in your browser, then reload.', 'error');
      $('gate-action').textContent = 'Retry';
      $('gate-action').disabled = false;
      $('gate-action').onclick = () => location.reload();
      return;
    }

    // Hosts join without a face check — they are the ones running the room.
    if (state.isHost) {
      document.querySelector('.gate-steps li[data-step="models"]')?.remove();
      gateMsg('Ready when you are.', 'ok');
      $('gate-action').textContent = 'Start the class';
      $('gate-action').disabled = false;
      $('gate-action').onclick = () => joinAsHost();
      return;
    }

    // -- 2. face models ---------------------------------------------------
    setStep('models', 'active');
    gateMsg('Loading the on-device face recognition models…');
    FaceRecognition.on('progress', (p) => {
      if (p.pct < 100) gateMsg(`Loading face models… ${p.pct}%`);
    });
    const ready = await FaceRecognition.init();
    if (!ready) {
      setStep('models', 'fail');
      gateMsg('Could not load the face models. Check your connection and reload.', 'error');
      return;
    }
    setStep('models', 'done');

    // -- ensure this account has an enrolled face -------------------------
    const status = await FaceRecognition.status();
    if (!status.enrolled) {
      setStep('face', 'active');
      $('gate-title').textContent = 'One-time face enrolment';
      gateMsg('Look straight at the camera. We\'ll capture 3 samples.');
      $('gate-action').textContent = 'Capture my face';
      $('gate-action').disabled = false;
      $('gate-action').onclick = async () => {
        $('gate-action').disabled = true;
        $('gate-preview')?.classList.add('scanning');
        document.querySelector('.gate-preview').classList.add('scanning');
        const res = await FaceRecognition.enroll($('gate-video'), 3, (p) => {
          gateMsg(p.issue
            ? `Adjust your position (${p.issue.replace('_', ' ')})… ${p.captured}/${p.total}`
            : `Captured ${p.captured} of ${p.total}…`);
        }).catch(e => ({ success: false, reason: e.message }));
        document.querySelector('.gate-preview').classList.remove('scanning');
        if (!res.success) {
          gateMsg(`Enrolment failed: ${res.reason || 'no face detected'}. Try better lighting.`, 'error');
          $('gate-action').disabled = false;
          return;
        }
        gateMsg('Face enrolled ✓', 'ok');
        verifyAndJoin();
      };
      return;
    }

    verifyAndJoin();
  }

  async function joinAsHost() {
    $('gate-action').disabled = true;
    $('gate-action').textContent = 'Opening room…';
    try {
      const res = await API.join({ session_id: state.sessionId });
      if (!res.admitted) { gateMsg(res.reason || 'Could not open the room.', 'error'); return; }
      state.session = res.session;
      state.classroom = res.classroom;
      state.isHost = true;
      await enterClassroom(res);
    } catch (err) {
      gateMsg(err.message, 'error');
      $('gate-action').disabled = false;
      $('gate-action').textContent = 'Retry';
    }
  }

  async function verifyAndJoin() {
    setStep('face', 'active');
    $('gate-action').disabled = true;
    $('gate-action').textContent = 'Verifying…';
    document.querySelector('.gate-preview').classList.add('scanning');
    gateMsg('Looking for your face…');

    // Retry a few times — first frames are often dark or half-loaded.
    let described = null;
    for (let i = 0; i < 8 && !described?.ok; i += 1) {
      described = await FaceRecognition.describe($('gate-video'));
      if (!described.ok) {
        gateMsg(described.reason === 'multiple_faces'
          ? 'More than one face in frame — please attend alone.'
          : 'Centre your face in the frame…');
        await new Promise(r => setTimeout(r, 700));
      }
    }
    document.querySelector('.gate-preview').classList.remove('scanning');

    if (!described?.ok) {
      setStep('face', 'fail');
      gateMsg('Could not read your face. Improve the lighting and try again.', 'error');
      $('gate-action').textContent = 'Try again';
      $('gate-action').disabled = false;
      $('gate-action').onclick = verifyAndJoin;
      return;
    }
    setStep('face', 'done');

    // -- 4. roll-number gate on the server --------------------------------
    setStep('roll', 'active');
    gateMsg('Matching your roll number against the class roster…');

    let res;
    try {
      res = await API.join({
        session_id: state.sessionId || null,
        room_code: state.roomCode || null,
        descriptor: described.descriptor,
      });
    } catch (err) {
      setStep('roll', 'fail');
      gateMsg(err.message, 'error');
      $('gate-action').textContent = 'Retry';
      $('gate-action').disabled = false;
      $('gate-action').onclick = verifyAndJoin;
      return;
    }

    if (!res.admitted) {
      setStep('roll', 'fail');
      const reason = res.reason === 'face_required'
        ? 'This class requires face verification.'
        : res.reason === 'enrollment_required'
          ? 'No students have enrolled their face for this class yet.'
          : res.reason;
      gateMsg(reason, 'error');
      $('gate-action').textContent = 'Try again';
      $('gate-action').disabled = false;
      $('gate-action').onclick = verifyAndJoin;
      return;
    }

    setStep('roll', 'done');
    gateMsg(`Verified — welcome, ${state.user.full_name}${state.user.roll_number ? ` (${state.user.roll_number})` : ''}`, 'ok');
    state.session = res.session;
    state.classroom = res.classroom;
    state.sessionId = res.session.id;
    state.isHost = state.user.id === res.session.host_id || state.user.role !== 'student';

    $('gate-action').textContent = 'Enter classroom';
    $('gate-action').disabled = false;
    $('gate-action').onclick = () => enterClassroom(res);
    // Fullscreen needs a user gesture, so we wait for that click.
  }

  /* ------------------------------------------------------- the classroom */
  async function enterClassroom(joinRes) {
    $('gate').hidden = true;
    $('shell').hidden = false;

    $('class-name').textContent = state.classroom?.name || 'Class';
    $('class-subject').textContent = state.classroom?.subject || '';
    document.title = `${state.classroom?.name || 'Class'} · SunnyClass AI`;
    document.body.classList.toggle('is-host', state.isHost);
    if (!state.isHost) {
      document.querySelectorAll('.teacher-only').forEach(el => el.remove());
    }

    // 1. lock the screen down (student only)
    if (!state.isHost && state.classroom?.enforce_fullscreen !== false) {
      await FullscreenGuard.requestFullscreen();
      FullscreenGuard.start(state.sessionId);
      FullscreenGuard.on(onViolation);
    }

    // 2. open the class socket
    state.socket = API.socket(state.sessionId);
    wireSocket(state.socket);
    state.socket.connect();

    // 3. join the mesh
    await MeshRTC.start({
      sessionId: state.sessionId,
      sock: state.socket,
      ice: joinRes.ice_servers,
      initialQuality: joinRes.quality_profile,
    });
    MeshRTC.on('stream', addRemoteTile);
    MeshRTC.on('leave', removeTile);
    MeshRTC.on('quality', (q) => { $('net-label').textContent = q.label || q.profile; });
    MeshRTC.on('stats', (s) => {
      $('rtt-label').textContent = s.rttMs ? `${s.rttMs}ms` : '–';
      $('rtt-chip').title = `Latency ${s.rttMs}ms · packet loss ${s.lossPct}% · ${s.peers} peers`;
    });
    MeshRTC.enforceCamera((why) => {
      if (why === 'unrecoverable') toast('Camera lost — rejoin to continue.', 'error', 9000);
      else toast('Camera interrupted, reconnecting…', 'warning');
    });

    addSelfTile();

    // 4. attendance + attention loops
    state.startedAt = new Date(state.session.started_at).getTime() || Date.now();
    state.timerTimer = setInterval(tickTimer, 1000);
    tickTimer();

    if (!state.isHost) {
      EyeTracker.start(document.querySelector('.tile.self video'));
      EyeTracker.on('score', updateRing);
      EyeTracker.on('drowsy', () => toast('You look drowsy — sit up and refocus.', 'warning'));
      EyeTracker.on('away', () => toast('Eyes back on the class, please.', 'info', 2500));

      FaceRecognition.startContinuousVerification(
        document.querySelector('.tile.self video'), state.sessionId, 45000);
      FaceRecognition.on('verified', (r) => {
        markVerified(state.user.id, r.confidence);
      });
      FaceRecognition.on('mismatch', () => {
        toast('The face on camera does not match your account.', 'error', 8000);
      });

      state.heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_INTERVAL_SECS * 1000);
      sendHeartbeat();
    }

    // 5. captions
    CaptionEngine.onCaption(renderCaption);
    if (state.isHost) toggleCaptions(true);   // teacher speech drives the transcript

    // 6. SUNNY, tuned to this lecture
    SUNNY.init({ sessionId: state.sessionId });

    // 7. meeting features — chat, whiteboard, polls, reactions
    Meeting.init({ sock: state.socket, session: state.sessionId,
                   user: state.user, teacher: state.isHost });

    wireToolbar();
    wirePanels();
    refreshAttendancePanel();
    setInterval(refreshAttendancePanel, 20000);

    toast(`Joined ${state.classroom?.name}. You're marked present.`, 'success');
  }

  /* ---------------------------------------------------------------- tiles */
  function tileMarkup(name, roll, extraClass = '') {
    const el = document.createElement('div');
    el.className = `tile ${extraClass}`;
    el.innerHTML = `
      <video autoplay playsinline ${extraClass.includes('self') ? 'muted' : ''}></video>
      <div class="tile-off" hidden>${initials(name)}</div>
      <div class="tile-label"><span>${name}</span>${roll ? `<span class="tile-roll">${roll}</span>` : ''}</div>
      <div class="tile-badges"></div>`;
    return el;
  }

  function addSelfTile() {
    const el = tileMarkup(`${state.user.full_name} (you)`, state.user.roll_number, 'self');
    el.querySelector('video').srcObject = MeshRTC.localStream;
    $('video-grid').appendChild(el);
    state.tiles.set('self', el);
    reflow();
  }

  function addRemoteTile(peerId, stream, info) {
    let el = state.tiles.get(peerId);
    if (!el) {
      el = tileMarkup(info?.name || 'Participant', info?.rollNumber, info?.role !== 'student' ? 'pinned' : '');
      $('video-grid').appendChild(el);
      state.tiles.set(peerId, el);
    }
    el.querySelector('video').srcObject = stream;
    reflow();
  }

  function removeTile(peerId) {
    state.tiles.get(peerId)?.remove();
    state.tiles.delete(peerId);
    reflow();
  }

  function reflow() {
    const grid = $('video-grid');
    grid.dataset.count = String(state.tiles.size);
    $('people-count').textContent = String(state.tiles.size);
  }

  function markVerified(userId, confidence) {
    const el = state.tiles.get('self');
    if (!el) return;
    const badges = el.querySelector('.tile-badges');
    let b = badges.querySelector('.verified');
    if (!b) { b = document.createElement('span'); b.className = 'tile-badge verified'; badges.appendChild(b); }
    b.textContent = `✓ ${Math.round(confidence * 100)}%`;
  }

  /* ------------------------------------------------------------- socket */
  function wireSocket(sock) {
    sock.on('welcome', (msg) => {
      (msg.participants || []).forEach(p => state.participants.set(p.peerId, p));
      (msg.captions || []).forEach(c => appendTranscript(c.speaker, c.text, c.offsetMs));
      renderPeople();
    });
    sock.on('peer-joined', (msg) => {
      state.participants.set(msg.peer.peerId, msg.peer);
      renderPeople();
      toast(`${msg.peer.name} joined`, 'info', 2500);
    });
    sock.on('peer-left', (msg) => { state.participants.delete(msg.peerId); renderPeople(); });
    sock.on('caption', (msg) => {
      CaptionEngine.receive(msg);
      if (msg.isFinal) appendTranscript(msg.speaker, msg.text, msg.offsetMs);
    });
    sock.on('attendance', (msg) => {
      if (msg.userId === state.user.id) markVerified(msg.userId, msg.confidence || 0.9);
      refreshAttendancePanel();
    });
    sock.on('engagement', (msg) => {
      const p = [...state.participants.values()].find(x => x.userId === msg.userId);
      if (p) { p.engagement = msg.score; renderPeople(); }
    });
    sock.on('hand', (msg) => {
      const el = state.tiles.get(msg.peerId);
      if (el) {
        let b = el.querySelector('.tile-badge.hand');
        if (msg.raised && !b) {
          b = document.createElement('span'); b.className = 'tile-badge hand'; b.textContent = '✋';
          el.querySelector('.tile-badges').appendChild(b);
        } else if (!msg.raised) b?.remove();
      }
      if (msg.raised && state.isHost) toast(`${msg.name} raised their hand`, 'info');
    });
    sock.on('violation', (msg) => {
      if (state.isHost) toast(`${msg.name} — ${msg.kind.replace('_', ' ')} (${msg.count})`, 'warning');
    });
    sock.on('class-ended', () => {
      toast('The teacher ended the class.', 'info', 6000);
      setTimeout(leave, 2200);
    });
    sock.on('reconnecting', (d) => toast(`Connection lost — retrying in ${Math.round(d.delay / 1000)}s`, 'warning', 2500));
    sock.on('open', () => $('live-pill').style.opacity = '1');
    sock.on('close', () => $('live-pill').style.opacity = '.4');
  }

  /* -------------------------------------------------------------- panels */
  function wirePanels() {
    document.querySelectorAll('.side-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const name = tab.dataset.panel;
        if (name === 'chat') { Meeting.openChat(); return; }
        Meeting.switchPanel(name);
      });
    });
  }

  function renderPeople() {
    const box = $('panel-people');
    const all = [
      { name: `${state.user.full_name} (you)`, rollNumber: state.user.roll_number,
        role: state.user.role, engagement: EyeTracker.getEngagementScore() },
      ...state.participants.values(),
    ];
    box.innerHTML = '';
    all.forEach(p => {
      const cls = p.engagement >= 80 ? 'score-good' : p.engagement >= 50 ? 'score-mid' : 'score-bad';
      const row = document.createElement('div');
      row.className = 'person';
      row.innerHTML = `
        <div class="avatar">${initials(p.name)}</div>
        <div class="person-meta">
          <div class="person-name"></div>
          <div class="person-sub"></div>
        </div>
        <div class="person-score ${cls}">${p.engagement ?? '–'}${p.engagement != null ? '%' : ''}</div>`;
      row.querySelector('.person-name').textContent = p.name;
      row.querySelector('.person-sub').textContent =
        `${p.rollNumber || (p.role === 'teacher' ? 'Teacher' : '')}${p.handRaised ? ' · ✋' : ''}`;
      box.appendChild(row);
    });
  }

  async function refreshAttendancePanel() {
    if (!state.sessionId) return;
    try {
      const data = state.isHost
        ? await API.analytics.live(state.sessionId)
        : await API.attendance.list(`?session_id=${state.sessionId}`);

      const box = $('panel-attendance');
      const records = data.records || [];
      // Count each bucket strictly — the analytics endpoint's `present` folds in
      // late arrivals, which would double-count them here.
      const present = records.filter(r => r.status === 'present').length;
      const late = records.filter(r => r.status === 'late').length;
      const absent = Math.max(0, (data.roster || records.length) - present - late);

      box.innerHTML = `
        <div class="att-summary">
          <div class="att-stat"><b>${present}</b><span>Present</span></div>
          <div class="att-stat"><b>${late}</b><span>Late</span></div>
          <div class="att-stat"><b>${absent}</b><span>Absent</span></div>
        </div>`;

      records.slice(0, 60).forEach(r => {
        const row = document.createElement('div');
        row.className = 'att-row';
        row.innerHTML = `<span class="att-dot att-${r.status}"></span>
                         <span class="att-name"></span>
                         <span class="att-conf">${Math.round((r.confidence || 0) * 100)}%</span>`;
        row.querySelector('.att-name').textContent =
          `${r.roll_number || ''} ${r.student_name || ''}`.trim() || 'Student';
        box.appendChild(row);
      });
      if (!records.length) box.insertAdjacentHTML('beforeend',
        '<p class="tx-line">No check-ins recorded yet.</p>');
    } catch { /* transient */ }
  }

  const transcript = [];
  function appendTranscript(speaker, text, offsetMs = 0) {
    transcript.push({ speaker, text, offsetMs });
    const box = $('panel-transcript');
    const m = Math.floor(offsetMs / 60000), s = Math.floor((offsetMs % 60000) / 1000);
    const line = document.createElement('div');
    line.className = 'tx-line';
    line.innerHTML = `<span class="tx-time">${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}</span><b></b> <span></span>`;
    line.querySelector('b').textContent = `${speaker || 'Speaker'}:`;
    line.querySelector('span:last-child').textContent = text;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }

  /* ------------------------------------------------------------ captions */
  function renderCaption({ final, interim, speaker, error }) {
    if (error) { toast(error, 'warning'); return; }
    const bar = $('captions-bar');
    const text = interim || final;
    if (!text) return;
    bar.hidden = false;
    $('cc-speaker').textContent = speaker ? `${speaker}:` : '';
    $('cc-text').textContent = text;
    clearTimeout(bar._t);
    bar._t = setTimeout(() => { if (!state.captionsOn) bar.hidden = true; else $('cc-text').textContent = ''; }, 6000);
  }

  function toggleCaptions(force) {
    state.captionsOn = force !== undefined ? force : !state.captionsOn;
    $('btn-captions')?.classList.toggle('active', state.captionsOn);
    $('captions-bar').hidden = !state.captionsOn;
    if (state.captionsOn) CaptionEngine.start({ session: state.sessionId, sock: state.socket });
    else CaptionEngine.stop();
  }

  /* --------------------------------------------------------- heartbeats */
  async function sendHeartbeat() {
    const st = EyeTracker.getState();
    try {
      const res = await API.attendance.heartbeat({
        session_id: state.sessionId,
        engagement_score: st.score,
        gaze_on_screen: st.gazeOnScreen,
        eye_aspect_ratio: st.ear,
        face_present: st.facePresent,
        elapsed_seconds: HEARTBEAT_INTERVAL_SECS,
      });
      updateRing(res.engagement_score);
      state.socket?.send({ type: 'engagement', score: res.engagement_score });
    } catch { /* offline; next tick retries */ }
  }

  function updateRing(score) {
    const ring = $('engagement-ring');
    const colour = score >= 80 ? '#2ECC71' : score >= 50 ? '#F5A623' : 'var(--accent)';
    ring.style.background = `conic-gradient(${colour} 0% ${score}%, var(--bg-tertiary) ${score}% 100%)`;
    $('engagement-score').textContent = `${Math.round(score)}%`;
  }

  function tickTimer() {
    const secs = Math.max(0, Math.floor((Date.now() - state.startedAt) / 1000));
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
    $('class-timer').textContent = h
      ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
      : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  /* -------------------------------------------------------- proctoring */
  function onViolation({ type, count, flagged }) {
    const overlay = $('violation-overlay');
    const titles = {
      tab_switch: 'Come back to class',
      fullscreen_exit: 'Fullscreen is required',
      window_blur: 'The class lost focus',
      devtools: 'Developer tools are not allowed',
    };
    $('violation-title').textContent = titles[type] || 'Focus required';
    $('violation-count').textContent = String(count);
    overlay.hidden = false;
    if (flagged) toast('Your attendance record has been flagged for your teacher.', 'error', 8000);
  }

  /* ---------------------------------------------------------- toolbar */
  function wireToolbar() {
    $('btn-mic').addEventListener('click', (e) => {
      const on = MeshRTC.toggleMic();
      e.currentTarget.querySelector('span').textContent = on ? '🎤' : '🔇';
      e.currentTarget.classList.toggle('active', !on);
    });

    $('btn-captions').addEventListener('click', () => toggleCaptions());

    $('btn-hand').addEventListener('click', (e) => {
      state.handRaised = !state.handRaised;
      state.socket?.send({ type: 'hand', raised: state.handRaised });
      e.currentTarget.classList.toggle('active', state.handRaised);
    });

    $('btn-screen')?.addEventListener('click', async () => {
      try { await MeshRTC.shareScreen(); toast('Screen sharing started', 'success'); }
      catch { toast('Screen sharing cancelled', 'info'); }
    });

    $('btn-record')?.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (!Recorder.isRecording()) {
        const peerStreams = [...MeshRTC.peers.values()].map(p => p.stream).filter(Boolean);
        Recorder.init(MeshRTC.localStream, { session: state.sessionId, peerStreams });
        if (Recorder.start()) {
          btn.classList.add('active', 'recording');
          btn.querySelector('label').textContent = 'Stop';
          toast('Recording started', 'success');
        }
      } else {
        btn.classList.remove('active', 'recording');
        btn.querySelector('label').textContent = 'Record';
        toast('Uploading recording…', 'info');
        const res = await Recorder.stopAndUpload();
        toast(res.uploaded
          ? `Recording saved (${(res.size / 1048576).toFixed(1)} MB)`
          : 'Recording downloaded to your device', res.uploaded ? 'success' : 'warning');
      }
    });

    const menu = $('quality-menu');
    $('btn-quality').addEventListener('click', () => { menu.hidden = !menu.hidden; });
    menu.querySelectorAll('button').forEach(b => {
      b.addEventListener('click', () => {
        const profiles = {
          high: { width: 1280, height: 720, frameRate: 30, maxBitrate: 1200000, profile: 'high', label: 'HD 720p' },
          medium: { width: 854, height: 480, frameRate: 20, maxBitrate: 600000, profile: 'medium', label: 'SD 480p' },
          low: { width: 640, height: 360, frameRate: 15, maxBitrate: 300000, profile: 'low', label: 'Low 360p' },
          minimal: { width: 320, height: 240, frameRate: 10, maxBitrate: 120000, profile: 'minimal', label: 'Data saver' },
          audio: { width: 0, height: 0, frameRate: 0, maxBitrate: 24000, profile: 'audio', label: 'Audio only' },
        };
        MeshRTC.applyQuality(profiles[b.dataset.q]);
        menu.querySelectorAll('button').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        menu.hidden = true;
        toast(`Video set to ${profiles[b.dataset.q].label}`, 'info');
      });
    });

    $('btn-sunny').addEventListener('click', () => SUNNY.open());
    $('btn-leave').addEventListener('click', leave);

    $('btn-dismiss-violation').addEventListener('click', async () => {
      $('violation-overlay').hidden = true;
      if (!state.isHost) await FullscreenGuard.requestFullscreen();
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      const k = e.key.toLowerCase();
      if (k === 'm') $('btn-mic').click();
      if (k === 'c') $('btn-captions').click();
      if (k === 'h') $('btn-hand').click();
      if (k === 't') $('btn-chat').click();
      if (k === 'w') $('btn-whiteboard')?.click();
      if (k === 'r') $('btn-reactions')?.click();
    });
  }

  /* ------------------------------------------------------------- leave */
  async function leave() {
    if (!confirm('Leave the class?')) return;
    clearInterval(state.heartbeatTimer);
    clearInterval(state.timerTimer);
    FullscreenGuard.stop();
    await FullscreenGuard.exitFullscreen();
    EyeTracker.stop();
    FaceRecognition.stopContinuousVerification();
    CaptionEngine.stop();
    state.socket?.close();
    MeshRTC.stop();

    if (state.isHost && confirm('End the class for everyone? (Cancel just leaves)')) {
      try { await API.sessions.end(state.sessionId); } catch { /* ignore */ }
    }
    location.href = '/dashboard.html';
  }

  /* --------------------------------------------------------------- boot */
  document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    runGate().catch(err => {
      console.error(err);
      gateMsg(err.message || 'Something went wrong.', 'error');
    });
  });
})();
