"""
app/tools/crm.py — Customer Relationship Management adapter.

Notes (from errors_and_decisions.md):
- Case and Customer both live in app/schemas/customer.py
- create_case generates a new case_id and appends to the in-memory store
- get_customer is implemented on the adapter but NOT registered in TOOL_REGISTRY
  (contract.md only registers crm_create_case)
"""

import time
import uuid
from datetime import datetime, timezone

from app.schemas.customer import Case, Customer
from app.schemas.order import Order
from app.schemas.trace import ToolResult
from app.services.data_loader import get_store


class CRMAdapter:
    """Thin adapter over the in-memory DataStore for CRM operations."""

    # ------------------------------------------------------------------
    # get_customer
    # ------------------------------------------------------------------

    def get_customer(self, customer_id: str) -> ToolResult:
        """
        Look up a customer by ID.

        Not registered in TOOL_REGISTRY — used internally by other
        adapters and tests, but not exposed to the planner.
        """
        t0 = time.perf_counter()
        try:
            store = get_store()
            customer = store.get_customer(customer_id)

            if customer is None:
                return ToolResult(
                    status="FAILED",
                    error_code="CUSTOMER_NOT_FOUND",
                    error_message=f"No customer found with id '{customer_id}'",
                    latency_ms=_ms(t0),
                )

            return ToolResult(
                status="SUCCESS",
                data=customer.model_dump(),
                latency_ms=_ms(t0),
            )

        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                status="FAILED",
                error_code="CRM_INTERNAL_ERROR",
                error_message=str(exc),
                latency_ms=_ms(t0),
            )
        
    # ------------------------------------------------------------------
    # get_case
    # ------------------------------------------------------------------
    def get_case(self, case_id: str) -> ToolResult:
        """
        Look up a support case by ID.

        Not registered in TOOL_REGISTRY — used internally by other
        adapters and tests, but not exposed to the planner.
        """
        t0 = time.perf_counter()
        try:
            store = get_store()
            case = store.get_case(case_id)

            if case is None:
                return ToolResult(
                    status="FAILED",
                    error_code="CASE_NOT_FOUND",
                    error_message=f"No case found with id '{case_id}'",
                    latency_ms=_ms(t0),
                )

            return ToolResult(
                status="SUCCESS",
                data=case.model_dump(),
                latency_ms=_ms(t0),
            )

        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                status="FAILED",
                error_code="CRM_INTERNAL_ERROR",
                error_message=str(exc),
                latency_ms=_ms(t0),
            )




    # ------------------------------------------------------------------
    # create_case
    # ------------------------------------------------------------------

    def create_case(
        self,
        customer_id: str | None,
        order_id: str,
        description: str,
        amount_inr: float | None,
        trace_id: str,
    ) -> ToolResult:
        """
        Create a new support case and append it to the in-memory store.

        Returns SUCCESS with the new case data, or FAILED if the
        customer does not exist.
        """
        t0 = time.perf_counter()
        try:
            store = get_store()

            if not customer_id and order_id:
                order = store.get_order(order_id)
                if order:
                    customer_id = order.customer_id
                else:
                    return ToolResult(
                        status="FAILED",
                        error_code="ORDER_NOT_FOUND",
                        error_message=f"Provided order_id '{order_id}' does not exist.",
                        latency_ms=_ms(t0),
                    )

            # Validate customer exists before creating a case for them
            if not customer_id:
                return ToolResult(
                    status="FAILED",
                    error_code="MISSING_CUSTOMER_ID",
                    error_message="Customer ID could not be determined from input or order.",
                    latency_ms=_ms(t0),
                )
            customer = store.get_customer(customer_id)
            if customer is None:
                return ToolResult(
                    status="FAILED",
                    error_code="CUSTOMER_NOT_FOUND",
                    error_message=(
                        f"Cannot create case: customer '{customer_id}' not found"
                    ),
                    latency_ms=_ms(t0),
                )

            # Generate a new case_id (CASE-XXXXXX pattern)
            suffix = uuid.uuid4().hex[:6].upper()
            new_case_id = f"CASE-{suffix}"

            # Build the Case record
            now_utc = datetime.now(timezone.utc).isoformat()
            new_case = Case(
                case_id=new_case_id,
                customer_id=customer_id,
                order_id=order_id,
                description=description,
                amount_inr=amount_inr,
                status="open",
                priority="medium",
                created_at=now_utc,
                trace_id=trace_id,
            )

            # Append to in-memory store
            store.cases[new_case_id] = new_case

            return ToolResult(
                status="SUCCESS",
                data=new_case.model_dump(),
                latency_ms=_ms(t0),
            )

        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                status="FAILED",
                error_code="CRM_INTERNAL_ERROR",
                error_message=str(exc),
                latency_ms=_ms(t0),
            )



# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_crm_adapter: CRMAdapter | None = None


def get_crm() -> CRMAdapter:
    """Return the module-level CRMAdapter singleton."""
    global _crm_adapter
    if _crm_adapter is None:
        _crm_adapter = CRMAdapter()
    return _crm_adapter


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)
