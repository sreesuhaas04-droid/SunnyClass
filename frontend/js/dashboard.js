/* ================================================================
   SunnyClass AI — Dashboard
   Every number and every chart below is computed server-side from real
   attendance rows; nothing here is mock data.
   ================================================================ */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = { user: null, classes: [], live: [] };

  /* ---------------- helpers ---------------- */
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function toast(msg, type = 'info') {
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.innerHTML = '<span>ℹ</span><span></span>';
    el.lastElementChild.textContent = msg;
    $('toasts').appendChild(el);
    setTimeout(() => el.remove(), 4500);
  }

  /* tooltip shared by every chart */
  const tip = $('tip');
  function bindTip(el, html) {
    el.addEventListener('mouseenter', () => { tip.innerHTML = html; tip.classList.add('show'); });
    el.addEventListener('mousemove', (e) => {
      tip.style.left = `${Math.min(e.clientX + 14, window.innerWidth - 230)}px`;
      tip.style.top = `${e.clientY - 12}px`;
    });
    el.addEventListener('mouseleave', () => tip.classList.remove('show'));
  }

  /* ---------------- theme ---------------- */
  function applyTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('sc-theme', t);
    $('btn-theme').textContent = t === 'dark' ? '🌙' : '☀️';
    document.querySelector('meta[name=theme-color]')
      .setAttribute('content', t === 'dark' ? '#1A0706' : '#f5f0ef');
  }

  /* ---------------- stat tiles ---------------- */
  function renderStats(overview) {
    const rate = overview.rate;
    const cls = rate >= 85 ? 'stat-good' : rate >= 70 ? 'stat-warn' : 'stat-bad';
    const tiles = [
      { label: 'Attendance rate', value: `${rate}%`, note:
          `${overview.totals.present + overview.totals.late} of ${overview.totals.records} sessions`, cls },
      { label: 'Avg engagement', value: `${overview.avg_engagement}%`, note: 'From gaze & attention sampling',
        cls: overview.avg_engagement >= 80 ? 'stat-good' : overview.avg_engagement >= 60 ? 'stat-warn' : 'stat-bad' },
      { label: 'Sessions missed', value: String(overview.totals.absent), note: 'Marked absent',
        cls: overview.totals.absent === 0 ? 'stat-good' : 'stat-bad' },
      { label: 'Proctor flags', value: String(overview.total_violations), note:
          `${overview.flagged} record${overview.flagged === 1 ? '' : 's'} flagged`,
        cls: overview.total_violations === 0 ? 'stat-good' : 'stat-warn' },
    ];
    $('stats').innerHTML = tiles.map(t => `
      <div class="card stat">
        <span class="stat-label">${esc(t.label)}</span>
        <span class="stat-value ${t.cls}">${esc(t.value)}</span>
        <span class="stat-note">${esc(t.note)}</span>
      </div>`).join('');
  }

  /* ---------------- stacked bar: part-to-whole ---------------- */
  function renderComposition(overview) {
    const segs = [
      { key: 'present', label: 'Present', value: overview.totals.present, colour: 'var(--status-good)', icon: '●' },
      { key: 'late', label: 'Late', value: overview.totals.late, colour: 'var(--status-warning)', icon: '◐' },
      { key: 'absent', label: 'Absent', value: overview.totals.absent, colour: 'var(--status-critical)', icon: '○' },
    ];
    const total = segs.reduce((a, s) => a + s.value, 0) || 1;

    const stack = $('comp-stack');
    stack.innerHTML = '';
    segs.filter(s => s.value > 0).forEach(s => {
      const pct = (s.value / total) * 100;
      const el = document.createElement('div');
      el.className = `stack-seg${pct < 9 ? ' narrow' : ''}`;
      el.style.cssText = `flex:${s.value};background:${s.colour}`;
      el.innerHTML = `<b>${Math.round(pct)}%</b>`;
      bindTip(el, `<b>${esc(s.label)}</b>${s.value} sessions · ${pct.toFixed(1)}%`);
      stack.appendChild(el);
    });

    // Legend carries the icon + label, so status never rides on colour alone.
    $('comp-legend').innerHTML = segs.map(s => `
      <span class="legend-item">
        <span class="legend-swatch" style="background:${s.colour}"></span>
        ${esc(s.icon)} ${esc(s.label)} <b>${s.value}</b>
        <span style="color:var(--text-tertiary)">(${((s.value / total) * 100).toFixed(1)}%)</span>
      </span>`).join('');

    $('comp-table').innerHTML = `
      <table class="data">
        <thead><tr><th>Status</th><th>Sessions</th><th>Share</th></tr></thead>
        <tbody>${segs.map(s => `<tr><td>${esc(s.icon)} ${esc(s.label)}</td><td>${s.value}</td>
          <td>${((s.value / total) * 100).toFixed(1)}%</td></tr>`).join('')}
          <tr><td><strong>Total</strong></td><td><strong>${total}</strong></td><td>100%</td></tr>
        </tbody>
      </table>`;
    $('comp-sub').textContent = `${total} recorded sessions over the last 30 days`;
  }

  /* ---------------- bar chart: weekly rate ---------------- */
  function renderWeekly(series) {
    const box = $('weekly-bars');
    box.innerHTML = '';
    if (!series.length) { box.innerHTML = '<p class="empty">No sessions yet this week.</p>'; return; }

    series.forEach(d => {
      const col = document.createElement('div');
      col.className = 'bar-col';
      const height = d.total ? Math.max(2, d.rate) : 0;
      col.innerHTML = `
        <div class="bar-track">
          <div class="bar${d.total ? '' : ' bar-empty'}" style="height:${height}%">
            ${d.total ? `<span class="bar-val">${Math.round(d.rate)}%</span>` : ''}
          </div>
        </div>
        <span class="bar-x">${esc(d.day)}</span>`;
      if (d.total) {
        bindTip(col.querySelector('.bar'),
          `<b>${esc(d.day)} ${esc(d.date)}</b>${d.present} present · ${d.late} late · ${d.absent} absent<br>Rate ${d.rate}%`);
      }
      box.appendChild(col);
    });

    $('weekly-table').innerHTML = `
      <table class="data">
        <thead><tr><th>Day</th><th>Present</th><th>Late</th><th>Absent</th><th>Rate</th></tr></thead>
        <tbody>${series.map(d => `<tr><td>${esc(d.day)} ${esc(d.date)}</td><td>${d.present}</td>
          <td>${d.late}</td><td>${d.absent}</td><td>${d.rate}%</td></tr>`).join('')}</tbody>
      </table>`;
  }

  /* ---------------- heatmap: sequential magnitude ---------------- */
  function renderHeatmap(cells) {
    const box = $('heatmap');
    box.innerHTML = '';
    const byDate = new Map(cells.map(c => [c.date, c]));

    const today = new Date();
    const start = new Date(today);
    start.setDate(today.getDate() - 90);
    start.setDate(start.getDate() - start.getDay());   // align to a Sunday column

    for (let d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) {
      const key = d.toISOString().slice(0, 10);
      const cell = byDate.get(key);
      const el = document.createElement('div');
      el.className = 'heat-cell';
      const step = !cell ? 0
        : cell.intensity >= 1 ? 5
        : cell.intensity >= 0.75 ? 4
        : cell.intensity >= 0.5 ? 3
        : cell.intensity >= 0.25 ? 2 : 1;
      el.style.background = `var(--seq-${step})`;
      bindTip(el, cell
        ? `<b>${key}</b>${cell.present} of ${cell.total} classes attended`
        : `<b>${key}</b>No classes`);
      box.appendChild(el);
    }
  }

  /* ---------------- classes ---------------- */
  function renderClasses() {
    const box = $('classes');
    box.innerHTML = '';
    if (!state.classes.length) {
      box.innerHTML = '<p class="empty">You are not enrolled in any classes yet.</p>';
      return;
    }
    state.classes.forEach(c => {
      const card = document.createElement('div');
      card.className = 'class-card';
      const isLive = c.status === 'live';
      card.innerHTML = `
        <span class="class-swatch" style="background:${esc(c.color)}"></span>
        <div class="class-meta">
          <b>${esc(c.name)}</b>
          <span>${esc(c.subject)} · ${esc(c.schedule_days)} at ${esc(c.schedule_time)} · ${c.student_count} students</span>
        </div>
        ${isLive ? '<span class="pill-live">● LIVE</span>' : ''}
        <button class="btn ${isLive ? 'btn-primary' : 'btn-ghost'}" style="padding:8px 14px;font-size:13px">
          ${isLive ? 'Join' : (state.user.role === 'student' ? 'Details' : 'Start')}
        </button>`;

      card.querySelector('button').onclick = async () => {
        if (isLive) { location.href = `/app.html?session=${c.live_session_id}`; return; }
        if (state.user.role !== 'student') {
          try {
            const r = await API.sessions.start(c.id);
            location.href = `/app.html?session=${r.session.id}`;
          } catch (e) { toast(e.message, 'error'); }
        } else {
          toast(`${c.name} runs ${c.schedule_days} at ${c.schedule_time}.`);
        }
      };
      box.appendChild(card);
    });
  }

  async function renderRecordings() {
    const box = $('recordings');
    try {
      const data = await API.recordings.list();
      box.innerHTML = '';
      if (!data.recordings.length) {
        box.innerHTML = '<p class="empty">No recordings yet. Recordings appear here once a teacher records a class.</p>';
        return;
      }
      data.recordings.slice(0, 6).forEach(r => {
        const mins = Math.round(r.duration_seconds / 60);
        const mb = (r.size_bytes / 1048576).toFixed(1);
        const card = document.createElement('div');
        card.className = 'class-card';
        card.innerHTML = `
          <span class="class-swatch" style="background:var(--accent)"></span>
          <div class="class-meta">
            <b>${esc(r.filename)}</b>
            <span>${mins} min · ${mb} MB · ${new Date(r.created_at).toLocaleDateString()}</span>
          </div>
          <a class="btn btn-ghost" style="padding:8px 14px;font-size:13px;text-decoration:none"
             href="${esc(r.url)}" target="_blank" rel="noopener">Watch</a>`;
        box.appendChild(card);
      });
    } catch {
      box.innerHTML = '<p class="empty">Could not load recordings.</p>';
    }
  }

  /* ---------------- teacher: per-student grid ---------------- */
  async function renderRoster() {
    if (state.user.role === 'student' || !state.classes.length) return;
    $('roster-card').hidden = false;
    try {
      const data = await API.analytics.classBreakdown(state.classes[0].id);
      $('roster-sub').textContent =
        `${esc(state.classes[0].name)} · class average ${data.class_average}% · ${data.at_risk.length} students need attention`;

      $('roster-table').innerHTML = `
        <table class="data">
          <thead><tr>
            <th>Roll</th><th>Student</th><th>Attendance</th><th>Sessions</th>
            <th>Engagement</th><th>Flags</th><th>Face</th>
          </tr></thead>
          <tbody>${data.students.map(s => {
            const badge = s.attendance_rate >= 85 ? 'badge-good'
              : s.attendance_rate >= 70 ? 'badge-warn' : 'badge-bad';
            return `<tr>
              <td>${esc(s.roll_number || '—')}</td>
              <td>${esc(s.name)}</td>
              <td><span class="badge ${badge}">${s.attendance_rate}%</span></td>
              <td>${s.attended}/${s.sessions}</td>
              <td>${s.avg_engagement}%</td>
              <td>${s.violations > 0 ? `<span class="badge badge-warn">${s.violations}</span>` : '—'}</td>
              <td>${s.face_enrolled ? '✓' : '—'}</td>
            </tr>`;
          }).join('')}</tbody>
        </table>`;
    } catch (e) {
      $('roster-table').innerHTML = `<p class="empty">${esc(e.message)}</p>`;
    }
  }

  /* ---------------- boot ---------------- */
  async function boot() {
    if (!API.isAuthed()) { location.href = '/index.html'; return; }

    try { state.user = await API.auth.me(); }
    catch { location.href = '/index.html'; return; }

    applyTheme(state.user.theme || localStorage.getItem('sc-theme') || 'dark');
    $('btn-theme').onclick = () => {
      const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      API.auth.setTheme(next).catch(() => {});
    };
    $('btn-logout').onclick = () => API.logout();

    const hour = new Date().getHours();
    const part = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
    // "Dr. Ramesh Iyer" should greet Ramesh, not "Dr." — skip honorifics.
    const TITLES = new Set(['dr', 'dr.', 'prof', 'prof.', 'mr', 'mr.', 'mrs', 'mrs.', 'ms', 'ms.']);
    const firstName = state.user.full_name.split(' ')
      .find(w => !TITLES.has(w.toLowerCase())) || state.user.full_name;
    $('greeting').textContent = `${part}, ${firstName}`;
    $('who-name').textContent = state.user.full_name;
    $('who-sub').textContent = state.user.roll_number || (state.user.role === 'teacher' ? 'Teacher' : '');

    if (state.user.role !== 'student') {
      $('classes-title').textContent = 'Classes I teach';
      $('heat-title').textContent = 'Your teaching days over the last 90 days';
    }

    // Load everything in parallel — the dashboard should feel instant.
    const [overview, weekly, heat, classes, live] = await Promise.allSettled([
      API.analytics.overview(),
      API.analytics.weekly(),
      API.analytics.heatmap(),
      API.classes.list(),
      API.sessions.live(),
    ]);

    if (overview.status === 'fulfilled') {
      renderStats(overview.value);
      renderComposition(overview.value);
    }
    if (weekly.status === 'fulfilled') renderWeekly(weekly.value.series);
    if (heat.status === 'fulfilled') renderHeatmap(heat.value.cells);

    if (classes.status === 'fulfilled') {
      state.classes = classes.value.classrooms;
      $('classes-sub').textContent = `${state.classes.length} class${state.classes.length === 1 ? '' : 'es'}`;
      renderClasses();
      renderRoster();
    }

    if (live.status === 'fulfilled' && live.value.sessions.length) {
      state.live = live.value.sessions;
      const s = state.live[0];
      $('subgreeting').innerHTML =
        `<strong style="color:var(--accent)">${esc(s.class_name)}</strong> is live right now — ${s.participants} connected.`;
      $('btn-join').hidden = false;
      $('btn-join').textContent = `Join ${s.class_name} →`;
      $('btn-join').onclick = () => { location.href = `/app.html?session=${s.session_id}`; };
    } else {
      $('subgreeting').textContent = state.user.role === 'student'
        ? 'No class is live right now. Join with a Meeting ID below when your teacher shares one.'
        : 'No live session. Host a meeting or start one from a class below.';
    }

    wireMeetingButtons();
    renderRecordings();

    // table-view toggles (the relief rule for sub-3:1 status colours in light mode)
    const wire = (btn, panel) => {
      $(btn).onclick = () => {
        const el = $(panel);
        el.hidden = !el.hidden;
        $(btn).textContent = el.hidden ? 'Show as table' : 'Hide table';
      };
    };
    wire('comp-table-toggle', 'comp-table');
    wire('weekly-table-toggle', 'weekly-table');
  }

  /* ---------------- host / join meeting (Zoom-style) ---------------- */
  let createdMeeting = null;   // {sessionId, meetingId, passcode, roomCode}

  function wireMeetingButtons() {
    const isTeacher = state.user.role !== 'student';

    /* --- Host a meeting (teacher) --- */
    if (isTeacher && state.classes.length) {
      $('btn-host').hidden = false;
      $('btn-host').onclick = openHostModal;
    }

    /* --- Join with meeting ID (everyone) --- */
    $('btn-join-code').hidden = false;
    $('btn-join-code').onclick = () => {
      $('joincode-modal').hidden = false;
      $('jc-msg').textContent = '';
      setTimeout(() => $('jc-id').focus(), 60);
    };
    $('jc-cancel').onclick = () => { $('joincode-modal').hidden = true; };
    $('jc-go').onclick = joinByMeetingId;
    $('jc-pass').addEventListener('keydown', e => { if (e.key === 'Enter') joinByMeetingId(); });
  }

  function openHostModal() {
    const sel = $('host-class');
    sel.innerHTML = '';
    state.classes.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = `${c.name} — ${c.subject || 'Class'}${c.live_session ? ' (live!)' : ''}`;
      sel.appendChild(opt);
    });
    $('host-modal').hidden = false;
  }
  $('host-cancel') && ($('host-cancel').onclick = () => { $('host-modal').hidden = true; });
  $('host-create') && ($('host-create').onclick = createMeeting);

  async function createMeeting() {
    const classId = $('host-class').value;
    if (!classId) return;
    $('host-create').disabled = true;
    $('host-create').textContent = 'Starting…';
    try {
      const res = await API.sessions.start(classId, $('host-title').value.trim() || null);
      const s = res.session;
      createdMeeting = {
        sessionId: s.id,
        meetingId: res.meeting?.id || s.meeting_id || '—',
        passcode: res.meeting?.passcode || s.meeting_passcode || '—',
        roomCode: res.meeting?.room_code || '—',
      };
      $('host-modal').hidden = true;
      $('share-id').textContent = createdMeeting.meetingId;
      $('share-pass').textContent = createdMeeting.passcode;
      $('share-room').textContent = createdMeeting.roomCode;
      $('share-modal').hidden = false;
      // A live class now exists — surface the join button too.
      $('btn-join').hidden = false;
      $('btn-join').textContent = `Join ${s.title || 'class'} →`;
      $('btn-join').onclick = () => { location.href = `/app.html?session=${s.id}`; };
    } catch (e) {
      alert('Could not start the meeting: ' + e.message);
    } finally {
      $('host-create').disabled = false;
      $('host-create').textContent = 'Start meeting';
    }
  }

  const copyText = async (text, btn) => {
    try { await navigator.clipboard.writeText(text); } catch {
      const ta = document.createElement('textarea');
      ta.value = text; document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); ta.remove();
    }
    const old = btn.textContent;
    btn.textContent = 'Copied ✓';
    setTimeout(() => { btn.textContent = old; }, 1400);
  };
  $('copy-id') && ($('copy-id').onclick = () => copyText($('share-id').textContent, $('copy-id')));
  $('copy-pass') && ($('copy-pass').onclick = () => copyText($('share-pass').textContent, $('copy-pass')));
  $('copy-room') && ($('copy-room').onclick = () => copyText($('share-room').textContent, $('copy-room')));
  $('share-close') && ($('share-close').onclick = () => { $('share-modal').hidden = true; });
  $('share-enter') && ($('share-enter').onclick = () => {
    location.href = `/app.html?session=${createdMeeting.sessionId}`;
  });

  async function joinByMeetingId() {
    const id = $('jc-id').value.trim().replace(/\s+/g, '');
    const pass = $('jc-pass').value.trim().toUpperCase();
    if (!id || !pass) { $('jc-msg').textContent = 'Enter both the meeting ID and passcode.'; return; }
    $('jc-go').disabled = true;
    try {
      const res = await API.request('/api/meetings/validate', {
        method: 'POST', body: { meeting_id: id, meeting_passcode: pass },
      });
      if (!res.valid) { $('jc-msg').textContent = res.reason || 'Meeting not found.'; return; }
      $('joincode-modal').hidden = true;
      // carry the passcode through so the /api/join gate can verify it again
      sessionStorage.setItem('sc-join-code', id);
      sessionStorage.setItem('sc-join-pass', pass);
      location.href = `/app.html?session=${res.session.id}`;
    } catch (e) {
      $('jc-msg').textContent = 'Could not reach the server. Try again.';
    } finally {
      $('jc-go').disabled = false;
    }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
