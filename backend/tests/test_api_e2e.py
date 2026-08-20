"""End-to-end smoke test of the whole flow, run against a live server."""
import json, sys
import httpx
import numpy as np

BASE = "http://localhost:8000"
ok = lambda label, cond, extra="": print(f"{'PASS' if cond else 'FAIL'}  {label} {extra}") or cond
results = []

def check(label, cond, extra=""):
    results.append(cond)
    print(f"{'  ok  ' if cond else ' FAIL '} {label} {extra}")
    return cond

c = httpx.Client(base_url=BASE, timeout=30)

# --- login as teacher and student ---
r = c.post("/api/auth/login", json={"email": "ramesh.iyer@smartclass.edu", "password": "teach1234"})
check("teacher login", r.status_code == 200, r.text[:120])
tt = r.json()["token"]; TH = {"Authorization": f"Bearer {tt}"}

r = c.post("/api/auth/login", json={"email": "aarav.sharma@student.smartclass.edu", "password": "student123"})
check("student login", r.status_code == 200)
st = r.json()["token"]; SH = {"Authorization": f"Bearer {st}"}
student = r.json()["user"]
check("student has roll number", student["roll_number"] == "21CS001", student["roll_number"])

r = c.post("/api/auth/login", json={"email": "aarav.sharma@student.smartclass.edu", "password": "wrong"})
check("bad password rejected", r.status_code == 401)

r = c.get("/api/auth/me")
check("unauthenticated /me rejected", r.status_code == 401)

# --- classes ---
r = c.get("/api/classes", headers=TH)
check("teacher class list", r.status_code == 200 and r.json()["total"] >= 1, f"{r.json().get('total')} classes")
r = c.get("/api/classes", headers=SH)
sclasses = r.json()["classrooms"]
check("student sees enrolled classes", len(sclasses) >= 4, f"{len(sclasses)}")
live_class = next((x for x in sclasses if x["status"] == "live"), None)
check("a live class is visible", live_class is not None, live_class["name"] if live_class else "")

r = c.get("/api/sessions/live", headers=SH)
sess_id = r.json()["sessions"][0]["session_id"]
check("live session endpoint", r.status_code == 200 and bool(sess_id), sess_id)

# --- face gate ---
r = c.get("/api/face/status", headers=SH)
check("face enrolled from seed", r.json()["enrolled"] is True, f"{r.json()['samples']} samples")

rng = np.random.default_rng(1000)   # matches seed's _descriptor(1000 + 0) for student 0
mine = rng.normal(scale=0.12, size=128).round(6).tolist()

r = c.post("/api/face/verify", headers=SH, json={"descriptor": mine, "session_id": sess_id})
j = r.json()
check("own face verifies", j["matched"] and j["roll_number"] == "21CS001",
      f"conf={j['confidence']} dist={j['distance']}")
check("verification marks attendance", j["attendance_marked"] is True, j.get("status"))

impostor = np.random.default_rng(1005).normal(scale=0.12, size=128).round(6).tolist()
r = c.post("/api/face/verify", headers=SH, json={"descriptor": impostor, "session_id": sess_id})
j = r.json()
check("someone else's face is refused", not j["matched"] and j["status"] == "identity_mismatch",
      f"matched_roll={j.get('roll_number')}")

noise = (np.random.default_rng(9).normal(size=128) * 2).round(6).tolist()
r = c.post("/api/face/verify", headers=SH, json={"descriptor": noise, "session_id": sess_id})
check("unknown face rejected", r.json()["matched"] is False, r.json()["status"])

r = c.post("/api/face/verify", headers=SH, json={"descriptor": [0.1] * 64, "session_id": sess_id})
check("malformed descriptor rejected (422)", r.status_code == 422)

# --- join gate ---
r = c.post("/api/join", headers=SH, json={"session_id": sess_id, "descriptor": mine,
                                          "device": {"kind": "laptop" if False else "desktop", "network": "3g"}})
j = r.json()
check("enrolled student admitted", j["admitted"] is True, j.get("reason") or "")
check("ICE servers returned", len(j["ice_servers"]) >= 1)
check("adaptive profile for 3g", j["quality_profile"]["profile"] in ("low", "minimal"),
      j["quality_profile"]["label"])
check("ws url issued", j["ws_url"].startswith("ws://"), j["ws_url"])

r = c.post("/api/join", headers=SH, json={"session_id": sess_id})
check("join without face refused", r.json()["admitted"] is False and r.json()["reason"] == "face_required")

r = c.post("/api/join", headers=SH, json={"session_id": sess_id, "descriptor": impostor})
check("join with wrong face refused", r.json()["admitted"] is False, r.json()["reason"][:50])

r = c.post("/api/join", headers=SH, json={"session_id": sess_id, "descriptor": mine,
                                          "roll_number": "21CS099"})
check("mismatched roll number refused", r.json()["admitted"] is False, r.json()["reason"][:50])

r = c.post("/api/join", headers=SH, json={"room_code": "NOPE-0000", "descriptor": mine})
check("unknown room code refused", r.json()["admitted"] is False)

# a student NOT enrolled in the English class cannot join it
r = c.post("/api/auth/login", json={"email": "divya.thakur@student.smartclass.edu", "password": "student123"})
OH = {"Authorization": f"Bearer {r.json()['token']}"}

# --- heartbeats, violations ---
r = c.post("/api/attendance/heartbeat", headers=SH,
           json={"session_id": sess_id, "engagement_score": 91, "elapsed_seconds": 30})
check("heartbeat accepted", r.status_code == 200, f"engagement={r.json()['engagement_score']}")

r = c.post("/api/attendance/violation", headers=SH,
           json={"session_id": sess_id, "kind": "tab_switch", "severity": "warning"})
j = r.json()
check("violation logged + penalty applied", j["violations"] >= 1,
      f"violations={j['violations']} engagement={j['engagement_score']}")

# --- captions ---
r = c.post("/api/captions", headers=TH,
           json={"session_id": sess_id, "text": "Integration by parts, one more time.",
                 "is_final": True, "offset_ms": 300000})
check("caption stored", r.json()["stored"] is True)
r = c.get(f"/api/captions/{sess_id}", headers=SH)
check("captions readable", r.json()["total"] >= 6, f"{r.json()['total']} lines")
r = c.get(f"/api/captions/{sess_id}/transcript", headers=SH)
check("transcript renders", "Dr. Ramesh Iyer" in r.text, r.text.splitlines()[0][:50])

# --- SUNNY ---
r = c.post("/api/chat", headers=SH, json={"message": "what is my attendance?", "session_id": sess_id})
j = r.json()
check("SUNNY answers", len(j["response"]) > 50, f"intent={j['intent']} provider={j['provider']} {j['latency_ms']}ms")
r = c.get("/api/chat/status")
check("SUNNY status reports fallback", r.json()["provider"] == "fallback")
r = c.post("/api/chat", headers=SH, json={"message": "hello sunny"})
check("SUNNY keeps conversation id", bool(r.json()["conversation_id"]))

# The no-API-key mode must still answer with the student's REAL numbers,
# not a generic brochure paragraph.
r = c.post("/api/chat", headers=SH, json={"message": "what is my attendance?"})
body = r.json()["response"]
check("offline SUNNY quotes real attendance", "%" in body and "Aarav" in body,
      body.splitlines()[0][:60])

# ...but "how does X work" should still get the explainer, not the numbers.
for probe in ("how does face recognition work?",
              "how does the face recognition attendance actually work?",
              "explain facial recognition"):
    body = c.post("/api/chat", headers=SH, json={"message": probe}).json()["response"]
    check(f"offline SUNNY explains: {probe[:34]}…", "face-api.js" in body,
          body.splitlines()[0][:44])

r = c.post(f"/api/captions/{sess_id}/summarize", headers=SH, json={})
j = r.json()
check("extractive lecture summary without an LLM",
      j["provider"] == "extractive" and "Key points" in j["summary"],
      f"{j['caption_lines']} lines")

# --- analytics ---
r = c.get("/api/analytics/overview", headers=SH)
j = r.json()
check("student overview", j["totals"]["records"] > 0,
      f"rate={j['rate']}% engagement={j['avg_engagement']}")
r = c.get("/api/analytics/weekly", headers=SH)
check("weekly series", len(r.json()["series"]) >= 1, f"{len(r.json()['series'])} days")
r = c.get("/api/analytics/heatmap", headers=SH)
check("heatmap cells", len(r.json()["cells"]) > 5, f"{len(r.json()['cells'])} cells")
cls_id = live_class["id"]
r = c.get(f"/api/analytics/class/{cls_id}", headers=TH)
j = r.json()
check("teacher class breakdown", j["total"] == 30, f"avg={j['class_average']}% at_risk={len(j['at_risk'])}")
r = c.get(f"/api/analytics/class/{cls_id}", headers=SH)
check("student blocked from teacher analytics", r.status_code == 403)
r = c.get(f"/api/analytics/live/{sess_id}", headers=TH)
j = r.json()
check("live stats", j["roster"] == 30, f"joined={j['joined']} present={j['present']}")

# --- theme persistence ---
r = c.patch("/api/auth/theme", headers=SH, json={"theme": "light"})
check("theme saved to account", r.json()["theme"] == "light")
c.patch("/api/auth/theme", headers=SH, json={"theme": "dark"})

# --- config ---
r = c.get("/api/config")
check("client config", r.json()["faceThreshold"] == 0.52)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
