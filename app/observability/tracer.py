"""
app/observability/tracer.py
----------------------------
TraceStore — Redis-backed, append-only trace storage for AtlasCare.

Storage layout:
  Key:   trace:{trace_id}
  Value: JSON string (TraceRecord.model_dump_json())
  TTL:   TRACE_TTL_SECONDS (default 7 days)

Failure policy:
  If Redis is unreachable, all operations log a WARNING and return gracefully.
  Observability must never break the happy path.
"""

from __future__ import annotations

import logging
from typing import Optional

import redis

from app.config import settings
from app.schemas.trace import TraceRecord

logger = logging.getLogger(__name__)

_KEY_PREFIX = "trace:"
_TRACE_TTL_SECONDS = 7 * 24 * 60 * 60   # 7 days


def _make_key(trace_id: str) -> str:
    return f"{_KEY_PREFIX}{trace_id}"


class TraceStore:
    """
    Append-only trace store backed by Redis.

    Instantiate via get_trace_store() singleton.
    All methods are synchronous and never raise.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: Optional[redis.Redis] = None
        self._connect()

    def _connect(self) -> None:
        """Attempt to connect to Redis. Failure is non-fatal."""
        try:
            self._client = redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            # Ping to verify connectivity at startup
            self._client.ping()
            logger.info("TraceStore connected to Redis at %s", self._redis_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "TraceStore could not connect to Redis (%s): %s — traces will be dropped",
                self._redis_url, exc,
            )
            self._client = None

    def _get_client(self) -> Optional[redis.Redis]:
        """Return the Redis client, attempting reconnect if previously unavailable."""
        if self._client is None:
            self._connect()
        return self._client

    # -----------------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------------

    def append(self, record: TraceRecord) -> None:
        """
        Persist a TraceRecord to Redis.

        Silently logs and returns if Redis is unavailable.
        Overwrites any existing record with the same trace_id (last-write-wins).
        """
        client = self._get_client()
        if client is None:
            logger.warning(
                "TraceStore.append skipped — Redis unavailable (trace_id=%s)",
                record.trace_id,
            )
            return

        try:
            key = _make_key(record.trace_id)
            client.setex(key, _TRACE_TTL_SECONDS, record.model_dump_json())
            logger.debug("TraceStore.append trace_id=%s", record.trace_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "TraceStore.append failed (trace_id=%s): %s",
                record.trace_id, exc,
            )

    def get(self, trace_id: str) -> TraceRecord | None:
        """
        Retrieve a TraceRecord by trace_id.

        Returns None if not found or Redis is unavailable.
        """
        client = self._get_client()
        if client is None:
            logger.warning(
                "TraceStore.get skipped — Redis unavailable (trace_id=%s)", trace_id,
            )
            return None

        try:
            raw = client.get(_make_key(trace_id))
            if raw is None:
                return None
            return TraceRecord.model_validate_json(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "TraceStore.get failed (trace_id=%s): %s", trace_id, exc,
            )
            return None

    def close(self) -> None:
        """
        Close the Redis connection cleanly.

        Called by the FastAPI lifespan shutdown hook (app/core.py).
        Safe to call multiple times — subsequent calls are no-ops.
        Never raises; follows the same failure policy as all other methods.
        """
        if self._client is None:
            return
        try:
            self._client.close()
            logger.info("TraceStore Redis connection closed.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("TraceStore.close failed: %s", exc)
        finally:
            self._client = None

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_trace_store: TraceStore | None = None


def get_trace_store() -> TraceStore:
    """Return the module-level TraceStore singleton."""
    global _trace_store
    if _trace_store is None:
        _trace_store = TraceStore(redis_url=settings.REDIS_URL)
    return _trace_store
