"""Step 3b: re-rank the similar tickets with entropy-weighted TOPSIS (FR-02, FR-04, FR-08). Off by default.

Retrieval (similar.py) orders past tickets by text match only. This step scores each candidate on several
criteria (how close the text is, whether it was actually fixed, same service, how recent, does the classifier
agree, did analysts find it useful), weights the criteria, and orders the candidates by TOPSIS closeness.

  weights   blended = lam * entropy + (1 - lam) * prior, then a floor per criterion (policy.yaml `mcdm`).
            Entropy weight = how much a criterion separates THESE candidates (a column where all candidates
            are equal gets 0). With fewer than min_candidates_for_entropy candidates: prior weights only.
  output    the top_n candidates in the new order, each with closeness, rank before/after, the criterion
            values and each criterion's share ("why it ranked here").

What it never does: change SimilarTicket.score. TOPSIS closeness is relative (the best of a bad set still
scores near 1), so the gate keeps deciding ESCALATE / not-found on the absolute retrieval score.

Runs after masking: it only sees the masked ticket and query. The trace line holds criterion names and
numbers, never ticket text. Disabled -> retrieval order. Any error -> retrieval order + a warn Finding.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable, Optional

import numpy as np
import yaml

from app.common.doc_search import tokenize
from app.common.mcdm import blend_weights, entropy_weights, order, topsis
from app.common.redact import mask
from app.common.report_checks import Finding
from app.tickets.models import Classification, SimilarTicket, TicketIn
from app.tickets.similar import POLICY_FILE, TicketIndex, get_ticket_index

logger = logging.getLogger(__name__)

METHODS = ("blended", "prior", "entropy")  # "retrieval" = this step off; used by the evaluation
RESOLVED_STATUSES = frozenset({"resolved", "closed"})

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "pool_k": 20,
    "top_n": 5,
    "lam": 0.5,
    "weight_floor": 0.05,
    "min_candidates_for_entropy": 4,
    "criteria": [
        {"name": "dense_similarity", "direction": "benefit", "prior_weight": 0.30},
        {"name": "bm25", "direction": "benefit", "prior_weight": 0.15},
        {"name": "category_agreement", "direction": "benefit", "prior_weight": 0.20},
        {"name": "same_service", "direction": "benefit", "prior_weight": 0.10},
        {"name": "resolution_quality", "direction": "benefit", "prior_weight": 0.15},
        {"name": "age_days", "direction": "cost", "prior_weight": 0.05},
        {"name": "analyst_feedback", "direction": "benefit", "prior_weight": 0.05},
    ],
}


@lru_cache(maxsize=1)
def mcdm_policy() -> dict[str, Any]:
    """The `mcdm` section of policy.yaml with defaults for anything missing (off until it is added)."""
    try:
        with POLICY_FILE.open(encoding="utf-8") as f:
            section = (yaml.safe_load(f) or {}).get("mcdm") or {}
    except FileNotFoundError:
        section = {}
    return {**DEFAULTS, **section}


# ---- Criteria: each returns one value per candidate, or None when the source does not exist ----------

@dataclass
class _Inputs:
    pool: list[SimilarTicket]
    rows: list[Optional[dict[str, Any]]]   # the index row of each candidate (None if not in the index)
    query: str                              # masked
    ticket: TicketIn                        # masked
    classification: Optional[Classification]
    index: TicketIndex


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _parse_date(text: Any) -> Optional[datetime]:
    try:
        value = datetime.fromisoformat(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _dense_similarity(x: _Inputs) -> Optional[list[float]]:
    # SimilarTicket.score is the embedding cosine only in hybrid mode; in BM25 mode it is word overlap.
    return [s.score for s in x.pool] if x.index.mode == "hybrid" else None


def _bm25(x: _Inputs) -> Optional[list[float]]:
    if x.index.bm25 is None:
        return None
    scores = np.asarray(x.index.bm25.get_scores(tokenize(mask(x.query))), dtype=float)
    position = {str(r.get("ticket_id")): i for i, r in enumerate(x.index.rows)}
    raw = [float(scores[position[s.ticket_id]]) if s.ticket_id in position else float("nan") for s in x.pool]
    top = max((v for v in raw if np.isfinite(v)), default=0.0)
    # Normalised per query (best candidate = 1). BM25 can be <= 0 when every word is common; then all 0.
    # A candidate missing from the index stays NaN, so the whole criterion is dropped and reported.
    return [v if not np.isfinite(v) else (max(v, 0.0) / top if top > 0 else 0.0) for v in raw]


def _category_agreement(x: _Inputs) -> Optional[list[float]]:
    if x.classification is None:
        return None
    probs = {_norm(c.label): c.prob for c in x.classification.top3_category}
    probs.setdefault(_norm(x.classification.category.label), x.classification.category.prob)
    return [probs.get(_norm(s.category), 0.0) for s in x.pool]  # soft: the classifier's probability


def _same_service(x: _Inputs) -> Optional[list[float]]:
    if not x.ticket.affected_service:
        return None
    want = _norm(x.ticket.affected_service)
    return [float(bool(r) and _norm(r.get("affected_service")) == want) for r in x.rows]


def _resolution_quality(x: _Inputs) -> list[float]:
    return [0.5 * (_norm(s.status) in RESOLVED_STATUSES) + 0.5 * bool((s.resolution_text or "").strip())
            for s in x.pool]


def _age_days(x: _Inputs) -> list[float]:
    ref = x.ticket.created_at or datetime.now(timezone.utc)
    ref = ref if ref.tzinfo else ref.replace(tzinfo=timezone.utc)
    out = []
    for r in x.rows:
        when = (_parse_date(r.get("resolved_at")) or _parse_date(r.get("created_at"))) if r else None
        out.append(max((ref - when).total_seconds() / 86400.0, 0.0) if when else float("nan"))
    return out


def _analyst_feedback(x: _Inputs) -> list[float]:
    # (useful + 1) / (useful + incorrect + 2): 0.5 with no feedback, moves towards 1 or 0 as votes come in.
    return [(s.past_feedback.get("useful", 0) + 1) / (s.past_feedback.get("useful", 0)
                                                      + s.past_feedback.get("incorrect", 0) + 2) for s in x.pool]


CRITERIA: dict[str, Callable[[_Inputs], Optional[list[float]]]] = {
    "dense_similarity": _dense_similarity,
    "bm25": _bm25,
    "category_agreement": _category_agreement,
    "same_service": _same_service,
    "resolution_quality": _resolution_quality,
    "age_days": _age_days,
    "analyst_feedback": _analyst_feedback,
}


# ---- The step ----------------------------------------------------------------------------------------

@dataclass
class RerankResult:
    similar: list[SimilarTicket]                        # top_n, in the order the LLM and the UI get them
    applied: bool                                       # False = retrieval order kept (off, or failed)
    method: str = "retrieval"
    weights: dict[str, dict[str, Optional[float]]] = field(default_factory=dict)  # name -> entropy/prior/blended
    dropped: list[str] = field(default_factory=list)    # criteria with no data for this ticket
    explanations: dict[str, dict[str, Any]] = field(default_factory=dict)  # ticket_id -> closeness, ranks, ...
    findings: list[Finding] = field(default_factory=list)
    detail: str = ""                                    # one trace line: numbers and criterion names only


def _attach(ticket: SimilarTicket, explain: dict[str, Any]) -> SimilarTicket:
    """Put the explanation on the ticket once models.py has SimilarTicket.mcdm; until then it lives in
    RerankResult.explanations only."""
    if "mcdm" in type(ticket).model_fields:
        return ticket.model_copy(update={"mcdm": explain})
    return ticket


def _retrieval_order(pool: list[SimilarTicket], top_n: int, detail: str,
                     finding: Optional[Finding] = None) -> RerankResult:
    return RerankResult(similar=list(pool[:top_n]), applied=False, detail=detail,
                        findings=[finding] if finding else [])


def mcdm_rank(pool: list[SimilarTicket], query: str, ticket: TicketIn,
              classification: Optional[Classification] = None, policy: Optional[dict[str, Any]] = None,
              method: str = "blended", index: Optional[TicketIndex] = None) -> RerankResult:
    """Re-rank `pool` (retrieval order, best first). Never raises: on any error the retrieval order is kept.

    `policy` overrides keys of policy.yaml `mcdm` (tests, evaluation). `method`: "blended" (normal),
    "prior" or "entropy" (evaluation arms). `query` and `ticket` must already be masked.
    """
    p = {**mcdm_policy(), **(policy or {})}
    top_n = int(p["top_n"])
    if not p["enabled"]:
        return _retrieval_order(pool, top_n, "mcdm off: retrieval order")
    try:
        return _rank(pool, query, ticket, classification, p, method, index or get_ticket_index())
    except Exception as exc:  # never break the run: keep retrieval order, say so
        name = exc.__class__.__name__  # never the message: it could quote ticket text
        logger.warning("mcdm re-ranking skipped (%s)", name)
        finding = Finding(code="MCDM_SKIPPED", severity="warn", where="similar",
                          message=f"Re-ranking skipped ({name}); similar tickets are in retrieval order.")
        return _retrieval_order(pool, top_n, f"mcdm failed ({name}): retrieval order", finding)


def _rank(pool: list[SimilarTicket], query: str, ticket: TicketIn, classification: Optional[Classification],
          p: dict[str, Any], method: str, index: TicketIndex) -> RerankResult:
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}; use one of {METHODS}")
    top_n = int(p["top_n"])
    if not pool:
        return RerankResult(similar=[], applied=False, method=method, detail="mcdm: no candidates")

    by_id = {str(r.get("ticket_id")): r for r in index.rows}
    x = _Inputs(pool, [by_id.get(s.ticket_id) for s in pool], query, ticket, classification, index)

    names, columns, benefit, prior, dropped = [], [], [], [], []
    for c in p["criteria"]:
        name = c["name"]
        if name not in CRITERIA:
            raise ValueError(f"unknown mcdm criterion {name!r}")
        if c.get("direction") not in ("benefit", "cost"):
            raise ValueError(f"criterion {name!r}: direction must be benefit or cost")
        values = CRITERIA[name](x)
        if values is None or not np.all(np.isfinite(values)):
            dropped.append(name)
            continue
        names.append(name)
        columns.append(values)
        benefit.append(c["direction"] == "benefit")
        prior.append(float(c["prior_weight"]))
    if not names:
        return RerankResult(similar=list(pool[:top_n]), applied=False, method=method, dropped=dropped,
                            detail=f"mcdm: no usable criteria (dropped {', '.join(dropped)}): retrieval order")

    X = np.array(columns, dtype=float).T          # candidates x criteria
    m = X.shape[0]
    w_entropy = entropy_weights(X, benefit) if m >= int(p["min_candidates_for_entropy"]) else None
    floor = float(p["weight_floor"])
    if method == "prior":
        w = blend_weights(None, prior, 0.0, floor)
    elif method == "entropy":
        w = blend_weights(w_entropy, prior, 1.0, floor) if w_entropy is not None else blend_weights(None, prior, 0, floor)
    else:
        w = blend_weights(w_entropy, prior, float(p["lam"]), floor)
    prior_n = np.asarray(prior) / sum(prior) if sum(prior) > 0 else np.full(len(prior), 1.0 / len(prior))

    result = topsis(X, w, benefit)
    ranked = order(result.closeness)[:top_n]      # pool is in retrieval order, so ties keep it
    out, explanations = [], {}
    for after, i in enumerate(ranked, start=1):
        explain = {
            "closeness": round(float(result.closeness[i]), 6),
            "rank_before": int(i) + 1,
            "rank_after": after,
            "criteria": {n: round(float(X[i, j]), 6) for j, n in enumerate(names)},
            "contributions": {n: round(float(result.contributions[i, j]), 6) for j, n in enumerate(names)},
        }
        explanations[pool[i].ticket_id] = explain
        out.append(_attach(pool[i], explain))

    weights = {n: {"entropy": None if w_entropy is None else round(float(w_entropy[j]), 4),
                   "prior": round(float(prior_n[j]), 4), "blended": round(float(w[j]), 4)}
               for j, n in enumerate(names)}
    source = "prior only (too few candidates)" if w_entropy is None else method
    detail = (f"mcdm {source}: {m} candidates -> top {len(out)}; weights "
              + ", ".join(f"{n} {weights[n]['blended']:.2f}" for n in names)
              + (f"; dropped {', '.join(dropped)}" if dropped else "")
              + f"; top-1 was retrieval rank {int(ranked[0]) + 1}")
    return RerankResult(similar=out, applied=True, method=method, weights=weights, dropped=dropped,
                        explanations=explanations, detail=detail)
