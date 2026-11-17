"""The re-ranking step (app/tickets/rerank.py) on a small made-up index (BM25 only, see conftest.py)."""
from datetime import datetime, timezone

import pytest

from app.tickets import rerank as R
from app.tickets.models import Classification, LabelScore, SimilarTicket, TicketIn
from app.tickets.similar import build_ticket_index

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)
ON = {"enabled": True, "top_n": 3, "min_candidates_for_entropy": 2}


def _row(tid, status, service, resolved_at, category="Network", notes="Fixed it.", steps="Restart client | Reconnect"):
    return {"ticket_id": tid, "created_at": "2026-01-01T00:00:00+00:00", "resolved_at": resolved_at,
            "status": status, "category": category, "subcategory": "VPN", "affected_service": service,
            "subject": f"VPN error 809 on laptop {tid}", "description": "The VPN tunnel will not connect, error 809.",
            "root_cause": "client config", "resolution_notes": notes if status in ("resolved", "closed") else "",
            "resolution_steps": steps if status in ("resolved", "closed") else "", "split": "train"}


ROWS = [
    # Retrieval put T1 first, but it is still open, old, another service and another category.
    _row("T1", "open", "Email", "", category="Email"),
    _row("T2", "resolved", "VPN", "2026-11-10T00:00:00+00:00"),        # fixed, same service, recent
    _row("T3", "closed", "VPN", "2026-03-01T00:00:00+00:00"),
    _row("T4", "resolved", "Email", "2026-02-01T00:00:00+00:00", category="Email"),
]


def _similar(row, score):
    notes = row["resolution_notes"]
    return SimilarTicket(ticket_id=row["ticket_id"], subject=row["subject"], category=row["category"],
                         status=row["status"], score=score, match="related",
                         resolution_text=(f"{notes} Steps: {row['resolution_steps']}" if notes else None))


@pytest.fixture
def index():
    return build_ticket_index(rows=ROWS, splits=("train",))


@pytest.fixture
def pool():
    return [_similar(r, s) for r, s in zip(ROWS, [0.9, 0.85, 0.8, 0.7])]   # retrieval order T1..T4


TICKET = TicketIn(subject="VPN error 809", description="VPN tunnel will not connect, error 809",
                  affected_service="VPN", created_at=NOW)
QUERY = "VPN error 809. VPN tunnel will not connect, error 809"
CLASSIFICATION = Classification(
    category=LabelScore(label="Network", prob=0.8), subcategory=LabelScore(label="VPN", prob=0.7),
    priority=LabelScore(label="P3", prob=0.6),
    top3_category=[LabelScore(label="Network", prob=0.8), LabelScore(label="Email", prob=0.15)])


def test_disabled_keeps_retrieval_order(pool, index):
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy={"enabled": False, "top_n": 3}, index=index)
    assert not r.applied
    assert [s.ticket_id for s in r.similar] == ["T1", "T2", "T3"]
    assert r.explanations == {} and r.findings == []
    assert "off" in r.detail


def test_default_policy_is_off(pool, index):
    R.mcdm_policy.cache_clear()
    assert R.mcdm_policy()["enabled"] is False
    assert not R.mcdm_rank(pool, QUERY, TICKET, index=index).applied


def test_resolved_same_service_recent_ticket_moves_to_the_top(pool, index):
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=ON, index=index)
    assert r.applied
    assert r.similar[0].ticket_id == "T2"
    assert r.explanations["T2"]["rank_before"] == 2 and r.explanations["T2"]["rank_after"] == 1
    assert "top-1 was retrieval rank 2" in r.detail


def test_enabled_output_is_consistent(pool, index):
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=ON, index=index)
    assert len(r.similar) == 3
    assert [r.explanations[s.ticket_id]["rank_after"] for s in r.similar] == [1, 2, 3]
    closeness = [r.explanations[s.ticket_id]["closeness"] for s in r.similar]
    assert closeness == sorted(closeness, reverse=True) and all(0 <= c <= 1 for c in closeness)
    by_id = {s.ticket_id: s for s in pool}
    assert all(s.score == by_id[s.ticket_id].score for s in r.similar)  # absolute score never changes
    for e in r.explanations.values():
        assert sum(e["contributions"].values()) == pytest.approx(1.0, abs=1e-5) or not any(e["contributions"].values())
    assert sum(w["blended"] for w in r.weights.values()) == pytest.approx(1.0, abs=1e-3)


def test_missing_sources_are_dropped_and_reported(pool, index):
    # No classifier -> no category_agreement; BM25-only index -> SimilarTicket.score is not a cosine.
    r = R.mcdm_rank(pool, QUERY, TICKET, None, policy=ON, index=index)
    assert r.applied
    assert set(r.dropped) == {"category_agreement", "dense_similarity"}
    assert "category_agreement" not in r.weights and "dense_similarity" not in r.weights
    assert "dropped" in r.detail


def test_no_service_on_the_ticket_drops_same_service(pool, index):
    ticket = TICKET.model_copy(update={"affected_service": None})
    r = R.mcdm_rank(pool, QUERY, ticket, CLASSIFICATION, policy=ON, index=index)
    assert "same_service" in r.dropped


def test_candidate_missing_from_index_drops_row_based_criteria(index):
    pool = [_similar(ROWS[1], 0.9), SimilarTicket(ticket_id="GONE", subject="x", category="Network",
                                                  status="resolved", score=0.8, match="related")]
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=ON, index=index)
    assert r.applied and {"age_days", "bm25"} <= set(r.dropped)


def test_too_few_candidates_use_prior_weights(pool, index):
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy={**ON, "min_candidates_for_entropy": 10}, index=index)
    assert r.applied and "prior only" in r.detail
    assert all(w["entropy"] is None for w in r.weights.values())


@pytest.mark.parametrize("method", ["prior", "entropy", "blended"])
def test_every_method_runs(pool, index, method):
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=ON, method=method, index=index)
    assert r.applied and r.method == method and len(r.similar) == 3


def test_errors_keep_retrieval_order_with_a_warn_finding(pool, index, monkeypatch):
    def broken(x):
        raise RuntimeError("secret ticket text must not leak")
    monkeypatch.setitem(R.CRITERIA, "bm25", broken)
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=ON, index=index)
    assert not r.applied
    assert [s.ticket_id for s in r.similar] == ["T1", "T2", "T3"]
    assert [(f.code, f.severity) for f in r.findings] == [("MCDM_SKIPPED", "warn")]
    assert "secret" not in r.findings[0].message and "secret" not in r.detail


def test_bad_policy_is_an_error_not_a_crash(pool, index):
    bad = {**ON, "criteria": [{"name": "made_up", "direction": "benefit", "prior_weight": 1.0}]}
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=bad, index=index)
    assert not r.applied and r.findings[0].code == "MCDM_SKIPPED"


def test_empty_pool(index):
    r = R.mcdm_rank([], QUERY, TICKET, CLASSIFICATION, policy=ON, index=index)
    assert r.similar == [] and not r.applied


def test_trace_detail_has_no_ticket_text(pool, index):
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=ON, index=index)
    for text in (TICKET.subject, TICKET.description, "809", "tunnel", ROWS[0]["subject"]):
        assert text not in r.detail


def test_analyst_feedback_is_neutral_without_votes(index):
    pool = [_similar(r, 0.8) for r in ROWS[1:3]]
    pool[1] = pool[1].model_copy(update={"past_feedback": {"useful": 3, "incorrect": 0}})
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=ON, index=index)
    values = {tid: e["criteria"]["analyst_feedback"] for tid, e in r.explanations.items()}
    assert values == pytest.approx({"T2": 0.5, "T3": 0.8})   # (0+1)/(0+2) and (3+1)/(3+2)


# ---- AHP priors ------------------------------------------------------------------------------------------

from app.common.mcdm import blend_weights  # noqa: E402


def test_judgements_file_is_consistent_and_matches_the_criteria():
    a = R.ahp_prior()
    assert a.cr < 0.10 and a.accepted
    assert list(a.weights) == [c["name"] for c in R.DEFAULTS["criteria"]]   # same order as the code
    assert sum(a.weights.values()) == pytest.approx(1.0)
    assert "decided before seeing results" in a.author


def _comparable(r):
    return ([s.ticket_id for s in r.similar], r.weights, r.explanations, r.detail, r.dropped, r.applied)


def test_typed_prior_source_gives_exactly_the_old_results(pool, index):
    default = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=ON, index=index)
    typed = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy={**ON, "prior_source": "typed"}, index=index)
    assert _comparable(typed) == _comparable(default)
    assert typed.prior_source == "typed" and typed.ahp is None and "AHP" not in typed.detail
    assert all(set(w) == {"entropy", "prior", "blended"} for w in typed.weights.values())   # no new keys


def test_ahp_prior_source_uses_the_ahp_weights(pool, index):
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy={**ON, "prior_source": "ahp"}, index=index)
    a = R.ahp_prior()
    kept = list(r.weights)
    total = sum(a.weights[n] for n in kept)
    assert r.prior_source == "ahp" and "prior AHP (CR" in r.detail
    for n in kept:   # the prior shown is the AHP weight, renormalised over the criteria that had data
        assert r.weights[n]["prior"] == pytest.approx(a.weights[n] / total, abs=1e-4)
        assert r.weights[n]["ahp"] == r.weights[n]["prior"]
    assert r.ahp["cr"] < 0.10 and set(r.ahp["weights"]) == set(a.weights)


@pytest.mark.parametrize("method", ["ahp", "ahp_entropy"])
def test_ahp_methods_run_and_report_ahp(pool, index, method):
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=ON, method=method, index=index)
    assert r.applied and r.method == method and r.prior_source == "ahp"
    assert sum(w["blended"] for w in r.weights.values()) == pytest.approx(1.0, abs=1e-3)


def test_ahp_method_is_ahp_weights_only(pool, index):
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=ON, method="ahp", index=index)
    ahp = [r.weights[n]["ahp"] for n in r.weights]
    expected = blend_weights(None, ahp, 0.0, ON.get("weight_floor", 0.05))
    assert [r.weights[n]["blended"] for n in r.weights] == pytest.approx(list(expected), abs=1e-3)


INCONSISTENT = """
author: test
criteria: [bm25, resolution_quality, analyst_feedback]
judgements:
  - {row: bm25, col: resolution_quality, value: 9}
  - {row: bm25, col: analyst_feedback, value: 1/9}
  - {row: resolution_quality, col: analyst_feedback, value: 9}
"""
THREE = [{"name": "bm25", "direction": "benefit", "prior_weight": 0.5},
         {"name": "resolution_quality", "direction": "benefit", "prior_weight": 0.3},
         {"name": "analyst_feedback", "direction": "benefit", "prior_weight": 0.2}]


def test_inconsistent_judgements_fall_back_to_typed_priors(pool, index, tmp_path, caplog):
    path = tmp_path / "bad.yaml"
    path.write_text(INCONSISTENT, encoding="utf-8")
    policy = {**ON, "criteria": THREE, "ahp_file": str(path)}
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=policy, method="ahp", index=index)
    typed = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy=policy, method="prior", index=index)
    assert r.applied and r.prior_source == "typed"
    assert r.ahp["accepted"] is False and r.ahp["cr"] > 0.10
    assert "AHP rejected" in r.detail and "typed prior_weight used instead" in r.detail
    assert [r.weights[n]["blended"] for n in r.weights] == [typed.weights[n]["blended"] for n in typed.weights]
    assert "inconsistent" in caplog.text


def test_broken_judgements_file_keeps_retrieval_order(pool, index, tmp_path):
    path = tmp_path / "dup.yaml"
    path.write_text(INCONSISTENT + "  - {row: bm25, col: resolution_quality, value: 2}\n", encoding="utf-8")
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy={**ON, "criteria": THREE, "ahp_file": str(path)},
                    method="ahp", index=index)
    assert not r.applied and r.findings[0].code == "MCDM_SKIPPED"


def test_unknown_prior_source_is_an_error_not_a_crash(pool, index):
    r = R.mcdm_rank(pool, QUERY, TICKET, CLASSIFICATION, policy={**ON, "prior_source": "vibes"}, index=index)
    assert not r.applied and r.findings[0].code == "MCDM_SKIPPED"
