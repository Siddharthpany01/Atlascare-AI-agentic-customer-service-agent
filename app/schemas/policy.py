"""
app/schemas/policy.py
---------------------
Pydantic v2 schemas for the Policy Engine.

These models cross module boundaries (policy.py → executor.py → router.py)
and therefore live in the shared schemas layer, not inside app/agents/.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Input context passed to PolicyEngine.validate_plan()
# ---------------------------------------------------------------------------

class RequestContext(BaseModel):
    trace_id: str
    session_id: str
    customer_tier: str | None = None   # tier-based guardrails


# ---------------------------------------------------------------------------
# Policy output
# ---------------------------------------------------------------------------

class PolicyViolation(BaseModel):
    rule: str                                          # e.g. "THRESHOLD_CHECK"
    tool: str                                          # tool key that triggered it
    message: str                                       # human-readable reason
    action: Literal["BLOCK", "ESCALATE", "WARN"]


class PolicyResult(BaseModel):
    approved: bool                                     # False if any BLOCK violation exists
    violations: list[PolicyViolation] = []
