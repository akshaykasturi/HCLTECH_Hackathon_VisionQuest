"""Evaluate the whole pipeline (steps 1-6) on the labelled cases, and write the worked examples.

  cases     data/tickets/labelled_cases.jsonl (read-only): 330 val/test tickets from tickets.csv with gold outcome,
            similar tickets and KB article, plus 40 authored ESCALATE / BLOCKED cases.
  run       app.tickets.pipeline.analyze() per case. Tickets from tickets.csv are searched leave-one-out (their own
            ticket_id excluded: finding itself would fake a perfect match).
  offline   by default the LLM keys are blanked for this process, so recommendations use the template writer and
            runs are repeatable; --llm uses the configured providers instead. Results are NEVER saved to the
            database during evaluation (the save step is switched off), so evaluation never pollutes history.
  metrics   outcome accuracy (95% bootstrap CI) + confusion matrix + recall/precision per outcome; safety (BLOCKED
            recall, RECOMMEND where the gold says otherwise, RECOMMEND cases that were not recommended);
            similar tickets recall@5 / hit@1 / MRR against the gold same-issue tickets (the case's duplicate group),
            and again against every other ticket with the same root_cause (same scenario, other duplicate groups;
            root_cause is an evaluation label here, never a feature); KB hit@3 / MRR against the gold doc_id; recommendation checks (steps, findings, generator); latency p50 / p95 after warm-up.
  splits    val by default. --final scores test ONCE (refuses if docs/results/eval_test.json exists).
  output    docs/results/eval_<split>.json (ids, labels and numbers only: no ticket text),
            a section in docs/EVALUATION.md, and docs/WORKED-EXAMPLES.md (val only; masked ticket text).
  stand-in  the gate only RECOMMENDs with a classification. While tickets/classify.py is not merged, val is run
            twice: (1) main as it is, (2) with a STAND-IN classify() built from backend/models/clf_category.joblib
            and clf_subcategory.joblib (scripts/compare_classifiers.py), plugged in only inside this script and
            labelled "stand-in, not classify.py". Worked examples come from the stand-in run. Once classify.py is
            in the pipeline, the stand-in run is skipped and its EVALUATION.md section is removed.
  --final   needs the real classify.py: without it nothing is written and the test split is not used up.

Usage (from backend):  .\\.venv\\Scripts\\python.exe scripts\\evaluate.py [--final] [--llm] [--limit N]
                macOS:  .venv/bin/python scripts/evaluate.py [--final] [--llm] [--limit N]
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
import logging
import os
import re
import statistics
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.stats import bootstrap_ci  # noqa: E402
from app.common.paths import BACKEND_DIR, DATA_DIR  # noqa: E402
from app.tickets.models import AnalysisResult, TicketIn, User  # noqa: E402

CASES_PATH = DATA_DIR / "tickets" / "labelled_cases.jsonl"
TICKETS_CSV = DATA_DIR / "tickets" / "tickets.csv"
DOCS_DIR = BACKEND_DIR.parent / "docs"
RESULTS_DIR = DOCS_DIR / "results"
EVAL_MD = DOCS_DIR / "EVALUATION.md"
EXAMPLES_MD = DOCS_DIR / "WORKED-EXAMPLES.md"
OUTCOMES = ("RECOMMEND", "CLARIFY", "ESCALATE", "BLOCKED")
TICKET_FIELDS = ("subject", "description", "affected_service", "environment", "channel", "impact", "category_hint")
K_SIMILAR, K_KB = 5, 3
START = {"val": "<!-- eval-val:start -->", "test": "<!-- eval-test:start -->",
         "standin": "<!-- eval-val-standin:start -->"}
END = {"val": "<!-- eval-val:end -->", "test": "<!-- eval-test:end -->", "standin": "<!-- eval-val-standin:end -->"}
STANDIN_LABEL = "stand-in, not classify.py"
EVAL_USER = User(username="evaluation", role="analyst")


# ---------------------------------------------------------------- cases

def _lit(value: Any) -> Any:
    """labelled_cases.jsonl writes Python-style values as text ('None', "['INC-1']", "{'subject': ...}")."""
    if not isinstance(value, str):
        return value
    v = value.strip()
    if v == "None":
        return None
    if v[:1] in "[{(":
        try:
            return ast.literal_eval(v)
        except (ValueError, SyntaxError):
            return value
    return value


def load_cases(splits: Sequence[str], path: Path = CASES_PATH) -> list[dict[str, Any]]:
    """Cases of the requested splits only, with text-encoded values decoded. A test case is never read unless
    "test" is asked for."""
    out = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            raw = json.loads(line)
            if raw.get("split") not in splits:
                continue
            case = {k: _lit(v) for k, v in raw.items()}
            case["gold_similar_ticket_ids"] = list(case.get("gold_similar_ticket_ids") or [])
            case["gold_kb_doc_ids"] = list(case.get("gold_kb_doc_ids") or [])
            out.append(case)
    return out


def read_tickets(path: Path = TICKETS_CSV) -> dict[str, dict[str, Any]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return {r["ticket_id"]: r for r in csv.DictReader(f)}


def case_ticket(case: dict[str, Any], tickets: dict[str, dict[str, Any]]) -> tuple[TicketIn, list[str]]:
    """(TicketIn, ticket ids to exclude from similar search). Only the fields a new ticket has when it arrives:
    no category, resolution or other column known only after the ticket was worked on."""
    if isinstance(case.get("ticket"), dict):
        data = case["ticket"]
        exclude: list[str] = []
    else:
        tid = case.get("ticket_id")
        if not tid or tid not in tickets:
            raise KeyError(f"case {case.get('case_id')}: ticket {tid} not in tickets.csv")
        data = tickets[tid]
        exclude = [tid]
    fields = {k: data[k] for k in TICKET_FIELDS if data.get(k) not in (None, "")}
    return TicketIn(**fields), exclude


# ---------------------------------------------------------------- stand-in classifier

class StandInUnavailable(RuntimeError):
    pass


def standin_classify(models_dir: Optional[Path] = None) -> Callable[[TicketIn], Any]:
    """classify(ticket) -> Classification from the saved clf_category / clf_subcategory pipelines. Evaluation only
    ("stand-in, not classify.py"). Priority is not predicted (a rule decides it): label "unknown", prob 0."""
    import joblib
    from app.tickets.clf_pipeline import MODELS_DIR, TEXT_RECIPE
    from app.tickets.models import Classification, LabelScore

    folder = Path(models_dir or MODELS_DIR)
    manifest_path = folder / "clf_manifest.json"
    if not manifest_path.exists():
        raise StandInUnavailable("no backend/models/clf_manifest.json: run scripts/compare_classifiers.py first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("text_recipe") != TEXT_RECIPE:
        raise StandInUnavailable("the saved classifiers use an older text recipe: rerun compare_classifiers.py")
    models = {}
    for label in ("category", "subcategory"):
        entry = manifest.get("labels", {}).get(label, {})
        path = folder / str(entry.get("file", ""))
        if not entry.get("file") or not path.exists():
            raise StandInUnavailable(f"no saved {label} model: run scripts/compare_classifiers.py first")
        models[label] = joblib.load(path)
    version = f"stand-in:{manifest['labels']['category'].get('model_version', 'unknown')}"

    def ranked(label: str, row: dict[str, Any]) -> list[Any]:
        proba = models[label].predict_proba([row])[0]
        order = np.argsort(-proba)
        return [LabelScore(label=str(models[label].classes_[i]), prob=float(proba[i])) for i in order]

    def classify(ticket: TicketIn) -> Any:
        row = {"subject": ticket.subject, "description": ticket.description}   # the pipeline applies row_text
        cats = ranked("category", row)
        return Classification(category=cats[0], subcategory=ranked("subcategory", row)[0],
                              priority=LabelScore(label="unknown", prob=0.0), top3_category=cats[:3],
                              priority_rule="stand-in: priority not predicted", model_version=version)

    return classify


# ---------------------------------------------------------------- running

@contextmanager
def evaluation_mode(use_llm: bool, classify: Optional[Callable[..., Any]] = None) -> Iterator[None]:
    """No database writes; no LLM unless asked; `classify` (the stand-in) replaces tickets/classify.py when given.
    Everything is put back afterwards."""
    import app.tickets.pipeline as pipeline

    real_optional = pipeline.optional

    def no_save(module: str, name: str):
        if module == "app.db.repo":
            return None
        if classify is not None and (module, name) == ("app.tickets.classify", "classify"):
            return classify
        return real_optional(module, name)

    saved_env = {k: os.environ.get(k) for k in ("GEMINI_API_KEY", "GROQ_API_KEY")}
    pipeline.optional = no_save
    if use_llm:
        from app.common.env import load_dotenv
        load_dotenv()
    else:
        for k in saved_env:
            os.environ[k] = ""
    try:
        yield
    finally:
        pipeline.optional = real_optional
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_KB_DOC = re.compile(r"kb/([^#/]+)#")


def kb_doc_id(section_id: str) -> str:
    """'doc:kb/KB-020#3' -> 'KB-020'."""
    m = _KB_DOC.search(section_id or "")
    return m.group(1) if m else section_id


def same_cause_ids(tickets: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """ticket_id -> the other tickets with the same root_cause (an evaluation label only)."""
    by_cause: dict[str, list[str]] = {}
    for tid, row in tickets.items():
        if row.get("root_cause"):
            by_cause.setdefault(row["root_cause"], []).append(tid)
    return {tid: [t for t in by_cause.get(row.get("root_cause"), []) if t != tid]
            for tid, row in tickets.items() if row.get("root_cause")}


def record(case: dict[str, Any], result: AnalysisResult, ms: float,
           same_cause: Optional[list[str]] = None) -> dict[str, Any]:
    """What is kept per case: ids, labels and numbers. No ticket text."""
    rec = result.recommendation
    return {
        "case_id": case["case_id"], "ticket_id": case.get("ticket_id"), "source": case.get("source"),
        "expected_outcome": case.get("expected_outcome"), "expected_reason": case.get("expected_reason"),
        "outcome": result.outcome, "reasons": list(result.reasons), "confidence": round(result.confidence, 4),
        "gold_similar": case["gold_similar_ticket_ids"], "similar": [s.ticket_id for s in result.similar],
        "gold_same_cause": list(same_cause or []),
        "gold_kb": case["gold_kb_doc_ids"], "kb": [kb_doc_id(k.section_id) for k in result.kb],
        "generator": rec.generator if rec else None, "steps": len(rec.steps) if rec else 0,
        "findings": [f.code for f in result.findings],
        "classified": result.classification is not None,
        "ms": round(ms, 2),
    }


def warm_up() -> None:
    """Build the search indexes, load the embedding model and train/load the classifier before timing anything."""
    from app.common.trace import optional
    from app.tickets.similar import get_ticket_index
    get_ticket_index()
    load_classifier = optional("app.tickets.classify", "load_classifier")
    if load_classifier is not None:
        load_classifier()
    try:
        from app.tickets.kb import get_kb_index
        get_kb_index()
    except ImportError:
        pass


def run(cases: Sequence[dict[str, Any]], tickets: dict[str, dict[str, Any]], use_llm: bool = False,
        analyze: Optional[Callable[..., AnalysisResult]] = None, log: Callable[[str], None] = print,
        classify: Optional[Callable[..., Any]] = None) -> tuple[list[dict[str, Any]], dict[str, AnalysisResult]]:
    """(records, results by case_id). `classify` = the stand-in; `analyze` is replaced only by tests."""
    with evaluation_mode(use_llm, classify):
        if analyze is None:
            from app.tickets.pipeline import analyze as real_analyze
            analyze = real_analyze
            warm_up()
        records, results = [], {}
        causes = same_cause_ids(tickets)
        for i, case in enumerate(cases, 1):
            ticket, exclude = case_ticket(case, tickets)
            start = time.perf_counter()
            result = analyze(ticket, EVAL_USER, exclude_ticket_ids=exclude)
            ms = (time.perf_counter() - start) * 1000
            records.append(record(case, result, ms, causes.get(case.get("ticket_id") or "")))
            results[case["case_id"]] = result
            if i % 50 == 0:
                log(f"  {i}/{len(cases)} cases")
    return records, results


# ---------------------------------------------------------------- metrics

def _rate(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None


def outcome_metrics(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    correct = [r["outcome"] == r["expected_outcome"] for r in records]
    point, lo, hi = bootstrap_ci(np.array(correct, dtype=float), seed=0) if records else (None, None, None)
    matrix = {e: {g: 0 for g in OUTCOMES} for e in OUTCOMES}
    for r in records:
        if r["expected_outcome"] in matrix and r["outcome"] in OUTCOMES:
            matrix[r["expected_outcome"]][r["outcome"]] += 1
    per = {}
    for o in OUTCOMES:
        tp = matrix[o][o]
        expected = sum(matrix[o].values())
        predicted = sum(matrix[e][o] for e in OUTCOMES)
        per[o] = {"expected": expected, "predicted": predicted, "recall": _rate(tp, expected),
                  "precision": _rate(tp, predicted)}
    return {"n": len(records), "accuracy": None if point is None else round(point, 4),
            "accuracy_ci95": None if point is None else [round(lo, 4), round(hi, 4)],
            "confusion": matrix, "per_outcome": per}


def safety_metrics(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    should_block = [r for r in records if r["expected_outcome"] == "BLOCKED"]
    not_rec = [r for r in records if r["expected_outcome"] != "RECOMMEND"]
    should_rec = [r for r in records if r["expected_outcome"] == "RECOMMEND"]
    unsafe = [r for r in not_rec if r["outcome"] == "RECOMMEND"]
    return {
        "blocked_recall": _rate(sum(r["outcome"] == "BLOCKED" for r in should_block), len(should_block)),
        "blocked_missed": [r["case_id"] for r in should_block if r["outcome"] != "BLOCKED"],
        "recommend_when_gold_is_not": _rate(len(unsafe), len(not_rec)),
        "recommend_when_gold_is_not_cases": [r["case_id"] for r in unsafe],
        "recommend_cases_not_recommended": _rate(sum(r["outcome"] != "RECOMMEND" for r in should_rec), len(should_rec)),
    }


def ranking_metrics(pairs: Sequence[tuple[Sequence[str], Sequence[str]]], k: int) -> dict[str, Any]:
    """pairs = (gold ids, ranked ids). Cases with no gold are skipped. recall@k = share of gold found in the top k
    (mean over cases); hit@1 = top result is gold; hit@k = any gold in the top k; MRR = 1 / rank of first gold."""
    used = [(set(g), list(r)[:k]) for g, r in pairs if g]
    if not used:
        return {"n": 0, f"recall@{k}": None, "hit@1": None, f"hit@{k}": None, "mrr": None}
    recall, hit1, hitk, rr = [], [], [], []
    for gold, ranked in used:
        found = [i for i, x in enumerate(ranked) if x in gold]
        recall.append(len(gold & set(ranked)) / len(gold))
        hit1.append(1.0 if ranked[:1] and ranked[0] in gold else 0.0)
        hitk.append(1.0 if found else 0.0)
        rr.append(1.0 / (found[0] + 1) if found else 0.0)
    m = statistics.fmean
    return {"n": len(used), f"recall@{k}": round(m(recall), 4), "hit@1": round(m(hit1), 4),
            f"hit@{k}": round(m(hitk), 4), "mrr": round(m(rr), 4)}


def retrieval_metrics(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Only cases where search ran (not BLOCKED by the screen) and that have gold labels."""
    searched = [r for r in records if r["outcome"] != "BLOCKED"]
    return {"similar": ranking_metrics([(r["gold_similar"], r["similar"]) for r in searched], K_SIMILAR),
            "similar_same_cause": ranking_metrics([(r.get("gold_same_cause", []), r["similar"]) for r in searched],
                                                  K_SIMILAR),
            "kb": ranking_metrics([(r["gold_kb"], list(dict.fromkeys(r["kb"]))) for r in searched], K_KB)}


def recommendation_metrics(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    recs = [r for r in records if r["outcome"] == "RECOMMEND"]
    findings = Counter(code for r in recs for code in r["findings"])
    return {"n": len(recs), "generators": dict(Counter(r["generator"] for r in recs)),
            "mean_steps": round(statistics.fmean(r["steps"] for r in recs), 2) if recs else None,
            "with_findings": sum(1 for r in recs if r["findings"]), "finding_codes": dict(findings.most_common(10))}


def latency(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ms = sorted(r["ms"] for r in records)
    if not ms:
        return {"p50_ms": None, "p95_ms": None}
    return {"p50_ms": round(float(np.percentile(ms, 50)), 1), "p95_ms": round(float(np.percentile(ms, 95)), 1)}


def reasons_by_expected(records: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """For each gold reason, which reason codes the gate gave (to see why cases went the wrong way)."""
    out: dict[str, Counter] = {}
    for r in records:
        out.setdefault(str(r["expected_reason"]), Counter()).update(r["reasons"] or ["(none)"])
    return {k: dict(v.most_common(5)) for k, v in sorted(out.items())}


def summarize(records: Sequence[dict[str, Any]], split: str, use_llm: bool, standin: bool = False) -> dict[str, Any]:
    return {
        "split": split, "standin_classifier": standin,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "llm": "configured providers" if use_llm else "off (template writer)",
        "classifier_in_pipeline": sum(r["classified"] for r in records),
        "outcome": outcome_metrics(records), "safety": safety_metrics(records),
        "retrieval": retrieval_metrics(records), "recommendations": recommendation_metrics(records),
        "latency": latency(records), "reasons_by_expected": reasons_by_expected(records),
        "cases": list(records),
    }


# ---------------------------------------------------------------- reports

def _f(x: Optional[float], pct: bool = False) -> str:
    if x is None:
        return "-"
    return f"{x:.1%}" if pct else f"{x:.3f}"


def eval_markdown(s: dict[str, Any]) -> str:
    split, o, sf, rt, rc = s["split"], s["outcome"], s["safety"], s["retrieval"], s["recommendations"]
    lo, hi = o["accuracy_ci95"] or (None, None)
    standin = s.get("standin_classifier", False)
    key = "standin" if standin else split
    title = {"val": "Pipeline evaluation (val, main as it is)", "test": "Pipeline evaluation (test, FINAL)",
             "standin": f"Pipeline evaluation (val, {STANDIN_LABEL})"}[key]
    lines = [START[key], f"## {title}", "",
             f"{o['n']} labelled cases, `scripts/evaluate.py`, LLM {s['llm']}, nothing saved to the database. "
             "Tickets from tickets.csv are searched leave-one-out. Selection and tuning use val; test is scored once "
             "with --final.", ""]
    if standin:
        lines += [f"**{STANDIN_LABEL.capitalize()}.** `tickets/classify.py` is not merged yet, so this run plugs in the "
                  "saved classifiers from `scripts/compare_classifiers.py` (category and subcategory, svm C1) as "
                  "classify(), inside `evaluate.py` only. Priority is not predicted. When classify.py is merged this "
                  "section is removed and the section above becomes the real result.", ""]
    elif s["classifier_in_pipeline"] == 0:
        lines += ["**Note:** `tickets/classify.py` is not in the pipeline yet, so these runs have no classification. "
                  "The gate only RECOMMENDs with a classification at least 0.60 sure, so no case can be recommended "
                  "here: this is main's real state today. See the stand-in run below. The classifier itself is "
                  "evaluated in \"Classifier comparison (val)\" above.", ""]
    lines += [f"**Outcome accuracy:** {_f(o['accuracy'], True)} (95% CI {_f(lo, True)}-{_f(hi, True)})", "",
              "| Gold \\ Got | " + " | ".join(OUTCOMES) + " | Recall |", "|---" * (len(OUTCOMES) + 2) + "|"]
    for e in OUTCOMES:
        row = o["confusion"][e]
        lines.append(f"| {e} | " + " | ".join(str(row[g]) for g in OUTCOMES)
                     + f" | {_f(o['per_outcome'][e]['recall'], True)} |")
    lines.append("| Precision | " + " | ".join(_f(o["per_outcome"][g]["precision"], True) for g in OUTCOMES) + " | |")
    lines += ["", "**Safety**", "",
              f"- BLOCKED recall: {_f(sf['blocked_recall'], True)}"
              + (f" (missed: {', '.join(sf['blocked_missed'])})" if sf["blocked_missed"] else ""),
              f"- RECOMMEND given where the gold outcome is not RECOMMEND: {_f(sf['recommend_when_gold_is_not'], True)}"
              + (f" ({', '.join(sf['recommend_when_gold_is_not_cases'][:8])})"
                 if sf["recommend_when_gold_is_not_cases"] else ""),
              f"- Gold RECOMMEND cases that got another outcome (over-caution): "
              f"{_f(sf['recommend_cases_not_recommended'], True)}", "",
              "**Retrieval** (cases where search ran and gold labels exist)", "",
              "| Search | Cases | Recall@k | Hit@1 | Hit@k | MRR |", "|---|---|---|---|---|---|"]
    labels = {"similar": "Similar tickets (k=5, gold = same duplicate group)",
              "similar_same_cause": "Similar tickets (k=5, gold = same root_cause)",
              "kb": "KB (k=3, gold = doc_id)"}
    for name, k in (("similar", K_SIMILAR), ("similar_same_cause", K_SIMILAR), ("kb", K_KB)):
        m = rt[name]
        label = labels[name]
        lines.append(f"| {label} | {m['n']} | {_f(m.get(f'recall@{k}'))} | {_f(m['hit@1'])} | "
                     f"{_f(m.get(f'hit@{k}'))} | {_f(m['mrr'])} |")
    gens = ", ".join(f"{g} {n}" for g, n in rc["generators"].items()) or "-"
    lines += ["", f"**Recommendations:** {rc['n']} (writer: {gens}), mean {rc['mean_steps'] or 0} cited steps, "
              f"{rc['with_findings']} with check findings"
              + (f" ({', '.join(f'{c} {n}' for c, n in rc['finding_codes'].items())})" if rc["finding_codes"] else "")
              + ".", "",
              "The duplicate-group gold is strict: a top match from the same scenario but another duplicate group "
              "counts as a miss. The same-root_cause row counts it as found (recall@5 there is capped because a "
              "root cause has more tickets than 5 slots).", "",
              f"**Latency** (after warm-up): p50 {s['latency']['p50_ms']} ms, p95 {s['latency']['p95_ms']} ms.", "",
              "Gate reasons per gold reason (top 5):", "", "| Gold reason | Gate reasons given |", "|---|---|"]
    for reason, counts in s["reasons_by_expected"].items():
        lines.append(f"| {reason} | " + ", ".join(f"{k} {v}" for k, v in counts.items()) + " |")
    lines.append(END[key])
    return "\n".join(lines)


def write_section(path: Path, start: str, end: str, block: str) -> None:
    """Replace the block between the markers, or append it; an empty block removes the section."""
    text = path.read_text(encoding="utf-8") if path.exists() else "# Evaluation\n"
    pattern = re.compile(r"\n*" + re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not block:
        text = pattern.sub("", text).rstrip("\n") + "\n"
    elif pattern.search(text):
        text = pattern.sub(lambda _: "\n\n" + block, text)
    else:
        text = text.rstrip("\n") + "\n\n" + block + "\n"
    path.write_text(text, encoding="utf-8")


def pick_examples(records: Sequence[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """One correct case per outcome (first by case_id), plus the first wrong case, so the examples are not only
    successes. Deterministic."""
    chosen: list[tuple[str, dict[str, Any]]] = []
    ordered = sorted(records, key=lambda r: r["case_id"])
    for o in OUTCOMES:
        hit = next((r for r in ordered if r["expected_outcome"] == o and r["outcome"] == o), None)
        if hit:
            chosen.append((f"Correct {o}", hit))
    miss = next((r for r in ordered if r["outcome"] != r["expected_outcome"]), None)
    if miss:
        chosen.append((f"Wrong: gold {miss['expected_outcome']}, got {miss['outcome']}", miss))
    return chosen


def _one_line(text: Optional[str], limit: int = 400) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= limit else t[: limit - 3] + "..."


def examples_markdown(records: Sequence[dict[str, Any]], results: dict[str, AnalysisResult], use_llm: bool,
                      standin: bool = False) -> str:
    lines = ["# Worked examples", "",
             "Generated by `backend/scripts/evaluate.py` from real pipeline runs on **val** cases (LLM "
             f"{'on' if use_llm else 'off: template writer'}). Ticket text is the pipeline's masked copy; the "
             "tickets are synthetic (docs/DATA-CARD.md). One correct case per outcome, plus the first wrong case.", ""]
    if standin:
        lines += [f"**Classifier: {STANDIN_LABEL}.** `tickets/classify.py` is not merged yet; these runs use the saved "
                  "classifiers from `scripts/compare_classifiers.py`, plugged in inside `evaluate.py` only. They will "
                  "be regenerated with the real classify.py.", ""]
    for n, (title, r) in enumerate(pick_examples(records), 1):
        res = results[r["case_id"]]
        t = res.ticket
        lines += [f"## {n}. {title} ({r['case_id']})", "",
                  f"**Ticket** (masked): *{_one_line(t.subject, 150)}*. {_one_line(t.description)}",
                  f"Service `{t.affected_service or '-'}`, environment `{t.environment or '-'}`, "
                  f"channel `{t.channel or '-'}`.", "",
                  f"**Gold:** {r['expected_outcome']} ({r['expected_reason']}). "
                  f"**Got:** {res.outcome}, reasons `{', '.join(res.reasons) or '-'}`, "
                  f"confidence {res.confidence:.2f}.", ""]
        if res.question:
            lines += [f"**Question to the analyst:** {_one_line(res.question)}", ""]
        if res.escalation_group:
            lines += [f"**Escalation group:** {res.escalation_group}", ""]
        if res.similar:
            lines += ["**Similar tickets** (gold: " + (", ".join(r["gold_similar"]) or "none") + ")", "",
                      "| Ticket | Score | Match | Why |", "|---|---|---|---|"]
            lines += [f"| {s.ticket_id} | {s.score:.2f} | {s.match}{' (lookalike)' if s.lookalike else ''} | "
                      f"{_one_line('; '.join(s.why), 80) or '-'} |" for s in res.similar[:K_SIMILAR]]
            lines.append("")
        if res.kb:
            lines += ["**KB sections** (gold: " + (", ".join(r["gold_kb"]) or "none") + "): "
                      + ", ".join(f"{k.section_id} ({k.score:.2f})" for k in res.kb), ""]
        if res.recommendation:
            rec = res.recommendation
            lines += [f"**Recommendation** (writer: {rec.generator}): {_one_line(rec.summary)}", ""]
            for i, step in enumerate(rec.steps, 1):
                lines.append(f"{i}. {_one_line(step.text, 200)} - source `{step.source_id}`: "
                             f"\"{_one_line(step.excerpt, 160)}\"")
            lines.append("")
        if res.findings:
            lines += ["**Check findings:** " + ", ".join(f"{f.code} ({f.severity})" for f in res.findings), ""]
        lines += ["**Trace:** " + " -> ".join(f"{s.step} ({s.ms:.0f} ms)" for s in res.trace), ""]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- main

class FinalRefused(RuntimeError):
    pass


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the pipeline on the labelled cases.")
    parser.add_argument("--final", action="store_true", help="score the TEST split once (otherwise val)")
    parser.add_argument("--llm", action="store_true", help="use the configured LLM providers (default: template)")
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases (quick check; not saved)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)

    split = "test" if args.final else "val"
    out_json = RESULTS_DIR / f"eval_{split}.json"
    if args.final and out_json.exists() and args.limit is None:
        print(f"REFUSED: {out_json.name} exists; the test split is scored only once.")
        return 1
    cases = load_cases((split,))
    if args.limit:
        cases = cases[: args.limit]
    tickets = read_tickets()
    print(f"Evaluating {len(cases)} {split} cases (LLM {'on' if args.llm else 'off'}) ...")
    records, results = run(cases, tickets, use_llm=args.llm)
    summary = summarize(records, split, args.llm)
    md = eval_markdown(summary)
    print(md)
    classified = summary["classifier_in_pipeline"] > 0

    if args.final and not classified:
        print("\nNOT WRITTEN: test is scored only with the real tickets/classify.py in the pipeline. "
              "The test split was not used up.")
        return 2

    standin_summary = standin_md = None
    standin_records: Sequence[dict[str, Any]] = ()
    standin_results: dict[str, AnalysisResult] = {}
    if split == "val" and not classified:
        try:
            classify = standin_classify()
        except StandInUnavailable as exc:
            print(f"\nNOT WRITTEN: classify.py is not merged and the stand-in is unavailable ({exc}).")
            return 2
        print(f"\nSecond run ({STANDIN_LABEL}) ...")
        standin_records, standin_results = run(cases, tickets, use_llm=args.llm, classify=classify)
        standin_summary = summarize(standin_records, split, args.llm, standin=True)
        standin_md = eval_markdown(standin_summary)
        print(standin_md)

    if args.limit:
        print("\n--limit given: nothing written.")
        return 0
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_section(EVAL_MD, START[split], END[split], md)
    written = [out_json.name, EVAL_MD.name]
    if split == "val":
        standin_json = RESULTS_DIR / "eval_val_standin.json"
        if standin_summary is not None:
            standin_json.write_text(json.dumps(standin_summary, indent=2), encoding="utf-8")
            write_section(EVAL_MD, START["standin"], END["standin"], standin_md)
            EXAMPLES_MD.write_text(examples_markdown(standin_records, standin_results, args.llm, standin=True),
                                   encoding="utf-8")
            written.append(standin_json.name)
        else:
            # classify.py is merged: the stand-in section and file are no longer needed.
            write_section(EVAL_MD, START["standin"], END["standin"], "")
            if standin_json.exists():
                standin_json.unlink()
            EXAMPLES_MD.write_text(examples_markdown(records, results, args.llm), encoding="utf-8")
        written.append(EXAMPLES_MD.name)
    print("\nWrote " + ", ".join(written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
