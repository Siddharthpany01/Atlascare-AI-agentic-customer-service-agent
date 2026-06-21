"""
app/api/router.py
-----------------
Public API surface.


Endpoints
---------
GET  /health   → liveness probe (used by Docker healthcheck and load balancers)
POST /query    → main customer-facing endpoint
"""
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.schemas.trace import ToolCallRecord
from app.safety import sanitize_user_input, mask_pii


logger = logging.getLogger(__name__)

router = APIRouter()

def _count_model_calls(records: list[ToolCallRecord], escalated: bool) -> int:
    """
    Count Groq (LLM) model calls made during this request.

    classify=1, plan=1, synthesize=1 — total 3 in a normal flow.
    If policy blocked (all records are ESCALATE with POLICY_BLOCK),
    synthesize still runs, but plan ran before the block was detected,
    so count remains 3. Returns 0 only for an empty plan (UNKNOWN intent
    where classify still ran = 1, plan = 1, synthesize = 1 → still 3).
    """
    # Always: classify (1) + plan (1) + synthesize (1) = 3
    # Unless we have zero records AND escalated=False meaning classify returned
    # UNKNOWN and plan returned empty — all three still called.
    return 3



# ── Request / Response models ─────────────────────────────────────────────────

class QueryRequest(BaseModel):
    message: str = Field(..., description="Raw customer message")
    session_id: str = Field(..., description="Caller-supplied session identifier")


class TraceInfo(BaseModel):
    trace_id: str = Field(..., description="Unique ID linking all logs for this interaction")
    session_id: str
    latency_ms: float = Field(..., description="Request latency in milliseconds")
    escalation_triggered: bool = Field(..., description="True if this request escalated")
    tool_calls: list[ToolCallRecord] = Field(default_factory=list, description="Tool execution records")


class QueryResponse(BaseModel):
    response: str = Field(..., description="Natural-language reply to the customer")
    trace: TraceInfo = Field(..., description="Trace metadata and tool call records")


class HealthResponse(BaseModel):
    status: str
    version: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse, tags=["ops"])
async def health(request: Request):
    """
    Liveness probe.
    Returns 200 + version string.  Used by:
      - pytest tests/test_health.py 
      - Docker HEALTHCHECK directive
      - Load balancer health checks
    """
    from app.config import settings
    return HealthResponse(status="ok", version=settings.APP_VERSION)


@router.post("/query", response_model=QueryResponse, tags=["agent"])
async def query(body: QueryRequest, request: Request):
    """

    Pipeline:
        1. Classify intent        (Groq)
        2. Plan tool calls        (Groq)
        3. Policy gate            (PolicyEngine — NO LLM)
        4. Execute plan           (TOOL_REGISTRY, sync, with retry)
        5. Synthesize reply       (Groq)

    Session context is fetched before the pipeline and updated after.
    escalated=True if any tool returned status ESCALATE (includes policy BLOCKs).
    """
    import time
    from datetime import datetime, timezone

    from app.agents.classifier import classify
    from app.agents.executor import execute
    from app.agents.planner import plan
    from app.agents.synthesizer import synthesize
    from app.observability.audit import get_audit_logger
    from app.observability.metrics import record_request, record_tool_call
    from app.observability.tracer import get_trace_store
    from app.schemas.trace import TraceRecord
    from app.services.session import get_session_store

    t_start = time.monotonic()
    trace_id = getattr(request.state, "trace_id", "trace-not-set")
    session_store = get_session_store()

    # ── 1. Session context ────────────────────────────────────────────────────
    session = session_store.get_or_create(body.session_id)

    # ── 2. Classify ───────────────────────────────────────────────────────────
    message = sanitize_user_input(body.message)
    classify_result =  classify(message, trace_id=trace_id)
    logger.info(
        "[%s] intent=%s confidence=%.2f",
        trace_id, classify_result.intent, classify_result.confidence,
    )

    # ── 3. Plan ───────────────────────────────────────────────────────────────
    tool_plan = plan(
        message=body.message,
        intent=classify_result.intent,
        session_context=session,
        trace_id=trace_id,
    )

    # ── 4. Execute (+  policy gate) ───────────────────────────────────
    records = execute(tool_plan, trace_id=trace_id, session_id=body.session_id)

    # ── 5. Determine escalation ───────────────────────────────────────────────
    escalated = any(r.status == "ESCALATE" for r in records)

    # ── 6. Synthesize ─────────────────────────────────────────────────────────
    reply = synthesize(
        message=body.message,
        tool_results=records,
        trace_id=trace_id,
    )

    # ── 7. Update session ─────────────────────────────────────────────────────
    session_store.set(body.session_id, {
        **session,
        "last_intent": classify_result.intent,
        "last_trace_id": trace_id,
        "last_tool_statuses": [r.status for r in records],
    })

    # ── 8. Observability ──────────────────────────────────────────────────────
    latency_ms = (time.monotonic() - t_start) * 1000
    latency_breach = latency_ms > (settings.LLM_TIMEOUT_SECONDS * 1000)

    # Collect policy violations from POLICY_BLOCK escalation records
    # TraceRecord.policy_violations expects a list[dict]. Wrap string summaries in a dict.
    policy_violations = [
        {"summary": r.result_summary}
        for r in records
        if r.status == "ESCALATE" and r.result_summary and "POLICY_BLOCK" in r.result_summary
    ]

    trace_record = TraceRecord(
        trace_id=trace_id,
        session_id=body.session_id,
        intent=classify_result.intent,
        latency_ms=latency_ms,
        latency_breach=latency_breach,
        tool_calls=records,
        escalation_triggered=escalated,
        policy_violations=policy_violations,
        model_calls=_count_model_calls(records, escalated),
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
    )

    get_trace_store().append(trace_record)

    get_audit_logger().log(
        trace_id=trace_id,
        event="QUERY_COMPLETE",
        payload={
            "session_id": body.session_id,
            "intent": classify_result.intent,
            "escalated": escalated,
            "latency_ms": round(latency_ms, 2),
            "latency_breach": latency_breach,
            "tools_called": [r.tool for r in records],
            "tool_statuses": [r.status for r in records],
            "policy_violations": policy_violations,
        },
    )

    for r in records:
        record_tool_call(tool=r.tool, status=r.status)

    record_request(
        intent=classify_result.intent,
        escalated=escalated,
        latency_seconds=latency_ms / 1000,
    )

    logger.info(
        "[%s] query complete escalated=%s steps=%d latency_ms=%.1f breach=%s",
        trace_id, escalated, len(records), latency_ms, latency_breach,
    )


    return QueryResponse(
        response=reply,
        trace=TraceInfo(
            trace_id=trace_id,
            session_id=body.session_id,
            latency_ms=latency_ms,
            escalation_triggered=escalated,
            tool_calls=records,
        ),
    )
