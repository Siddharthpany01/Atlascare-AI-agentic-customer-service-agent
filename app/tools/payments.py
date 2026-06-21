"""
app/tools/payments.py — Payments gateway adapter.

Critical ordering (from errors_and_decisions.md):
  1. COD check FIRST — return ESCALATE immediately, skip threshold
  2. Amount threshold check (> 25_000) — return ESCALATE
  3. Gateway failure simulation via behaviour.failure_rate
  4. SUCCESS — include refund_sla_days in data


"""

import random
import time

from app.schemas.trace import ToolResult
from app.services.data_loader import get_store
from app.config import settings


DEFAULT_REFUND_AUTO_LIMIT_INR: float = settings.REFUND_AUTO_LIMIT_INR


class PaymentsAdapter:
    """Mock payments gateway adapter with configurable failure simulation."""

    def initiate_refund(
        self,
        method: str | None = None,
        amount: float | None = None,
        customer_id: str | None = None,
        order_id: str | None = None,
        line_id: int | None = None,
    ) -> ToolResult:
        t0 = time.perf_counter()
        try:
            store = get_store()
            config = store.payment_config

            # Step 1: COD guard — must be checked before threshold logic
            if method and isinstance(method, str) and method.upper() == "COD":
                return ToolResult(
                    status="ESCALATE",
                    error_code="COD_NOT_SUPPORTED",
                    error_message=(
                        "Cash-on-delivery orders cannot be refunded via the "
                        "payment gateway. Escalating to human agent."
                    ),
                    latency_ms=_ms(t0),
                )

            # Require at least amount or order_id
            if amount is None and order_id is None:
                return ToolResult(
                    status="FAILED",
                    error_code="MISSING_REFUND_INFO",
                    error_message="Either amount or order_id must be provided to determine refund amount.",
                    latency_ms=_ms(t0),
                )

            # If an amount is provided we must also have an order id
            if amount is not None and order_id is None:
                return ToolResult(
                    status="FAILED",
                    error_code="ORDER_ID_REQUIRED",
                    error_message="Order ID is required when specifying a refund amount.",
                    latency_ms=_ms(t0),
                )

            # Validate numeric amount when provided
            if amount is not None and amount != 0.0:
                try:
                    amount = float(amount)  # type: ignore
                    if amount <= 0:
                        raise ValueError("Amount must be greater than zero.")
                except (TypeError, ValueError):
                    return ToolResult(
                        status="FAILED",
                        error_code="INVALID_AMOUNT",
                        error_message=f"Provided amount {amount} is invalid. Must be a number greater than zero.",
                        latency_ms=_ms(t0),
                    )

            # Load order when an order id is available
            order = store.get_order(order_id) if order_id is not None else None
            if order is None:
                return ToolResult(status="FAILED", error_code="ORDER_NOT_FOUND", error_message="Order not found.", latency_ms=_ms(t0))

            # Line-item handling
            if line_id is not None:
                if line_id < 1 or line_id > len(order.items):
                    return ToolResult(status="FAILED", error_code="ITEM_NOT_FOUND", error_message="Line item not found.", latency_ms=_ms(t0))
                item = order.items[line_id - 1]
                calc_amt = item.unit_price * item.quantity
                if amount is not None and calc_amt != amount:
                    return ToolResult(
                        status="FAILED",
                        error_code="AMOUNT_LINEITEM_MISMATCH",
                        error_message=(
                            f"Provided amount ₹{amount:,.2f} does not match the calculated amount for line_id {line_id} of order {order_id}."
                        ),
                        latency_ms=_ms(t0),
                    )
                amount = calc_amt
            else:
                # No line item: if amount not provided, use order total; if provided, ensure matches order total
                if amount is None:
                    amount = order.total_amount
                else:
                    if amount != order.total_amount:
                        return ToolResult(
                            status="FAILED",
                            error_code="AMOUNT_ORDER_MISMATCH",
                            error_message=(
                                f"Provided amount ₹{amount:,.2f} does not match the total amount of order {order_id}."
                            ),
                            latency_ms=_ms(t0),
                        )

            # Determine customer and tier
            customer = store.get_customer(str(order.customer_id)) if order and getattr(order, "customer_id", None) else None
            customer_tier = customer.tier if customer else None

            # Determine applicable auto-approval limit (default then per-tier)
            applicable_limit = DEFAULT_REFUND_AUTO_LIMIT_INR
            if customer_tier == "GOLD":
                applicable_limit = settings.GOLD_REFUND_AUTO_LIMIT_INR
            elif customer_tier == "STANDARD":
                applicable_limit = settings.STANDARD_REFUND_AUTO_LIMIT_INR

            # Step 2: Refund threshold check
            if amount > applicable_limit:
                return ToolResult(
                    status="ESCALATE",
                    error_code="REFUND_EXCEEDS_AUTO_LIMIT",
                    error_message=(
                        f"Refund amount ₹{amount:,.2f} exceeds the auto-approval limit of ₹{applicable_limit:,.0f} for Tier {customer_tier}. Escalating."
                    ),
                    latency_ms=_ms(t0),
                )

            # Delivered orders require escalation and should not be refunded automatically.
            if getattr(order, "status", None) == "delivered":
                return ToolResult(
                    status="ESCALATE",
                    error_code="ORDER_DELIVERED",
                    error_message=(
                        f"Order {order_id} has already been delivered. Refunds for delivered orders require manual review. Escalating."
                    ),
                    latency_ms=_ms(t0),
                )

            # Step 3: Gateway failure simulation
            failure_rate = getattr(getattr(config, "behaviour", None), "failure_rate", 0.0)
            if random.random() < failure_rate:
                return ToolResult(
                    status="FAILED",
                    error_code="PAYMENT_GATEWAY_TIMEOUT",
                    error_message=(
                        "Upstream payment gateway returned 504. Retry after backoff."
                    ),
                    latency_ms=_ms(t0),
                )

            # Step 4: SUCCESS
            if order is not None and method in (
                "HDFC_CREDIT",
                "ICICI_DEBIT",
                "SBI_NETBANKING",
                "UPI",
            ):
                order.payment_method = method

            return ToolResult(
                status="SUCCESS",
                data={
                    "customer_id": customer.customer_id if customer else None,
                    "amount_inr": amount,
                    "method": method,
                    "refund_sla_days": config.refund_sla_days,
                    "message": (
                        f"Refund of ₹{amount:,.2f} via {method or 'UNKNOWN'} initiated. "
                        f"Expect credit within {config.refund_sla_days} business days."
                    ),
                },
                latency_ms=_ms(t0),
            )

        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                status="FAILED",
                error_code="PAYMENTS_INTERNAL_ERROR",
                error_message=str(exc),
                latency_ms=_ms(t0),
            )


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_payments_adapter: PaymentsAdapter | None = None


def get_payments() -> PaymentsAdapter:
    """Return the module-level PaymentsAdapter singleton."""
    global _payments_adapter
    if _payments_adapter is None:
        _payments_adapter = PaymentsAdapter()
    return _payments_adapter


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)
