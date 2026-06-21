"""
tests/test_tool_adapters.py — Phase 3 CI gate.

Tests cover:
  OMS:      get_order (found / not found), cancel_item (success / idempotent /
            item-not-found / order-not-found), update_address (success /
            delivered-order blocked / not-found)
  CRM:      get_customer (found / not found), create_case (success /
            customer-not-found), created case appears in store
  KB:       search (hits / empty result / empty-tags error)
  Payments: COD escalation, threshold escalation, success path,
            refund_sla_days present in SUCCESS data
  Registry: all six keys present, correct policy tags
"""

import pytest

from app.schemas.trace import ToolResult
from app.services.data_loader import DataStore, get_store
from app.schemas.order import LineItem, Order, ShippingAddress
from app.tools.crm import CRMAdapter
from app.tools.kb import KBAdapter
from app.tools.oms import OMSAdapter
from app.tools.payments import PaymentsAdapter
from app.tools.registry import TOOL_REGISTRY


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_store(tmp_path):
    """
    Re-load a fresh DataStore from the real data/ directory before each
    test so mutations in one test don't bleed into the next.
    """
    get_store.cache_clear()
    # Point DataStore at the real data directory (relative to project root)
    import app.services.data_loader as dl
    #dl._store = None  # clear any cached singleton set outside lru_cache

    yield

    get_store.cache_clear()
    #store=get_store()


@pytest.fixture
def oms():
    return OMSAdapter()


@pytest.fixture
def crm():
    return CRMAdapter()


@pytest.fixture
def kb():
    return KBAdapter()


@pytest.fixture
def pay():
    return PaymentsAdapter()


# Helper — known IDs from data fixtures (contract.md J1/J2/J3 coverage)
KNOWN_ORDER_ID   = "ORD-78321"   # J1: shipped
KNOWN_ORDER_ID_2 = "ORD-78450"   # J2: delivered
KNOWN_CUSTOMER   = "CUST-001"


# ──────────────────────────────────────────────────────────────────────
# OMS — get_order
# ──────────────────────────────────────────────────────────────────────

class TestOMSGetOrder:

    def test_found_returns_success(self, oms):
        result = oms.get_order(KNOWN_ORDER_ID)
        assert result.status == "SUCCESS"
        assert result.data is not None
        assert result.data["order_id"] == KNOWN_ORDER_ID

    def test_not_found_returns_failed(self, oms):
        result = oms.get_order("ORD-NOPE")
        assert result.status == "FAILED"
        assert result.error_code == "ORDER_NOT_FOUND"

    def test_latency_ms_is_positive(self, oms):
        result = oms.get_order(KNOWN_ORDER_ID)
        assert result.latency_ms >= 0


# ──────────────────────────────────────────────────────────────────────
# OMS — cancel_item
# ──────────────────────────────────────────────────────────────────────

class TestOMSCancelItem:

    def test_cancel_active_item_success(self, oms):
        # Get first active line item's line_id from the known order
        store = get_store()
        order = store.get_order(KNOWN_ORDER_ID)
        active = next(i for i in order.items if i.status == "active")

        result = oms.cancel_item(KNOWN_ORDER_ID, active.line_id)
        assert result.status == "SUCCESS"
        assert result.data["line_id"] == active.line_id

    def test_cancel_already_cancelled_is_idempotent(self, oms):
        store = get_store()
        order = store.get_order(KNOWN_ORDER_ID)
        active = next(i for i in order.items if i.status == "active")

        # Cancel once
        oms.cancel_item(KNOWN_ORDER_ID, active.line_id)
        # Cancel again — must be IDEMPOTENT, not SUCCESS
        result = oms.cancel_item(KNOWN_ORDER_ID, active.line_id)
        assert result.status == "IDEMPOTENT"

    def test_cancel_invalid_line_id_fails(self, oms):
        result = oms.cancel_item(KNOWN_ORDER_ID, 9999)
        assert result.status == "FAILED"
        assert result.error_code == "ITEM_NOT_FOUND"

    def test_cancel_unknown_order_fails(self, oms):
        result = oms.cancel_item("ORD-NOPE", 1)
        assert result.status == "FAILED"
        assert result.error_code == "ORDER_NOT_FOUND"

    def test_line_id_is_one_indexed_not_zero(self, oms):
        # line_id=0 should fail (items start at 1)
        result = oms.cancel_item(KNOWN_ORDER_ID, 0)
        assert result.status == "FAILED"
        assert result.error_code == "ITEM_NOT_FOUND"


# ──────────────────────────────────────────────────────────────────────
# OMS — update_address
# ──────────────────────────────────────────────────────────────────────

class TestOMSUpdateAddress:

    NEW_ADDR = {
        "line1": "456 New Street",
        "city": "Mumbai",
        "state": "Maharashtra",
        "pincode": "400001",
    }

    def test_update_shipped_order_success(self, oms):
        result = oms.update_address(KNOWN_ORDER_ID, self.NEW_ADDR)
        assert result.status == "SUCCESS"
        assert result.data["new_address"] == self.NEW_ADDR

    def test_update_address_by_label_success(self, oms):
        result = oms.update_address(KNOWN_ORDER_ID, {"label": "home"})
        assert result.status == "SUCCESS"
        assert result.data["new_address"] == {
            "line1": "12 MG Road",
            "line2": "Koramangala",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560034",
        }

    def test_update_address_with_unknown_label_fails(self, oms):
        result = oms.update_address(KNOWN_ORDER_ID, {"label": "vacation"})
        assert result.status == "FAILED"
        assert result.error_code == "ADDRESS_LABEL_NOT_FOUND"

    def test_update_delivered_order_blocked(self, oms):
        # J2 order (ORD-78450) is delivered — should be blocked
        result = oms.update_address(KNOWN_ORDER_ID_2, self.NEW_ADDR)
        assert result.status == "FAILED"
        assert result.error_code == "ADDRESS_UPDATE_NOT_ALLOWED"

    def test_update_unknown_order_fails(self, oms):
        result = oms.update_address("ORD-NOPE", self.NEW_ADDR)
        assert result.status == "FAILED"
        assert result.error_code == "ORDER_NOT_FOUND"


# ──────────────────────────────────────────────────────────────────────
# CRM — get_customer
# ──────────────────────────────────────────────────────────────────────

class TestCRMGetCustomer:

    def test_known_customer_success(self, crm):
        result = crm.get_customer(KNOWN_CUSTOMER)
        assert result.status == "SUCCESS"
        assert result.data["customer_id"] == KNOWN_CUSTOMER

    def test_unknown_customer_failed(self, crm):
        result = crm.get_customer("CUST-999")
        assert result.status == "FAILED"
        assert result.error_code == "CUSTOMER_NOT_FOUND"


# ──────────────────────────────────────────────────────────────────────
# CRM — create_case
# ──────────────────────────────────────────────────────────────────────

class TestCRMCreateCase:

    def test_create_case_success(self, crm):
        result = crm.create_case(
            customer_id=KNOWN_CUSTOMER,
            order_id=KNOWN_ORDER_ID,
            description="Test case from Phase 3 CI",
            amount_inr=5000.0,
            trace_id="trc_test_001",
        )
        assert result.status == "SUCCESS"
        assert result.data["customer_id"] == KNOWN_CUSTOMER
        assert result.data["status"] == "open"
        assert result.data["case_id"].startswith("CASE-")

    def test_new_case_appears_in_store(self, crm):
        result = crm.create_case(
            customer_id=KNOWN_CUSTOMER,
            order_id=KNOWN_ORDER_ID,
            description="Verify persistence",
            amount_inr=None,
            trace_id="trc_test_002",
        )
        new_id = result.data["case_id"]
        store = get_store()
        assert new_id in store.cases
        assert store.cases[new_id].customer_id == KNOWN_CUSTOMER

    def test_create_case_unknown_customer_fails(self, crm):
        result = crm.create_case(
            customer_id="CUST-999",
            order_id=KNOWN_ORDER_ID,
            description="Should fail",
            amount_inr=None,
            trace_id="trc_test_003",
        )
        assert result.status == "FAILED"
        assert result.error_code == "CUSTOMER_NOT_FOUND"

    def test_create_case_null_amount_allowed(self, crm):
        result = crm.create_case(
            customer_id=KNOWN_CUSTOMER,
            order_id=KNOWN_ORDER_ID,
            description="No amount",
            amount_inr=None,
            trace_id="trc_test_004",
        )
        assert result.status == "SUCCESS"
        assert result.data["amount_inr"] is None

    def test_create_case_with_order_id_derives_customer(self, crm):
        # Pass customer_id=None; derive it from order_id
        result = crm.create_case(
            customer_id=None,  # type: ignore[arg-type]
            order_id=KNOWN_ORDER_ID,
            description="Case created via order_id lookup",
            amount_inr=1500.0,
            trace_id="trc_test_005",
        )
        assert result.status == "SUCCESS"
        assert result.data["customer_id"] == KNOWN_CUSTOMER
        assert result.data["order_id"] == KNOWN_ORDER_ID


# ──────────────────────────────────────────────────────────────────────
# KB — search
# ──────────────────────────────────────────────────────────────────────

class TestKBSearch:

    def test_search_with_matching_tag_returns_articles(self, kb):
        result = kb.search(["return"])
        assert result.status == "SUCCESS"
        assert result.data["count"] >= 1

    def test_search_returns_count_field(self, kb):
        result = kb.search(["reship"])
        assert result.status == "SUCCESS"
        assert "count" in result.data
        assert result.data["count"] == len(result.data["articles"])

    def test_search_no_match_returns_empty_success(self, kb):
        result = kb.search(["zzz_no_such_tag_xyz"])
        assert result.status == "SUCCESS"
        assert result.data["count"] == 0
        assert result.data["articles"] == []

    def test_empty_tags_returns_failed(self, kb):
        result = kb.search([])
        assert result.status == "FAILED"
        assert result.error_code == "KB_EMPTY_TAGS"

    def test_search_is_case_insensitive(self, kb):
        lower = kb.search(["returns"])
        upper = kb.search(["RETURNS"])
        assert lower.data["count"] == upper.data["count"]


# ──────────────────────────────────────────────────────────────────────
# Payments — initiate_refund
# ──────────────────────────────────────────────────────────────────────

class TestPaymentsInitiateRefund:

    def test_cod_escalates_immediately(self, pay):
        result = pay.initiate_refund(
            method="COD",
            amount=500.0,
            customer_id=KNOWN_CUSTOMER,
            order_id=KNOWN_ORDER_ID,
        )
        assert result.status == "ESCALATE"
        assert result.error_code == "COD_NOT_SUPPORTED"

    def test_cod_escalates_even_under_threshold(self, pay):
        """COD check must fire BEFORE threshold check."""
        result = pay.initiate_refund(
            method="COD",
            amount=100.0,
            customer_id=KNOWN_CUSTOMER,
            order_id=KNOWN_ORDER_ID,
        )
        assert result.status == "ESCALATE"
        assert result.error_code == "COD_NOT_SUPPORTED"

    def test_amount_above_limit_escalates(self, pay):
        store = get_store()
        # Add a temporary high-value order so the limit can be exercised.
        high_value_order = Order(
            order_id="ORD-99999",
            customer_id=KNOWN_CUSTOMER,
            status="shipped",
            shipping_address=ShippingAddress(
                line1="100 Test Street",
                city="Bengaluru",
                state="Karnataka",
                pincode="560100",
            ),
            items=[LineItem(
                line_id=1,
                product_id="PROD-HIGH-001",
                name="Premium Gadget",
                quantity=1,
                unit_price=30_000.0,
                status="active",
            )],
            total_amount=30_000.0,
            payment_method="UPI",
        )
        store.orders[high_value_order.order_id] = high_value_order
        result = pay.initiate_refund(
            method="UPI",
            amount=30_000.0,
            customer_id=KNOWN_CUSTOMER,
            order_id=high_value_order.order_id,
        )
        assert result.status == "ESCALATE"
        assert result.error_code == "REFUND_EXCEEDS_AUTO_LIMIT"

    def test_amount_at_limit_does_not_escalate(self, pay, monkeypatch):
        """Exactly 25_000 should NOT escalate (> not >=)."""
        # Disable random failure for deterministic test
        monkeypatch.setattr("app.tools.payments.random.random", lambda: 1.0)
        store = get_store()
        limit_order = Order(
            order_id="ORD-88888",
            customer_id=KNOWN_CUSTOMER,
            status="shipped",
            shipping_address=ShippingAddress(
                line1="200 Test Avenue",
                city="Bengaluru",
                state="Karnataka",
                pincode="560101",
            ),
            items=[LineItem(
                line_id=1,
                product_id="PROD-LIMIT-001",
                name="Limit Gadget",
                quantity=1,
                unit_price=25_000.0,
                status="active",
            )],
            total_amount=25_000.0,
            payment_method="UPI",
        )
        store.orders[limit_order.order_id] = limit_order
        result = pay.initiate_refund(
            method="UPI",
            amount=25_000.0,
            customer_id=KNOWN_CUSTOMER,
            order_id=limit_order.order_id,
        )
        assert result.status == "SUCCESS"

    def test_success_contains_refund_sla_days(self, pay, monkeypatch):
        monkeypatch.setattr("app.tools.payments.random.random", lambda: 1.0)
        result = pay.initiate_refund(
            method="UPI",
            amount=12_500.0,
            customer_id=KNOWN_CUSTOMER,
            order_id=KNOWN_ORDER_ID,
        )
        assert result.status == "SUCCESS"
        assert "refund_sla_days" in result.data
        assert isinstance(result.data["refund_sla_days"], int)

    def test_gateway_timeout_simulation(self, pay, monkeypatch):
        """Force failure_rate to 1.0 to always trigger timeout."""
        monkeypatch.setattr("app.tools.payments.random.random", lambda: 0.0)
        # Patch failure_rate on the config object
        store = get_store()
        original = store.payment_config.behaviour.failure_rate
        store.payment_config.behaviour.failure_rate = 1.0

        result = pay.initiate_refund(
            method="UPI",
            amount=1500.0,
            customer_id=KNOWN_CUSTOMER,
            order_id=KNOWN_ORDER_ID,
            line_id=2,
        )
        assert result.status == "FAILED"
        assert result.error_code == "PAYMENT_GATEWAY_TIMEOUT"

        # Restore
        store.payment_config.behaviour.failure_rate = original

    def test_cod_case_insensitive(self, pay):
        """'cod', 'Cod', 'COD' all escalate."""
        for variant in ("cod", "Cod", "COD"):
            result = pay.initiate_refund(
                method=variant,
                amount=500.0,
                customer_id=KNOWN_CUSTOMER,
                order_id=KNOWN_ORDER_ID,
            )
            assert result.status == "ESCALATE", f"Expected ESCALATE for method={variant!r}"


# ──────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────

class TestToolRegistry:

    EXPECTED_KEYS = {
        "oms_get_order",
        "oms_cancel_item",
        "oms_update_address",
        "payments_refund",
        "crm_create_case",
        "kb_search",
        "case_search",
    }

    def test_all_six_keys_present(self):
        assert set(TOOL_REGISTRY.keys()) == self.EXPECTED_KEYS

    def test_each_entry_has_handler(self):
        for name, defn in TOOL_REGISTRY.items():
            assert callable(defn["handler"]), f"{name}: handler must be callable"

    def test_each_entry_has_policy_list(self):
        for name, defn in TOOL_REGISTRY.items():
            assert isinstance(defn["policy"], list), f"{name}: policy must be a list"
            assert len(defn["policy"]) >= 1, f"{name}: policy list must not be empty"

    def test_read_only_tools_have_no_write_tag(self):
        for key in ("oms_get_order", "kb_search"):
            assert "WRITE" not in TOOL_REGISTRY[key]["policy"]

    def test_payments_refund_has_threshold_check(self):
        assert "THRESHOLD_CHECK" in TOOL_REGISTRY["payments_refund"]["policy"]

    def test_cancel_item_has_requires_confirm(self):
        assert "REQUIRES_CONFIRM" in TOOL_REGISTRY["oms_cancel_item"]["policy"]

    def test_all_tool_results_are_tool_result_instances(self, monkeypatch):
        """Smoke test: call each handler and verify it returns a ToolResult."""
        monkeypatch.setattr("app.tools.payments.random.random", lambda: 1.0)
        calls = {
            "oms_get_order":      {"order_id": KNOWN_ORDER_ID},
            "oms_cancel_item":    {"order_id": "ORD-NOPE", "line_id": 1},
            "oms_update_address": {"order_id": "ORD-NOPE", "address": {}},
            "payments_refund":    {"customer_id": KNOWN_CUSTOMER, "amount": 500.0, "method": "UPI"},
            "crm_create_case":    {
                "customer_id": KNOWN_CUSTOMER,
                "order_id": KNOWN_ORDER_ID,
                "description": "smoke test",
                "amount_inr": None,
                "trace_id": "trc_smoke",
            },
            "kb_search":          {"tags": ["returns"]},
        }
        for name, args in calls.items():
            result = TOOL_REGISTRY[name]["handler"](**args)
            assert isinstance(result, ToolResult), (
                f"{name} did not return a ToolResult (got {type(result)})"
            )
