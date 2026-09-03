/* ================================================================
   SunnyClass AI — WebRTC mesh
   Peer-to-peer media (lowest possible latency, no media server) with
   adaptive encoding driven by the server's quality ladder.
   ================================================================ */
const MeshRTC = (() => {
  'use strict';

  const peers = new Map();          // peerId -> { pc, stream, info, stats }
  let socket = null;
  let localStream = null;
  let iceServers = [{ urls: 'stun:stun.l.google.com:19302' }];
  let quality = null;
  let selfPeerId = null;
  const MeshTrack = { userDisabled: false };   // camera mute is user-intent; quality steps don't touch it
  const handlers = { stream: [], leave: [], state: [], quality: [], stats: [] };

  const on = (evt, fn) => { (handlers[evt] ||= []).push(fn); return MeshRTC; };
  const fire = (evt, ...args) => (handlers[evt] || []).forEach(fn => {
    try { fn(...args); } catch (e) { console.error(e); }
  });

  /* ---------------- local media ---------------- */
  const PROFILES = {
    high:    { width: 1280, height: 720, frameRate: 30 },
    medium:  { width: 854,  height: 480, frameRate: 20 },
    low:     { width: 640,  height: 360, frameRate: 15 },
    minimal: { width: 320,  height: 240, frameRate: 10 },
    audio:   null,
  };

  function guessProfile() {
    const c = navigator.connection;
    const t = c?.effectiveType;
    if (c?.saveData) return 'minimal';
    if (t === '2g' || t === 'slow-2g') return 'minimal';
    if (t === '3g') return 'low';
    if (window.innerWidth < 700) return 'medium';   // phones: don't waste uplink
    return 'high';
  }

  async function initLocalMedia(profileName = null) {
    const name = profileName || guessProfile();
    const p = PROFILES[name] || PROFILES.medium;
    const constraints = {
      audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true,
        channelCount: 1, sampleRate: 24000,      // mono/narrow = far less uplink
      },
      video: p ? {
        width: { ideal: p.width }, height: { ideal: p.height },
        frameRate: { ideal: p.frameRate, max: p.frameRate },
        facingMode: 'user',
      } : false,
    };

    try {
      localStream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      if (err.name === 'OverconstrainedError') {
        localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
      } else { throw err; }
    }
    return localStream;
  }

  /**
   * Camera enforcement. The video track is kept live for the whole class; if the
   * OS or another app steals the camera we transparently re-acquire it.
   */
  function enforceCamera(onLost) {
    const track = localStream?.getVideoTracks()[0];
    if (!track) return;
    track.addEventListener('ended', async () => {
      onLost?.('ended');
      try {
        const fresh = await navigator.mediaDevices.getUserMedia({ video: true });
        const newTrack = fresh.getVideoTracks()[0];
        localStream.removeTrack(track);
        localStream.addTrack(newTrack);
        for (const { pc } of peers.values()) {
          const sender = pc.getSenders().find(s => s.track?.kind === 'video');
          if (sender) await sender.replaceTrack(newTrack);
        }
        enforceCamera(onLost);
      } catch { onLost?.('unrecoverable'); }
    });
    track.addEventListener('mute', () => onLost?.('muted'));
  }

  /* ---------------- peer connections ---------------- */
  function createPeer(peerId, info, initiate) {
    if (peers.has(peerId)) return peers.get(peerId);

    const pc = new RTCPeerConnection({
      iceServers,
      iceCandidatePoolSize: 4,          // pre-gather -> faster first frame
      bundlePolicy: 'max-bundle',       // one transport = fewer round trips
      rtcpMuxPolicy: 'require',
    });

    const entry = { pc, stream: null, info, initiate };
    peers.set(peerId, entry);

    localStream?.getTracks().forEach(track => pc.addTrack(track, localStream));
    applyEncodingLimits(pc);

    pc.onicecandidate = (e) => {
      if (e.candidate) socket?.send({ type: 'ice', to: peerId, payload: e.candidate });
    };

    pc.ontrack = (e) => {
      entry.stream = e.streams[0];
      fire('stream', peerId, e.streams[0], info);
    };

    pc.onconnectionstatechange = () => {
      fire('state', peerId, pc.connectionState);
      if (pc.connectionState === 'failed') pc.restartIce?.();
    };

    if (initiate) negotiate(peerId);
    return entry;
  }

  async function negotiate(peerId) {
    const entry = peers.get(peerId);
    if (!entry) return;
    const offer = await entry.pc.createOffer();
    await entry.pc.setLocalDescription(offer);
    socket?.send({ type: 'offer', to: peerId, payload: entry.pc.localDescription });
  }

  /** Clamp per-peer bitrate. In a mesh every extra peer multiplies uplink, so
   *  the server's ladder already accounts for room size. */
  function applyEncodingLimits(pc) {
    if (!quality) return;
    pc.getSenders().forEach(sender => {
      if (sender.track?.kind !== 'video') return;
      const params = sender.getParameters();
      params.encodings = params.encodings?.length ? params.encodings : [{}];
      params.encodings[0].maxBitrate = quality.maxBitrate || 600000;
      params.encodings[0].maxFramerate = quality.frameRate || 20;
      params.degradationPreference = 'maintain-framerate';
      sender.setParameters(params).catch(() => {});
    });
  }

  async function applyQuality(q) {
    quality = q;
    fire('quality', q);
    const track = localStream?.getVideoTracks()[0];
    if (track && q.width) {
      try {
        await track.applyConstraints({
          width: { ideal: q.width }, height: { ideal: q.height },
          frameRate: { ideal: q.frameRate, max: q.frameRate },
        });
      } catch { /* device may refuse; bitrate cap below still applies */ }
    }
    if (q.profile === 'audio' && track) track.enabled = false;
    else if (track && !MeshTrack.userDisabled) track.enabled = true;   // step back up re-enables
    for (const { pc } of peers.values()) applyEncodingLimits(pc);
  }

  /* ---------------- connection stats (shown as a network pill) ---------------- */
  let statsTimer = null;
  function startStats(intervalMs = 4000) {
    clearInterval(statsTimer);
    statsTimer = setInterval(async () => {
      let rttSum = 0, n = 0, lossPct = 0, kbps = 0;
      for (const { pc } of peers.values()) {
        try {
          const report = await pc.getStats();
          report.forEach(s => {
            if (s.type === 'candidate-pair' && s.state === 'succeeded' && s.currentRoundTripTime != null) {
              rttSum += s.currentRoundTripTime * 1000; n += 1;
            }
            if (s.type === 'inbound-rtp' && s.kind === 'video') {
              if (s.packetsLost && s.packetsReceived) {
                lossPct = Math.max(lossPct, (s.packetsLost / (s.packetsLost + s.packetsReceived)) * 100);
              }
              if (s.bytesReceived) kbps += Math.round(s.bytesReceived / 1024);
            }
          });
        } catch { /* ignore */ }
      }
      const stats = {
        peers: peers.size,
        rttMs: n ? Math.round(rttSum / n) : (socket?.rtt || 0),
        lossPct: Math.round(lossPct * 10) / 10,
        profile: quality?.profile || 'auto',
        label: quality?.label || '',
      };
      fire('stats', stats);

      // Self-healing: sustained loss on a good profile -> ask the server to step down.
      if (stats.lossPct > 8 && quality && quality.profile !== 'audio') {
        socket?.send({ type: 'quality', network: '3g' });
      }
    }, intervalMs);
  }

  /* ---------------- public lifecycle ---------------- */
  async function start({ sessionId, sock, ice, initialQuality, profile }) {
    socket = sock;
    if (ice?.length) iceServers = ice;
    quality = initialQuality || null;
    if (!localStream) await initLocalMedia(profile);

    sock.on('welcome', async (msg) => {
      selfPeerId = msg.peerId;
      if (msg.iceServers?.length) iceServers = msg.iceServers;
      if (msg.quality) await applyQuality(msg.quality);
      (msg.participants || []).forEach(p => {
        createPeer(p.peerId, p, (msg.shouldInitiate || []).includes(p.peerId));
      });
      startStats();
    });

    sock.on('peer-joined', (msg) => {
      // The newcomer initiates; we just prepare to receive.
      createPeer(msg.peer.peerId, msg.peer, false);
    });

    sock.on('offer', async (msg) => {
      const entry = peers.get(msg.from) || createPeer(msg.from, msg.peer, false);
      await entry.pc.setRemoteDescription(new RTCSessionDescription(msg.payload));
      const answer = await entry.pc.createAnswer();
      await entry.pc.setLocalDescription(answer);
      socket.send({ type: 'answer', to: msg.from, payload: entry.pc.localDescription });
    });

    sock.on('answer', async (msg) => {
      const entry = peers.get(msg.from);
      if (entry && entry.pc.signalingState !== 'stable') {
        await entry.pc.setRemoteDescription(new RTCSessionDescription(msg.payload));
      }
    });

    sock.on('ice', async (msg) => {
      const entry = peers.get(msg.from);
      if (!entry) return;
      try { await entry.pc.addIceCandidate(new RTCIceCandidate(msg.payload)); }
      catch { /* candidate arrived before the description; browser buffers most */ }
    });

    sock.on('peer-left', (msg) => {
      const entry = peers.get(msg.peerId);
      if (entry) { entry.pc.close(); peers.delete(msg.peerId); }
      fire('leave', msg.peerId);
    });

    sock.on('quality-update', (msg) => applyQuality(msg.quality));

    return localStream;
  }

  async function shareScreen() {
    const display = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: 15 }, audio: false,
    });
    const screenTrack = display.getVideoTracks()[0];
    const camTrack = localStream.getVideoTracks()[0];
    for (const { pc } of peers.values()) {
      const sender = pc.getSenders().find(s => s.track?.kind === 'video');
      await sender?.replaceTrack(screenTrack);
    }
    socket?.send({ type: 'screen-share', on: true });
    screenTrack.onended = async () => {
      for (const { pc } of peers.values()) {
        const sender = pc.getSenders().find(s => s.track?.kind === 'video');
        await sender?.replaceTrack(camTrack);
      }
      socket?.send({ type: 'screen-share', on: false });
    };
    return display;
  }

  function toggleMic(force) {
    const track = localStream?.getAudioTracks()[0];
    if (!track) return false;
    track.enabled = force !== undefined ? force : !track.enabled;
    socket?.send({ type: 'media-state', micOn: track.enabled, cameraOn: true });
    return track.enabled;
  }

  function stop() {
    clearInterval(statsTimer);
    peers.forEach(({ pc }) => pc.close());
    peers.clear();
    localStream?.getTracks().forEach(t => t.stop());
    localStream = null;
  }

  return {
    start, stop, on, initLocalMedia, enforceCamera, shareScreen, toggleMic,
    applyQuality, startStats,
    get localStream() { return localStream; },
    get peers() { return peers; },
    get quality() { return quality; },
    get selfPeerId() { return selfPeerId; },
  };
})();

window.MeshRTC = MeshRTC;
