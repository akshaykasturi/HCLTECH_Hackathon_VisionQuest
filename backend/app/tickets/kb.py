"""Step 3b: search the approved KB articles (FR-03). Uses common/doc_search (BM25 + embeddings + RRF) over the
chunks chosen in policy.yaml `kb_chunking` (fixed | heading | contextual; picked by the chunking trial on val,
see scripts/eval_chunking.py).

    search_kb(query, k=3) -> list[KBHit]

- KBHit.text is ALWAYS the original chunk text. With contextual chunking the index also saw a context prefix,
  but the prefix is never returned: checks.py verifies that every quoted excerpt appears word for word in
  KBHit.text, and the LLM must only quote what the article says.
- KBHit.score is the dense cosine similarity (0..1) when embeddings are on, else the raw BM25 score (0..,
  not bounded). The ranking itself is the doc_search order (RRF in hybrid mode).
- get_kb_index() builds once per process and strategy; main.py calls it at startup as a warm-up.
- No KB folder or no articles -> an empty index and search_kb returns [] (never crashes).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import yaml

from app.common.docs_loader import DocSection, DocsLoaderError
from app.common.doc_search import DocIndex, build_index, search
from app.tickets.chunking import STRATEGIES, build_chunks
from app.tickets.models import KBHit
from app.tickets.similar import POLICY_FILE

logger = logging.getLogger(__name__)
DEFAULT_STRATEGY = "heading"


def kb_strategy() -> str:
    """policy.yaml `kb_chunking` (default heading)."""
    try:
        with POLICY_FILE.open(encoding="utf-8") as f:
            value = (yaml.safe_load(f) or {}).get("kb_chunking") or DEFAULT_STRATEGY
    except FileNotFoundError:
        value = DEFAULT_STRATEGY
    if value not in STRATEGIES:
        logger.warning("kb: unknown kb_chunking %r; using %s", value, DEFAULT_STRATEGY)
        return DEFAULT_STRATEGY
    return value


@dataclass
class KBIndex:
    strategy: str
    index: DocIndex                       # what search sees (contextual: prefix + text)
    shown: dict[str, DocSection]          # chunk id -> the original chunk (what we return and cite)


@lru_cache(maxsize=4)
def get_kb_index(strategy: Optional[str] = None) -> KBIndex:
    strategy = strategy or kb_strategy()
    try:
        indexed, shown = build_chunks(strategy)
    except DocsLoaderError as exc:  # no KB folder: search returns []
        logger.warning("kb: no KB articles (%s); KB search is empty", type(exc).__name__)
        indexed, shown = [], {}
    idx = KBIndex(strategy, build_index(indexed), shown)
    logger.info("kb index ready: %d chunks, strategy %s, mode %s", len(indexed), strategy, idx.index.mode)
    return idx


def search_kb(query: str, k: int = 3, method: str = "auto", index: Optional[KBIndex] = None) -> list[KBHit]:
    """Top k KB chunks for an (already masked) ticket text. method: auto | bm25 | dense | hybrid."""
    kb = index or get_kb_index()
    hits = search(query, k=k, method=method, index=kb.index)
    out = []
    for h in hits:
        original = kb.shown[h.section_id]
        score = h.dense_score if h.dense_score is not None else h.bm25_score
        out.append(KBHit(section_id=h.section_id, title=original.title or original.heading,
                         text=original.text, score=round(float(score), 4)))
    return out
