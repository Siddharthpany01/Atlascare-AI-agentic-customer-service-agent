"""
app/services/data_loader.py
---------------------------
Loads all four JSON data files into typed Pydantic models.
Exposes a DataStore singleton used by all tool adapters.

Loading pattern is driven by the ACTUAL file structures:

  orders.json        → { "orders": [...] }          top-level key: "orders"
  crm_cases.json     → { "customers": [...],         two arrays in one file
                         "cases": [...] }
  kb_articles.json   → { "articles": [...] }         top-level key: "articles"
  payment_config.json → flat object                  no array wrapper

Usage:
    from app.services.data_loader import get_store
    store = get_store()
    order = store.orders.get("ORD-78321")
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.schemas.customer import Case, Customer
from app.schemas.kb import Article
from app.schemas.order import Order
from app.schemas.payment import PaymentConfig

logger = logging.getLogger(__name__)


class DataStore:
    """
    In-memory store of all fixture data.
    Loaded once at startup; all dictionaries are keyed by their primary ID field.
    Tool adapters receive this store via dependency injection.
    """

    def __init__(self, data_dir: str = "data") -> None:
        base = Path(data_dir)

        # ── orders.json → { "orders": [ {...}, ... ] } ───────────────────────
        raw_orders = self._load_raw(base / "orders.json")
        self.orders: dict[str, Order] = {
            o["order_id"]: Order(**o)
            for o in raw_orders["orders"]
        }

        # ── crm_cases.json → { "customers": [...], "cases": [...] } ──────────
        # Both arrays live in the same file — load once, split into two dicts
        raw_crm = self._load_raw(base / "crm_cases.json")
        self.customers: dict[str, Customer] = {
            c["customer_id"]: Customer(**c)
            for c in raw_crm["customers"]
        }
        self.cases: dict[str, Case] = {
            c["case_id"]: Case(**c)
            for c in raw_crm["cases"]
        }

        # ── kb_articles.json → { "articles": [ {...}, ... ] } ────────────────
        raw_kb = self._load_raw(base / "kb_articles.json")
        self.articles: dict[str, Article] = {
            a["article_id"]: Article(**a)
            for a in raw_kb["articles"]
        }

        # ── payment_config.json → flat object (no array wrapper) ─────────────
        raw_payment = self._load_raw(base / "payment_config.json")
        self.payment_config: PaymentConfig = PaymentConfig(**raw_payment)

        logger.info(
            "DataStore loaded: %d orders, %d customers, %d cases, %d articles",
            len(self.orders),
            len(self.customers),
            len(self.cases),
            len(self.articles),
        )

    @staticmethod
    def _load_raw(path: Path) -> dict[str, Any]:
        """Read a JSON file and return the raw dict. Raises FileNotFoundError if missing."""
        if not path.exists():
            raise FileNotFoundError(
                f"Data file not found: {path}. "
                "Ensure all four JSON files exist in the data/ directory."
            )
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    # ── Convenience lookup helpers ────────────────────────────────────────────
    # These are thin wrappers so tool adapters don't have to handle missing keys.

    def get_order(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)

    def get_customer(self, customer_id: str) -> Customer | None:
        return self.customers.get(customer_id)

    def get_case(self, case_id: str) -> Case | None:
        return self.cases.get(case_id)

    def get_cases_for_customer(self, customer_id: str) -> list[Case]:
        return [c for c in self.cases.values() if c.customer_id == customer_id]

    def search_articles(self, tags: list[str]) -> list[Article]:
        """Return articles that match ANY of the provided tags."""
        tag_set = set(t.lower() for t in tags)
        return [
            a for a in self.articles.values()
            if tag_set.intersection(t.lower() for t in a.tags)
        ]


@lru_cache(maxsize=1)
def get_store() -> DataStore:
    """
    Return the cached DataStore singleton.
    Loaded once on first call; subsequent calls return the same instance.

    To reload (e.g. in tests):
        get_store.cache_clear()
        store = get_store()
    """
    from app.config import settings
    return DataStore(data_dir=settings.DATA_DIR)
