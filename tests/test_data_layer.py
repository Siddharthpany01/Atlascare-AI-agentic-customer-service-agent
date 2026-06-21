"""
tests/test_data_layer.py
------------------------
Phase 2 CI gate.

What it tests
-------------
1. DataStore loads all four files without error
2. Correct number of records loaded
3. All models are the right Pydantic types (not raw dicts)
4. Key field values match the data files exactly
5. order line_ids are 1-indexed
6. LineItem and Case status enums are lowercase
7. payment_config has no COD in supported_methods
8. search_articles returns matches on tag overlap
9. SessionStore get/set/delete/TTL/get_or_create

Run with: pytest tests/test_data_layer.py -v
"""
import time
import pytest

from app.services.data_loader import DataStore
from app.services.session import SessionStore
from app.schemas.order import Order, LineItem
from app.schemas.customer import Customer, Case
from app.schemas.kb import Article
from app.schemas.payment import PaymentConfig
from app.schemas.trace import ToolResult, TraceRecord, ToolCallRecord


@pytest.fixture(scope="module")
def store() -> DataStore:
    return DataStore(data_dir="data")


# ── DataStore loading ─────────────────────────────────────────────────────────

def test_orders_loaded(store):
    assert len(store.orders) == 3

def test_customers_loaded(store):
    assert len(store.customers) == 2

def test_cases_loaded(store):
    assert len(store.cases) == 2

def test_articles_loaded(store):
    assert len(store.articles) == 4

def test_payment_config_loaded(store):
    assert store.payment_config is not None


# ── Correct Pydantic types ────────────────────────────────────────────────────

def test_orders_are_order_instances(store):
    for o in store.orders.values():
        assert isinstance(o, Order)

def test_customers_are_customer_instances(store):
    for c in store.customers.values():
        assert isinstance(c, Customer)

def test_cases_are_case_instances(store):
    for c in store.cases.values():
        assert isinstance(c, Case)

def test_articles_are_article_instances(store):
    for a in store.articles.values():
        assert isinstance(a, Article)

def test_payment_config_is_correct_type(store):
    assert isinstance(store.payment_config, PaymentConfig)


# ── Field value correctness ───────────────────────────────────────────────────

def test_known_order_exists(store):
    order = store.get_order("ORD-78321")
    assert order is not None
    assert order.customer_id == "CUST-001"
    assert order.status == "shipped"
    assert order.payment_method == "HDFC_CREDIT"

def test_known_customer_exists(store):
    customer = store.get_customer("CUST-001")
    assert customer is not None
    assert customer.name == "Priya Sharma"
    assert customer.tier == "gold"

def test_known_case_exists(store):
    case = store.get_case("CASE-A1B2C3")
    assert case is not None
    assert case.customer_id == "CUST-001"
    assert case.status == "open"
    assert case.priority == "high"
    assert case.amount_inr == 42000.0

def test_payment_config_values(store):
    pc = store.payment_config
    assert pc.auto_refund_limit_inr == 25000
    assert pc.refund_sla_days == 5
    assert pc.behaviour.failure_rate == 0.03


# ── Schema contract enforcement ───────────────────────────────────────────────

def test_line_ids_are_one_indexed(store):
    """line_id must start at 1, not 0."""
    for order in store.orders.values():
        ids = [i.line_id for i in order.items]
        assert ids == list(range(1, len(ids) + 1)), \
            f"{order.order_id} line_ids are not 1-indexed: {ids}"

def test_lineitem_status_is_lowercase(store):
    """Status enum values must be lowercase per schema."""
    for order in store.orders.values():
        for item in order.items:
            assert item.status in ("active", "cancelled"), \
                f"Unexpected item status: {item.status}"

def test_case_status_is_lowercase(store):
    for case in store.cases.values():
        assert case.status in ("open", "in_progress", "resolved", "closed")

def test_cod_not_in_payment_supported_methods(store):
    """COD cannot be refunded via gateway — must not appear in supported_methods."""
    assert "COD" not in store.payment_config.supported_methods

def test_order_total_equals_active_items_sum(store):
    for order in store.orders.values():
        expected = sum(
            i.unit_price * i.quantity
            for i in order.items
            if i.status == "active"
        )
        assert abs(order.total_amount - expected) < 0.01, \
            f"{order.order_id}: total_amount {order.total_amount} != {expected}"


# ── Convenience helpers ───────────────────────────────────────────────────────

def test_get_order_returns_none_for_unknown(store):
    assert store.get_order("ORD-99999") is None

def test_get_customer_returns_none_for_unknown(store):
    assert store.get_customer("CUST-999") is None

def test_get_cases_for_customer(store):
    cases = store.get_cases_for_customer("CUST-001")
    assert len(cases) >= 1
    assert all(c.customer_id == "CUST-001" for c in cases)

def test_get_item_by_line_id(store):
    order = store.get_order("ORD-78321")
    item = order.get_item_by_line_id(1)
    assert item is not None
    assert item.line_id == 1

def test_get_item_by_line_id_missing(store):
    order = store.get_order("ORD-78321")
    assert order.get_item_by_line_id(99) is None

def test_search_articles_by_tag(store):
    results = store.search_articles(["refund"])
    assert len(results) >= 1
    assert all(isinstance(a, Article) for a in results)

def test_search_articles_no_match(store):
    results = store.search_articles(["nonexistent_tag_xyz"])
    assert results == []

def test_search_articles_case_insensitive(store):
    lower = store.search_articles(["refund"])
    upper = store.search_articles(["REFUND"])
    assert len(lower) == len(upper)


# ── SessionStore ──────────────────────────────────────────────────────────────

@pytest.fixture
def session():
    return SessionStore()

def test_session_set_and_get(session):
    session.set("sess-001", {"turn": 1})
    data = session.get("sess-001")
    assert data == {"turn": 1}

def test_session_get_missing_returns_none(session):
    assert session.get("sess-nonexistent") is None

def test_session_delete(session):
    session.set("sess-002", {"x": 1})
    session.delete("sess-002")
    assert session.get("sess-002") is None

def test_session_ttl_expiry(session):
    """Entries older than TTL must be evicted on get()."""
    session.set("sess-003", {"x": 1})
    # Manually backdate the entry
    session._store["sess-003"]["updated_at"] -= 99999
    assert session.get("sess-003") is None

def test_session_get_or_create_new(session):
    data = session.get_or_create("sess-004")
    assert "turn" in data
    assert data["turn"] == 0

def test_session_get_or_create_existing(session):
    session.set("sess-005", {"turn": 3, "tool_results": ["x"]})
    data = session.get_or_create("sess-005")
    assert data["turn"] == 3

def test_session_active_count(session):
    session.set("sess-006", {})
    session.set("sess-007", {})
    assert session.active_count >= 2


# ── Shared schema models ──────────────────────────────────────────────────────

def test_tool_result_success():
    r = ToolResult(status="SUCCESS", data={"order_id": "ORD-78321"}, latency_ms=12.5)
    assert r.status == "SUCCESS"
    assert r.data["order_id"] == "ORD-78321"

def test_tool_result_failed():
    r = ToolResult(status="FAILED", error_code="ORDER_NOT_FOUND", error_message="Not found")
    assert r.error_code == "ORDER_NOT_FOUND"

def test_tool_result_escalate():
    r = ToolResult(status="ESCALATE", error_code="COD_NOT_REFUNDABLE")
    assert r.status == "ESCALATE"

def test_trace_record_defaults():
    t = TraceRecord(trace_id="trc_test_001", session_id="sess-abc")
    assert t.tool_calls == []
    assert t.escalation_triggered is False
    assert t.model_calls == 0
    assert t.timestamp_utc is not None
