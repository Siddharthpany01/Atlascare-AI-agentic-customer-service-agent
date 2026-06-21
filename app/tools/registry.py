"""
app/tools/registry.py — Central tool registry.

TOOL_REGISTRY maps tool name → ToolDef (handler callable + policy tags).
The Policy Engine reads policy tags at plan-validation time.
The Executor calls handler_fn with unpacked args.

Registered keys (from contract.md):
  oms_get_order | oms_cancel_item | oms_update_address
  payments_refund | crm_create_case | kb_search
"""

from typing import Any, Callable, TypedDict

from app.tools.crm import get_crm
from app.tools.kb import get_kb
from app.tools.oms import get_oms
from app.tools.payments import get_payments


class ToolDef(TypedDict):
    """
    Definition record for a single registered tool.

    Attributes:
        handler:     Callable that accepts keyword args and returns ToolResult.
        policy:      List of policy tag strings read by PolicyEngine.
                     Tags: READ | WRITE | REFUND | THRESHOLD_CHECK | REQUIRES_CONFIRM
        description: Human-readable description for planner prompt context.
    """

    handler: Callable[..., Any]
    policy: list[str]
    description: str


def _build_registry() -> dict[str, ToolDef]:
    """
    Build the registry lazily so adapter singletons are created on first
    access rather than at import time (keeps test setup clean).
    """
    oms = get_oms()
    crm = get_crm()
    kb = get_kb()
    pay = get_payments()

    return {
        "oms_get_order": ToolDef(
            handler=oms.get_order,
            policy=["READ"],
            description="Fetch full order details by order_id.",
        ),
        "oms_cancel_item": ToolDef(
            handler=oms.cancel_item,
            policy=["WRITE", "REQUIRES_CONFIRM"],
            description=(
                "Cancel a single line item on an order. or cancel the order "
                "Args: order_id (str), line_id (int, 1-indexed)."
            ),
        ),
        "oms_update_address": ToolDef(
            handler=oms.update_address,
            policy=["WRITE"],
            description=(
                "Update the shipping address on an order, based on complete given address or the fetched address using user given address label."
                "Args: order_id (str), address (dict)."
            ),
        ),
        "payments_refund": ToolDef(
            handler=pay.initiate_refund,
            policy=["WRITE", "REFUND", "THRESHOLD_CHECK"],
            description=(
                "Initiate a refund for a customer. "
                "Args: customer_id (str, optional), amount (float, INR, optional), method (str), "
                "order_id (str), line_id (int, 1-indexed)."
            ),
        ),
        "crm_create_case": ToolDef(
            handler=crm.create_case,
            policy=["WRITE"],
            description=(
                "Create a new CRM support case. "
                "Args: customer_id, order_id, description, "
                "amount_inr (float|None), trace_id (str)."
            ),
        ),
        "kb_search": ToolDef(
            handler=kb.search,
            policy=["READ"],
            description=(
                "Search the knowledge base by tags. "
                "Args: tags (list[str])."
            ),
        ),
        "case_search": ToolDef(
            handler=crm.get_case,
            policy=["READ"],
            description=(
                "Search for support cases by various criteria. "
                "Args: customer_id (str|None), order_id (str|None)."
            ),
        ),
    }


# Module-level registry instance — built once on first import.
TOOL_REGISTRY: dict[str, ToolDef] = _build_registry()
