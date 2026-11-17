"""Step 3a: find similar past tickets (FR-02). Hybrid search, same approach as common/doc_search.py.

  index:    one past ticket = one unit (tickets are not chunked). Only train + val tickets go in: test
            tickets are the exam questions for the evaluation, so they must never be in the index.
            Every text field is masked (common/redact.mask) before it is indexed or stored.
  BM25:     keyword score over subject + description + affected_service (policy.yaml similar.index_fields).
            Good at exact codes ("809").
  dense:    meaning score: cosine of embeddings (common/embed.py). Good at paraphrases ("tunnel won't
            establish" ~ "VPN can't connect").
  fusion:   reciprocal rank fusion of the two RANKS: sum of 1 / (rrf_k + rank). A same-service ticket gets
            service_bonus on top. This fused number only decides the ORDER.
  score:    what we show and threshold (SimilarTicket.score, 0..1): dense cosine when embeddings are on,
            else the Jaccard overlap of the word sets. Unlike the fused number, it means the same thing
            for every query, so a fixed threshold works (likely_duplicate / related / weak).
  lookalike: shares keywords but is a different issue (BM25 top 3 with low cosine, or a different category
            than the classifier predicted while still scoring high). The gate should not trust it.

Filters are applied BEFORE ranking, so a filtered-out ticket never takes a top-k place.
If the CSV is missing the index is empty and search returns []; if embeddings are unavailable
(offline, or DOC_SEARCH_DENSE=0) search runs on BM25 only and logs a warning.

Environment: TICKETS_CSV (default backend/data/tickets/tickets.csv), DOC_SEARCH_DENSE (1/0).
"""
from __future__ import annotations

import csv
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import numpy as np
import yaml
from rank_bm25 import BM25Okapi

from app.common.doc_search import tokenize
from app.common.embed import l2_normalise
from app.common.paths import DATA_DIR
from app.common.redact import mask
from app.common.trace import optional
from app.tickets.models import SimilarTicket, TicketFilters

logger = logging.getLogger(__name__)

POLICY_FILE = Path(__file__).with_name("policy.yaml")
DEFAULTS: dict[str, Any] = {"index_fields": ["subject", "description", "affected_service"], "k": 5, "rrf_k": 60,
                            "service_bonus": 0.008, "dup_threshold": 0.85, "related_threshold": 0.70}
METHODS = ("auto", "bm25", "dense", "hybrid")
_HAS_DIGIT = re.compile(r"\d")

Embed = Callable[[list[str]], np.ndarray]


@lru_cache(maxsize=1)
def policy() -> dict[str, Any]:
    """The `similar` section of policy.yaml, with defaults for anything missing."""
    try:
        with POLICY_FILE.open(encoding="utf-8") as f:
            section = (yaml.safe_load(f) or {}).get("similar") or {}
    except FileNotFoundError:
        section = {}
    return {**DEFAULTS, **section}


def tickets_csv() -> Path:
    return Path(os.getenv("TICKETS_CSV") or DATA_DIR / "tickets" / "tickets.csv")


# ---- Index -------------------------------------------------------------------------------------

@dataclass
class TicketIndex:
    rows: list[dict[str, Any]]           # masked ticket rows (the CSV's own field names)
    tokens: list[set[str]]               # word set per ticket, for Jaccard and "why"
    bm25: Optional[BM25Okapi]            # None when the index is empty
    vectors: Optional[np.ndarray]        # (n, dim) unit length; None = BM25 only
    embed: Optional[Embed]

    @property
    def mode(self) -> str:
        return "hybrid" if self.vectors is not None else "bm25"


def read_rows(path: Optional[Path] = None) -> list[dict[str, Any]]:
    """All rows of the tickets CSV ([] if the file is missing)."""
    path = path or tickets_csv()
    if not path.exists():
        logger.warning("similar tickets: %s not found; index is empty", path.name)
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _mask_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: mask(v) if isinstance(v, str) else v for k, v in row.items()}


def index_text(row: dict[str, Any], fields: Optional[Iterable[str]] = None) -> str:
    return " ".join(str(row.get(f) or "") for f in (fields or policy()["index_fields"])).strip()


def _dense_wanted() -> bool:
    return os.getenv("DOC_SEARCH_DENSE", "1").strip().lower() not in ("0", "false", "no", "off")


def _default_embed() -> Optional[Embed]:
    try:
        from app.common.embed import embed_texts
        embed_texts(["warm up"])  # load the model now, so a failure shows here and not mid-search
        return embed_texts
    except Exception as exc:  # offline, model missing...: BM25 still works
        logger.warning("similar tickets: embeddings unavailable (%s); using BM25 only", type(exc).__name__)
        return None


def build_ticket_index(rows: Optional[list[dict[str, Any]]] = None, splits: Iterable[str] = ("train", "val"),
                       embed: Optional[Embed] = None) -> TicketIndex:
    """Index the tickets of the given splits (default train + val; never test). `embed` overrides the
    model (tests pass a fake). To tune on val without the answers in the index, use splits=("train",)."""
    wanted = set(splits)
    rows = read_rows() if rows is None else rows
    kept = [_mask_row(r) for r in rows if (r.get("split") or "").strip() in wanted]
    texts = [index_text(r) for r in kept]
    tokens = [tokenize(t) for t in texts]
    bm25 = BM25Okapi(tokens) if kept else None  # BM25Okapi needs 1+ documents

    if embed is None and kept:
        if _dense_wanted():
            embed = _default_embed()
        else:
            logger.warning("similar tickets: DOC_SEARCH_DENSE=0; using BM25 only")
    vectors = None
    if embed is not None and kept:
        try:
            vectors = l2_normalise(np.asarray(embed(texts), dtype=np.float32))
        except Exception as exc:
            logger.warning("similar tickets: embedding failed (%s); using BM25 only", type(exc).__name__)
            embed = None
    return TicketIndex(kept, [set(t) for t in tokens], bm25, vectors, embed if vectors is not None else None)


@lru_cache(maxsize=1)
def get_ticket_index() -> TicketIndex:
    """The index over the tickets CSV, built once per process. Also the startup warm-up."""
    idx = build_ticket_index()
    logger.info("similar tickets index ready: %d tickets, mode %s", len(idx.rows), idx.mode)
    return idx


# ---- Filters -----------------------------------------------------------------------------------

_EXACT_FILTERS = ("category", "subcategory", "priority", "status", "assigned_team", "affected_service",
                  "environment")


def _as_naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _parse_date(text: Any) -> Optional[datetime]:
    try:
        return _as_naive_utc(datetime.fromisoformat(str(text).strip()))
    except (TypeError, ValueError):
        return None


def _passes(row: dict[str, Any], filters: Optional[TicketFilters]) -> bool:
    """True if the row matches every filter that is set (text compared case-insensitively).
    filters.q is the free-text search, not a filter: pass it as the query."""
    if filters is None:
        return True
    for name in _EXACT_FILTERS:
        want = getattr(filters, name)
        if want and str(row.get(name) or "").strip().lower() != want.strip().lower():
            return False
    if filters.date_from or filters.date_to:
        created = _parse_date(row.get("created_at"))
        if created is None:
            return False
        if filters.date_from and created < _as_naive_utc(filters.date_from):
            return False
        if filters.date_to and created > _as_naive_utc(filters.date_to):
            return False
    return True


def passes_filters(row: dict[str, Any], filters: Optional[TicketFilters]) -> bool:
    """Public: does an index row match the filters (GET /api/tickets lists rows without a query)."""
    return _passes(row, filters)


# ---- Search ------------------------------------------------------------------------------------

def _ranks(scores: np.ndarray, keep: list[int]) -> dict[int, int]:
    """{row position: rank (1 = best)} among the kept rows; ties broken by position."""
    order = sorted(keep, key=lambda i: (-scores[i], i))
    return {i: r + 1 for r, i in enumerate(order)}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _same(a: Any, b: Any) -> bool:
    return bool(a) and bool(b) and str(a).strip().lower() == str(b).strip().lower()


def _past_feedback(ticket_ids: list[str]) -> dict[str, dict[str, int]]:
    """Analyst feedback counts per ticket from the database, if db/repo.py is ready; else {}.
    Expected: feedback_counts(ticket_ids) -> {ticket_id: {"useful": 3, "incorrect": 1}}."""
    counts = optional("app.db.repo", "feedback_counts")
    if counts is None or not ticket_ids:
        return {}
    try:
        return counts(ticket_ids) or {}
    except Exception as exc:  # the search must not fail because the database is down
        logger.warning("similar tickets: feedback counts unavailable (%s)", type(exc).__name__)
        return {}


def _resolution_text(row: dict[str, Any]) -> Optional[str]:
    notes = (row.get("resolution_notes") or "").strip()
    steps = (row.get("resolution_steps") or "").strip()
    text = " ".join(x for x in (notes, f"Steps: {steps}" if steps else "") if x)
    return text or None


def resolution_text(row: dict[str, Any]) -> Optional[str]:
    """Public: the (masked) resolution notes + " Steps: " + steps of an index row."""
    return _resolution_text(row)


def search_similar(query: str, k: int = 5, filters: Optional[TicketFilters] = None,
                   category_hint: Optional[str] = None, affected_service: Optional[str] = None,
                   method: str = "auto", index: Optional[TicketIndex] = None) -> list[SimilarTicket]:
    """Top k similar past tickets for an (already masked) ticket text.

    method: "auto" (hybrid if embeddings are available, else BM25), "hybrid", "bm25" or "dense"
    (the last three for evaluation; "hybrid"/"dense" without embeddings raise RuntimeError).
    category_hint: the classifier's category, used to flag lookalikes.
    affected_service: the new ticket's service; same-service tickets get policy service_bonus.
    """
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}; use one of {METHODS}")
    idx = index or get_ticket_index()
    if not idx.rows or idx.bm25 is None or k <= 0:
        return []
    keep = [i for i, row in enumerate(idx.rows) if _passes(row, filters)]
    if not keep:
        return []

    p = policy()
    query = mask(query)
    q_tokens = tokenize(query)
    q_set = set(q_tokens)
    bm25 = np.asarray(idx.bm25.get_scores(q_tokens), dtype=float)

    dense = None
    if idx.vectors is not None:
        try:
            q = l2_normalise(np.asarray(idx.embed([query]), dtype=np.float32))[0]
            dense = idx.vectors @ q
        except Exception as exc:
            logger.warning("similar tickets: query embedding failed (%s); using BM25 only", type(exc).__name__)
    if method in ("hybrid", "dense") and dense is None:
        raise RuntimeError(f"method {method!r} needs embeddings, which are not available")
    use_bm25 = method in ("bm25", "hybrid") or (method == "auto")
    use_dense = method in ("dense", "hybrid") or (method == "auto" and dense is not None)

    # Order: RRF over the rankings used, plus the same-service bonus (both in RRF units).
    rrf_k = p["rrf_k"]
    bm25_rank = _ranks(bm25, keep)
    dense_rank = _ranks(dense, keep) if dense is not None else {}
    fused: dict[int, float] = {}
    for i in keep:
        f = (1 / (rrf_k + bm25_rank[i]) if use_bm25 else 0.0) + (1 / (rrf_k + dense_rank[i]) if use_dense else 0.0)
        if _same(affected_service, idx.rows[i].get("affected_service")):
            f += p["service_bonus"]
        fused[i] = f
    best = sorted(keep, key=lambda i: (-fused[i], i))[:k]

    bm25_top3 = {i for i, r in bm25_rank.items() if r <= 3 and bm25[i] > 0}
    feedback = _past_feedback([str(idx.rows[i].get("ticket_id")) for i in best])
    out = []
    for i in best:
        row = idx.rows[i]
        cosine = float(dense[i]) if dense is not None else None
        score = min(max(cosine if cosine is not None else _jaccard(q_set, idx.tokens[i]), 0.0), 1.0)
        match = ("likely_duplicate" if score >= p["dup_threshold"]
                 else "related" if score >= p["related_threshold"] else "weak")
        keyword_only = i in bm25_top3 and cosine is not None and cosine < p["related_threshold"]
        other_category = (category_hint is not None and not _same(category_hint, row.get("category"))
                          and score >= p["related_threshold"])
        lookalike = keyword_only or other_category

        why = []
        if _same(affected_service, row.get("affected_service")):
            why.append(f"same affected_service: {row.get('affected_service')}")
        codes = sorted(t for t in q_set & idx.tokens[i] if _HAS_DIGIT.search(t))
        if codes:
            why.append("shared error code: " + ", ".join(codes[:3]))
        if cosine is not None and cosine >= p["related_threshold"]:
            why.append(f"similar meaning ({cosine:.2f})")
        if row.get("problem_id"):
            why.append(f"linked to problem: {row.get('problem_id')}")
        if keyword_only:
            why.append("keyword overlap only")
        if other_category:
            why.append(f"different category: {row.get('category')}")
        if not why and q_set & idx.tokens[i]:
            why.append("shared words: " + ", ".join(sorted(q_set & idx.tokens[i])[:3]))

        tid = str(row.get("ticket_id"))
        out.append(SimilarTicket(
            ticket_id=tid, subject=row.get("subject") or "", category=row.get("category") or "",
            subcategory=row.get("subcategory") or None, status=row.get("status") or "",
            score=round(score, 4), match=match, why=why, lookalike=lookalike,
            problem_id=row.get("problem_id") or None, root_cause=(row.get("root_cause") or "").strip() or None,
            resolution_text=_resolution_text(row),
            past_feedback=feedback.get(tid, {})))
    return out


# ---- Evaluation --------------------------------------------------------------------------------

def retrieval_eval(queries: list[dict[str, Any]], index: TicketIndex, k: int = 5,
                   methods: Iterable[str] = ("bm25", "dense", "hybrid")) -> dict[str, dict[str, Any]]:
    """Compare retrieval methods on held-out tickets (rows with the CSV fields, e.g. the test split).

    Relevant = index tickets with the same dup_group_id as the query (the query itself excluded);
    queries whose group has nothing in the index are skipped. Per method:
      recall_at_k          share of the relevant tickets found in the top k, averaged over queries
      mrr                  1 / rank of the first relevant ticket (0 if none in the top k), averaged
      lookalike_rate_top3  share of top-3 results from a different category than the query (wrong issue)
      n                    number of queries scored
    Each query is searched as the pipeline would: its index fields, with its affected_service.
    A method that needs embeddings when there are none is reported as skipped.
    """
    groups: dict[str, set[str]] = {}
    for row in index.rows:
        if row.get("dup_group_id"):
            groups.setdefault(str(row["dup_group_id"]), set()).add(str(row.get("ticket_id")))

    results: dict[str, dict[str, Any]] = {}
    for method in methods:
        if method in ("dense", "hybrid") and index.vectors is None:
            results[method] = {"skipped": "no embeddings"}
            continue
        recalls, rrs, wrong, n_top3 = [], [], 0, 0
        for q in queries:
            qid = str(q.get("ticket_id"))
            relevant = groups.get(str(q.get("dup_group_id") or ""), set()) - {qid}
            if not relevant:
                continue
            hits = [h for h in search_similar(index_text(q), k=k + 1, affected_service=q.get("affected_service"),
                                              method=method, index=index) if h.ticket_id != qid][:k]
            ids = [h.ticket_id for h in hits]
            recalls.append(len(relevant & set(ids)) / len(relevant))
            rrs.append(next((1 / r for r, t in enumerate(ids, start=1) if t in relevant), 0.0))
            for h in hits[:3]:
                n_top3 += 1
                wrong += not _same(h.category, q.get("category"))
        n = len(recalls)
        results[method] = {"recall_at_k": round(sum(recalls) / n, 4) if n else 0.0,
                           "mrr": round(sum(rrs) / n, 4) if n else 0.0,
                           "lookalike_rate_top3": round(wrong / n_top3, 4) if n_top3 else 0.0,
                           "n": n, "k": k}
    return results
