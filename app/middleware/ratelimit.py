"""
app/middleware/ratelimit.py
----------------------------
RateLimitMiddleware — per-session_id sliding-window rate limiter.

Limit:  settings.RATE_LIMIT_RPS requests per second (default 10).
Key:    session_id extracted from the JSON request body.
        Falls back to client IP if session_id is absent or the body
        is not JSON (e.g. GET /health).

Response when limit is exceeded:
    HTTP 429  {"detail": "Rate limit exceeded. Max {rps} req/s per session."}

Design notes:
- Uses a collections.deque per key to store request timestamps.
- The window is a rolling 1-second window (not a fixed 1-s bucket), so
  bursts are smoothed rather than allowed all at once at bucket boundary.
- All state is in-process. For multi-worker deployments, replace the
  _windows dict with a Redis ZSET (ZADD / ZREMRANGEBYSCORE / ZCARD).
- Middleware never raises — if state is corrupted it fails open (allows).
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

logger = logging.getLogger(__name__)

# wall-clock seconds in the sliding window
_WINDOW_SECONDS: float = 1.0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter keyed on session_id (or client IP).

    Registered in app/core.py via app.add_middleware(RateLimitMiddleware).
    """

    def __init__(self, app, rps: int | None = None) -> None:
        super().__init__(app)
        self._rps: int = rps if rps is not None else settings.RATE_LIMIT_RPS
        # { key: deque of timestamps (float, monotonic) }
        self._windows: dict[str, Deque[float]] = {}

    # ------------------------------------------------------------------
    # Starlette hook
    # ------------------------------------------------------------------

    async def dispatch(self, request: Request, call_next) -> Response:
        key = await self._extract_key(request)

        try:
            if not self._is_allowed(key):
                logger.warning(
                    "RateLimitMiddleware: limit exceeded key=%s rps=%d", key, self._rps
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": (
                            f"Rate limit exceeded. Max {self._rps} req/s per session."
                        )
                    },
                )
        except Exception:  # noqa: BLE001 — fail open
            logger.exception("RateLimitMiddleware: error checking limit for key=%s", key)

        return await call_next(request)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _extract_key(self, request: Request) -> str:
        """
        Return session_id from the JSON body, falling back to client IP.
        Reads the body non-destructively by replacing request._body.
        """
        try:
            body_bytes = await request.body()
            payload = json.loads(body_bytes)
            session_id = payload.get("session_id", "").strip()
            if session_id:
                return f"session:{session_id}"
        except Exception:  # noqa: BLE001
            pass

        # Fall back to client IP
        client_ip = (
            request.client.host if request.client else "unknown"
        )
        return f"ip:{client_ip}"

    def _is_allowed(self, key: str) -> bool:
        """
        Return True if this request is within the rate limit.
        Evicts timestamps outside the 1-second window before checking.
        """
        now = time.monotonic()
        window_start = now - _WINDOW_SECONDS

        if key not in self._windows:
            self._windows[key] = deque()

        dq = self._windows[key]

        # Evict expired timestamps from the left
        while dq and dq[0] <= window_start:
            dq.popleft()

        if len(dq) >= self._rps:
            return False

        dq.append(now)
        return True
