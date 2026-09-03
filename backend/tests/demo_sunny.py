"""
SUNNY AI — end-to-end walkthrough.

Traces one question all the way through the pipeline so you can see exactly
what SUNNY knows, what it sends to the language model, and what comes back.

    python tests/demo_sunny.py            # against a running server on :8000
"""
import asyncio, json, os, sys, textwrap

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.environ.get("SUNNYCLASS_URL", "http://localhost:8000")
W = 78

def rule(title=""):
    if title:
        print("\n" + "═" * W); print(f" {title}"); print("═" * W)
    else:
        print("─" * W)

def wrap(text, indent="  "):
    for para in str(text).split("\n"):
        if not para.strip():
            print(); continue
        print(textwrap.fill(para, width=W - 2, initial_indent=indent,
                            subsequent_indent=indent))


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        # ---------------------------------------------------------------- 1
        rule("STEP 1 — Who is asking?")
        r = await c.post("/api/auth/login", json={
            "email": "aarav.sharma@student.sunnyclass.edu", "password": "student123"})
        r.raise_for_status()
        tok = r.json()["token"]; user = r.json()["user"]
        H = {"Authorization": f"Bearer {tok}"}
        print(f"  Signed in as : {user['full_name']}")
        print(f"  Roll number  : {user['roll_number']}")
        print(f"  Role         : {user['role']}")
        print("\n  SUNNY answers as this student. Another student asking the same")
        print("  question gets different numbers, because the context is per-user.")

        # ---------------------------------------------------------------- 2
        rule("STEP 2 — What SUNNY looks up before answering")
        live = (await c.get("/api/sessions/live", headers=H)).json()
        session_id = live["sessions"][0]["session_id"] if live["sessions"] else None

        # Rebuild the exact same context the endpoint builds, using the app code.
        from app.core.database import SessionLocal
        from app.models import User
        from app.routers.chat import build_context
        from sqlalchemy import select

        async with SessionLocal() as db:
            db_user = await db.scalar(select(User).where(User.id == user["id"]))
            ctx = await build_context(db, db_user, session_id, None)

        print("  Pulled live from PostgreSQL — no hardcoded values:\n")
        for k, v in ctx.items():
            v = str(v)
            if len(v) > 62:
                v = v[:59] + "…"
            print(f"    {k:28s} {v}")

        # ---------------------------------------------------------------- 3
        rule("STEP 3 — The prompt that gets built")
        from app.core.config import settings
        from app.services import llm

        question = "What is my attendance, and am I at risk of falling short?"
        msgs = llm._build_messages(question, ctx, [])
        print("  SYSTEM MESSAGE (truncated):")
        wrap(msgs[0]["content"][:600] + "\n…", "    ")
        print("\n  USER MESSAGE:")
        wrap(question, "    ")

        # ---------------------------------------------------------------- 4
        rule("STEP 4 — Ask SUNNY (through the real HTTP endpoint)")
        status = (await c.get("/api/chat/status")).json()
        print(f"  Provider configured : {status['provider']}")
        print(f"  General knowledge   : {'yes' if status['llm_enabled'] else 'no (offline knowledge base)'}")

        questions = [
            "What is my attendance, and am I at risk of falling short?",
            "How does the face recognition attendance actually work?",
            "Summarise what the teacher has said in class so far.",
            "Solve the integral of x times e^x dx, showing the steps.",
        ]
        conversation = None
        for q in questions:
            rule()
            print(f"  YOU   ▸ {q}\n")
            res = (await c.post("/api/chat", headers=H, json={
                "message": q, "session_id": session_id,
                "conversation_id": conversation})).json()
            conversation = res["conversation_id"]
            print(f"  SUNNY ▾  [intent={res['intent']}  via={res['provider']}  {res['latency_ms']}ms]")
            wrap(res["response"], "         ")
            if res["suggestions"]:
                print(f"\n         follow-ups: {' · '.join(res['suggestions'][:3])}")

        # ---------------------------------------------------------------- 5
        rule("STEP 5 — Memory: the conversation persists")
        hist = (await c.get(f"/api/chat/history?conversation_id={conversation}",
                            headers=H)).json()
        print(f"  {len(hist['messages'])} messages stored under {conversation}")
        print("  The last 10 turns are replayed into every new request, so SUNNY")
        print("  can follow up on 'explain that again' or 'what about last week?'\n")
        for m in hist["messages"][-4:]:
            who = "YOU  " if m["role"] == "user" else "SUNNY"
            print(f"    {who} │ {m['content'][:64].splitlines()[0]}…")

        # ---------------------------------------------------------------- 6
        rule("STEP 6 — Lecture summary from the stored transcript")
        if session_id:
            s = (await c.post(f"/api/captions/{session_id}/summarize",
                              headers=H, json={})).json()
            print(f"  Built from {s['caption_lines']} stored caption lines "
                  f"(via={s['provider']}, {s['latency_ms']}ms):\n")
            wrap(s["summary"][:900], "    ")

        rule("DONE")
        if not status["llm_enabled"]:
            print("  Everything above ran on the built-in knowledge base — no API key,")
            print("  no cost, works offline. Set LLM_PROVIDER + LLM_API_KEY in .env and")
            print("  the same endpoints answer open-ended questions too.\n")


if __name__ == "__main__":
    asyncio.run(main())
