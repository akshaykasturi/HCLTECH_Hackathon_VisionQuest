"""Does TOPSIS re-ranking find the right past tickets better than plain retrieval? Four arms, same pool:

  retrieval   hybrid/RRF order (baseline)          prior     TOPSIS, typed prior weights
  entropy     TOPSIS, entropy weights only         blended   TOPSIS, lam * entropy + (1 - lam) * typed prior
  ahp         TOPSIS, AHP weights only             ahp_entropy  TOPSIS, lam * entropy + (1 - lam) * AHP
  (AHP weights come from app/tickets/ahp_judgements.yaml; if their CR >= 0.10 the typed priors are used.)

Each query = one val (or test) ticket with gold_similar_ticket_ids (data/tickets/labelled_cases.jsonl).
Gold tickets are in the query's own split, so the index is train + that split, with the query itself removed.
Metrics on the top `top_n` (default 5), with bootstrap 95% intervals (app/analysis/stats.py):
  recall@k   share of the gold similar tickets found       MRR   1 / rank of the first gold ticket
  nDCG@k     graded: gold / same duplicate group = 2, same scenario = 1 (retrieval_pairs.csv), else 0
Also: mean weight per criterion, how often the top-1 differs from retrieval, wrong-category share in the top 3.

Choose lam, priors and `enabled` on the VAL split (default). Run --split test once, for the reported numbers.
The classifier criterion is used only if app/tickets/classify.py exists; gold categories are never used.

Usage (from backend):  .\\.venv\\Scripts\\python.exe scripts\\eval_rerank.py [--split val|test] [--bm25-only]
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ARMS = ("retrieval", "prior", "entropy", "blended", "ahp", "ahp_entropy")
logger = logging.getLogger("eval_rerank")


def _load_gold(data_dir: Path, split: str) -> tuple[list[dict[str, Any]], dict[tuple[str, str], int]]:
    cases = []
    with (data_dir / "labelled_cases.jsonl").open(encoding="utf-8-sig") as f:
        for line in f:
            if line.strip():
                case = json.loads(line)
                if case.get("split") == split and case.get("gold_similar_ticket_ids") and case.get("ticket_id"):
                    cases.append(case)
    grades: dict[tuple[str, str], int] = {}
    pairs = data_dir / "retrieval_pairs.csv"
    if pairs.exists():
        with pairs.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                grades[(row["query_ticket_id"], row["candidate_ticket_id"])] = int(row["relevance"])
    return cases, grades


def _created(row: Optional[dict[str, Any]]) -> Optional[datetime]:
    try:
        value = datetime.fromisoformat(str(row["created_at"]).strip()) if row and row.get("created_at") else None
    except ValueError:
        return None
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _ndcg(ids: list[str], gains: dict[str, int], k: int) -> float:
    dcg = sum((2 ** gains.get(t, 0) - 1) / math.log2(r + 1) for r, t in enumerate(ids[:k], start=1))
    ideal = sorted(gains.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(r + 1) for r, g in enumerate(ideal, start=1))
    return dcg / idcg if idcg > 0 else 0.0


def rerank_eval(split: str = "val", pool_k: int = 20, top_n: int = 5, policy: Optional[dict[str, Any]] = None,
                rows: Optional[list[dict[str, Any]]] = None, data_dir: Optional[Path] = None,
                index: Any = None, time_aware: bool = True) -> dict[str, Any]:
    """Per-arm metric lists and summary. Importable by scripts/evaluate.py; no printing here.
    time_aware (default): each query only sees tickets created before it, as in production."""
    from app.common.paths import DATA_DIR
    from app.common.trace import optional
    from app.tickets.models import TicketFilters, TicketIn
    from app.tickets.rerank import mcdm_rank
    from app.tickets.similar import build_ticket_index, index_text, read_rows, search_similar

    data_dir = data_dir or DATA_DIR / "tickets"
    cases, grades = _load_gold(data_dir, split)
    rows = read_rows() if rows is None else rows
    by_id = {str(r.get("ticket_id")): r for r in rows}
    index = index or build_ticket_index(rows, splits=("train", split))
    classify = optional("app.tickets.classify", "classify")
    run_policy = {**(policy or {}), "enabled": True, "top_n": top_n}

    per_arm: dict[str, dict[str, list[float]]] = {a: {"recall": [], "mrr": [], "ndcg": [], "wrong_cat_top3": [],
                                                       "top1_changed": []} for a in ARMS}
    weight_sums: dict[str, list[float]] = {}
    ahp_summary: Optional[dict[str, Any]] = None
    skipped = 0
    for case in cases:
        qid = str(case["ticket_id"])
        row = by_id.get(qid)
        if row is None:
            skipped += 1
            continue
        gold = {str(t) for t in case["gold_similar_ticket_ids"]} - {qid}
        gains = {cand: g for (q, cand), g in grades.items() if q == qid and cand != qid}
        gains.update({t: 2 for t in gold})
        created = _created(row)
        ticket =TicketIn(subject=(row.get("subject") or "---")[:300], description=(row.get("description") or "-")[:5000],
                          affected_service=row.get("affected_service") or None, created_at=created)
        classification = None
        if classify is not None:
            try:
                classification = classify(ticket)
            except Exception as exc:
                logger.warning("classify failed on %s (%s); criterion dropped for it", qid, type(exc).__name__)
        filters = None
        if time_aware:
            # As in production: only tickets that existed before this one can be found. Without this, half
            # the gold tickets are in the query's future and age_days (clipped at 0) rewards them unfairly.
            if created is None:
                skipped += 1
                continue
            filters = TicketFilters(date_to=created - timedelta(seconds=1))
            before = {t for t in gold if _created(by_id.get(t)) and _created(by_id.get(t)) < created}
            gains = {t: g for t, g in gains.items() if _created(by_id.get(t)) and _created(by_id.get(t)) < created}
            gold = before
            if not gold:
                skipped += 1
                continue
        query = index_text(row)
        pool = [h for h in search_similar(query, k=pool_k + 1, filters=filters, affected_service=ticket.affected_service,
                                          category_hint=classification.category.label if classification else None,
                                          index=index) if h.ticket_id != qid][:pool_k]

        tops: dict[str, list[str]] = {"retrieval": [h.ticket_id for h in pool[:top_n]]}
        cats = {h.ticket_id: h.category for h in pool}
        for arm in ARMS[1:]:
            r = mcdm_rank(pool, query, ticket, classification, policy=run_policy, method=arm, index=index)
            tops[arm] = [h.ticket_id for h in r.similar]
            if arm in ("blended", "ahp_entropy"):
                for name, w in r.weights.items():
                    for kind in ("entropy", "prior", "blended"):
                        if w[kind] is not None:
                            weight_sums.setdefault(f"{name}|{arm}|{kind}", []).append(w[kind])
            if arm == "ahp" and r.ahp is not None:
                ahp_summary = {"cr": r.ahp["cr"], "accepted": r.ahp["accepted"], "weights": r.ahp["weights"]}
        for arm, ids in tops.items():
            m = per_arm[arm]
            m["recall"].append(len(gold & set(ids)) / len(gold) if gold else 0.0)
            m["mrr"].append(next((1 / r for r, t in enumerate(ids, start=1) if t in gold), 0.0))
            m["ndcg"].append(_ndcg(ids, gains, top_n))
            top3 = ids[:3]
            m["wrong_cat_top3"].append(sum(cats.get(t, "") != case.get("expected_category", row.get("category"))
                                           for t in top3) / len(top3) if top3 else 0.0)
            m["top1_changed"].append(float(bool(ids) and bool(tops["retrieval"]) and ids[0] != tops["retrieval"][0]))

    weights = {key: sum(v) / len(v) for key, v in weight_sums.items()}
    return {"split": split, "n": len(per_arm["retrieval"]["recall"]), "skipped": skipped, "mode": index.mode,
            "classifier": classify is not None, "time_aware": time_aware, "pool_k": pool_k, "top_n": top_n, "arms": per_arm,
            "mean_weights": weights, "ahp": ahp_summary}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compare retrieval order with TOPSIS re-ranking.")
    parser.add_argument("--split", default="val", choices=["val", "test"])
    parser.add_argument("--pool-k", type=int, default=None, help="candidates to re-rank (default policy pool_k)")
    parser.add_argument("--top-n", type=int, default=None, help="cut-off k for the metrics (default policy top_n)")
    parser.add_argument("--bm25-only", action="store_true", help="no embeddings (faster, offline)")
    parser.add_argument("--no-time-aware", action="store_true",
                        help="let queries find tickets from their future (NOT realistic; for comparison only)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.bm25_only:
        os.environ["DOC_SEARCH_DENSE"] = "0"

    from app.analysis.stats import bootstrap_ci
    from app.common.paths import DATA_DIR
    from app.tickets.rerank import mcdm_policy

    data_dir = DATA_DIR / "tickets"
    if not (data_dir / "labelled_cases.jsonl").exists() or not (data_dir / "tickets.csv").exists():
        print(f"No labelled data in {data_dir} (need tickets.csv and labelled_cases.jsonl). Nothing to evaluate.")
        return 0
    p = mcdm_policy()
    res = rerank_eval(args.split, pool_k=args.pool_k or int(p["pool_k"]), top_n=args.top_n or int(p["top_n"]),
                      time_aware=not args.no_time_aware)
    if res["n"] == 0:
        print(f"No usable gold cases for split '{args.split}'. Nothing to evaluate.")
        return 0

    k = res["top_n"]
    print(f"Re-ranking on the {res['split']} split: {res['n']} queries ({res['skipped']} skipped: no earlier gold), "
          f"pool {res['pool_k']} -> top {k}, search mode {res['mode']}, "
          f"{'time-aware (past tickets only)' if res['time_aware'] else 'NOT time-aware'}, "
          f"classifier {'used' if res['classifier'] else 'not available (criterion dropped)'}")
    print("95% bootstrap intervals.\n")
    print(f"| arm | recall@{k} | MRR | nDCG@{k} | top-1 changed | wrong category in top 3 |")
    print("|---|---|---|---|---|---|")
    for arm in ARMS:
        m = res["arms"][arm]
        cell = []
        for key in ("recall", "mrr", "ndcg"):
            point, low, high = bootstrap_ci(m[key])
            cell.append(f"{point:.3f} [{low:.3f}, {high:.3f}]")
        changed = sum(m["top1_changed"]) / len(m["top1_changed"])
        wrong = sum(m["wrong_cat_top3"]) / len(m["wrong_cat_top3"])
        print(f"| {arm} | {' | '.join(cell)} | {changed:.1%} | {wrong:.1%} |")

    a = res.get("ahp")
    if a:
        print(f"\nAHP judgements: CR {a['cr']:.3f} ({'accepted' if a['accepted'] else 'REJECTED: typed priors used'})")
    print("\nMean weights per criterion (criteria with data in this run; renormalised):\n")
    print("| criterion | entropy | typed prior | blended | AHP prior | ahp_entropy |")
    print("|---|---|---|---|---|---|")
    mw = res["mean_weights"]
    f = lambda v: "-" if v is None else f"{v:.3f}"  # noqa: E731
    for name in sorted({key.split("|")[0] for key in mw}):
        print(f"| {name} | {f(mw.get(f'{name}|blended|entropy'))} | {f(mw.get(f'{name}|blended|prior'))} | "
              f"{f(mw.get(f'{name}|blended|blended'))} | {f(mw.get(f'{name}|ahp_entropy|prior'))} | "
              f"{f(mw.get(f'{name}|ahp_entropy|blended'))} |")
    if res["split"] == "val":
        print("\nTune lam / prior weights here; set mcdm.enabled only if blended beats retrieval. Test once at the end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
