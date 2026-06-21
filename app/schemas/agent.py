"""
app/schemas/agent.py
--------------------
Pydantic v2 schemas for the Agent Layer.

These models cross module boundaries (classifier → planner → executor → synthesizer)
and therefore live in the shared schemas layer, not inside app/agents/.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Classifier output
# ---------------------------------------------------------------------------

Intent = Literal["TRACKING", "COMPOUND", "ESCALATION", "UNKNOWN"]


class ClassifyResult(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Planner output
# ---------------------------------------------------------------------------

class ToolCallStep(BaseModel):
    tool: Literal[
        "oms_get_order",
        "oms_cancel_item",
        "oms_update_address",
        "payments_refund",
        "crm_create_case",
        "kb_search",
        "case_search",
    ]
    args: dict
    rationale: str


class ToolCallPlan(BaseModel):
    steps: list[ToolCallStep]
