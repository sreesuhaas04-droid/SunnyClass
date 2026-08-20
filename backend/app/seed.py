"""Seed the database with a realistic demo college: teachers, 30 students with
roll numbers, 5 classes, rosters, and 4 weeks of attendance history so every
chart on the dashboard has real data behind it.

    python -m app.seed            # seed (idempotent-ish; skips if users exist)
    python -m app.seed --reset    # drop everything and reseed
"""
from __future__ import annotations

import asyncio
import random
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import delete, select

from app.core.database import Base, SessionLocal, engine, init_models
from app.core.security import hash_password
from app.models import (
    AttendanceRecord, AttendanceStatus, Caption, ChatMessage, Classroom,
    ClassSession, Device, EngagementSample, Enrollment, FaceDescriptor,
    ProctorEvent, Recording, Role, SessionStatus, User,
)

rng = random.Random(2026)
nprng = np.random.default_rng(2026)

STUDENT_NAMES = [
    "Aarav Sharma", "Ananya Patel", "Arjun Kumar", "Diya Gupta", "Ishaan Singh",
    "Kavya Reddy", "Krishna Iyer", "Meera Nair", "Priya Verma", "Rahul Joshi",
    "Riya Chopra", "Rohan Desai", "Saanvi Rao", "Siddharth Mehta", "Tanya Agarwal",
    "Varun Bhat", "Vihaan Mishra", "Aisha Khan", "Devansh Pandey", "Nisha Saxena",
    "Aditya Kulkarni", "Sneha Kapoor", "Harsh Tiwari", "Pooja Sinha", "Yash Malhotra",
    "Simran Kaur", "Mohit Choudhary", "Anushka Das", "Karthik Menon", "Divya Thakur",
]

TEACHERS = [
    ("Dr. Ramesh Iyer", "ramesh.iyer@smartclass.edu"),
    ("Prof. Sunita Rao", "sunita.rao@smartclass.edu"),
    ("Dr. Vikram Jha", "vikram.jha@smartclass.edu"),
    ("Ms. Priya Nambiar", "priya.nambiar@smartclass.edu"),
    ("Dr. Anil Patel", "anil.patel@smartclass.edu"),
]

CLASSES = [
    ("Mathematics", "Calculus II", "Mon,Wed,Fri", "09:00", 60, "#DD0200",
     "Integration techniques, series convergence and multivariable calculus."),
    ("Physics", "Quantum Mechanics", "Tue,Thu", "10:30", 75, "#55100D",
     "Wave functions, the Schrodinger equation and quantum states."),
    ("Computer Science", "Data Structures & Algorithms", "Mon,Wed", "14:00", 90, "#8E1F1B",
     "Trees, graphs, dynamic programming and complexity analysis."),
    ("English", "Modern Literature", "Tue,Thu,Fri", "11:00", 50, "#B03A2E",
     "Contemporary world literature and critical reading."),
    ("Chemistry", "Organic Chemistry", "Wed,Fri", "15:30", 60, "#6E1410",
     "Functional groups, reaction mechanisms and stereochemistry."),
]

LECTURE_LINES = [
    "Alright everyone, let's pick up where we left off last time.",
    "Today we're covering integration by parts and when to reach for it.",
    "Remember the formula: the integral of u dv equals uv minus the integral of v du.",
    "The trick is choosing u so that its derivative gets simpler.",
    "Let's work through an example on the board together.",
    "Notice how the second term collapses once we substitute.",
    "This shows up constantly in physics, so it's worth drilling.",
    "Your homework is exercises four through twelve, due Friday.",
    "There's a quiz next Wednesday covering everything up to today.",
    "Any questions before we move on to the next section?",
]


def _descriptor(seed: int) -> list[float]:
    """A deterministic pseudo face descriptor so the demo has an enrolled gallery.
    Real descriptors come from face-api.js in the browser."""
    r = np.random.default_rng(seed)
    v = r.normal(scale=0.12, size=128).astype(np.float32)
    return [round(float(x), 6) for x in v]


async def reset() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("• dropped and recreated all tables")


async def seed() -> None:
    await init_models()
    async with SessionLocal() as db:
        if await db.scalar(select(User).limit(1)):
            print("! database already has users — run with --reset to wipe and reseed")
            return

        # --- teachers ------------------------------------------------------ #
        teachers: list[User] = []
        for name, email in TEACHERS:
            t = User(email=email, password_hash=hash_password("teach1234"),
                     full_name=name, role=Role.teacher.value,
                     department="Faculty", theme="dark")
            db.add(t)
            teachers.append(t)
        await db.flush()
        print(f"• {len(teachers)} teachers  (password: teach1234)")

        # --- students ------------------------------------------------------ #
        students: list[User] = []
        for i, name in enumerate(STUDENT_NAMES):
            roll = f"21CS{i + 1:03d}"
            handle = name.lower().replace(" ", ".").replace("dr.", "")
            s = User(
                email=f"{handle}@student.smartclass.edu",
                password_hash=hash_password("student123"),
                full_name=name, role=Role.student.value, roll_number=roll,
                department="Computer Science", section="A" if i < 15 else "B",
                year=3, theme="dark" if i % 3 else "light", face_enrolled=True,
            )
            db.add(s)
            students.append(s)
        await db.flush()

        # 2 face samples each, so the matcher has a real gallery to search
        for i, s in enumerate(students):
            base = _descriptor(1000 + i)
            db.add(FaceDescriptor(user_id=s.id, vector=base, source="enroll"))
            jitter = [round(x + float(nprng.normal(scale=0.01)), 6) for x in base]
            db.add(FaceDescriptor(user_id=s.id, vector=jitter, source="reinforce"))
        await db.flush()
        print(f"• {len(students)} students with roll numbers 21CS001–21CS030 "
              f"(password: student123), 2 face samples each")

        # --- classes + rosters --------------------------------------------- #
        classrooms: list[Classroom] = []
        for idx, (name, subject, days, at, dur, color, desc) in enumerate(CLASSES):
            c = Classroom(
                name=name, subject=subject, description=desc,
                teacher_id=teachers[idx % len(teachers)].id,
                room_code=f"{name[:4].upper().replace(' ', '')}-{2026}",
                color=color, schedule_days=days, schedule_time=at,
                duration_minutes=dur,
            )
            db.add(c)
            classrooms.append(c)
        await db.flush()

        for c in classrooms:
            roster = students if c.name != "English" else students[:25]
            for s in roster:
                db.add(Enrollment(class_id=c.id, student_id=s.id))
        await db.flush()
        print(f"• {len(classrooms)} classes with rosters")

        # --- 4 weeks of attendance history --------------------------------- #
        now = datetime.now(timezone.utc)
        sessions_made = 0
        records_made = 0
        # per-student reliability, so charts show believable variation
        reliability = {s.id: min(0.99, max(0.55, nprng.normal(0.88, 0.11))) for s in students}

        for back in range(28, 0, -1):
            day = now - timedelta(days=back)
            weekday = day.strftime("%a")
            for c in classrooms:
                if weekday not in c.schedule_days.split(","):
                    continue
                hh, mm = (int(x) for x in c.schedule_time.split(":"))
                started = day.replace(hour=hh, minute=mm, second=0, microsecond=0)
                ended = started + timedelta(minutes=c.duration_minutes)
                sess = ClassSession(
                    class_id=c.id, host_id=c.teacher_id,
                    title=f"{c.name} — {c.subject}",
                    status=SessionStatus.ended.value,
                    started_at=started, ended_at=ended,
                )
                db.add(sess)
                await db.flush()
                sessions_made += 1

                roster = (await db.scalars(select(Enrollment.student_id)
                                           .where(Enrollment.class_id == c.id))).all()
                joined = 0
                for sid in roster:
                    p = reliability[sid]
                    roll = nprng.random()
                    if roll < p - 0.08:
                        status = AttendanceStatus.present.value
                        ratio = float(nprng.uniform(0.82, 1.0))
                    elif roll < p:
                        status = AttendanceStatus.late.value
                        ratio = float(nprng.uniform(0.45, 0.8))
                    else:
                        status = AttendanceStatus.absent.value
                        ratio = 0.0

                    present_seconds = int(c.duration_minutes * 60 * ratio)
                    violations = int(max(0, nprng.poisson(0.35 if p < 0.8 else 0.08)))
                    engagement = 0 if ratio == 0 else int(
                        max(20, min(100, nprng.normal(88 if p > 0.85 else 72, 9) - violations * 6)))
                    conf = 0.0 if ratio == 0 else round(float(nprng.uniform(0.74, 0.98)), 3)
                    if ratio > 0:
                        joined += 1

                    db.add(AttendanceRecord(
                        session_id=sess.id, class_id=c.id, student_id=sid,
                        date_key=started.strftime("%Y-%m-%d"), status=status,
                        method="face" if ratio else "face", confidence=conf,
                        verifications=int(present_seconds / 45) if ratio else 0,
                        first_seen_at=started if ratio else None,
                        last_seen_at=started + timedelta(seconds=present_seconds) if ratio else None,
                        present_seconds=present_seconds,
                        engagement_score=engagement, violations=violations,
                        flagged=violations >= 3,
                    ))
                    records_made += 1

                    for _ in range(violations):
                        db.add(ProctorEvent(
                            session_id=sess.id, student_id=sid,
                            kind=rng.choice(["tab_switch", "fullscreen_exit",
                                             "window_blur", "face_lost"]),
                            severity="warning",
                            ts=started + timedelta(minutes=rng.randint(1, c.duration_minutes)),
                        ))

                sess.peak_participants = joined

                # captions for the most recent Mathematics lecture only (keeps seed fast)
                if c.name == "Mathematics" and back <= 2:
                    for i, line in enumerate(LECTURE_LINES):
                        db.add(Caption(
                            session_id=sess.id, speaker_id=c.teacher_id,
                            speaker_name=next(t.full_name for t in teachers
                                              if t.id == c.teacher_id),
                            text=line, offset_ms=i * 42_000,
                            ts=started + timedelta(seconds=i * 42),
                        ))
            await db.flush()

        print(f"• {sessions_made} past sessions, {records_made} attendance records "
              f"(4 weeks of history)")

        # --- one live session so the classroom is immediately demoable ------ #
        math = classrooms[0]
        live = ClassSession(
            class_id=math.id, host_id=math.teacher_id,
            title=f"{math.name} — {math.subject}",
            status=SessionStatus.live.value,
            started_at=now - timedelta(minutes=6),
            settings={"require_face_attendance": True, "enforce_fullscreen": True,
                      "enforce_camera": True, "allow_recording": True},
        )
        db.add(live)
        await db.flush()

        # a handful of students already checked in
        for i, s in enumerate(students[:11]):
            db.add(AttendanceRecord(
                session_id=live.id, class_id=math.id, student_id=s.id,
                date_key=now.strftime("%Y-%m-%d"),
                status=AttendanceStatus.present.value if i < 9 else AttendanceStatus.late.value,
                method="face", confidence=round(float(nprng.uniform(0.8, 0.97)), 3),
                verifications=rng.randint(2, 8),
                first_seen_at=now - timedelta(minutes=rng.randint(1, 6)),
                last_seen_at=now, present_seconds=rng.randint(120, 360),
                engagement_score=rng.randint(68, 99),
            ))
        for i, line in enumerate(LECTURE_LINES[:5]):
            db.add(Caption(session_id=live.id, speaker_id=math.teacher_id,
                           speaker_name="Dr. Ramesh Iyer", text=line,
                           offset_ms=i * 60_000))
        await db.commit()

        print(f"• 1 LIVE session for {math.name} (room code {math.room_code})")
        print("\nSign in with:")
        print("  teacher  ramesh.iyer@smartclass.edu / teach1234")
        print("  student  aarav.sharma@student.smartclass.edu / student123  (roll 21CS001)")


async def main() -> None:
    if "--reset" in sys.argv:
        await reset()
    await seed()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
