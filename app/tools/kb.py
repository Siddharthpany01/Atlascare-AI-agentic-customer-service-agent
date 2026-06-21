"""
app/tools/kb.py — Knowledge Base adapter.

Delegates tag-based article search to DataStore.search_articles(),
which performs a case-insensitive ANY-match on tags.
"""

import time

from app.schemas.trace import ToolResult
from app.services.data_loader import get_store


class KBAdapter:
    """Thin adapter over the in-memory DataStore for KB article search."""

    def search(self, tags: list[str]) -> ToolResult:
        """
        Search articles by tags (case-insensitive, ANY match).

        Returns SUCCESS with a list of matched articles, or FAILED on
        unexpected error. An empty result set is SUCCESS with data=[].
        """
        t0 = time.perf_counter()
        try:
            if not tags:
                return ToolResult(
                    status="FAILED",
                    error_code="KB_EMPTY_TAGS",
                    error_message="At least one tag is required for KB search",
                    latency_ms=_ms(t0),
                )

            store = get_store()
            articles = store.search_articles(tags)

            return ToolResult(
                status="SUCCESS",
                data={
                    "articles": [a.model_dump() for a in articles],
                    "count": len(articles),
                    "query_tags": tags,
                },
                latency_ms=_ms(t0),
            )

        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                status="FAILED",
                error_code="KB_INTERNAL_ERROR",
                error_message=str(exc),
                latency_ms=_ms(t0),
            )


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_kb_adapter: KBAdapter | None = None


def get_kb() -> KBAdapter:
    """Return the module-level KBAdapter singleton."""
    global _kb_adapter
    if _kb_adapter is None:
        _kb_adapter = KBAdapter()
    return _kb_adapter


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)
