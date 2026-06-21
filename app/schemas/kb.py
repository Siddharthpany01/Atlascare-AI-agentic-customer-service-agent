"""
app/schemas/kb.py
-----------------
Pydantic model for knowledge base articles.
Field names derived directly from kb_articles.json schema.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class Article(BaseModel):
    article_id: str                         # pattern: KB-000
    title: str
    tags: list[str]                         # used for search: kb.search(tags=["refund"])
    content: str                            # full policy text surfaced to synthesizer
    last_updated: str                       # date string YYYY-MM-DD
    applies_to: Optional[list[str]] = None  # product categories e.g. ["electronics"]
