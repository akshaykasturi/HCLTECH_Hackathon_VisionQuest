"""Chunking trial (mentor): 3 chunking strategies x 3 retrieval methods on the KB.

Run from the backend folder (val to choose, test once at the end for the report):
    .\\.venv\\Scripts\\python.exe scripts\\eval_chunking.py --split val
    .\\.venv\\Scripts\\python.exe scripts\\eval_chunking.py --split test

Queries: labelled cases of the split that have gold_kb_doc_id + key_sentence; the query is the ticket's
subject + ". " + description from tickets.csv, masked, exactly as the pipeline searches.
  recall@3       a top-3 chunk's ORIGINAL text contains the key sentence (case/whitespace-insensitive)
  MRR            1 / rank of the first such chunk within the top 10
  doc_recall@3   a top-3 chunk comes from the gold article
  n_chunks, avg_words, build_seconds (chunking + indexing; the embedding model is loaded before timing)
Writes docs/results/chunking_<split>.json. Every number is computed here; nothing is typed by hand.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.common.doc_search import _dense_wanted, build_index, search  # noqa: E402
from app.common.embed import DEFAULT_MODEL  # noqa: E402
from app.common.redact import mask  # noqa: E402
from app.tickets.chunking import STRATEGIES, build_chunks, evaluate  # noqa: E402

DATA = BACKEND_DIR / "data" / "tickets"
RESULTS = BACKEND_DIR.parent / "docs" / "results"
METHODS = ("bm25", "dense", "hybrid")


def load_queries(split: str) -> list[dict[str, str]]:
    rows = {r["ticket_id"]: r for r in csv.DictReader(open(DATA / "tickets.csv", encoding="utf-8-sig"))}
    out = []
    for line in open(DATA / "labelled_cases.jsonl", encoding="utf-8"):
        c = json.loads(line)
        if c["split"] != split or not c.get("gold_kb_doc_id") or not c.get("key_sentence"):
            continue
        src = c.get("ticket") or rows.get(c.get("ticket_id") or "")
        if not src:
            continue
        out.append({"case_id": c["case_id"], "query": mask(f"{src['subject']}. {src['description']}"),
                    "key_sentence": c["key_sentence"], "gold_doc": c["gold_kb_doc_id"]})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="KB chunking trial")
    parser.add_argument("--split", choices=("val", "test"), default="val")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--depth", type=int, default=10, help="ranks searched for MRR")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)

    queries = load_queries(args.split)
    dense = _dense_wanted()
    if dense:  # load the model once, outside the timed builds, so no strategy pays for it
        from app.common.embed import embed_texts
        embed_texts(["warm up"])

    rows = []
    for strategy in STRATEGIES:
        started = time.perf_counter()
        indexed, shown = build_chunks(strategy)
        idx = build_index(indexed)
        build_seconds = round(time.perf_counter() - started, 2)
        avg_words = round(sum(len(s.text.split()) for s in shown.values()) / max(len(shown), 1), 1)
        for method in METHODS:
            if method != "bm25" and idx.mode != "hybrid":
                continue  # no embeddings: dense and hybrid cannot run

            def run(query: str, depth: int, method=method, idx=idx) -> list[str]:
                return [h.section_id for h in search(query, k=depth, method=method, index=idx)]

            scores = evaluate(queries, run, shown, k=args.k, depth=args.depth)
            rows.append({"strategy": strategy, "method": method, **scores, "n_chunks": len(shown),
                         "avg_words": avg_words, "build_seconds": build_seconds})

    best = max(rows, key=lambda r: (r["recall_at_k"], r["mrr"], r["doc_recall_at_k"]))
    k = args.k
    header = f"{'strategy':11} {'method':7} {f'recall@{k}':>9} {'MRR':>6} {f'doc_recall@{k}':>13} " \
             f"{'n_chunks':>8} {'avg_words':>9} {'build_s':>7}"
    print(f"split {args.split}: {len(queries)} queries, embeddings {'on' if dense else 'off'}\n{header}\n"
          + "-" * len(header))
    for r in rows:
        mark = "  <- best" if r is best else ""
        print(f"{r['strategy']:11} {r['method']:7} {r['recall_at_k']:9.3f} {r['mrr']:6.3f} "
              f"{r['doc_recall_at_k']:13.3f} {r['n_chunks']:8} {r['avg_words']:9.1f} {r['build_seconds']:7.2f}{mark}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"chunking_{args.split}.json"
    out.write_text(json.dumps({"split": args.split, "n_queries": len(queries), "k": k, "mrr_depth": args.depth,
                               "embeddings": DEFAULT_MODEL if dense else None,
                               "best": {"strategy": best["strategy"], "method": best["method"]},
                               "results": rows}, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {out.relative_to(BACKEND_DIR.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
