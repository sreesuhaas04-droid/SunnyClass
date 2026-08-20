"""Server-side face matching.

The browser runs face-api.js (tinyFaceDetector + faceLandmark68 + faceRecognitionNet)
and produces a 128-d descriptor. Only that vector is transmitted — the video frames
never leave the student's device. The server owns the enrolled gallery and the
match decision, so a client cannot simply assert "I am roll 21CS042".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np

from app.core.config import settings


@dataclass(slots=True)
class Match:
    user_id: Optional[str]
    distance: float
    confidence: float
    matched: bool
    runner_up_distance: float = 1.0

    @property
    def margin(self) -> float:
        """Gap to the second-best candidate — low margin means an ambiguous match."""
        return max(0.0, self.runner_up_distance - self.distance)


def normalize(vec: Sequence[float]) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    if arr.ndim != 1:
        arr = arr.ravel()
    return arr


def validate_descriptor(vec: Sequence[float]) -> np.ndarray:
    arr = normalize(vec)
    if arr.size != settings.FACE_DESCRIPTOR_DIM:
        raise ValueError(
            f"Descriptor must have {settings.FACE_DESCRIPTOR_DIM} dimensions, got {arr.size}"
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError("Descriptor contains non-finite values")
    if float(np.linalg.norm(arr)) == 0.0:
        raise ValueError("Descriptor is a zero vector")
    return arr


def distance_to_confidence(distance: float, threshold: float) -> float:
    """Map euclidean distance to a 0..1 confidence with a soft shoulder.

    distance 0.0        -> 1.00
    distance = threshold-> ~0.60 (the attendance cut-off)
    distance >= 1.0     -> 0.00
    """
    if distance <= 0:
        return 1.0
    if distance >= 1.0:
        return 0.0
    if distance <= threshold:
        # 1.0 .. 0.6 over [0, threshold]
        return float(1.0 - 0.4 * (distance / threshold))
    # 0.6 .. 0.0 over (threshold, 1.0]
    span = max(1e-6, 1.0 - threshold)
    return float(0.6 * (1.0 - (distance - threshold) / span))


def match_descriptor(
    probe: Sequence[float],
    gallery: Iterable[tuple[str, Sequence[float]]],
    threshold: Optional[float] = None,
) -> Match:
    """Nearest-neighbour match of `probe` against (user_id, descriptor) pairs.

    Multiple descriptors per user are supported: the best (smallest) distance for
    a user wins, matching face-api.js's own FaceMatcher semantics.
    """
    thr = threshold if threshold is not None else settings.FACE_MATCH_THRESHOLD
    p = validate_descriptor(probe)

    ids: list[str] = []
    vectors: list[np.ndarray] = []
    for uid, vec in gallery:
        try:
            vectors.append(validate_descriptor(vec))
            ids.append(uid)
        except ValueError:
            continue

    if not vectors:
        return Match(user_id=None, distance=1.0, confidence=0.0, matched=False)

    mat = np.vstack(vectors)                       # (N, 128)
    dists = np.linalg.norm(mat - p, axis=1)        # euclidean, same metric as face-api.js

    # collapse to best distance per user
    best_per_user: dict[str, float] = {}
    for uid, d in zip(ids, dists):
        d = float(d)
        if uid not in best_per_user or d < best_per_user[uid]:
            best_per_user[uid] = d

    ordered = sorted(best_per_user.items(), key=lambda kv: kv[1])
    best_id, best_d = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else 1.0

    confidence = distance_to_confidence(best_d, thr)
    matched = best_d <= thr and confidence >= settings.FACE_ATTENDANCE_MIN_CONFIDENCE

    return Match(
        user_id=best_id if matched else None,
        distance=round(best_d, 4),
        confidence=round(confidence, 4),
        matched=matched,
        runner_up_distance=round(float(runner_up), 4),
    )
