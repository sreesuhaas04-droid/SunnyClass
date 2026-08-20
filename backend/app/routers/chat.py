"""SUNNY AI endpoints — grounded in the student's own live data."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.deps import get_current_user
from app.models import (
    AttendanceRecord, Caption, Classroom, ClassSession, ChatMessage, Enrollment,
    Role, SessionStatus, User,
)
from app.schemas import ChatIn, ChatOut
from app.services import llm

router = APIRouter(prefix="/api/chat", tags=["sunny"])

PRESENT = ("present", "late")


async def build_context(db: AsyncSession, user: User, session_id: str | None,
                        extra: dict | None) -> dict:
    """Ground SUNNY in real rows so it quotes true numbers instead of inventing them."""
    ctx: dict = {
        "student_name": user.full_name,
        "role": user.role,
        "roll_number": user.roll_number,
        "today": datetime.now(timezone.utc).strftime("%A %d %B %Y"),
    }

    records = list((await db.scalars(
        select(AttendanceRecord).where(AttendanceRecord.student_id == user.id)
        .order_by(AttendanceRecord.date_key.desc()).limit(200))).all())
    if records:
        present = sum(1 for r in records if r.status in PRESENT)
        ctx["attendance_rate"] = round(present / len(records) * 100, 1)
        ctx["sessions_recorded"] = len(records)
        ctx["classes_missed"] = len(records) - present
        ctx["avg_engagement"] = round(
            sum(r.engagement_score for r in records) / len(records), 1)
        ctx["total_violations"] = sum(r.violations for r in records)

    if user.role == Role.student.value:
        classes = (await db.execute(
            select(Classroom).join(Enrollment, Enrollment.class_id == Classroom.id)
            .where(Enrollment.student_id == user.id))).scalars().all()
    else:
        classes = (await db.scalars(
            select(Classroom).where(Classroom.teacher_id == user.id))).all()
    if classes:
        ctx["classes_count"] = len(classes)
        ctx["my_classes"] = "; ".join(
            f"{c.name} ({c.subject}) {c.schedule_days} at {c.schedule_time}" for c in classes[:8])

    live = (await db.execute(
        select(ClassSession, Classroom).join(Classroom, Classroom.id == ClassSession.class_id)
        .where(ClassSession.status == SessionStatus.live.value))).all()
    if live:
        ctx["live_now"] = "; ".join(f"{c.name} ({c.room_code})" for _, c in live[:5])

    # Transcript context. In class we use the current session; from the dashboard
    # we fall back to the most recent lecture this user actually has captions
    # for, so "summarise today's lecture" works from anywhere.
    target_session = session_id
    if not target_session:
        if user.role == Role.student.value:
            recent = (await db.execute(
                select(ClassSession.id)
                .join(Classroom, Classroom.id == ClassSession.class_id)
                .join(Enrollment, Enrollment.class_id == Classroom.id)
                .join(Caption, Caption.session_id == ClassSession.id)
                .where(Enrollment.student_id == user.id)
                .order_by(ClassSession.started_at.desc()).limit(1))).first()
        else:
            recent = (await db.execute(
                select(ClassSession.id)
                .join(Classroom, Classroom.id == ClassSession.class_id)
                .join(Caption, Caption.session_id == ClassSession.id)
                .where(Classroom.teacher_id == user.id)
                .order_by(ClassSession.started_at.desc()).limit(1))).first()
        target_session = recent[0] if recent else None

    if target_session:
        caps = (await db.scalars(
            select(Caption).where(Caption.session_id == target_session)
            .order_by(Caption.id.desc()).limit(60))).all()
        if caps:
            ordered = list(reversed(caps))
            ctx["lecture_transcript_recent"] = " ".join(c.text for c in ordered)[-4000:]
            ctx["transcript_session_id"] = target_session

    if extra:
        ctx.update({k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool))})
    return ctx


@router.post("", response_model=ChatOut)
async def chat(payload: ChatIn, user: User = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)):
    conversation_id = payload.conversation_id or f"cnv_{uuid.uuid4().hex[:16]}"

    history_rows = (await db.scalars(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.id.desc()).limit(10))).all()
    history = [{"role": m.role, "content": m.content} for m in reversed(history_rows)]

    context = await build_context(db, user, payload.session_id, payload.context)
    answer, provider, latency = await llm.ask(payload.message, context, history)
    intent = llm.detect_intent(payload.message)

    db.add(ChatMessage(user_id=user.id, session_id=payload.session_id,
                       conversation_id=conversation_id, role="user",
                       content=payload.message, intent=intent))
    db.add(ChatMessage(user_id=user.id, session_id=payload.session_id,
                       conversation_id=conversation_id, role="assistant",
                       content=answer, intent=intent, provider=provider,
                       latency_ms=latency))

    return ChatOut(
        response=answer, intent=intent, provider=provider,
        conversation_id=conversation_id, latency_ms=latency,
        suggestions=llm.suggestions_for(intent),
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/history")
async def history(conversation_id: str | None = None, limit: int = Query(default=50, le=200),
                  user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(ChatMessage).where(ChatMessage.user_id == user.id)
    if conversation_id:
        stmt = stmt.where(ChatMessage.conversation_id == conversation_id)
    rows = (await db.scalars(stmt.order_by(ChatMessage.id.desc()).limit(limit))).all()
    return {
        "success": True,
        "messages": [{"role": m.role, "content": m.content, "intent": m.intent,
                      "conversation_id": m.conversation_id, "created_at": m.created_at}
                     for m in reversed(rows)],
    }


@router.get("/status")
async def sunny_status():
    provider = settings.LLM_PROVIDER.lower().strip()
    enabled = llm.provider_enabled()
    return {
        "success": True,
        "llm_enabled": enabled,
        "provider": provider if enabled else "fallback",
        "model": settings.LLM_MODEL or llm.DEFAULT_MODELS.get(provider, ""),
        "capabilities": {
            "platform_questions": True,
            "general_knowledge": enabled,
            "lecture_summary": True,
            "summary_mode": "abstractive" if enabled else "extractive",
            "grounded_attendance_data": True,
        },
    }
