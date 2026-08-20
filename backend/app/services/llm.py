"""SUNNY AI — provider-agnostic LLM client with a rule-based fallback.

Set LLM_PROVIDER + LLM_API_KEY in .env to enable real answers to arbitrary
questions. With no key configured the service degrades to a deterministic
knowledge-base engine so the product still works offline / for free.

Supported providers: groq | gemini | openai | anthropic
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Any, AsyncIterator, Optional

import httpx

from app.core.config import settings

DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "custom": "local-model",
    "ollama": "llama3.1",
    "vllm": "local-model",
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
}

SYSTEM_PROMPT = """You are SUNNY, the AI assistant inside SmartClass AI, a virtual
classroom platform for students and teachers.

You do two jobs:
1. Answer questions about the platform (attendance, recordings, captions,
   engagement tracking, schedules, settings) using the CONTEXT provided.
2. Act as a capable study tutor — answer ANY academic or general question the
   student asks (maths, physics, CS, literature, history, coding, exam prep),
   showing working steps for problems.

Style: warm, concise, and clear. Use short paragraphs and markdown. Prefer
worked examples over abstractions. If the CONTEXT contains the student's real
attendance or class data, use those exact numbers and never invent figures.
If you truly don't know something, say so plainly.
Never claim to have access to a student's camera feed or private video."""


# --------------------------------------------------------------------------- #
# Rule-based fallback engine
# --------------------------------------------------------------------------- #
KB: dict[str, dict[str, Any]] = {
    "greetings": {
        "patterns": ["hello", "hi ", "hey", "good morning", "good evening",
                     "good afternoon", "namaste", "yo "],
        "responses": [
            "Hey! ☀️ I'm SUNNY, your SmartClass assistant. Ask me about your "
            "attendance, your schedule, recordings — or anything from today's lecture.",
            "Hi there! I'm SUNNY. I can pull up your attendance, explain a concept "
            "from class, or summarise the lecture transcript. What do you need?",
        ],
    },
    "attendance": {
        "patterns": ["attendance", "present", "absent", "mark me", "check in",
                     "roll call", "am i present"],
        "responses": [
            "📋 **Attendance runs automatically.**\n\n"
            "1. Your camera stays on during class\n"
            "2. face-api.js computes a 128-d face descriptor **in your browser**\n"
            "3. Only that descriptor is sent to the server, which matches it "
            "against the class roster\n"
            "4. You're marked present once confidence clears 60%, then re-verified "
            "every 45 seconds\n\n"
            "Your presence time has to cover at least 60% of the session to stay "
            "'Present' at the end.",
        ],
    },
    "face": {
        "patterns": ["face recognition", "facial recognition", "face detect", "face id",
                     "recognise my face", "recognize my face", "face scan", "biometric",
                     "descriptor", "face model", "face enrol", "face enroll", "my face"],
        "responses": [
            "👤 **How face recognition works here**\n\n"
            "1. `face-api.js` runs **in your browser**: tinyFaceDetector finds your "
            "face, 68 landmarks align it, and faceRecognitionNet turns it into a "
            "**128-number descriptor**\n"
            "2. Only that descriptor is sent to the server — your video and photos "
            "never leave your device\n"
            "3. The server compares it against the enrolled descriptors for *your "
            "class roster only*, using euclidean distance\n"
            "4. A distance under **0.52** with confidence over **60%** counts as a "
            "match, and you're marked present\n"
            "5. It re-checks every 45 seconds, so you can't join and walk away\n\n"
            "If the descriptor matches a *different* roll number, the join is "
            "refused and logged — that's the anti-impersonation check.\n\n"
            "You enrol once (3 samples, different angles). Ask me to reset it if "
            "your appearance changes a lot.",
        ],
    },
    "recording": {
        "patterns": ["record", "recording", "download", "replay", "playback", "rewatch"],
        "responses": [
            "🎥 **Recordings** are captured with MediaRecorder and uploaded to the "
            "server in chunks, so you can replay a class from any device. Each "
            "recording carries its full caption transcript — open Dashboard → "
            "Recordings to watch or download.",
        ],
    },
    "captions": {
        "patterns": ["caption", "subtitle", "transcript", "what was said", "summary", "summarise"],
        "responses": [
            "📝 **Live captions** run through the Web Speech API and stream to "
            "everyone in the room over the class WebSocket, so students on slow "
            "connections can follow along even at audio-only quality. Every line is "
            "stored, so the full transcript is downloadable after class.",
        ],
    },
    "engagement": {
        "patterns": ["engagement", "attention", "eye track", "gaze", "distracted",
                     "drowsy", "focus score"],
        "responses": [
            "👁️ **Engagement** comes from 68-point facial landmarks: eye-aspect-ratio "
            "detects drowsiness and gaze direction estimates whether you're looking at "
            "the screen. Samples are smoothed, so one blink won't hurt you — but "
            "tab-switching and leaving fullscreen each cost you points.",
        ],
    },
    "fullscreen": {
        "patterns": ["fullscreen", "tab switch", "switch tab", "violation", "locked", "exit"],
        "responses": [
            "🔒 Class runs in **enforced fullscreen**. Leaving fullscreen, switching "
            "tabs, or blurring the window raises a proctor event that's logged against "
            "the session. Three violations flag the record for your teacher's review.",
        ],
    },
    "schedule": {
        "patterns": ["schedule", "timetable", "next class", "upcoming", "when is"],
        "responses": [
            "📅 Your schedule lives on the Dashboard under **My Classes** — each card "
            "shows the next occurrence and turns red with a LIVE pill when the teacher "
            "starts the session.",
        ],
    },
    "camera": {
        "patterns": ["camera", "webcam", "turn off video", "disable camera"],
        "responses": [
            "📷 The camera stays **on** for the whole class — it's what powers "
            "auto-attendance and engagement. Video frames are processed locally in "
            "your browser; only face descriptors and telemetry reach the server.",
        ],
    },
    "network": {
        "patterns": ["slow", "lag", "bandwidth", "network", "buffer", "quality", "data"],
        "responses": [
            "📶 SmartClass adapts to your connection automatically: 720p on 4G/WiFi, "
            "480p@15fps on 3G, 360p@10fps on 2G, and audio-only + captions below that. "
            "You can force a profile from the toolbar's quality menu.",
        ],
    },
    "help": {
        "patterns": ["help", "what can you", "capabilities", "features", "how do i"],
        "responses": [
            "I can help with:\n\n"
            "• 📋 **Attendance** — \"what's my attendance?\"\n"
            "• 📅 **Schedule** — \"when is my next class?\"\n"
            "• 🎥 **Recordings** — \"show me last week's lecture\"\n"
            "• 📝 **Captions** — \"summarise today's class\"\n"
            "• 🎓 **Study help** — ask me any subject question\n\n"
            "Connect an LLM key in the backend `.env` and I can answer *anything*, "
            "not just platform questions.",
        ],
    },
}

SUGGESTIONS = [
    "What's my attendance this month?",
    "Summarise today's lecture",
    "When is my next class?",
    "Explain this topic again",
]


# Words that mean "tell me MY numbers" rather than "explain how this works".
DATA_CUES = ("my ", "am i", "i am", "how many", "how much", "what's my", "what is my",
             "was i", "do i", "have i", "mine", "me ")
EXPLAIN_CUES = ("how does", "how do", "how is", "explain", "what is the", "why does",
                "why is", "work", "works", "mean")


def detect_intent(message: str) -> str:
    lower = f" {message.lower().strip()} "
    best, best_score = "general", 0
    for topic, data in KB.items():
        for pattern in data["patterns"]:
            if pattern in lower and len(pattern) > best_score:
                best, best_score = topic, len(pattern)
    return best


def _wants_data(message: str) -> bool:
    """Is the student asking about themselves, or asking how the feature works?"""
    lower = f" {message.lower().strip()} "
    data_hit = any(c in lower for c in DATA_CUES)
    explain_hit = any(c in lower for c in EXPLAIN_CUES)
    if data_hit and not explain_hit:
        return True
    if explain_hit and not data_hit:
        return False
    return data_hit


def summarize_transcript(transcript: str, max_points: int = 6) -> str:
    """Extractive summary with no language model.

    Scores sentences by keyword salience (term frequency over content words,
    with a small bonus for sentences carrying dates, numbers or action verbs),
    then returns the top-ranked ones in their original order so the summary
    still reads chronologically.
    """
    text = re.sub(r"\s+", " ", transcript or "").strip()
    if not text:
        return "There is no transcript for this session yet."

    raw = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 15]
    # A teacher repeats themselves, and speech recognition re-emits lines — keep
    # the first occurrence of each distinct sentence only.
    sentences, seen = [], set()
    for sent in raw:
        key = re.sub(r"[^a-z0-9 ]", "", sent.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            sentences.append(sent)
    if not sentences:
        return text[:400]

    stop = set("""a an the and or but if then so of to in on at by for with from as is are was
        were be been being it its this that these those i you he she we they me my your our their
        will would can could shall should may might do does did not no yes just now here there
        what which who whom when where why how all any both each few more most other some such
        only own same than too very s t don now let lets okay alright right well going go got""".split())

    freq: dict[str, int] = {}
    for word in re.findall(r"[a-z][a-z'-]+", text.lower()):
        if word not in stop and len(word) > 2:
            freq[word] = freq.get(word, 0) + 1
    if not freq:
        return " ".join(sentences[:max_points])

    peak = max(freq.values())
    scored = []
    for idx, sent in enumerate(sentences):
        words = [w for w in re.findall(r"[a-z][a-z'-]+", sent.lower())
                 if w not in stop and len(w) > 2]
        if not words:
            continue
        score = sum(freq.get(w, 0) for w in words) / (len(words) ** 0.65) / peak
        if re.search(r"\b(homework|due|exam|quiz|test|assignment|deadline|submit|chapter|"
                     r"page|week|tomorrow|friday|monday)\b", sent, re.I):
            score *= 1.6                       # actionable lines matter most
        if re.search(r"\d", sent):
            score *= 1.15
        if idx == 0:
            score *= 1.25                      # openers usually state the topic
        scored.append((score, idx, sent))

    action_re = re.compile(
        r"\b(homework|due|exam|quiz|test|assignment|deadline|submit)\b", re.I)
    actions = [s for s in sentences if action_re.search(s)]

    # Keep the two sections disjoint — an item listed under "To do" shouldn't
    # also be padding out the key points.
    content = [t for t in scored if not action_re.search(t[2])] if actions else scored
    top = sorted(content, reverse=True)[:max_points]
    chosen = [s for _, _, s in sorted(top, key=lambda t: t[1])]

    out = ["**Key points from this lecture**", ""]
    out += [f"- {s}" for s in chosen]
    if actions:
        out += ["", "**To do**", ""]
        out += [f"- {s}" for s in dict.fromkeys(actions)]
    out += ["", "_Extractive summary generated without a language model — connect an "
            "LLM key for an abstractive summary with revision questions._"]
    return "\n".join(out)


def _risk_note(rate: float) -> str:
    if rate >= 85:
        return "That is comfortably above the usual 75% requirement — you're in good shape."
    if rate >= 75:
        return ("That clears the usual 75% requirement, but not by much — missing two or "
                "three more classes would put you under.")
    return ("That is **below the usual 75% requirement**. Talk to your teacher about "
            "making up the shortfall, and try not to miss any more sessions.")


def grounded_answer(intent: str, context: dict) -> Optional[str]:
    """Answer from the student's own rows when we actually have them.

    This is what keeps the no-API-key mode genuinely useful: the numbers are
    real even when there is no language model to phrase them.
    """
    if not context:
        return None
    name = str(context.get("student_name", "")).split(" ")[0]

    if intent == "attendance" and context.get("attendance_rate") is not None:
        rate = float(context["attendance_rate"])
        lines = [
            f"📋 **Your attendance is {rate}%**"
            + (f", {name}." if name else "."),
            "",
            f"- Sessions recorded: **{context.get('sessions_recorded', '?')}**",
            f"- Classes missed: **{context.get('classes_missed', 0)}**",
            f"- Average engagement: **{context.get('avg_engagement', '?')}%**",
        ]
        if context.get("total_violations"):
            lines.append(f"- Proctor flags: **{context['total_violations']}** "
                         "(tab switches, leaving fullscreen, and similar)")
        lines += ["", _risk_note(rate)]
        return "\n".join(lines)

    if intent == "engagement" and context.get("avg_engagement") is not None:
        return (f"👁️ **Your average engagement is {context['avg_engagement']}%.**\n\n"
                "It comes from 68-point facial landmarks: eye-aspect-ratio detects "
                "drowsiness and gaze direction estimates whether you're looking at the "
                "screen. Scores are smoothed, so a blink costs nothing — but tab-switching "
                f"and leaving fullscreen do. You have **{context.get('total_violations', 0)}** "
                "recorded infractions.")

    if intent == "schedule" and context.get("my_classes"):
        rows = [f"- {c.strip()}" for c in str(context["my_classes"]).split(";") if c.strip()]
        out = [f"📅 **Your {context.get('classes_count', len(rows))} classes**", ""] + rows
        if context.get("live_now"):
            out += ["", f"🔴 **Live right now:** {context['live_now']} — "
                        "the Join button is on your dashboard."]
        return "\n".join(out)

    if intent in ("captions", "recording") and context.get("lecture_transcript_recent"):
        return summarize_transcript(str(context["lecture_transcript_recent"]))

    return None


def fallback_answer(message: str, context: Optional[dict] = None) -> str:
    intent = detect_intent(message)

    # Prefer the student's real data over the generic explainer whenever the
    # question is about them and we actually hold the rows.
    if _wants_data(message) or intent in ("captions", "recording"):
        grounded = grounded_answer(intent, context or {})
        if grounded:
            return grounded

    if intent == "general":
        hint = ""
        if context and context.get("attendance_rate") is not None:
            hint = (f"\n\nWhat I *can* tell you right now: your attendance is "
                    f"**{context['attendance_rate']}%** across "
                    f"{context.get('classes_count', 0)} classes, with an average "
                    f"engagement of {context.get('avg_engagement', '?')}%.")
        return (
            "I don't have a language model connected yet, so I can only answer "
            "questions about SmartClass itself — your attendance, engagement, "
            "schedule, recordings, captions and network quality.\n\n"
            "To let me answer *any* question — maths, physics, code, exam prep — "
            "set `LLM_PROVIDER` and `LLM_API_KEY` in the backend `.env`. "
            "Groq and Gemini both have free tiers." + hint
        )
    return random.choice(KB[intent]["responses"])


# --------------------------------------------------------------------------- #
# Provider adapters
# --------------------------------------------------------------------------- #
def provider_enabled() -> bool:
    """True when a real language model is configured and usable."""
    return (settings.LLM_PROVIDER.lower().strip() not in ("", "none")
            and bool(settings.LLM_API_KEY.strip()))


def _model() -> str:
    return settings.LLM_MODEL or DEFAULT_MODELS.get(settings.LLM_PROVIDER, "")


def _build_messages(message: str, context: Optional[dict], history: list[dict]) -> list[dict]:
    ctx_block = ""
    if context:
        hidden = {"transcript_session_id"}
        lines = [f"- {k}: {v}" for k, v in context.items()
                 if v not in (None, "", [], {}) and k not in hidden]
        if lines:
            ctx_block = "CONTEXT (live data about this student and class):\n" + "\n".join(lines)
    msgs = [{"role": "system", "content": SYSTEM_PROMPT + ("\n\n" + ctx_block if ctx_block else "")}]
    msgs.extend(history[-10:])
    msgs.append({"role": "user", "content": message})
    return msgs


async def _call_openai_compatible(base_url: str, key: str, messages: list[dict]) -> str:
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
        r = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": _model(),
                "messages": messages,
                "max_tokens": settings.LLM_MAX_TOKENS,
                "temperature": 0.6,
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


async def _call_anthropic(key: str, messages: list[dict]) -> str:
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    convo = [m for m in messages if m["role"] != "system"]
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            json={
                "model": _model(),
                "system": system,
                "messages": convo,
                "max_tokens": settings.LLM_MAX_TOKENS,
            },
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", [])).strip()


async def _call_gemini(key: str, messages: list[dict]) -> str:
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user",
         "parts": [{"text": m["content"]}]}
        for m in messages if m["role"] != "system"
    ]
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{_model()}:generateContent")
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
        r = await client.post(
            url,
            params={"key": key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {"maxOutputTokens": settings.LLM_MAX_TOKENS,
                                     "temperature": 0.6},
            },
        )
        r.raise_for_status()
        cands = r.json().get("candidates", [])
        if not cands:
            raise RuntimeError("Gemini returned no candidates")
        return "".join(p.get("text", "") for p in cands[0]["content"]["parts"]).strip()


async def ask(
    message: str,
    context: Optional[dict] = None,
    history: Optional[list[dict]] = None,
) -> tuple[str, str, int]:
    """Returns (answer, provider_used, latency_ms). Never raises."""
    started = time.perf_counter()
    provider = settings.LLM_PROVIDER.lower().strip()
    key = settings.LLM_API_KEY.strip()

    if provider in ("", "none") or not key:
        answer = fallback_answer(message, context)
        return answer, "fallback", int((time.perf_counter() - started) * 1000)

    messages = _build_messages(message, context, history or [])
    base = settings.LLM_BASE_URL.rstrip("/")
    try:
        if provider == "groq":
            answer = await _call_openai_compatible(
                base or "https://api.groq.com/openai/v1", key, messages)
        elif provider in ("openai", "custom", "ollama", "vllm"):
            answer = await _call_openai_compatible(
                base or "https://api.openai.com/v1", key, messages)
        elif provider == "anthropic":
            answer = await _call_anthropic(key, messages)
        elif provider == "gemini":
            answer = await _call_gemini(key, messages)
        else:
            raise ValueError(f"Unknown LLM_PROVIDER '{provider}'")
        return answer, provider, int((time.perf_counter() - started) * 1000)
    except Exception as exc:  # network error, bad key, rate limit -> degrade
        answer = fallback_answer(message, context)
        note = ("\n\n_(SUNNY's language model is unreachable right now, so that was "
                "the built-in knowledge base. Error: "
                f"{type(exc).__name__}.)_")
        return answer + note, "fallback", int((time.perf_counter() - started) * 1000)


def suggestions_for(intent: str) -> list[str]:
    extra = {
        "attendance": ["Why was I marked late?", "Show my weekly attendance"],
        "recording": ["Summarise the last recording", "Download today's transcript"],
        "captions": ["Summarise today's class", "What did the teacher say about the exam?"],
    }
    return (extra.get(intent, []) + SUGGESTIONS)[:4]
