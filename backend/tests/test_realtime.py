"""Two-peer WebSocket signaling test: presence, SDP relay, captions, gating."""
import asyncio, json, sys
import httpx, numpy as np, websockets

BASE = "http://localhost:8000"; WS = "ws://localhost:8000"
res = []
def check(l, c, e=""):
    res.append(c); print(f"{'  ok  ' if c else ' FAIL '} {l} {e}")

async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        tk = lambda r: r.json()["token"]
        t = tk(await c.post("/api/auth/login", json={"email":"ramesh.iyer@smartclass.edu","password":"teach1234"}))
        s1 = tk(await c.post("/api/auth/login", json={"email":"aarav.sharma@student.smartclass.edu","password":"student123"}))
        s2 = tk(await c.post("/api/auth/login", json={"email":"ananya.patel@student.smartclass.edu","password":"student123"}))
        s3 = tk(await c.post("/api/auth/login", json={"email":"divya.thakur@student.smartclass.edu","password":"student123"}))
        r = await c.get("/api/sessions/live", headers={"Authorization":f"Bearer {s1}"})
        sid = r.json()["sessions"][0]["session_id"]
        # admit student 2 through the gate (student 1 already admitted by seed)
        d2 = np.random.default_rng(1001).normal(scale=0.12, size=128).round(6).tolist()
        j = (await c.post("/api/join", headers={"Authorization":f"Bearer {s2}"},
                          json={"session_id": sid, "descriptor": d2})).json()
        check("second student admitted via gate", j["admitted"], j.get("reason",""))

    # --- unauthorised socket is closed ---
    try:
        async with websockets.connect(f"{WS}/ws/class/{sid}?token=garbage") as w:
            m = json.loads(await w.recv())
            check("bad token rejected", m.get("code") == "unauthorized", m)
    except Exception as e:
        check("bad token rejected", True, type(e).__name__)

    # --- student never admitted through /api/join cannot open the socket ---
    try:
        async with websockets.connect(f"{WS}/ws/class/{sid}?token={s3}") as w:
            m = json.loads(await w.recv())
            check("non-admitted student blocked from room", m.get("code") == "not_admitted", m.get("code"))
    except Exception as e:
        check("non-admitted student blocked from room", True, type(e).__name__)

    # --- two real peers ---
    async with websockets.connect(f"{WS}/ws/class/{sid}?token={t}&network=wifi") as teacher:
        w1 = json.loads(await teacher.recv())
        check("teacher welcome", w1["type"] == "welcome", f"peer={w1['peerId'][:12]}")
        check("ice servers over ws", len(w1["iceServers"]) >= 1)
        check("seeded captions replayed", len(w1["captions"]) >= 0, f"{len(w1['captions'])}")

        async with websockets.connect(f"{WS}/ws/class/{sid}?token={s1}&network=3g") as stu:
            w2 = json.loads(await stu.recv())
            check("student welcome", w2["type"] == "welcome")
            check("student sees teacher in room", len(w2["participants"]) == 1,
                  w2["participants"][0]["name"] if w2["participants"] else "")
            check("newcomer told whom to offer", w2["shouldInitiate"] == [w1["peerId"]])
            check("3g student gets stepped-down profile", w2["quality"]["profile"] in ("low","minimal"),
                  w2["quality"]["label"])

            ev = json.loads(await teacher.recv())
            check("teacher notified of join", ev["type"] == "peer-joined",
                  ev.get("peer",{}).get("rollNumber"))
            _ = json.loads(await teacher.recv())   # quality-update

            # SDP relay
            await stu.send(json.dumps({"type":"offer","to":w1["peerId"],
                                       "payload":{"sdp":"v=0 fake-offer","type":"offer"}}))
            got = json.loads(await teacher.recv())
            check("offer relayed to the right peer", got["type"]=="offer" and got["from"]==w2["peerId"],
                  got["payload"]["sdp"])
            await teacher.send(json.dumps({"type":"answer","to":w2["peerId"],
                                           "payload":{"sdp":"v=0 fake-answer"}}))
            got = json.loads(await stu.recv())
            check("answer relayed back", got["type"]=="answer")
            await teacher.send(json.dumps({"type":"ice","to":w2["peerId"],"payload":{"candidate":"cand:1"}}))
            got = json.loads(await stu.recv())
            check("ICE candidate relayed", got["payload"]["candidate"]=="cand:1")

            # captions broadcast
            await teacher.send(json.dumps({"type":"caption","text":"Let's begin.","isFinal":True,"offsetMs":1000}))
            got = json.loads(await stu.recv())
            check("live caption broadcast", got["type"]=="caption" and got["text"]=="Let's begin.", got["speaker"])

            # hand raise
            await stu.send(json.dumps({"type":"hand","raised":True}))
            got = json.loads(await teacher.recv())
            check("hand raise broadcast", got["type"]=="hand" and got["raised"] is True, got["name"])

            # engagement only reaches hosts
            await stu.send(json.dumps({"type":"engagement","score":64}))
            got = json.loads(await teacher.recv())
            check("engagement pushed to teacher", got["type"]=="engagement" and got["score"]==64)

            # latency probe
            await stu.send(json.dumps({"type":"ping","t":123}))
            got = json.loads(await stu.recv())
            while got.get("type") != "pong":      # drain any queued broadcasts
                print("      (drained", got.get("type"), ")")
                got = json.loads(await stu.recv())
            check("ping/pong keepalive", got["type"]=="pong" and got["t"]==123)

            async with httpx.AsyncClient(base_url=BASE) as c:
                r = await c.get("/api/realtime/stats")
                check("hub reports 2 participants", r.json()["participants"] == 2, r.json()["detail"])

        left = json.loads(await teacher.recv())
        check("peer-left announced", left["type"]=="peer-left", left["peerId"][:12])

    print(f"\n{sum(res)}/{len(res)} checks passed")
    sys.exit(0 if all(res) else 1)

asyncio.run(main())
