/* ================================================================
   SunnyClass AI — API client
   One place that knows how to talk to the FastAPI backend: auth token
   storage, JSON fetch with error surfacing, and the class WebSocket.
   ================================================================ */
const API = (() => {
  'use strict';

  const TOKEN_KEY = 'sc-token';
  const USER_KEY = 'sc-user';

  // Same-origin by default (the backend serves this frontend). Override with
  // window.SUNNYCLASS_API_BASE = 'https://api.example.com' when split-deployed.
  const base = () => (window.SUNNYCLASS_API_BASE || '').replace(/\/$/, '');

  /* ---------------- token & session ---------------- */
  const getToken = () => localStorage.getItem(TOKEN_KEY);
  const setToken = (t) => t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY);

  function getUser() {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); }
    catch { return null; }
  }
  function setUser(u) {
    if (u) localStorage.setItem(USER_KEY, JSON.stringify(u));
    else localStorage.removeItem(USER_KEY);
  }
  const isAuthed = () => Boolean(getToken());

  function logout(redirect = '/index.html') {
    setToken(null); setUser(null);
    if (redirect) window.location.href = redirect;
  }

  /* ---------------- core request ---------------- */
  class ApiError extends Error {
    constructor(message, status, body) {
      super(message); this.name = 'ApiError'; this.status = status; this.body = body;
    }
  }

  async function request(path, { method = 'GET', body, headers = {}, raw = false, timeout = 25000 } = {}) {
    const token = getToken();
    const h = { ...headers };
    if (token) h['Authorization'] = `Bearer ${token}`;

    let payload = body;
    if (body && !(body instanceof FormData)) {
      h['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    let res;
    try {
      res = await fetch(`${base()}${path}`, { method, headers: h, body: payload, signal: controller.signal });
    } catch (err) {
      clearTimeout(timer);
      if (err.name === 'AbortError') throw new ApiError('Request timed out', 0, null);
      throw new ApiError('Cannot reach the SunnyClass server', 0, null);
    }
    clearTimeout(timer);

    if (res.status === 401) {
      setToken(null); setUser(null);
      if (!/index\.html|\/$/.test(location.pathname)) location.href = '/index.html?expired=1';
      throw new ApiError('Session expired — please sign in again', 401, null);
    }

    if (raw) return res;

    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }

    if (!res.ok) {
      const detail = data?.detail ?? data?.error ?? res.statusText;
      throw new ApiError(typeof detail === 'string' ? detail : JSON.stringify(detail), res.status, data);
    }
    return data;
  }

  const get = (p, o) => request(p, { ...o, method: 'GET' });
  const post = (p, body, o) => request(p, { ...o, method: 'POST', body });
  const patch = (p, body, o) => request(p, { ...o, method: 'PATCH', body });
  const del = (p, o) => request(p, { ...o, method: 'DELETE' });

  /* ---------------- device fingerprint ---------------- */
  function deviceInfo() {
    const w = window.innerWidth;
    const kind = w < 600 ? 'phone' : w < 1024 ? 'tablet' : 'desktop';
    const conn = navigator.connection || {};
    return {
      kind,
      label: `${kind} · ${w}×${window.innerHeight}`,
      user_agent: navigator.userAgent.slice(0, 380),
      screen: `${w}x${window.innerHeight}`,
      network: conn.effectiveType || (navigator.onLine ? '4g' : 'offline'),
    };
  }

  /* ---------------- endpoints ---------------- */
  const auth = {
    async register(payload) {
      const r = await post('/api/auth/register', payload);
      setToken(r.token); setUser(r.user); return r;
    },
    async login(email, password) {
      const r = await post('/api/auth/login', { email, password });
      setToken(r.token); setUser(r.user);
      try { await post('/api/auth/devices', deviceInfo()); } catch { /* non-fatal */ }
      return r;
    },
    me: () => get('/api/auth/me'),
    setTheme: (theme) => patch('/api/auth/theme', { theme }),
    devices: () => get('/api/auth/devices'),
    logout,
  };

  const classes = {
    list: (params = '') => get(`/api/classes${params}`),
    get: (id) => get(`/api/classes/${id}`),
    create: (payload) => post('/api/classes', payload),
    roster: (id) => get(`/api/classes/${id}/roster`),
    enroll: (id, payload) => post(`/api/classes/${id}/enroll`, payload),
  };

  const sessions = {
    live: () => get('/api/sessions/live'),
    get: (id) => get(`/api/sessions/${id}`),
    start: (classId, title) => post('/api/sessions/start', { class_id: classId, title }),
    end: (id) => post(`/api/sessions/${id}/end`),
  };

  const face = {
    status: () => get('/api/face/status'),
    enroll: (descriptors, quality = 1.0) =>
      post('/api/face/enroll', { descriptors, quality }),
    verify: (descriptor, sessionId) =>
      post('/api/face/verify', { descriptor, session_id: sessionId || null }),
    reset: () => del('/api/face/enroll'),
  };

  const join = (payload) => post('/api/join', { ...payload, device: deviceInfo() });

  const attendance = {
    list: (qs = '') => get(`/api/attendance${qs}`),
    heartbeat: (payload) => post('/api/attendance/heartbeat', payload),
    violation: (payload) => post('/api/attendance/violation', payload),
    mark: (payload) => post('/api/attendance/mark', payload),
  };

  const analytics = {
    overview: (qs = '') => get(`/api/analytics/overview${qs}`),
    weekly: (qs = '') => get(`/api/analytics/weekly${qs}`),
    heatmap: (qs = '') => get(`/api/analytics/heatmap${qs}`),
    classBreakdown: (id) => get(`/api/analytics/class/${id}`),
    live: (sessionId) => get(`/api/analytics/live/${sessionId}`),
  };

  const captions = {
    push: (payload) => post('/api/captions', payload),
    list: (sessionId) => get(`/api/captions/${sessionId}`),
    transcript: (sessionId) => request(`/api/captions/${sessionId}/transcript`, { raw: true }).then(r => r.text()),
    summarize: (sessionId) => post(`/api/captions/${sessionId}/summarize`, {}, { timeout: 60000 }),
  };

  const recordings = {
    list: (qs = '') => get(`/api/recordings${qs}`),
    async upload(blob, sessionId, durationSeconds) {
      const fd = new FormData();
      fd.append('file', blob, `class-${sessionId}-${Date.now()}.webm`);
      fd.append('session_id', sessionId);
      fd.append('duration_seconds', String(Math.round(durationSeconds || 0)));
      return request('/api/recordings/upload', { method: 'POST', body: fd, timeout: 300000 });
    },
  };

  const sunny = {
    ask: (message, opts = {}) => post('/api/chat', {
      message,
      session_id: opts.sessionId || null,
      conversation_id: opts.conversationId || null,
      context: opts.context || null,
    }, { timeout: 45000 }),
    history: (conversationId) =>
      get(`/api/chat/history${conversationId ? `?conversation_id=${conversationId}` : ''}`),
    status: () => get('/api/chat/status'),
  };

  const config = () => get('/api/config');
  const health = () => get('/api/health');

  /* ---------------- class WebSocket ---------------- */
  /**
   * Auto-reconnecting socket for signaling + live class events.
   * Usage:  const sock = API.socket(sessionId); sock.on('caption', fn); sock.connect();
   */
  function socket(sessionId, { network } = {}) {
    const listeners = new Map();
    let ws = null, closed = false, retries = 0, pingTimer = null;
    let lastPongAt = 0, rtt = 0;

    const emit = (type, data) => {
      (listeners.get(type) || []).forEach(fn => { try { fn(data); } catch (e) { console.error(e); } });
      (listeners.get('*') || []).forEach(fn => { try { fn(type, data); } catch (e) { console.error(e); } });
    };

    function url() {
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
      const host = base() ? base().replace(/^https?:\/\//, '') : location.host;
      const net = network || (navigator.connection?.effectiveType) || '4g';
      const dev = deviceInfo().kind;
      return `${scheme}://${host}/ws/class/${sessionId}?token=${encodeURIComponent(getToken() || '')}` +
             `&network=${encodeURIComponent(net)}&device=${encodeURIComponent(dev)}`;
    }

    function connect() {
      closed = false;
      ws = new WebSocket(url());

      ws.onopen = () => {
        retries = 0;
        emit('open');
        clearInterval(pingTimer);
        pingTimer = setInterval(() => {
          if (ws?.readyState === WebSocket.OPEN) send({ type: 'ping', t: Date.now() });
        }, 20000);
      };

      ws.onmessage = (evt) => {
        let msg; try { msg = JSON.parse(evt.data); } catch { return; }
        if (msg.type === 'pong') {
          lastPongAt = Date.now(); rtt = lastPongAt - (msg.t || lastPongAt);
          emit('rtt', rtt); return;
        }
        emit(msg.type, msg);
      };

      ws.onclose = () => {
        clearInterval(pingTimer);
        emit('close');
        if (closed) return;
        retries += 1;
        const delay = Math.min(15000, 600 * Math.pow(1.8, retries));  // backoff
        emit('reconnecting', { attempt: retries, delay });
        setTimeout(() => { if (!closed) connect(); }, delay);
      };

      ws.onerror = () => emit('error');
      return api;
    }

    function send(obj) {
      if (ws?.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); return true; }
      return false;
    }

    const api = {
      connect,
      send,
      on(type, fn) {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push(fn);
        return api;
      },
      off(type, fn) {
        const arr = listeners.get(type) || [];
        const i = arr.indexOf(fn); if (i >= 0) arr.splice(i, 1);
        return api;
      },
      close() { closed = true; clearInterval(pingTimer); ws?.close(); },
      get rtt() { return rtt; },
      get state() { return ws ? ws.readyState : WebSocket.CLOSED; },
    };
    return api;
  }

  return {
    ApiError, request, get, post, patch, del,
    getToken, setToken, getUser, setUser, isAuthed, logout, deviceInfo,
    auth, classes, sessions, face, join, attendance, analytics,
    captions, recordings, sunny, config, health, socket,
  };
})();

window.API = API;
