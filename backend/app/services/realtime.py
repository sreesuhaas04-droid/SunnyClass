"""In-memory room hub for WebRTC signaling + live class events.

Protocol (JSON over a single WS per participant):

  <- {"type":"welcome", "peer_id", "participants":[...], "ice_servers":[...], "quality":{...}}
  -> {"type":"offer"|"answer"|"ice", "to": peer_id, "payload": {...}}       # relayed verbatim
  <- {"type":"peer-joined"|"peer-left", "peer": {...}}
  -> {"type":"caption", "text", "is_final"}      <- broadcast to room
  -> {"type":"hand"|"reaction"|"chat", ...}      <- broadcast to room
  -> {"type":"quality", "network":"3g"}          <- server replies with a profile
  <- {"type":"attendance", ...}                  <- pushed when someone is verified
  <- {"type":"violation", ...}                   <- pushed to the host only

Signaling messages are relayed, never parsed — media stays peer-to-peer, so the
server carries only a few KB per participant per session.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import WebSocket

# Bitrate ladder used for adaptive quality on weak networks.
QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    "high":  {"width": 1280, "height": 720, "frameRate": 30, "maxBitrate": 1_200_000,
              "label": "HD 720p"},
    "medium": {"width": 854, "height": 480, "frameRate": 20, "maxBitrate": 600_000,
               "label": "SD 480p"},
    "low":   {"width": 640, "height": 360, "frameRate": 15, "maxBitrate": 300_000,
              "label": "Low 360p"},
    "minimal": {"width": 320, "height": 240, "frameRate": 10, "maxBitrate": 120_000,
                "label": "Data saver 240p"},
    "audio":  {"width": 0, "height": 0, "frameRate": 0, "maxBitrate": 24_000,
               "label": "Audio only + captions"},
}

NETWORK_TO_PROFILE = {
    "wifi": "high", "ethernet": "high", "4g": "high",
    "3g": "low", "2g": "minimal", "slow-2g": "audio",
}


def profile_for(network: Optional[str], participants: int = 1) -> dict[str, Any]:
    """Pick a send profile from the reported network *and* the mesh size —
    in a mesh each peer uploads N-1 copies, so the ladder steps down as the
    room grows."""
    name = NETWORK_TO_PROFILE.get((network or "4g").lower(), "high")
    order = ["high", "medium", "low", "minimal", "audio"]
    idx = order.index(name)
    if participants > 4:
        idx = min(len(order) - 1, idx + 1)
    if participants > 8:
        idx = min(len(order) - 1, idx + 1)
    chosen = order[idx]
    return {"profile": chosen, **QUALITY_PROFILES[chosen], "meshSize": participants}


@dataclass
class Participant:
    peer_id: str
    user_id: str
    name: str
    role: str
    roll_number: Optional[str]
    ws: WebSocket
    device: str = "desktop"
    network: str = "4g"
    joined_at: float = field(default_factory=time.time)
    camera_on: bool = True
    mic_on: bool = True
    hand_raised: bool = False
    engagement: int = 100

    def public(self) -> dict[str, Any]:
        return {
            "peerId": self.peer_id,
            "userId": self.user_id,
            "name": self.name,
            "role": self.role,
            "rollNumber": self.roll_number,
            "device": self.device,
            "cameraOn": self.camera_on,
            "micOn": self.mic_on,
            "handRaised": self.hand_raised,
            "engagement": self.engagement,
            "joinedAt": self.joined_at,
        }


class Room:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.participants: dict[str, Participant] = {}
        self.captions: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()
        self.peak = 0
        # --- meeting features ------------------------------------------- #
        self.polls: dict[str, dict[str, Any]] = {}     # poll_id -> poll state
        self.chat_backlog: list[dict[str, Any]] = []   # last N meeting-chat msgs
        self._pending_ghosts: list[Participant] = []   # dead peers awaiting peer-left

    def snapshot(self) -> list[dict[str, Any]]:
        return [p.public() for p in self.participants.values()]

    def chat_tail(self, n: int = 50) -> list[dict[str, Any]]:
        return self.chat_backlog[-n:]

    # ---------------------------------------------------------------- polls
    def create_poll(self, poll_id: str, question: str, options: list[str],
                    created_by: str) -> dict[str, Any]:
        poll = {
            "pollId": poll_id,
            "question": question[:300],
            "options": [str(o)[:120] for o in options][:6],
            "votes": {},           # peer_id -> option index
            "voters": set(),       # user_ids that already voted
            "createdBy": created_by,
            "closed": False,
            "reveal": False,
        }
        self.polls[poll_id] = poll
        return self._poll_public(poll)

    def vote_poll(self, poll_id: str, user_id: str, option: int) -> Optional[dict[str, Any]]:
        poll = self.polls.get(poll_id)
        if not poll or poll["closed"] or user_id in poll["voters"]:
            return None
        if not isinstance(option, int) or not (0 <= option < len(poll["options"])):
            return None
        poll["votes"][user_id] = option
        poll["voters"].add(user_id)
        return self._poll_public(poll)

    def close_poll(self, poll_id: str) -> Optional[dict[str, Any]]:
        poll = self.polls.get(poll_id)
        if not poll:
            return None
        poll["closed"] = True
        poll["reveal"] = True
        return self._poll_public(poll)

    def _poll_public(self, poll: dict[str, Any], reveal: Optional[bool] = None) -> dict[str, Any]:
        counts = [0] * len(poll["options"])
        for opt in poll["votes"].values():
            counts[opt] += 1
        total = len(poll["votes"])
        show = poll["reveal"] if reveal is None else reveal
        return {
            "type": "poll", "pollId": poll["pollId"], "question": poll["question"],
            "options": poll["options"], "closed": poll["closed"],
            "total": total,
            **({"counts": counts} if show else {"voted": total}),
        }

    async def add(self, p: Participant) -> None:
        async with self.lock:
            self.participants[p.peer_id] = p
            self.peak = max(self.peak, len(self.participants))

    async def remove(self, peer_id: str) -> Optional[Participant]:
        async with self.lock:
            return self.participants.pop(peer_id, None)

    async def send_to(self, peer_id: str, message: dict[str, Any]) -> bool:
        p = self.participants.get(peer_id)
        if not p:
            return False
        try:
            await p.ws.send_text(json.dumps(message))
            return True
        except Exception:
            return False

    async def broadcast(self, message: dict[str, Any], exclude: Optional[str] = None) -> None:
        payload = json.dumps(message)
        dead: list[str] = []
        for pid, p in list(self.participants.items()):
            if pid == exclude:
                continue
            try:
                await p.ws.send_text(payload)
            except Exception:
                dead.append(pid)
        if dead:
            # Hold the lock while cleaning up dead peers to avoid racing
            # with concurrent add() / remove() calls. Announce the ghosts so
            # every client's roster stays truthful.
            async with self.lock:
                for pid in dead:
                    ghost = self.participants.pop(pid, None)
                    if ghost:
                        self._pending_ghosts.append(ghost)

    def drain_ghosts(self) -> list[Participant]:
        """Dead peers discovered mid-broadcast; caller sends their peer-left."""
        ghosts, self._pending_ghosts = self._pending_ghosts, []
        return ghosts

    async def broadcast_to_hosts(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message)
        for p in list(self.participants.values()):
            if p.role in ("teacher", "admin"):
                try:
                    await p.ws.send_text(payload)
                except Exception:
                    pass


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def room(self, session_id: str) -> Room:
        if session_id not in self.rooms:
            self.rooms[session_id] = Room(session_id)
        return self.rooms[session_id]

    def get(self, session_id: str) -> Optional[Room]:
        return self.rooms.get(session_id)

    def close(self, session_id: str) -> None:
        self.rooms.pop(session_id, None)

    def stats(self) -> dict[str, Any]:
        return {
            "rooms": len(self.rooms),
            "participants": sum(len(r.participants) for r in self.rooms.values()),
            "detail": {sid: len(r.participants) for sid, r in self.rooms.items()},
        }

    async def push(self, session_id: str, message: dict[str, Any]) -> None:
        """Server-originated push (attendance marked, violation, class ending)."""
        room = self.rooms.get(session_id)
        if room:
            await room.broadcast(message)


hub = Hub()
