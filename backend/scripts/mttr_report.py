"""MTTR report for docs/EVALUATION.md (FR-09): the measured baseline and a what-if table for the assisted estimate.

  baseline  app.tickets.mttr.load_baseline(): created_at -> resolved_at on resolved/closed rows of tickets.csv
  what-if   app.tickets.mttr.estimate_assisted() on a scenario: a share of the historical tickets gets a RECOMMEND
            and a share of those is rated useful; every other RECOMMEND is counted as incorrect (it costs review
            time), the rest save nothing. These are scenarios, not results: the dashboard computes the estimate
            from the real analysed results and their feedback.
  caveat    The synthetic resolution timestamps were not generated to match the SLA targets, so the breach rate
            (81% on the current data) is a property of the simulated data, not a finding about a real service
            desk. The breach column shows the method; in real use it would run on the organisation's own ITSM
            timestamps.

Usage (from backend):  .\\.venv\\Scripts\\python.exe scripts\\mttr_report.py
                macOS:  .venv/bin/python scripts/mttr_report.py
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.common.paths import BACKEND_DIR  # noqa: E402
from app.tickets.mttr import (ASSUMPTION_SOURCES, DEFAULT_ASSUMPTIONS, PRIORITIES, SLA_SOURCE,  # noqa: E402
                              estimate_assisted, load_baseline)

EVAL_MD = BACKEND_DIR.parent / "docs" / "EVALUATION.md"
START, END = "<!-- mttr:start -->", "<!-- mttr:end -->"
RECOMMEND_SHARES = (0.5, 0.75)
USEFUL_RATES = (0.5, 0.7, 0.9)


def scenario(baseline: dict[str, Any], recommend_share: float, useful_rate: float, per_ticket: int = 200) -> dict:
    """Each historical resolved ticket's priority is repeated per_ticket times; of those, recommend_share become
    RECOMMEND, of which useful_rate are rated useful and the rest incorrect. Deterministic (no randomness)."""
    n_rec = round(per_ticket * recommend_share)
    n_useful = round(n_rec * useful_rate)
    block = (["useful"] * n_useful + ["incorrect"] * (n_rec - n_useful))
    results = []
    for prio, s in baseline["by_priority"].items():
        for _ in range(s["n"]):
            results += [{"outcome": "RECOMMEND", "priority": prio, "verdict": v} for v in block]
            results += [{"outcome": "ESCALATE", "priority": prio, "verdict": None}] * (per_ticket - n_rec)
    return estimate_assisted(results, baseline=baseline)


def markdown(baseline: dict[str, Any]) -> str:
    o = baseline["overall"]
    a = DEFAULT_ASSUMPTIONS
    lines = [START, "## MTTR: manual baseline (measured) and assisted (estimated)", "",
             f"**Baseline, measured** from `created_at` to `resolved_at` on {o['n']} resolved or closed tickets "
             f"(skipped: {', '.join(f'{k} {v}' for k, v in baseline['skipped'].items()) or 'none'}). The timestamps "
             "are simulated (no business hours or holidays), so these hours are illustrative. Mean: 95% bootstrap "
             "interval (2000 resamples, seed 0). SLA breached = resolved later than the priority's target "
             f"({SLA_SOURCE}).", "",
             "| Priority | Tickets | Mean h (95% CI) | Median h | P90 h | SLA target h | SLA breached |",
             "|---|---|---|---|---|---|---|"]

    def ci(s: dict) -> str:
        lo, hi = s["mean_ci95_h"]
        return f"{s['mean_h']:.1f} ({lo:.1f}-{hi:.1f})"

    def breach(n: int | None, total: int, share: float | None) -> str:
        return "-" if n is None or share is None else f"{n} / {total} ({share:.1%})"

    for p in [*PRIORITIES, *sorted(set(baseline["by_priority"]) - set(PRIORITIES))]:
        s = baseline["by_priority"].get(p)
        if s and s["n"]:
            target = "-" if s["sla_target_h"] is None else f"{s['sla_target_h']:.0f}"
            lines.append(f"| {p} | {s['n']} | {ci(s)} | {s['median_h']:.1f} | {s['p90_h']:.1f} | {target} | "
                         f"{breach(s['breached'], s['n'], s['breach_share'])} |")
    lines.append(f"| all | {o['n']} | {ci(o)} | {o['median_h']:.1f} | {o['p90_h']:.1f} | per priority | "
                 f"{breach(o['breached'], o['sla_n'], o['breach_share'])} |")
    if o["breach_share"] is not None:
        lines += ["", f"The synthetic resolution timestamps were not generated to match the SLA targets, so the "
                  f"{o['breach_share']:.0%} breach rate is a property of the simulated data, not a finding about a "
                  "real service desk. The breach column shows the method; in real use it would run on the "
                  "organisation's own ITSM timestamps."]
    lines += ["", "**Assisted, estimated** (never observed: the assistant recommends, analysts resolve). For each "
              "analysed ticket, start from the baseline mean for its priority. A RECOMMEND rated useful saves "
              f"`diagnosis_share` ({a['diagnosis_share']:.2f}, range {a['diagnosis_share_low']:.2f}-"
              f"{a['diagnosis_share_high']:.2f}; source: {ASSUMPTION_SOURCES['diagnosis_share']}) x "
              f"`useful_saving` ({a['useful_saving']:.2f}) of it. A RECOMMEND "
              f"rated incorrect costs {a['incorrect_cost_h']:.2f} h of review. Everything else (unrated, "
              "unresolved, CLARIFY, ESCALATE, BLOCKED) saves nothing. The dashboard applies this to the real "
              "results and their latest feedback (`mttr.estimate_assisted`).", "",
              "What-if table (scenarios, not results): share of tickets that get a RECOMMEND x share of those "
              "rated useful (the rest counted as incorrect).", "",
              "| RECOMMEND share | Useful rate | Baseline mean h | Assisted mean h (range) | Saving % (range) |",
              "|---|---|---|---|---|"]
    for share in RECOMMEND_SHARES:
        for rate in USEFUL_RATES:
            e = scenario(baseline, share, rate)
            lo_h, hi_h = e["assisted_mean_h_range"]
            lo_p, hi_p = e["saving_pct_range"]
            lines.append(f"| {share:.0%} | {rate:.0%} | {e['baseline_mean_h']:.1f} | {e['assisted_mean_h']:.1f} "
                         f"({lo_h:.1f}-{hi_h:.1f}) | {e['saving_pct']:.1f} ({lo_p:.1f}-{hi_p:.1f}) |")
    lines += ["", "The assumptions are ours, not measured. Report the assisted MTTR as an estimate with this range, "
              "and replace the scenarios with the real useful rate once analysts have reviewed results.", END]
    return "\n".join(lines)


def write_section(path: Path, block: str) -> None:
    """Replace the MTTR block between its markers, or append it. Other sections are left alone."""
    text = path.read_text(encoding="utf-8") if path.exists() else "# Evaluation\n"
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    text = pattern.sub(lambda _: block, text) if pattern.search(text) else text.rstrip("\n") + "\n\n" + block + "\n"
    path.write_text(text, encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MTTR baseline and what-if estimate -> docs/EVALUATION.md")
    parser.add_argument("--out", type=Path, default=EVAL_MD)
    args = parser.parse_args(argv)
    md = markdown(load_baseline())
    write_section(args.out, md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
