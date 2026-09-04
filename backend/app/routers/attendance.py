"""Attendance records, heartbeats, proctor events, and visual analytics."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps import get_current_user, require_teacher
from app.models import (
    AttendanceRecord, AttendanceStatus, Classroom, ClassSession, EngagementSample,
    Enrollment, ProctorEvent, Role, SessionStatus, User,
)
from app.schemas import AttendanceOut, HeartbeatIn, MarkAttendanceIn, ProctorEventIn
from app.services.attendance import apply_heartbeat, apply_violation, get_or_create_record
from app.services.realtime import hub

router = APIRouter(prefix="/api/attendance", tags=["attendance"])

PRESENT_STATES = (AttendanceStatus.present.value, AttendanceStatus.late.value)


async def _hydrate(db: AsyncSession, records: list[AttendanceRecord]) -> list[AttendanceOut]:
    if not records:
        return []
    ids = {r.student_id for r in records}
    users = {u.id: u for u in (await db.scalars(select(User).where(User.id.in_(ids)))).all()}
    out = []
    for r in records:
        item = AttendanceOut.model_validate(r)
        u = users.get(r.student_id)
        if u:
            item.student_name = u.full_name
            item.roll_number = u.roll_number
        out.append(item)
    return out


@router.get("")
async def list_attendance(
    class_id: Optional[str] = None,
    session_id: Optional[str] = None,
    student_id: Optional[str] = None,
    date_key: Optional[str] = None,
    limit: int = Query(default=500, le=2000),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AttendanceRecord)
    if user.role == Role.student.value:
        stmt = stmt.where(AttendanceRecord.student_id == user.id)
    elif student_id:
        stmt = stmt.where(AttendanceRecord.student_id == student_id)
    if class_id:
        stmt = stmt.where(AttendanceRecord.class_id == class_id)
    if session_id:
        stmt = stmt.where(AttendanceRecord.session_id == session_id)
    if date_key:
        stmt = stmt.where(AttendanceRecord.date_key == date_key)

    rows = list((await db.scalars(
        stmt.order_by(AttendanceRecord.date_key.desc()).limit(limit))).all())
    items = await _hydrate(db, rows)
    present = sum(1 for r in rows if r.status in PRESENT_STATES)
    return {
        "success": True,
        "total": len(rows),
        "present": present,
        "absent": len(rows) - present,
        "rate": round(present / len(rows) * 100, 1) if rows else 0.0,
        "records": items,
    }


@router.post("/mark", status_code=201)
async def mark_attendance(payload: MarkAttendanceIn, user: User = Depends(require_teacher),
                          db: AsyncSession = Depends(get_db)):
    """Manual override for a teacher (e.g. a student whose camera failed)."""
    session = await db.get(ClassSession, payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Only the teacher who owns this class (or an admin) may manually override
    # attendance — prevents a different teacher from editing another class's records.
    classroom = await db.get(Classroom, session.class_id)
    if classroom and classroom.teacher_id != user.id and user.role != Role.admin.value:
        raise HTTPException(status_code=403,
                            detail="Only the class teacher can manually mark attendance")

    student = None
    if payload.student_id:
        student = await db.get(User, payload.student_id)
    elif payload.roll_number:
        student = await db.scalar(
            select(User).where(User.roll_number == payload.roll_number.strip().upper()))
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    record = await get_or_create_record(db, session, student, payload.method, payload.confidence)
    record.status = payload.status
    record.method = payload.method
    record.notes = payload.notes
    await db.flush()
    await hub.push(session.id, {"type": "attendance", "userId": student.id,
                                "rollNumber": student.roll_number,
                                "name": student.full_name, "status": record.status,
                                "manual": True})
    return {"success": True, "record": (await _hydrate(db, [record]))[0]}


@router.post("/heartbeat")
async def heartbeat(payload: HeartbeatIn, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    """Called every ~15s by the classroom client with gaze/attention telemetry.
    Keeps presence time honest and drives the engagement ring."""
    session = await db.get(ClassSession, payload.session_id)
    if not session or session.status != SessionStatus.live.value:
        raise HTTPException(status_code=404, detail="No live session")

    record = await db.scalar(select(AttendanceRecord).where(
        AttendanceRecord.session_id == session.id, AttendanceRecord.student_id == user.id))
    if not record:
        # The roll-number + face gates at /api/join create this row. Heartbeats
        # only continue attendance for students who were actually admitted —
        # they must never mint attendance for anyone who skipped the gate.
        cls = await db.get(Classroom, session.class_id)
        if user.role not in (Role.teacher.value, Role.admin.value) and user.id != session.host_id:
            enrolled = await db.scalar(select(Enrollment).where(
                Enrollment.class_id == session.class_id, Enrollment.student_id == user.id))
            reason = ("not_admitted: join through the class gate first"
                      if enrolled else "not_enrolled: you are not on this class roster")
            raise HTTPException(status_code=403, detail=reason)
        record = await get_or_create_record(db, session, user, "roll", 0.0)

    await apply_heartbeat(
        db, record,
        elapsed_seconds=payload.elapsed_seconds,
        engagement_score=payload.engagement_score,
        gaze_on_screen=payload.gaze_on_screen,
        eye_aspect_ratio=payload.eye_aspect_ratio,
        face_present=payload.face_present,
    )
    await db.flush()
    await hub.push(session.id, {"type": "engagement", "userId": user.id,
                                "score": record.engagement_score})
    return {"success": True, "engagement_score": record.engagement_score,
            "present_seconds": record.present_seconds, "status": record.status,
            "violations": record.violations}


@router.post("/violation", status_code=201)
async def violation(payload: ProctorEventIn, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    session = await db.get(ClassSession, payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    record = await db.scalar(select(AttendanceRecord).where(
        AttendanceRecord.session_id == session.id, AttendanceRecord.student_id == user.id))
    if not record:
        # Violations apply to admitted students' records only — same gate as
        # heartbeat, so an outsider cannot pollute a class they never joined.
        if user.role not in (Role.teacher.value, Role.admin.value) and user.id != session.host_id:
            raise HTTPException(status_code=403,
                                detail="not_admitted: no attendance record for this session")
        record = await get_or_create_record(db, session, user, "roll", 0.0)

    await apply_violation(db, record, payload.kind, payload.severity, payload.detail)
    await db.flush()

    room = hub.get(session.id)
    if room:
        await room.broadcast_to_hosts({
            "type": "violation", "userId": user.id, "name": user.full_name,
            "rollNumber": user.roll_number, "kind": payload.kind,
            "severity": payload.severity, "count": record.violations,
            "flagged": record.flagged,
        })
    return {"success": True, "violations": record.violations,
            "engagement_score": record.engagement_score, "flagged": record.flagged}


# --------------------------------------------------------------------------- #
# Analytics — everything the dashboard charts need
# --------------------------------------------------------------------------- #
analytics = APIRouter(prefix="/api/analytics", tags=["analytics"])


@analytics.get("/overview")
async def overview(class_id: Optional[str] = None, days: int = Query(default=30, le=365),
                   user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    stmt = select(AttendanceRecord).where(AttendanceRecord.date_key >= since)
    if user.role == Role.student.value:
        stmt = stmt.where(AttendanceRecord.student_id == user.id)
    if class_id:
        stmt = stmt.where(AttendanceRecord.class_id == class_id)
    rows = list((await db.scalars(stmt)).all())

    counts = defaultdict(int)
    for r in rows:
        counts[r.status] += 1
    total = len(rows) or 1
    present = counts[AttendanceStatus.present.value]
    late = counts[AttendanceStatus.late.value]
    absent = counts[AttendanceStatus.absent.value]
    avg_engagement = round(sum(r.engagement_score for r in rows) / total, 1) if rows else 0.0

    return {
        "success": True,
        "range_days": days,
        "totals": {"records": len(rows), "present": present, "late": late,
                   "absent": absent, "excused": counts[AttendanceStatus.excused.value]},
        "rate": round((present + late) / total * 100, 1),
        "avg_engagement": avg_engagement,
        "total_violations": sum(r.violations for r in rows),
        "flagged": sum(1 for r in rows if r.flagged),
        # ready-to-render donut segments
        "donut": [
            {"label": "Present", "value": present, "color": "#2ECC71"},
            {"label": "Late", "value": late, "color": "#F5A623"},
            {"label": "Absent", "value": absent, "color": "#DD0200"},
        ],
    }


@analytics.get("/weekly")
async def weekly(class_id: Optional[str] = None, weeks: int = Query(default=1, le=12),
                 user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Bar-chart series: attendance rate per weekday."""
    today = date.today()
    start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
    stmt = select(AttendanceRecord).where(AttendanceRecord.date_key >= start.strftime("%Y-%m-%d"))
    if user.role == Role.student.value:
        stmt = stmt.where(AttendanceRecord.student_id == user.id)
    if class_id:
        stmt = stmt.where(AttendanceRecord.class_id == class_id)
    rows = list((await db.scalars(stmt)).all())

    by_day: dict[str, list[AttendanceRecord]] = defaultdict(list)
    for r in rows:
        by_day[r.date_key].append(r)

    series = []
    for offset in range(7 * weeks):
        d = start + timedelta(days=offset)
        if d > today:
            break
        key = d.strftime("%Y-%m-%d")
        day_rows = by_day.get(key, [])
        present = sum(1 for r in day_rows if r.status == AttendanceStatus.present.value)
        late = sum(1 for r in day_rows if r.status == AttendanceStatus.late.value)
        absent = sum(1 for r in day_rows if r.status == AttendanceStatus.absent.value)
        total = len(day_rows)
        series.append({
            "date": key,
            "day": d.strftime("%a"),
            "total": total, "present": present, "late": late, "absent": absent,
            "rate": round((present + late) / total * 100, 1) if total else 0.0,
        })
    return {"success": True, "series": series}


@analytics.get("/heatmap")
async def heatmap(days: int = Query(default=90, le=365), student_id: Optional[str] = None,
                  user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Calendar heatmap cells: date -> 0..1 intensity."""
    target = user.id if user.role == Role.student.value else (student_id or user.id)
    since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = list((await db.scalars(select(AttendanceRecord).where(
        AttendanceRecord.student_id == target,
        AttendanceRecord.date_key >= since))).all())

    by_day: dict[str, list[AttendanceRecord]] = defaultdict(list)
    for r in rows:
        by_day[r.date_key].append(r)
    cells = []
    for key, day_rows in sorted(by_day.items()):
        good = sum(1 for r in day_rows if r.status in PRESENT_STATES)
        cells.append({"date": key, "total": len(day_rows), "present": good,
                      "intensity": round(good / len(day_rows), 2)})
    return {"success": True, "student_id": target, "cells": cells}


@analytics.get("/class/{class_id}")
async def class_breakdown(class_id: str, user: User = Depends(require_teacher),
                          db: AsyncSession = Depends(get_db)):
    """Per-student grid for the teacher dashboard: presence, engagement, violations."""
    roster = (await db.execute(
        select(User).join(Enrollment, Enrollment.student_id == User.id)
        .where(Enrollment.class_id == class_id).order_by(User.roll_number))).scalars().all()
    rows = list((await db.scalars(select(AttendanceRecord).where(
        AttendanceRecord.class_id == class_id))).all())

    per_student: dict[str, list[AttendanceRecord]] = defaultdict(list)
    for r in rows:
        per_student[r.student_id].append(r)

    students = []
    for s in roster:
        recs = per_student.get(s.id, [])
        attended = sum(1 for r in recs if r.status in PRESENT_STATES)
        students.append({
            "id": s.id, "name": s.full_name, "roll_number": s.roll_number,
            "email": s.email, "face_enrolled": s.face_enrolled,
            "sessions": len(recs),
            "attended": attended,
            "attendance_rate": round(attended / len(recs) * 100, 1) if recs else 0.0,
            "avg_engagement": round(sum(r.engagement_score for r in recs) / len(recs), 1) if recs else 0.0,
            "violations": sum(r.violations for r in recs),
            "flagged": any(r.flagged for r in recs),
            "last_seen": max((r.last_seen_at for r in recs if r.last_seen_at), default=None),
        })

    at_risk = [s for s in students if s["attendance_rate"] < 75 or s["violations"] >= 3]
    return {"success": True, "class_id": class_id, "total": len(students),
            "students": students, "at_risk": at_risk,
            "class_average": round(
                sum(s["attendance_rate"] for s in students) / len(students), 1) if students else 0.0}


@analytics.get("/live/{session_id}")
async def live_stats(session_id: str, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    """Real-time tiles for the in-class teacher panel."""
    session = await db.get(ClassSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    rows = list((await db.scalars(select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id))).all())
    roster_count = await db.scalar(select(func.count()).select_from(Enrollment)
                                   .where(Enrollment.class_id == session.class_id)) or 0
    room = hub.get(session_id)
    present = sum(1 for r in rows if r.status in PRESENT_STATES)
    return {
        "success": True,
        "session_id": session_id,
        "roster": roster_count,
        "joined": len(rows),
        "present": present,
        "absent": max(0, roster_count - present),
        "connected": len(room.participants) if room else 0,
        "avg_engagement": round(sum(r.engagement_score for r in rows) / len(rows), 1) if rows else 0,
        "violations": sum(r.violations for r in rows),
        "participants": room.snapshot() if room else [],
        "records": await _hydrate(db, rows),
    }
