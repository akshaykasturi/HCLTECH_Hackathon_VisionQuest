"""MTTR: the manual baseline (measured) and the assisted MTTR (estimated) for FR-09.

  baseline_mttr      MEASURED from historical tickets: resolved_at - created_at, in hours, for tickets whose status
                     is resolved or closed. Rows with a missing, unreadable or negative duration are skipped and
                     counted, never guessed.
  estimate_assisted  An ESTIMATE, never an observation. The assistant does not resolve tickets; it can only shorten
                     the time an analyst spends finding the fix. Every number comes from the stated assumptions
                     applied to the baseline, and the output always says kind="estimate" with a low/high range.

The historical timestamps are simulated (docs/DATA-CARD.md: no business hours or holidays), so the baseline is
illustrative too. Mean hours carry a 95% bootstrap interval (app.analysis.stats.bootstrap_ci, 2000 resamples,
seed 0). SLA breach: a resolved ticket whose hours exceed its priority's target (docs/DATA-CARD.md).
"""
from __future__ import annotations

import csv
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from app.analysis.stats import bootstrap_ci
from app.common.paths import DATA_DIR

TICKETS_CSV = DATA_DIR / "tickets" / "tickets.csv"
RESOLVED_STATUSES = frozenset({"resolved", "closed"})
PRIORITIES = ("P1", "P2", "P3", "P4")
# SLA resolution targets in hours, from docs/DATA-CARD.md (sla_target_hours). Breached = hours > target.
SLA_TARGET_H: dict[str, float] = {"P1": 4.0, "P2": 8.0, "P3": 24.0, "P4": 72.0}
SLA_SOURCE = "docs/DATA-CARD.md sla_target_hours: P1 4, P2 8, P3 24, P4 72"
CI_RESAMPLES = 2000
CI_SEED = 0

# How a result changes the time to resolve. All are assumptions, shown next to every estimate.
DEFAULT_ASSUMPTIONS: dict[str, float] = {
    # Share of the resolution time spent working out what is wrong and what to do (vs waiting, doing the fix,
    # confirming with the user). Low/high give the range shown with the estimate.
    "diagnosis_share": 0.30, "diagnosis_share_low": 0.15, "diagnosis_share_high": 0.45,
    # Share of that diagnosis time saved when the analyst rated the recommendation useful.
    "useful_saving": 0.50,
    # Hours an analyst loses reading a recommendation they rated incorrect.
    "incorrect_cost_h": 0.25,
}
ASSUMPTION_NOTES: dict[str, str] = {
    "diagnosis_share": "share of resolution time spent diagnosing (assumed; range low-high)",
    "useful_saving": "share of diagnosis time saved by a recommendation rated useful (assumed)",
    "incorrect_cost_h": "hours lost reading a recommendation rated incorrect (assumed)",
    "other": "unrated, unresolved, CLARIFY, ESCALATE and BLOCKED results save nothing (conservative)",
}
ASSUMPTION_SOURCES: dict[str, str] = {
    "diagnosis_share": ("our assumption, not measured: diagnosis/triage as a share of resolution time is 0.30; "
                        "0.15-0.45 is the sensitivity range shown with every estimate"),
    "useful_saving": "our assumption, not measured",
    "incorrect_cost_h": "our assumption, not measured",
}


# ---------------------------------------------------------------- helpers

def parse_time(value: Any) -> Optional[datetime]:
    """ISO 8601 text or a datetime -> aware UTC datetime; None if missing or unreadable. No zone -> UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def hours_between(start: Any, end: Any) -> Optional[float]:
    a, b = parse_time(start), parse_time(end)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile (q in 0..1) of an already sorted, non-empty list."""
    pos = (len(sorted_values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def summarize(hours: Iterable[float], with_ci: bool = False) -> dict[str, Any]:
    """n, mean, median and p90 in hours (rounded to 0.01); None for each when there are no values.
    with_ci adds mean_ci95_h = [low, high], a 95% bootstrap interval of the mean."""
    v = sorted(hours)
    if not v:
        out: dict[str, Any] = {"n": 0, "mean_h": None, "median_h": None, "p90_h": None}
        if with_ci:
            out["mean_ci95_h"] = None
        return out
    out = {"n": len(v), "mean_h": round(statistics.fmean(v), 2), "median_h": round(statistics.median(v), 2),
           "p90_h": round(_percentile(v, 0.9), 2)}
    if with_ci:
        _, low, high = bootstrap_ci(v, n=CI_RESAMPLES, seed=CI_SEED)
        out["mean_ci95_h"] = [round(low, 2), round(high, 2)]
    return out


def sla_breaches(hours: Iterable[float], target_h: Optional[float]) -> dict[str, Any]:
    """Resolved tickets over their SLA target: {sla_target_h, breached, n, breach_share}. No target -> Nones."""
    v = list(hours)
    if target_h is None:
        return {"sla_target_h": None, "breached": None, "breach_share": None}
    breached = sum(1 for h in v if h > target_h)
    return {"sla_target_h": target_h, "breached": breached,
            "breach_share": round(breached / len(v), 4) if v else None}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


# ---------------------------------------------------------------- baseline (measured)

def baseline_mttr(tickets: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Measured manual MTTR from historical tickets (the data's own field names: created_at, resolved_at, status,
    priority, category). Only resolved/closed tickets count; skipped rows are counted by reason."""
    hours: list[float] = []
    by_priority: dict[str, list[float]] = defaultdict(list)
    by_category: dict[str, list[float]] = defaultdict(list)
    skipped: Counter[str] = Counter()
    for t in tickets:
        if str(t.get("status") or "").strip().lower() not in RESOLVED_STATUSES:
            skipped["not_resolved"] += 1
            continue
        h = hours_between(t.get("created_at"), t.get("resolved_at"))
        if h is None:
            skipped["missing_or_unreadable_time"] += 1
            continue
        if h < 0:
            skipped["resolved_before_created"] += 1
            continue
        hours.append(h)
        by_priority[str(t.get("priority") or "unknown")].append(h)
        by_category[str(t.get("category") or "unknown")].append(h)
    by_prio_out = {p: {**summarize(v, with_ci=True), **sla_breaches(v, SLA_TARGET_H.get(p))}
                   for p, v in sorted(by_priority.items())}
    with_target = [p for p in by_priority if p in SLA_TARGET_H]
    n_target = sum(len(by_priority[p]) for p in with_target)
    breached = sum(by_prio_out[p]["breached"] for p in with_target)
    return {
        "kind": "measured",
        "unit": "hours",
        "overall": {**summarize(hours, with_ci=True),
                    "sla_n": n_target, "breached": breached,
                    "breach_share": round(breached / n_target, 4) if n_target else None},
        "by_priority": by_prio_out,
        "by_category": {c: summarize(v) for c, v in sorted(by_category.items())},
        "skipped": dict(skipped),
        "sla_source": SLA_SOURCE,
        "note": "created_at to resolved_at on resolved/closed tickets; timestamps are simulated (see DATA-CARD.md)",
    }


def load_baseline(csv_path=TICKETS_CSV) -> dict[str, Any]:
    """baseline_mttr over every row of tickets.csv (read-only). Timestamps are not labels, so all splits count."""
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return baseline_mttr(csv.DictReader(f))


# ---------------------------------------------------------------- assisted (estimated)

def _priority_of(result: Any) -> str:
    """priority from a plain 'priority' field, or classification.priority.label of an AnalysisResult."""
    p = _get(result, "priority")
    if p:
        return str(p)
    cls = _get(result, "classification")
    label = _get(_get(cls, "priority"), "label") if cls is not None else None
    return str(label) if label else "unknown"


def _saved_hours(base_h: float, outcome: str, verdict: Optional[str], a: Mapping[str, float], share: float) -> float:
    if outcome == "RECOMMEND" and verdict == "useful":
        return base_h * share * a["useful_saving"]
    if outcome == "RECOMMEND" and verdict == "incorrect":
        return -a["incorrect_cost_h"]
    return 0.0


def estimate_assisted(results: Iterable[Any], assumptions: Optional[Mapping[str, float]] = None,
                      baseline: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Estimated MTTR for the analysed results, against the baseline for the same priorities.

    results: mappings (or AnalysisResult-like objects) with `outcome`, `priority` (or classification.priority.label)
    and `verdict` (the latest analyst feedback: useful / incorrect / unresolved / None). If `created_at` and
    `resolved_at` are present, the observed demo time is reported separately; it is not comparable to the
    historical baseline (demo tickets are resolved in minutes, not worked for real).
    baseline: the output of baseline_mttr(); each result starts from its priority's mean (overall mean if unknown).
    Left out, it is measured from tickets.csv (load_baseline).
    """
    a = {**DEFAULT_ASSUMPTIONS, **(assumptions or {})}
    for key in ("diagnosis_share", "diagnosis_share_low", "diagnosis_share_high", "useful_saving"):
        if not 0.0 <= a[key] <= 1.0:
            raise ValueError(f"assumption {key} must be between 0 and 1, got {a[key]}")
    if a["incorrect_cost_h"] < 0:
        raise ValueError("assumption incorrect_cost_h must not be negative")
    if baseline is None:
        baseline = load_baseline()
    if not baseline.get("overall", {}).get("n"):
        raise ValueError("a baseline with at least one resolved ticket is needed (baseline_mttr output)")

    overall_mean = baseline["overall"]["mean_h"]
    prio_mean = {p: s["mean_h"] for p, s in baseline.get("by_priority", {}).items() if s.get("n")}

    rows = list(results)
    outcomes: Counter[str] = Counter()
    verdicts: Counter[str] = Counter()
    base_total = 0.0
    saved = {"mid": 0.0, "low": 0.0, "high": 0.0}
    observed: list[float] = []
    no_baseline = 0
    for r in rows:
        outcome = str(_get(r, "outcome") or "unknown")
        verdict = _get(r, "verdict")
        outcomes[outcome] += 1
        verdicts[str(verdict) if verdict else "none"] += 1
        prio = _priority_of(r)
        if prio not in prio_mean:
            no_baseline += 1
        base_h = prio_mean.get(prio, overall_mean)
        base_total += base_h
        for k, share in (("mid", a["diagnosis_share"]), ("low", a["diagnosis_share_low"]),
                         ("high", a["diagnosis_share_high"])):
            saved[k] += _saved_hours(base_h, outcome, verdict, a, share)
        h = hours_between(_get(r, "created_at"), _get(r, "resolved_at"))
        if h is not None and h >= 0:
            observed.append(h)

    n = len(rows)
    out: dict[str, Any] = {
        "kind": "estimate",
        "unit": "hours",
        "n_results": n,
        "outcomes": dict(outcomes),
        "verdicts": dict(verdicts),
        "assumptions": {k: a[k] for k in DEFAULT_ASSUMPTIONS},
        "assumption_notes": ASSUMPTION_NOTES,
        "assumption_sources": ASSUMPTION_SOURCES,
        "results_without_priority_baseline": no_baseline,
        "observed_demo": {**summarize(observed), "note": "demo timings; not comparable to the historical baseline"},
        "note": "estimate from stated assumptions applied to the measured baseline; never an observed MTTR",
    }
    if n == 0:
        out.update(baseline_mean_h=None, assisted_mean_h=None, saving_h=None, saving_pct=None,
                   saving_pct_range=None)
        return out
    base_mean = base_total / n
    mid = saved["mid"] / n
    # Low share -> least saved (most conservative); compute both ends and order them.
    ends = sorted([saved["low"] / n, saved["high"] / n])
    out.update(
        baseline_mean_h=round(base_mean, 2),
        assisted_mean_h=round(base_mean - mid, 2),
        assisted_mean_h_range=[round(base_mean - ends[1], 2), round(base_mean - ends[0], 2)],
        saving_h=round(mid, 2),
        saving_pct=round(100 * mid / base_mean, 1) if base_mean else None,
        saving_pct_range=[round(100 * e / base_mean, 1) for e in ends] if base_mean else None,
    )
    return out
