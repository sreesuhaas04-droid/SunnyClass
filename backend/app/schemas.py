"""Pydantic request/response contracts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------- auth --
class RegisterIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    role: Literal["student", "teacher"] = "student"
    roll_number: Optional[str] = Field(default=None, max_length=32)
    department: Optional[str] = None
    section: Optional[str] = None
    year: Optional[int] = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(ORMModel):
    id: str
    email: EmailStr
    full_name: str
    role: str
    roll_number: Optional[str] = None
    department: Optional[str] = None
    section: Optional[str] = None
    year: Optional[int] = None
    theme: str = "dark"
    face_enrolled: bool = False
    avatar_url: Optional[str] = None
    created_at: Optional[datetime] = None


class TokenOut(BaseModel):
    success: bool = True
    token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class ThemeIn(BaseModel):
    theme: Literal["dark", "light"]


class DeviceIn(BaseModel):
    kind: Literal["phone", "tablet", "desktop"] = "desktop"
    label: Optional[str] = None
    user_agent: Optional[str] = None
    screen: Optional[str] = None
    network: Optional[str] = None


# ---------------------------------------------------------------- classroom --
class ClassroomIn(BaseModel):
    name: str
    subject: str
    description: Optional[str] = None
    schedule_days: str = "Mon,Wed,Fri"
    schedule_time: str = "09:00"
    duration_minutes: int = 60
    color: str = "#DD0200"
    require_face_attendance: bool = True
    enforce_fullscreen: bool = True
    enforce_camera: bool = True
    allow_recording: bool = True


class ClassroomOut(ORMModel):
    id: str
    name: str
    subject: str
    description: Optional[str] = None
    teacher_id: str
    room_code: str
    color: str
    schedule_days: str
    schedule_time: str
    duration_minutes: int
    require_face_attendance: bool
    enforce_fullscreen: bool
    enforce_camera: bool
    allow_recording: bool
    created_at: Optional[datetime] = None
    # computed
    teacher_name: Optional[str] = None
    student_count: int = 0
    live_session_id: Optional[str] = None
    status: str = "upcoming"


class EnrollIn(BaseModel):
    roll_numbers: Optional[list[str]] = None
    student_ids: Optional[list[str]] = None
    emails: Optional[list[EmailStr]] = None


# ------------------------------------------------------------------ session --
class SessionStartIn(BaseModel):
    class_id: str
    title: Optional[str] = None


class SessionOut(ORMModel):
    id: str
    class_id: str
    host_id: str
    title: Optional[str] = None
    status: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    peak_participants: int = 0
    settings: dict[str, Any] = {}


class JoinIn(BaseModel):
    """Roll-number gated join. Face descriptor is optional but required when the
    classroom has require_face_attendance enabled."""
    room_code: Optional[str] = None
    session_id: Optional[str] = None
    meeting_id: Optional[str] = None
    meeting_passcode: Optional[str] = None
    roll_number: Optional[str] = None
    descriptor: Optional[list[float]] = None
    device: Optional[DeviceIn] = None


class JoinOut(BaseModel):
    success: bool
    admitted: bool
    reason: Optional[str] = None
    session: Optional[SessionOut] = None
    classroom: Optional[ClassroomOut] = None
    attendance_id: Optional[str] = None
    identity: Optional[dict[str, Any]] = None
    ice_servers: list[dict[str, Any]] = []
    ws_url: Optional[str] = None
    quality_profile: Optional[dict[str, Any]] = None


# --------------------------------------------------------------------- face --
class FaceEnrollIn(BaseModel):
    descriptors: list[list[float]] = Field(min_length=1)
    quality: float = 1.0
    reenrol: bool = False      # recovery path: replace THIS user's stale gallery


class FaceVerifyIn(BaseModel):
    descriptor: list[float]
    session_id: Optional[str] = None
    scope: Literal["roster", "self", "global"] = "roster"


class FaceVerifyOut(BaseModel):
    success: bool = True
    matched: bool
    user_id: Optional[str] = None
    full_name: Optional[str] = None
    roll_number: Optional[str] = None
    confidence: float = 0.0
    distance: float = 1.0
    threshold: float = 0.0
    attendance_marked: bool = False
    status: Optional[str] = None


# --------------------------------------------------------------- attendance --
class MarkAttendanceIn(BaseModel):
    session_id: str
    student_id: Optional[str] = None
    roll_number: Optional[str] = None
    status: Literal["present", "late", "absent", "excused"] = "present"
    method: Literal["face", "manual", "roll"] = "manual"
    confidence: float = 1.0
    notes: Optional[str] = None


class AttendanceOut(ORMModel):
    id: str
    session_id: str
    class_id: str
    student_id: str
    date_key: str
    status: str
    method: str
    confidence: float
    verifications: int
    present_seconds: int
    engagement_score: int
    violations: int
    flagged: bool
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    student_name: Optional[str] = None
    roll_number: Optional[str] = None


class HeartbeatIn(BaseModel):
    session_id: str
    engagement_score: Optional[int] = None
    gaze_on_screen: bool = True
    eye_aspect_ratio: float = 0.3
    face_present: bool = True
    elapsed_seconds: int = 15


class ProctorEventIn(BaseModel):
    session_id: str
    kind: Literal[
        "tab_switch", "fullscreen_exit", "window_blur", "face_lost",
        "multiple_faces", "camera_off", "devtools", "copy_paste",
    ]
    severity: Literal["info", "warning", "critical"] = "warning"
    detail: Optional[str] = None


# ------------------------------------------------------------------ caption --
class CaptionIn(BaseModel):
    session_id: str
    text: str
    lang: str = "en-IN"
    offset_ms: int = 0
    confidence: float = 0.9
    is_final: bool = True


# --------------------------------------------------------------------- chat --
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None
    context: Optional[dict[str, Any]] = None


class ChatOut(BaseModel):
    success: bool = True
    response: str
    intent: str = "general"
    provider: str = "fallback"
    conversation_id: str
    latency_ms: int = 0
    suggestions: list[str] = []
    timestamp: datetime
