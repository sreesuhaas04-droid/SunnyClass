"""WebSocket endpoint: WebRTC signaling relay + live meeting event bus.

One socket per participant carries everything — SDP/ICE relay, presence, live
captions, hand raises, engagement pushes and proctor alerts — so a student on a
weak connection maintains a single TCP/TLS session instead of polling.

Meeting features on the same socket: in-meeting text chat (persisted), live
polls (create / vote / close with per-user dedup), a whiteboard relay
(stroke-batch + clear), emoji reactions, spotlight/pin and layout switching.
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
    AttendanceRecord, Caption, ClassSession, Enrollment, MeetingChatMessage,
    Role, SessionStatus, User,
)
from app.services.realtime import Participant, hub, profile_for

log = logging.getLogger("sunnyclass.ws")

router = APIRouter(tags=["realtime"])

RELAY_TYPES = {"offer", "answer", "ice", "renegotiate", "track-update"}
BROADCAST_TYPES = {"hand", "reaction", "media-state", "screen-share", "pin",
                   "layout", "whiteboard"}

# Server-side clamps so one participant cannot flood the room.
MAX_TEXT = 500          # chat / caption text
MAX_WB_POINTS = 600     # whiteboard points per stroke
MAX_CHAT_BACKLOG = 50


def _text(v: Any, limit: int = MAX_TEXT) -> str:
    return str(v)[:limit] if isinstance(v, (str, int, float)) else ""


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

        recent_chat = (await db.scalars(
            select(MeetingChatMessage).where(MeetingChatMessage.session_id == session_id)
            .order_by(MeetingChatMessage.id.desc()).limit(MAX_CHAT_BACKLOG))).all()
        chat_backlog = [{"type": "chat", "messageId": m.id, "senderId": m.sender_id,
                         "senderName": m.sender_name, "senderRole": m.sender_role,
                         "text": m.text, "ts": m.ts.isoformat() + "Z" if m.ts else None}
                        for m in reversed(recent_chat)]

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
        "chat": room.chat_tail(MAX_CHAT_BACKLOG) or chat_backlog,
        "polls": [room._poll_public(p) for p in room.polls.values()],
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

    async def drain_ghosts() -> None:
        for ghost in room.drain_ghosts():
            await room.broadcast({"type": "peer-left", "peerId": ghost.peer_id,
                                  "userId": ghost.user_id})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue                      # arrays/strings/etc: ignore, don't drop
            mtype = msg.get("type")

            # ---- 1:1 signaling relay (server never inspects the SDP) ----- #
            if mtype in RELAY_TYPES:
                target = msg.get("to")
                if isinstance(target, str) and target:
                    await room.send_to(target, {
                        "type": mtype, "from": peer_id,
                        "payload": msg.get("payload"), "peer": participant.public(),
                    })
                continue

            # ---- live captions -------------------------------------------- #
            if mtype == "caption":
                entry = {"type": "caption", "text": _text(msg.get("text")),
                         "speaker": participant.name, "speakerId": participant.user_id,
                         "isFinal": bool(msg.get("isFinal")),
                         "offsetMs": msg.get("offsetMs", 0) if isinstance(msg.get("offsetMs"), int) else 0}
                if entry["isFinal"] and entry["text"]:
                    room.captions.append(entry)
                    # In-place truncation keeps the same list object so concurrent
                    # readers (e.g. the welcome-frame builder) never see a stale ref.
                    del room.captions[:-200]
                    # Persist here so clients send each caption once (socket only).
                    async with SessionLocal() as db:
                        db.add(Caption(session_id=session_id, speaker_id=participant.user_id,
                                       speaker_name=participant.name, text=entry["text"],
                                       offset_ms=entry["offsetMs"]))
                        await db.commit()
                await room.broadcast(entry, exclude=peer_id)
                await drain_ghosts()
                continue

            # ---- in-meeting text chat (persisted) -------------------------- #
            if mtype == "chat":
                text = _text(msg.get("text")).strip()
                if not text:
                    continue
                chat_msg = {"type": "chat", "messageId": None, "senderId": participant.user_id,
                            "senderName": participant.name, "senderRole": participant.role,
                            "text": text}
                async with SessionLocal() as db:
                    row = MeetingChatMessage(session_id=session_id, sender_id=participant.user_id,
                                             sender_name=participant.name,
                                             sender_role=participant.role, text=text)
                    db.add(row)
                    await db.commit()
                    await db.refresh(row)
                    chat_msg["messageId"] = row.id
                room.chat_backlog.append(chat_msg)
                del room.chat_backlog[:-MAX_CHAT_BACKLOG]
                await room.broadcast(chat_msg, exclude=peer_id)
                await websocket.send_text(json.dumps(chat_msg))   # echo to sender
                await drain_ghosts()
                continue

            # ---- polls ------------------------------------------------------ #
            if mtype == "poll-create":
                if participant.role == "student":
                    continue
                question = _text(msg.get("question"), 300).strip()
                options = [str(o).strip()[:120] for o in (msg.get("options") or [])
                           if isinstance(o, (str, int, float)) and str(o).strip()][:6]
                if not question or len(options) < 2:
                    continue
                poll = room.create_poll(f"poll_{uuid.uuid4().hex[:10]}", question,
                                        options, participant.user_id)
                await room.broadcast(poll)
                await drain_ghosts()
                continue

            if mtype == "poll-vote":
                room.vote_poll(str(msg.get("pollId") or ""), participant.user_id,
                               msg.get("option") if isinstance(msg.get("option"), int) else -1)
                # students see only "voted" counts; results reveal on close
                if room.polls.get(str(msg.get("pollId") or "")):
                    await room.broadcast_to_hosts(
                        room._poll_public(room.polls[msg.get("pollId")], reveal=True))
                continue

            if mtype == "poll-close":
                if participant.role == "student":
                    continue
                closed = room.close_poll(str(msg.get("pollId") or ""))
                if closed:
                    await room.broadcast(closed)   # revealed with counts
                    await drain_ghosts()
                continue

            # ---- whiteboard relay (batched strokes) ------------------------ #
            if mtype == "whiteboard":
                action = msg.get("action")
                if action == "stroke":
                    pts = msg.get("points")
                    if not isinstance(pts, list) or not pts or len(pts) > MAX_WB_POINTS:
                        continue
                    payload = {"type": "whiteboard", "action": "stroke",
                               "points": pts, "color": _text(msg.get("color"), 16) or "#E8B98F",
                               "width": msg.get("width") if isinstance(msg.get("width"), (int, float)) else 3,
                               "peerId": peer_id}
                elif action in ("clear", "undo"):
                    payload = {"type": "whiteboard", "action": action, "peerId": peer_id}
                else:
                    continue
                await room.broadcast(payload, exclude=peer_id)
                await drain_ghosts()
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
                score = msg.get("score")
                score = int(score) if isinstance(score, (int, float)) else participant.engagement
                participant.engagement = max(0, min(100, score))
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
                out = {**msg, "peerId": peer_id, "name": participant.name}
                # pin/layout carry only the string payload fields
                if mtype in ("pin", "layout"):
                    out["target"] = _text(msg.get("target"), 60)
                    out["mode"] = _text(msg.get("mode"), 24)
                await room.broadcast(out, exclude=peer_id)
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


@router.get("/api/realtime/monitor")
async def monitor_status():
    """Which live sessions SUNNY is currently supervising."""
    from app.services.monitor import monitor_hub
    return {"success": True, "monitoring": monitor_hub.active()}
