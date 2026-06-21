"""
app/services/session.py
-----------------------
Short-term conversation context store.
Holds the last N tool results and turn history per session_id.
TTL is enforced lazily on get().

Production upgrade path:
    Replace the _store dict with a Redis client.
    The get/set/delete interface is intentionally Redis-compatible.
    # Redis integration point — swap dict for:
    #   import redis.asyncio as redis
    #   self._redis = redis.from_url(settings.REDIS_URL)
    #   await self._redis.setex(session_id, TTL_SECONDS, json.dumps(data))
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# TTL in seconds, derived from config (default 30 minutes)
_TTL_SECONDS = settings.SESSION_TTL_MIN * 60


class SessionStore:
    """
    In-memory key-value store for conversation sessions.
    Each value is a plain dict the caller controls.
    Entries expire after SESSION_TTL_MIN minutes of inactivity.
    """

    def __init__(self) -> None:
        # { session_id: {"data": {...}, "updated_at": float} }
        self._store: dict[str, dict[str, Any]] = {}

    def get(self, session_id: str) -> dict[str, Any] | None:
        """
        Return session data if it exists and has not expired.
        Expired sessions are evicted on access (lazy TTL).
        """
        entry = self._store.get(session_id)
        if entry is None:
            return None

        age = time.time() - entry["updated_at"]
        if age > _TTL_SECONDS:
            logger.debug("Session expired, evicting: %s (age=%.0fs)", session_id, age)
            del self._store[session_id]
            return None

        return entry["data"]

    def set(self, session_id: str, data: dict[str, Any]) -> None:
        """
        Write or overwrite session data, resetting the TTL clock.
        """
        self._store[session_id] = {
            "data": data,
            "updated_at": time.time(),
        }
        logger.debug("Session updated: %s (%d keys)", session_id, len(data))

    def delete(self, session_id: str) -> None:
        """Explicitly remove a session (e.g. on logout or test teardown)."""
        self._store.pop(session_id, None)

    def get_or_create(self, session_id: str) -> dict[str, Any]:
        """
        Return existing session data or create a fresh empty session.
        Used by the router so it never has to check for None.
        """
        existing = self.get(session_id)
        if existing is not None:
            return existing
        fresh: dict[str, Any] = {"turn": 0, "tool_results": [], "history": []}
        self.set(session_id, fresh)
        logger.info("New session created: %s", session_id)
        return fresh

    @property
    def active_count(self) -> int:
        """Number of non-expired sessions currently in memory."""
        now = time.time()
        return sum(
            1 for e in self._store.values()
            if (now - e["updated_at"]) <= _TTL_SECONDS
        )


# ── Module-level singleton ────────────────────────────────────────────────────
# Phase 9: replace with an async Redis-backed store
# Redis integration point ↓
_session_store = SessionStore()


def get_session_store() -> SessionStore:
    """Return the module-level SessionStore singleton."""
    return _session_store
