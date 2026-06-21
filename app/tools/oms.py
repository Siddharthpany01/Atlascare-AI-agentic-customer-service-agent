"""
app/tools/oms.py — Order Management System adapter.

Rules of engagement for this module:
- cancel_item uses line_id (1-indexed), never array position
- LineItem status comparisons are lowercase ('cancelled', 'active')
- Never raise unhandled exceptions — always return ToolResult
"""

import time
from typing import Any

from app.schemas.order import ShippingAddress
from app.schemas.trace import ToolResult
from app.services.data_loader import get_store


class OMSAdapter:
    """Thin adapter over the in-memory DataStore for order operations."""

    # ------------------------------------------------------------------
    # get_order
    # ------------------------------------------------------------------

    def get_order(self, order_id: str) -> ToolResult:
        """Return order details, or FAILED if not found."""
        t0 = time.perf_counter()
        try:
            store = get_store()
            order = store.get_order(order_id)

            if order is None:
                return ToolResult(
                    status="FAILED",
                    error_code="ORDER_NOT_FOUND",
                    error_message=f"No order found with id '{order_id}'",
                    latency_ms=_ms(t0),
                )

            return ToolResult(
                status="SUCCESS",
                data=order.model_dump(),
                latency_ms=_ms(t0),
            )

        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                status="FAILED",
                error_code="OMS_INTERNAL_ERROR",
                error_message=str(exc),
                latency_ms=_ms(t0),
            )

    # ------------------------------------------------------------------
    # cancel_item
    # ------------------------------------------------------------------

    def cancel_item(self, order_id: str, line_id: int | None) -> ToolResult:
        """
        Cancel a single line item by its 1-indexed line_id.

        Returns:
          IDEMPOTENT  — item was already cancelled
          SUCCESS     — item just cancelled
          FAILED      — order or item not found, or order not cancellable
        """
        t0 = time.perf_counter()
        try:
            store = get_store()
            order = store.get_order(order_id)

        
            if order is None:
                return ToolResult(
                    status="FAILED",
                    error_code="ORDER_NOT_FOUND",
                    error_message=f"No order found with id '{order_id}'",
                    latency_ms=_ms(t0),
                )
            
            if order and line_id is None:
                if order.status in ("delivered", "cancelled"):
                    return ToolResult(
                        status="FAILED",
                        error_code="ORDER_CANCEL_NOT_ALLOWED",
                        error_message=(
                            f"Cannot cancel order in status '{order.status}'"
                        ),
                        latency_ms=_ms(t0),
                    )
                else:
                    return ToolResult(
                    status="SUCCESS",
                    data={
                    "order_id": order_id,
                    "message": f"order {order_id} cancelled successfully",
                },
                    latency_ms=_ms(t0),
                )
                    order.status = "cancelled"
        

            # Find item by line_id (1-indexed — never use array position)
            item = next(
                (i for i in order.items if i.line_id == line_id), None
            )


            if item is None:
                return ToolResult(
                    status="FAILED",
                    error_code="ITEM_NOT_FOUND",
                    error_message=(
                        f"No line item with line_id={line_id} in order '{order_id}'"
                    ),
                    latency_ms=_ms(t0),
                )

            # Idempotency check — lowercase per schema
            if item.status == "cancelled":
                return ToolResult(
                    status="IDEMPOTENT",
                    data={
                        "order_id": order_id,
                        "line_id": line_id,
                        "message": "Item was already cancelled",
                    },
                    latency_ms=_ms(t0),
                )

            # Mutate the in-memory record
            item.status = "cancelled"

            return ToolResult(
                status="SUCCESS",
                data={
                    "order_id": order_id,
                    "line_id": line_id,
                    "product_name": getattr(item, "product_name", None),
                    "message": f"Line item {line_id} is now cancelled successfully",
                },
                latency_ms=_ms(t0),
            )

        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                status="FAILED",
                error_code="OMS_INTERNAL_ERROR",
                error_message=str(exc),
                latency_ms=_ms(t0),
            )

    # ------------------------------------------------------------------
    # update_address
    # ------------------------------------------------------------------

    def update_address(self, order_id: str, address: dict[str, Any] | None) -> ToolResult:
        """
        Replace the shipping address on an order.

        The caller is responsible for providing a valid address dict.
        No address schema validation is performed here — that belongs in
        the Policy Engine / planner layer.
        """
        t0 = time.perf_counter()
        try:
            store = get_store()
            order = store.get_order(order_id)

            if order is None:
                return ToolResult(
                    status="FAILED",
                    error_code="ORDER_NOT_FOUND",
                    error_message=f"No order found with id '{order_id}'",
                    latency_ms=_ms(t0),
                )

            if order.status in ("delivered", "cancelled"):
                return ToolResult(
                    status="FAILED",
                    error_code="ADDRESS_UPDATE_NOT_ALLOWED",
                    error_message=(
                        f"Cannot update address on order in status '{order.status}'"
                    ),
                    latency_ms=_ms(t0),
                )

            if address is None or not isinstance(address, dict):
                return ToolResult(
                    status="FAILED",
                    error_code="INVALID_ADDRESS",
                    error_message="Address payload is missing or invalid",
                    latency_ms=_ms(t0),
                )

            if label := address.get("label"):
                customer = store.get_customer(order.customer_id)
                if customer is None:
                    return ToolResult(
                        status="FAILED",
                        error_code="CUSTOMER_NOT_FOUND",
                        error_message=(
                            f"No customer found for order '{order_id}'"
                        ),
                        latency_ms=_ms(t0),
                    )

                labeled_address = next(
                    (a for a in (customer.addresses or []) if a.label == label),
                    None,
                )
                if labeled_address is None:
                    return ToolResult(
                        status="FAILED",
                        error_code="ADDRESS_LABEL_NOT_FOUND",
                        error_message=(
                            f"No address found with label '{label}' for customer '{customer.customer_id} in records'"
                        ),
                        latency_ms=_ms(t0),
                    )

                address = labeled_address.model_dump()

            normalized_address = _normalize_shipping_address(address)
            order.shipping_address = ShippingAddress(**normalized_address)

            return ToolResult(
                status="SUCCESS",
                data={
                    "order_id": order_id,
                    "new_address": normalized_address,
                    "message": "Shipping address updated successfully",
                },
                latency_ms=_ms(t0),
            )

        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                status="FAILED",
                error_code="OMS_INTERNAL_ERROR",
                error_message=str(exc),
                latency_ms=_ms(t0),
            )


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _normalize_shipping_address(address: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate an order shipping address payload."""
    line1_raw = address.get("line1")
    if not isinstance(line1_raw, str) or not line1_raw.strip():
        raise ValueError("Address line1 must be a non-empty string")

    line1, line2 = _split_line1(line1_raw)

    city = address.get("city")
    state = address.get("state")
    pincode = address.get("pincode")
    if not all(isinstance(value, str) and value.strip() for value in (city, state, pincode)):
        raise ValueError("Address city, state, and pincode must be non-empty strings")

    normalized = {
        "line1": line1,
        "city": city.strip(), #type: ignore
        "state": state.strip(), #type: ignore
        "pincode": pincode.strip(), #type: ignore
    }
    if line2 is not None:
        normalized["line2"] = line2
    return normalized


def _split_line1(raw_line1: str) -> tuple[str, None | str]:
    """Split line1 into line1 and line2 using the first comma."""
    parts = [part.strip() for part in raw_line1.split(",", 1)]
    if len(parts) == 2:
        return parts[0], parts[1] or None
    return parts[0], None


# ------------------------------------------------------------------
# Module-level singleton (mirrors get_store pattern)
# ------------------------------------------------------------------

_oms_adapter: OMSAdapter | None = None


def get_oms() -> OMSAdapter:
    """Return the module-level OMSAdapter singleton."""
    global _oms_adapter
    if _oms_adapter is None:
        _oms_adapter = OMSAdapter()
    return _oms_adapter


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _ms(t0: float) -> float:
    """Return elapsed milliseconds since t0 (perf_counter)."""
    return round((time.perf_counter() - t0) * 1000, 2)
