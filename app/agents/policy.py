"""
app/agents/policy.py
--------------------
Policy Engine for AtlasCare

Pure Python rule evaluation. NO LLM calls anywhere in this module.

Rules evaluated:
  1. THRESHOLD_CHECK — any payments_refund step with amount > REFUND_AUTO_LIMIT_INR → BLOCK

The adapter layer enforces the refund limit as a backstop.
This engine is the PRIMARY enforcement point — it runs before any tool is called.


oms_cancel_item executes directly — no confirmation step required
"""

from __future__ import annotations

import logging

from app.config import settings
from app.schemas.agent import ToolCallPlan
from app.schemas.policy import PolicyResult, PolicyViolation, RequestContext
from app.tools.registry import TOOL_REGISTRY
from app.services.data_loader import get_store

logger = logging.getLogger(__name__)


class PolicyEngine:
    """
    Stateless policy evaluator.

    Instantiate once (module-level singleton via get_policy_engine()) and reuse.
    All rule methods are pure functions with no side-effects.
    """

    def validate_plan(
        self,
        plan: ToolCallPlan,
        ctx: RequestContext,
    ) -> PolicyResult:
        """
        Evaluate all policy rules against the planned steps.

        Returns PolicyResult(approved=True) if no BLOCK violations exist.
        Returns PolicyResult(approved=False) if one or more BLOCK violations exist.
        Never raises.
        """
        violations: list[PolicyViolation] = []

        for step in plan.steps:
            policy_tags = self._get_policy_tags(step.tool)

            if "THRESHOLD_CHECK" in policy_tags:
                violation = self._check_threshold(step.tool, step.args, ctx)
                if violation:
                    violations.append(violation)

        blocked = any(v.action == "BLOCK" for v in violations)
        approved = not blocked

        if violations:
            logger.warning(
                "[%s] PolicyEngine found %d violation(s): %s",
                ctx.trace_id,
                len(violations),
                [v.rule for v in violations],
            )

        logger.info(
            "[%s] PolicyEngine approved=%s violations=%d",
            ctx.trace_id,
            approved,
            len(violations),
        )

        return PolicyResult(approved=approved, violations=violations)

    # -----------------------------------------------------------------------
    # Rule implementations
    # -----------------------------------------------------------------------

    def _check_threshold(
        self,
        tool: str,
        args: dict,
        ctx: RequestContext,
    ) -> PolicyViolation | None:
        """
        THRESHOLD_CHECK — block refunds above REFUND_AUTO_LIMIT_INR.

        Reads limit from settings (never hardcoded) per errors_and_decisions.md [Phase 3].
        Customer tier is inferred from the order, not from user input (customer_id and
        customer_tier args are optional and may be incorrect; we derive tier from order).
        """
        limit = settings.REFUND_AUTO_LIMIT_INR
        amount = args.get("amount")
        order_id = args.get("order_id")
        line_id = args.get("line_id")

        store = get_store()
        order = None
        
        # Derive amount from order if not explicitly provided
        if amount is None and order_id is not None:
            order = store.get_order(str(order_id))
            
            if order:
                if line_id is not None:
                    # Ensure line_id is valid for the order items list
                    try:
                        item = order.items[int(line_id) - 1]
                        amount = item.unit_price * item.quantity
                    except (IndexError, ValueError, TypeError):
                        logger.error("[%s] THRESHOLD_CHECK: Invalid line_id=%r", ctx.trace_id, line_id)
                else:
                    amount = order.total_amount
            else:
                logger.error("[%s] THRESHOLD_CHECK: Order %s not found", ctx.trace_id, order_id)

        try:
            amount_float = float(amount) # type: ignore
        except (TypeError, ValueError):
            logger.error(
                "[%s] THRESHOLD_CHECK: could not parse amount=%r on tool=%s",
                ctx.trace_id, amount, tool,
            )
            return None
        # store = get_store()
        # order = store.get_order(str(order_id))
        # if order_id is not None and order is not None:
        #     if customer_id is not None and customer_id != order.customer_id:
        #         return PolicyViolation(
        #         rule="CUSTOMER_ID_MISMATCH",
        #         tool=tool,
        #         message=(
        #             f"The provided customer_id {customer_id} does not match the customer_id associated with order_id {order_id}."
        #         ),
        #         action="BLOCK",
        #     )
        
        # customer = store.get_customer(str(order.customer_id)) if order else None
        # if customer_tier is not None:
        #     if order_id is not None and order is not None:
        #         if customer is not None and customer_tier != customer.tier:
        #             return PolicyViolation(
        #             rule="CUSTOMER_TIER_MISMATCH",
        #             tool=tool,
        #             message=(
        #                 f"The provided customer_tier {customer_tier} does not match the customer_tier associated with order_id {order_id}."
        #             ),
        #             action="BLOCK",
        #         )
        #     else:
        #         return PolicyViolation(
        #             rule="ORDER_ID_NOT_PROVIDED",
        #             tool=tool,
        #             message=(
        #                 f"Please provide a valid order_id to check customer_tier {customer_tier} against."
        #             ),
        #             action="BLOCK",
        #         )
        
        store = get_store()
        order = store.get_order(str(order_id))
        customer = store.get_customer(str(order.customer_id)) if order else None
        customer_tier = customer.tier if customer else None
        
        if customer_tier == "GOLD":
            limit = settings.GOLD_REFUND_AUTO_LIMIT_INR
        elif customer_tier == "STANDARD":
            limit = settings.STANDARD_REFUND_AUTO_LIMIT_INR

        if amount_float > limit:
            return PolicyViolation(
                rule="THRESHOLD_CHECK",
                tool=tool,
                message=(
                    f"Refund amount ₹{amount_float:,.2f} exceeds the auto-approval limit of ₹{limit:,.2f} "
                    f"for customer tier '{customer_tier}'. Escalation required."
                ),
                action="BLOCK",
            )

        return None

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _get_policy_tags(self, tool_key: str) -> list[str]:
        """Return the policy tag list for a tool, or [] if tool is not in registry."""
        entry = TOOL_REGISTRY.get(tool_key)
        if entry is None:
            logger.warning("PolicyEngine: tool '%s' not found in TOOL_REGISTRY", tool_key)
            return []
        return entry.get("policy", [])


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_policy_engine: PolicyEngine | None = None


def get_policy_engine() -> PolicyEngine:
    """Return the module-level PolicyEngine singleton."""
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = PolicyEngine()
    return _policy_engine
