"""
app/middleware/trace.py
-----------------------
Starlette middleware that runs on EVERY request before any route handler.

Responsibilities
----------------
1. Generate a unique trace_id and attach it to request.state
2. Start a wall-clock timer
3. Inject trace_id into the response header (X-Trace-ID)
4. Log request start / end with latency

trace_id format: trc_{YYYYMMDDHHMMSS}_{8-char uuid hex}
Example:         trc_20250523102455_a1b2c3d4

All downstream code reads trace_id via:  request.state.trace_id
All log lines must include trace_id so you can grep a single interaction.
"""
import time
import uuid
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import logging

try:
    from app.observability.otel import set_trace_id_in_current_span
except Exception:
    def set_trace_id_in_current_span(trace_id: str) -> None:
        return

logger = logging.getLogger(__name__)


def _make_trace_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    uid = uuid.uuid4().hex[:8]
    return f"trc_{ts}_{uid}"


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = _make_trace_id()

        # Attach to request.state so any route handler can read it
        request.state.trace_id = trace_id

        start = time.perf_counter()
        logger.info(
            "request_start",
            extra={"trace_id": trace_id, "path": request.url.path, "method": request.method},
        )

        response: Response = await call_next(request)

        set_trace_id_in_current_span(trace_id)

        latency_ms = round((time.perf_counter() - start) * 1000, 1)

        # Surface trace_id to the caller — useful for support tickets
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Latency-MS"] = str(latency_ms)

        logger.info(
            "request_end",
            extra={
                "trace_id": trace_id,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
            },
        )

        return response
