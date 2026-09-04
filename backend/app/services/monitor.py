"""SUNNY session monitor — the always-on supervisor for a live class.

While a meeting runs, this loop (started when a session goes live) watches
every admitted student and:

  * recomputes attendance status from presence ratio mid-meeting (a student
    who joined and walked away is downgraded *during* class, not just at end)
  * flags face-verification staleness (no successful verify within N minutes)
  * flags low engagement (sustained under the threshold) and missing-in-action
    students (no heartbeats for M minutes)
  * pushes every finding to the teacher's classroom over the WS hub
  * writes proctor events so the audit trail survives the meeting

It is per-session, in-memory and cancelled when the session ends — matching the
single-worker hub design.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import AttendanceRecord, ClassSession, ProctorEvent, SessionStatus, User
from app.services import attendance as attendance_service
from app.services.realtime import hub

log = logging.getLogger("sunnyclass.monitor")

CHECK_INTERVAL = 60           # seconds between sweeps
ENGAGEMENT_LOW = 40           # sustained engagement under this → at-risk
MIA_AFTER = 180               # no heartbeat for this long → missing


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class SessionMonitor:
    """Watches one live session and reports to the teacher."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.task: asyncio.Task | None = None
        self.last_alert: dict[str, tuple[str, float]] = {}   # dedup (kind, time)

    def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.task = asyncio.create_task(self._run(), name=f"monitor-{self.session_id}")
        log.info("monitor started for %s", self.session_id)

    def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        log.info("monitor stopped for %s", self.session_id)

    # ------------------------------------------------------------------ #
    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(CHECK_INTERVAL)
                try:
                    await self._sweep()
                except Exception:                       # never kill the loop
                    log.exception("monitor sweep failed for %s", self.session_id)
        except asyncio.CancelledError:
            pass

    async def _sweep(self) -> None:
        async with SessionLocal() as db:
            session = await db.get(ClassSession, self.session_id)
            if not session or session.status != SessionStatus.live.value:
                return                                   # reaped elsewhere

            records = (await db.scalars(select(AttendanceRecord).where(
                AttendanceRecord.session_id == self.session_id))).all()

            started = _aware(session.started_at)
            assert started is not None
            total = max(1.0, (datetime.now(timezone.utc) - started).total_seconds())

            findings: list[dict[str, Any]] = []
            for r in records:
                student = await db.get(User, r.student_id)
                if not student:
                    continue

                ratio = (r.present_seconds or 0) / total
                alerts: list[dict[str, Any]] = []

                # 1. face verification freshness
                if (r.verifications or 0) == 0:
                    alerts.append({"kind": "face_never", "severity": "warning",
                                   "text": "Face never verified this class"})
                elif (r.failed_verifications or 0) >= 3:
                    alerts.append({"kind": "face_failures", "severity": "warning",
                                   "text": f"{r.failed_verifications} failed face checks"})

                # 2. missing in action (heartbeat gap)
                if r.last_seen_at:
                    seen = _aware(r.last_seen_at)
                    gap = (datetime.now(timezone.utc) - seen).total_seconds() if seen else 1e9
                    if gap > MIA_AFTER:
                        alerts.append({"kind": "mia", "severity": "critical",
                                       "text": f"No signal for {int(gap // 60)} min"})
                else:
                    alerts.append({"kind": "mia", "severity": "critical",
                                   "text": "Never seen since joining"})

                # 3. engagement
                if (r.engagement_score or 100) < ENGAGEMENT_LOW:
                    alerts.append({"kind": "low_engagement", "severity": "warning",
                                   "text": f"Engagement {int(r.engagement_score or 0)}%"})

                # 4. live presence-ratio status recompute (mid-meeting)
                new_status = attendance_service.derive_status(ratio, r.status)
                if new_status != r.status:
                    r.status = new_status
                    alerts.append({"kind": "presence_" + new_status, "severity": "warning",
                                   "text": f"Status auto-set to {new_status} "
                                           f"(presence {int(ratio * 100)}%)"})

                for a in alerts:
                    self._emit(db, r, student, a, findings)

            if findings:
                await db.commit()
                room = hub.get(self.session_id)
                if room:
                    await room.broadcast_to_hosts({"type": "monitor", "findings": findings})

    def _emit(self, db, record, student, alert, findings: list[dict[str, Any]]) -> None:
        """Dedup repeated alerts, persist proctor events, collect for push."""
        now = datetime.now(timezone.utc).timestamp()
        key = f"{record.student_id}:{alert['kind']}"
        last = self.last_alert.get(key)
        if last and now - last < 10 * 60:               # re-alert after 10 min
            return
        self.last_alert[key] = now
        db.add(ProctorEvent(session_id=self.session_id, student_id=record.student_id,
                            kind=alert["kind"], severity=alert["severity"],
                            detail=f"{student.roll_number or student.full_name}: {alert['text']}"))
        findings.append({
            "userId": record.student_id,
            "name": student.full_name,
            "roll": student.roll_number,
            **alert,
        })


class MonitorHub:
    def __init__(self) -> None:
        self._monitors: dict[str, SessionMonitor] = {}

    def start_for(self, session_id: str) -> None:
        if session_id not in self._monitors:
            self._monitors[session_id] = SessionMonitor(session_id)
        self._monitors[session_id].start()

    def stop_for(self, session_id: str) -> None:
        mon = self._monitors.pop(session_id, None)
        if mon:
            mon.stop()

    def active(self) -> list[str]:
        return [sid for sid, m in self._monitors.items()
                if m.task and not m.task.done()]


monitor_hub = MonitorHub()
