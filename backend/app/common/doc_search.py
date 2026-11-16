"""Hybrid search over documents: BM25 + embeddings, fused with reciprocal rank fusion (RRF).

  chunks:     docs_loader splits the docs into DocSections (one per "##" heading). We search sections.
  BM25:       keyword score; rare words (ERR_TIMEOUT) count more than common ones; long sections
              don't win just by being long. Library: rank-bm25.
  embeddings: meaning score; each section becomes 384 numbers (app/common/embed.py); similar meaning ->
              similar numbers (cosine).
  fusion:     we fuse RANKS, not scores (the two scores are on different scales):
              rrf(section) = sum over the two lists of 1 / (rrf_k + rank in that list).

Deprecated sections (status: deprecated) are removed BEFORE ranking unless include_deprecated=True,
so an outdated fact is never quoted by default.

The index is built once per process (get_index, cached). Call get_index() at startup as a warm-up
(main.py's lifespan hook) so the first user does not wait. If the embedding model cannot load (offline,
or switched off with DOC_SEARCH_DENSE=0, as the tests do), search uses BM25 only and says so in `mode`.

Environment variables: DOC_SEARCH_DENSE (1/0, default 1), DOC_SEARCH_RRF_K (default 60), DOCS_DIR.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Literal, Optional

import numpy as np
from pydantic import BaseModel
from rank_bm25 import BM25Okapi

from app.common.docs_loader import DocSection, load_docs

logger = logging.getLogger(__name__)

Mode = Literal["hybrid", "bm25"]
DEFAULT_RRF_K = 60

# Words too common to help ("the", "is"...). Numbers and codes are never stop words.
_STOP = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "is", "are", "be", "by", "with", "as",
         "at", "it", "this", "that", "from", "if", "do", "does", "how", "what", "which", "i", "my", "we", "you",
         "can", "per", "each", "not", "no", "there"}
_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    """Lower-case words and numbers. Keeps codes whole (err_timeout) and also adds their parts
    (err, timeout), so exact codes match exactly and prose ("timeout") still matches."""
    out = []
    for raw in _TOKEN.findall(text.lower()):
        if raw in _STOP:
            continue
        out.append(raw)
        if "_" in raw:
            out.extend(p for p in raw.split("_") if p and p not in _STOP)
    return out


class DocHit(BaseModel):
    """One search result: which section, how it scored, and how it was found."""

    section_id: str  # "doc:setup#1"
    doc_id: str  # "setup"
    title: Optional[str]
    heading: str
    status: Optional[str]  # "current" | "deprecated" | None (not stated)
    version: Optional[str]
    score: float  # fused RRF score (hybrid) or the BM25 score (bm25 mode)
    bm25_score: float
    dense_score: Optional[float]  # cosine similarity, None in bm25 mode
    rank: int  # 1 = best
    mode: Mode


@dataclass
class DocIndex:
    sections: list[DocSection]
    bm25: Optional[BM25Okapi]  # None when there are no sections
    vectors: Optional[np.ndarray]  # (n, dim), unit length; None in bm25 mode
    embed: Optional[Callable[[list[str]], np.ndarray]]

    @property
    def mode(self) -> Mode:
        return "hybrid" if self.vectors is not None else "bm25"


def section_text(s: DocSection) -> str:
    """What we index: title, heading and text (headings carry a lot of meaning)."""
    return " ".join(x for x in (s.title, s.heading, s.text) if x)


def _dense_wanted() -> bool:
    return os.getenv("DOC_SEARCH_DENSE", "1").strip().lower() not in ("0", "false", "no", "off")


def _rrf_k() -> int:
    try:
        return int(os.getenv("DOC_SEARCH_RRF_K", DEFAULT_RRF_K))
    except ValueError:
        return DEFAULT_RRF_K


def _default_embed() -> Optional[Callable[[list[str]], np.ndarray]]:
    try:
        from app.common.embed import embed_texts
        embed_texts(["warm up"])  # load the model now, so a failure shows here and not mid-search
        return embed_texts
    except Exception as exc:  # offline, model missing...: BM25 still works
        logger.warning("doc search: embeddings unavailable (%s); using BM25 only", type(exc).__name__)
        return None


def build_index(sections: Optional[list[DocSection]] = None,
                embed: Optional[Callable[[list[str]], np.ndarray]] = None) -> DocIndex:
    """Index sections (default: all docs from docs_loader). `embed` overrides the model (tests use a fake)."""
    sections = list(load_docs() if sections is None else sections)
    texts = [section_text(s) for s in sections]
    bm25 = BM25Okapi([tokenize(t) for t in texts]) if texts else None  # BM25Okapi needs 1+ documents
    if embed is None and _dense_wanted() and sections:
        embed = _default_embed()
    vectors = None
    if embed is not None and sections:
        vectors = np.asarray(embed(texts), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.where(norms == 0, 1.0, norms)
    return DocIndex(sections, bm25, vectors, embed)


@lru_cache(maxsize=1)
def get_index() -> DocIndex:
    """The index over all docs, built once per process. Also the startup warm-up."""
    idx = build_index()
    logger.info("doc search index ready: %d sections, mode %s", len(idx.sections), idx.mode)
    return idx


def _ranks(scores: np.ndarray, keep: np.ndarray) -> dict[int, int]:
    """{section position: rank (1 = best)} among the kept sections; ties broken by position."""
    order = [i for i in np.lexsort((np.arange(len(scores)), -scores)) if keep[i]]
    return {int(i): r + 1 for r, i in enumerate(order)}


def search(query: str, k: int = 5, include_deprecated: bool = False, method: str = "auto",
           doc_id: Optional[str] = None, index: Optional[DocIndex] = None) -> list[DocHit]:
    """Top k sections for a query. method: "auto" (hybrid if embeddings are available), "hybrid",
    "bm25" or "dense" (the last two for evaluation). Deprecated sections are excluded unless asked for.
    doc_id limits the search to one document."""
    idx = index or get_index()
    if not idx.sections or idx.bm25 is None:
        return []
    keep = np.array([(include_deprecated or s.status != "deprecated") and (doc_id is None or s.doc_id == doc_id)
                     for s in idx.sections])
    bm25 = np.asarray(idx.bm25.get_scores(tokenize(query)), dtype=float)
    dense = None
    if idx.vectors is not None and method in ("auto", "hybrid", "dense"):
        q = np.asarray(idx.embed([query]), dtype=np.float32)[0]
        q = q / (np.linalg.norm(q) or 1.0)
        dense = idx.vectors @ q
    if method in ("hybrid", "dense") and dense is None:
        raise RuntimeError(f"method {method!r} needs embeddings, which are not available")

    rrf_k = _rrf_k()
    bm25_rank = _ranks(bm25, keep)
    if method == "bm25" or (method == "auto" and dense is None):
        final, mode = {i: bm25[i] for i in bm25_rank}, "bm25"
    elif method == "dense":
        final, mode = {i: float(dense[i]) for i in _ranks(dense, keep)}, "hybrid"
    else:
        dense_rank = _ranks(dense, keep)
        final = {i: 1 / (rrf_k + bm25_rank[i]) + 1 / (rrf_k + dense_rank[i]) for i in bm25_rank}
        mode = "hybrid"

    best = sorted(final, key=lambda i: (-final[i], i))[:k]
    hits = []
    for rank, i in enumerate(best, start=1):
        s = idx.sections[i]
        hits.append(DocHit(section_id=s.id, doc_id=s.doc_id, title=s.title, heading=s.heading, status=s.status,
                           version=s.version, score=round(float(final[i]), 6), bm25_score=round(float(bm25[i]), 4),
                           dense_score=None if dense is None else round(float(dense[i]), 4), rank=rank, mode=mode))
    return hits


def first_sentences(text: str, n: int = 2, max_chars: int = 240) -> str:
    """The first n sentences of a section, markdown removed, on one line (good for a quote).
    A numbered step ("1. Count ...") counts as one sentence, number included."""
    clean = re.sub(r"[*`#>]", "", text)
    clean = re.sub(r"(?m)^\s*-\s+", "; ", clean)  # list items -> "; "
    clean = " ".join(clean.split())
    clean = re.sub(r":\s*;\s*", ": ", clean).lstrip("; ").strip()
    clean = re.sub(r"\s+;", ";", clean)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", clean)
    sentences: list[str] = []
    for part in parts:
        if sentences and re.fullmatch(r"\d+\.", sentences[-1]):
            sentences[-1] += " " + part  # "1." + "Count the requests..." is one step
        else:
            sentences.append(part)
    out = " ".join(sentences[:n]).strip()
    return out if len(out) <= max_chars else out[: max_chars - 3].rsplit(" ", 1)[0] + "..."


def get_section(section_id: str, index: Optional[DocIndex] = None) -> Optional[DocSection]:
    return next((s for s in (index or get_index()).sections if s.id == section_id), None)
