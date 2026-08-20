"""Application configuration — 12-factor, env driven."""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core ---
    APP_NAME: str = "SmartClass AI"
    ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "change-me-in-production-smartclass-ai"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    # --- Database ---
    # postgresql+asyncpg://user:pass@host:5432/db   (sqlite+aiosqlite:///./smartclass.db also works)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:smartclass@localhost:5432/smartclass"
    DB_ECHO: bool = False

    # --- CORS ---
    CORS_ORIGINS: str = "*"

    # --- SUNNY AI ---
    # provider: groq | gemini | openai | anthropic | none  (none => rule-based fallback)
    LLM_PROVIDER: str = "none"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""
    # Override the provider's base URL to point at a self-hosted or proxied
    # OpenAI-compatible server (Ollama, vLLM, LM Studio, LiteLLM, OpenRouter…).
    # Leave empty to use the provider's own endpoint.
    LLM_BASE_URL: str = ""
    LLM_TIMEOUT: float = 20.0
    LLM_MAX_TOKENS: int = 700

    # --- Face recognition ---
    FACE_MATCH_THRESHOLD: float = 0.52       # euclidean distance on 128-d face-api descriptors
    FACE_ATTENDANCE_MIN_CONFIDENCE: float = 0.60
    FACE_DESCRIPTOR_DIM: int = 128
    FACE_MIN_SAMPLES: int = 1

    # --- Attendance policy ---
    LATE_AFTER_MINUTES: int = 10
    PRESENT_MIN_PRESENCE_RATIO: float = 0.60   # must be seen for >=60% of session to stay 'present'
    VERIFY_INTERVAL_SECONDS: int = 45

    # --- Proctoring ---
    MAX_VIOLATIONS_BEFORE_FLAG: int = 3
    VIOLATION_ENGAGEMENT_PENALTY: int = 8

    # --- Media / storage ---
    STORAGE_DIR: str = "storage"
    MAX_RECORDING_MB: int = 512

    # --- WebRTC ---
    STUN_SERVERS: str = "stun:stun.l.google.com:19302,stun:stun1.l.google.com:19302"
    TURN_URL: str = ""
    TURN_USERNAME: str = ""
    TURN_CREDENTIAL: str = ""

    @property
    def cors_list(self) -> List[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def ice_servers(self) -> List[dict]:
        servers = [{"urls": u.strip()} for u in self.STUN_SERVERS.split(",") if u.strip()]
        if self.TURN_URL:
            servers.append({
                "urls": self.TURN_URL,
                "username": self.TURN_USERNAME,
                "credential": self.TURN_CREDENTIAL,
            })
        return servers


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
