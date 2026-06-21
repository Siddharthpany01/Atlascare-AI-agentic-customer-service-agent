"""
app/core.py
-----------
Application factory.  main.py calls create_app() once.
Registering routers and middleware here keeps main.py clean.
"""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from app.config import settings
from app.api.router import router as api_router
from app.middleware.trace import TraceMiddleware
from app.observability.otel import init_tracing
from app.middleware.ratelimit import RateLimitMiddleware
from prometheus_client import make_asgi_app
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Mount


logger = logging.getLogger(__name__)

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Everything before `yield` runs on startup.
    Everything after `yield` runs on shutdown (including SIGTERM).
    """
    # ------------------------------------------------------------------ #
    # STARTUP                                                              #
    # ------------------------------------------------------------------ #

    # Shared httpx AsyncClient with connection pooling.
    # Tool adapters are currently in-memory, but this client is available
    # on app.state.http_client for any future external HTTP calls and for
    # integration tests that need to probe external endpoints.
    limits = httpx.Limits(
        max_connections=settings.HTTP_POOL_MAX_CONNECTIONS,
        max_keepalive_connections=settings.HTTP_POOL_MAX_KEEPALIVE,
        keepalive_expiry=settings.HTTP_KEEPALIVE_EXPIRY_S,
    )
    app.state.http_client = httpx.AsyncClient(limits=limits)
    logger.info(
        "httpx pool initialised (max_conn=%d, keepalive=%d)",
        settings.HTTP_POOL_MAX_CONNECTIONS,
        settings.HTTP_POOL_MAX_KEEPALIVE,
    )

    yield

    # ------------------------------------------------------------------ #
    # SHUTDOWN                                                             #
    # ------------------------------------------------------------------ #

    # 1. Close httpx connection pool — waits for in-flight requests to finish.
    try:
        await app.state.http_client.aclose()
        logger.info("httpx pool closed.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("httpx pool close failed: %s", exc)

    # 2. Close TraceStore Redis connection.
    #    Import is deferred to avoid circular imports at module load time.
    try:
        from app.observability.tracer import get_trace_store
        trace_store = get_trace_store()
        if hasattr(trace_store, 'close'):
            trace_store.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("TraceStore close failed: %s", exc)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs",       # interactive Swagger UI 
        redoc_url="/redoc",
        debug=settings.DEBUG,
        lifespan=_lifespan,
    )

    # ── Middleware (order matters — outermost first) ──────────────────────────
    # TraceMiddleware assigns trace_id and starts the latency timer on every request.
    app.add_middleware(TraceMiddleware)

    app.add_middleware(RateLimitMiddleware)

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(api_router)

    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    # Initialize OpenTelemetry (opt-in via OTEL_EXPORTER env var). This is
    # additive to `TraceMiddleware` — it instruments FastAPI and HTTP clients.
    try:
        init_tracing(app)
    except Exception:
        pass

    return app
