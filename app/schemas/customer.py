"""
app/schemas/customer.py
-----------------------
Pydantic models for CRM data.
Both Customer and Case live here because they come from the same file (crm_cases.json)
and are always loaded together.
Field names derived directly from crm_cases.json schema.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, EmailStr


class Address(BaseModel):
    label: str                              # e.g. "home", "office"
    line1: str
    city: str
    state: str
    pincode: str


class Customer(BaseModel):
    customer_id: str                        # pattern: CUST-000
    name: str
    email: str                              # EmailStr validation omitted to avoid
    phone: str                              # extra dependency; validated by pattern in schema
    tier: Literal["standard", "silver", "gold", "platinum"]
    order_ids: list[str]
    preferred_refund_method: Optional[
        Literal["HDFC_CREDIT", "ICICI_DEBIT", "SBI_NETBANKING", "UPI", "original"]
    ] = None
    addresses: Optional[list[Address]] = None


class Case(BaseModel):
    case_id: str                            # pattern: CASE-XXXXXX (6 alphanumeric)
    customer_id: str
    order_id: str
    status: Literal["open", "in_progress", "resolved", "closed"]
    priority: Literal["low", "medium", "high"]
    description: str
    amount_inr: Optional[float] = None      # null if case is not refund-related
    trace_id: Optional[str] = None          # trace_id of the interaction that created this case
    created_at: str                         # ISO 8601 datetime string
