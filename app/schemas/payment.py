"""
app/schemas/payment.py
----------------------
Pydantic models for payment gateway configuration.
Field names derived directly from payment_config.json schema.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel


class PaymentBehaviour(BaseModel):
    """Simulated gateway behaviour — used by payments adapter for chaos testing."""
    failure_rate: float                     # 0.0–1.0; 0.03 = 3% random 504 timeout
    failure_code: Optional[str] = None      # e.g. "504"
    failure_message: Optional[str] = None  # e.g. "PAYMENT_GATEWAY_TIMEOUT"


class PaymentConfig(BaseModel):
    auto_refund_limit_inr: float            # const 25000 — maximum for auto-processing
    supported_methods: list[
        Literal["HDFC_CREDIT", "ICICI_DEBIT", "SBI_NETBANKING", "UPI", "original"]
    ]                                       # NOTE: COD is NOT in this list
    refund_sla_days: int                    # business days for refund to appear in account
    behaviour: PaymentBehaviour
