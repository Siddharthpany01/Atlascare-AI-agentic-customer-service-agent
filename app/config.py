"""
app/config.py
-------------
Central configuration loaded from environment variables / .env file.
All other modules import `settings` from here — never use os.getenv() directly elsewhere.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    # ── AI provider ─────────────────────────────────────────────────────────
    # Using Groq API for LLM inference
    GROQ_API_KEY: str = ""

    # ── Application ──────────────────────────────────────────────────────────
    APP_NAME: str = "AtlasCare"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False

    # ── Session ───────────────────────────────────────────────────────────────
    SESSION_TTL_MIN: int = 30           # minutes until idle session is evicted

    # ── Data ──────────────────────────────────────────────────────────────────
    DATA_DIR: str = "data"              # relative path to mock JSON fixtures

    # ── Payments / Policy ─────────────────────────────────────────────────────
    REFUND_AUTO_LIMIT_INR: float = 25_000.0   # hard cap; also in PolicyEngine

    GOLD_REFUND_AUTO_LIMIT_INR: float = 25_000.0   # for GOLD customers
    STANDARD_REFUND_AUTO_LIMIT_INR: float = 10_000.0   # for STANDARD customers

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"  # used by TraceStore 


    # ── Rate limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_RPS: int = 10            # requests per second per session_id

    # ── Retry / timeouts ─────────────────────────────────────────────────────
    LLM_TIMEOUT_SECONDS: float = 3.0
    TOOL_RETRY_MAX: int = 2
    TOOL_RETRY_BACKOFF_BASE: float = 0.5   # seconds; doubles each retry
    
    # Rate limiting
    RATE_LIMIT_RPS: int = 10            # max requests per second per session_id

    # Shared httpx connection pool
    HTTP_POOL_MAX_CONNECTIONS: int = 100
    HTTP_POOL_MAX_KEEPALIVE: int = 20
    HTTP_KEEPALIVE_EXPIRY_S: float = 30.0



    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached Settings singleton.
    Use this everywhere: from app.config import get_settings; cfg = get_settings()
    """
    return Settings()


# Convenience alias used across modules
settings = get_settings()
