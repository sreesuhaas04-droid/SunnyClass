# SmartClass AI — backend + database

A complete FastAPI + PostgreSQL backend for the SmartClass AI frontend, plus the
frontend rewired to use it. Nothing in the app is mock data any more: every
number on the dashboard is computed from real attendance rows.

---

## What was built

The original zip shipped a static frontend and five `api/*.js` files that
returned hardcoded arrays — no persistence, no auth, no signaling, and faces
stored in `localStorage`. Those were replaced with:

| Layer | Technology |
|---|---|
| API | FastAPI (async), 40 endpoints, OpenAPI docs at `/api/docs` |
| Database | PostgreSQL 16 via async SQLAlchemy 2.0 (SQLite also supported) |
| Auth | JWT (HS256) + bcrypt, role-based (student / teacher / admin) |
| Live class | WebSocket hub — WebRTC signaling relay, presence, captions, telemetry |
| Video | WebRTC **mesh** — peer-to-peer, no media server, lowest latency |
| Face matching | Browser computes the descriptor, **server owns the match decision** |
| SUNNY AI | Provider-agnostic LLM (Groq / Gemini / OpenAI / Anthropic) + offline fallback |

---

## Quick start

### Option A — Docker (everything, one command)

```bash
docker compose up --build
docker compose exec api python -m app.seed     # demo college data
```

Open <http://localhost:8000>.

### Option B — local Python

```bash
# Postgres must be running and a `smartclass` database must exist
createdb smartclass

cp backend/.env.example backend/.env           # edit DATABASE_URL if needed
./run.sh
```

No PostgreSQL to hand? Set this in `backend/.env` and everything still works:

```
DATABASE_URL=sqlite+aiosqlite:///./smartclass.db
```

### Demo accounts (created by the seeder)

| Role | Email | Password |
|---|---|---|
| Teacher | `ramesh.iyer@smartclass.edu` | `teach1234` |
| Student | `aarav.sharma@student.smartclass.edu` | `student123` (roll `21CS001`) |

The seeder creates 5 teachers, 30 students (`21CS001`–`21CS030`), 5 classes with
rosters, **4 weeks of attendance history**, and one **live** Mathematics session
so the classroom is demoable the moment you sign in.

---

## How each requirement is met

### 1. Auto attendance by facial recognition

Attendance is *derived*, not asserted:

```
join              → record opened, status from lateness (>10 min ⇒ late)
every 45 s        → face re-verified, verifications++
every 15 s        → heartbeat adds presence seconds + gaze telemetry
violation         → engagement penalty, violation counter
session end       → status recomputed from presence ratio; no-shows marked absent
```

A student who joins and walks away does **not** stay "present": at `POST
/api/sessions/{id}/end` anyone below 60% presence is downgraded to *late*, and
below 21% to *absent* (`app/services/attendance.py`).

The face pipeline: `face-api.js` (tinyFaceDetector → 68 landmarks →
faceRecognitionNet) runs **in the browser** and produces a 128-float descriptor.
Only that vector is POSTed. The server holds the enrolled gallery in Postgres and
does the nearest-neighbour match with numpy (`app/services/face.py`), scoped to
the class roster so the search space — and the latency — stays small.

Video frames never leave the device.

### 2. The meeting only admits recognised roll numbers

`POST /api/join` runs four gates in order (`app/routers/face.py`):

1. Valid JWT and a **live** session.
2. The account carries a `roll_number` that exists in the database.
3. That roll number is **enrolled in this specific class**.
4. The submitted descriptor matches **this account's** gallery above threshold —
   a descriptor that matches a *different* roll number is refused as
   impersonation and written to `proctor_events`.

Only then is an attendance row opened and the ICE/WebSocket credentials issued.
The WebSocket handler re-checks admission independently, so a student cannot skip
`/api/join` and open the room socket directly.

### 3. Always-on video, fullscreen, no tab switching

- `MeshRTC.enforceCamera()` re-acquires the camera transparently if the OS or
  another app steals the track.
- `FullscreenGuard` requests fullscreen, takes a **wake lock**, locks phones to
  landscape, and reports `tab_switch`, `fullscreen_exit`, `window_blur`,
  `devtools` and `face_lost` to `POST /api/attendance/violation`.
- Each violation costs engagement points; three flags the record for the teacher,
  who sees it live on their in-class panel.
- iPhone Safari has no Fullscreen API, so there's a scroll-locked immersive
  fallback rather than locking the student out of class.

### 4. Low bandwidth, low latency

- **Mesh WebRTC**: media goes peer-to-peer. There is no server hop to add
  latency and no media server to pay for. The backend relays only SDP/ICE — a
  few KB per participant per session.
- A **quality ladder** (720p → 480p → 360p → 240p → audio-only + captions) is
  chosen server-side from the reported network *and* the room size, because in a
  mesh every extra peer multiplies your uplink. It steps down automatically as
  people join.
- Client-side self-healing: sustained packet loss above 8% asks the server for a
  lower profile.
- Audio is mono at 24 kHz with echo cancellation — a fraction of stereo's uplink.
- Face inference runs on `requestIdleCallback` so it never competes with video
  rendering on a low-end phone, and picks WebGL → wasm → cpu by what the device
  actually has.
- Models are **vendored locally** (`/models`, `/vendor`) with immutable cache
  headers instead of a CDN, so a re-join costs zero bytes.

### 5. SUNNY AI answers anything

See it end to end with `python backend/tests/demo_sunny.py` (server running).

**How a question is answered:**

```
student asks
   │
   ├─ 1. identity     JWT → which student, which roll number, which classes
   │
   ├─ 2. grounding    build_context() queries PostgreSQL live:
   │                    attendance_rate, sessions_recorded, classes_missed,
   │                    avg_engagement, total_violations, my_classes,
   │                    live_now, lecture_transcript_recent (last 60 lines)
   │
   ├─ 3. history      last 10 turns of this conversation_id
   │
   ├─ 4. prompt       system prompt + CONTEXT block + history + question
   │
   ├─ 5a. LLM path    provider configured  → Groq / Gemini / OpenAI / Anthropic
   │                                         (or any OpenAI-compatible URL)
   └─ 5b. local path  no key, or provider unreachable →
                        • intent detection over a knowledge base
                        • grounded answers using the SAME real numbers
                        • extractive transcript summarisation
```

**The context is per-student.** Two students asking "what's my attendance?"
get different numbers, because the block is rebuilt from their own rows every
time. The model is told to use those figures and never invent them.

**Enabling open-ended answers** — set two variables in `backend/.env`:

```
LLM_PROVIDER=groq          # groq | gemini | openai | anthropic | custom
LLM_API_KEY=...
# LLM_BASE_URL=http://localhost:11434/v1   # Ollama, vLLM, LM Studio, LiteLLM…
```

**Without a key it is still useful, not a brochure.** The offline engine reads
the same context, so:

| Question | Offline answer |
|---|---|
| "What's my attendance, am I at risk?" | Real 81.6%, 49 sessions, 9 missed, plus a pass/fail judgement against the 75% rule |
| "How does face recognition work?" | The pipeline explainer — it can tell a *data* question from a *how does this work* question |
| "Summarise today's lecture" | A real extractive summary of the stored transcript, with homework and deadlines split into a **To do** section |
| "Solve ∫x·eˣ dx" | Says plainly that this needs an LLM key — and still offers the data it does have |

Summarisation without a model scores sentences by keyword salience, weights
lines mentioning homework/exams/deadlines, deduplicates a teacher's repetitions,
and returns the winners in chronological order.

**Failure is graceful.** A bad key, a rate limit, a timeout or a dead provider
returns HTTP 200 with the local answer and an honest one-line note — the student
never sees a stack trace.

`POST /api/captions/{id}/summarize` produces the post-class study summary
(abstractive with a key, extractive without).

### 6. Live captions

Web Speech API transcription is pushed over the class socket for sub-100 ms
fan-out **and** persisted with `POST /api/captions`. Students whose browser lacks
speech support still *receive* everyone else's captions. A late joiner gets the
backlog replayed in their `welcome` frame. Full transcript at
`GET /api/captions/{id}/transcript`.

### 7. Attendance in visuals

`/api/analytics/*` returns render-ready series:

| Endpoint | Powers |
|---|---|
| `overview` | Stat tiles + attendance composition |
| `weekly` | Per-weekday bar chart |
| `heatmap` | 90-day calendar heatmap |
| `class/{id}` | Teacher's per-student grid + at-risk list |
| `live/{id}` | In-class real-time tiles |

Charts follow a status palette (present / late / absent) that was run through a
colour-blindness validator; every one carries a legend, direct labels **and** a
table view, so status is never conveyed by colour alone. Dark mode is a
separately-stepped ramp, not an inverted one.

### 8. Dark / light and multi-device

The theme is stored on the **account** (`PATCH /api/auth/theme`), so it follows a
student from laptop to phone. Layouts are responsive from 360 px through tablet
to desktop, with a landscape-phone mode that reclaims vertical space in class,
and safe-area insets for notched devices. Sign-ins register a device row
(`phone` / `tablet` / `desktop` + network type), which also feeds the quality
ladder.

---

## API surface

Full interactive docs: **<http://localhost:8000/api/docs>**

```
POST   /api/auth/register|login        GET  /api/auth/me
PATCH  /api/auth/theme                 GET|POST /api/auth/devices

GET|POST /api/classes                  GET  /api/classes/{id}/roster
POST   /api/classes/{id}/enroll

POST   /api/sessions/start             POST /api/sessions/{id}/end
GET    /api/sessions/live              GET  /api/sessions/{id}

POST   /api/face/enroll|verify         GET  /api/face/status
POST   /api/join                       ← the roll-number + face gate

GET    /api/attendance                 POST /api/attendance/mark
POST   /api/attendance/heartbeat       POST /api/attendance/violation

GET    /api/analytics/overview|weekly|heatmap|class/{id}|live/{id}

POST   /api/captions                   GET  /api/captions/{id}[/transcript]
POST   /api/captions/{id}/summarize
GET    /api/recordings                 POST /api/recordings/upload

POST   /api/chat                       GET  /api/chat/history|status

WS     /ws/class/{session_id}?token=…
```

---

## Database schema

12 tables (`app/models.py`):

`users` · `devices` · `face_descriptors` · `classrooms` · `enrollments` ·
`class_sessions` · `attendance_records` · `engagement_samples` ·
`proctor_events` · `captions` · `recordings` · `chat_messages`

Tables are created on startup via `create_all` for convenience. For production,
generate an Alembic baseline before your first deploy:

```bash
alembic init migrations && alembic revision --autogenerate -m "baseline"
```

---

## Tests

Start the server, then:

```bash
python backend/tests/test_api_e2e.py     # 44 checks — auth, gates, analytics, SUNNY
python backend/tests/test_realtime.py    # 20 checks — signaling, presence, captions
python backend/tests/demo_sunny.py       # narrated SUNNY walkthrough
```

To exercise the LLM code path without spending an API key, run the bundled mock
provider and point SUNNY at it:

```bash
python backend/tests/mock_llm_server.py &          # OpenAI-compatible, on :9999
# in backend/.env:
#   LLM_PROVIDER=custom
#   LLM_API_KEY=anything
#   LLM_BASE_URL=http://127.0.0.1:9999/v1
```

The mock echoes back how many grounded facts and history turns it received, so
you can confirm the prompt is assembled correctly before paying for real tokens.

The suites cover the security-critical paths explicitly: a wrong password, an
expired/garbage token, another student's face, a mismatched roll number, an
unknown room code, a student blocked from teacher-only analytics, and a student
who skips `/api/join` and tries to open the class socket directly.

---

## Production notes

- **Set `SECRET_KEY`.** The default is a placeholder; every JWT depends on it.
- **Serve over HTTPS.** `getUserMedia`, the Fullscreen API and wake locks all
  require a secure context.
- **Add a TURN server** (`TURN_URL` / `TURN_USERNAME` / `TURN_CREDENTIAL`).
  STUN alone fails for students behind symmetric NAT — typically 10–15% of a
  real class. `coturn` is the usual choice.
- **Mesh scales to roughly 8–12 participants.** For a full 30-student class with
  everyone's camera on, put an SFU (mediasoup or LiveKit) behind the same
  signaling protocol — the client already negotiates per-peer.
- **One API worker.** Room state lives in memory in `app/services/realtime.py`;
  to run multiple workers, back the hub with Redis pub/sub.
- **Recordings** are written to `backend/storage/recordings`. Point that at S3 or
  a volume for real deployments; the 512 MB per-file cap is in `.env`.
- Face descriptors are biometric data. Check what your jurisdiction requires
  (consent, retention limits, deletion) — `DELETE /api/face/enroll` is wired up
  for erasure requests.

---

MIT licensed.
