"""
app/schemas/order.py
--------------------
Pydantic models for order data.
Field names and enums derived directly from orders.json schema —
do not rename fields to match guide text; use the schema as the source of truth.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, field_validator


class ShippingAddress(BaseModel):
    line1: str
    line2: Optional[str] = None
    city: str
    state: str
    pincode: str


class LineItem(BaseModel):
    line_id: int                                    # 1-indexed, per schema
    product_id: str
    name: str
    quantity: int
    unit_price: float
    status: Literal["active", "cancelled"]          # lowercase per schema enum


class Order(BaseModel):
    order_id: str
    customer_id: str
    status: Literal["placed", "processing", "shipped", "delivered", "cancelled"]
    created_at: Optional[str] = None
    estimated_delivery: Optional[str] = None
    tracking_number: Optional[str] = None
    shipping_address: ShippingAddress
    items: list[LineItem]
    total_amount: float
    payment_method: Literal["HDFC_CREDIT", "ICICI_DEBIT", "SBI_NETBANKING", "UPI", "COD"]

    @field_validator("items")
    @classmethod
    def at_least_one_item(cls, v: list[LineItem]) -> list[LineItem]:
        if len(v) < 1:
            raise ValueError("Order must have at least one item")
        return v

    def get_item_by_line_id(self, line_id: int) -> Optional[LineItem]:
        """Look up a line item by its 1-indexed line_id. Returns None if not found."""
        return next((i for i in self.items if i.line_id == line_id), None)

    @property
    def active_items(self) -> list[LineItem]:
        return [i for i in self.items if i.status == "active"]
