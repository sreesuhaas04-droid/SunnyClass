"""Face enrollment, verification, and the roll-number-gated join endpoint.

Admission rule (the requirement: "the meeting should allow students who are in
database recognised roll number"):

  1. Caller must hold a valid JWT.
  2. Their user row must carry a roll_number that exists in the DB.
  3. That roll_number must be enrolled in the classroom being joined.
  4. If the classroom requires face attendance, the submitted descriptor must
     match THIS user's enrolled gallery above the confidence threshold — a
     descriptor that matches a different roll number is rejected as an
     impersonation attempt and logged.

Only when all four pass is a join granted, an attendance row opened, and the
ICE/WS credentials returned.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.deps import get_current_user
from app.models import (
    AttendanceRecord, Classroom, ClassSession, Device, Enrollment, FaceDescriptor,
    ProctorEvent, Role, SessionStatus, User,
)
from app.schemas import (
    ClassroomOut, FaceEnrollIn, FaceVerifyIn, FaceVerifyOut, JoinIn, JoinOut,
    SessionOut,
)
from app.services import face as face_service
from app.services.attendance import get_or_create_record, record_verification
from app.services.realtime import hub, profile_for

router = APIRouter(prefix="/api/face", tags=["face"])


async def _gallery(db: AsyncSession, user_ids: Optional[list[str]] = None):
    stmt = select(FaceDescriptor.user_id, FaceDescriptor.vector)
    if user_ids is not None:
        if not user_ids:
            return []
        stmt = stmt.where(FaceDescriptor.user_id.in_(user_ids))
    return [(uid, vec) for uid, vec in (await db.execute(stmt)).all()]


@router.post("/enroll", status_code=201)
async def enroll_face(payload: FaceEnrollIn, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """Store one or more 128-d descriptors for the signed-in user.

    Several samples (different angles/lighting) materially improve recall, so the
    frontend captures 3 by default.
    """
    # Validate all descriptors before touching the DB.
    validated = []
    for vec in payload.descriptors:
        try:
            validated.append(face_service.validate_descriptor(vec))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # Guard against enrolling a face that already belongs to someone else.
    # Fetch the gallery BEFORE adding any new rows so we don't accidentally
    # compare against our own just-added (but unflushed) descriptors.
    others = await _gallery(db, None)
    others = [(uid, v) for uid, v in others if uid != user.id]
    if others:
        clash = face_service.match_descriptor(payload.descriptors[0], others)
        if clash.matched:
            raise HTTPException(
                status_code=409,
                detail="This face is already enrolled under a different roll number.",
            )

    stored = 0
    for arr in validated:
        db.add(FaceDescriptor(user_id=user.id, vector=[float(x) for x in arr],
                              quality=payload.quality, source="enroll"))
        stored += 1

    user.face_enrolled = True
    db.add(user)
    await db.flush()
    total = len((await db.scalars(
        select(FaceDescriptor).where(FaceDescriptor.user_id == user.id))).all())
    return {"success": True, "stored": stored, "total_samples": total,
            "threshold": settings.FACE_MATCH_THRESHOLD}


@router.get("/status")
async def face_status(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    samples = (await db.scalars(
        select(FaceDescriptor).where(FaceDescriptor.user_id == user.id))).all()
    return {
        "success": True,
        "enrolled": bool(samples),
        "samples": len(samples),
        "roll_number": user.roll_number,
        "threshold": settings.FACE_MATCH_THRESHOLD,
        "min_confidence": settings.FACE_ATTENDANCE_MIN_CONFIDENCE,
    }


@router.delete("/enroll")
async def reset_face(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(
        select(FaceDescriptor).where(FaceDescriptor.user_id == user.id))).all()
    for r in rows:
        await db.delete(r)
    user.face_enrolled = False
    db.add(user)
    return {"success": True, "removed": len(rows)}


@router.post("/verify", response_model=FaceVerifyOut)
async def verify_face(payload: FaceVerifyIn, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """Continuous in-class verification. Matches against the class roster (not the
    whole user table) so the search space stays small and latency stays low."""
    session: Optional[ClassSession] = None
    scope_ids: Optional[list[str]] = None

    if payload.session_id:
        session = await db.get(ClassSession, payload.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        roster = (await db.scalars(
            select(Enrollment.student_id).where(Enrollment.class_id == session.class_id))).all()
        scope_ids = list(roster)
    elif payload.scope == "self":
        scope_ids = [user.id]

    # scope="self" always wins — even when session_id is provided — so the
    # continuous self-verify path cannot be forced to match against the full roster.
    if payload.scope == "self":
        scope_ids = [user.id]

    gallery = await _gallery(db, scope_ids)
    try:
        match = face_service.match_descriptor(payload.descriptor, gallery)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    out = FaceVerifyOut(
        matched=match.matched,
        confidence=match.confidence,
        distance=match.distance,
        threshold=settings.FACE_MATCH_THRESHOLD,
    )

    if match.matched and match.user_id:
        matched_user = await db.get(User, match.user_id)
        out.user_id = matched_user.id
        out.full_name = matched_user.full_name
        out.roll_number = matched_user.roll_number

        # Impersonation guard: the face must belong to the signed-in account.
        if matched_user.id != user.id:
            out.matched = False
            out.status = "identity_mismatch"
            if session:
                db.add(ProctorEvent(
                    session_id=session.id, student_id=user.id, kind="face_lost",
                    severity="critical",
                    detail=f"Descriptor matched {matched_user.roll_number}, not the signed-in account.",
                ))
            return out

        if session and session.status == SessionStatus.live.value:
            record = await get_or_create_record(db, session, user, "face", match.confidence)
            await record_verification(db, record, match.confidence, success=True)
            out.attendance_marked = True
            out.status = record.status
            await hub.push(session.id, {
                "type": "attendance",
                "userId": user.id,
                "rollNumber": user.roll_number,
                "name": user.full_name,
                "status": record.status,
                "confidence": match.confidence,
                "verifications": record.verifications,
            })
    else:
        out.status = "no_match"
        if session:
            record = await db.scalar(select(AttendanceRecord).where(
                AttendanceRecord.session_id == session.id,
                AttendanceRecord.student_id == user.id,
            ))
            if record:
                await record_verification(db, record, 0.0, success=False)

    return out


# --------------------------------------------------------------------------- #
# Join gate
# --------------------------------------------------------------------------- #
join_router = APIRouter(prefix="/api/join", tags=["join"])


@join_router.post("", response_model=JoinOut)
async def join_class(payload: JoinIn, request: Request,
                     user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    # --- resolve the target session -------------------------------------- #
    session: Optional[ClassSession] = None
    if payload.session_id:
        session = await db.get(ClassSession, payload.session_id)
    elif payload.room_code:
        cls = await db.scalar(
            select(Classroom).where(Classroom.room_code == payload.room_code.strip().upper()))
        if cls:
            session = await db.scalar(
                select(ClassSession)
                .where(ClassSession.class_id == cls.id,
                       ClassSession.status == SessionStatus.live.value)
                .order_by(ClassSession.started_at.desc()))
            if not session:
                return JoinOut(success=True, admitted=False,
                               reason=f"{cls.name} has no live session right now.")
    if not session:
        return JoinOut(success=True, admitted=False, reason="No such class or room code.")
    if session.status != SessionStatus.live.value:
        return JoinOut(success=True, admitted=False, reason="That class has already ended.")

    classroom = await db.get(Classroom, session.class_id)
    is_host = user.id == session.host_id or user.role in (Role.teacher.value, Role.admin.value)

    # --- gate 1: roll number must exist in the DB ------------------------- #
    if not is_host:
        if not user.roll_number:
            return JoinOut(success=True, admitted=False,
                           reason="Your account has no roll number on file. Ask your "
                                  "teacher to add you to the roster.")
        if payload.roll_number and payload.roll_number.strip().upper() != user.roll_number:
            return JoinOut(success=True, admitted=False,
                           reason="The roll number you entered does not match your account.")

        # --- gate 2: must be on this class's roster ----------------------- #
        enrolled = await db.scalar(select(Enrollment).where(
            Enrollment.class_id == classroom.id, Enrollment.student_id == user.id))
        if not enrolled:
            return JoinOut(
                success=True, admitted=False,
                reason=f"Roll number {user.roll_number} is not enrolled in "
                       f"{classroom.name}.")

    # --- gate 3: face must match this account ----------------------------- #
    identity: dict = {"userId": user.id, "name": user.full_name,
                      "rollNumber": user.roll_number, "role": user.role,
                      "verified": False, "confidence": 0.0}
    confidence = 0.0

    if classroom.require_face_attendance and not is_host:
        if not payload.descriptor:
            return JoinOut(success=True, admitted=False, reason="face_required",
                           classroom=ClassroomOut.model_validate(classroom))
        roster_ids = list((await db.scalars(
            select(Enrollment.student_id).where(Enrollment.class_id == classroom.id))).all())
        gallery = await _gallery(db, roster_ids)
        if not gallery:
            return JoinOut(success=True, admitted=False, reason="enrollment_required")

        try:
            match = face_service.match_descriptor(payload.descriptor, gallery)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        if not match.matched:
            return JoinOut(
                success=True, admitted=False,
                reason="Face not recognised against the class roster. Move into "
                       "better light and try again.")
        if match.user_id != user.id:
            other = await db.get(User, match.user_id)
            db.add(ProctorEvent(
                session_id=session.id, student_id=user.id, kind="multiple_faces",
                severity="critical",
                detail=f"Join attempt: face matched {other.roll_number if other else '?'} "
                       f"while signed in as {user.roll_number}."))
            return JoinOut(success=True, admitted=False,
                           reason="The face on camera belongs to a different roll number.")
        confidence = match.confidence
        identity.update(verified=True, confidence=confidence, distance=match.distance)

    # --- admitted: open the attendance record ----------------------------- #
    record = None
    if not is_host:
        record = await get_or_create_record(
            db, session, user, method="face" if confidence else "roll", confidence=confidence)

    if payload.device:
        db.add(Device(user_id=user.id, **payload.device.model_dump()))

    await db.flush()

    room = hub.get(session.id)
    participants = len(room.participants) if room else 0
    quality = profile_for(payload.device.network if payload.device else None, participants + 1)

    scheme = "wss" if request.url.scheme == "https" else "ws"
    ws_url = f"{scheme}://{request.url.netloc}/ws/class/{session.id}"

    return JoinOut(
        success=True,
        admitted=True,
        session=SessionOut.model_validate(session),
        classroom=ClassroomOut.model_validate(classroom),
        attendance_id=record.id if record else None,
        identity=identity,
        ice_servers=settings.ice_servers,
        ws_url=ws_url,
        quality_profile=quality,
    )
