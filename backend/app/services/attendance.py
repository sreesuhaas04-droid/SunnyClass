"""Attendance derivation.

Attendance is not a single boolean set at join time — it accumulates:

  join  -> record created (status derived from lateness)
  every heartbeat (~45s) -> present_seconds += delta, verifications += 1
  proctor violation      -> engagement penalty, violation counter
  session end            -> final status recomputed from presence ratio

That makes the number on the dashboard defensible: a student who joins and
immediately walks away does not stay 'present'.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    AttendanceRecord, AttendanceStatus, ClassSession, EngagementSample,
    ProctorEvent, User, utcnow,
)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def derive_join_status(session: ClassSession, now: Optional[datetime] = None) -> str:
    now = now or utcnow()
    started = _aware(session.started_at) or now
    minutes_late = (now - started).total_seconds() / 60.0
    if minutes_late > settings.LATE_AFTER_MINUTES:
        return AttendanceStatus.late.value
    return AttendanceStatus.present.value


async def get_or_create_record(
    db: AsyncSession,
    session: ClassSession,
    student: User,
    method: str = "face",
    confidence: float = 0.0,
) -> AttendanceRecord:
    existing = await db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.session_id == session.id,
            AttendanceRecord.student_id == student.id,
        )
    )
    now = utcnow()
    if existing:
        existing.last_seen_at = now
        if confidence > existing.confidence:
            existing.confidence = confidence
        return existing

    started = _aware(session.started_at) or now
    record = AttendanceRecord(
        session_id=session.id,
        class_id=session.class_id,
        student_id=student.id,
        date_key=started.strftime("%Y-%m-%d"),
        status=derive_join_status(session, now),
        method=method,
        confidence=confidence,
        verifications=1 if method == "face" else 0,
        first_seen_at=now,
        last_seen_at=now,
        engagement_score=100,
    )
    db.add(record)
    await db.flush()
    return record


async def record_verification(
    db: AsyncSession,
    record: AttendanceRecord,
    confidence: float,
    success: bool = True,
) -> AttendanceRecord:
    now = utcnow()
    if success:
        record.verifications += 1
        record.last_seen_at = now
        record.confidence = round(
            (record.confidence * (record.verifications - 1) + confidence) / record.verifications, 4
        )
        if record.status == AttendanceStatus.absent.value:
            record.status = AttendanceStatus.present.value
    else:
        record.failed_verifications += 1
        if record.failed_verifications >= 3:
            record.flagged = True
    return record


async def apply_heartbeat(
    db: AsyncSession,
    record: AttendanceRecord,
    *,
    elapsed_seconds: int,
    engagement_score: Optional[int],
    gaze_on_screen: bool,
    eye_aspect_ratio: float,
    face_present: bool,
) -> AttendanceRecord:
    # Cap the credited interval so a client cannot inflate presence time.
    credited = max(0, min(elapsed_seconds, settings.VERIFY_INTERVAL_SECONDS * 2))
    record.present_seconds += credited
    record.last_seen_at = utcnow()

    if engagement_score is not None:
        # smooth to avoid a single blink tanking the score
        record.engagement_score = int(round(record.engagement_score * 0.7 + engagement_score * 0.3))
        record.engagement_score = max(0, min(100, record.engagement_score))

    db.add(EngagementSample(
        session_id=record.session_id,
        student_id=record.student_id,
        score=record.engagement_score,
        gaze_on_screen=gaze_on_screen,
        eye_aspect_ratio=eye_aspect_ratio,
        face_present=face_present,
    ))
    return record


async def apply_violation(
    db: AsyncSession,
    record: AttendanceRecord,
    kind: str,
    severity: str = "warning",
    detail: Optional[str] = None,
) -> AttendanceRecord:
    record.violations += 1
    penalty = settings.VIOLATION_ENGAGEMENT_PENALTY * (2 if severity == "critical" else 1)
    record.engagement_score = max(0, record.engagement_score - penalty)
    if record.violations >= settings.MAX_VIOLATIONS_BEFORE_FLAG:
        record.flagged = True
    db.add(ProctorEvent(
        session_id=record.session_id,
        student_id=record.student_id,
        kind=kind,
        severity=severity,
        detail=detail,
    ))
    return record


async def finalize_session(db: AsyncSession, session: ClassSession) -> dict:
    """Recompute final statuses when a class ends, and create absent rows for
    every enrolled student who never showed up."""
    from app.models import Enrollment  # local import avoids a cycle at module load

    now = utcnow()
    started = _aware(session.started_at) or now
    ended = _aware(session.ended_at) or now
    total_seconds = max(1, int((ended - started).total_seconds()))

    records = list((await db.scalars(
        select(AttendanceRecord).where(AttendanceRecord.session_id == session.id)
    )).all())
    seen = {r.student_id for r in records}

    for r in records:
        ratio = r.present_seconds / total_seconds
        if r.status != AttendanceStatus.excused.value:
            if ratio < settings.PRESENT_MIN_PRESENCE_RATIO * 0.35:
                r.status = AttendanceStatus.absent.value
                r.notes = (r.notes or "") + f" auto: presence {ratio:.0%} below floor."
            elif ratio < settings.PRESENT_MIN_PRESENCE_RATIO:
                r.status = AttendanceStatus.late.value
                r.notes = (r.notes or "") + f" auto: partial presence {ratio:.0%}."

    roster = list((await db.scalars(
        select(Enrollment).where(Enrollment.class_id == session.class_id)
    )).all())
    created = 0
    for e in roster:
        if e.student_id in seen:
            continue
        db.add(AttendanceRecord(
            session_id=session.id,
            class_id=session.class_id,
            student_id=e.student_id,
            date_key=started.strftime("%Y-%m-%d"),
            status=AttendanceStatus.absent.value,
            method="face",
            confidence=0.0,
            engagement_score=0,
        ))
        created += 1

    await db.flush()
    return {
        "session_id": session.id,
        "duration_seconds": total_seconds,
        "records_updated": len(records),
        "absent_created": created,
    }
