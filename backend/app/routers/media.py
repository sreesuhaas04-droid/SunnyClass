"""Captions (live + persisted transcript) and class recordings."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, UploadFile,
)
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.deps import get_current_user
from app.models import Caption, ClassSession, Classroom, MeetingChatMessage, Recording, Role, User
from app.schemas import CaptionIn
from app.services import llm
from app.services.realtime import hub

router = APIRouter(prefix="/api/captions", tags=["captions"])
meetings_router = APIRouter(prefix="/api/meetings", tags=["meetings"])


@meetings_router.post("/validate")
async def validate_meeting(payload: dict, user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    """Resolve a meeting ID (+ optional passcode) to its live session *before*
    the camera/face gate, so the join page can route the student correctly."""
    code = str(payload.get("meeting_id", "")).strip().replace(" ", "")
    if not code:
        raise HTTPException(status_code=422, detail="meeting_id is required")
    session = await db.scalar(
        select(ClassSession).where(ClassSession.meeting_code == code,
                                   ClassSession.status == "live"))
    if not session:
        return {"success": True, "valid": False, "reason": "No live meeting with that ID."}
    supplied = str(payload.get("meeting_passcode", "")).strip().upper()
    stored = (session.meeting_passcode or "").strip().upper()
    if supplied != stored:
        return {"success": True, "valid": False, "reason": "Incorrect meeting passcode."}
    cls = await db.get(Classroom, session.class_id)
    return {"success": True, "valid": True,
            "session": {"id": session.id, "title": session.title,
                        "class_name": cls.name if cls else "Class"}}


@meetings_router.get("/{session_id}/chat")
async def meeting_chat_history(session_id: str, limit: int = Query(default=100, le=500, ge=1),
                               user: User = Depends(get_current_user),
                               db: AsyncSession = Depends(get_db)):
    """In-meeting chat history for a session, newest last."""
    rows = (await db.scalars(
        select(MeetingChatMessage).where(MeetingChatMessage.session_id == session_id)
        .order_by(MeetingChatMessage.id.desc()).limit(limit))).all()
    return {"success": True, "total": len(rows),
            "messages": [{"id": m.id, "senderId": m.sender_id, "senderName": m.sender_name,
                          "senderRole": m.sender_role, "text": m.text,
                          "ts": m.ts.isoformat() + "Z" if m.ts else None}
                         for m in reversed(rows)]}

STORAGE = Path(settings.STORAGE_DIR)
(STORAGE / "recordings").mkdir(parents=True, exist_ok=True)


@router.post("", status_code=201)
async def push_caption(payload: CaptionIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """Interim captions are broadcast but not stored; final ones are persisted so
    the transcript survives the class."""
    await hub.push(payload.session_id, {
        "type": "caption",
        "text": payload.text,
        "speaker": user.full_name,
        "speakerId": user.id,
        "isFinal": payload.is_final,
        "offsetMs": payload.offset_ms,
    })
    if not payload.is_final:
        return {"success": True, "stored": False}

    db.add(Caption(
        session_id=payload.session_id, speaker_id=user.id, speaker_name=user.full_name,
        text=payload.text.strip(), lang=payload.lang, offset_ms=payload.offset_ms,
        confidence=payload.confidence,
    ))
    return {"success": True, "stored": True}


@router.get("/{session_id}")
async def get_captions(session_id: str, limit: int = Query(default=1000, le=5000),
                       user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Caption).where(Caption.session_id == session_id)
                             .order_by(Caption.offset_ms, Caption.id).limit(limit))).all()
    return {
        "success": True,
        "total": len(rows),
        "captions": [{"id": c.id, "speaker": c.speaker_name, "text": c.text,
                      "offset_ms": c.offset_ms, "ts": c.ts} for c in rows],
    }


@router.get("/{session_id}/transcript", response_class=PlainTextResponse)
async def transcript(session_id: str, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Caption).where(Caption.session_id == session_id)
                             .order_by(Caption.offset_ms, Caption.id))).all()
    lines = []
    for c in rows:
        stamp = f"[{c.offset_ms // 60000:02d}:{(c.offset_ms // 1000) % 60:02d}]"
        lines.append(f"{stamp} {c.speaker_name or 'Speaker'}: {c.text}")
    return "\n".join(lines) or "(no captions recorded)"


@router.post("/{session_id}/summarize")
async def summarize(session_id: str, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    """SUNNY reads the stored transcript and produces a study summary."""
    rows = (await db.scalars(select(Caption).where(Caption.session_id == session_id)
                             .order_by(Caption.offset_ms, Caption.id))).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No captions recorded for this session")
    text = "\n".join(c.text for c in rows)[:12000]

    if not llm.provider_enabled():
        # No language model configured — produce a real extractive summary
        # rather than an apology.
        started = time.perf_counter()
        summary = llm.summarize_transcript(text)
        return {"success": True, "summary": summary, "provider": "extractive",
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "caption_lines": len(rows)}

    answer, provider, latency = await llm.ask(
        "Summarise this lecture transcript into: a 3-sentence overview, the key "
        "points as bullets, any homework or deadlines mentioned, and 3 revision "
        "questions.\n\nTRANSCRIPT:\n" + text
    )
    return {"success": True, "summary": answer, "provider": provider,
            "latency_ms": latency, "caption_lines": len(rows)}


# --------------------------------------------------------------------------- #
# Recordings
# --------------------------------------------------------------------------- #
rec_router = APIRouter(prefix="/api/recordings", tags=["recordings"])


@rec_router.get("")
async def list_recordings(class_id: Optional[str] = None, session_id: Optional[str] = None,
                          user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    stmt = select(Recording).where(Recording.status == "ready")
    if class_id:
        stmt = stmt.where(Recording.class_id == class_id)
    if session_id:
        stmt = stmt.where(Recording.session_id == session_id)
    rows = (await db.scalars(stmt.order_by(Recording.created_at.desc()))).all()
    return {
        "success": True,
        "total": len(rows),
        "recordings": [{
            "id": r.id, "session_id": r.session_id, "class_id": r.class_id,
            "filename": r.filename, "size_bytes": r.size_bytes,
            "duration_seconds": r.duration_seconds, "created_at": r.created_at,
            "url": f"/api/recordings/{r.id}/stream",
            "has_summary": bool(r.summary),
        } for r in rows],
    }


@rec_router.post("/upload", status_code=201)
async def upload_recording(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    duration_seconds: int = Form(default=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(ClassSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    rec = Recording(
        session_id=session_id, class_id=session.class_id, uploaded_by=user.id,
        filename=file.filename or f"{session_id}.webm",
        mime_type=file.content_type or "video/webm",
        duration_seconds=duration_seconds, status="uploading",
    )
    db.add(rec)
    await db.flush()

    dest = STORAGE / "recordings" / f"{rec.id}.webm"
    size = 0
    max_bytes = settings.MAX_RECORDING_MB * 1024 * 1024
    with dest.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                fh.close()
                dest.unlink(missing_ok=True)
                # Delete the DB row so it doesn't remain stuck as "uploading"
                # forever — the HTTPException causes a rollback which would
                # revert a status update, but a delete + flush before raising
                # is also rolled back. We therefore flush the delete first so
                # the row is removed within this transaction before we raise.
                await db.delete(rec)
                await db.flush()
                raise HTTPException(
                    status_code=413,
                    detail=f"Recording exceeds {settings.MAX_RECORDING_MB} MB limit")
            fh.write(chunk)

    # attach the transcript captured during the class
    caps = (await db.scalars(select(Caption).where(Caption.session_id == session_id)
                             .order_by(Caption.offset_ms, Caption.id))).all()
    rec.transcript = "\n".join(f"{c.speaker_name or 'Speaker'}: {c.text}" for c in caps) or None
    rec.size_bytes = size
    rec.status = "ready"
    await db.flush()
    return {"success": True, "recording_id": rec.id, "size_bytes": size,
            "url": f"/api/recordings/{rec.id}/stream"}


@rec_router.get("/{recording_id}/stream")
async def stream_recording(recording_id: str, db: AsyncSession = Depends(get_db)):
    rec = await db.get(Recording, recording_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    path = STORAGE / "recordings" / f"{rec.id}.webm"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Recording file missing")
    return FileResponse(path, media_type=rec.mime_type, filename=rec.filename)


@rec_router.get("/{recording_id}/transcript", response_class=PlainTextResponse)
async def recording_transcript(recording_id: str, user: User = Depends(get_current_user),
                               db: AsyncSession = Depends(get_db)):
    rec = await db.get(Recording, recording_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    return rec.transcript or "(no transcript)"
