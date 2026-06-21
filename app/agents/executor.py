"""
app/agents/executor.py
----------------------
Sequential tool executor for AtlasCare.

Iterates over the steps in a ToolCallPlan, calls each tool via TOOL_REGISTRY,
applies retry logic (TOOL_RETRY_MAX attempts, exponential backoff), and records
every call as a ToolCallRecord.

Synchronous — all tool adapters are plain `def`.

PolicyEngine.validate_plan() runs before any tool is called.
  - BLOCK violation  → early exit; one ESCALATE ToolCallRecord per blocked step.
  - WARN violation   → logged, execution continues normally.
  - approved=True    → execution proceeds as before.
"""

from __future__ import annotations

import logging
import time

from app.config import settings
from app.schemas.agent import ToolCallPlan
from app.schemas.policy import PolicyResult, RequestContext
from app.schemas.trace import ToolCallRecord, ToolResult
from app.tools.registry import TOOL_REGISTRY

from opentelemetry import trace

tracer = trace.get_tracer(__name__)

logger = logging.getLogger(__name__)


def _normalize_payments_refund_steps(plan: ToolCallPlan, trace_id: str) -> None:
    """
    Normalize payments_refund steps by inheriting order/line metadata from
    earlier oms_cancel_item steps.

    If the planner emits a compound cancel+refund request without explicit
    line_id/amount on payments_refund, we can safely derive the refund amount
    from the already-cancelled line item instead of treating it as a full-order
    refund.
    """
    cancel_line_ids: dict[str, int] = {}
    for step in plan.steps:
        if step.tool != "oms_cancel_item" or not isinstance(step.args, dict):
            continue

        order_id = step.args.get("order_id")
        line_id = step.args.get("line_id")
        if order_id is not None and line_id is not None:
            cancel_line_ids[str(order_id)] = line_id

    for step in plan.steps:
        if step.tool != "payments_refund" or not isinstance(step.args, dict):
            continue

        order_id = step.args.get("order_id")
        if order_id is None and len(cancel_line_ids) == 1:
            order_id = next(iter(cancel_line_ids))
            step.args["order_id"] = order_id
            logger.info(
                "[%s] Propagated order_id=%s to payments_refund from oms_cancel_item",
                trace_id,
                order_id,
            )

        if order_id is not None and step.args.get("line_id") is None:
            line_id = cancel_line_ids.get(str(order_id))
            if line_id is not None:
                step.args["line_id"] = line_id
                logger.info(
                    "[%s] Propagated line_id=%s to payments_refund for order_id=%s",
                    trace_id,
                    line_id,
                    order_id,
                )

        if order_id is not None and step.args.get("amount") is None and step.args.get("line_id") is not None:
            from app.services.data_loader import get_store

            order = get_store().get_order(str(order_id))
            if order is not None:
                try:
                    item = order.items[int(step.args["line_id"]) - 1]
                    step.args["amount"] = item.unit_price * item.quantity
                    logger.info(
                        "[%s] Derived amount=%.2f for payments_refund line_id=%s order_id=%s",
                        trace_id,
                        step.args["amount"],
                        step.args["line_id"],
                        order_id,
                    )
                except (IndexError, ValueError, TypeError):
                    logger.error(
                        "[%s] Could not derive refund amount for payments_refund line_id=%r order_id=%s",
                        trace_id,
                        step.args.get("line_id"),
                        order_id,
                    )


def execute(
    plan: ToolCallPlan,
    trace_id: str,
    session_id: str = "",
    customer_tier: str | None = None,
) -> list[ToolCallRecord]:
    """
    Execute each step in the plan sequentially.

    PolicyEngine.validate_plan() runs before any tool is called.
      - Any BLOCK violation → returns early with one ESCALATE record per step.
      - WARN violations     → logged, execution continues.

    - Retries on FAILED status up to settings.TOOL_RETRY_MAX times.
    - Does NOT retry ESCALATE or IDEMPOTENT results.
    - Returns a ToolCallRecord for every step (even failed ones).
    - Never raises.
    """
    from app.agents.policy import get_policy_engine  # deferred to avoid circular import

    # Normalize compound cancel+refund plans before policy validation.
    _normalize_payments_refund_steps(plan, trace_id)

    # ── Phase 5: Policy gate ─────────────────────────────────────────────────
    ctx = RequestContext(
        trace_id=trace_id,
        session_id=session_id,
        customer_tier=customer_tier,
    )
    policy_result: PolicyResult = get_policy_engine().validate_plan(plan, ctx)


    if not policy_result.approved:
        # One ESCALATE record per planned step — tools are NOT called
        blocked_records: list[ToolCallRecord] = []
        block_violations = [v for v in policy_result.violations if v.action == "BLOCK"]
        block_summary = "; ".join(f"{v.rule}: {v.message}" for v in block_violations)

        for seq, step in enumerate(plan.steps, start=1):
            record = ToolCallRecord(
                seq=seq,
                tool=step.tool,
                args=step.args,
                status="ESCALATE",
                latency_ms=0.0,
                result_summary=f"POLICY_BLOCK: {block_summary}",
            )
            record.__dict__["_tool_result"] = ToolResult( # type: ignore
                status="ESCALATE",
                error_code="POLICY_BLOCK",
                error_message=block_summary,
            )
            blocked_records.append(record)
            logger.warning(
                "[%s] Policy BLOCK on step %d tool=%s — %s",
                trace_id, seq, step.tool, block_summary,
            )

        return blocked_records

    # Log any WARN violations before proceeding
    for v in policy_result.violations:
        logger.warning(
            "[%s] Policy WARN rule=%s tool=%s — %s",
            trace_id, v.rule, v.tool, v.message,
        )


    records: list[ToolCallRecord] = []

    for seq, step in enumerate(plan.steps, start=1):
        tool_key = step.tool
        args = step.args
            
        if tool_key == "payments_refund" and isinstance(args, dict):
            order_id = args.get("order_id")
            if order_id:
                from app.services.data_loader import get_store

                order = get_store().get_order(order_id)
                if order is not None and getattr(order, "status", None) == "delivered":
                    logger.info(
                        "[%s] Delivered order %s refund converted to CRM case instead of payments_refund",
                        trace_id,
                        order_id,
                    )
                    crm_handler = TOOL_REGISTRY["crm_create_case"]["handler"]
                    crm_args = {
                        "customer_id": args.get("customer_id"),
                        "order_id": order_id,
                        "description": (
                            f"Refund requested for delivered order {order_id}; manual review required."
                        ),
                        "amount_inr": float(args.get("amount")) if args.get("amount") is not None else None,
                        "trace_id": trace_id,
                    }
                    t0_delivered = time.perf_counter()
                    result = crm_handler(**crm_args)
                    total_latency = t0_delivered
                    summary = _summarise(result)
                    record = ToolCallRecord(
                        seq=seq,
                        tool="crm_create_case",
                        args=crm_args,
                        status=result.status,
                        latency_ms=total_latency,
                        result_summary=summary,
                    )
                    record.__dict__["_tool_result"] = result  # type: ignore
                    records.append(record)
                    continue

        if tool_key not in TOOL_REGISTRY:
            logger.error("[%s] Unknown tool key '%s' at step %d", trace_id, tool_key, seq)
            records.append(
                ToolCallRecord(
                    seq=seq,
                    tool=tool_key,
                    args=args,
                    status="FAILED",
                    latency_ms=0.0,
                    result_summary=f"Tool '{tool_key}' not found in registry",
                )
            )
            continue
        
        logger.info(
        "[%s] Executing step=%d tool=%s args=%s",
        trace_id,
        seq,
        tool_key,
        args,
        )

        handler = TOOL_REGISTRY[tool_key]["handler"]
        result: ToolResult | None = None
        total_latency = 0.0

        for attempt in range(1, settings.TOOL_RETRY_MAX + 2):  # +2 = max+1 attempts total
            t0 = time.monotonic()
            try:
                result = handler(**args)
            except Exception as exc:  # noqa: BLE001
                elapsed = (time.monotonic() - t0) * 1000
                total_latency += elapsed
                logger.error(
                    "[%s] Tool %s attempt %d raised: %s",
                    trace_id, tool_key, attempt, exc,
                )
                result = ToolResult(
                    status="FAILED",
                    error_code="EXECUTOR_UNHANDLED_EXCEPTION",
                    error_message=str(exc),
                    latency_ms=elapsed,
                )
            else:
                elapsed = (time.monotonic() - t0) * 1000
                total_latency += elapsed
                result = result.model_copy(update={"latency_ms": elapsed}) # type: ignore

            # Don't retry on success, escalation, or idempotent
            if result.status in ("SUCCESS", "ESCALATE", "IDEMPOTENT"):
                break

            # Don't retry if we've exhausted attempts
            if attempt > settings.TOOL_RETRY_MAX:
                break

            # Exponential backoff before next retry
            backoff = settings.TOOL_RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
            logger.warning(
                "[%s] Tool %s attempt %d FAILED (code=%s) — retrying in %.2fs",
                trace_id, tool_key, attempt,
                result.error_code, backoff,
            )
            time.sleep(backoff)

        assert result is not None  # always set after the loop

        summary = _summarise(result)
        record = ToolCallRecord(
            seq=seq,
            tool=tool_key,
            args=args,
            status=result.status,
            latency_ms=total_latency,
            result_summary=summary,
        )
        records.append(record)
        

        logger.info(
            "[%s] step %d tool=%s status=%s latency=%.1fms",
            trace_id, seq, tool_key, result.status, total_latency,
        )

        # Attach full ToolResult to the record for the synthesizer to use
        # We store it as a non-schema attribute for intra-request use only
        record.__dict__["_tool_result"] = result # type: ignore

        with tracer.start_as_current_span(f"tool.{step.tool}") as tool_span:
            tool_span.set_attribute("tool.seq", seq)
            tool_span.set_attribute("tool.name", step.tool)
            tool_span.set_attribute("tool.args", str(step.args))
            tool_span.set_attribute("tool.status", result.status)
            tool_span.set_attribute("tool.latency_ms", total_latency)

    # AUTOMATED ESCALATION HANDOFF: If a refund step escalation occurred,
    # create a CRM case so the trace reflects the follow-up action.
    if any(r.tool == "payments_refund" and r.status == "ESCALATE" for r in records):
        if not any(r.tool == "crm_create_case" for r in records):
            try:
                crm_handler = TOOL_REGISTRY["crm_create_case"]["handler"]
                payment_args = next(
                    r.args
                    for r in records
                    if r.tool == "payments_refund" and r.status == "ESCALATE"
                )
                crm_args = {
                    "customer_id": None,
                    "order_id": payment_args.get("order_id"),
                    "description": (
                        f"Manual review required for escalated refund on order {payment_args.get('order_id')}"
                    ),
                    "amount_inr": float(payment_args.get("amount")) if payment_args.get("amount") is not None else None,
                    "trace_id": trace_id,
                }
                crm_result = crm_handler(**crm_args)
                crm_latency = crm_result.latency_ms
                crm_record = ToolCallRecord(
                    seq=len(records) + 1,
                    tool="crm_create_case",
                    args=crm_args,
                    status=crm_result.status,
                    latency_ms=crm_latency,
                    result_summary=_summarise(crm_result),
                )
                crm_record.__dict__["_tool_result"] = crm_result  # type: ignore
                records.append(crm_record)
                logger.info(
                    "[%s] Added automated crm_create_case follow-up for escalated refund",
                    trace_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[%s] Failed to create CRM follow-up case for escalated refund: %s",
                    trace_id,
                    exc,
                )

    with tracer.start_as_current_span("executor") as span:
        span.set_attribute("app.trace_id", trace_id)
        span.set_attribute("executor.tool_count", len(records))
    return records


def _summarise(result: ToolResult) -> str:
    """Produce a short human-readable summary for logging and trace storage."""
    if result.status == "SUCCESS":
        keys = list((result.data or {}).keys())
        return f"SUCCESS keys={keys}"
    if result.status == "IDEMPOTENT":
        return "IDEMPOTENT — already done"
    if result.status == "ESCALATE":
        return f"ESCALATE code={result.error_code}"
    return f"FAILED code={result.error_code} msg={result.error_message}"
