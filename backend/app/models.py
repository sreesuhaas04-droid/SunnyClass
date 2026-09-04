"""SunnyClass AI — relational schema.

Design notes
------------
* Every table uses a string primary key with a human-readable prefix so IDs are
  debuggable in logs and safe to expose to the frontend.
* Face descriptors are stored as JSON arrays of 128 floats (face-api.js output).
  Raw video never leaves the student's device — only the embedding is stored.
* Attendance is derived, not asserted: `AttendanceRecord` accumulates verified
  sightings and presence seconds, and the final status is computed at session end.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _meeting_code() -> str:
    """Zoom-style 9-digit meeting ID, e.g. 841 203 966."""
    return f"{random.randint(100, 999)}{random.randint(100, 999)}{random.randint(100, 999)}"


def _passcode() -> str:
    """6-char alphanumeric passcode, unambiguous characters only."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choices(alphabet, k=6))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, PyEnum):
    student = "student"
    teacher = "teacher"
    admin = "admin"


class AttendanceStatus(str, PyEnum):
    present = "present"
    late = "late"
    absent = "absent"
    excused = "excused"


class SessionStatus(str, PyEnum):
    scheduled = "scheduled"
    live = "live"
    ended = "ended"


# --------------------------------------------------------------------------- #
# Users & roster
# --------------------------------------------------------------------------- #
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("usr"))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=Role.student.value, index=True)

    # Roll number is the identity key for the meeting gate. Unique when present.
    roll_number: Mapped[Optional[str]] = mapped_column(String(32), unique=True, index=True)
    department: Mapped[Optional[str]] = mapped_column(String(80))
    section: Mapped[Optional[str]] = mapped_column(String(16))
    year: Mapped[Optional[int]] = mapped_column(Integer)
    phone: Mapped[Optional[str]] = mapped_column(String(24))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500))

    theme: Mapped[str] = mapped_column(String(10), default="dark")       # dark | light
    locale: Mapped[str] = mapped_column(String(10), default="en-IN")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    face_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    descriptors: Mapped[list["FaceDescriptor"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")
    enrollments: Mapped[list["Enrollment"]] = relationship(
        back_populates="student", cascade="all, delete-orphan")
    devices: Mapped[list["Device"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")


class Device(Base):
    """Multi-device support: a user may be signed in on phone + tablet + laptop."""
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("dev"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="desktop")   # phone|tablet|desktop
    label: Mapped[Optional[str]] = mapped_column(String(120))
    user_agent: Mapped[Optional[str]] = mapped_column(String(400))
    screen: Mapped[Optional[str]] = mapped_column(String(32))
    network: Mapped[Optional[str]] = mapped_column(String(16))         # 4g|3g|2g|wifi
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="devices")


class FaceDescriptor(Base):
    """A 128-dimension face-api.js embedding. Multiple samples per user improve matching."""
    __tablename__ = "face_descriptors"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("face"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    vector: Mapped[list] = mapped_column(JSON, nullable=False)
    quality: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(24), default="enroll")   # enroll | reinforce
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="descriptors")


# --------------------------------------------------------------------------- #
# Classes
# --------------------------------------------------------------------------- #
class Classroom(Base):
    __tablename__ = "classrooms"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("cls"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    room_code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    color: Mapped[str] = mapped_column(String(16), default="#DD0200")

    schedule_days: Mapped[str] = mapped_column(String(40), default="Mon,Wed,Fri")
    schedule_time: Mapped[str] = mapped_column(String(8), default="09:00")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)

    require_face_attendance: Mapped[bool] = mapped_column(Boolean, default=True)
    enforce_fullscreen: Mapped[bool] = mapped_column(Boolean, default=True)
    enforce_camera: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_recording: Mapped[bool] = mapped_column(Boolean, default=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    teacher: Mapped["User"] = relationship()
    enrollments: Mapped[list["Enrollment"]] = relationship(
        back_populates="classroom", cascade="all, delete-orphan")
    sessions: Mapped[list["ClassSession"]] = relationship(
        back_populates="classroom", cascade="all, delete-orphan")


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("class_id", "student_id", name="uq_enrollment"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("enr"))
    class_id: Mapped[str] = mapped_column(ForeignKey("classrooms.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    classroom: Mapped["Classroom"] = relationship(back_populates="enrollments")
    student: Mapped["User"] = relationship(back_populates="enrollments")


class ClassSession(Base):
    __tablename__ = "class_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("ses"))
    class_id: Mapped[str] = mapped_column(ForeignKey("classrooms.id", ondelete="CASCADE"), index=True)
    host_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[Optional[str]] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default=SessionStatus.live.value, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    peak_participants: Mapped[int] = mapped_column(Integer, default=0)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    # Zoom-style shareable credentials: "Meeting ID 9-digit" + 6-char passcode.
    meeting_code: Mapped[Optional[str]] = mapped_column(String(16), index=True,
                                                        default=lambda: _meeting_code())
    meeting_passcode: Mapped[Optional[str]] = mapped_column(String(8),
                                                            default=lambda: _passcode())

    classroom: Mapped["Classroom"] = relationship(back_populates="sessions")
    attendance: Mapped[list["AttendanceRecord"]] = relationship(
        back_populates="session", cascade="all, delete-orphan")


# --------------------------------------------------------------------------- #
# Attendance & proctoring
# --------------------------------------------------------------------------- #
class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("session_id", "student_id", name="uq_attendance"),
        Index("ix_attendance_class_date", "class_id", "date_key"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("att"))
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), index=True)
    class_id: Mapped[str] = mapped_column(ForeignKey("classrooms.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date_key: Mapped[str] = mapped_column(String(10), index=True)      # YYYY-MM-DD

    status: Mapped[str] = mapped_column(String(12), default=AttendanceStatus.absent.value, index=True)
    method: Mapped[str] = mapped_column(String(12), default="face")    # face | manual | roll
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    verifications: Mapped[int] = mapped_column(Integer, default=0)
    failed_verifications: Mapped[int] = mapped_column(Integer, default=0)

    first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    present_seconds: Mapped[int] = mapped_column(Integer, default=0)

    engagement_score: Mapped[int] = mapped_column(Integer, default=100)
    violations: Mapped[int] = mapped_column(Integer, default=0)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    session: Mapped["ClassSession"] = relationship(back_populates="attendance")
    student: Mapped["User"] = relationship()


class EngagementSample(Base):
    """Rolling gaze/attention telemetry, one row per sampling tick."""
    __tablename__ = "engagement_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    score: Mapped[int] = mapped_column(Integer, default=100)
    gaze_on_screen: Mapped[bool] = mapped_column(Boolean, default=True)
    eye_aspect_ratio: Mapped[float] = mapped_column(Float, default=0.3)
    face_present: Mapped[bool] = mapped_column(Boolean, default=True)


class ProctorEvent(Base):
    __tablename__ = "proctor_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(12), default="warning")
    detail: Mapped[Optional[str]] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# --------------------------------------------------------------------------- #
# Captions, recordings, chat
# --------------------------------------------------------------------------- #
class Caption(Base):
    __tablename__ = "captions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), index=True)
    speaker_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    speaker_name: Mapped[Optional[str]] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str] = mapped_column(String(10), default="en-IN")
    offset_ms: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.9)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _id("rec"))
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), index=True)
    class_id: Mapped[str] = mapped_column(ForeignKey("classrooms.id", ondelete="CASCADE"), index=True)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(80), default="video/webm")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="uploading")  # uploading|ready|failed
    transcript: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MeetingChatMessage(Base):
    """In-meeting text chat (separate from the SUNNY AI assistant chat)."""
    __tablename__ = "meeting_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), index=True)
    sender_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    sender_name: Mapped[str] = mapped_column(String(120))
    sender_role: Mapped[str] = mapped_column(String(16), default="student")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True, default=lambda: _id("cnv"))
    role: Mapped[str] = mapped_column(String(12))            # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[Optional[str]] = mapped_column(String(40))
    provider: Mapped[Optional[str]] = mapped_column(String(24))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
