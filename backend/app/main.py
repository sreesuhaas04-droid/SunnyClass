"""SmartClass AI — FastAPI application entrypoint."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import init_models
from app.routers import attendance, auth, chat, classes, face, media, ws

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s")
log = logging.getLogger("smartclass")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    log.info("Database ready (%s)", settings.DATABASE_URL.split("@")[-1])
    log.info("SUNNY provider: %s", settings.LLM_PROVIDER or "fallback")
    yield
    log.info("Shutting down")


app = FastAPI(
    title="SmartClass AI API",
    description=(
        "Backend for SmartClass AI — roll-number gated virtual classrooms with "
        "facial-recognition attendance, WebRTC mesh video, live captions, "
        "recordings, proctoring, and the SUNNY AI assistant."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(GZipMiddleware, minimum_size=800)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def timing_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500,
                        content={"success": False, "error": "Internal server error"})


# --- routers ------------------------------------------------------------- #
app.include_router(auth.router)
app.include_router(classes.router)
app.include_router(classes.sessions_router)
app.include_router(face.router)
app.include_router(face.join_router)
app.include_router(attendance.router)
app.include_router(attendance.analytics)
app.include_router(media.router)
app.include_router(media.rec_router)
app.include_router(chat.router)
app.include_router(ws.router)


@app.get("/api/health", tags=["meta"])
async def health():
    return {
        "success": True,
        "status": "ok",
        "app": settings.APP_NAME,
        "env": settings.ENV,
        "sunny_provider": settings.LLM_PROVIDER or "fallback",
        "face_threshold": settings.FACE_MATCH_THRESHOLD,
    }


@app.get("/api/config", tags=["meta"])
async def client_config():
    """Runtime config the frontend reads on boot — no rebuild needed to change
    ICE servers, thresholds or intervals."""
    return {
        "success": True,
        "iceServers": settings.ice_servers,
        "verifyIntervalSeconds": settings.VERIFY_INTERVAL_SECONDS,
        "faceThreshold": settings.FACE_MATCH_THRESHOLD,
        "minConfidence": settings.FACE_ATTENDANCE_MIN_CONFIDENCE,
        "maxViolations": settings.MAX_VIOLATIONS_BEFORE_FLAG,
        "lateAfterMinutes": settings.LATE_AFTER_MINUTES,
    }


# --- static frontend ----------------------------------------------------- #
# Resolve frontend directory: try repo-relative first (local dev), then
# the Docker container path /app/frontend (Render / docker compose).
_here = Path(__file__).resolve()
FRONTEND_DIR = _here.parents[2] / "frontend"   # local: <repo>/frontend
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = _here.parents[1] / "frontend"  # Docker: /app/frontend


class CachedStatic(StaticFiles):
    """Immutable caching for the face model weights (they never change) and a
    short cache for everything else, so a re-join doesn't re-download 7 MB."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        # Starlette passes full_path as a keyword arg; fall back to positional.
        path = str(kwargs.get("full_path") or (args[0] if args else ""))
        if "/models/" in path or "/vendor/" in path:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.endswith((".css", ".js")):
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response


if FRONTEND_DIR.exists():
    app.mount("/", CachedStatic(directory=str(FRONTEND_DIR), html=True), name="frontend")
    log.info("Serving frontend from %s", FRONTEND_DIR)
else:
    log.warning("Frontend directory not found — API-only mode")

