import random

import pytest

from app.tools import payments
from app.schemas.trace import ToolResult


class FakeItem:
    def __init__(self, unit_price, quantity):
        self.unit_price = unit_price
        self.quantity = quantity


class FakeOrder:
    def __init__(self, order_id, total_amount, items, status, customer_id):
        self.order_id = order_id
        self.total_amount = total_amount
        self.items = items
        self.status = status
        self.customer_id = customer_id
        self.payment_method = None


class FakeCustomer:
    def __init__(self, customer_id, tier=None):
        self.customer_id = customer_id
        self.tier = tier


class FakeBehaviour:
    def __init__(self, failure_rate=0.0):
        self.failure_rate = failure_rate


class FakePaymentConfig:
    def __init__(self, refund_sla_days=3, failure_rate=0.0):
        self.refund_sla_days = refund_sla_days
        self.behaviour = FakeBehaviour(failure_rate=failure_rate)


class FakeStore:
    def __init__(self, orders=None, customers=None, payment_config=None):
        self._orders = {o.order_id: o for o in (orders or [])}
        self._customers = {c.customer_id: c for c in (customers or [])}
        self.payment_config = payment_config or FakePaymentConfig()

    def get_order(self, order_id):
        return self._orders.get(order_id)

    def get_customer(self, customer_id):
        return self._customers.get(customer_id)


@pytest.fixture(autouse=True)
def reset_payments_singleton():
    # Ensure the module-level singleton is reset for each test
    payments._payments_adapter = None
    yield
    payments._payments_adapter = None


def make_store(**kwargs):
    return FakeStore(**kwargs)


def test_missing_refund_info(monkeypatch):
    fake_store = make_store()
    monkeypatch.setattr(payments, "get_store", lambda: fake_store)
    adapter = payments.get_payments()

    res = adapter.initiate_refund()
    assert isinstance(res, ToolResult)
    assert res.status == "FAILED"
    assert res.error_code == "MISSING_REFUND_INFO"


def test_invalid_amount_when_both_provided(monkeypatch):
    order = FakeOrder("ORD1", 100.0, [FakeItem(50, 2)], "processing", "C1")
    fake_store = make_store(orders=[order])
    monkeypatch.setattr(payments, "get_store", lambda: fake_store)
    adapter = payments.get_payments()

    res = adapter.initiate_refund(method="UPI", amount="not-a-number", order_id="ORD1")
    assert res.status == "FAILED"
    assert res.error_code == "INVALID_AMOUNT"


def test_amount_without_order_id(monkeypatch):
    fake_store = make_store()
    monkeypatch.setattr(payments, "get_store", lambda: fake_store)
    adapter = payments.get_payments()

    res = adapter.initiate_refund(method="UPI", amount=50.0)
    assert res.status == "FAILED"
    assert res.error_code == "ORDER_ID_REQUIRED"


def test_order_not_found(monkeypatch):
    fake_store = make_store()
    monkeypatch.setattr(payments, "get_store", lambda: fake_store)
    adapter = payments.get_payments()

    res = adapter.initiate_refund(method="UPI", amount=100.0, order_id="MISSING")
    assert res.status == "FAILED"
    assert res.error_code == "ORDER_NOT_FOUND"


def test_amount_order_mismatch(monkeypatch):
    order = FakeOrder("ORD2", 200.0, [FakeItem(100, 2)], "processing", "C2")
    fake_store = make_store(orders=[order])
    monkeypatch.setattr(payments, "get_store", lambda: fake_store)
    adapter = payments.get_payments()

    res = adapter.initiate_refund(method="UPI", amount=150.0, order_id="ORD2")
    assert res.status == "FAILED"
    assert res.error_code == "AMOUNT_ORDER_MISMATCH"


def test_lineitem_amount_mismatch_and_item_not_found(monkeypatch):
    order = FakeOrder("ORD3", 300.0, [FakeItem(100, 3)], "processing", "C3")
    fake_store = make_store(orders=[order])
    monkeypatch.setattr(payments, "get_store", lambda: fake_store)
    adapter = payments.get_payments()

    # Mismatch for valid line
    res = adapter.initiate_refund(method="UPI", amount=50.0, order_id="ORD3", line_id=1)
    assert res.status == "FAILED"
    assert res.error_code == "AMOUNT_LINEITEM_MISMATCH"

    # Invalid line id
    res2 = adapter.initiate_refund(method="UPI", amount=100.0, order_id="ORD3", line_id=99)
    assert res2.status == "FAILED"
    assert res2.error_code == "ITEM_NOT_FOUND"


def test_cod_escalation_preempts_threshold(monkeypatch):
    # COD must escalate immediately
    order = FakeOrder("ORD4", 5000.0, [FakeItem(5000, 1)], "processing", "C4")
    fake_store = make_store(orders=[order])
    monkeypatch.setattr(payments, "get_store", lambda: fake_store)
    adapter = payments.get_payments()

    res = adapter.initiate_refund(method="COD", amount=5000.0, order_id="ORD4")
    assert res.status == "ESCALATE"
    assert res.error_code == "COD_NOT_SUPPORTED"


def test_refund_exceeds_auto_limit_and_delivered(monkeypatch):
    # Create a GOLD customer and an order exceeding standard limit
    order = FakeOrder("ORD5", 50_000.0, [FakeItem(50_000, 1)], "processing", "C5")
    cust = FakeCustomer("C5", tier="GOLD")
    fake_store = make_store(orders=[order], customers=[cust])
    monkeypatch.setattr(payments, "get_store", lambda: fake_store)
    adapter = payments.get_payments()

    res = adapter.initiate_refund(method="UPI", amount=50_000.0, order_id="ORD5")
    assert res.status == "ESCALATE"
    assert res.error_code == "REFUND_EXCEEDS_AUTO_LIMIT"

    # Delivered order escalates
    order_del = FakeOrder("ORD6", 100.0, [FakeItem(50, 2)], "delivered", "C6")
    fake_store2 = make_store(orders=[order_del])
    monkeypatch.setattr(payments, "get_store", lambda: fake_store2)
    adapter = payments.get_payments()
    res2 = adapter.initiate_refund(method="UPI", amount=100.0, order_id="ORD6")
    assert res2.status == "ESCALATE"
    assert res2.error_code == "ORDER_DELIVERED"


def test_gateway_timeout_simulation(monkeypatch):
    order = FakeOrder("ORD7", 100.0, [FakeItem(50, 2)], "processing", "C7")
    cfg = FakePaymentConfig(refund_sla_days=2, failure_rate=1.0)
    fake_store = make_store(orders=[order], payment_config=cfg)
    monkeypatch.setattr(payments, "get_store", lambda: fake_store)
    adapter = payments.get_payments()

    # With failure_rate=1.0 this should always simulate timeout
    res = adapter.initiate_refund(method="UPI", amount=100.0, order_id="ORD7")
    assert res.status == "FAILED"
    assert res.error_code == "PAYMENT_GATEWAY_TIMEOUT"


def test_success_refund(monkeypatch):
    order = FakeOrder("ORD8", 120.0, [FakeItem(60, 2)], "processing", "C8")
    cfg = FakePaymentConfig(refund_sla_days=5, failure_rate=0.0)
    cust = FakeCustomer("C8", tier="STANDARD")
    fake_store = make_store(orders=[order], customers=[cust], payment_config=cfg)
    monkeypatch.setattr(payments, "get_store", lambda: fake_store)
    adapter = payments.get_payments()

    res = adapter.initiate_refund(method="UPI", amount=120.0, order_id="ORD8")
    assert res.status == "SUCCESS"
    assert isinstance(res.data, dict)
    assert res.data.get("refund_sla_days") == 5
    assert res.data.get("amount_inr") == 120.0
