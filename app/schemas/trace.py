"""
app/schemas/trace.py
--------------------
Shared models used across tool adapters , executor ,
and observability layer .


Import path for all other modules:
    from app.schemas.trace import ToolResult, TraceRecord, ToolCallRecord
"""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field
import datetime


class ToolResult(BaseModel):
    """
    Standard return type for every tool adapter.
    Tools NEVER raise exceptions — they always return a ToolResult.
    The executor reads .status and never catches raw exceptions from tools.
    """
    status: Literal["SUCCESS", "FAILED", "IDEMPOTENT", "ESCALATE"]
    data: Optional[dict[str, Any]] = None       # structured result payload
    error_code: Optional[str] = None            # e.g. "ORDER_NOT_FOUND"
    error_message: Optional[str] = None         # human-readable, for logs only
    latency_ms: float = 0.0


class ToolCallRecord(BaseModel):
    """
    Record of a single tool invocation — appended to TraceRecord.tool_calls.
    Built by the executor after each tool returns.
    """
    seq: int                                    # 1-indexed call sequence number
    tool: str                                   # tool name from TOOL_REGISTRY
    args: dict[str, Any]                        # args passed to the tool
    status: Literal["SUCCESS", "FAILED", "IDEMPOTENT", "ESCALATE"]
    latency_ms: float
    result_summary: Optional[str] = None        # one-line summary for audit log


class TraceRecord(BaseModel):
    """
    Full trace of one customer interaction.
    Written once to TraceStore at the end of the request lifecycle.
    """
    trace_id: str
    session_id: str
    intent: Optional[str] = None                # TRACKING | COMPOUND | ESCALATION | UNKNOWN
    latency_ms: float = 0.0
    latency_breach: bool = False                # True if > LLM_TIMEOUT_SECONDS
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    escalation_triggered: bool = False
    policy_violations: list[dict[str, Any]] = Field(default_factory=list)
    model_calls: int = 0                        # number of LLM calls made
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
