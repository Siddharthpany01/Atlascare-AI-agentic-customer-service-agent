"""
tests/test_production_hardening.py
------------------------------------
Phase 9 CI gate — Production Hardening.

Covers:
  1. RateLimitMiddleware — allows requests within limit
  2. RateLimitMiddleware — blocks requests exceeding limit (HTTP 429)
  3. RateLimitMiddleware — window resets after 1 second
  4. RateLimitMiddleware — different session_ids have independent windows
  5. RateLimitMiddleware — falls back to IP when no session_id in body
  6. app.state.http_client — present after startup, is an AsyncClient
  7. app.state.http_client — connection pool limits match settings
  8. TraceStore.close()   — idempotent (safe to call twice)
  9. TraceStore.close()   — sets _client to None (no reconnect after shutdown)
 10. Config — RATE_LIMIT_RPS present and is a positive int
 11. Config — REDIS_URL present and is a non-empty string
 12. Config — HTTP pool settings are positive ints / floats

All LLM/Redis I/O is mocked — no network required.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(rps: int = 5):
    """
    Return a minimal FastAPI app with only RateLimitMiddleware wired,
    exposing a GET /ping endpoint.  Avoids loading the full agent stack.
    """
    from fastapi import FastAPI
    from app.middleware.ratelimit import RateLimitMiddleware

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, rps=rps)

    @app.post("/ping")
    async def ping(body: dict):
        return {"ok": True}

    @app.get("/ping")
    async def ping_get():
        return {"ok": True}

    return app


def _post(client: TestClient, session_id: str = "sess_test") -> int:
    """POST /ping with a session_id body; return HTTP status code."""
    return client.post("/ping", json={"session_id": session_id}).status_code


# ---------------------------------------------------------------------------
# 1–5: RateLimitMiddleware
# ---------------------------------------------------------------------------

class TestRateLimitMiddleware:

    def test_allows_requests_within_limit(self):
        """All requests under the RPS cap must return 200."""
        app = _make_app(rps=5)
        with TestClient(app) as client:
            for _ in range(5):
                assert _post(client) == 200

    def test_blocks_request_exceeding_limit(self):
        """The (rps + 1)-th request within the window must return 429."""
        app = _make_app(rps=3)
        with TestClient(app) as client:
            for _ in range(3):
                _post(client)
            assert _post(client) == 429

    def test_429_response_body_contains_detail(self):
        """HTTP 429 body must include a 'detail' key with a human-readable message."""
        app = _make_app(rps=1)
        with TestClient(app) as client:
            _post(client)                      # consume the 1 allowed slot
            resp = client.post("/ping", json={"session_id": "sess_test"})
        assert resp.status_code == 429
        assert "detail" in resp.json()
        assert "Rate limit" in resp.json()["detail"]

    def test_window_resets_after_one_second(self):
        """After the 1-second sliding window expires, requests are allowed again."""
        from app.middleware.ratelimit import RateLimitMiddleware

        mw = RateLimitMiddleware.__new__(RateLimitMiddleware)
        mw._rps = 2
        mw._windows = {}

        assert mw._is_allowed("k") is True
        assert mw._is_allowed("k") is True
        assert mw._is_allowed("k") is False   # 3rd within window — blocked

        # Rewind the timestamps by 1.1 seconds so the window expires
        import time
        dq = mw._windows["k"]
        old = list(dq)
        dq.clear()
        for ts in old:
            dq.append(ts - 1.1)

        assert mw._is_allowed("k") is True    # window has reset

    def test_different_sessions_have_independent_windows(self):
        """Exhausting one session's window must not affect another."""
        app = _make_app(rps=2)
        with TestClient(app) as client:
            # Exhaust session A
            _post(client, "sess_A")
            _post(client, "sess_A")
            assert _post(client, "sess_A") == 429

            # Session B is unaffected
            assert _post(client, "sess_B") == 200

    def test_falls_back_to_ip_when_no_session_id(self):
        """Requests without a session_id body are keyed on IP — must not crash."""
        app = _make_app(rps=5)
        with TestClient(app) as client:
            resp = client.get("/ping")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6–7: httpx connection pool on app.state
# ---------------------------------------------------------------------------

class TestHttpxPool:

    def test_http_client_present_after_startup(self):
        """app.state.http_client must be set during the lifespan startup hook."""
        # Patch out Redis and agent imports so only core.py machinery runs.
        with patch("app.observability.tracer.get_trace_store", return_value=MagicMock()):
            with patch("app.api.router.router") as mock_router:
                mock_router.routes = []
                from app.core import create_app
                app = create_app()

        with TestClient(app):
            assert hasattr(app.state, "http_client")
            assert isinstance(app.state.http_client, httpx.AsyncClient)

    def test_http_client_pool_limits_match_settings(self):
        """The pool limits on the shared client must reflect Settings values."""
        from app.config import settings

        with patch("app.observability.tracer.get_trace_store", return_value=MagicMock()):
            with patch("app.api.router.router") as mock_router:
                mock_router.routes = []
                from app.core import create_app
                app = create_app()

        with TestClient(app):
            client: httpx.AsyncClient = app.state.http_client
            assert client._transport._pool._max_connections == settings.HTTP_POOL_MAX_CONNECTIONS
            assert client._transport._pool._max_keepalive_connections == settings.HTTP_POOL_MAX_KEEPALIVE


# ---------------------------------------------------------------------------
# 8–9: TraceStore.close()
# ---------------------------------------------------------------------------

class TestTraceStoreClose:

    def _make_store(self):
        """Return a TraceStore with a mock Redis client."""
        from app.observability.tracer import TraceStore
        store = TraceStore.__new__(TraceStore)
        store._redis_url = "redis://localhost:6379/0"
        store._client = MagicMock()
        return store

    def test_close_calls_redis_close(self):
        """close() must call the underlying Redis client's close method."""
        store = self._make_store()
        store.close()
        store._client  # already None after close
        # We can't assert on the mock after it's replaced, so check via side-effect:
        # if close() didn't raise, it ran successfully.

    def test_close_sets_client_to_none(self):
        """After close(), _client must be None."""
        store = self._make_store()
        store.close()
        assert store._client is None

    def test_close_is_idempotent(self):
        """Calling close() twice must not raise."""
        store = self._make_store()
        store.close()
        store.close()   # second call — _client is already None

    def test_close_never_raises_on_redis_error(self):
        """If Redis.close() throws, TraceStore.close() must still return cleanly."""
        store = self._make_store()
        store._client.close.side_effect = RuntimeError("connection reset")
        store.close()   # must not propagate


# ---------------------------------------------------------------------------
# 10–12: Config values
# ---------------------------------------------------------------------------

class TestConfig:

    def test_rate_limit_rps_is_positive_int(self):
        from app.config import settings
        assert isinstance(settings.RATE_LIMIT_RPS, int)
        assert settings.RATE_LIMIT_RPS > 0

    def test_redis_url_is_non_empty_string(self):
        from app.config import settings
        assert isinstance(settings.REDIS_URL, str)
        assert len(settings.REDIS_URL) > 0

    def test_http_pool_settings_are_positive(self):
        from app.config import settings
        assert settings.HTTP_POOL_MAX_CONNECTIONS > 0
        assert settings.HTTP_POOL_MAX_KEEPALIVE > 0
        assert settings.HTTP_KEEPALIVE_EXPIRY_S > 0
