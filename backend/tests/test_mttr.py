"""MTTR (FR-09): measured baseline and estimated assisted MTTR. Small made-up tickets, plus the real CSV's counts."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.tickets import mttr


def _t(priority, hours, status="resolved", category="Network", created="2026-05-01T00:00:00+00:00"):
    start = mttr.parse_time(created)
    end = None if hours is None else (start.timestamp() + hours * 3600)
    resolved = "" if end is None else datetime.fromtimestamp(end, tz=timezone.utc).isoformat()
    return {"created_at": created, "resolved_at": resolved, "status": status, "priority": priority,
            "category": category}


TICKETS = [_t("P1", 2), _t("P1", 4), _t("P3", 40, status="closed"), _t("P3", 60, category="Email")]
BASE = mttr.baseline_mttr(TICKETS)


def test_parse_time_accepts_z_offset_and_naive_as_utc():
    assert mttr.parse_time("2026-05-01T03:00:00Z") == datetime(2026, 5, 1, 3, tzinfo=timezone.utc)
    assert mttr.parse_time("2026-05-01T05:00:00+02:00") == datetime(2026, 5, 1, 3, tzinfo=timezone.utc)
    assert mttr.parse_time("2026-05-01 03:00:00") == datetime(2026, 5, 1, 3, tzinfo=timezone.utc)
    assert mttr.parse_time("not a date") is None and mttr.parse_time("") is None and mttr.parse_time(None) is None


def test_baseline_is_measured_per_priority_and_category():
    assert BASE["kind"] == "measured"
    o = BASE["overall"]
    assert (o["n"], o["mean_h"], o["median_h"], o["p90_h"]) == (4, 26.5, 22.0, 54.0)
    assert BASE["by_priority"]["P1"]["mean_h"] == 3.0
    assert BASE["by_priority"]["P3"]["median_h"] == 50.0
    assert BASE["by_category"]["Email"]["n"] == 1


def test_baseline_skips_and_counts_bad_rows():
    bad = [_t("P2", 5, status="open"), _t("P2", None), {**_t("P2", 1), "resolved_at": "garbage"},
           {**_t("P2", 1), "resolved_at": "2026-04-30T00:00:00+00:00"}]
    out = mttr.baseline_mttr(TICKETS + bad)
    assert out["overall"]["n"] == 4
    assert out["skipped"] == {"not_resolved": 1, "missing_or_unreadable_time": 2, "resolved_before_created": 1}


def test_baseline_empty():
    o = mttr.baseline_mttr([])["overall"]
    assert (o["n"], o["mean_h"], o["mean_ci95_h"], o["breached"], o["breach_share"]) == (0, None, None, 0, None)


def test_real_csv_baseline_counts():
    base = mttr.load_baseline()
    assert base["overall"]["n"] == 980 and base["skipped"] == {"not_resolved": 120}
    means = [base["by_priority"][p]["mean_h"] for p in mttr.PRIORITIES]
    assert means == sorted(means)            # P1 fastest ... P4 slowest


def test_estimate_saves_only_on_useful_recommendations():
    results = [{"outcome": "RECOMMEND", "priority": "P3", "verdict": "useful"},
               {"outcome": "RECOMMEND", "priority": "P3", "verdict": "unresolved"},
               {"outcome": "ESCALATE", "priority": "P3", "verdict": None},
               {"outcome": "CLARIFY", "priority": "P3", "verdict": "useful"}]
    est = mttr.estimate_assisted(results, baseline=BASE)
    assert est["kind"] == "estimate" and "never an observed MTTR" in est["note"]
    # P3 baseline 50 h; one useful RECOMMEND saves 50 * 0.30 * 0.50 = 7.5 h, spread over 4 results.
    assert est["baseline_mean_h"] == 50.0
    assert est["saving_h"] == pytest.approx(1.88, abs=0.01)
    assert est["assisted_mean_h"] == pytest.approx(48.12, abs=0.01)
    low, high = est["saving_pct_range"]
    assert low < est["saving_pct"] < high
    assert est["outcomes"] == {"RECOMMEND": 2, "ESCALATE": 1, "CLARIFY": 1}


def test_incorrect_recommendation_costs_time():
    est = mttr.estimate_assisted([{"outcome": "RECOMMEND", "priority": "P1", "verdict": "incorrect"}],
                                 baseline=BASE)
    assert est["saving_h"] == -0.25 and est["assisted_mean_h"] == 3.25


def test_no_useful_feedback_means_no_saving():
    est = mttr.estimate_assisted([{"outcome": "RECOMMEND", "priority": "P1", "verdict": None}] * 3, baseline=BASE)
    assert est["saving_h"] == 0 and est["assisted_mean_h"] == est["baseline_mean_h"]


def test_reads_priority_from_classification_and_falls_back_to_overall():
    obj = SimpleNamespace(outcome="RECOMMEND", verdict="useful",
                          classification=SimpleNamespace(priority=SimpleNamespace(label="P1")))
    est = mttr.estimate_assisted([obj, {"outcome": "RECOMMEND", "priority": "P9", "verdict": None}], baseline=BASE)
    assert est["results_without_priority_baseline"] == 1
    assert est["baseline_mean_h"] == pytest.approx((3.0 + 26.5) / 2, abs=0.01)


def test_observed_demo_time_is_kept_apart():
    r = {"outcome": "RECOMMEND", "priority": "P1", "verdict": "useful",
         "created_at": "2026-11-17T10:00:00+00:00", "resolved_at": "2026-11-17T10:30:00+00:00"}
    est = mttr.estimate_assisted([r], baseline=BASE)
    assert est["observed_demo"]["n"] == 1 and est["observed_demo"]["mean_h"] == 0.5
    assert "not comparable" in est["observed_demo"]["note"]


def test_empty_results_and_bad_assumptions():
    est = mttr.estimate_assisted([], baseline=BASE)
    assert est["n_results"] == 0 and est["assisted_mean_h"] is None
    with pytest.raises(ValueError):
        mttr.estimate_assisted([], {"useful_saving": 1.5}, baseline=BASE)
    with pytest.raises(ValueError):
        mttr.estimate_assisted([], baseline=mttr.baseline_mttr([]))


def test_two_argument_call_measures_baseline_from_csv():
    est = mttr.estimate_assisted([{"outcome": "RECOMMEND", "priority": "P2", "verdict": "useful"}], {})
    assert est["baseline_mean_h"] == mttr.load_baseline()["by_priority"]["P2"]["mean_h"]


def test_report_scenario_rates_are_exact_and_sections_are_kept(tmp_path):
    import scripts.mttr_report as rep
    e = rep.scenario(BASE, 0.75, 0.5)
    assert e["outcomes"]["RECOMMEND"] == 4 * 150 and e["verdicts"]["useful"] == 4 * 75
    assert e["kind"] == "estimate"
    md_path = tmp_path / "EVALUATION.md"
    md_path.write_text("# Evaluation\n\n<!-- clf-val:start -->\nclassifier\n<!-- clf-val:end -->\n")
    rep.write_section(md_path, rep.markdown(BASE))
    rep.write_section(md_path, rep.markdown(BASE))
    text = md_path.read_text()
    assert "classifier" in text and text.count(rep.START) == 1
    assert "scenarios, not results" in text and "measured" in text
    assert "not a finding about a real service desk" in text


def test_sla_breach_counts_per_priority_and_overall():
    # P1 target 4 h: 3 h ok, 4 h ok (not over), 5 h breached. P3 target 24 h: 30 h breached. P9 has no target.
    rows = [_t("P1", 3), _t("P1", 4), _t("P1", 5), _t("P3", 30), _t("P9", 100)]
    out = mttr.baseline_mttr(rows)
    p1 = out["by_priority"]["P1"]
    assert (p1["sla_target_h"], p1["breached"], p1["breach_share"]) == (4.0, 1, round(1 / 3, 4))
    assert out["by_priority"]["P3"]["breached"] == 1
    assert out["by_priority"]["P9"]["breached"] is None and out["by_priority"]["P9"]["sla_target_h"] is None
    assert (out["overall"]["sla_n"], out["overall"]["breached"], out["overall"]["breach_share"]) == (4, 2, 0.5)
    assert "DATA-CARD" in out["sla_source"]


def test_mean_ci_matches_stats_bootstrap_and_contains_mean():
    import numpy as np
    from app.analysis.stats import bootstrap_ci
    p1 = BASE["by_priority"]["P1"]
    _, lo, hi = bootstrap_ci(np.array([2.0, 4.0]), n=2000, seed=0)
    assert p1["mean_ci95_h"] == [round(lo, 2), round(hi, 2)]
    for s in (BASE["overall"], *BASE["by_priority"].values()):
        assert s["mean_ci95_h"][0] <= s["mean_h"] <= s["mean_ci95_h"][1]
    assert mttr.summarize([], with_ci=True)["mean_ci95_h"] is None


def test_diagnosis_share_has_a_source():
    src = mttr.ASSUMPTION_SOURCES["diagnosis_share"]
    assert "our assumption" in src and "0.30" in src and "0.15-0.45" in src
    est = mttr.estimate_assisted([], baseline=BASE)
    assert est["assumption_sources"]["diagnosis_share"] == src
