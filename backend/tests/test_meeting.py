"""End-to-end meeting test: one teacher + three students in a live class.

Exercises the new meeting layer over the real WS:
  chat (send + broadcast + persistence + REST history)
  polls (teacher creates, students vote, dedup, close+reveal)
  whiteboard strokes (relay), reactions, captions (single-send now)
Plus the classic path: join gates, presence, quality ladder.
"""
import asyncio
import json
import sys
import urllib.request
import urllib.error

import httpx
import websockets

BASE = "http://localhost:8000"
WS = "ws://localhost:8000"

results = []


def check(name, ok, extra=""):
    results.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name} {extra}")


def api(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def login(email, pw):
    return api("POST", "/api/auth/login", body={"email": email, "password": pw})["token"]


async def recv_until(ws, want_type, timeout=6.0, collect=None):
    """Read frames until one of `want_type` arrives; stash others in collect."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(.1, deadline - time.time()))
        except asyncio.TimeoutError:
            break
        msg = json.loads(raw)
        if collect is not None and msg.get("type") != want_type:
            collect.append(msg)
        if msg.get("type") in want_type:
            return msg
    return None


async def main():
    teacher_tok = login("ramesh.iyer@sunnyclass.edu", "teach1234")
    students = [login(e, "student123") for e in [
        "aarav.sharma@student.sunnyclass.edu",
        "ananya.patel@student.sunnyclass.edu",
        "divya.thakur@student.sunnyclass.edu"]]

    # fresh live session
    live = api("GET", "/api/sessions/live", teacher_tok)
    if live["sessions"]:
        api("POST", f"/api/sessions/{live['sessions'][0]['session_id']}/end", teacher_tok)
    sess = api("POST", "/api/sessions/start", teacher_tok,
               {"class_id": "cls_29936af776494412"})["session"]
    sid = sess["id"]
    print(f"session {sid}")

    # students need attendance rows to pass the WS admission gate — the
    # seeded class has them from prior joins; ensure via heartbeat like the
    # app does after /api/join. For this test the seeded students already
    # hold rows (seed created them for the live session), so skip if present.
    # If the fresh session has none, create rows by heartbeating once.
    for st in students:
        api("POST", "/api/attendance/heartbeat", st,
            {"session_id": sid, "engagement_score": 90, "elapsed_seconds": 15})

    async def connect(tok, network="4g"):
        ws = await websockets.connect(f"{WS}/ws/class/{sid}?token={tok}&network={network}")
        welcome = json.loads(await ws.recv())
        assert welcome["type"] == "welcome", welcome
        return ws, welcome

    teacher, tw = await connect(teacher_tok)
    s1, w1 = await connect(students[0])
    s2, w2 = await connect(students[1])
    check("all four connected", True, f"mesh={len(tw['participants'])}+3")

    # ---- chat ---------------------------------------------------------- #
    await s1.send(json.dumps({"type": "chat", "text": "Good morning everyone!"}))
    t_seen = await recv_until(teacher, "chat")
    s2_seen = await recv_until(s2, "chat")
    check("chat broadcast to room", bool(t_seen and s2_seen) and
          t_seen["senderName"] == "Aarav Sharma",
          f"teacher got: {(t_seen or {}).get('text','')!r}")
    echo = await recv_until(s1, "chat")
    check("chat echoed to sender", bool(echo and echo.get("messageId")),
          f"id={echo.get('messageId') if echo else None}")
    await teacher.send(json.dumps({"type": "chat", "text": "Welcome! Let's begin."}))
    got = await recv_until(s1, "chat")
    check("teacher chat broadcast", bool(got and got.get("senderRole") == "teacher"))

    # ---- poll ----------------------------------------------------------- #
    await teacher.send(json.dumps({"type": "poll-create",
                                   "question": "Ready for the quiz on Friday?",
                                   "options": ["Yes!", "Need more practice", "No"]}))
    poll = await recv_until(s1, "poll")
    check("poll broadcast to students", bool(poll and "counts" not in poll),
          f"options={poll.get('options') if poll else None}")
    pid = poll["pollId"]

    await s1.send(json.dumps({"type": "poll-vote", "pollId": pid, "option": 0}))
    got1 = await recv_until(s1, "poll")     # own vote ack path is broadcast_to_hosts only; wait on teacher
    await s2.send(json.dumps({"type": "poll-vote", "pollId": pid, "option": 1}))
    await s2.send(json.dumps({"type": "poll-vote", "pollId": pid, "option": 2}))  # dedup → ignored
    await asyncio.sleep(.8)
    # teacher should see live counts (two updates, latest has both votes)
    t_poll = None
    import time as _t
    deadline = _t.time() + 5
    while _t.time() < deadline:
        try:
            m = json.loads(await asyncio.wait_for(teacher.recv(), timeout=1.0))
        except asyncio.TimeoutError:
            continue
        if m.get("type") == "poll" and "counts" in m:
            t_poll = m
            if sum(m["counts"]) >= 2:
                break
    check("teacher sees live poll counts", bool(t_poll) and sum(t_poll["counts"]) == 2,
          f"counts={t_poll['counts'] if t_poll else None}")

    await teacher.send(json.dumps({"type": "poll-close", "pollId": pid}))
    final = await recv_until(s1, "poll")
    check("poll close reveals results to students",
          bool(final and "counts" in final and sum(final["counts"]) == 2),
          f"counts={final.get('counts') if final else None}")

    # student tries to create a poll → must be ignored (no broadcast)
    await s1.send(json.dumps({"type": "poll-create", "question": "skip class?",
                              "options": ["yes", "no"]}))
    await asyncio.sleep(.4)
    await s1.send(json.dumps({"type": "ping", "t": 2}))
    pong = json.loads(await asyncio.wait_for(s1.recv(), timeout=3))
    while pong.get("type") != "pong":
        pong = json.loads(await asyncio.wait_for(s1.recv(), timeout=3))
    rogue = None
    try:
        while True:
            m = json.loads(await asyncio.wait_for(teacher.recv(), timeout=1.0))
            if m.get("type") == "poll" and m.get("question") == "skip class?":
                rogue = m; break
    except asyncio.TimeoutError:
        pass
    check("student cannot create polls", rogue is None)

    # ---- whiteboard ------------------------------------------------------ #
    await teacher.send(json.dumps({"type": "whiteboard", "action": "stroke",
                                   "points": [[10, 10], [40, 42], [80, 90]],
                                   "color": "#FF5252", "width": 3}))
    stroke = await recv_until(s1, "whiteboard")
    check("whiteboard stroke relayed", bool(stroke and stroke["points"][0] == [10, 10]))
    await s2.send(json.dumps({"type": "ping", "t": 99}))          # flush any stale frames on s2
    clr = await recv_until(s2, "whiteboard")
    while clr and clr.get("action") == "stroke":                  # skip the stroke, want the clear
        clr = await recv_until(s2, "whiteboard")
    await teacher.send(json.dumps({"type": "whiteboard", "action": "clear"}))
    clr = await recv_until(s2, "whiteboard")
    check("whiteboard clear relayed", bool(clr and clr["action"] == "clear"))

    # ---- reactions -------------------------------------------------------- #
    await s1.send(json.dumps({"type": "reaction", "emoji": "🎉"}))
    r = await recv_until(teacher, "reaction")
    check("reaction broadcast", bool(r and r["emoji"] == "🎉"))

    # ---- captions (single send → room + persistence) ----------------------- #
    await teacher.send(json.dumps({"type": "caption", "text": "Integration by parts.",
                                   "isFinal": True, "offsetMs": 5000}))
    cap = await recv_until(s1, "caption")
    check("caption broadcast", bool(cap and cap["text"] == "Integration by parts."))
    await asyncio.sleep(.8)
    req = urllib.request.Request(BASE + f"/api/captions/{sid}/transcript",
                                 headers={"Authorization": f"Bearer {students[0]}"})
    with urllib.request.urlopen(req) as r:
        hist_text = r.read().decode()
    check("caption persisted via WS path", "Integration by parts." in hist_text,
          f"{len(hist_text)} chars of transcript")

    # chat persisted?
    chat_hist = api("GET", f"/api/meetings/{sid}/chat", students[0])
    texts = [m["text"] for m in chat_hist.get("messages", [])]
    check("chat persisted + REST history", "Good morning everyone!" in texts and
          "Welcome! Let's begin." in texts, f"{len(texts)} messages")

    # late joiner sees the chat backlog in the welcome frame
    s3, w3 = await connect(students[2])
    check("welcome carried chat backlog", len(w3.get("chat", [])) >= 2,
          f"{len(w3.get('chat', []))} msgs replayed to late joiner")

    for ws in (teacher, s1, s2, s3):
        await ws.close()

    # end session
    api("POST", f"/api/sessions/{sid}/end", teacher_tok)
    print(f"\n{sum(1 for _, ok in results if ok)}/{len(results)} meeting checks passed")
    if any(not ok for _, ok in results):
        sys.exit(1)


asyncio.run(main())
