"""Real-LLM check of steps 5-6: does the LLM write fix steps that survive tickets/checks.py?

Run from the backend folder, with GEMINI_API_KEY and/or GROQ_API_KEY in .env:
    .\\.venv\\Scripts\\python.exe scripts\\llm_check.py              # 10 val tickets, prints a summary
    .\\.venv\\Scripts\\python.exe scripts\\llm_check.py --n 20 --write  # also writes docs/LLM_CHECK.md + .json

Why it skips the gate: until the classifier is merged the gate escalates every normal ticket, so the LLM step never
runs on real tickets. This script feeds the LLM exactly what the pipeline would after a RECOMMEND: the masked ticket,
the similar past tickets (its own ticket left out) and the KB sections, with the same allowed ids. Then it runs the
same checks. Nothing is saved to the database; no db/ needed.

What it reports:
  answered        tickets where an LLM replied with valid JSON (Gemini first, then Groq)
  steps kept      steps that passed every check / steps the LLM wrote  (= step groundedness)
  verified        tickets with at least one kept step (these would be RECOMMEND; the rest NO_VERIFIED_STEPS)
  why dropped     how often each check fired (CITE_QUOTE_NOT_FOUND, STEP_SOURCE_NOT_ALLOWED, CERTAINTY, ...)

Calls are spaced out (--pause, default 4 s) to stay under free-tier rate limits.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

LABELLED_FILE = BACKEND / "data" / "tickets" / "labelled_cases.jsonl"
TICKETS_CSV = BACKEND / "data" / "tickets" / "tickets.csv"
REPORT_MD = BACKEND.parent / "docs" / "LLM_CHECK.md"
REPORT_JSON = BACKEND.parent / "docs" / "llm_check.json"


def load_tickets(split: str, n: int, labelled: Path = LABELLED_FILE, tickets_csv: Path = TICKETS_CSV
                 ) -> list[dict[str, Any]]:
    """The first n labelled cases of `split` that should be RECOMMEND, with their ticket row."""
    with tickets_csv.open(encoding="utf-8") as f:
        rows = {r["ticket_id"]: r for r in csv.DictReader(f)}
    out = []
    for line in labelled.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        if c.get("split") != split or c.get("expected_outcome") != "RECOMMEND":
            continue
        row = rows.get(c.get("ticket_id") or "")
        if row:
            out.append({"case_id": c["case_id"], "ticket_id": row["ticket_id"], "row": row})
        if len(out) >= n:
            break
    return out


def evidence_for(masked: Any, ticket_id: str) -> tuple[list[Any], list[Any], list[str]]:
    """Similar tickets + KB sections + allowed ids, exactly as pipeline.analyze builds them (own ticket left out)."""
    from app.common.trace import optional
    from app.tickets.similar import search_similar
    query = f"{masked.subject}. {masked.description}"
    similar = search_similar(query, category_hint=None, affected_service=masked.affected_service,
                             exclude_ticket_ids=[ticket_id])
    search_kb = optional("app.tickets.kb", "search_kb")
    kb = (search_kb(query) or []) if search_kb else []
    allowed = [s.ticket_id for s in similar if not s.lookalike] + [k.section_id for k in kb]
    return similar, kb, allowed


def check_one(case: dict[str, Any], generate_fn: Callable[..., Any]) -> dict[str, Any]:
    """Mask -> evidence -> LLM -> checks for one ticket. Returns counts and codes, no ticket text."""
    from app.tickets.checks import check_recommendation
    from app.tickets.guard import mask_ticket
    from app.tickets.models import TicketIn
    r = case["row"]
    ticket = TicketIn(subject=r["subject"][:300].ljust(3, "."), description=(r["description"] or "(none)")[:5000],
                      affected_service=(r.get("affected_service") or None), environment=(r.get("environment") or None))
    masked, _ = mask_ticket(ticket)
    similar, kb, allowed = evidence_for(masked, case["ticket_id"])
    started = time.perf_counter()
    rec, provider, errors = generate_fn(masked, None, similar, kb, allowed)
    ms = round((time.perf_counter() - started) * 1000)
    out = {"case_id": case["case_id"], "ticket_id": case["ticket_id"], "evidence": len(allowed), "provider": provider,
           "ms": ms, "llm_errors": errors, "written": 0, "kept": 0, "codes": [], "dropped_examples": []}
    if rec is None:
        return out
    out["written"] = len(rec.steps)
    checked, findings = check_recommendation(rec, similar, kb, allowed, ticket=masked)
    out["kept"] = len(checked.steps) if checked else 0
    out["codes"] = [f.code for f in findings if f.severity == "block" and f.code != "NO_STEPS_LEFT"]
    out["warnings"] = [f.code for f in findings if f.severity == "warn"]
    by_step: dict[str, list[str]] = {}
    for f in findings:
        if f.severity == "block" and f.where.startswith("steps["):
            by_step.setdefault(f.where.split("]")[0] + "]", []).append(f.code)
    for where, codes in list(by_step.items())[:2]:
        i = int(where[6:-1])
        if i < len(rec.steps):
            s = rec.steps[i]
            out["dropped_examples"].append({"codes": codes, "text": s.text[:160], "source_id": s.source_id,
                                            "excerpt": s.excerpt[:160]})
    return out


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results)
    answered = [r for r in results if r["provider"]]
    written = sum(r["written"] for r in results)
    kept = sum(r["kept"] for r in results)
    return {
        "tickets": n,
        "answered": len(answered),
        "providers": dict(Counter(r["provider"] for r in answered)),
        "steps_written": written,
        "steps_kept": kept,
        "step_groundedness": round(kept / written, 4) if written else None,
        "tickets_verified": sum(1 for r in results if r["kept"] > 0),
        "tickets_empty_answer": sum(1 for r in answered if r["written"] == 0),
        "why_dropped": dict(Counter(c for r in results for c in r["codes"]).most_common()),
        "warnings": dict(Counter(c for r in results for c in r.get("warnings", [])).most_common()),
        "median_ms": sorted(r["ms"] for r in answered)[len(answered) // 2] if answered else None,
        "llm_errors": sorted({e for r in results for e in r["llm_errors"]})[:10],
    }


def render_markdown(summary: dict[str, Any], results: list[dict[str, Any]], split: str, when: str) -> str:
    s = summary
    g = "n/a" if s["step_groundedness"] is None else f"{100 * s['step_groundedness']:.0f}%"
    lines = [
        "# LLM check (steps 5-6 on real tickets)",
        "",
        f"Generated by `backend/scripts/llm_check.py` on {when}, {s['tickets']} `{split}` tickets expected to be "
        "RECOMMEND. The gate is skipped (no classifier yet); evidence, allowed ids and checks are the pipeline's own.",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| LLM answered | {s['answered']}/{s['tickets']} ({', '.join(f'{k} {v}' for k, v in s['providers'].items()) or 'none'}) |",
        f"| Steps kept / written (step groundedness) | {s['steps_kept']}/{s['steps_written']} ({g}) |",
        f"| Tickets with at least one verified step | {s['tickets_verified']}/{s['tickets']} |",
        f"| LLM said no evidence fits (empty steps) | {s['tickets_empty_answer']} |",
        f"| Median LLM time | {s['median_ms']} ms |",
        "",
        "## Why steps were dropped",
        "",
        "| Check | Times |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in s["why_dropped"].items()],
        "",
    ]
    examples = [e for r in results for e in r["dropped_examples"]][:6]
    if examples:
        lines += ["## Examples of dropped steps (synthetic data, masked)", ""]
        for e in examples:
            lines += [f"- **{', '.join(e['codes'])}**, source `{e['source_id']}`: step \"{e['text']}\"; "
                      f"excerpt \"{e['excerpt']}\""]
        lines.append("")
    lines += ["Live LLM output varies run to run; rerun before quoting numbers.", ""]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None, generate_fn: Optional[Callable[..., Any]] = None) -> int:
    ap = argparse.ArgumentParser(description="Check real LLM steps against tickets/checks.py.")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--split", choices=("val", "test"), default="val")
    ap.add_argument("--pause", type=float, default=4.0, help="seconds between LLM calls (free-tier limits)")
    ap.add_argument("--write", action="store_true", help="write docs/LLM_CHECK.md and docs/llm_check.json")
    args = ap.parse_args(argv)

    from app.common.env import load_dotenv
    load_dotenv()
    import logging
    logging.disable(logging.WARNING)
    if generate_fn is None:
        if not (os.getenv("GEMINI_API_KEY") or os.getenv("GROQ_API_KEY")):
            print("No GEMINI_API_KEY or GROQ_API_KEY in backend/.env: nothing to check.")
            return 1
        from app.tickets.generate import generate_detailed
        generate_fn = generate_detailed

    cases = load_tickets(args.split, args.n)
    results = []
    try:
        for i, case in enumerate(cases):
            if i and args.pause:
                time.sleep(args.pause)
            r = check_one(case, generate_fn)
            results.append(r)
            print(f"  {r['case_id']}: {r['provider'] or 'no LLM'} | evidence {r['evidence']} | "
                  f"kept {r['kept']}/{r['written']} | {', '.join(sorted(set(r['codes']))) or '-'}")
    finally:
        logging.disable(logging.NOTSET)
    summary = summarise(results)
    g = summary["step_groundedness"]
    print(f"answered {summary['answered']}/{summary['tickets']} | steps kept {summary['steps_kept']}/"
          f"{summary['steps_written']}" + (f" ({100 * g:.0f}%)" if g is not None else "") +
          f" | tickets verified {summary['tickets_verified']}/{summary['tickets']} | why dropped {summary['why_dropped']}")
    if summary["llm_errors"]:
        print("LLM problems (masked):", "; ".join(summary["llm_errors"][:3]))
    if args.write:
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        REPORT_MD.write_text(render_markdown(summary, results, args.split, when), encoding="utf-8")
        REPORT_JSON.write_text(json.dumps({"generated_at": when, "split": args.split, **summary,
                                           "results": results}, indent=2), encoding="utf-8")
        print(f"wrote {REPORT_MD.relative_to(BACKEND.parent)} and {REPORT_JSON.relative_to(BACKEND.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
