"""WebSocket endpoint: WebRTC signaling relay + live class event bus.

One socket per participant carries everything — SDP/ICE relay, presence, live
captions, hand raises, engagement pushes and proctor alerts — so a student on a
weak connection maintains a single TCP/TLS session instead of polling.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.deps import user_from_token
from app.models import (
    AttendanceRecord, Caption, ClassSession, Enrollment, Role, SessionStatus, User,
)
from app.services.realtime import Participant, hub, profile_for

log = logging.getLogger("smartclass.ws")

router = APIRouter(tags=["realtime"])

RELAY_TYPES = {"offer", "answer", "ice", "renegotiate", "track-update"}
BROADCAST_TYPES = {"hand", "reaction", "chat", "media-state", "screen-share", "pin"}


@router.websocket("/ws/class/{session_id}")
async def class_socket(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(default=""),
    network: str = Query(default="4g"),
    device: str = Query(default="desktop"),
):
    await websocket.accept()

    # ---- authenticate & authorise before joining the room ---------------- #
    async with SessionLocal() as db:
        user = await user_from_token(token, db)
        if not user:
            await websocket.send_text(json.dumps({"type": "error", "code": "unauthorized"}))
            await websocket.close(code=4401)
            return

        session = await db.get(ClassSession, session_id)
        if not session or session.status != SessionStatus.live.value:
            await websocket.send_text(json.dumps({"type": "error", "code": "no_live_session"}))
            await websocket.close(code=4404)
            return

        is_host = user.id == session.host_id or user.role in (Role.teacher.value, Role.admin.value)
        if not is_host:
            enrolled = await db.scalar(select(Enrollment).where(
                Enrollment.class_id == session.class_id, Enrollment.student_id == user.id))
            # A student must already hold an admitted attendance row (created by
            # /api/join after the roll-number + face gate).
            admitted = await db.scalar(select(AttendanceRecord).where(
                AttendanceRecord.session_id == session_id,
                AttendanceRecord.student_id == user.id))
            if not enrolled or not admitted:
                await websocket.send_text(json.dumps(
                    {"type": "error", "code": "not_admitted",
                     "message": "Join through /api/join first — roll number and face "
                                "must be verified."}))
                await websocket.close(code=4403)
                return

        display = {"id": user.id, "name": user.full_name, "role": user.role,
                   "roll": user.roll_number}

        # Backlog so a late joiner immediately sees what has been said so far.
        recent = (await db.scalars(
            select(Caption).where(Caption.session_id == session_id)
            .order_by(Caption.id.desc()).limit(40))).all()
        backlog = [{"type": "caption", "text": cap.text, "speaker": cap.speaker_name,
                    "speakerId": cap.speaker_id, "isFinal": True,
                    "offsetMs": cap.offset_ms} for cap in reversed(recent)]

    room = hub.room(session_id)
    peer_id = f"peer_{uuid.uuid4().hex[:12]}"
    participant = Participant(
        peer_id=peer_id, user_id=display["id"], name=display["name"],
        role=display["role"], roll_number=display["roll"], ws=websocket,
        device=device, network=network,
    )

    existing = room.snapshot()
    await room.add(participant)

    await websocket.send_text(json.dumps({
        "type": "welcome",
        "peerId": peer_id,
        "sessionId": session_id,
        "self": participant.public(),
        "participants": existing,
        "iceServers": settings.ice_servers,
        "quality": profile_for(network, len(room.participants)),
        "captions": (backlog + room.captions)[-40:],
        # The newcomer initiates offers to everyone already in the room; existing
        # peers wait for the offer. This keeps mesh negotiation collision-free.
        "shouldInitiate": [p["peerId"] for p in existing],
    }))
    await room.broadcast({"type": "peer-joined", "peer": participant.public()}, exclude=peer_id)

    # Room grew — tell the existing peers to step the ladder down if needed.
    # The newcomer already got its profile inside the welcome frame.
    await room.broadcast({"type": "quality-update",
                          "quality": profile_for(network, len(room.participants))},
                         exclude=peer_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")

            # ---- 1:1 signaling relay (server never inspects the SDP) ----- #
            if mtype in RELAY_TYPES:
                target = msg.get("to")
                if target:
                    await room.send_to(target, {
                        "type": mtype, "from": peer_id,
                        "payload": msg.get("payload"), "peer": participant.public(),
                    })
                continue

            # ---- live captions -------------------------------------------- #
            if mtype == "caption":
                entry = {"type": "caption", "text": msg.get("text", ""),
                         "speaker": participant.name, "speakerId": participant.user_id,
                         "isFinal": bool(msg.get("isFinal")),
                         "offsetMs": msg.get("offsetMs", 0)}
                if entry["isFinal"]:
                    room.captions.append(entry)
                    # In-place truncation keeps the same list object so concurrent
                    # readers (e.g. the welcome-frame builder) never see a stale ref.
                    del room.captions[:-200]
                await room.broadcast(entry, exclude=peer_id)
                continue

            # ---- presence / media state ----------------------------------- #
            if mtype == "media-state":
                participant.camera_on = bool(msg.get("cameraOn", participant.camera_on))
                participant.mic_on = bool(msg.get("micOn", participant.mic_on))
                await room.broadcast({"type": "media-state", "peerId": peer_id,
                                      "cameraOn": participant.camera_on,
                                      "micOn": participant.mic_on}, exclude=peer_id)
                continue

            if mtype == "hand":
                participant.hand_raised = bool(msg.get("raised"))
                await room.broadcast({"type": "hand", "peerId": peer_id,
                                      "name": participant.name,
                                      "raised": participant.hand_raised},
                                     exclude=peer_id)
                continue

            if mtype == "engagement":
                participant.engagement = int(msg.get("score", participant.engagement))
                await room.broadcast_to_hosts({
                    "type": "engagement", "peerId": peer_id,
                    "userId": participant.user_id, "name": participant.name,
                    "score": participant.engagement})
                continue

            if mtype == "quality":
                participant.network = msg.get("network", participant.network)
                await websocket.send_text(json.dumps({
                    "type": "quality-update",
                    "quality": profile_for(participant.network, len(room.participants))}))
                continue

            if mtype in BROADCAST_TYPES:
                await room.broadcast({**msg, "peerId": peer_id,
                                      "name": participant.name}, exclude=peer_id)
                continue

            if mtype == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "t": msg.get("t")}))
                continue

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Unhandled error in WebSocket loop for session=%s peer=%s",
                      session_id, peer_id)
    finally:
        await room.remove(peer_id)
        await room.broadcast({"type": "peer-left", "peerId": peer_id,
                              "userId": participant.user_id})
        if room.participants:
            # Use None (→ "4g" default) so the quality step-down is based on
            # mesh size only, not the leaver's network which is now irrelevant.
            await room.broadcast({"type": "quality-update",
                                  "quality": profile_for(None, len(room.participants))})


@router.get("/api/realtime/stats")
async def realtime_stats():
    return {"success": True, **hub.stats()}
