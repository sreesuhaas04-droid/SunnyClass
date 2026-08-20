"""Classroom CRUD, roster management, and live-session lifecycle."""
import random
import string
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps import get_current_user, require_teacher
from app.models import (
    Classroom, ClassSession, Enrollment, Role, SessionStatus, User, utcnow,
)
from app.schemas import ClassroomIn, ClassroomOut, EnrollIn, SessionOut, SessionStartIn
from app.services.attendance import finalize_session
from app.services.realtime import hub

router = APIRouter(prefix="/api/classes", tags=["classes"])


def _room_code(name: str) -> str:
    prefix = "".join(c for c in name.upper() if c.isalpha())[:4] or "CLASS"
    return f"{prefix}-{''.join(random.choices(string.digits, k=4))}"


async def _decorate(db: AsyncSession, cls: Classroom) -> ClassroomOut:
    out = ClassroomOut.model_validate(cls)
    teacher = await db.get(User, cls.teacher_id)
    out.teacher_name = teacher.full_name if teacher else None
    out.student_count = await db.scalar(
        select(func.count()).select_from(Enrollment).where(Enrollment.class_id == cls.id)
    ) or 0
    live = await db.scalar(
        select(ClassSession).where(
            ClassSession.class_id == cls.id,
            ClassSession.status == SessionStatus.live.value,
        ).order_by(ClassSession.started_at.desc()).limit(1)
    )
    out.live_session_id = live.id if live else None
    out.status = "live" if live else "upcoming"
    return out


@router.get("")
async def list_classes(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    mine: bool = Query(default=True),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Teachers see the classes they own; students see the classes they're enrolled in."""
    if user.role in (Role.teacher.value, Role.admin.value) and mine:
        stmt = select(Classroom).where(
            Classroom.teacher_id == user.id, Classroom.is_archived.is_(False))
    elif mine:
        stmt = (select(Classroom)
                .join(Enrollment, Enrollment.class_id == Classroom.id)
                .where(Enrollment.student_id == user.id, Classroom.is_archived.is_(False)))
    else:
        stmt = select(Classroom).where(Classroom.is_archived.is_(False))

    rows = (await db.scalars(stmt.order_by(Classroom.schedule_time))).all()
    items = [await _decorate(db, c) for c in rows]
    if status_filter:
        items = [i for i in items if i.status == status_filter]
    return {"success": True, "total": len(items), "classrooms": items}


@router.post("", status_code=201)
async def create_class(payload: ClassroomIn, user: User = Depends(require_teacher),
                       db: AsyncSession = Depends(get_db)):
    cls = Classroom(teacher_id=user.id, room_code=_room_code(payload.name),
                    **payload.model_dump())
    db.add(cls)
    await db.flush()
    return {"success": True, "classroom": await _decorate(db, cls)}


@router.get("/{class_id}")
async def get_class(class_id: str, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    cls = await db.get(Classroom, class_id)
    if not cls:
        cls = await db.scalar(select(Classroom).where(Classroom.room_code == class_id.upper()))
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found")
    return {"success": True, "classroom": await _decorate(db, cls)}


@router.get("/{class_id}/roster")
async def roster(class_id: str, user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(User, Enrollment)
        .join(Enrollment, Enrollment.student_id == User.id)
        .where(Enrollment.class_id == class_id)
        .order_by(User.roll_number)
    )).all()
    return {
        "success": True,
        "total": len(rows),
        "students": [
            {"id": u.id, "name": u.full_name, "roll_number": u.roll_number,
             "email": u.email, "section": u.section, "face_enrolled": u.face_enrolled,
             "enrolled_at": e.enrolled_at}
            for u, e in rows
        ],
    }


@router.post("/{class_id}/enroll")
async def enroll(class_id: str, payload: EnrollIn, user: User = Depends(require_teacher),
                 db: AsyncSession = Depends(get_db)):
    cls = await db.get(Classroom, class_id)
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found")

    targets: list[User] = []
    if payload.student_ids:
        targets += list((await db.scalars(
            select(User).where(User.id.in_(payload.student_ids)))).all())
    if payload.roll_numbers:
        rolls = [r.strip().upper() for r in payload.roll_numbers]
        targets += list((await db.scalars(
            select(User).where(User.roll_number.in_(rolls)))).all())
    if payload.emails:
        emails = [e.lower() for e in payload.emails]
        targets += list((await db.scalars(
            select(User).where(User.email.in_(emails)))).all())

    added, skipped = [], []
    seen = set()
    for student in targets:
        if student.id in seen:
            continue
        seen.add(student.id)
        exists = await db.scalar(select(Enrollment).where(
            Enrollment.class_id == class_id, Enrollment.student_id == student.id))
        if exists:
            skipped.append(student.roll_number or student.email)
            continue
        db.add(Enrollment(class_id=class_id, student_id=student.id))
        added.append(student.roll_number or student.email)
    await db.flush()
    return {"success": True, "added": added, "already_enrolled": skipped}


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
sessions_router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@sessions_router.post("/start", status_code=201)
async def start_session(payload: SessionStartIn, user: User = Depends(require_teacher),
                        db: AsyncSession = Depends(get_db)):
    cls = await db.get(Classroom, payload.class_id)
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found")
    if cls.teacher_id != user.id and user.role != Role.admin.value:
        raise HTTPException(status_code=403, detail="Only the class teacher can start this session")

    live = await db.scalar(select(ClassSession).where(
        ClassSession.class_id == cls.id, ClassSession.status == SessionStatus.live.value))
    if live:
        return {"success": True, "already_live": True, "session": SessionOut.model_validate(live)}

    session = ClassSession(
        class_id=cls.id,
        host_id=user.id,
        title=payload.title or f"{cls.name} — {cls.subject}",
        settings={
            "require_face_attendance": cls.require_face_attendance,
            "enforce_fullscreen": cls.enforce_fullscreen,
            "enforce_camera": cls.enforce_camera,
            "allow_recording": cls.allow_recording,
        },
    )
    db.add(session)
    await db.flush()
    return {"success": True, "session": SessionOut.model_validate(session)}


@sessions_router.get("/live")
async def live_sessions(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.role in (Role.teacher.value, Role.admin.value):
        stmt = (select(ClassSession, Classroom)
                .join(Classroom, Classroom.id == ClassSession.class_id)
                .where(ClassSession.status == SessionStatus.live.value,
                       Classroom.teacher_id == user.id))
    else:
        stmt = (select(ClassSession, Classroom)
                .join(Classroom, Classroom.id == ClassSession.class_id)
                .join(Enrollment, Enrollment.class_id == Classroom.id)
                .where(ClassSession.status == SessionStatus.live.value,
                       Enrollment.student_id == user.id))
    rows = (await db.execute(stmt)).all()
    return {
        "success": True,
        "sessions": [
            {"session_id": s.id, "class_id": c.id, "class_name": c.name,
             "subject": c.subject, "room_code": c.room_code, "title": s.title,
             "started_at": s.started_at,
             "participants": len(hub.room(s.id).participants)}
            for s, c in rows
        ],
    }


@sessions_router.get("/{session_id}")
async def get_session(session_id: str, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    session = await db.get(ClassSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    cls = await db.get(Classroom, session.class_id)
    room = hub.get(session_id)
    return {
        "success": True,
        "session": SessionOut.model_validate(session),
        "classroom": await _decorate(db, cls) if cls else None,
        "participants": room.snapshot() if room else [],
    }


@sessions_router.post("/{session_id}/end")
async def end_session(session_id: str, user: User = Depends(require_teacher),
                      db: AsyncSession = Depends(get_db)):
    session = await db.get(ClassSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status == SessionStatus.ended.value:
        return {"success": True, "already_ended": True}

    # Only the class teacher (or an admin) may end a session — mirrors the
    # guard in start_session so a different teacher cannot hijack a live class.
    cls = await db.get(Classroom, session.class_id)
    if cls and cls.teacher_id != user.id and user.role != Role.admin.value:
        raise HTTPException(status_code=403,
                            detail="Only the class teacher can end this session")

    room = hub.get(session_id)
    session.peak_participants = max(session.peak_participants, room.peak if room else 0)
    session.status = SessionStatus.ended.value
    session.ended_at = utcnow()
    await db.flush()

    summary = await finalize_session(db, session)
    await hub.push(session_id, {"type": "class-ended", "summary": summary})
    hub.close(session_id)
    return {"success": True, "summary": summary}
